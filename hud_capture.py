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
real content correctly; still used here for single-still `capture_frame`.

Video (`record_hud_clip`) previously frame-stitched periodic `grim`
stills at 2fps because no video-capture tool was installed for this
wlroots kiosk -- confirmed live 2026-07-21 that this was genuinely
choppy on a real Reel. `wf-recorder` (installed via apt, works against
any wlroots compositor including `cage`) now does real continuous
capture instead -- confirmed live: 24fps, 116 real frames over a 4.8s
clip, vs. the old approach's ~10 stitched stills for the same span."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

WAYLAND_DISPLAY = "wayland-0"
XDG_RUNTIME_DIR = "/run/user/1000"
CAPTURE_FPS = 24
FRAME_TIMEOUT_SECONDS = 5
RECORDER_EXIT_TIMEOUT_SECONDS = 15


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
    """Records `duration_seconds` of the HUD's own display via
    `wf-recorder` -- real continuous capture at `fps`, not periodic
    stills. `wf-recorder` finalizes its output on SIGINT (its documented
    stop signal, same as Ctrl+C), so it's started, left running for the
    clip's duration, then signalled and given a chance to exit cleanly
    before the file is checked."""
    command = [
        "wf-recorder", "-y",
        "-r", str(fps),
        "-f", str(out_path),
    ]

    try:
        process = subprocess.Popen(
            command,
            env=_wayland_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise HudCaptureError(
            f"wf-recorder failed to start: {error}"
        ) from error

    time.sleep(duration_seconds)

    process.send_signal(signal.SIGINT)
    try:
        _, stderr = process.communicate(
            timeout=RECORDER_EXIT_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise HudCaptureError(
            "wf-recorder did not exit after SIGINT"
        )

    result_path = Path(out_path)
    if not result_path.is_file() or result_path.stat().st_size == 0:
        raise HudCaptureError(
            "HUD clip is missing or empty "
            f"(wf-recorder: {stderr.decode(errors='replace').strip()})"
        )

    return str(result_path)
