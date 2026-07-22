from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from time import monotonic
from typing import Any
from uuid import uuid4


CommandRunner = Callable[
    ...,
    subprocess.CompletedProcess[str],
]


class FileTransferError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RemoteFileInfo:
    path: str
    name: str
    size: int
    sha256: str
    modified_at: str


@dataclass(frozen=True, slots=True)
class FileTransferResult:
    ok: bool
    verified: bool
    remote_path: str
    local_path: str | None
    bytes_transferred: int
    remote_sha256: str | None
    local_sha256: str | None
    reused_existing: bool
    error: str | None
    duration_ms: float


class SFTPClient:
    def __init__(
        self,
        *,
        host: str,
        username: str,
        identity_file: str | Path,
        staging_directory: str | Path,
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
            raise ValueError("port must be between 1 and 65535")

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
        self.staging_directory = Path(staging_directory)
        self.approved_remote_roots = roots
        self.port = port
        self.command_timeout_seconds = (
            command_timeout_seconds
        )
        self._run_command = command_runner

    def remote_file_info(
        self,
        remote_path: str | PureWindowsPath,
    ) -> RemoteFileInfo:
        path = self._validate_remote_path(remote_path)
        escaped_path = str(path).replace("'", "''")

        script = (
            "$ErrorActionPreference='Stop';"
            f"$p='{escaped_path}';"
            "$f=Get-Item -LiteralPath $p -Force;"
            "if($f.PSIsContainer){"
            "throw 'Path is a directory.'"
            "};"
            "$h=(Get-FileHash -LiteralPath $p "
            "-Algorithm SHA256).Hash.ToLowerInvariant();"
            "$o=[ordered]@{"
            "path=$f.FullName;"
            "name=$f.Name;"
            "size=[int64]$f.Length;"
            "sha256=$h;"
            "modified_at=$f.LastWriteTimeUtc.ToString('o')"
            "};"
            "[Console]::Out.Write("
            "($o|ConvertTo-Json -Compress)"
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
            raise FileTransferError(
                f"Could not inspect remote file: {exc}"
            ) from exc

        if completed.returncode != 0:
            detail = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or f"SSH exited with code {completed.returncode}"
            )
            raise FileTransferError(
                f"Could not inspect remote file: {detail}"
            )

        try:
            payload = json.loads(completed.stdout.strip())
            remote_info_path = self._validate_remote_path(
                payload["path"]
            )
            name = str(payload["name"])
            size = int(payload["size"])
            sha256 = str(payload["sha256"]).lower()
            modified_at = str(payload["modified_at"])
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise FileTransferError(
                "Remote file information was invalid."
            ) from exc

        if size < 0:
            raise FileTransferError(
                "Remote file reported a negative size."
            )

        if (
            len(sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in sha256
            )
        ):
            raise FileTransferError(
                "Remote file reported an invalid SHA-256."
            )

        return RemoteFileInfo(
            path=str(remote_info_path),
            name=name,
            size=size,
            sha256=sha256,
            modified_at=modified_at,
        )

    def download(
        self,
        remote_path: str | PureWindowsPath,
        *,
        local_name: str | None = None,
    ) -> FileTransferResult:
        started_clock = monotonic()
        requested_path = str(remote_path)
        temporary_path: Path | None = None

        try:
            info = self.remote_file_info(remote_path)
            destination_name = (
                local_name
                if local_name is not None
                else info.name
            )
            self._validate_local_name(destination_name)

            self.staging_directory.mkdir(
                mode=0o700,
                parents=True,
                exist_ok=True,
            )
            destination = (
                self.staging_directory / destination_name
            )

            if destination.exists():
                if destination.is_file():
                    existing_size = destination.stat().st_size
                    existing_hash = self._sha256(destination)

                    if (
                        existing_size == info.size
                        and existing_hash == info.sha256
                    ):
                        return self._transfer_result(
                            started_clock,
                            ok=True,
                            verified=True,
                            remote_path=info.path,
                            local_path=str(destination),
                            bytes_transferred=existing_size,
                            remote_sha256=info.sha256,
                            local_sha256=existing_hash,
                            reused_existing=True,
                        )

                return self._transfer_result(
                    started_clock,
                    ok=False,
                    verified=False,
                    remote_path=info.path,
                    local_path=str(destination),
                    bytes_transferred=0,
                    remote_sha256=info.sha256,
                    local_sha256=None,
                    error=(
                        "Destination already exists with "
                        "different contents."
                    ),
                )

            temporary_path = self.staging_directory / (
                f".{destination_name}.{uuid4().hex}.part"
            )
            remote_sftp_path = "/" + PureWindowsPath(
                info.path
            ).as_posix()
            batch = (
                f'get "{remote_sftp_path}" '
                f'"{temporary_path.as_posix()}"\n'
            )
            command = [
                "sftp",
                "-q",
                "-i",
                str(self.identity_file),
                "-P",
                str(self.port),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-b",
                "-",
                f"{self.username}@{self.host}",
            ]

            completed = self._run_command(
                command,
                input=batch,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_seconds,
                check=False,
            )

            if completed.returncode != 0:
                detail = (
                    completed.stderr.strip()
                    or completed.stdout.strip()
                    or (
                        "SFTP exited with code "
                        f"{completed.returncode}"
                    )
                )
                raise FileTransferError(
                    f"SFTP download failed: {detail}"
                )

            if not temporary_path.is_file():
                raise FileTransferError(
                    "SFTP reported success but no file arrived."
                )

            local_size = temporary_path.stat().st_size
            local_hash = self._sha256(temporary_path)

            if (
                local_size != info.size
                or local_hash != info.sha256
            ):
                return self._transfer_result(
                    started_clock,
                    ok=False,
                    verified=False,
                    remote_path=info.path,
                    local_path=None,
                    bytes_transferred=local_size,
                    remote_sha256=info.sha256,
                    local_sha256=local_hash,
                    error=(
                        "Transferred file failed size or "
                        "SHA-256 verification."
                    ),
                )

            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, destination)
            temporary_path = None

            return self._transfer_result(
                started_clock,
                ok=True,
                verified=True,
                remote_path=info.path,
                local_path=str(destination),
                bytes_transferred=local_size,
                remote_sha256=info.sha256,
                local_sha256=local_hash,
                reused_existing=False,
            )
        except (
            FileTransferError,
            OSError,
            subprocess.SubprocessError,
        ) as exc:
            return self._transfer_result(
                started_clock,
                ok=False,
                verified=False,
                remote_path=requested_path,
                local_path=None,
                bytes_transferred=0,
                remote_sha256=None,
                local_sha256=None,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def upload(
        self,
        local_path: str | Path,
        remote_path: str | PureWindowsPath,
    ) -> FileTransferResult:
        """Uploads a local Pi file to an approved Windows path via sftp
        put, verifying SHA-256 on both sides before confirming success.
        Mirrors download() in the opposite direction."""
        started_clock = monotonic()
        requested_remote = str(remote_path)
        source = Path(local_path)

        try:
            if not source.is_file():
                raise FileTransferError(
                    "Local file does not exist."
                )

            destination = self._validate_remote_path(
                remote_path
            )
            local_size = source.stat().st_size
            local_hash = self._sha256(source)

            remote_sftp_path = "/" + PureWindowsPath(
                destination
            ).as_posix()
            batch = (
                f'put "{source}" '
                f'"{remote_sftp_path}"\n'
            )
            command = [
                "sftp",
                "-q",
                "-i",
                str(self.identity_file),
                "-P",
                str(self.port),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-b",
                "-",
                f"{self.username}@{self.host}",
            ]

            completed = self._run_command(
                command,
                input=batch,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_seconds,
                check=False,
            )

            if completed.returncode != 0:
                detail = (
                    completed.stderr.strip()
                    or completed.stdout.strip()
                    or (
                        "SFTP exited with code "
                        f"{completed.returncode}"
                    )
                )
                raise FileTransferError(
                    f"SFTP upload failed: {detail}"
                )

            info = self.remote_file_info(destination)

            if (
                info.size != local_size
                or info.sha256 != local_hash
            ):
                return self._transfer_result(
                    started_clock,
                    ok=False,
                    verified=False,
                    remote_path=str(destination),
                    local_path=str(source),
                    bytes_transferred=info.size,
                    remote_sha256=info.sha256,
                    local_sha256=local_hash,
                    error=(
                        "Uploaded file failed size or "
                        "SHA-256 verification."
                    ),
                )

            return self._transfer_result(
                started_clock,
                ok=True,
                verified=True,
                remote_path=str(destination),
                local_path=str(source),
                bytes_transferred=local_size,
                remote_sha256=info.sha256,
                local_sha256=local_hash,
            )
        except (
            FileTransferError,
            ValueError,
            OSError,
            subprocess.SubprocessError,
        ) as exc:
            return self._transfer_result(
                started_clock,
                ok=False,
                verified=False,
                remote_path=requested_remote,
                local_path=str(source),
                bytes_transferred=0,
                remote_sha256=None,
                local_sha256=None,
                error=f"{type(exc).__name__}: {exc}",
            )

    def make_directory(
        self,
        remote_path: str | PureWindowsPath,
    ) -> str:
        """Create an approved Windows directory, including its parents."""
        path = self._validate_remote_path(remote_path)
        escaped_path = str(path).replace("'", "''")
        script = (
            "$ErrorActionPreference='Stop';"
            f"$p='{escaped_path}';"
            "New-Item -ItemType Directory -Path $p -Force | Out-Null;"
            "$f=Get-Item -LiteralPath $p -Force;"
            "if(-not $f.PSIsContainer){throw 'Path is not a directory.'};"
            "[Console]::Out.Write($f.FullName)"
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
        except (OSError, subprocess.SubprocessError) as exc:
            raise FileTransferError(
                f"Could not create remote directory: {exc}"
            ) from exc
        if completed.returncode != 0:
            detail = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or f"SSH exited with code {completed.returncode}"
            )
            raise FileTransferError(
                f"Could not create remote directory: {detail}"
            )
        return str(path)

    def _validate_remote_path(
        self,
        remote_path: str | PureWindowsPath,
    ) -> PureWindowsPath:
        raw_path = str(remote_path)

        if any(
            character in raw_path
            for character in ('\x00', '\r', '\n', '"')
        ):
            raise ValueError(
                "Remote path contains invalid characters"
            )

        path = PureWindowsPath(raw_path)

        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(
                "Remote path must be absolute without traversal"
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
            "Remote path is outside approved roots"
        )

    @staticmethod
    def _validate_local_name(name: str) -> None:
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or "\x00" in name
            or "\r" in name
            or "\n" in name
        ):
            raise ValueError("Invalid local file name")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()

        with path.open("rb") as handle:
            for chunk in iter(
                lambda: handle.read(1024 * 1024),
                b"",
            ):
                digest.update(chunk)

        return digest.hexdigest()

    @staticmethod
    def _transfer_result(
        started_clock: float,
        *,
        ok: bool,
        verified: bool,
        remote_path: str,
        local_path: str | None,
        bytes_transferred: int,
        remote_sha256: str | None,
        local_sha256: str | None,
        reused_existing: bool = False,
        error: str | None = None,
    ) -> FileTransferResult:
        return FileTransferResult(
            ok=ok,
            verified=verified,
            remote_path=remote_path,
            local_path=local_path,
            bytes_transferred=bytes_transferred,
            remote_sha256=remote_sha256,
            local_sha256=local_sha256,
            reused_existing=reused_existing,
            error=error,
            duration_ms=round(
                (monotonic() - started_clock) * 1000,
                3,
            ),
        )
