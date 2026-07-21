"""Captures A.T.L.A.S.'s own physical HUD kiosk display for the
self-showcase pipeline -- this is genuinely his own screen (the `cage`/
Chromium kiosk on the Pi's attached display), not the Windows PC's
screen.

The kiosk runs on Wayland via `cage` (confirmed live in
`atlas-hud.service`), not X11. `atlas_agent/pi_tools.py`'s existing
`capture_hud_frame` used `scrot` against `DISPLAY=:0`, which is an X11
tool -- confirmed live it was silently capturing a solid-black frame
every time (an unrelated stale X11 socket exists, but nothing draws to
it). `grim` is the Wayland-native equivalent and captures the kiosk's
real content correctly.

There's no installed video-capture tool for this wlroots kiosk (no
wf-recorder), so video here is frame-stitched: periodic `grim` stills at
a low frame rate, muxed into an mp4 via ffmpeg's image2 sequence input.
That's a reasonable substitute for HUD content, which is mostly static
panels changing state rather than fast motion.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

WAYLAND_DISPLAY = "wayland-0"
XDG_RUNTIME_DIR = "/run/user/1000"
CAPTURE_FPS = 2
FRAME_TIMEOUT_SECONDS = 5
FFMPEG_TIMEOUT_SECONDS = 120


class HudCaptureError(RuntimeError):
    pass


def _wayland_env() -> dict:
    return {
        **os.environ,
        "WAYLAND_DISPLAY": WAYLAND_DISPLAY,
        "XDG_RUNTIME_DIR": XDG_RUNTIME_DIR,
    }


def capture_frame(out_path) -> bool:
    """Grabs one still of the kiosk's real Wayland display. Returns
    False (never raises) on a single missed frame -- a sequence capture
    shouldn't abort a whole recording over one bad frame."""
    try:
        subprocess.run(
            ["grim", str(out_path)],
            env=_wayland_env(),
            check=True,
            capture_output=True,
            timeout=FRAME_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError):
        return False

    path = Path(out_path)
    return path.is_file() and path.stat().st_size > 0


def record_hud_clip(
    duration_seconds: float, out_path, *, fps: float = CAPTURE_FPS
) -> str:
    """Records `duration_seconds` of the HUD's own display: captures
    `grim` frames at `fps`, then stitches them into an mp4."""
    frame_interval = 1.0 / fps
    frame_count = max(1, round(duration_seconds * fps))

    with tempfile.TemporaryDirectory(prefix="atlas_hud_frames_") as frame_dir:
        saved_index = 0

        for _attempt in range(frame_count):
            frame_path = Path(frame_dir) / f"frame_{saved_index:04d}.png"
            if capture_frame(frame_path):
                saved_index += 1
            time.sleep(frame_interval)

        if saved_index == 0:
            raise HudCaptureError("no HUD frames were captured")

        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", str(fps),
            "-i", str(Path(frame_dir) / "frame_%04d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
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
            raise HudCaptureError(
                f"ffmpeg frame-stitch failed: {error.stderr.strip()}"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise HudCaptureError(
                f"ffmpeg frame-stitch timed out after "
                f"{FFMPEG_TIMEOUT_SECONDS}s"
            ) from error

    result_path = Path(out_path)
    if not result_path.is_file() or result_path.stat().st_size == 0:
        raise HudCaptureError("HUD clip is missing or empty")

    return str(result_path)
