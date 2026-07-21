from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Any

import requests

from atlas_agent.sftp_client import SFTPClient
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool
from atlas_agent.verifier import (
    ResultVerifier,
    VerificationCheck,
)

HUD_DISPLAY = ":0"
HUB = "http://127.0.0.1:5051"

CameraCaptureHandler = Callable[
    ...,
    dict[str, Any] | None,
]
HudFrameHandler = Callable[[Path], bool]
HudRecordingNotifier = Callable[[bool], None]


def _notify_hud_recording(active: bool) -> None:
    """Best-effort HUD recording indicator toggle — never raises, since a
    HUD/network hiccup must not block or fail the actual capture."""
    try:
        requests.post(f"{HUB}/hud/recording", json={"active": active}, timeout=3)
    except requests.RequestException:
        pass


def _capture_hud_frame_file(out_path: Path) -> bool:
    """Grabs a still of the kiosk's own X display via scrot. Degrades to
    a plain False (never raises) so a missing scrot install or headless
    kiosk just fails this one capture rather than crashing the agent."""
    env = {**os.environ, "DISPLAY": HUD_DISPLAY}

    try:
        subprocess.run(
            ["scrot", "--overwrite", str(out_path)],
            env=env, check=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as error:
        print("HUD frame capture failed:", type(error).__name__, error, flush=True)
        return False

    return out_path.is_file() and out_path.stat().st_size > 0


def register_pi_capture_tools(
    registry: ToolRegistry,
    verifier: ResultVerifier,
    *,
    sftp_client: SFTPClient,
    recordings_remote_root: str,
    staging_directory: str | Path,
    camera_capture_handler: CameraCaptureHandler | None = None,
    hud_frame_handler: HudFrameHandler | None = None,
    hud_recording_notifier: HudRecordingNotifier | None = None,
) -> list[AtlasTool]:
    """Registers A.T.L.A.S.'s self-showcase capture tools: recording
    himself via the USB camera, and grabbing his own HUD kiosk screen.
    Both stage briefly on the Pi, upload to the PC's recordings folder
    over the existing SFTP link, and delete the local copy only once
    the upload is verified — footage never lingers on the Pi."""
    if camera_capture_handler is None:
        import camera_gate

        camera_capture_handler = camera_gate.capture_clip

    if hud_frame_handler is None:
        hud_frame_handler = _capture_hud_frame_file

    if hud_recording_notifier is None:
        hud_recording_notifier = _notify_hud_recording

    staging_path = Path(staging_directory)

    def _upload_and_finalize(
        local_path: Path,
        remote_name: str,
    ) -> dict[str, Any]:
        remote_path = str(
            PureWindowsPath(recordings_remote_root) / remote_name
        )
        result = sftp_client.upload(local_path, remote_path)
        verified = result.ok and result.verified

        if verified:
            local_path.unlink(missing_ok=True)

        return {
            "ok": verified,
            "remote_path": result.remote_path,
            "local_path": None if verified else result.local_path,
            "bytes_transferred": result.bytes_transferred,
            "error": result.error,
        }

    def capture_hud_frame(
        mission: str | None = None,
    ) -> dict[str, Any]:
        if mission is not None and not isinstance(
            mission, str
        ):
            raise ValueError(
                "mission must be a string or null"
            )

        staging_path.mkdir(parents=True, exist_ok=True)
        name = f"hud_frame_{int(time.time())}.png"
        local_path = staging_path / name

        if not hud_frame_handler(local_path):
            return {
                "ok": False,
                "mission": mission,
                "error": "HUD frame capture failed",
            }

        return {
            "mission": mission,
            **_upload_and_finalize(local_path, name),
        }

    def capture_self_clip(
        duration_seconds: int,
        mission: str | None = None,
        mute_audio: bool = False,
    ) -> dict[str, Any]:
        if (
            not isinstance(duration_seconds, int)
            or duration_seconds <= 0
        ):
            raise ValueError(
                "duration_seconds must be a positive integer"
            )

        if mission is not None and not isinstance(
            mission, str
        ):
            raise ValueError(
                "mission must be a string or null"
            )

        if not isinstance(mute_audio, bool):
            raise ValueError(
                "mute_audio must be a boolean"
            )

        hud_recording_notifier(True)
        try:
            clip = camera_capture_handler(
                duration_seconds,
                mission=mission,
                mute_audio=mute_audio,
            )
        finally:
            hud_recording_notifier(False)

        if clip is None:
            return {
                "ok": False,
                "mission": mission,
                "error": "camera clip capture failed",
            }

        local_path = Path(clip["path"])

        return {
            "mission": mission,
            "duration_seconds": clip.get("duration_seconds"),
            "has_audio": clip.get("has_audio"),
            **_upload_and_finalize(
                local_path, local_path.name
            ),
        }

    tools = [
        AtlasTool(
            name="pi.capture_hud_frame",
            description=(
                "Capture a still of A.T.L.A.S.'s own HUD "
                "kiosk display and upload it to the PC's "
                "recordings folder for the self-showcase "
                "media pipeline."
            ),
            runs_on="pi",
            handler=capture_hud_frame,
            permission_level=0,
            timeout_seconds=45,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mission": {
                            "type": ["string", "null"],
                        }
                    },
                    "required": ["mission"],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="camera.capture_clip",
            description=(
                "Record A.T.L.A.S. himself via the USB "
                "camera (video) and Pi microphone (audio, "
                "unless mute_audio) for a bounded duration, "
                "then upload the clip to the PC's "
                "recordings folder."
            ),
            runs_on="pi",
            handler=capture_self_clip,
            permission_level=0,
            timeout_seconds=180,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "duration_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 120,
                        },
                        "mission": {
                            "type": ["string", "null"],
                        },
                        "mute_audio": {
                            "type": "boolean",
                        },
                    },
                    "required": [
                        "duration_seconds",
                        "mission",
                        "mute_audio",
                    ],
                    "additionalProperties": False,
                }
            },
        ),
    ]

    for tool in tools:
        registry.register(tool)

    verifier.register(
        "pi.capture_hud_frame", _verify_capture_upload
    )
    verifier.register(
        "camera.capture_clip", _verify_capture_upload
    )

    return tools


def _verify_capture_upload(call, result) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason="Capture output was not an object.",
        )

    verified = (
        output.get("ok") is True
        and bool(output.get("remote_path"))
    )

    return VerificationCheck(
        verified=verified,
        reason=(
            "The capture was verified uploaded to the PC."
            if verified
            else "The capture failed or was not verified on the PC."
        ),
        evidence={
            "error": output.get("error"),
            "bytes_transferred": output.get(
                "bytes_transferred"
            ),
        },
    )
