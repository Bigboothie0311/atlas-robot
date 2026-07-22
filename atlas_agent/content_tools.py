"""Agent tools for the self-showcase pipeline: narrate a tour of Atlas's
own HUD, edit it into a Reel, and (only with explicit confirmation)
publish it to Instagram or YouTube.

content.record_self_showcase records Atlas's own screen -- the physical
HUD kiosk on the Pi (see hud_capture.py) -- by default, not the Windows
PC's screen. Confirmed live 2026-07-21: recording the PC screen as a
stand-in for "record yourself" shows the owner's desktop, not Atlas;
the whole point of self-showcase content is Atlas narrating his own
features (weather radar, self-diagnostics, ...), which only exist on
his own HUD. That's still the default and still what most beats use.

Separately (added the same day, once that mistake was already fixed):
individual beats can be flagged "source": "pc" to deliberately splice in
a real clip of the Windows PC's own screen -- e.g. Atlas opening a
YouTube video or an app, via pc.start_screen_recording's underlying
primitive (_record_pc_demo_clip) -- mixed into the same tour and
stitched into the same final Reel by concat_clips(). This is not the
old mistake: it's an explicitly-flagged, additional clip source
alongside the HUD, not a replacement for it, and only available when
this runtime was actually built with a PC connection (pc_client/
sftp_client both non-None). A "source": "pc" beat can also type a
message into Notepad on camera (pc_action type_text), which is how
Atlas talks to viewers in text mid-video before hopping back to his own
screen; the typing is paced to finish just as that beat's narration
does. It edits each clip with content_pipeline.edit_reel and
concatenates them. Nothing it does is public or destructive, so it
stays at permission_level=0.

With no explicit 'beats', the tour is now written fresh at record time
by the model (atlas_agent/showcase_script.py) with Atlas's real live
state as context -- what to talk about, how many beats, whether to hop
to the PC and what to do there. The older canned tour
(_build_default_tour()) is kept purely as the offline fallback for when
scripting fails: it randomizes phrasing, beat selection, and order, but
its *content* is a fixed handful of talking points, which is exactly
why every early Reel felt like the same video.

The social publishing tools use PermissionLevel.CONFIRMATION_REQUIRED:
the safety model explicitly requires confirming the exact media and
metadata before any upload runs.
"""

from __future__ import annotations

import json
import math
import random
import re
import shutil
import time
import wave
from pathlib import Path, PureWindowsPath
from typing import Any

import requests

import content_pipeline
import facebook_publish
import hud_capture
import instagram_publish
import reel_package
import social_publish
import youtube_publish
from atlas_growth import DEFAULT_DATABASE_PATH, GrowthStore
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool
from atlas_agent.verifier import ResultVerifier, VerificationCheck

HUB = "http://127.0.0.1:5051"
HUD_REQUEST_TIMEOUT_SECONDS = 10

# Extra headroom on top of each beat's narration length so its HUD clip
# doesn't cut off mid-sentence before edit_reel trims it back down to
# the narration length anyway.
RECORDING_BUFFER_SECONDS = 3

# Lets the HUD's own CSS transition/animation finish before frame
# capture starts, so the clip doesn't open on a mid-transition frame.
HUD_ACTION_SETTLE_SECONDS = 1.0
REEL_PREVIEW_TIMEOUT_SECONDS = 180
MIN_REEL_SECONDS = 40.0
MAX_REEL_SECONDS = 80.0
SHOWCASE_TOOL_TIMEOUT_SECONDS = 900

# How far before the end of a "type_text" beat the typing should finish.
# edit_reel trims each clip back to its narration length, so typing
# paced to the full clip would get cut off mid-word; this lands the last
# character just inside the final cut.
TYPING_LEAD_SECONDS = 2.0
PC_SAFE_SURFACE_SETTLE_SECONDS = 4.0
PC_RECORDING_FINALIZE_SECONDS = 1.0
PC_DOWNLOAD_ATTEMPTS = 3
PC_DOWNLOAD_RETRY_SECONDS = 1.0

# A desktop_goal beat drives a vision loop: every step is a full-screen
# screenshot, a model decision, and a real input action. That is far
# slower than the beat's narration, and ffmpeg's -t is a hard
# self-terminating cap -- a cap sized to the narration ended the
# recording during the launch phase, so the finished Reel showed Paint
# opening and never showed it being used. Size the cap from the step
# budget instead, then stop as soon as the goal actually reports done.
PC_DESKTOP_GOAL_STEP_SECONDS = 15.0
PC_DESKTOP_GOAL_MAX_RECORDING_SECONDS = 600

DESKTOP_EXPORT_PACKAGE_FOLDERS = frozenset(
    {"subtitles", "translations", "trials"}
)

# Named visual requirements the owner can ask for out loud. These are
# contracts, not suggestions: "include MS Paint" has to produce a real
# drawing, not a generic "PC shot" or yet more HUD footage.
_MS_PAINT_REQUEST = re.compile(
    r"\b(?:microsoft\s+paint|ms\s+paint|mspaint|paint)\b",
    re.IGNORECASE,
)
_NOTEPAD_REQUEST = re.compile(r"\bnote\s?pad\b", re.IGNORECASE)
_YOUTUBE_REQUEST = re.compile(r"\byou\s?tube\b", re.IGNORECASE)

NOTEPAD_MESSAGES: tuple[str, ...] = (
    "Hey - Atlas here. A Raspberry Pi is typing this on a gaming PC, "
    "live, because someone asked me to prove I actually can.",
    "This is being typed by a robot on a real keyboard. No edit, no "
    "cut. If the typos land, they are mine.",
    "Message from a Raspberry Pi to whoever is watching: the machine "
    "you are looking at is not mine, and I am still driving it.",
)
YOUTUBE_QUERIES: tuple[str, ...] = (
    "raspberry pi robot build timelapse",
    "homelab automation raspberry pi projects",
    "diy robotics raspberry pi voice assistant",
)


def _export_reel_to_desktop(
    *,
    video_path: str | Path,
    package_path: str | Path | None,
    pc_client: Any,
    sftp_client: Any,
    remote_root: str,
) -> dict[str, Any]:
    """Copy one curated, hash-verified Reel package to Windows."""
    source_video = Path(video_path).resolve()
    destination = PureWindowsPath(remote_root) / source_video.stem
    try:
        connection = pc_client.ensure_online(wake_if_needed=True)
        if not getattr(connection, "success", False):
            raise RuntimeError(
                getattr(connection, "error", None)
                or getattr(connection, "message", None)
                or "the PC is offline"
            )
        sftp_client.make_directory(destination)
        sources: list[tuple[Path, PureWindowsPath]] = [
            (source_video, PureWindowsPath("reel.mp4"))
        ]
        package = Path(package_path).resolve() if package_path else None
        if package is not None and package.is_dir():
            root_files = {
                "caption.txt",
                "cover.png",
                "manifest.json",
                "collaboration_kit.json",
                "collaboration_pitch.txt",
            }
            for item in sorted(package.rglob("*")):
                if not item.is_file():
                    continue
                relative = item.relative_to(package)
                if (
                    relative.as_posix() in root_files
                    or relative.parts[0] in DESKTOP_EXPORT_PACKAGE_FOLDERS
                ):
                    sources.append(
                        (item, PureWindowsPath(*relative.parts))
                    )

        created_directories = {destination}
        uploaded: list[str] = []
        for local_path, relative_path in sources:
            remote_path = destination / relative_path
            parent = remote_path.parent
            if parent not in created_directories:
                sftp_client.make_directory(parent)
                created_directories.add(parent)
            transfer = sftp_client.upload(local_path, remote_path)
            if not (
                getattr(transfer, "ok", False)
                and getattr(transfer, "verified", False)
            ):
                raise RuntimeError(
                    getattr(transfer, "error", None)
                    or f"verification failed for {remote_path.name}"
                )
            uploaded.append(str(remote_path))
        return {
            "ok": True,
            "folder": str(destination),
            "files": uploaded,
            "file_count": len(uploaded),
        }
    except Exception as error:
        return {
            "ok": False,
            "folder": str(destination),
            "files": [],
            "file_count": 0,
            "error": f"{type(error).__name__}: {error}",
        }


# A picture Atlas draws stroke by stroke, in canvas coordinates for a
# maximized Paint window on a 1920x1080 desktop: house, roof, door, two
# windows, sun, and a horizon line. Deterministic on purpose -- three
# live runs of the vision-driven agent ended with Paint open and the
# canvas blank, and an explicitly requested Paint Reel must never ship
# without a drawing in it.
PAINT_DRAWINGS: dict[str, tuple[tuple[tuple[int, int], ...], ...]] = {
    "a little house on a hill": (
        ((700, 780), (700, 560), (1100, 560), (1100, 780), (700, 780)),
        ((700, 560), (900, 420), (1100, 560)),
        ((860, 780), (860, 680), (940, 680), (940, 780)),
        ((760, 610), (820, 610), (820, 660), (760, 660), (760, 610)),
        ((990, 610), (1050, 610), (1050, 660), (990, 660), (990, 610)),
        (
            (1270, 400), (1330, 360), (1390, 400),
            (1390, 470), (1330, 505), (1270, 470), (1270, 400),
        ),
        ((450, 780), (1500, 780)),
    ),
    "a robot that looks a bit like me": (
        ((760, 400), (1140, 400), (1140, 700), (760, 700), (760, 400)),
        ((850, 480), (910, 480), (910, 540), (850, 540), (850, 480)),
        ((990, 480), (1050, 480), (1050, 540), (990, 540), (990, 480)),
        ((860, 620), (950, 650), (1040, 620)),
        ((950, 400), (950, 320)),
        ((910, 300), (990, 300), (990, 330), (910, 330), (910, 300)),
        ((760, 500), (680, 500), (680, 620)),
        ((1140, 500), (1220, 500), (1220, 620)),
    ),
    "a rocket heading somewhere better": (
        ((950, 330), (1030, 500), (1030, 700), (870, 700), (870, 500), (950, 330)),
        ((870, 560), (780, 700), (870, 690)),
        ((1030, 560), (1120, 700), (1030, 690)),
        ((910, 480), (990, 480), (990, 550), (910, 550), (910, 480)),
        ((900, 700), (930, 800), (950, 720), (970, 800), (1000, 700)),
        ((600, 400), (640, 400)),
        ((1300, 480), (1340, 480)),
    ),
    "a sleepy cat": (
        (
            (860, 560), (940, 520), (1030, 560),
            (1060, 650), (990, 710), (890, 710), (830, 650), (860, 560),
        ),
        ((860, 560), (845, 480), (905, 520)),
        ((1030, 560), (1050, 480), (985, 520)),
        ((890, 610), (920, 610)),
        ((970, 610), (1000, 610)),
        ((930, 645), (945, 660), (960, 645)),
        ((1060, 660), (1180, 700), (1150, 620)),
    ),
    "mountains at sunrise": (
        ((450, 760), (650, 520), (800, 700), (900, 600), (1080, 760)),
        ((1000, 700), (1150, 520), (1330, 760)),
        (
            (700, 400), (760, 360), (820, 400),
            (820, 470), (760, 505), (700, 470), (700, 400),
        ),
        ((450, 780), (1500, 780)),
        ((520, 640), (560, 610), (600, 640)),
        ((1180, 660), (1220, 630), (1260, 660)),
    ),
}
PAINT_STROKES = PAINT_DRAWINGS["a little house on a hill"]
# Toolbar coordinates for a maximized Paint window on a 1920x1080 desktop.
# Ctrl+A switches Paint to the Selection tool, so without re-picking the
# pencil every later drag draws a selection box instead of ink -- which is
# exactly how a run finished with a blank canvas and every stroke "ok".
PAINT_PENCIL_TOOL = (262, 88)
PAINT_LAUNCH_SETTLE_SECONDS = 9.0
PAINT_FOCUS_SETTLE_SECONDS = 1.5
PAINT_STROKE_TIMEOUT_SECONDS = 180


def _prepare_paint_canvas(pc_client: Any) -> None:
    """Get Paint open, focused, maximized and blank BEFORE recording.

    Launching and clearing on camera wasted most of the clip, so the beat
    showed an app starting rather than a drawing appearing. It also never
    closes Paint: closing with unsaved work raises a modal "save your
    work?" dialog that silently swallows every later click.
    """
    windows = (
        (pc_client.execute("active_apps", timeout_seconds=60).data or {})
        .get("windows")
        or []
    )
    if not any("paint" in str(title).lower() for title in windows):
        pc_client.execute(
            "launch_process",
            {"executable": "mspaint.exe", "arguments": [], "hidden": False},
            timeout_seconds=60,
        )
        time.sleep(PAINT_LAUNCH_SETTLE_SECONDS)

    focused = pc_client.execute(
        "window_control",
        {"action": "focus", "title": "Paint"},
        timeout_seconds=90,
    )
    if not (focused.data or {}).get("ok"):
        raise RuntimeError("could not bring Microsoft Paint to the foreground")

    pc_client.execute(
        "window_control",
        {"action": "maximize", "title": "Paint"},
        timeout_seconds=60,
    )
    time.sleep(PAINT_FOCUS_SETTLE_SECONDS)

    for keys in ("^a", "{DEL}", "{ESC}"):
        pc_client.execute(
            "desktop_input", {"action": "keys", "keys": keys},
            timeout_seconds=60,
        )
        time.sleep(0.4)

    # Back to the pencil: the clear above left the Selection tool active,
    # under which every drag draws a selection box instead of ink.
    pc_client.execute(
        "desktop_input",
        {
            "action": "click",
            "x": PAINT_PENCIL_TOOL[0],
            "y": PAINT_PENCIL_TOOL[1],
            "button": "left",
        },
        timeout_seconds=60,
    )
    time.sleep(0.6)


def _draw_in_paint(
    pc_client: Any,
    clip_seconds: float,
    subject: str | None = None,
) -> dict[str, Any]:
    """Draw one real picture in Paint, stroke by stroke, on camera."""
    strokes = PAINT_DRAWINGS.get(str(subject or ""), PAINT_STROKES)

    windows = (
        (pc_client.execute("active_apps", timeout_seconds=60).data or {})
        .get("windows")
        or []
    )
    if not any("paint" in str(title).lower() for title in windows):
        _prepare_paint_canvas(pc_client)

    # Pace the strokes so the drawing is still visibly unfolding for the
    # whole beat rather than finishing in the first second.
    pause = max(0.2, (clip_seconds - 4.0) / max(1, len(strokes)))
    drawn = 0
    for stroke in strokes:
        result = pc_client.execute(
            "desktop_input",
            {
                "action": "drag",
                "path": [list(point) for point in stroke],
                "button": "left",
            },
            timeout_seconds=PAINT_STROKE_TIMEOUT_SECONDS,
        )
        if (result.data or {}).get("ok"):
            drawn += 1
        time.sleep(pause)

    if drawn == 0:
        raise RuntimeError("Microsoft Paint accepted no drawing strokes")

    return {"ok": True, "strokes": drawn, "subject": subject}


def _apply_best_hook(
    plan: dict[str, Any],
    model_hooks: list[Any] | tuple[Any, ...],
) -> None:
    """Promote the strongest generated hook to the plan's actual hook.

    The candidate list used to be replaced while ``hook`` kept its weak
    templated value, so a Reel shipped a hook scored 62 with a 90 sitting
    unused in the very same payload.
    """
    cleaned = [
        str(hook).strip() for hook in model_hooks if str(hook or "").strip()
    ]
    if len(cleaned) < 2:
        return

    plan["hook_candidates"] = [
        {"text": hook, "score": max(50, 90 - index * 5)}
        for index, hook in enumerate(cleaned)
    ]
    plan["hook"] = plan["hook_candidates"][0]["text"]
    plan["hook_score"] = plan["hook_candidates"][0]["score"]


def _required_pc_scene(mission: str | None) -> dict[str, Any] | None:
    """Return a deterministic scene for an explicit owner request.

    Model-written tours are intentionally creative, but named visual
    requirements are contracts rather than suggestions. Keep this small and
    deterministic so a request to show Paint cannot be generalized into a
    generic "PC shot" or silently replaced with more HUD footage.
    """
    if not isinstance(mission, str):
        return None

    if _NOTEPAD_REQUEST.search(mission) and not _MS_PAINT_REQUEST.search(mission):
        message = random.choice(NOTEPAD_MESSAGES)
        return {
            "narration": (
                "You asked me to use Notepad, so I am typing this to you "
                "live on the real machine, one keystroke at a time."
            ),
            "source": "pc",
            "action": "idle",
            "pc_action": {
                "type": "type_text", "app": "notepad", "text": message,
            },
        }

    if _YOUTUBE_REQUEST.search(mission) and not _MS_PAINT_REQUEST.search(mission):
        return {
            "narration": (
                "You asked for YouTube, so here is my real browser pulling "
                "up a search on the machine I actually control."
            ),
            "source": "pc",
            "action": "idle",
            "pc_action": {
                "type": "youtube_search",
                "query": random.choice(YOUTUBE_QUERIES),
            },
        }

    if not _MS_PAINT_REQUEST.search(mission):
        return None

    subject = random.choice(sorted(PAINT_DRAWINGS))
    return {
        "narration": (
            "You asked to watch me use Microsoft Paint, so here is my real "
            f"desktop while I draw {subject}, stroke by stroke, right now."
        ),
        "source": "pc",
        "action": "idle",
        "pc_action": {"type": "paint_drawing", "subject": subject},
    }


def _ensure_required_pc_scene(
    tour: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    mission: str | None,
) -> tuple[dict[str, Any], ...]:
    required = _required_pc_scene(mission)
    directed = tuple(dict(beat) for beat in tour)
    if required is None:
        return directed

    def is_paint_beat(beat: dict[str, Any]) -> bool:
        action = beat.get("pc_action")
        return (
            beat.get("source") == "pc"
            and isinstance(action, dict)
            and (
                action.get("type") == "paint_drawing"
                or (
                    action.get("type") == "desktop_goal"
                    and _MS_PAINT_REQUEST.search(str(action.get("goal") or ""))
                )
            )
        )

    # A model-written Paint goal does NOT satisfy the request. It used to,
    # and the vision agent then drew nothing while the beat's narration
    # promised a picture that never appeared on screen. Any Paint beat the
    # model wrote is replaced wholesale -- narration included -- so what is
    # said and what is drawn always match.
    existing = [index for index, beat in enumerate(directed) if is_paint_beat(beat)]
    if existing:
        first = existing[0]
        kept = tuple(
            beat for index, beat in enumerate(directed)
            if index == first or index not in existing
        )
        return tuple(
            required if index == first else beat
            for index, beat in enumerate(kept)
        )

    insert_at = max(1, len(directed) - 1)
    if len(directed) < 8:
        return directed[:insert_at] + (required,) + directed[insert_at:]

    # Preserve the hook and closing question when an eight-beat model tour
    # has already used the maximum shot budget.
    replacement = min(max(1, len(directed) - 2), len(directed) - 1)
    return directed[:replacement] + (required,) + directed[replacement + 1:]

# Each completed Reel records the exact narration and shots used. The next
# script request receives this bounded history, making "do not repeat" a
# real constraint instead of an impossible stateless instruction.
SHOWCASE_HISTORY_FILENAME = "showcase_history.json"
SHOWCASE_HISTORY_LIMIT = 8
FALLBACK_TOUR_ATTEMPTS = 24

# Offline fallback material. Every item drives a visibly different HUD
# state; weather and diagnostics are options, never mandatory fixtures.
# The selector below rotates away from recently used states before it
# randomizes, so an API/budget failure cannot silently resurrect the old
# HUD -> weather -> diagnostics template.
INTRO_LINES: tuple[str, ...] = (
    "Hi, I'm Atlas. This is not a static mockup. Let me show you "
    "what is genuinely live on my own screen and what I can do with it.",
    "Hey, it's Atlas -- here's a quick look at what's live on my "
    "display right now, how the pieces connect, and why I keep the "
    "evidence visible instead of asking you to take my word for it.",
    "Atlas here. Let me walk you through what I've got running "
    "today. Every panel you are about to see is part of the real system, "
    "not a collection of screenshots prepared ahead of time.",
)

WEATHER_LINES: tuple[str, ...] = (
    "This is my weather radar -- live conditions and the forecast, "
    "right here on my display.",
    "Here's my weather radar, showing live conditions and the "
    "forecast straight off my own screen.",
    "Right here is my weather radar -- current conditions and "
    "forecast, updated live.",
)

DIAGNOSTICS_LINES: tuple[str, ...] = (
    "And this is my self-diagnostics -- I check my own services, "
    "sensors, and budget, and report exactly what I find.",
    "Now here's my self-diagnostics -- a live check of my own "
    "services, sensors, and budget, reported honestly.",
    "This is self-diagnostics -- a real check on my own services, "
    "sensors, and budget, showing exactly what comes back.",
)

OUTRO_LINES: tuple[str, ...] = (
    "That is one honest look at what I can do without hiding the machinery. "
    "What real task should I take on and show you next?",
    "That is only one slice of what is running on me right now. Which part "
    "should I push further in the next video?",
    "That was my own screen and a real run, not a staged demo. What should "
    "I build, test, or try on camera next?",
)

HUD_FEATURE_BEATS: tuple[dict[str, str], ...] = (
    {
        "narration": (
            "Over here is my system status -- live CPU, memory, and "
            "thermal readings straight off this Pi."
        ),
        "action": "focus_system",
    },
    {
        "narration": (
            "This panel tracks my printer -- real status from the "
            "device, not a guess."
        ),
        "action": "focus_printer",
    },
    {
        "narration": (
            "And this keeps an eye on the gaming PC on the network -- "
            "health and what's running over there."
        ),
        "action": "focus_pc",
    },
    {
        "narration": (
            "This is my social link -- the account and latest Reel "
            "telemetry come back to my own display."
        ),
        "action": "focus_instagram",
    },
    {
        "narration": (
            "My live system log keeps moving while I work, mixing real "
            "telemetry with the interface activity around it."
        ),
        "action": "focus_terminal",
    },
    {
        "narration": (
            "The center core is where my current state, workload, and "
            "voice activity stay visible."
        ),
        "action": "focus_core",
    },
    {
        "narration": random.choice(WEATHER_LINES),
        "action": "weather_open",
    },
    {
        "narration": random.choice(DIAGNOSTICS_LINES),
        "action": "diagnostics",
    },
)
MAX_FEATURE_BEATS = 5

# Beats with "source": "pc" record the Windows PC's own screen instead
# of the HUD (via pc.start_screen_recording's underlying primitive) --
# a deliberate hop over to show real PC control (opening a video,
# opening an app), then back to the HUD for whatever beat follows.
# This is NOT the old mistake of recording the PC screen as a stand-in
# for "record yourself" (see the module docstring) -- it's an
# additional, explicitly-flagged clip source mixed into the same tour,
# stitched into the same final Reel by concat_clips() either way.
PC_DEMO_BEATS: tuple[dict[str, Any], ...] = (
    {
        "narration": (
            "I can drive my own PC too -- here I'm pulling up a video "
            "on YouTube."
        ),
        "source": "pc",
        "pc_action": {
            "type": "youtube_search",
            "query": "raspberry pi home automation projects",
        },
    },
    {
        "narration": (
            "And I can pull up whatever's useful on the PC, not just "
            "watch my own screen."
        ),
        "source": "pc",
        "pc_action": {
            "type": "youtube_search",
            "query": "robotics project builds",
        },
    },
    {
        "narration": (
            "This time I'm leaving a note on the PC while I narrate "
            "the rest from here."
        ),
        "source": "pc",
        "pc_action": {
            "type": "type_text",
            "app": "notepad",
            "text": "Atlas was here — fresh run, fresh evidence.",
        },
    },
    {
        "narration": (
            "A quick hop to the desktop: I can write to the screen and "
            "bring the result back into my own edit."
        ),
        "source": "pc",
        "pc_action": {
            "type": "type_text",
            "app": "notepad",
            "text": "Robot on the Pi. Hands on the PC. One finished Reel.",
        },
    },
)
# open_app is a real, supported pc_action for custom 'beats' -- it's
# just not in this default pool. It only launches apps from the PC
# companion's own approved_apps whitelist (act_open_app in
# atlas_companion.py), which is owner-configured per machine and not
# discoverable from here; confirmed live that a plausible-sounding
# guess ("notepad") isn't actually in it on this PC and just silently
# no-ops (best-effort, same as an unrecognized HUD action -- doesn't
# error, just doesn't change the display for that beat). youtube_search
# has no such whitelist dependency, so it's what the automatic default
# tour uses; a caller who knows a real approved app name can still pass
# open_app explicitly via a custom 'beats' list.
MAX_PC_DEMO_BEATS = 1
# A canned desktop action is rarely interesting twice. The live writer may use
# the whole desktop creatively; the offline fallback stays on Atlas's own HUD
# instead of forcing the same YouTube/Notepad insert into every video.
PC_DEMO_PROBABILITY = 0.0


def _build_default_tour(
    *,
    pc_demo_available: bool = False,
    recent_tours: tuple[dict[str, Any], ...] = (),
) -> tuple[dict[str, Any], ...]:
    """Build a genuinely rotating offline tour.

    The previous fallback hardcoded weather and diagnostics into every
    Reel. This one selects two least-recently-used visual features and
    never puts weather and diagnostics in the same automatic video.
    """
    recent_actions = [
        str(beat.get("action") or "idle")
        for entry in recent_tours
        for beat in entry.get("beats", [])
        if isinstance(entry, dict) and isinstance(beat, dict)
    ]
    action_counts = {
        beat["action"]: recent_actions.count(beat["action"])
        for beat in HUD_FEATURE_BEATS
    }
    minimum_count = min(action_counts.values(), default=0)
    preferred = [
        beat for beat in HUD_FEATURE_BEATS
        if action_counts[beat["action"]] == minimum_count
    ]
    remaining = [
        beat for beat in HUD_FEATURE_BEATS if beat not in preferred
    ]
    random.shuffle(preferred)
    random.shuffle(remaining)
    selected: list[dict[str, Any]] = []

    for beat in [*preferred, *remaining]:
        actions = {item["action"] for item in selected}
        if (
            beat["action"] == "weather_open"
            and "diagnostics" in actions
        ) or (
            beat["action"] == "diagnostics"
            and "weather_open" in actions
        ):
            continue
        selected.append(dict(beat))
        if len(selected) == MAX_FEATURE_BEATS:
            break

    pc_demo: tuple[dict[str, Any], ...] = ()
    if pc_demo_available and random.random() < PC_DEMO_PROBABILITY:
        recent_pc_details = {
            shot[2]
            for entry in recent_tours
            for shot in _tour_signature(
                entry.get("beats") if isinstance(entry, dict) else ()
            )
            if len(shot) >= 3 and shot[0] == "pc"
        }
        pc_pool = [
            beat for beat in PC_DEMO_BEATS
            if str(
                (beat.get("pc_action") or {}).get("query")
                or (beat.get("pc_action") or {}).get("text")
                or ""
            ).casefold() not in recent_pc_details
        ] or list(PC_DEMO_BEATS)
        pc_demo = tuple(
            random.sample(
                pc_pool,
                k=min(MAX_PC_DEMO_BEATS, len(pc_pool)),
            )
        )

    middle = [*selected, *pc_demo]
    random.shuffle(middle)

    return (
        {"narration": random.choice(INTRO_LINES), "action": "idle"},
        *middle,
        {"narration": random.choice(OUTRO_LINES), "action": "idle"},
    )


def _wav_duration_seconds(wav_path) -> float:
    with wave.open(str(wav_path), "rb") as wav_file:
        return wav_file.getnframes() / float(wav_file.getframerate())


def _apply_hud_action(action: str) -> None:
    """Drives a real HUD state change for one tour beat. Best-effort:
    a HUD-state hiccup shouldn't abort the whole recording, it just
    means that beat's clip won't show the intended overlay."""
    try:
        focus = (
            action.removeprefix("focus_")
            if action.startswith("focus_")
            else None
        )
        requests.post(
            f"{HUB}/hud/showcase_focus",
            json={"focus": focus},
            timeout=HUD_REQUEST_TIMEOUT_SECONDS,
        )

        if action == "weather_open":
            requests.post(
                f"{HUB}/hud/weather_overlay",
                json={"open": True},
                timeout=HUD_REQUEST_TIMEOUT_SECONDS,
            )
        elif action in ("weather_close", "idle"):
            requests.post(
                f"{HUB}/hud/weather_overlay",
                json={"open": False},
                timeout=HUD_REQUEST_TIMEOUT_SECONDS,
            )

        if action == "diagnostics":
            import diagnostics

            findings = diagnostics.run_structured_checks()
            requests.post(
                f"{HUB}/diagnostics_report",
                json={"findings": findings},
                timeout=HUD_REQUEST_TIMEOUT_SECONDS,
            )
    except requests.RequestException as error:
        print(
            f"HUD action '{action}' failed during self-showcase "
            f"recording: {error}",
            flush=True,
        )


def _set_recording_indicator(active: bool) -> None:
    try:
        requests.post(
            f"{HUB}/hud/recording",
            json={"active": active},
            timeout=HUD_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        print(f"HUD recording indicator failed: {error}", flush=True)


class PcDemoCaptureError(RuntimeError):
    pass


def _perform_pc_action(
    pc_client: Any,
    pc_action: dict[str, Any] | None,
    clip_seconds: float = 0.0,
    *,
    strict: bool = False,
    pc_demo_director: Any = None,
) -> None:
    """Drives one real action on the PC during a "source": "pc" beat --
    best-effort, like _apply_hud_action: an unrecognized or missing
    action just leaves the desktop showing whatever it already was
    rather than aborting the whole recording."""
    if not isinstance(pc_action, dict):
        return

    action_type = pc_action.get("type")

    try:
        result = None
        if action_type == "youtube_search":
            result = pc_client.execute(
                "youtube_search",
                {
                    "query": str(pc_action.get("query", "")),
                    "fullscreen": True,
                    "private": True,
                },
            )
        elif action_type == "open_app":
            result = pc_client.execute(
                "open_app", {"app": str(pc_action.get("app", ""))}
            )
        elif action_type == "type_text":
            # duration_seconds paces the companion's keystrokes to
            # finish around the same time the narration does, so the
            # typing is still visibly in progress for the whole beat
            # instead of completing in the first second and leaving a
            # static shot -- and isn't still going when edit_reel trims
            # the clip back to the narration's length. TYPING_LEAD_
            # SECONDS lands the last character just before the cut.
            result = pc_client.execute(
                "type_text",
                {
                    "app": str(pc_action.get("app") or "notepad"),
                    "text": str(pc_action.get("text", "")),
                    "duration_seconds": max(
                        1.0, clip_seconds - TYPING_LEAD_SECONDS
                    ),
                },
            )
        elif action_type == "paint_drawing":
            _draw_in_paint(
                pc_client, clip_seconds, pc_action.get("subject")
            )
        elif action_type == "desktop_goal":
            if pc_demo_director is None:
                raise RuntimeError("general desktop director is unavailable")
            directed = pc_demo_director(
                str(pc_action.get("goal") or "Demonstrate one useful PC action."),
                max_steps=int(pc_action.get("max_steps") or 3),
            )
            if not isinstance(directed, dict) or not directed.get("ok"):
                raise RuntimeError(
                    (directed or {}).get("error")
                    if isinstance(directed, dict)
                    else "desktop direction failed"
                )
        elif strict:
            raise RuntimeError(f"unsupported PC demo action: {action_type}")

        if result is not None and (
            not result.ok or not (result.data or {}).get("ok")
        ):
            raise RuntimeError(
                result.error
                or (result.data or {}).get("error")
                or f"{action_type} failed"
            )
    except Exception as error:
        if strict:
            raise PcDemoCaptureError(
                f"PC demo action '{action_type}' failed: {error}"
            ) from error
        print(
            f"PC demo action '{action_type}' failed during "
            f"self-showcase recording: {error}",
            flush=True,
        )


def _prepare_pc_demo_surface(
    pc_client: Any,
    pc_action: dict[str, Any] | None,
) -> None:
    """Move the PC onto a known non-personal surface before capture starts.

    YouTube demos open the generated public search in a private browser
    window first. Typing demos open their approved blank editor first.
    This prevents the opening frames from exposing whichever personal app
    happened to be focused before Atlas began the Reel.
    """
    if not isinstance(pc_action, dict):
        return

    action_type = pc_action.get("type")
    if action_type == "youtube_search":
        _perform_pc_action(pc_client, pc_action, strict=True)
    elif action_type == "paint_drawing":
        _prepare_paint_canvas(pc_client)
    elif action_type == "type_text":
        app = str(pc_action.get("app") or "notepad")
        result = pc_client.execute("focus_or_open_app", {"app": app})
        if not result.ok or not (result.data or {}).get("ok"):
            raise PcDemoCaptureError(
                "could not prepare a clean PC demo surface: "
                f"{result.error or (result.data or {}).get('error')}"
            )
    else:
        return

    time.sleep(PC_SAFE_SURFACE_SETTLE_SECONDS)


def _record_pc_demo_clip(
    pc_client: Any,
    sftp_client: Any,
    pc_action: dict[str, Any] | None,
    clip_seconds: float,
    mission: str | None,
    pc_demo_director: Any = None,
) -> str:
    """Records a real clip of the Windows PC's own screen: starts a PC
    screen recording (the same primitive pc.start_screen_recording
    wraps), performs the beat's PC action so the actual demo -- opening
    a video, opening an app -- gets captured live, waits out the clip's
    duration, stops the recording, and downloads the finished file.
    Raises PcDemoCaptureError with a clear reason on any failure --
    start, stop, or download -- never silently returns a bad path."""
    _prepare_pc_demo_surface(pc_client, pc_action)
    action_type = pc_action.get("type") if isinstance(pc_action, dict) else None
    is_desktop_goal = action_type in ("desktop_goal", "paint_drawing")
    if is_desktop_goal:
        # One extra step covers the loop's final verification turn.
        steps = int(
            pc_action.get("max_steps")
            or (len(PAINT_DRAWINGS.get(
                str(pc_action.get("subject") or ""), PAINT_STROKES
            )) + 4 if action_type == "paint_drawing" else 3)
        ) + 1
        recording_seconds = min(
            PC_DESKTOP_GOAL_MAX_RECORDING_SECONDS,
            max(
                math.ceil(clip_seconds),
                math.ceil(steps * PC_DESKTOP_GOAL_STEP_SECONDS),
            ),
        )
    else:
        recording_seconds = max(1, math.ceil(clip_seconds))
    started_clock = time.monotonic()
    start_result = pc_client.execute(
        "start_recording",
        {
            "target": "full",
            "mission": mission,
            "privacy": False,
            "max_seconds": recording_seconds,
        },
    )
    start_data = start_result.data or {}
    if not start_result.ok or not start_data.get("ok"):
        raise PcDemoCaptureError(
            "could not start the PC screen recording: "
            f"{start_result.error or start_data.get('error')}"
        )

    try:
        _perform_pc_action(
            pc_client,
            pc_action,
            clip_seconds,
            strict=True,
            pc_demo_director=pc_demo_director,
        )
        # The desktop_goal cap above is deliberately generous so ffmpeg
        # cannot truncate the demo. Don't turn that headroom into dead
        # air: once the goal reports done, only hold the recording long
        # enough to cover the narration this clip has to fill.
        hold_seconds = clip_seconds if is_desktop_goal else recording_seconds
        elapsed = time.monotonic() - started_clock
        time.sleep(
            max(0.0, hold_seconds - elapsed)
            + PC_RECORDING_FINALIZE_SECONDS
        )
    except Exception:
        # Never strand the companion's recording_state.json when an input
        # action (most commonly Windows foreground focus) fails mid-beat.
        pc_client.execute("stop_recording")
        raise

    stop_result = pc_client.execute("stop_recording")
    stop_data = stop_result.data or {}
    if not stop_result.ok or not stop_data.get("ok"):
        raise PcDemoCaptureError(
            "could not stop the PC screen recording: "
            f"{stop_result.error or stop_data.get('error')}"
        )

    remote_path = stop_data.get("path")
    if not remote_path:
        raise PcDemoCaptureError(
            "PC recording stopped but reported no file path"
        )

    download_result = None
    for attempt in range(1, PC_DOWNLOAD_ATTEMPTS + 1):
        download_result = sftp_client.download(remote_path)
        if getattr(download_result, "verified", False):
            break
        if attempt < PC_DOWNLOAD_ATTEMPTS:
            time.sleep(PC_DOWNLOAD_RETRY_SECONDS)

    if not getattr(download_result, "verified", False):
        detail = getattr(download_result, "error", None)
        raise PcDemoCaptureError(
            "downloaded PC recording failed size/hash verification after "
            f"{PC_DOWNLOAD_ATTEMPTS} attempts"
            + (f": {detail}" if detail else "")
        )

    return str(download_result.local_path)


def _live_context() -> dict[str, Any]:
    """Real, current facts handed to the script writer so unscripted
    commentary stays honest -- Atlas talks about what is actually on his
    display and actually true of the machine right now, rather than
    inventing a capability. Best-effort: a stats hiccup just means a
    thinner brief, never a failed recording."""
    context: dict[str, Any] = {}

    try:
        import hud_stats

        raw_hud = hud_stats.get_hud_stats()

        def public_fields(section: Any, names: tuple[str, ...]) -> dict[str, Any]:
            if not isinstance(section, dict):
                return {}
            return {
                name: section[name]
                for name in names
                if name in section
                and section[name] is not None
                and isinstance(section[name], (bool, int, float, str))
            }

        context["hud"] = {
            "cpu": public_fields(raw_hud.get("cpu"), ("percent", "temp_c")),
            "memory": public_fields(raw_hud.get("memory"), ("percent",)),
            "disk": public_fields(raw_hud.get("disk"), ("percent",)),
            "weather": public_fields(
                raw_hud.get("weather"),
                ("temp_f", "high_f", "low_f", "precip_chance", "condition", "stale"),
            ),
            "gaming_pc": public_fields(
                raw_hud.get("gaming_pc"),
                (
                    "online", "cpu_percent", "cpu_temp_c", "gpu_percent",
                    "gpu_temp_c", "ram_percent",
                ),
            ),
            "uptime_seconds": raw_hud.get("uptime_seconds"),
        }
    except Exception:
        context["hud_available"] = False

    try:
        import diagnostics

        context["diagnostics"] = [
            {
                "component": str(finding.get("component", "")),
                "ok": bool(finding.get("ok")),
            }
            for finding in diagnostics.run_structured_checks()
            if isinstance(finding, dict) and finding.get("component")
        ]
    except Exception:
        context["diagnostics_available"] = False

    return context


_PRIVATE_TEXT_PATTERNS = (
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"\b[A-Za-z]:\\[^\s]+"),
    re.compile(r"/(?:home|Users|var|etc)/[^\s]+", re.IGNORECASE),
    re.compile(r"@[A-Za-z0-9_.-]+"),
)


def _redact_private_text(value: Any) -> str:
    """Remove private identifiers before creative context reaches a model."""
    text = str(value or "")

    for pattern in _PRIVATE_TEXT_PATTERNS:
        text = pattern.sub("[private detail omitted]", text)

    try:
        import robot_config

        configured_terms = (
            robot_config.get("HOME_CITY", ""),
            robot_config.get("STATION_NAME", ""),
        )
    except Exception:
        configured_terms = ()

    expanded_terms = {
        part.strip()
        for term in configured_terms
        for part in (str(term or ""), *str(term or "").split(","))
        if len(part.strip()) >= 3
    }

    for term in sorted(expanded_terms, key=len, reverse=True):
        if term.casefold() not in {"home", "station-01"}:
            text = re.sub(
                rf"(?<!\w){re.escape(term)}(?!\w)",
                "[private detail omitted]",
                text,
                flags=re.IGNORECASE,
            )

    return text


def _public_recent_tours(
    recent_tours: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    """History for diversity without paths, captions, missions, or PII."""
    public: list[dict[str, Any]] = []

    for entry in recent_tours:
        beats = []
        for beat in entry.get("beats", []):
            if not isinstance(beat, dict):
                continue
            action = beat.get("pc_action")
            safe_action = None
            if isinstance(action, dict):
                safe_action = {
                    "type": action.get("type"),
                    "query": _redact_private_text(action.get("query")),
                    "text": _redact_private_text(action.get("text")),
                    "goal": _redact_private_text(action.get("goal")),
                }
            beats.append({
                "narration": _redact_private_text(beat.get("narration")),
                "action": beat.get("action"),
                "source": beat.get("recorded_source")
                or beat.get("source")
                or "hud",
                "pc_action": safe_action,
            })
        public.append({"beats": beats})

    return public


def _tour_signature(tour: Any) -> tuple[tuple[str, ...], ...]:
    """Visual fingerprint used to reject a recently repeated clip plan.

    Narration is deliberately excluded: swapping synonyms over the same
    dashboard/weather/diagnostics shots is the exact failure this guard is
    meant to catch. PC action content is included because a different
    search or typed message produces genuinely different footage.
    """
    signature: list[tuple[str, ...]] = []

    for beat in tour or ():
        if not isinstance(beat, dict):
            continue

        source = str(
            beat.get("recorded_source")
            or beat.get("source")
            or "hud"
        )

        if source == "pc":
            action = beat.get("pc_action")
            action = action if isinstance(action, dict) else {}
            action_type = str(action.get("type") or "desktop")
            detail = str(
                action.get("query")
                or action.get("text")
                or action.get("goal")
                or action.get("app")
                or ""
            ).casefold()
            signature.append(("pc", action_type, detail))
            continue

        signature.append(("hud", str(beat.get("action") or "idle")))

    return tuple(signature)


def _tour_is_fresh(
    tour: Any,
    recent_tours: tuple[dict[str, Any], ...],
) -> bool:
    candidate = _tour_signature(tour)

    if not candidate:
        return False

    recent_signatures = {
        _tour_signature(entry.get("beats"))
        for entry in recent_tours
        if isinstance(entry, dict)
    }
    if candidate in recent_signatures:
        return False

    if not recent_tours:
        return True

    def meaningful(signature: tuple[tuple[str, ...], ...]) -> set[tuple[str, ...]]:
        return {
            shot for shot in signature
            if shot not in {("hud", "idle"), ("hud", "weather_close")}
        }

    candidate_shots = meaningful(candidate)
    latest_shots = meaningful(
        _tour_signature(recent_tours[-1].get("beats"))
    )
    required_new = min(2, len(candidate_shots))
    return len(candidate_shots - latest_shots) >= required_new


def _load_showcase_history(
    staging_path: Path,
) -> tuple[dict[str, Any], ...]:
    path = staging_path / SHOWCASE_HISTORY_FILENAME

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return ()

    tours = payload.get("tours") if isinstance(payload, dict) else None

    if not isinstance(tours, list):
        return ()

    return tuple(
        entry for entry in tours[-SHOWCASE_HISTORY_LIMIT:]
        if isinstance(entry, dict)
    )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(
        f".{path.name}.{time.time_ns()}.tmp"
    )
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _save_showcase_history(
    staging_path: Path,
    entry: dict[str, Any],
) -> None:
    try:
        tours = list(_load_showcase_history(staging_path))
        tours.append(entry)
        tours = tours[-SHOWCASE_HISTORY_LIMIT:]
        _write_json_atomic(
            staging_path / SHOWCASE_HISTORY_FILENAME,
            {"version": 1, "tours": tours},
        )
        # Keep the caption/shot evidence beside its exact media file too.
        video_path = entry.get("video_path")
        if isinstance(video_path, str) and video_path:
            reel_path = Path(video_path)
            _write_json_atomic(
                reel_path.with_suffix(reel_path.suffix + ".json"),
                {"version": 1, **entry},
            )
    except (OSError, TypeError, ValueError) as error:
        print(
            "Could not save showcase history: "
            f"{type(error).__name__}: {error}",
            flush=True,
        )


def _apply_growth_direction(
    tour: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    plan: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Guarantee the selected promise and viewer question reach the script."""
    directed = tuple(dict(beat) for beat in tour)
    if not directed:
        return directed
    hook = str(plan.get("hook") or "").strip()
    cta = str(plan.get("cta") or "").strip()
    first = directed[0]
    last = directed[-1]
    opening = str(first.get("narration") or "").strip()
    if (
        hook
        and hook.casefold() not in opening.casefold()
        and not _opening_covers_hook(hook, opening)
    ):
        first["narration"] = f"{hook} {str(first.get('narration') or '').strip()}".strip()
    closing = str(last.get("narration") or "").strip()
    if cta and "?" not in closing:
        last["narration"] = f"{closing} {cta}".strip()
    return directed


def _opening_covers_hook(hook: str, opening: str) -> bool:
    """Recognize a model-written paraphrase so growth does not say it twice."""
    stop_words = {
        "a", "an", "and", "can", "in", "is", "it", "of", "on", "one",
        "really", "the", "this", "to", "today",
    }
    hook_words = set(re.findall(r"[a-z0-9]+", hook.casefold())) - stop_words
    first_sentence = re.split(r"[.!?]", opening, maxsplit=1)[0]
    opening_words = (
        set(re.findall(r"[a-z0-9]+", first_sentence.casefold())) - stop_words
    )
    if not hook_words or not opening_words:
        return False
    shared = len(hook_words & opening_words)
    return shared / min(len(hook_words), len(opening_words)) >= 0.7


def _resolve_tour(
    script_writer: Any,
    *,
    pc_demo_available: bool,
    recent_tours: tuple[dict[str, Any], ...] = (),
    creative_brief: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Picks the tour for a default (no explicit 'beats') recording.

    Prefers a freshly written, unscripted one; falls back to the canned
    randomized tour if scripting is unavailable or returns anything this
    runtime can't actually execute. The fallback is the point: a dead
    API key or a budget stop should cost variety, not the video."""
    live_context = _live_context()
    live_context["recent_showcase_tours"] = _public_recent_tours(
        recent_tours
    )
    live_context["creative_brief"] = _redact_private_text(
        creative_brief
    )

    if script_writer is not None:
        try:
            written = script_writer(
                pc_demo_available=pc_demo_available,
                context=live_context,
            )
            if _tour_is_fresh(written, recent_tours):
                return written

            raise RuntimeError(
                "the generated shot sequence repeats a recent Reel"
            )
        except Exception as error:
            print(
                "Unscripted showcase generation failed, falling back to "
                f"the canned tour: {error}",
                flush=True,
            )

    fallback = _build_default_tour(
        pc_demo_available=pc_demo_available,
        recent_tours=recent_tours,
    )

    for _ in range(FALLBACK_TOUR_ATTEMPTS - 1):
        if _tour_is_fresh(fallback, recent_tours):
            break
        fallback = _build_default_tour(
            pc_demo_available=pc_demo_available,
            recent_tours=recent_tours,
        )

    return fallback


def _preview_reel(video_path: str) -> tuple[bool, str | None]:
    """Play the finished Reel on Atlas's HUD and HDMI speaker.

    The hub request intentionally blocks until playback finishes, which
    guarantees the voice controller cannot ask "post or save?" early.
    A preview failure never destroys an otherwise valid finished Reel.
    """
    try:
        response = requests.post(
            f"{HUB}/hud/reel_preview",
            json={"video_path": video_path},
            timeout=REEL_PREVIEW_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and not payload.get("ok"):
            return False, str(payload.get("error") or "preview failed")
        return True, None
    except (requests.RequestException, AttributeError, ValueError) as error:
        return False, f"{type(error).__name__}: {error}"


def register_content_tools(
    registry: ToolRegistry,
    verifier: ResultVerifier,
    *,
    staging_directory: str | Path,
    pc_client: Any = None,
    sftp_client: Any = None,
    script_writer: Any = None,
    caption_writer: Any = None,
    growth_writer: Any = None,
    enable_growth_package: bool = False,
    enable_facebook_publish: bool = False,
    enable_youtube_publish: bool = False,
    enable_combined_social_publish: bool = False,
    desktop_reels_remote_root: str | None = None,
    growth_database_path: str | Path | None = None,
    enforce_reel_duration: bool = False,
    pc_demo_director: Any = None,
) -> list[AtlasTool]:
    staging_path = Path(staging_directory)
    growth_store = (
        GrowthStore(growth_database_path or DEFAULT_DATABASE_PATH)
        if enable_growth_package
        else None
    )

    def record_self_showcase(
        mission: str | None = None,
        beats: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if mission is not None and not isinstance(mission, str):
            raise ValueError("mission must be a string or null")

        if beats is not None and (
            not isinstance(beats, list)
            or not all(
                isinstance(beat, dict)
                and isinstance(beat.get("narration"), str)
                and beat["narration"].strip()
                for beat in beats
            )
        ):
            raise ValueError(
                "beats must be a list of objects each with a "
                "non-empty 'narration' string, or null for the "
                "default tour"
            )

        is_custom_script = bool(beats)
        recent_tours = _load_showcase_history(staging_path)
        growth_plan = growth_store.plan_reel(mission) if growth_store is not None else {}
        creative_brief = mission
        if growth_plan:
            creative_brief = "\n".join(
                part for part in (
                    mission,
                    f"Series: {growth_plan['series']}",
                    f"Opening promise: {growth_plan['hook']}",
                    f"Closing viewer question: {growth_plan['cta']}",
                    f"Angle: {growth_plan['series_angle']}",
                )
                if part
            )
        tour = beats if beats else _resolve_tour(
            script_writer,
            pc_demo_available=(
                pc_client is not None and sftp_client is not None
            ),
            recent_tours=recent_tours,
            creative_brief=creative_brief,
        )
        required_scene = _required_pc_scene(mission)
        if required_scene is not None and (
            pc_client is None
            or sftp_client is None
            or pc_demo_director is None
        ):
            return {
                "ok": False,
                "error": (
                    "This Reel explicitly requires a recorded Microsoft "
                    "Paint demonstration, but verified PC recording/control "
                    "is unavailable. No misleading HUD-only Reel was produced."
                ),
            }
        tour = _ensure_required_pc_scene(tour, mission)
        if enable_growth_package and not is_custom_script:
            tour = _apply_growth_direction(tour, growth_plan)

        if (
            is_custom_script
            and any(beat.get("source") == "pc" for beat in tour)
            and (pc_client is None or sftp_client is None)
        ):
            return {
                "ok": False,
                "error": (
                    "This Reel explicitly requested a PC clip, but "
                    "this runtime has no PC connection."
                ),
            }

        staging_path.mkdir(parents=True, exist_ok=True)

        clip_paths: list[str] = []
        narration_lines: list[str] = []
        recorded_beats: list[dict[str, Any]] = []
        subtitle_cues: list[dict[str, Any]] = []
        narration_duration_seconds = 0.0

        _set_recording_indicator(True)
        try:
            for index, beat in enumerate(tour):
                narration_text = beat["narration"]
                action = beat.get("action", "idle")
                source = beat.get("source", "hud")

                try:
                    wav_path = content_pipeline.render_narration(
                        narration_text
                    )
                except content_pipeline.ContentPipelineError as error:
                    return {"ok": False, "error": str(error)}

                narration_seconds = _wav_duration_seconds(wav_path)
                cue_start = narration_duration_seconds
                narration_duration_seconds += narration_seconds
                subtitle_cues.append(
                    {
                        "start": round(cue_start, 3),
                        "end": round(narration_duration_seconds, 3),
                        "text": narration_text,
                    }
                )
                clip_seconds = narration_seconds + RECORDING_BUFFER_SECONDS

                record_on_hud = source != "pc"

                if source == "pc":
                    if pc_client is None or sftp_client is None:
                        Path(wav_path).unlink(missing_ok=True)
                        for prior_clip in clip_paths:
                            Path(prior_clip).unlink(missing_ok=True)
                        return {
                            "ok": False,
                            "error": (
                                "This Reel requires a PC demo clip, but "
                                "the runtime has no PC connection. No "
                                "incomplete HUD-only Reel was produced."
                            ),
                        }
                    else:
                        pc_error = None
                        # Freeform desktop goals can already have visible side
                        # effects when their director fails to verify the last
                        # step. Replaying the whole beat duplicated Paint in a
                        # live Reel attempt. Retry capture/transfer beats that
                        # are safe to repeat, but never restart an autonomous
                        # desktop task from the beginning.
                        pc_attempts = (
                            1
                            if isinstance(beat.get("pc_action"), dict)
                            and beat["pc_action"].get("type") == "desktop_goal"
                            else 2
                        )
                        for pc_attempt in range(pc_attempts):
                            try:
                                raw_clip_path = _record_pc_demo_clip(
                                    pc_client,
                                    sftp_client,
                                    beat.get("pc_action"),
                                    clip_seconds,
                                    mission,
                                    pc_demo_director,
                                )
                                pc_error = None
                                break
                            except PcDemoCaptureError as error:
                                pc_error = error
                                if pc_attempt + 1 < pc_attempts:
                                    time.sleep(PC_DOWNLOAD_RETRY_SECONDS)
                        if pc_error is not None:
                            Path(wav_path).unlink(missing_ok=True)
                            for prior_clip in clip_paths:
                                Path(prior_clip).unlink(missing_ok=True)
                            return {
                                "ok": False,
                                "error": (
                                    "PC footage failed"
                                    + (" after retry" if pc_attempts > 1 else "")
                                    + f": {pc_error}. No "
                                    "incomplete HUD-only Reel was produced."
                                ),
                            }

                if record_on_hud:
                    _apply_hud_action(action)
                    time.sleep(HUD_ACTION_SETTLE_SECONDS)

                    raw_clip_path = (
                        staging_path
                        / f"hud_raw_{index}_{int(time.time())}.mp4"
                    )

                    try:
                        hud_capture.record_hud_clip(
                            clip_seconds, raw_clip_path
                        )
                    except hud_capture.HudCaptureError as error:
                        Path(wav_path).unlink(missing_ok=True)
                        return {"ok": False, "error": str(error)}

                beat_clip_path = (
                    staging_path
                    / f"hud_beat_{index}_{int(time.time())}.mp4"
                )

                try:
                    content_pipeline.edit_reel(
                        raw_clip_path, wav_path, beat_clip_path
                    )
                except content_pipeline.ContentPipelineError as error:
                    return {"ok": False, "error": str(error)}
                finally:
                    Path(wav_path).unlink(missing_ok=True)
                    Path(raw_clip_path).unlink(missing_ok=True)

                clip_paths.append(str(beat_clip_path))
                narration_lines.append(narration_text)
                recorded_beats.append({
                    **beat,
                    "recorded_source": (
                        "hud" if record_on_hud else "pc"
                    ),
                })
        finally:
            _set_recording_indicator(False)
            _apply_hud_action("idle")

        if enforce_reel_duration and not (
            MIN_REEL_SECONDS
            <= narration_duration_seconds
            <= MAX_REEL_SECONDS
        ):
            for clip_path in clip_paths:
                Path(clip_path).unlink(missing_ok=True)
            return {
                "ok": False,
                "error": (
                    "The generated Reel narration would run "
                    f"{narration_duration_seconds:.1f} seconds; finished "
                    f"Reels must be {MIN_REEL_SECONDS:.0f}-"
                    f"{MAX_REEL_SECONDS:.0f} seconds. No out-of-range Reel "
                    "was saved."
                ),
            }

        reel_stamp = int(time.time())
        out_path = staging_path / f"reel_{reel_stamp}.mp4"
        concat_path = (
            staging_path / f"reel_{reel_stamp}_unbranded.mp4"
            if enable_growth_package
            else out_path
        )

        try:
            content_pipeline.concat_clips(clip_paths, concat_path)
        except content_pipeline.ContentPipelineError as error:
            return {"ok": False, "error": str(error)}
        finally:
            for clip_path in clip_paths:
                Path(clip_path).unlink(missing_ok=True)

        branding_error = None
        if enable_growth_package:
            try:
                content_pipeline.brand_reel(
                    concat_path,
                    out_path,
                    cues=subtitle_cues,
                    title=str(growth_plan.get("title") or "Atlas"),
                    series=str(growth_plan.get("series") or "Building Atlas"),
                )
            except content_pipeline.ContentPipelineError as error:
                branding_error = str(error)
                Path(concat_path).replace(out_path)
            else:
                Path(concat_path).unlink(missing_ok=True)

        caption = None
        if caption_writer is not None:
            try:
                caption = caption_writer(
                    beats=recorded_beats,
                    recent_captions=[
                        str(entry.get("caption") or "")
                        for entry in recent_tours
                        if isinstance(entry, dict)
                    ],
                )
            except Exception as error:
                print(
                    "Personality caption generation failed; using the "
                    f"local caption fallback: {error}",
                    flush=True,
                )
        if not caption:
            caption = content_pipeline.build_caption(
                " ".join(narration_lines)
            )
        caption = content_pipeline.ensure_raspberry_pi_hashtags(caption)
        translations: dict[str, dict[str, Any]] = {}
        generated_growth_assets: dict[str, Any] = {}
        growth_asset_error = None
        if enable_growth_package and growth_writer is not None:
            try:
                generated_growth_assets = growth_writer(
                    beats=recorded_beats,
                    plan=growth_plan,
                )
                growth_plan["title"] = (
                    generated_growth_assets.get("title")
                    or growth_plan.get("title")
                )
                _apply_best_hook(
                    growth_plan,
                    generated_growth_assets.get("hook_candidates") or [],
                )
                translations = generated_growth_assets.get("translations") or {}
            except Exception as error:
                growth_asset_error = f"{type(error).__name__}: {error}"

        package_manifest = None
        package_error = None
        package_path = staging_path / f"reel_{reel_stamp}_package"
        if enable_growth_package:
            try:
                package_manifest = reel_package.create_distribution_package(
                    master_video=out_path,
                    package_directory=package_path,
                    plan=growth_plan,
                    caption=caption,
                    cues=subtitle_cues,
                    translations=translations,
                )
            except (OSError, ValueError, content_pipeline.ContentPipelineError) as error:
                package_error = f"{type(error).__name__}: {error}"

        created_at = time.time()
        output = {
            "ok": True,
            "video_path": str(out_path),
            "caption": caption,
            "mission": mission,
            "beats": recorded_beats,
            "duration_seconds": round(narration_duration_seconds, 3),
            "growth_plan": growth_plan or None,
            "growth_package": package_manifest,
            "package_path": str(package_path) if package_manifest else None,
            "branding_error": branding_error,
            "growth_asset_error": growth_asset_error,
            "package_error": package_error,
        }
        # Keep the draft local until the owner chooses post, save, or delete.
        # Both post and save explicitly call content.save_showcase first, so a
        # delete choice never leaves an unwanted copy behind on the Desktop.
        if enable_growth_package:
            local_id = growth_store.record_draft(
                {
                    **output,
                    "created_at": created_at,
                }
            )
            output["growth_local_id"] = local_id
            for variant in (package_manifest or {}).get("trial_variants", []):
                growth_store.record_experiment(
                    local_id,
                    str(variant.get("name")),
                    str(variant.get("hook")),
                    str(variant.get("video_path")),
                )
        _save_showcase_history(
            staging_path,
            {
                "created_at": created_at,
                "video_path": str(out_path),
                "caption": caption,
                "mission": mission,
                "beats": recorded_beats,
                "growth_plan": growth_plan or None,
                "package_path": str(package_path) if package_manifest else None,
            },
        )
        previewed, preview_error = _preview_reel(str(out_path))
        output["previewed"] = previewed
        output["preview_error"] = preview_error
        return output

    def save_showcase(
        video_path: str,
        package_path: str | None = None,
    ) -> dict[str, Any]:
        if (
            desktop_reels_remote_root is None
            or pc_client is None
            or sftp_client is None
        ):
            return {
                "ok": False,
                "error": "Verified Windows Desktop export is not configured.",
            }
        try:
            reel = Path(video_path).expanduser().resolve(strict=True)
            reel.relative_to(staging_path.resolve())
            if reel.suffix.casefold() != ".mp4":
                raise ValueError("the saved Reel must be an MP4")
            package = None
            if package_path is not None:
                package = Path(package_path).expanduser().resolve(strict=True)
                package.relative_to(staging_path.resolve())
                if not package.is_dir():
                    raise ValueError("the Reel package is not a directory")
        except (OSError, ValueError) as error:
            return {"ok": False, "error": f"Invalid Reel path: {error}"}

        return _export_reel_to_desktop(
            video_path=reel,
            package_path=package,
            pc_client=pc_client,
            sftp_client=sftp_client,
            remote_root=desktop_reels_remote_root,
        )

    def delete_showcase(
        video_path: str,
        package_path: str | None = None,
    ) -> dict[str, Any]:
        try:
            root = staging_path.resolve()
            reel = Path(video_path).expanduser().resolve(strict=True)
            reel.relative_to(root)
            if reel.suffix.casefold() != ".mp4" or not reel.name.startswith("reel_"):
                raise ValueError("only a staged reel_*.mp4 may be deleted")
            targets: list[Path] = [
                reel,
                reel.with_suffix(reel.suffix + ".json"),
            ]
            if package_path is not None:
                package = Path(package_path).expanduser().resolve(strict=True)
                package.relative_to(root)
                if not package.is_dir():
                    raise ValueError("the Reel package is not a directory")
                targets.append(package)
        except (OSError, ValueError) as error:
            return {"ok": False, "error": f"Invalid Reel path: {error}"}

        deleted: list[str] = []
        try:
            for target in targets:
                if target.is_dir():
                    shutil.rmtree(target)
                    deleted.append(str(target))
                elif target.exists():
                    target.unlink()
                    deleted.append(str(target))
        except OSError as error:
            return {
                "ok": False,
                "deleted": deleted,
                "error": f"Could not delete the complete Reel: {error}",
            }
        return {"ok": True, "deleted": deleted, "video_path": str(reel)}

    def publish_to_instagram(
        video_path: str,
        caption: str,
        mission: str | None = None,
    ) -> dict[str, Any]:
        if (
            not isinstance(video_path, str)
            or not video_path.strip()
        ):
            raise ValueError(
                "video_path must be a non-empty string"
            )

        if (
            not isinstance(caption, str)
            or not caption.strip()
        ):
            raise ValueError(
                "caption must be a non-empty string"
            )

        if mission is not None and not isinstance(
            mission, str
        ):
            raise ValueError(
                "mission must be a string or null"
            )

        try:
            caption = content_pipeline.ensure_raspberry_pi_hashtags(caption)
            result = instagram_publish.publish_reel(
                video_path,
                caption,
                dry_run=False,
                mission=mission,
            )
        except instagram_publish.InstagramPublishError as error:
            return {"ok": False, "error": str(error)}

        if growth_store is not None:
            growth_store.record_publish(
                {
                    **result,
                    "video_path": result.get("video_path") or video_path,
                    "caption": result.get("caption") or caption,
                    "mission": result.get("mission") or mission,
                }
            )
        return {"ok": True, **result}

    def publish_to_youtube(
        video_path: str,
        title: str,
        description: str,
        privacy_status: str,
        mission: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(video_path, str) or not video_path.strip():
            raise ValueError("video_path must be a non-empty string")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("description must be a non-empty string")
        if privacy_status not in youtube_publish.ALLOWED_PRIVACY_STATUSES:
            raise ValueError(
                "privacy_status must be private, unlisted, or public"
            )
        if mission is not None and not isinstance(mission, str):
            raise ValueError("mission must be a string or null")

        description = content_pipeline.ensure_raspberry_pi_hashtags(
            description
        )
        try:
            result = youtube_publish.publish_short(
                video_path,
                title[: youtube_publish.YOUTUBE_TITLE_MAX_LENGTH],
                description,
                privacy_status=privacy_status,
                mission=mission,
                tags=[
                    hashtag.lstrip("#")
                    for hashtag in content_pipeline.RASPBERRY_PI_HASHTAGS
                ],
            )
        except youtube_publish.YouTubePublishError as error:
            return {"ok": False, "error": str(error)}

        if growth_store is not None:
            growth_store.record_publish(
                {
                    **result,
                    "platform": "youtube",
                    "media_id": (
                        f"youtube:{result.get('video_id')}"
                        if result.get("video_id")
                        else None
                    ),
                    "caption": description,
                }
            )
        return {"ok": True, **result}

    def publish_to_facebook(
        video_path: str,
        title: str,
        description: str,
        mission: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(video_path, str) or not video_path.strip():
            raise ValueError("video_path must be a non-empty string")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("description must be a non-empty string")
        if mission is not None and not isinstance(mission, str):
            raise ValueError("mission must be a string or null")

        description = content_pipeline.ensure_raspberry_pi_hashtags(
            description
        )
        try:
            result = facebook_publish.publish_reel(
                video_path,
                title[: facebook_publish.MAX_TITLE_LENGTH],
                description,
                mission=mission,
            )
        except facebook_publish.FacebookPublishError as error:
            return {"ok": False, "error": str(error)}

        if growth_store is not None:
            growth_store.record_publish({
                **result,
                "platform": "facebook",
                "media_id": (
                    f"facebook:{result.get('video_id')}"
                    if result.get("video_id")
                    else None
                ),
                "caption": description,
            })
        return {"ok": True, **result}

    def publish_to_socials(
        video_path: str,
        title: str,
        caption: str,
        mission: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(video_path, str) or not video_path.strip():
            raise ValueError("video_path must be a non-empty string")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string")
        if not isinstance(caption, str) or not caption.strip():
            raise ValueError("caption must be a non-empty string")
        if mission is not None and not isinstance(mission, str):
            raise ValueError("mission must be a string or null")

        caption = content_pipeline.ensure_raspberry_pi_hashtags(caption)
        try:
            result = social_publish.publish_reel(
                video_path,
                title[: facebook_publish.MAX_TITLE_LENGTH],
                caption,
                mission=mission,
            )
        except social_publish.SocialPublishError as error:
            return {"ok": False, "error": str(error)}

        if growth_store is not None and result.get("ok"):
            instagram_result = result.get("instagram") or {}
            growth_store.record_publish(
                {
                    "platform": "instagram_and_facebook",
                    "media_id": instagram_result.get("media_id"),
                    "permalink": instagram_result.get("permalink"),
                    "video_path": result.get("video_path") or video_path,
                    "caption": result.get("caption") or caption,
                    "mission": result.get("mission") or mission,
                    "posted_at": result.get("released_at"),
                }
            )
        return result

    def get_growth_report() -> dict[str, Any]:
        if growth_store is None:
            return {"ok": False, "error": "growth memory is disabled"}
        return {"ok": True, **growth_store.report()}

    def list_viewer_missions(limit: int = 5) -> dict[str, Any]:
        if growth_store is None:
            return {"ok": False, "error": "growth memory is disabled"}
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 25:
            raise ValueError("limit must be an integer from 1 through 25")
        missions = growth_store.list_mission_drafts(limit)
        return {"ok": True, "missions": missions, "count": len(missions)}

    tools = [
        AtlasTool(
            name="content.record_self_showcase",
            description=(
                "Records a narrated tour of Atlas's own HUD screen -- "
                "and, when a PC connection is configured, can "
                "hop over to a real clip of the Windows PC's own "
                "screen for one or more beats (opening a YouTube video, "
                "opening an app) before hopping back -- then edits "
                "everything into one 9:16 Reel, returning the finished "
                "local video path and a draft caption. Does not "
                "publish anything. Atlas writes the "
                "whole video fresh at record time -- what he talks "
                "about, in what order, how many beats, and whether he "
                "hops over to the PC and what he does there -- using "
                "his real current state and saved shot history as "
                "context. The finished Reel is automatically played "
                "back on Atlas with audio before this tool returns. "
                "Put every requested subject, named app, and required "
                "on-camera scene verbatim in 'mission'; explicit scenes "
                "are mandatory and the recording fails rather than "
                "substituting HUD footage. Do not write a beat list in "
                "the planning step."
            ),
            runs_on="pi",
            handler=record_self_showcase,
            permission_level=0,
            timeout_seconds=SHOWCASE_TOOL_TIMEOUT_SECONDS,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mission": {
                            "type": ["string", "null"],
                        },
                    },
                    "required": ["mission"],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="content.save_showcase",
            description=(
                "Copy an exact finished local Reel and its curated package "
                "to the owner's Windows Desktop with transfer verification. "
                "Used only after the owner chooses save or post."
            ),
            runs_on="pi",
            handler=save_showcase,
            permission_level=0,
            timeout_seconds=300,
            metadata={
                "openai_plannable": False,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "video_path": {"type": "string"},
                        "package_path": {"type": ["string", "null"]},
                    },
                    "required": ["video_path", "package_path"],
                    "additionalProperties": False,
                },
            },
        ),
        AtlasTool(
            name="content.delete_showcase",
            description=(
                "Delete the exact staged Reel, its evidence sidecar, and its "
                "local package after the owner explicitly chooses delete."
            ),
            runs_on="pi",
            handler=delete_showcase,
            permission_level=2,
            timeout_seconds=60,
            metadata={
                "openai_plannable": False,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "video_path": {"type": "string"},
                        "package_path": {"type": ["string", "null"]},
                    },
                    "required": ["video_path", "package_path"],
                    "additionalProperties": False,
                },
            },
        ),
        AtlasTool(
            name="content.publish_to_instagram",
            description=(
                "Publish an exact finished Reel and caption to "
                "the Instagram account. Public and irreversible "
                "-- requires explicit confirmation of the exact "
                "media and caption before it runs."
            ),
            runs_on="pi",
            handler=publish_to_instagram,
            permission_level=2,
            timeout_seconds=180,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "video_path": {
                            "type": "string",
                            "description": (
                                "Exact local path returned by "
                                "content.record_self_showcase."
                            ),
                        },
                        "caption": {
                            "type": "string",
                            "description": (
                                "Exact caption to review with "
                                "the owner before publishing."
                            ),
                        },
                        "mission": {
                            "type": ["string", "null"],
                        },
                    },
                    "required": [
                        "video_path",
                        "caption",
                        "mission",
                    ],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="content.publish_to_youtube",
            description=(
                "Upload an exact finished vertical MP4 as a YouTube Short. "
                "Public and irreversible even when requested as private -- "
                "requires explicit owner confirmation of the exact video, "
                "title, description, and privacy status before it runs. "
                "Google restricts uploads from unaudited API projects to "
                "private viewing regardless of the requested status."
            ),
            runs_on="pi",
            handler=publish_to_youtube,
            permission_level=2,
            timeout_seconds=720,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "video_path": {
                            "type": "string",
                            "description": (
                                "Exact local MP4 path returned by "
                                "content.record_self_showcase."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "maxLength": 100,
                        },
                        "description": {
                            "type": "string",
                            "maxLength": 5000,
                        },
                        "privacy_status": {
                            "type": "string",
                            "enum": ["private", "unlisted", "public"],
                        },
                        "mission": {
                            "type": ["string", "null"],
                        },
                    },
                    "required": [
                        "video_path",
                        "title",
                        "description",
                        "privacy_status",
                        "mission",
                    ],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="content.publish_to_facebook",
            description=(
                "Publish an exact finished vertical MP4 as a Reel on the "
                "ATLAS AI Robot Facebook Page. Public and externally visible "
                "-- requires explicit owner confirmation of the exact video, "
                "title, and description before it runs."
            ),
            runs_on="pi",
            handler=publish_to_facebook,
            permission_level=2,
            timeout_seconds=720,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "video_path": {
                            "type": "string",
                            "description": (
                                "Exact local MP4 path returned by "
                                "content.record_self_showcase."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "maxLength": 255,
                        },
                        "description": {
                            "type": "string",
                            "maxLength": 5000,
                        },
                        "mission": {"type": ["string", "null"]},
                    },
                    "required": [
                        "video_path",
                        "title",
                        "description",
                        "mission",
                    ],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="content.publish_to_socials",
            description=(
                "Publish one exact finished Reel to both Instagram and the "
                "ATLAS AI Robot Facebook Page as one coordinated action. Both "
                "uploads are prepared first, then the final publish requests "
                "are started together. Public and irreversible -- one explicit "
                "owner confirmation approves both exact posts."
            ),
            runs_on="pi",
            handler=publish_to_socials,
            permission_level=2,
            timeout_seconds=900,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "video_path": {
                            "type": "string",
                            "description": (
                                "Exact local MP4 path returned by "
                                "content.record_self_showcase."
                            ),
                        },
                        "title": {"type": "string", "maxLength": 255},
                        "caption": {"type": "string", "maxLength": 5000},
                        "mission": {"type": ["string", "null"]},
                    },
                    "required": [
                        "video_path", "title", "caption", "mission",
                    ],
                    "additionalProperties": False,
                }
            },
        ),
    ]

    if not enable_facebook_publish:
        tools = [
            tool
            for tool in tools
            if tool.name != "content.publish_to_facebook"
        ]
    if not enable_youtube_publish:
        tools = [
            tool
            for tool in tools
            if tool.name != "content.publish_to_youtube"
        ]
    if enable_combined_social_publish:
        tools = [
            tool for tool in tools
            if tool.name not in {
                "content.publish_to_instagram",
                "content.publish_to_facebook",
                "content.publish_to_youtube",
            }
        ]
    else:
        tools = [
            tool for tool in tools
            if tool.name != "content.publish_to_socials"
        ]

    if growth_store is not None:
        tools.extend(
            [
                AtlasTool(
                    name="content.get_growth_report",
                    description=(
                        "Read Atlas's local Reel growth memory: published/draft "
                        "counts, newest performance, best observed series, and "
                        "the internally recommended next series. Read-only and "
                        "does not contact or publish to any social platform."
                    ),
                    runs_on="pi",
                    handler=get_growth_report,
                    permission_level=0,
                    metadata={
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                            "additionalProperties": False,
                        }
                    },
                ),
                AtlasTool(
                    name="content.list_viewer_missions",
                    description=(
                        "List safe local video-mission drafts derived from public "
                        "viewer requests. This never replies to viewers and never "
                        "records or publishes by itself."
                    ),
                    runs_on="pi",
                    handler=list_viewer_missions,
                    permission_level=0,
                    metadata={
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "limit": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 25,
                                }
                            },
                            "required": ["limit"],
                            "additionalProperties": False,
                        }
                    },
                ),
            ]
        )

    for tool in tools:
        registry.register(tool)

    verifier.register(
        "content.record_self_showcase",
        _verify_record_self_showcase,
    )
    verifier.register(
        "content.save_showcase",
        _verify_save_showcase,
    )
    verifier.register(
        "content.delete_showcase",
        _verify_delete_showcase,
    )
    verifier.register(
        "content.publish_to_instagram",
        _verify_publish_to_instagram,
    )
    if enable_combined_social_publish:
        verifier.register(
            "content.publish_to_socials",
            _verify_publish_to_socials,
        )
    if enable_facebook_publish:
        verifier.register(
            "content.publish_to_facebook",
            _verify_publish_to_facebook,
        )
    if enable_youtube_publish:
        verifier.register(
            "content.publish_to_youtube",
            _verify_publish_to_youtube,
        )
    if growth_store is not None:
        verifier.register(
            "content.get_growth_report",
            _verify_growth_read,
        )
        verifier.register(
            "content.list_viewer_missions",
            _verify_growth_read,
        )

    return tools


def _verify_record_self_showcase(call, result) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason="Recording output was not an object.",
        )

    video_path = output.get("video_path")
    verified = (
        output.get("ok") is True
        and bool(video_path)
        and Path(video_path).is_file()
        and Path(video_path).stat().st_size > 0
    )
    return VerificationCheck(
        verified=verified,
        reason=(
            "The edited Reel exists on disk with real bytes."
            if verified
            else "The self-showcase recording/edit did not produce a usable file."
        ),
        evidence={
            "video_path": video_path,
            "error": output.get("error"),
        },
    )


def _verify_save_showcase(call, result) -> VerificationCheck:
    output = result.output
    verified = (
        isinstance(output, dict)
        and output.get("ok") is True
        and isinstance(output.get("folder"), str)
        and bool(output.get("folder"))
        and isinstance(output.get("file_count"), int)
        and output.get("file_count") >= 1
    )
    return VerificationCheck(
        verified=verified,
        reason=(
            "The Reel was hash-verified on the Windows Desktop."
            if verified
            else "The Windows Desktop copy was not verified."
        ),
        evidence=output if isinstance(output, dict) else {},
    )


def _verify_delete_showcase(call, result) -> VerificationCheck:
    output = result.output
    video_path = output.get("video_path") if isinstance(output, dict) else None
    verified = (
        isinstance(output, dict)
        and output.get("ok") is True
        and isinstance(video_path, str)
        and not Path(video_path).exists()
    )
    return VerificationCheck(
        verified=verified,
        reason=(
            "The staged Reel and its local package were deleted."
            if verified
            else "The staged Reel deletion was not verified."
        ),
        evidence=output if isinstance(output, dict) else {},
    )

def _verify_publish_to_instagram(call, result) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason="Publish output was not an object.",
        )

    verified = (
        output.get("ok") is True
        and bool(output.get("permalink"))
    )
    return VerificationCheck(
        verified=verified,
        reason=(
            "The post was verified live with a real permalink."
            if verified
            else "The publish did not return a verified permalink."
        ),
        evidence={
            "permalink": output.get("permalink"),
            "media_id": output.get("media_id"),
            "error": output.get("error"),
        },
    )


def _verify_publish_to_youtube(call, result) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason="YouTube publish output was not an object.",
        )

    verified = (
        output.get("ok") is True
        and bool(output.get("video_id"))
        and bool(output.get("permalink"))
        and output.get("privacy_status")
        in youtube_publish.ALLOWED_PRIVACY_STATUSES
    )
    return VerificationCheck(
        verified=verified,
        reason=(
            "The YouTube upload was read back with a real video id and privacy status."
            if verified
            else "The YouTube upload could not be verified live."
        ),
        evidence={
            "video_id": output.get("video_id"),
            "permalink": output.get("permalink"),
            "privacy_status": output.get("privacy_status"),
            "processing_status": output.get("processing_status"),
            "error": output.get("error"),
        },
    )


def _verify_publish_to_facebook(call, result) -> VerificationCheck:
    output = result.output
    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason="Facebook publish output was not an object.",
        )
    verified = (
        output.get("ok") is True
        and bool(output.get("video_id"))
        and bool(output.get("permalink"))
        and output.get("publishing_status")
        in facebook_publish.TERMINAL_SUCCESS_STATUSES
    )
    return VerificationCheck(
        verified=verified,
        reason=(
            "The Facebook Reel was verified live with a video id and permalink."
            if verified
            else "The Facebook Reel publish could not be verified live."
        ),
        evidence={
            "video_id": output.get("video_id"),
            "permalink": output.get("permalink"),
            "publishing_status": output.get("publishing_status"),
            "error": output.get("error"),
        },
    )


def _verify_publish_to_socials(call, result) -> VerificationCheck:
    output = result.output
    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason="Coordinated social publish output was not an object.",
        )
    instagram = output.get("instagram")
    facebook = output.get("facebook")
    verified = (
        output.get("ok") is True
        and isinstance(instagram, dict)
        and instagram.get("verified") is True
        and bool(instagram.get("permalink"))
        and isinstance(facebook, dict)
        and facebook.get("verified") is True
        and bool(facebook.get("permalink"))
    )
    return VerificationCheck(
        verified=verified,
        reason=(
            "Both social posts were verified live with real permalinks."
            if verified
            else "Both social posts could not be verified; inspect each platform result."
        ),
        evidence={
            "instagram_permalink": (
                instagram.get("permalink")
                if isinstance(instagram, dict) else None
            ),
            "facebook_permalink": (
                facebook.get("permalink")
                if isinstance(facebook, dict) else None
            ),
            "error": output.get("error"),
        },
    )

def _verify_growth_read(call, result) -> VerificationCheck:
    output = result.output
    verified = isinstance(output, dict) and output.get("ok") is True
    return VerificationCheck(
        verified=verified,
        reason=(
            "Local growth memory was read successfully."
            if verified
            else "Local growth memory could not be read."
        ),
        evidence={"tool": call.tool_name},
    )
