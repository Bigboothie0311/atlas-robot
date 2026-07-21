from __future__ import annotations

from dataclasses import asdict
from typing import Any

from atlas_agent.pc_client import PCClient
from atlas_agent.sftp_client import SFTPClient
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool
from atlas_agent.verifier import (
    ResultVerifier,
    VerificationCheck,
)
from atlas_agent.windows_file_search import (
    WindowsFileSearch,
)


def register_pc_tools(
    registry: ToolRegistry,
    verifier: ResultVerifier,
    *,
    pc_client: PCClient,
    file_search: WindowsFileSearch,
    sftp_client: SFTPClient,
) -> list[AtlasTool]:
    def ensure_online(
        wake_if_needed: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(wake_if_needed, bool):
            raise ValueError(
                "wake_if_needed must be a boolean"
            )

        return asdict(
            pc_client.ensure_online(
                wake_if_needed=wake_if_needed,
                timeout_seconds=90,
                poll_interval_seconds=5,
            )
        )

    def search_files(
        query: str,
        extensions: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str):
            raise ValueError("query must be a string")

        if (
            extensions is not None
            and not isinstance(extensions, list)
        ):
            raise ValueError(
                "extensions must be a list or null"
            )

        if not isinstance(limit, int):
            raise ValueError("limit must be an integer")

        return [
            asdict(match)
            for match in file_search.search(
                query,
                extensions=extensions,
                limit=limit,
            )
        ]

    def download_file(
        remote_path: str,
        local_name: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(remote_path, str):
            raise ValueError(
                "remote_path must be a string"
            )

        if (
            local_name is not None
            and not isinstance(local_name, str)
        ):
            raise ValueError(
                "local_name must be a string or null"
            )

        return asdict(
            sftp_client.download(
                remote_path,
                local_name=local_name,
            )
        )

    def active_apps() -> dict[str, Any]:
        return asdict(
            pc_client.execute("active_apps")
        )

    def open_app(app: str) -> dict[str, Any]:
        if not isinstance(app, str) or not app.strip():
            raise ValueError(
                "app must be a non-empty string"
            )

        return asdict(
            pc_client.execute(
                "open_app",
                {"app": app.strip()},
            )
        )

    def focus_or_open_app(app: str) -> dict[str, Any]:
        if not isinstance(app, str) or not app.strip():
            raise ValueError(
                "app must be a non-empty string"
            )

        return asdict(
            pc_client.execute(
                "focus_or_open_app",
                {"app": app.strip()},
            )
        )

    def active_window() -> dict[str, Any]:
        return asdict(
            pc_client.execute("active_window")
        )

    def capture_screenshot(
        mission: str | None = None,
    ) -> dict[str, Any]:
        if mission is not None and not isinstance(
            mission, str
        ):
            raise ValueError(
                "mission must be a string or null"
            )

        return asdict(
            pc_client.execute(
                "capture_screenshot",
                {"mission": mission},
            )
        )

    def capture_window(
        window_title: str,
        mission: str | None = None,
    ) -> dict[str, Any]:
        if (
            not isinstance(window_title, str)
            or not window_title.strip()
        ):
            raise ValueError(
                "window_title must be a non-empty string"
            )

        if mission is not None and not isinstance(
            mission, str
        ):
            raise ValueError(
                "mission must be a string or null"
            )

        return asdict(
            pc_client.execute(
                "capture_window",
                {
                    "window_title": window_title.strip(),
                    "mission": mission,
                },
            )
        )

    def start_screen_recording(
        target: str = "full",
        window_title: str | None = None,
        mission: str | None = None,
        privacy: bool = False,
        max_seconds: int | None = None,
    ) -> dict[str, Any]:
        if target not in ("full", "window"):
            raise ValueError(
                "target must be 'full' or 'window'"
            )

        if target == "window" and (
            not isinstance(window_title, str)
            or not window_title.strip()
        ):
            raise ValueError(
                "window_title is required when target "
                "is 'window'"
            )

        if mission is not None and not isinstance(
            mission, str
        ):
            raise ValueError(
                "mission must be a string or null"
            )

        if not isinstance(privacy, bool):
            raise ValueError("privacy must be a boolean")

        if max_seconds is not None and not isinstance(
            max_seconds, int
        ):
            raise ValueError(
                "max_seconds must be an integer or null"
            )

        return asdict(
            pc_client.execute(
                "start_recording",
                {
                    "target": target,
                    "window_title": window_title,
                    "mission": mission,
                    "privacy": privacy,
                    "max_seconds": max_seconds,
                },
            )
        )

    def stop_screen_recording() -> dict[str, Any]:
        return asdict(
            pc_client.execute("stop_recording")
        )

    def list_recordings() -> dict[str, Any]:
        return asdict(
            pc_client.execute("list_recordings")
        )

    tools = [
        AtlasTool(
            name="pc.ensure_online",
            description=(
                "Check whether the Windows companion is online "
                "and wake the PC if needed, then verify it came up."
            ),
            runs_on="pi",
            handler=ensure_online,
            permission_level=0,
            timeout_seconds=100,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "wake_if_needed": {
                            "type": "boolean",
                            "description": (
                                "Whether to send Wake-on-LAN "
                                "when the PC is offline."
                            ),
                        }
                    },
                    "required": ["wake_if_needed"],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pc.search_files",
            description=(
                "Search approved Windows folders for files, "
                "returning newest matches first."
            ),
            runs_on="pc",
            handler=search_files,
            permission_level=0,
            timeout_seconds=130,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Literal text to find in filenames."
                            ),
                            "maxLength": 200,
                        },
                        "extensions": {
                            "type": ["array", "null"],
                            "items": {
                                "type": "string",
                            },
                            "description": (
                                "Optional file extensions such as "
                                "f3d, stl, py, or mp4."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                        },
                    },
                    "required": [
                        "query",
                        "extensions",
                        "limit",
                    ],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pc.download_file",
            description=(
                "Download an exact approved Windows file to "
                "A.T.L.A.S. staging and verify size and SHA-256."
            ),
            runs_on="pi",
            handler=download_file,
            permission_level=0,
            timeout_seconds=300,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "remote_path": {
                            "type": "string",
                            "description": (
                                "Exact absolute Windows path returned "
                                "by pc.search_files."
                            ),
                        },
                        "local_name": {
                            "type": ["string", "null"],
                            "description": (
                                "Optional destination filename."
                            ),
                        },
                    },
                    "required": [
                        "remote_path",
                        "local_name",
                    ],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pc.active_apps",
            description=(
                "List the titles of open Windows applications."
            ),
            runs_on="pc",
            handler=active_apps,
            permission_level=0,
            timeout_seconds=30,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pc.open_app",
            description=(
                "Open an application from the Windows "
                "companion's approved app list. Always "
                "opens a new instance — prefer "
                "pc.focus_or_open_app for the named "
                "profile apps (spotify, claude, codex, "
                "terminal, fusion, browser) so an "
                "already-open window is focused instead "
                "of duplicated."
            ),
            runs_on="pc",
            handler=open_app,
            permission_level=0,
            timeout_seconds=30,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "app": {
                            "type": "string",
                        }
                    },
                    "required": ["app"],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pc.focus_or_open_app",
            description=(
                "Focus an approved app's window if it is "
                "already open on the Windows PC, or open "
                "it if not — never opens a duplicate "
                "instance. Approved app keys: spotify, "
                "claude, codex, terminal, fusion, browser."
            ),
            runs_on="pc",
            handler=focus_or_open_app,
            permission_level=0,
            timeout_seconds=30,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "app": {
                            "type": "string",
                            "enum": [
                                "spotify",
                                "claude",
                                "codex",
                                "terminal",
                                "fusion",
                                "browser",
                            ],
                        }
                    },
                    "required": ["app"],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pc.active_window",
            description=(
                "Report the title of the currently "
                "focused window on the Windows PC."
            ),
            runs_on="pc",
            handler=active_window,
            permission_level=0,
            timeout_seconds=20,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pc.capture_screenshot",
            description=(
                "Capture the PC's full screen to the "
                "recordings folder with mission/window "
                "metadata. Refuses if a privacy-blocked "
                "window (password manager, email, "
                "banking) is focused."
            ),
            runs_on="pc",
            handler=capture_screenshot,
            permission_level=0,
            timeout_seconds=30,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mission": {
                            "type": ["string", "null"],
                            "description": (
                                "Optional mission/feature "
                                "name this capture belongs "
                                "to."
                            ),
                        }
                    },
                    "required": ["mission"],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pc.capture_window",
            description=(
                "Capture ONE named window on the PC by "
                "title substring, not the whole screen. "
                "Refuses privacy-blocked or unmatched "
                "titles."
            ),
            runs_on="pc",
            handler=capture_window,
            permission_level=0,
            timeout_seconds=30,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "window_title": {
                            "type": "string",
                            "maxLength": 200,
                        },
                        "mission": {
                            "type": ["string", "null"],
                        },
                    },
                    "required": [
                        "window_title",
                        "mission",
                    ],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pc.start_screen_recording",
            description=(
                "Start recording the PC's full desktop or "
                "one named window to the recordings "
                "folder. Duration is bounded by "
                "max_seconds (capped by the companion's "
                "configured ceiling); refuses a second "
                "concurrent recording and any "
                "privacy-blocked target."
            ),
            runs_on="pc",
            handler=start_screen_recording,
            permission_level=0,
            timeout_seconds=30,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "enum": ["full", "window"],
                        },
                        "window_title": {
                            "type": ["string", "null"],
                        },
                        "mission": {
                            "type": ["string", "null"],
                        },
                        "privacy": {
                            "type": "boolean",
                        },
                        "max_seconds": {
                            "type": ["integer", "null"],
                            "minimum": 1,
                        },
                    },
                    "required": [
                        "target",
                        "window_title",
                        "mission",
                        "privacy",
                        "max_seconds",
                    ],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pc.stop_screen_recording",
            description=(
                "Stop the in-progress screen recording "
                "and verify the file landed on disk with "
                "real bytes."
            ),
            runs_on="pc",
            handler=stop_screen_recording,
            permission_level=0,
            timeout_seconds=30,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pc.list_recordings",
            description=(
                "List every captured screenshot, window "
                "capture, and recording on the PC, newest "
                "first."
            ),
            runs_on="pc",
            handler=list_recordings,
            permission_level=0,
            timeout_seconds=30,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                }
            },
        ),
    ]

    for tool in tools:
        registry.register(tool)

    verifier.register(
        "pc.ensure_online",
        lambda call, result: _verify_boolean_flag(
            result.output,
            flag="online",
            success_reason=(
                "The Windows companion is reachable."
            ),
            failure_reason=(
                "The Windows companion is still offline."
            ),
        ),
    )
    verifier.register(
        "pc.search_files",
        _verify_search_result,
    )
    verifier.register(
        "pc.download_file",
        lambda call, result: _verify_boolean_flag(
            result.output,
            flag="verified",
            success_reason=(
                "The transferred file passed size and "
                "SHA-256 verification."
            ),
            failure_reason=(
                "The transferred file was not verified."
            ),
        ),
    )
    verifier.register(
        "pc.active_apps",
        lambda call, result: _verify_pc_action(
            result.output,
            required_data_field="windows",
        ),
    )
    verifier.register(
        "pc.open_app",
        lambda call, result: _verify_pc_action(
            result.output,
        ),
    )
    verifier.register(
        "pc.focus_or_open_app",
        lambda call, result: _verify_focus_or_open_app(
            result.output,
        ),
    )
    verifier.register(
        "pc.active_window",
        lambda call, result: _verify_pc_action(
            result.output,
            required_data_field="title",
        ),
    )
    verifier.register(
        "pc.capture_screenshot",
        lambda call, result: _verify_pc_action(
            result.output,
            required_data_field="path",
        ),
    )
    verifier.register(
        "pc.capture_window",
        lambda call, result: _verify_pc_action(
            result.output,
            required_data_field="path",
        ),
    )
    verifier.register(
        "pc.start_screen_recording",
        lambda call, result: _verify_pc_action(
            result.output,
            required_data_field="pid",
        ),
    )
    verifier.register(
        "pc.stop_screen_recording",
        lambda call, result: _verify_pc_action(
            result.output,
            required_data_field="size_bytes",
        ),
    )
    verifier.register(
        "pc.list_recordings",
        lambda call, result: _verify_pc_action(
            result.output,
            required_data_field="recordings",
        ),
    )

    return tools


def _verify_boolean_flag(
    output: Any,
    *,
    flag: str,
    success_reason: str,
    failure_reason: str,
) -> VerificationCheck:
    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason="Tool output was not an object.",
        )

    verified = output.get(flag) is True

    return VerificationCheck(
        verified=verified,
        reason=(
            success_reason
            if verified
            else failure_reason
        ),
        evidence={
            flag: output.get(flag),
            "error": output.get("error"),
        },
    )


def _verify_search_result(
    call,
    result,
) -> VerificationCheck:
    output = result.output

    if not isinstance(output, list):
        return VerificationCheck(
            verified=False,
            reason="File-search output was not a list.",
        )

    valid = all(
        isinstance(match, dict)
        and isinstance(match.get("path"), str)
        and isinstance(match.get("name"), str)
        and isinstance(match.get("size"), int)
        and match["size"] >= 0
        for match in output
    )

    return VerificationCheck(
        verified=valid,
        reason=(
            f"Windows file search returned "
            f"{len(output)} validated matches."
            if valid
            else "Windows file search returned invalid matches."
        ),
        evidence={
            "match_count": len(output),
        },
    )


def _verify_focus_or_open_app(
    output: Any,
) -> VerificationCheck:
    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason="PC action output was not an object.",
        )

    data = output.get("data")
    action = (
        data.get("action")
        if isinstance(data, dict)
        else None
    )
    verified = (
        output.get("ok") is True
        and isinstance(data, dict)
        and data.get("ok") is True
        and action in ("focused", "launched")
    )

    return VerificationCheck(
        verified=verified,
        reason=(
            "The Windows companion confirmed the app was "
            f"{action}."
            if verified
            else "The Windows companion did not confirm "
            "the app was focused or opened."
        ),
        evidence={
            "action": action,
            "error": (
                data.get("error")
                if isinstance(data, dict)
                else output.get("error")
            ),
        },
    )


def _verify_pc_action(
    output: Any,
    *,
    required_data_field: str | None = None,
) -> VerificationCheck:
    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason="PC action output was not an object.",
        )

    action_ok = output.get("ok") is True
    data = output.get("data")
    field_ok = (
        required_data_field is None
        or (
            isinstance(data, dict)
            and required_data_field in data
        )
    )
    verified = action_ok and field_ok

    return VerificationCheck(
        verified=verified,
        reason=(
            "The Windows companion confirmed the action."
            if verified
            else "The Windows companion did not confirm the action."
        ),
        evidence={
            "action_ok": action_ok,
            "error": output.get("error"),
        },
    )
