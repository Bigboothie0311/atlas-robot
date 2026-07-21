"""Agent tools for the self-showcase pipeline: narrate a tour of Atlas's
own HUD, edit it into a Reel, and (only with explicit confirmation)
publish it to Instagram.

content.record_self_showcase records Atlas's own screen -- the physical
HUD kiosk on the Pi (see hud_capture.py) -- not the Windows PC's screen.
Confirmed live 2026-07-21: recording the PC screen shows the owner's
desktop, not Atlas; the whole point of self-showcase content is Atlas
narrating his own features (weather radar, self-diagnostics, ...), which
only exist on his own HUD. It drives a varied "tour" of real HUD states
between narrated clips -- randomized phrasing and beat selection by
default (see _build_default_tour()) so repeated recordings don't produce
the same video twice, or a fully custom script via 'beats' -- edits each
with content_pipeline.edit_reel, and concatenates them. Nothing it does
is public or destructive, so it stays at permission_level=0.

content.publish_to_instagram is the one tool in this codebase that uses
PermissionLevel.CONFIRMATION_REQUIRED: it's the step the safety model
explicitly requires confirming the exact media and caption for before
anything goes public.
"""

from __future__ import annotations

import random
import time
import wave
from pathlib import Path
from typing import Any

import requests

import content_pipeline
import hud_capture
import instagram_publish
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

# The default tour for "make a promo video of yourself" -- an honest,
# narrated walk through real, currently-working features. Wesley's ask:
# Atlas should show his own screen and actually talk about what he can
# do, not just sit on one static shot -- and not post the exact same
# script and clip every single time either, so this is randomized
# (phrasing, and which extra beats show up) rather than one fixed
# tuple. Weather radar and self-diagnostics always run -- they're the
# only two beats with a real HUD-driving action -- everything else
# varies. All EXTRA_BEATS use action="idle" because system status,
# printer, and gaming-PC panels are already part of the always-visible
# HUD dashboard (see hud/app.js's #printer-panel, .panel-system-status,
# .panel-gaming-pc), not separate overlays that need driving open.
INTRO_LINES: tuple[str, ...] = (
    "Hi, I'm A.T.L.A.S. Let me show you around my own screen.",
    "Hey, it's A.T.L.A.S. -- here's a quick look at what's live on my "
    "display right now.",
    "A.T.L.A.S. here. Let me walk you through what I've got running "
    "today.",
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
    "That's a quick look at what I can do.",
    "That's just a slice of what's running on me right now.",
    "And that's a peek at my own screen -- there's more where that "
    "came from.",
)

EXTRA_BEATS: tuple[dict[str, str], ...] = (
    {
        "narration": (
            "Over here is my system status -- live CPU, memory, and "
            "thermal readings straight off this Pi."
        ),
        "action": "idle",
    },
    {
        "narration": (
            "This panel tracks my printer -- real status from the "
            "device, not a guess."
        ),
        "action": "idle",
    },
    {
        "narration": (
            "And this keeps an eye on the gaming PC on the network -- "
            "health and what's running over there."
        ),
        "action": "idle",
    },
)
MAX_EXTRA_BEATS = 2


def _build_default_tour() -> tuple[dict[str, str], ...]:
    """Builds one varied instance of the default tour: randomized
    intro/outro phrasing, the always-on weather + diagnostics beats
    (with randomized phrasing too), and a random subset of extra
    beats -- so consecutive "record a promo video" calls don't
    produce the same script and clip twice in a row."""
    extra = random.sample(
        EXTRA_BEATS,
        k=random.randint(0, min(MAX_EXTRA_BEATS, len(EXTRA_BEATS))),
    )

    return (
        {"narration": random.choice(INTRO_LINES), "action": "idle"},
        {
            "narration": random.choice(WEATHER_LINES),
            "action": "weather_open",
        },
        {
            "narration": random.choice(DIAGNOSTICS_LINES),
            "action": "diagnostics",
        },
        *extra,
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


def register_content_tools(
    registry: ToolRegistry,
    verifier: ResultVerifier,
    *,
    staging_directory: str | Path,
) -> list[AtlasTool]:
    staging_path = Path(staging_directory)

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

        tour = beats if beats else _build_default_tour()
        staging_path.mkdir(parents=True, exist_ok=True)

        clip_paths: list[str] = []
        narration_lines: list[str] = []

        _set_recording_indicator(True)
        try:
            for index, beat in enumerate(tour):
                narration_text = beat["narration"]
                action = beat.get("action", "idle")

                try:
                    wav_path = content_pipeline.render_narration(
                        narration_text
                    )
                except content_pipeline.ContentPipelineError as error:
                    return {"ok": False, "error": str(error)}

                _apply_hud_action(action)
                time.sleep(HUD_ACTION_SETTLE_SECONDS)

                clip_seconds = (
                    _wav_duration_seconds(wav_path)
                    + RECORDING_BUFFER_SECONDS
                )
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
        finally:
            _set_recording_indicator(False)
            _apply_hud_action("idle")

        out_path = staging_path / f"reel_{int(time.time())}.mp4"

        try:
            content_pipeline.concat_clips(clip_paths, out_path)
        except content_pipeline.ContentPipelineError as error:
            return {"ok": False, "error": str(error)}
        finally:
            for clip_path in clip_paths:
                Path(clip_path).unlink(missing_ok=True)

        return {
            "ok": True,
            "video_path": str(out_path),
            "caption": content_pipeline.build_caption(
                " ".join(narration_lines)
            ),
            "mission": mission,
        }

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
            result = instagram_publish.publish_reel(
                video_path,
                caption,
                dry_run=False,
                mission=mission,
            )
        except instagram_publish.InstagramPublishError as error:
            return {"ok": False, "error": str(error)}

        return {"ok": True, **result}

    tools = [
        AtlasTool(
            name="content.record_self_showcase",
            description=(
                "Records a narrated tour of Atlas's own HUD screen and "
                "edits it into a 9:16 Reel, returning the finished "
                "local video path and a draft caption. Does not "
                "publish anything. With no 'beats', runs a varied "
                "default tour -- weather radar and self-diagnostics "
                "always show up, phrasing and a few extra real "
                "feature beats (system status, printer, gaming PC) "
                "are randomized so repeated calls don't produce the "
                "same script and clip twice in a row. Not a fixed "
                "script either way: pass 'beats' with any narration "
                "lines, in any order, any length, to record a fully "
                "custom video saying and showing whatever is asked "
                "for instead."
            ),
            runs_on="pi",
            handler=record_self_showcase,
            permission_level=0,
            timeout_seconds=300,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mission": {
                            "type": ["string", "null"],
                        },
                        "beats": {
                            "type": ["array", "null"],
                            "description": (
                                "A fully custom script overriding the "
                                "default weather/diagnostics tour -- "
                                "any number of beats, any narration "
                                "text, in any order. Each item: "
                                "{narration: str, action: str}. "
                                "'action' drives a real HUD state for "
                                "that beat's clip: 'weather_open' / "
                                "'weather_close' opens/closes the "
                                "weather radar, 'diagnostics' runs and "
                                "shows self-diagnostics, 'idle' (or "
                                "any other/omitted value) leaves the "
                                "HUD showing whatever it's currently "
                                "on -- unrecognized actions never "
                                "error, they just don't change the "
                                "display for that beat."
                            ),
                        },
                    },
                    "required": ["mission", "beats"],
                    "additionalProperties": False,
                }
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
    ]

    for tool in tools:
        registry.register(tool)

    verifier.register(
        "content.record_self_showcase",
        _verify_record_self_showcase,
    )
    verifier.register(
        "content.publish_to_instagram",
        _verify_publish_to_instagram,
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
