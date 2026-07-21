from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool
from atlas_agent.verifier import (
    ResultVerifier,
    VerificationCheck,
)


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


def register_local_tools(
    registry: ToolRegistry,
    verifier: ResultVerifier,
    *,
    approved_roots: Iterable[str | Path],
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
