from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Any

from atlas_agent.sftp_client import SFTPClient
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool
from atlas_agent.verifier import (
    ResultVerifier,
    VerificationCheck,
)

HUD_DISPLAY = ":0"

HudFrameHandler = Callable[[Path], bool]


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
    hud_frame_handler: HudFrameHandler | None = None,
) -> list[AtlasTool]:
    """Registers A.T.L.A.S.'s self-showcase capture tools: currently just
    grabbing his own HUD kiosk screen. Stages briefly on the Pi, uploads
    to the PC's recordings folder over the existing SFTP link, and
    deletes the local copy only once the upload is verified — footage
    never lingers on the Pi.

    camera.capture_clip (recording via the physical USB camera) is
    deliberately NOT registered here. Confirmed live 2026-07-21: that
    camera faces the room, not Atlas, so any tool that can reach it will
    eventually get called for "record a clip of yourself" regardless of
    what capabilities.py's system-prompt text says -- removing it from
    capabilities.REGISTRY alone was not enough, since run_atlas_agent's
    own planner can still pick a registered-but-undocumented tool.
    camera_gate.capture_clip() itself, and its mic_arbiter coordination,
    are untouched and correct -- they're just not wired into the agent's
    tool roster until there's a camera actually pointed at Atlas, or a
    real "his own" video source (e.g. a Pi HUD screen recording)."""
    if hud_frame_handler is None:
        hud_frame_handler = _capture_hud_frame_file

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
    ]

    for tool in tools:
        registry.register(tool)

    verifier.register(
        "pi.capture_hud_frame", _verify_capture_upload
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
