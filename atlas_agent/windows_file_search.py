from __future__ import annotations

import base64
import json
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any


CommandRunner = Callable[
    ...,
    subprocess.CompletedProcess[str],
]
EXTENSION_PATTERN = re.compile(r"^\.[a-z0-9]{1,12}$")


class WindowsFileSearchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WindowsFileMatch:
    path: str
    name: str
    size: int
    modified_at: str


class WindowsFileSearch:
    def __init__(
        self,
        *,
        host: str,
        username: str,
        identity_file: str | Path,
        approved_remote_roots: Iterable[
            str | PureWindowsPath
        ],
        port: int = 22,
        command_timeout_seconds: float = 120,
        command_runner: CommandRunner = subprocess.run,
    ) -> None:
        if not host.strip():
            raise ValueError("host cannot be empty")

        if not username.strip():
            raise ValueError("username cannot be empty")

        if port < 1 or port > 65535:
            raise ValueError(
                "port must be between 1 and 65535"
            )

        if command_timeout_seconds <= 0:
            raise ValueError(
                "command_timeout_seconds must be greater than zero"
            )

        identity_path = Path(identity_file)

        if not identity_path.is_file():
            raise ValueError(
                f"SSH identity file does not exist: {identity_path}"
            )

        roots = tuple(
            PureWindowsPath(root)
            for root in approved_remote_roots
        )

        if not roots:
            raise ValueError(
                "At least one approved remote root is required"
            )

        for root in roots:
            if not root.is_absolute() or ".." in root.parts:
                raise ValueError(
                    f"Invalid approved remote root: {root}"
                )

        self.host = host
        self.username = username
        self.identity_file = identity_path
        self.approved_remote_roots = roots
        self.port = port
        self.command_timeout_seconds = (
            command_timeout_seconds
        )
        self._run_command = command_runner

    def search(
        self,
        query: str = "",
        *,
        extensions: Iterable[str] | None = None,
        limit: int = 50,
    ) -> list[WindowsFileMatch]:
        if any(
            character in query
            for character in ("\x00", "\r", "\n")
        ):
            raise ValueError(
                "query contains invalid characters"
            )

        if len(query) > 200:
            raise ValueError(
                "query cannot exceed 200 characters"
            )

        if limit < 1 or limit > 200:
            raise ValueError(
                "limit must be between 1 and 200"
            )

        normalized_extensions = self._extensions(
            extensions
        )
        roots_expression = ",".join(
            self._powershell_literal(str(root))
            for root in self.approved_remote_roots
        )
        extensions_expression = ",".join(
            self._powershell_literal(extension)
            for extension in normalized_extensions
        )
        query_literal = self._powershell_literal(query)

        script = (
            "$ErrorActionPreference='Stop';"
            f"$roots=@({roots_expression});"
            f"$extensions=@({extensions_expression});"
            f"$query={query_literal};"
            "$matches=@("
            "foreach($root in $roots){"
            "Get-ChildItem -LiteralPath $root "
            "-File -Recurse -ErrorAction SilentlyContinue|"
            "Where-Object{"
            "($query.Length -eq 0 -or "
            "$_.Name.IndexOf("
            "$query,"
            "[System.StringComparison]::OrdinalIgnoreCase"
            ") -ge 0)"
            "-and"
            "($extensions.Count -eq 0 -or "
            "$extensions -contains "
            "$_.Extension.ToLowerInvariant())"
            "}"
            "}"
            ");"
            "$selected=@("
            "$matches|"
            "Sort-Object LastWriteTimeUtc -Descending|"
            f"Select-Object -First {limit}"
            ");"
            "$result=@("
            "$selected|ForEach-Object{"
            "[ordered]@{"
            "path=$_.FullName;"
            "name=$_.Name;"
            "size=[int64]$_.Length;"
            "modified_at=$_.LastWriteTimeUtc.ToString('o')"
            "}"
            "}"
            ");"
            "[Console]::Out.Write("
            "(ConvertTo-Json "
            "-InputObject $result -Compress)"
            ")"
        )
        encoded_script = base64.b64encode(
            script.encode("utf-16-le")
        ).decode("ascii")
        command = [
            "ssh",
            "-i",
            str(self.identity_file),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=5",
            "-p",
            str(self.port),
            f"{self.username}@{self.host}",
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded_script,
        ]

        try:
            completed = self._run_command(
                command,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_seconds,
                check=False,
            )
        except (
            OSError,
            subprocess.SubprocessError,
        ) as exc:
            raise WindowsFileSearchError(
                f"Windows file search failed: {exc}"
            ) from exc

        if completed.returncode != 0:
            detail = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or (
                    "SSH exited with code "
                    f"{completed.returncode}"
                )
            )
            raise WindowsFileSearchError(
                f"Windows file search failed: {detail}"
            )

        try:
            payload = json.loads(
                completed.stdout.strip() or "[]"
            )
        except json.JSONDecodeError as exc:
            raise WindowsFileSearchError(
                "Windows file search returned invalid JSON."
            ) from exc

        if isinstance(payload, dict):
            payload = [payload]

        if not isinstance(payload, list):
            raise WindowsFileSearchError(
                "Windows file search result must be a list."
            )

        matches: list[WindowsFileMatch] = []

        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise WindowsFileSearchError(
                    f"Search result {index} is invalid."
                )

            try:
                path = self._validate_result_path(
                    item["path"]
                )
                name = str(item["name"])
                size = int(item["size"])
                modified_at = str(item["modified_at"])
            except (
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                raise WindowsFileSearchError(
                    f"Search result {index} is invalid."
                ) from exc

            if size < 0:
                raise WindowsFileSearchError(
                    f"Search result {index} has invalid size."
                )

            matches.append(
                WindowsFileMatch(
                    path=str(path),
                    name=name,
                    size=size,
                    modified_at=modified_at,
                )
            )

        return matches

    @staticmethod
    def _powershell_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _extensions(
        extensions: Iterable[str] | None,
    ) -> tuple[str, ...]:
        if extensions is None:
            return ()

        normalized: list[str] = []

        for extension in extensions:
            value = str(extension).strip().lower()

            if not value.startswith("."):
                value = "." + value

            if not EXTENSION_PATTERN.fullmatch(value):
                raise ValueError(
                    f"Invalid file extension: {extension}"
                )

            if value not in normalized:
                normalized.append(value)

        return tuple(normalized)

    def _validate_result_path(
        self,
        raw_path: Any,
    ) -> PureWindowsPath:
        path = PureWindowsPath(str(raw_path))

        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(
                "Search result path is not absolute"
            )

        candidate_parts = tuple(
            part.casefold()
            for part in path.parts
        )

        for root in self.approved_remote_roots:
            root_parts = tuple(
                part.casefold()
                for part in root.parts
            )

            if (
                len(candidate_parts) >= len(root_parts)
                and candidate_parts[:len(root_parts)]
                == root_parts
            ):
                return path

        raise ValueError(
            "Search result path is outside approved roots"
        )
