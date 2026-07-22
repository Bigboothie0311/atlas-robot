"""Pi-side self-showcase edit pipeline: narrate, mux, concat, caption.

Turns silent HUD clips (captured on the Pi itself by hud_capture.py --
this is genuinely Atlas's own screen, not the Windows PC's) into a
narrated 9:16 Reel. Narration is rendered locally through robot_hub's
Piper voice with play=False (see robot_hub._speak_text) so it never plays
out loud, then muxed onto each clip here. See atlas_agent/content_tools.py
for the "tour" orchestration that drives the HUD through its features
(weather radar, diagnostics, ...) between clips, and instagram_publish.py
for the publish half of the pipeline.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import requests

HUB = "http://127.0.0.1:5051"
REQUEST_TIMEOUT_SECONDS = 45
FFMPEG_TIMEOUT_SECONDS = 300
REEL_WIDTH = 1080
REEL_HEIGHT = 1920
# Pinned explicitly in edit_reel()'s output -- confirmed live: mixing a
# 24fps HUD clip (hud_capture.CAPTURE_FPS) with a 30fps PC screen-
# recording clip (windows-companion/atlas_companion.py's gdigrab capture)
# in the same Reel produced non-monotonic DTS on decode after
# concat_clips()'s stream-copy concat, since that assumes every input
# segment already shares the same frame rate/timebase. Every beat's clip
# gets re-encoded here regardless of source, so forcing one consistent
# output rate here is what actually makes that assumption true.
REEL_FRAME_RATE = 24
# Instagram's actual caption cap is 2200 characters -- 200 was an
# arbitrary placeholder that truncated real narration lines mid-sentence
# well before the platform's own limit kicked in.
CAPTION_MAX_LENGTH = 2200

# Every published Reel gets this exact bounded project-focused block after
# model caption generation. Thirty is both inside the owner's requested
# 30-40 range and keeps the caption comfortably below Instagram's character
# limit. Existing model-written hashtags are removed before this is appended.
RASPBERRY_PI_HASHTAGS = (
    "#raspberrypi",
    "#raspberrypiprojects",
    "#raspberrypibuild",
    "#raspberrypimaker",
    "#raspberrypicommunity",
    "#raspberrypi4",
    "#raspberrypi5",
    "#raspberrypizero",
    "#piprojects",
    "#pibuild",
    "#sbcprojects",
    "#singleboardcomputer",
    "#linuxprojects",
    "#embeddedlinux",
    "#pythonprojects",
    "#pythonelectronics",
    "#electronicsprojects",
    "#diyelectronics",
    "#diytech",
    "#makerprojects",
    "#makercommunity",
    "#roboticsprojects",
    "#robotbuild",
    "#homelabprojects",
    "#homeautomation",
    "#iotprojects",
    "#embeddedsystems",
    "#opensourcehardware",
    "#techprojects",
    "#buildinpublic",
)
HASHTAG_PATTERN = re.compile(r"(?<!\w)#[A-Za-z0-9_]+")
BRAND_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


class ContentPipelineError(RuntimeError):
    pass


def render_narration(text: str) -> str:
    """Synthesizes text via the hub's Piper voice without playing it out
    loud (play=False), returning the local WAV path. The caller owns
    deleting the file once it's been muxed in."""
    try:
        response = requests.post(
            f"{HUB}/speak",
            json={"text": text, "play": False},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        raise ContentPipelineError(
            f"narration request failed: {error}"
        ) from error

    if not payload.get("ok") or not payload.get("wav_path"):
        raise ContentPipelineError(
            payload.get("error")
            or "narration synthesis returned no wav_path"
        )

    return payload["wav_path"]


def edit_reel(video_path, narration_wav_path, out_path) -> str:
    """One deterministic ffmpeg pass: reframe the recording to 1080x1920,
    normalize narration loudness, mux it in as the audio track, and trim
    to the shorter of the two (-shortest) so a long silent recording
    doesn't outlast a short narration line or vice versa."""
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video_path),
        "-i", str(narration_wav_path),
        "-filter_complex",
        (
            f"[0:v]scale={REEL_WIDTH}:{REEL_HEIGHT}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={REEL_WIDTH}:{REEL_HEIGHT}:(ow-iw)/2:(oh-ih)/2[v];"
            "[1:a]loudnorm[a]"
        ),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        # -r alone (just relabeling the muxer's declared rate) still let
        # one non-monotonic-DTS warning through on decode when mixing a
        # 24fps HUD clip with a 30fps PC-recording clip -- confirmed
        # live. -fps_mode cfr forces ffmpeg to actually duplicate/drop
        # frames to real, evenly-spaced constant-frame-rate timestamps
        # rather than just relabeling them; combined with -r, that
        # cleared the warning entirely on the same mixed-source clip.
        "-fps_mode", "cfr", "-r", str(REEL_FRAME_RATE),
        # loudnorm's internal true-peak limiting resamples for its own
        # analysis and doesn't reliably preserve the input rate on
        # output -- confirmed live: without pinning -ar, this produced
        # a 96kHz track that Instagram's Reels processing silently
        # rejected (bare container ERROR, no other diagnostic). 48kHz
        # is the standard video-delivery rate every platform expects.
        "-c:a", "aac", "-ar", "48000",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]

    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as error:
        raise ContentPipelineError(
            f"ffmpeg edit failed: {error.stderr.strip()}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ContentPipelineError(
            f"ffmpeg edit timed out after {FFMPEG_TIMEOUT_SECONDS}s"
        ) from error

    result_path = Path(out_path)
    if not result_path.is_file() or result_path.stat().st_size == 0:
        raise ContentPipelineError("edited reel is missing or empty")

    return str(result_path)


def concat_clips(clip_paths, out_path) -> str:
    """Concatenates already-edited clips (each produced by edit_reel)
    into one final video via ffmpeg's concat demuxer, re-encoding rather
    than stream-copying. Stream-copy (-c copy) was the original
    approach and is fine when every clip shares one source's exact
    timebase, but confirmed live: mixing clips from different real
    sources (a 24fps HUD recording and a 30fps PC screen recording) fed
    to -c copy produced non-monotonic DTS on decode -- pinning fps in
    edit_reel() alone didn't fully clear it, only actually re-encoding
    the joined stream here did. This Pi has CPU headroom to spare for
    it (confirmed live: well under half its 4 cores' capacity even
    mid-recording -- see hud_capture.py)."""
    if not clip_paths:
        raise ContentPipelineError("no clips to concatenate")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as list_file:
        for clip_path in clip_paths:
            list_file.write(f"file '{Path(clip_path).resolve()}'\n")
        list_path = list_file.name

    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", list_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-fps_mode", "cfr", "-r", str(REEL_FRAME_RATE),
        "-c:a", "aac", "-ar", "48000",
        str(out_path),
    ]

    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as error:
        raise ContentPipelineError(
            f"ffmpeg concat failed: {error.stderr.strip()}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ContentPipelineError(
            f"ffmpeg concat timed out after {FFMPEG_TIMEOUT_SECONDS}s"
        ) from error
    finally:
        Path(list_path).unlink(missing_ok=True)

    result_path = Path(out_path)
    if not result_path.is_file() or result_path.stat().st_size == 0:
        raise ContentPipelineError("concatenated reel is missing or empty")

    return str(result_path)


def build_caption(narration_text: str) -> str:
    """Templated caption: the narration line (truncated if needed) plus a
    fixed hashtag set. Deliberately simple -- no LLM call for v1."""
    line = narration_text.strip()

    if len(line) > CAPTION_MAX_LENGTH:
        line = line[: CAPTION_MAX_LENGTH - 3].rstrip() + "..."

    return ensure_raspberry_pi_hashtags(line)


def ensure_raspberry_pi_hashtags(caption: str) -> str:
    """Replace arbitrary tags with exactly 30 Raspberry Pi project tags."""
    prose = HASHTAG_PATTERN.sub("", str(caption or ""))
    prose = re.sub(r"[ \t]+\n", "\n", prose)
    prose = re.sub(r"[ \t]{2,}", " ", prose)
    prose = re.sub(r"\n{3,}", "\n\n", prose).strip()
    hashtag_block = " ".join(RASPBERRY_PI_HASHTAGS)
    maximum_prose = CAPTION_MAX_LENGTH - len(hashtag_block) - 2

    if len(prose) > maximum_prose:
        prose = prose[: maximum_prose - 3].rstrip() + "..."

    return f"{prose}\n\n{hashtag_block}" if prose else hashtag_block


def probe_video_duration(video_path: str | Path) -> float:
    """Return a media duration without importing a heavyweight video SDK."""
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(video_path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(completed.stdout)
        duration = float(payload["format"]["duration"])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ContentPipelineError(f"could not read video duration: {error}") from error
    if duration <= 0:
        raise ContentPipelineError("video duration was not positive")
    return duration


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, int(round(float(seconds) * 100)))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{fraction:02d}"


def _ass_text(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", r"\e")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", r"\N")
    )


def build_brand_ass(
    *,
    duration_seconds: float,
    cues: list[dict[str, Any]],
    title: str,
    series: str,
) -> str:
    """Create burned-in subtitles, chapters, identity, and a progress meter."""
    duration = max(0.1, float(duration_seconds))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Subtitle,DejaVu Sans,56,&H00FFFFFF,&H000000FF,&H0010151A,"
        "&H90000000,-1,0,0,0,100,100,0,0,1,5,1,2,70,70,185,1",
        "Style: Chapter,DejaVu Sans,34,&H0019D3FF,&H000000FF,&H0010151A,"
        "&H70000000,-1,0,0,0,100,100,0,0,1,3,0,7,46,46,76,1",
        "Style: Brand,DejaVu Sans,27,&H00FFFFFF,&H000000FF,&H0010151A,"
        "&H50000000,-1,0,0,0,100,100,2,0,1,2,0,9,36,42,45,1",
        "Style: Title,DejaVu Sans,66,&H00FFFFFF,&H000000FF,&H0010151A,"
        "&HB0000000,-1,0,0,0,100,100,0,0,1,6,2,8,90,90,210,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text",
        (
            "Dialogue: 0,0:00:00.00,"
            f"{_ass_time(duration)},Brand,,0,0,0,,A.T.L.A.S. // RASPBERRY PI"
        ),
        (
            "Dialogue: 2,0:00:00.00,"
            f"{_ass_time(min(duration, 2.2))},Title,,0,0,0,,"
            r"{\fad(120,260)}"
            f"{_ass_text(series.upper())}\\N{_ass_text(title)}"
        ),
    ]
    cue_count = max(1, len(cues))
    for index, cue in enumerate(cues):
        start = max(0.0, float(cue.get("start") or 0.0))
        end = min(duration, max(start + 0.1, float(cue.get("end") or duration)))
        filled = max(1, round(12 * (index + 1) / cue_count))
        meter = "■" * filled + "□" * (12 - filled)
        chapter = f"{meter}  {index + 1:02d}/{cue_count:02d}"
        lines.append(
            f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},Chapter,,0,0,0,,"
            f"{chapter}"
        )
        lines.append(
            f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},Subtitle,,0,0,0,,"
            f"{_ass_text(str(cue.get('text') or ''))}"
        )
    return "\n".join(lines) + "\n"


def _ass_filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def brand_reel(
    video_path: str | Path,
    out_path: str | Path,
    *,
    cues: list[dict[str, Any]],
    title: str,
    series: str,
    add_signature_sound: bool = True,
) -> str:
    """Burn Atlas's identity, readable subtitles, chapters, and subtle chime."""
    source = Path(video_path)
    destination = Path(out_path)
    duration = probe_video_duration(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ass", delete=False, encoding="utf-8"
    ) as subtitle_file:
        subtitle_file.write(
            build_brand_ass(
                duration_seconds=duration,
                cues=cues,
                title=title,
                series=series,
            )
        )
        subtitle_path = Path(subtitle_file.name)

    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
    ]
    if add_signature_sound:
        command.extend(
            ["-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=0.16"]
        )
        filter_complex = (
            f"[0:v]ass='{_ass_filter_path(subtitle_path)}'[v];"
            "[1:a]volume=0.045[chime];"
            "[0:a][chime]amix=inputs=2:duration=first:normalize=0[a]"
        )
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        filter_complex = f"[0:v]ass='{_ass_filter_path(subtitle_path)}'[v]"
        maps = ["-map", "[v]", "-map", "0:a?"]
    command.extend(
        [
            "-filter_complex", filter_complex,
            *maps,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(REEL_FRAME_RATE),
            "-c:a", "aac", "-ar", "48000", "-b:a", "160k",
            "-movflags", "+faststart", str(destination),
        ]
    )
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as error:
        raise ContentPipelineError(
            f"ffmpeg branding failed: {error.stderr.strip()}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ContentPipelineError("ffmpeg branding timed out") from error
    finally:
        subtitle_path.unlink(missing_ok=True)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise ContentPipelineError("branded reel is missing or empty")
    return str(destination)


def _single_overlay_ass(text: str, end_seconds: float, *, cover: bool = False) -> str:
    font_size = 78 if cover else 68
    alignment = 5 if cover else 8
    margin = 180 if cover else 220
    return "\n".join(
        [
            "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080",
            "PlayResY: 1920", "WrapStyle: 0", "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Hook,DejaVu Sans,{font_size},&H00FFFFFF,&H000000FF,"
            "&H0010151A,&HC0000000,-1,0,0,0,100,100,0,0,1,7,2,"
            f"{alignment},90,90,{margin},1",
            "", "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text",
            f"Dialogue: 3,0:00:00.00,{_ass_time(end_seconds)},Hook,,0,0,0,,"
            r"{\fad(100,250)}" + _ass_text(text),
            "",
        ]
    )


def create_hook_variant(
    video_path: str | Path,
    out_path: str | Path,
    hook: str,
    *,
    overlay_seconds: float = 2.8,
) -> str:
    """Create a Trial Reel candidate with a different opening promise."""
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ass", delete=False, encoding="utf-8"
    ) as overlay_file:
        overlay_file.write(_single_overlay_ass(hook, overlay_seconds))
        overlay_path = Path(overlay_file.name)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video_path), "-vf", f"ass='{_ass_filter_path(overlay_path)}'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
        str(destination),
    ]
    try:
        subprocess.run(
            command, check=True, capture_output=True, text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as error:
        raise ContentPipelineError(
            f"trial variant rendering failed: {error.stderr.strip()}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ContentPipelineError("trial variant rendering timed out") from error
    finally:
        overlay_path.unlink(missing_ok=True)
    return str(destination)


def create_reel_cover(
    video_path: str | Path,
    out_path: str | Path,
    title: str,
) -> str:
    """Extract a consistent 9:16 grid cover with a large readable title."""
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ass", delete=False, encoding="utf-8"
    ) as overlay_file:
        overlay_file.write(_single_overlay_ass(title, 10.0, cover=True))
        overlay_path = Path(overlay_file.name)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", "0.6", "-i", str(video_path),
        "-vf", f"ass='{_ass_filter_path(overlay_path)}'", "-frames:v", "1",
        str(destination),
    ]
    try:
        subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=60,
        )
    except subprocess.CalledProcessError as error:
        raise ContentPipelineError(
            f"cover rendering failed: {error.stderr.strip()}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ContentPipelineError("cover rendering timed out") from error
    finally:
        overlay_path.unlink(missing_ok=True)
    return str(destination)
