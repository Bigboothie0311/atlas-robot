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

import subprocess
import tempfile
from pathlib import Path

import requests

HUB = "http://127.0.0.1:5051"
REQUEST_TIMEOUT_SECONDS = 45
FFMPEG_TIMEOUT_SECONDS = 300
REEL_WIDTH = 1080
REEL_HEIGHT = 1920
# Instagram's actual caption cap is 2200 characters -- 200 was an
# arbitrary placeholder that truncated real narration lines mid-sentence
# well before the platform's own limit kicked in.
CAPTION_MAX_LENGTH = 2200

# Static and deliberately generic -- no LLM call, no music, no watermark
# asset. Nothing fancier was asked for and it keeps this deterministic.
HASHTAGS = (
    "#atlas", "#ai", "#robotics", "#homelab", "#desksetup", "#buildinpublic",
)


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
    """Concatenates already-edited, uniformly-encoded clips (same codec/
    resolution -- each produced by edit_reel) into one final video via
    ffmpeg's concat demuxer with a stream copy, no re-encode needed."""
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
        "-c", "copy", str(out_path),
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

    hashtags = " ".join(HASHTAGS)

    return f"{line}\n\n{hashtags}" if line else hashtags
