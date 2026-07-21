from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import diagnostics
import implementation_ledger
import logbook
import recovery
from atlas_agent.mission_store import MissionStore
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool
from atlas_agent.verifier import (
    ResultVerifier,
    VerificationCheck,
)

_UPGRADE_STATUS_SCOPES = (
    "summary",
    "finished",
    "remaining",
    "blocked",
)

_MISSION_HISTORY_SCOPES = (
    "last",
    "recent",
    "failed",
)
_FAILED_TASK_STATUSES = {
    "failed",
    "cancelled",
}
_DEFAULT_MISSION_STORE_PATH = Path(
    "/home/atlas/atlas-robot/data/agent_missions.json"
)
_MAX_SPOKEN_ERROR_CHARS = 300
_MAX_FAILURE_ERRORS = 3
_MAX_RECENT_INCIDENTS = 5

_RECOVERY_COMPONENTS = tuple(sorted(recovery.PLAYBOOKS))


_MAX_TEXT_FILE_BYTES = 1_048_576
_SENSITIVE_DIRECTORY_NAMES = {
    ".gnupg",
    ".ssh",
    "credentials",
    "secrets",
}
_SENSITIVE_EXACT_NAMES = {
    "credentials.json",
    "secrets.json",
}
_SENSITIVE_SUFFIXES = {
    ".key",
    ".p12",
    ".pem",
    ".pfx",
}
_SENSITIVE_NAME_MARKERS = {
    "access_token",
    "api_key",
    "apikey",
    "client_secret",
    "private_key",
    "refresh_token",
}
_EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "graphify-out",
    ".superpowers",
    "archive",
    "data",
    "tools",
    "models",
    "voices",
}
_MAX_SEARCH_LINE_CHARS = 300
_SUBPROCESS_TIMEOUT_SECONDS = 10
_ALLOWED_SERVICES = {
    "atlas-wake.service",
    "atlas-robot.service",
    "atlas-hud.service",
    "atlas-hub.service",
    "graphify-mcp.service",
}


def _normalize_extensions(
    extensions: list[str] | None,
) -> set[str] | None:
    if extensions is None:
        return None

    if not isinstance(extensions, list):
        raise ValueError(
            "extensions must be a list of strings or null"
        )

    normalized: set[str] = set()

    for extension in extensions:
        if (
            not isinstance(extension, str)
            or not extension.strip()
        ):
            raise ValueError(
                "extensions must contain non-empty strings"
            )

        cleaned = extension.strip().casefold()

        if not cleaned.startswith("."):
            cleaned = f".{cleaned}"

        normalized.add(cleaned)

    return normalized or None


def _iter_tree(directory: Path) -> Iterable[Path]:
    try:
        children = sorted(
            directory.iterdir(),
            key=lambda item: item.name.casefold(),
        )
    except OSError:
        return

    for child in children:
        if child.is_symlink():
            continue

        if child.is_dir():
            if child.name in _EXCLUDED_DIRECTORY_NAMES:
                continue

            yield from _iter_tree(child)
        elif child.is_file():
            yield child


def register_local_tools(
    registry: ToolRegistry,
    verifier: ResultVerifier,
    *,
    approved_roots: Iterable[str | Path],
    mission_store_path: str | Path | None = None,
) -> list[AtlasTool]:
    roots = tuple(
        dict.fromkeys(
            Path(root).expanduser().resolve()
            for root in approved_roots
        )
    )

    if not roots:
        raise ValueError(
            "approved_roots must not be empty"
        )

    def resolve_approved_path(path: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise ValueError(
                "path must be a non-empty string"
            )

        resolved = Path(path).expanduser().resolve()

        if not any(
            resolved == root or root in resolved.parents
            for root in roots
        ):
            raise PermissionError(
                "Path is outside the approved "
                "Raspberry Pi roots."
            )

        return resolved

    def list_directory(
        path: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not isinstance(limit, int):
            raise ValueError(
                "limit must be an integer"
            )

        if not 1 <= limit <= 200:
            raise ValueError(
                "limit must be between 1 and 200"
            )

        resolved = resolve_approved_path(path)

        if not resolved.exists():
            raise FileNotFoundError(
                f"Directory does not exist: {resolved}"
            )

        if not resolved.is_dir():
            raise NotADirectoryError(
                f"Path is not a directory: {resolved}"
            )

        children = sorted(
            resolved.iterdir(),
            key=lambda item: (
                not item.is_dir(),
                item.name.casefold(),
            ),
        )
        selected = children[:limit]
        entries: list[dict[str, Any]] = []

        for child in selected:
            if child.is_dir():
                entry_type = "directory"
                size = None
            elif child.is_file():
                entry_type = "file"
                size = child.stat().st_size
            else:
                entry_type = "other"
                size = None

            entries.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "type": entry_type,
                    "size": size,
                }
            )

        return {
            "path": str(resolved),
            "entries": entries,
            "count": len(entries),
            "total_count": len(children),
            "truncated": len(children) > limit,
        }

    def read_text_file(
        path: str,
        start_line: int = 1,
        max_lines: int = 200,
        max_chars: int = 12_000,
    ) -> dict[str, Any]:
        if not isinstance(start_line, int):
            raise ValueError(
                "start_line must be an integer"
            )

        if start_line < 1:
            raise ValueError(
                "start_line must be at least 1"
            )

        if not isinstance(max_lines, int):
            raise ValueError(
                "max_lines must be an integer"
            )

        if not 1 <= max_lines <= 500:
            raise ValueError(
                "max_lines must be between 1 and 500"
            )

        if not isinstance(max_chars, int):
            raise ValueError(
                "max_chars must be an integer"
            )

        if not 1 <= max_chars <= 50_000:
            raise ValueError(
                "max_chars must be between 1 and 50000"
            )

        resolved = resolve_approved_path(path)

        if not resolved.exists():
            raise FileNotFoundError(
                f"File does not exist: {resolved}"
            )

        if not resolved.is_file():
            raise IsADirectoryError(
                f"Path is not a file: {resolved}"
            )

        if _is_sensitive_path(resolved):
            raise PermissionError(
                "Reading this sensitive file is not allowed."
            )

        size = resolved.stat().st_size

        if size > _MAX_TEXT_FILE_BYTES:
            raise ValueError(
                "Text file exceeds the 1 MiB safety limit."
            )

        raw = resolved.read_bytes()

        if b"\x00" in raw:
            raise ValueError(
                "File appears to be binary, not text."
            )

        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "File is not valid UTF-8 text."
            ) from exc

        lines = text.splitlines()
        total_lines = len(lines)

        if total_lines and start_line > total_lines:
            raise ValueError(
                f"start_line exceeds the file's "
                f"{total_lines} lines"
            )

        start_index = start_line - 1
        selected_lines = lines[
            start_index:start_index + max_lines
        ]

        fragments: list[str] = []
        used_chars = 0
        included_lines = 0
        char_truncated = False

        for line in selected_lines:
            prefix = "" if included_lines == 0 else "\n"
            fragment = prefix + line
            remaining = max_chars - used_chars

            if len(fragment) <= remaining:
                fragments.append(fragment)
                used_chars += len(fragment)
                included_lines += 1
                continue

            if remaining > 0:
                fragments.append(fragment[:remaining])
                used_chars += remaining
                included_lines += 1

            char_truncated = True
            break

        content = "".join(fragments)
        line_truncated = (
            start_index + len(selected_lines)
            < total_lines
        )
        end_line = (
            start_line + included_lines - 1
            if included_lines
            else 0
        )

        return {
            "path": str(resolved),
            "content": content,
            "start_line": start_line,
            "end_line": end_line,
            "line_count": included_lines,
            "total_lines": total_lines,
            "char_count": len(content),
            "size_bytes": size,
            "truncated": (
                char_truncated or line_truncated
            ),
        }

    def search_files(
        root: str,
        query: str,
        extensions: list[str] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        if (
            not isinstance(query, str)
            or not query.strip()
        ):
            raise ValueError(
                "query must be a non-empty string"
            )

        if not isinstance(limit, int):
            raise ValueError(
                "limit must be an integer"
            )

        if not 1 <= limit <= 200:
            raise ValueError(
                "limit must be between 1 and 200"
            )

        normalized_extensions = _normalize_extensions(
            extensions
        )
        resolved_root = resolve_approved_path(root)

        if not resolved_root.exists():
            raise FileNotFoundError(
                f"Directory does not exist: {resolved_root}"
            )

        if not resolved_root.is_dir():
            raise NotADirectoryError(
                f"Path is not a directory: {resolved_root}"
            )

        query_lower = query.casefold()
        collected: list[Path] = []

        for candidate in _iter_tree(resolved_root):
            if _is_sensitive_path(candidate):
                continue

            if (
                normalized_extensions is not None
                and candidate.suffix.casefold()
                not in normalized_extensions
            ):
                continue

            if query_lower not in candidate.name.casefold():
                continue

            collected.append(candidate)

            if len(collected) > limit:
                break

        truncated = len(collected) > limit
        selected = collected[:limit]

        entries = [
            {
                "name": path.name,
                "path": str(path),
                "relative_path": str(
                    path.relative_to(resolved_root)
                ),
                "type": "file",
                "size": path.stat().st_size,
            }
            for path in selected
        ]

        return {
            "root": str(resolved_root),
            "query": query,
            "entries": entries,
            "count": len(entries),
            "truncated": truncated,
        }

    def search_text(
        root: str,
        query: str,
        extensions: list[str] | None = None,
        case_sensitive: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query:
            raise ValueError(
                "query must be a non-empty string"
            )

        if not isinstance(case_sensitive, bool):
            raise ValueError(
                "case_sensitive must be a boolean"
            )

        if not isinstance(limit, int):
            raise ValueError(
                "limit must be an integer"
            )

        if not 1 <= limit <= 200:
            raise ValueError(
                "limit must be between 1 and 200"
            )

        normalized_extensions = _normalize_extensions(
            extensions
        )
        resolved_root = resolve_approved_path(root)

        if not resolved_root.exists():
            raise FileNotFoundError(
                f"Directory does not exist: {resolved_root}"
            )

        if not resolved_root.is_dir():
            raise NotADirectoryError(
                f"Path is not a directory: {resolved_root}"
            )

        search_query = (
            query if case_sensitive else query.casefold()
        )
        matches: list[dict[str, Any]] = []
        files_scanned = 0
        truncated = False

        for candidate in _iter_tree(resolved_root):
            if _is_sensitive_path(candidate):
                continue

            if (
                normalized_extensions is not None
                and candidate.suffix.casefold()
                not in normalized_extensions
            ):
                continue

            try:
                size = candidate.stat().st_size
            except OSError:
                continue

            if size > _MAX_TEXT_FILE_BYTES:
                continue

            try:
                raw = candidate.read_bytes()
            except OSError:
                continue

            if b"\x00" in raw:
                continue

            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                continue

            files_scanned += 1
            relative_path = str(
                candidate.relative_to(resolved_root)
            )

            for line_number, line in enumerate(
                text.splitlines(),
                start=1,
            ):
                haystack = (
                    line
                    if case_sensitive
                    else line.casefold()
                )

                if search_query not in haystack:
                    continue

                bounded_line = (
                    line[:_MAX_SEARCH_LINE_CHARS]
                    if len(line) > _MAX_SEARCH_LINE_CHARS
                    else line
                )

                matches.append(
                    {
                        "path": str(candidate),
                        "relative_path": relative_path,
                        "line_number": line_number,
                        "line": bounded_line,
                    }
                )

                if len(matches) >= limit:
                    truncated = True
                    break

            if truncated:
                break

        return {
            "root": str(resolved_root),
            "query": query,
            "matches": matches,
            "count": len(matches),
            "truncated": truncated,
            "files_scanned": files_scanned,
        }

    def read_service_logs(
        service: str,
        minutes: int = 10,
        limit: int = 200,
    ) -> dict[str, Any]:
        if (
            not isinstance(service, str)
            or service not in _ALLOWED_SERVICES
        ):
            raise ValueError(
                "service must be one of the approved "
                "A.T.L.A.S. services"
            )

        if not isinstance(minutes, int):
            raise ValueError(
                "minutes must be an integer"
            )

        if not 1 <= minutes <= 1440:
            raise ValueError(
                "minutes must be between 1 and 1440"
            )

        if not isinstance(limit, int):
            raise ValueError(
                "limit must be an integer"
            )

        if not 1 <= limit <= 500:
            raise ValueError(
                "limit must be between 1 and 500"
            )

        args = [
            "journalctl",
            "-u",
            service,
            "--since",
            f"{minutes} minutes ago",
            "--no-pager",
            "-o",
            "short-iso",
            "-n",
            str(limit),
        ]

        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"journalctl timed out for {service}"
            ) from exc

        if completed.returncode != 0:
            raise RuntimeError(
                f"journalctl failed for {service}: "
                f"{completed.stderr.strip()}"
            )

        raw_lines = [
            line
            for line in completed.stdout.splitlines()
            if line
        ]
        bounded_lines = raw_lines[:limit]

        return {
            "service": service,
            "minutes": minutes,
            "lines": bounded_lines,
            "count": len(bounded_lines),
            "truncated": len(raw_lines) > len(bounded_lines),
        }

    def get_service_status(
        service: str,
    ) -> dict[str, Any]:
        if (
            not isinstance(service, str)
            or service not in _ALLOWED_SERVICES
        ):
            raise ValueError(
                "service must be one of the approved "
                "A.T.L.A.S. services"
            )

        args = [
            "systemctl",
            "show",
            service,
            "--no-pager",
            "--property="
            "Id,Description,LoadState,ActiveState,"
            "SubState,MainPID",
        ]

        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"systemctl show timed out for {service}"
            ) from exc

        if completed.returncode != 0:
            raise RuntimeError(
                f"systemctl show failed for {service}: "
                f"{completed.stderr.strip()}"
            )

        properties: dict[str, str] = {}

        for line in completed.stdout.splitlines():
            if "=" not in line:
                continue

            key, _, value = line.partition("=")
            properties[key] = value

        load_state = properties.get("LoadState", "")
        active_state = properties.get("ActiveState", "")
        sub_state = properties.get("SubState", "")
        description = properties.get("Description", "")
        main_pid_raw = properties.get("MainPID", "")

        if not load_state or not active_state or not sub_state:
            raise RuntimeError(
                "systemctl show returned incomplete "
                f"status for {service}"
            )

        main_pid = (
            int(main_pid_raw)
            if main_pid_raw.isdigit()
            and main_pid_raw != "0"
            else None
        )

        return {
            "service": service,
            "description": description,
            "load_state": load_state,
            "active_state": active_state,
            "sub_state": sub_state,
            "main_pid": main_pid,
        }

    def get_upgrade_status(
        scope: str = "summary",
    ) -> dict[str, Any]:
        if scope not in _UPGRADE_STATUS_SCOPES:
            raise ValueError(
                "scope must be one of "
                f"{', '.join(_UPGRADE_STATUS_SCOPES)}"
            )

        summary = implementation_ledger.summarize()

        if scope == "summary":
            last = summary["last_updated_feature"]
            return {
                "scope": scope,
                "finished_count": summary["counts"]["finished"],
                "remaining_count": summary["counts"]["remaining"],
                "blocked_count": summary["counts"]["blocked"],
                "total_count": summary["counts"]["total"],
                "last_updated_feature": (
                    last["title"] if last is not None else None
                ),
            }

        entries = summary[scope]

        return {
            "scope": scope,
            "items": [
                {
                    "feature_id": entry["feature_id"],
                    "title": entry["title"],
                }
                for entry in entries
            ],
            "count": len(entries),
        }

    mission_store = MissionStore(
        Path(mission_store_path).expanduser()
        if mission_store_path is not None
        else _DEFAULT_MISSION_STORE_PATH
    )

    def _load_missions_newest_first() -> list[Any]:
        tasks = mission_store.load(
            recover_interrupted=False,
        )
        return sorted(
            tasks,
            key=lambda task: task.updated_at,
            reverse=True,
        )

    def _mission_entry(task: Any) -> dict[str, Any]:
        note = task.metadata.get("recovery_reason")

        return {
            "goal": task.goal,
            "source": task.source,
            "status": task.status.value,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "note": (
                note
                if isinstance(note, str) and note
                else None
            ),
        }

    def get_mission_history(
        scope: str,
        limit: int = 5,
    ) -> dict[str, Any]:
        if scope not in _MISSION_HISTORY_SCOPES:
            raise ValueError(
                "scope must be one of "
                f"{', '.join(_MISSION_HISTORY_SCOPES)}"
            )

        if not isinstance(limit, int):
            raise ValueError("limit must be an integer")

        if not 1 <= limit <= 20:
            raise ValueError(
                "limit must be between 1 and 20"
            )

        missions = _load_missions_newest_first()

        if scope == "failed":
            selected = [
                task
                for task in missions
                if task.status.value
                in _FAILED_TASK_STATUSES
            ][:limit]
        elif scope == "last":
            selected = missions[:1]
        else:
            selected = missions[:limit]

        return {
            "scope": scope,
            "missions": [
                _mission_entry(task)
                for task in selected
            ],
            "count": len(selected),
            "total_count": len(missions),
        }

    def explain_last_failure(
        window: int = 25,
    ) -> dict[str, Any]:
        if not isinstance(window, int):
            raise ValueError(
                "window must be an integer"
            )

        if not 1 <= window <= 50:
            raise ValueError(
                "window must be between 1 and 50"
            )

        failed_mission = None

        for task in _load_missions_newest_first():
            if (
                task.status.value
                in _FAILED_TASK_STATUSES
            ):
                failed_mission = _mission_entry(task)
                break

        last_error_interaction = None

        for record in reversed(
            logbook.read_interactions(window)
        ):
            errors = record.get("errors")

            if (
                not isinstance(errors, list)
                or not errors
            ):
                continue

            last_error_interaction = {
                "transcript": record.get("transcript"),
                "intent": record.get("intent"),
                "errors": [
                    str(error)[:_MAX_SPOKEN_ERROR_CHARS]
                    for error in errors[
                        :_MAX_FAILURE_ERRORS
                    ]
                ],
                "outcome": record.get("outcome"),
                "timestamp": record.get("ts"),
            }
            break

        recent_incidents = [
            {
                "component": incident.get("component"),
                "cause": incident.get("cause"),
                "action": incident.get("action"),
                "verification": incident.get(
                    "verification"
                ),
                "resolved": incident.get("resolved"),
                "timestamp": incident.get("ts"),
            }
            for incident in reversed(
                logbook.read_incidents(
                    _MAX_RECENT_INCIDENTS
                )
            )
        ]

        evidence_found = (
            failed_mission is not None
            or last_error_interaction is not None
            or bool(recent_incidents)
        )

        suggested_retries: list[dict[str, Any]] = []

        for incident in recent_incidents:
            if (
                incident.get("resolved") is False
                and incident.get("component")
                in _RECOVERY_COMPONENTS
            ):
                suggested_retries.append({
                    "action": "recover_component",
                    "component": incident["component"],
                    "reason": str(
                        incident.get("verification")
                        or "unresolved incident"
                    )[:_MAX_SPOKEN_ERROR_CHARS],
                })
                break

        if failed_mission is not None:
            suggested_retries.append({
                "action": "retry_mission",
                "goal": failed_mission["goal"],
                "reason": (
                    f"mission {failed_mission['status']}"
                ),
            })

        return {
            "window": window,
            "failed_mission": failed_mission,
            "last_error_interaction": (
                last_error_interaction
            ),
            "recent_incidents": recent_incidents,
            "incident_count": len(recent_incidents),
            "evidence_found": evidence_found,
            "suggested_retries": suggested_retries,
        }

    def run_diagnostics(
        components: list[str] | None,
    ) -> dict[str, Any]:
        if components is not None:
            if (
                not isinstance(components, list)
                or not components
            ):
                raise ValueError(
                    "components must be null or a "
                    "non-empty list"
                )

            unknown = [
                component
                for component in components
                if component
                not in diagnostics.STRUCTURED_COMPONENTS
            ]

            if unknown:
                raise ValueError(
                    "unknown diagnostic components: "
                    + ", ".join(
                        str(item) for item in unknown
                    )
                )

        findings = diagnostics.run_structured_checks(
            components
        )
        problem_count = sum(
            1
            for finding in findings
            if not finding["ok"]
        )

        return {
            "components": [
                finding["component"]
                for finding in findings
            ],
            "findings": findings,
            "count": len(findings),
            "ok_count": len(findings) - problem_count,
            "problem_count": problem_count,
            "all_ok": problem_count == 0,
        }

    def recover_component(
        component: str,
    ) -> dict[str, Any]:
        if (
            not isinstance(component, str)
            or component not in _RECOVERY_COMPONENTS
        ):
            raise ValueError(
                "component must be one of "
                f"{', '.join(_RECOVERY_COMPONENTS)}"
            )

        incident = recovery.run_playbook(component)

        return {
            "component": incident.get("component"),
            "cause": incident.get("cause"),
            "action": incident.get("action"),
            "verification": incident.get("verification"),
            "resolved": bool(incident.get("resolved")),
        }

    roots_text = ", ".join(str(root) for root in roots)

    tools = [
        AtlasTool(
            name="pi.list_directory",
            description=(
                "List the immediate files and subdirectories "
                "inside an approved Raspberry Pi folder. Use "
                "this for local Pi folders and the A.T.L.A.S. "
                "project instead of pc.search_files. Approved "
                f"roots: {roots_text}."
            ),
            runs_on="pi",
            handler=list_directory,
            permission_level=0,
            timeout_seconds=15,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Exact absolute Raspberry Pi "
                                "directory path. The A.T.L.A.S. "
                                "project folder is "
                                "/home/atlas/atlas-robot."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "description": (
                                "Maximum number of immediate "
                                "entries to return."
                            ),
                        },
                    },
                    "required": ["path", "limit"],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pi.read_text_file",
            description=(
                "Read a bounded section of a UTF-8 text file "
                "inside an approved Raspberry Pi root. This is "
                "read-only and rejects binary, oversized, and "
                "sensitive credential files. Approved roots: "
                f"{roots_text}."
            ),
            runs_on="pi",
            handler=read_text_file,
            permission_level=0,
            timeout_seconds=15,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Exact absolute path of the "
                                "Raspberry Pi text file."
                            ),
                        },
                        "start_line": {
                            "type": "integer",
                            "minimum": 1,
                            "description": (
                                "First one-based line to read."
                            ),
                        },
                        "max_lines": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 500,
                            "description": (
                                "Maximum number of lines to read."
                            ),
                        },
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50_000,
                            "description": (
                                "Maximum number of characters "
                                "to return."
                            ),
                        },
                    },
                    "required": [
                        "path",
                        "start_line",
                        "max_lines",
                        "max_chars",
                    ],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pi.search_files",
            description=(
                "Find files by filename inside an approved "
                "Raspberry Pi root. Use this for local Pi "
                "and A.T.L.A.S. project filename searches "
                "instead of pc.search_files. Approved roots: "
                f"{roots_text}."
            ),
            runs_on="pi",
            handler=search_files,
            permission_level=0,
            timeout_seconds=15,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "root": {
                            "type": "string",
                            "description": (
                                "Exact absolute Raspberry Pi "
                                "directory to search inside."
                            ),
                        },
                        "query": {
                            "type": "string",
                            "description": (
                                "Filename substring to match, "
                                "case-insensitively."
                            ),
                        },
                        "extensions": {
                            "type": ["array", "null"],
                            "items": {
                                "type": "string",
                            },
                            "description": (
                                "Optional list of file "
                                "extensions to keep, such as "
                                "['.py']. Null for no filter."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "description": (
                                "Maximum number of matching "
                                "files to return."
                            ),
                        },
                    },
                    "required": [
                        "root",
                        "query",
                        "extensions",
                        "limit",
                    ],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pi.search_text",
            description=(
                "Search approved Raspberry Pi text files for "
                "an exact substring and return matching lines. "
                "Read-only and skips sensitive, binary, and "
                f"oversized files. Approved roots: {roots_text}."
            ),
            runs_on="pi",
            handler=search_text,
            permission_level=0,
            timeout_seconds=20,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "root": {
                            "type": "string",
                            "description": (
                                "Exact absolute Raspberry Pi "
                                "directory to search inside."
                            ),
                        },
                        "query": {
                            "type": "string",
                            "description": (
                                "Literal text to search for."
                            ),
                        },
                        "extensions": {
                            "type": ["array", "null"],
                            "items": {
                                "type": "string",
                            },
                            "description": (
                                "Optional list of file "
                                "extensions to keep, such as "
                                "['.py']. Null for no filter."
                            ),
                        },
                        "case_sensitive": {
                            "type": "boolean",
                            "description": (
                                "Whether the text search is "
                                "case-sensitive."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "description": (
                                "Maximum number of matching "
                                "lines to return."
                            ),
                        },
                    },
                    "required": [
                        "root",
                        "query",
                        "extensions",
                        "case_sensitive",
                        "limit",
                    ],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pi.read_service_logs",
            description=(
                "Read bounded recent journalctl logs for an "
                "approved A.T.L.A.S. systemd service. "
                "Read-only. Approved services: "
                + ", ".join(sorted(_ALLOWED_SERVICES))
                + "."
            ),
            runs_on="pi",
            handler=read_service_logs,
            permission_level=0,
            timeout_seconds=20,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "enum": sorted(
                                _ALLOWED_SERVICES
                            ),
                            "description": (
                                "The approved systemd service "
                                "unit name."
                            ),
                        },
                        "minutes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 1440,
                            "description": (
                                "How many minutes of recent "
                                "logs to read."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 500,
                            "description": (
                                "Maximum number of log lines "
                                "to return."
                            ),
                        },
                    },
                    "required": [
                        "service",
                        "minutes",
                        "limit",
                    ],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pi.get_service_status",
            description=(
                "Report the current systemd status of an "
                "approved A.T.L.A.S. service. Read-only. "
                "Approved services: "
                + ", ".join(sorted(_ALLOWED_SERVICES))
                + "."
            ),
            runs_on="pi",
            handler=get_service_status,
            permission_level=0,
            timeout_seconds=15,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "enum": sorted(
                                _ALLOWED_SERVICES
                            ),
                            "description": (
                                "The approved systemd service "
                                "unit name."
                            ),
                        },
                    },
                    "required": ["service"],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pi.get_upgrade_status",
            description=(
                "Report the A.T.L.A.S. upgrade roadmap ledger: which "
                "features are finished, remaining, or blocked on an "
                "external dependency, and what was implemented last. "
                "Read-only."
            ),
            runs_on="pi",
            handler=get_upgrade_status,
            permission_level=0,
            timeout_seconds=10,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "enum": list(_UPGRADE_STATUS_SCOPES),
                            "description": (
                                "'summary' for counts and the last "
                                "finished item, or 'finished'/"
                                "'remaining'/'blocked' for the matching "
                                "feature list."
                            ),
                        },
                    },
                    "required": ["scope"],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pi.get_mission_history",
            description=(
                "Report recorded A.T.L.A.S. agent missions "
                "from the persistent mission store: the last "
                "mission, recent missions, or failed "
                "missions, with goal, status, and "
                "timestamps. Read-only."
            ),
            runs_on="pi",
            handler=get_mission_history,
            permission_level=0,
            timeout_seconds=10,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "enum": list(
                                _MISSION_HISTORY_SCOPES
                            ),
                            "description": (
                                "'last' for the most recent "
                                "mission, 'recent' for the "
                                "latest missions, or 'failed' "
                                "for missions that failed or "
                                "were cancelled."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "description": (
                                "Maximum number of missions "
                                "to return."
                            ),
                        },
                    },
                    "required": ["scope", "limit"],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pi.explain_last_failure",
            description=(
                "Explain the most recent recorded failure "
                "using only real evidence: the last failed "
                "mission from the mission store, the last "
                "logged voice-turn error, and recent "
                "recovery incidents. Never invents a root "
                "cause. Read-only."
            ),
            runs_on="pi",
            handler=explain_last_failure,
            permission_level=0,
            timeout_seconds=10,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "window": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "description": (
                                "How many recent logged "
                                "voice turns to scan for "
                                "errors."
                            ),
                        },
                    },
                    "required": ["window"],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pi.run_diagnostics",
            description=(
                "Run read-only structured diagnostics "
                "across the A.T.L.A.S. systems: services, "
                "microphone, speaker, camera, PC "
                "companion, direct Ethernet, Wi-Fi, disk, "
                "temperature, budget, mission store, "
                "Instagram refresher, printer, and voice "
                "provider. Reports honest findings and "
                "never repairs anything."
            ),
            runs_on="pi",
            handler=run_diagnostics,
            permission_level=0,
            timeout_seconds=60,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "components": {
                            "type": ["array", "null"],
                            "items": {
                                "type": "string",
                                "enum": list(
                                    diagnostics.STRUCTURED_COMPONENTS
                                ),
                            },
                            "description": (
                                "Specific components to "
                                "check, or null to check "
                                "everything."
                            ),
                        },
                    },
                    "required": ["components"],
                    "additionalProperties": False,
                }
            },
        ),
        AtlasTool(
            name="pi.recover_component",
            description=(
                "Run one approved, bounded recovery "
                "playbook for a failed A.T.L.A.S. "
                "component. Each playbook only repairs "
                "its own component, is cooldown-guarded "
                "against restart loops, verifies the "
                "result, and does nothing when the "
                "component is already healthy. Approved "
                "components: "
                + ", ".join(_RECOVERY_COMPONENTS)
                + "."
            ),
            runs_on="pi",
            handler=recover_component,
            permission_level=1,
            timeout_seconds=60,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "component": {
                            "type": "string",
                            "enum": list(
                                _RECOVERY_COMPONENTS
                            ),
                            "description": (
                                "The approved component "
                                "whose recovery playbook "
                                "should run."
                            ),
                        },
                    },
                    "required": ["component"],
                    "additionalProperties": False,
                }
            },
        ),
    ]

    for tool in tools:
        registry.register(tool)

    verifier.register(
        "pi.list_directory",
        _verify_directory_listing,
    )
    verifier.register(
        "pi.read_text_file",
        _verify_text_file_read,
    )
    verifier.register(
        "pi.search_files",
        _verify_file_search,
    )
    verifier.register(
        "pi.search_text",
        _verify_text_search,
    )
    verifier.register(
        "pi.read_service_logs",
        _verify_service_logs,
    )
    verifier.register(
        "pi.get_service_status",
        _verify_service_status,
    )
    verifier.register(
        "pi.get_upgrade_status",
        _verify_upgrade_status,
    )
    verifier.register(
        "pi.get_mission_history",
        _verify_mission_history,
    )
    verifier.register(
        "pi.explain_last_failure",
        _verify_failure_explanation,
    )
    verifier.register(
        "pi.run_diagnostics",
        _verify_diagnostics,
    )
    verifier.register(
        "pi.recover_component",
        _verify_recovery,
    )

    return tools


def _is_sensitive_path(path: Path) -> bool:
    lowered_parts = {
        part.casefold()
        for part in path.parts
    }
    name = path.name.casefold()

    if lowered_parts & _SENSITIVE_DIRECTORY_NAMES:
        return True

    if name.startswith(".env"):
        return True

    if name in _SENSITIVE_EXACT_NAMES:
        return True

    if path.suffix.casefold() in _SENSITIVE_SUFFIXES:
        return True

    return any(
        marker in name
        for marker in _SENSITIVE_NAME_MARKERS
    )


def _verify_directory_listing(
    call: Any,
    result: Any,
) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason=(
                "Directory listing output was not an object."
            ),
        )

    path = output.get("path")
    entries = output.get("entries")
    count = output.get("count")
    total_count = output.get("total_count")

    valid_entries = (
        isinstance(entries, list)
        and all(
            isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and isinstance(entry.get("path"), str)
            and entry.get("type")
            in {"file", "directory", "other"}
            for entry in entries
        )
    )

    verified = (
        isinstance(path, str)
        and bool(path)
        and valid_entries
        and isinstance(count, int)
        and count == len(entries)
        and isinstance(total_count, int)
        and total_count >= count
    )

    return VerificationCheck(
        verified=verified,
        reason=(
            "The Raspberry Pi directory listing was "
            "returned successfully."
            if verified
            else "The Raspberry Pi directory listing "
            "was malformed."
        ),
        evidence={
            "path": path,
            "entry_count": (
                count if isinstance(count, int) else 0
            ),
            "total_count": (
                total_count
                if isinstance(total_count, int)
                else 0
            ),
        },
    )


def _verify_text_file_read(
    call: Any,
    result: Any,
) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason=(
                "Text-file output was not an object."
            ),
        )

    path = output.get("path")
    content = output.get("content")
    start_line = output.get("start_line")
    end_line = output.get("end_line")
    line_count = output.get("line_count")
    total_lines = output.get("total_lines")
    char_count = output.get("char_count")
    truncated = output.get("truncated")

    verified = (
        isinstance(path, str)
        and bool(path)
        and isinstance(content, str)
        and isinstance(start_line, int)
        and start_line >= 1
        and isinstance(end_line, int)
        and end_line >= 0
        and isinstance(line_count, int)
        and line_count >= 0
        and isinstance(total_lines, int)
        and total_lines >= line_count
        and isinstance(char_count, int)
        and char_count == len(content)
        and isinstance(truncated, bool)
    )

    return VerificationCheck(
        verified=verified,
        reason=(
            "The Raspberry Pi text file was read "
            "successfully."
            if verified
            else "The Raspberry Pi text-file result "
            "was malformed."
        ),
        evidence={
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "line_count": line_count,
            "total_lines": total_lines,
            "char_count": char_count,
            "truncated": truncated,
        },
    )


def _verify_file_search(
    call: Any,
    result: Any,
) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason=(
                "File search output was not an object."
            ),
        )

    root = output.get("root")
    query = output.get("query")
    entries = output.get("entries")
    count = output.get("count")
    truncated = output.get("truncated")

    valid_entries = (
        isinstance(entries, list)
        and all(
            isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and isinstance(entry.get("path"), str)
            and isinstance(
                entry.get("relative_path"), str
            )
            and entry.get("type") == "file"
            and (
                entry.get("size") is None
                or isinstance(entry.get("size"), int)
            )
            for entry in entries
        )
    )

    verified = (
        isinstance(root, str)
        and bool(root)
        and isinstance(query, str)
        and bool(query)
        and valid_entries
        and isinstance(count, int)
        and count == len(entries)
        and isinstance(truncated, bool)
    )

    return VerificationCheck(
        verified=verified,
        reason=(
            "The Raspberry Pi file search was returned "
            "successfully."
            if verified
            else "The Raspberry Pi file search result "
            "was malformed."
        ),
        evidence={
            "root": root,
            "query": query,
            "count": (
                count if isinstance(count, int) else 0
            ),
            "truncated": truncated,
        },
    )


def _verify_text_search(
    call: Any,
    result: Any,
) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason=(
                "Text search output was not an object."
            ),
        )

    root = output.get("root")
    query = output.get("query")
    matches = output.get("matches")
    count = output.get("count")
    truncated = output.get("truncated")
    files_scanned = output.get("files_scanned")

    valid_matches = (
        isinstance(matches, list)
        and all(
            isinstance(match, dict)
            and isinstance(match.get("path"), str)
            and isinstance(
                match.get("relative_path"), str
            )
            and isinstance(match.get("line_number"), int)
            and match.get("line_number") >= 1
            and isinstance(match.get("line"), str)
            for match in matches
        )
    )

    verified = (
        isinstance(root, str)
        and bool(root)
        and isinstance(query, str)
        and bool(query)
        and valid_matches
        and isinstance(count, int)
        and count == len(matches)
        and isinstance(truncated, bool)
        and (
            files_scanned is None
            or (
                isinstance(files_scanned, int)
                and files_scanned >= 0
            )
        )
    )

    return VerificationCheck(
        verified=verified,
        reason=(
            "The Raspberry Pi text search was returned "
            "successfully."
            if verified
            else "The Raspberry Pi text search result "
            "was malformed."
        ),
        evidence={
            "root": root,
            "query": query,
            "count": (
                count if isinstance(count, int) else 0
            ),
            "truncated": truncated,
        },
    )


def _verify_service_logs(
    call: Any,
    result: Any,
) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason=(
                "Service log output was not an object."
            ),
        )

    service = output.get("service")
    minutes = output.get("minutes")
    lines = output.get("lines")
    count = output.get("count")
    truncated = output.get("truncated")

    verified = (
        isinstance(service, str)
        and service in _ALLOWED_SERVICES
        and isinstance(minutes, int)
        and minutes >= 1
        and isinstance(lines, list)
        and all(
            isinstance(line, str) for line in lines
        )
        and isinstance(count, int)
        and count == len(lines)
        and isinstance(truncated, bool)
    )

    return VerificationCheck(
        verified=verified,
        reason=(
            "The A.T.L.A.S. service logs were returned "
            "successfully."
            if verified
            else "The A.T.L.A.S. service log result "
            "was malformed."
        ),
        evidence={
            "service": service,
            "count": (
                count if isinstance(count, int) else 0
            ),
            "truncated": truncated,
        },
    )


def _verify_service_status(
    call: Any,
    result: Any,
) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason=(
                "Service status output was not an object."
            ),
        )

    service = output.get("service")
    load_state = output.get("load_state")
    active_state = output.get("active_state")
    sub_state = output.get("sub_state")
    main_pid = output.get("main_pid")

    verified = (
        isinstance(service, str)
        and service in _ALLOWED_SERVICES
        and isinstance(load_state, str)
        and bool(load_state)
        and isinstance(active_state, str)
        and bool(active_state)
        and isinstance(sub_state, str)
        and bool(sub_state)
        and (
            main_pid is None
            or (
                isinstance(main_pid, int)
                and main_pid >= 0
            )
        )
    )

    return VerificationCheck(
        verified=verified,
        reason=(
            "The A.T.L.A.S. service status was returned "
            "successfully."
            if verified
            else "The A.T.L.A.S. service status result "
            "was malformed."
        ),
        evidence={
            "service": service,
            "active_state": active_state,
            "sub_state": sub_state,
        },
    )


def _verify_upgrade_status(
    call: Any,
    result: Any,
) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason=(
                "Upgrade status output was not an object."
            ),
        )

    scope = output.get("scope")

    if scope not in _UPGRADE_STATUS_SCOPES:
        return VerificationCheck(
            verified=False,
            reason="Upgrade status result had an unknown scope.",
        )

    if scope == "summary":
        verified = (
            isinstance(output.get("finished_count"), int)
            and isinstance(output.get("remaining_count"), int)
            and isinstance(output.get("blocked_count"), int)
            and isinstance(output.get("total_count"), int)
        )
    else:
        items = output.get("items")
        count = output.get("count")
        verified = (
            isinstance(items, list)
            and all(
                isinstance(item, dict)
                and isinstance(item.get("feature_id"), str)
                and isinstance(item.get("title"), str)
                for item in items
            )
            and isinstance(count, int)
            and count == len(items)
        )

    return VerificationCheck(
        verified=verified,
        reason=(
            "The A.T.L.A.S. upgrade status was returned successfully."
            if verified
            else "The A.T.L.A.S. upgrade status result was malformed."
        ),
        evidence={"scope": scope},
    )


def _verify_mission_history(
    call: Any,
    result: Any,
) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason=(
                "Mission history output was not an object."
            ),
        )

    scope = output.get("scope")
    missions = output.get("missions")
    count = output.get("count")
    total_count = output.get("total_count")

    verified = (
        scope in _MISSION_HISTORY_SCOPES
        and isinstance(missions, list)
        and all(
            isinstance(mission, dict)
            and isinstance(mission.get("goal"), str)
            and isinstance(mission.get("status"), str)
            and isinstance(
                mission.get("updated_at"), str
            )
            for mission in missions
        )
        and isinstance(count, int)
        and count == len(missions)
        and isinstance(total_count, int)
        and total_count >= count
    )

    return VerificationCheck(
        verified=verified,
        reason=(
            "The A.T.L.A.S. mission history was returned "
            "successfully."
            if verified
            else "The A.T.L.A.S. mission history result "
            "was malformed."
        ),
        evidence={
            "scope": scope,
            "count": (
                count if isinstance(count, int) else 0
            ),
        },
    )


def _verify_failure_explanation(
    call: Any,
    result: Any,
) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason=(
                "Failure explanation output was not "
                "an object."
            ),
        )

    window = output.get("window")
    failed_mission = output.get("failed_mission")
    interaction = output.get("last_error_interaction")
    incidents = output.get("recent_incidents")
    incident_count = output.get("incident_count")
    evidence_found = output.get("evidence_found")

    mission_ok = failed_mission is None or (
        isinstance(failed_mission, dict)
        and isinstance(
            failed_mission.get("goal"), str
        )
        and failed_mission.get("status")
        in _FAILED_TASK_STATUSES
    )
    interaction_ok = interaction is None or (
        isinstance(interaction, dict)
        and isinstance(
            interaction.get("errors"), list
        )
        and bool(interaction.get("errors"))
    )
    incidents_ok = isinstance(
        incidents, list
    ) and all(
        isinstance(incident, dict)
        for incident in incidents
    )

    suggestions = output.get("suggested_retries")
    suggestions_ok = (
        isinstance(suggestions, list)
        and len(suggestions) <= 2
        and all(
            isinstance(suggestion, dict)
            and (
                (
                    suggestion.get("action")
                    == "recover_component"
                    and suggestion.get("component")
                    in _RECOVERY_COMPONENTS
                )
                or (
                    suggestion.get("action")
                    == "retry_mission"
                    and isinstance(
                        suggestion.get("goal"), str
                    )
                    and bool(suggestion.get("goal"))
                )
            )
            for suggestion in suggestions
        )
        and (
            not suggestions
            or evidence_found is True
        )
    )

    verified = (
        isinstance(window, int)
        and window >= 1
        and mission_ok
        and interaction_ok
        and incidents_ok
        and suggestions_ok
        and isinstance(incident_count, int)
        and incident_count == len(incidents)
        and isinstance(evidence_found, bool)
        and evidence_found
        == (
            failed_mission is not None
            or interaction is not None
            or bool(incidents)
        )
    )

    return VerificationCheck(
        verified=verified,
        reason=(
            "The failure explanation used only recorded "
            "evidence."
            if verified
            else "The failure explanation result was "
            "malformed."
        ),
        evidence={
            "evidence_found": evidence_found,
            "incident_count": (
                incident_count
                if isinstance(incident_count, int)
                else 0
            ),
        },
    )


def _verify_diagnostics(
    call: Any,
    result: Any,
) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason=(
                "Diagnostics output was not an object."
            ),
        )

    findings = output.get("findings")
    count = output.get("count")
    ok_count = output.get("ok_count")
    problem_count = output.get("problem_count")
    all_ok = output.get("all_ok")

    valid_findings = (
        isinstance(findings, list)
        and all(
            isinstance(finding, dict)
            and finding.get("component")
            in diagnostics.STRUCTURED_COMPONENTS
            and isinstance(finding.get("ok"), bool)
            and isinstance(finding.get("detail"), str)
            and bool(finding.get("detail"))
            for finding in findings
        )
    )

    verified = (
        valid_findings
        and isinstance(count, int)
        and count == len(findings)
        and isinstance(ok_count, int)
        and isinstance(problem_count, int)
        and ok_count + problem_count == count
        and problem_count
        == sum(
            1
            for finding in findings
            if finding.get("ok") is False
        )
        and isinstance(all_ok, bool)
        and all_ok == (problem_count == 0)
    )

    return VerificationCheck(
        verified=verified,
        reason=(
            "The structured diagnostics findings were "
            "returned consistently."
            if verified
            else "The structured diagnostics result was "
            "malformed."
        ),
        evidence={
            "count": (
                count if isinstance(count, int) else 0
            ),
            "problem_count": (
                problem_count
                if isinstance(problem_count, int)
                else 0
            ),
        },
    )


def _verify_recovery(
    call: Any,
    result: Any,
) -> VerificationCheck:
    output = result.output

    if not isinstance(output, dict):
        return VerificationCheck(
            verified=False,
            reason=(
                "Recovery output was not an object."
            ),
        )

    component = output.get("component")
    cause = output.get("cause")
    action = output.get("action")
    verification = output.get("verification")
    resolved = output.get("resolved")

    verified = (
        component in _RECOVERY_COMPONENTS
        and isinstance(cause, str)
        and bool(cause)
        and isinstance(action, str)
        and bool(action)
        and isinstance(verification, str)
        and bool(verification)
        and isinstance(resolved, bool)
    )

    return VerificationCheck(
        verified=verified,
        reason=(
            "The recovery playbook reported a complete "
            "incident record."
            if verified
            else "The recovery result was malformed."
        ),
        evidence={
            "component": component,
            "resolved": (
                resolved
                if isinstance(resolved, bool)
                else False
            ),
        },
    )
