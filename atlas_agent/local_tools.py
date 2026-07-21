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

    def list_directory(
        path: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or not path.strip():
            raise ValueError(
                "path must be a non-empty string"
            )

        if not isinstance(limit, int):
            raise ValueError(
                "limit must be an integer"
            )

        if not 1 <= limit <= 200:
            raise ValueError(
                "limit must be between 1 and 200"
            )

        requested = Path(path).expanduser()
        resolved = requested.resolve()

        if not any(
            resolved == root or root in resolved.parents
            for root in roots
        ):
            raise PermissionError(
                "Directory is outside the approved "
                "Raspberry Pi roots."
            )

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
        )
    ]

    for tool in tools:
        registry.register(tool)

    verifier.register(
        "pi.list_directory",
        _verify_directory_listing,
    )

    return tools


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
