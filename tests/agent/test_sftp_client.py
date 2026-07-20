import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from atlas_agent.sftp_client import (
    FileTransferError,
    SFTPClient,
)


REMOTE_PATH = (
    r"C:\Users\wesle\Documents\Atlas\project.f3d"
)
REMOTE_ROOT = r"C:\Users\wesle\Documents"


def remote_info(
    payload: bytes,
    *,
    path: str = REMOTE_PATH,
) -> dict[str, Any]:
    return {
        "path": path,
        "name": Path(path.replace("\\", "/")).name,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "modified_at": "2026-07-20T04:00:00.0000000Z",
    }


class FakeRunner:
    def __init__(
        self,
        *,
        info: dict[str, Any],
        download_bytes: bytes,
        ssh_stdout: str | None = None,
        ssh_returncode: int = 0,
        sftp_returncode: int = 0,
    ) -> None:
        self.info = info
        self.download_bytes = download_bytes
        self.ssh_stdout = ssh_stdout
        self.ssh_returncode = ssh_returncode
        self.sftp_returncode = sftp_returncode
        self.calls: list[
            tuple[list[str], dict[str, Any]]
        ] = []

    def __call__(
        self,
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))

        if command[0] == "ssh":
            stdout = (
                self.ssh_stdout
                if self.ssh_stdout is not None
                else json.dumps(self.info)
            )
            return subprocess.CompletedProcess(
                command,
                self.ssh_returncode,
                stdout=stdout,
                stderr=(
                    "ssh failed"
                    if self.ssh_returncode
                    else ""
                ),
            )

        if command[0] == "sftp":
            if self.sftp_returncode == 0:
                batch = kwargs["input"]
                match = re.search(
                    r'get ".*" "([^"]+)"',
                    batch,
                )
                assert match is not None
                Path(match.group(1)).write_bytes(
                    self.download_bytes
                )

            return subprocess.CompletedProcess(
                command,
                self.sftp_returncode,
                stdout="",
                stderr=(
                    "sftp failed"
                    if self.sftp_returncode
                    else ""
                ),
            )

        raise AssertionError(
            f"Unexpected command: {command}"
        )


def make_client(
    tmp_path,
    runner: FakeRunner,
) -> SFTPClient:
    identity = tmp_path / "atlas_pc_ed25519"
    identity.write_text(
        "test private key",
        encoding="utf-8",
    )

    return SFTPClient(
        host="192.168.50.2",
        username="wesle",
        identity_file=identity,
        staging_directory=tmp_path / "staging",
        approved_remote_roots=[REMOTE_ROOT],
        command_runner=runner,
    )


def test_remote_file_info_is_parsed_and_validated(
    tmp_path,
) -> None:
    payload = b"atlas project data"
    runner = FakeRunner(
        info=remote_info(payload),
        download_bytes=payload,
    )
    client = make_client(tmp_path, runner)

    info = client.remote_file_info(REMOTE_PATH)

    assert info.path == REMOTE_PATH
    assert info.size == len(payload)
    assert info.sha256 == hashlib.sha256(
        payload
    ).hexdigest()
    assert runner.calls[0][0][0] == "ssh"
    assert "-EncodedCommand" in runner.calls[0][0]


def test_download_is_verified_and_atomically_promoted(
    tmp_path,
) -> None:
    payload = b"verified atlas file"
    runner = FakeRunner(
        info=remote_info(payload),
        download_bytes=payload,
    )
    client = make_client(tmp_path, runner)

    result = client.download(REMOTE_PATH)

    destination = Path(result.local_path)

    assert result.ok is True
    assert result.verified is True
    assert result.reused_existing is False
    assert destination.read_bytes() == payload
    assert stat.S_IMODE(
        destination.stat().st_mode
    ) == 0o600
    assert result.local_sha256 == result.remote_sha256
    assert list(
        destination.parent.glob("*.part")
    ) == []


def test_matching_existing_file_is_reused(
    tmp_path,
) -> None:
    payload = b"existing verified file"
    runner = FakeRunner(
        info=remote_info(payload),
        download_bytes=payload,
    )
    client = make_client(tmp_path, runner)
    destination = (
        client.staging_directory / "project.f3d"
    )
    destination.parent.mkdir(parents=True)
    destination.write_bytes(payload)

    result = client.download(REMOTE_PATH)

    assert result.ok is True
    assert result.verified is True
    assert result.reused_existing is True
    assert [
        call
        for call, _kwargs in runner.calls
        if call[0] == "sftp"
    ] == []


def test_different_existing_file_is_not_overwritten(
    tmp_path,
) -> None:
    payload = b"new remote file"
    runner = FakeRunner(
        info=remote_info(payload),
        download_bytes=payload,
    )
    client = make_client(tmp_path, runner)
    destination = (
        client.staging_directory / "project.f3d"
    )
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"keep this file")

    result = client.download(REMOTE_PATH)

    assert result.ok is False
    assert result.verified is False
    assert destination.read_bytes() == b"keep this file"
    assert "different contents" in result.error


def test_hash_mismatch_removes_partial_file(
    tmp_path,
) -> None:
    expected = b"expected remote content"
    runner = FakeRunner(
        info=remote_info(expected),
        download_bytes=b"corrupted transfer",
    )
    client = make_client(tmp_path, runner)

    result = client.download(REMOTE_PATH)

    assert result.ok is False
    assert result.verified is False
    assert "failed size or SHA-256" in result.error
    assert not (
        client.staging_directory / "project.f3d"
    ).exists()
    assert list(
        client.staging_directory.glob("*.part")
    ) == []


def test_sftp_failure_returns_error_and_cleans_up(
    tmp_path,
) -> None:
    payload = b"remote content"
    runner = FakeRunner(
        info=remote_info(payload),
        download_bytes=payload,
        sftp_returncode=1,
    )
    client = make_client(tmp_path, runner)

    result = client.download(REMOTE_PATH)

    assert result.ok is False
    assert result.verified is False
    assert "SFTP download failed" in result.error
    assert list(
        client.staging_directory.glob("*.part")
    ) == []


def test_unapproved_and_unsafe_paths_are_rejected(
    tmp_path,
) -> None:
    payload = b"data"
    runner = FakeRunner(
        info=remote_info(payload),
        download_bytes=payload,
    )
    client = make_client(tmp_path, runner)

    with pytest.raises(
        ValueError,
        match="outside approved roots",
    ):
        client.remote_file_info(
            r"C:\Windows\System32\config\SAM"
        )

    with pytest.raises(
        ValueError,
        match="without traversal",
    ):
        client.remote_file_info(
            r"C:\Users\wesle\Documents\..\secret.txt"
        )

    with pytest.raises(
        ValueError,
        match="invalid characters",
    ):
        client.remote_file_info(
            'C:\\Users\\wesle\\Documents\\"bad.txt'
        )

    assert runner.calls == []


def test_invalid_remote_response_is_rejected(
    tmp_path,
) -> None:
    payload = b"data"
    runner = FakeRunner(
        info=remote_info(payload),
        download_bytes=payload,
        ssh_stdout="not json",
    )
    client = make_client(tmp_path, runner)

    with pytest.raises(
        FileTransferError,
        match="information was invalid",
    ):
        client.remote_file_info(REMOTE_PATH)


def test_invalid_client_configuration_is_rejected(
    tmp_path,
) -> None:
    missing_key = tmp_path / "missing-key"

    with pytest.raises(
        ValueError,
        match="identity file does not exist",
    ):
        SFTPClient(
            host="192.168.50.2",
            username="wesle",
            identity_file=missing_key,
            staging_directory=tmp_path / "staging",
            approved_remote_roots=[REMOTE_ROOT],
        )

    key = tmp_path / "key"
    key.write_text("key", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="At least one approved remote root",
    ):
        SFTPClient(
            host="192.168.50.2",
            username="wesle",
            identity_file=key,
            staging_directory=tmp_path / "staging",
            approved_remote_roots=[],
        )
