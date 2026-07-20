import base64
import json
import subprocess
from typing import Any

import pytest

from atlas_agent.windows_file_search import (
    WindowsFileSearch,
    WindowsFileSearchError,
)


REMOTE_ROOT = r"C:\Users\wesle"
FIRST_PATH = (
    r"C:\Users\wesle\Documents\Atlas\newest.f3d"
)
SECOND_PATH = (
    r"C:\Users\wesle\Downloads\older.stl"
)


class FakeRunner:
    def __init__(
        self,
        *,
        stdout: str,
        returncode: int = 0,
        stderr: str = "",
    ) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[
            tuple[list[str], dict[str, Any]]
        ] = []

    def __call__(
        self,
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))

        return subprocess.CompletedProcess(
            command,
            self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def make_search(
    tmp_path,
    runner: FakeRunner,
) -> WindowsFileSearch:
    identity = tmp_path / "atlas_pc_ed25519"
    identity.write_text(
        "test private key",
        encoding="utf-8",
    )

    return WindowsFileSearch(
        host="192.168.50.2",
        username="wesle",
        identity_file=identity,
        approved_remote_roots=[REMOTE_ROOT],
        command_runner=runner,
    )


def file_payload(
    path: str,
    name: str,
    size: int,
) -> dict[str, Any]:
    return {
        "path": path,
        "name": name,
        "size": size,
        "modified_at": "2026-07-20T04:00:00.0000000Z",
    }


def test_search_returns_structured_file_matches(
    tmp_path,
) -> None:
    runner = FakeRunner(
        stdout=json.dumps(
            [
                file_payload(
                    FIRST_PATH,
                    "newest.f3d",
                    1200,
                ),
                file_payload(
                    SECOND_PATH,
                    "older.stl",
                    800,
                ),
            ]
        )
    )
    search = make_search(tmp_path, runner)

    matches = search.search(
        "atlas",
        extensions=["f3d", ".stl"],
        limit=10,
    )

    assert [match.name for match in matches] == [
        "newest.f3d",
        "older.stl",
    ]
    assert matches[0].path == FIRST_PATH
    assert matches[0].size == 1200

    command = runner.calls[0][0]
    assert command[0] == "ssh"
    assert "-EncodedCommand" in command
    assert "atlas" not in command


def test_query_and_extensions_are_safely_encoded(
    tmp_path,
) -> None:
    runner = FakeRunner(
        stdout=json.dumps(
            file_payload(
                FIRST_PATH,
                "newest.f3d",
                1200,
            )
        )
    )
    search = make_search(tmp_path, runner)

    matches = search.search(
        "Wesley's Atlas",
        extensions=["F3D", ".STL", "f3d"],
    )

    command = runner.calls[0][0]
    encoded_script = command[
        command.index("-EncodedCommand") + 1
    ]
    script = base64.b64decode(
        encoded_script
    ).decode("utf-16-le")

    assert "$query='Wesley''s Atlas';" in script
    assert "$extensions=@('.f3d','.stl');" in script
    assert matches[0].name == "newest.f3d"


def test_empty_search_result_returns_empty_list(
    tmp_path,
) -> None:
    runner = FakeRunner(stdout="")
    search = make_search(tmp_path, runner)

    assert search.search("not found") == []


def test_result_outside_approved_root_is_rejected(
    tmp_path,
) -> None:
    runner = FakeRunner(
        stdout=json.dumps(
            file_payload(
                r"C:\Windows\System32\secret.bin",
                "secret.bin",
                100,
            )
        )
    )
    search = make_search(tmp_path, runner)

    with pytest.raises(
        WindowsFileSearchError,
        match="Search result 0 is invalid",
    ):
        search.search("secret")


def test_invalid_json_is_rejected(
    tmp_path,
) -> None:
    runner = FakeRunner(stdout="not json")
    search = make_search(tmp_path, runner)

    with pytest.raises(
        WindowsFileSearchError,
        match="invalid JSON",
    ):
        search.search("atlas")


def test_ssh_failure_is_reported(
    tmp_path,
) -> None:
    runner = FakeRunner(
        stdout="",
        returncode=1,
        stderr="connection failed",
    )
    search = make_search(tmp_path, runner)

    with pytest.raises(
        WindowsFileSearchError,
        match="connection failed",
    ):
        search.search("atlas")


def test_invalid_search_arguments_are_rejected(
    tmp_path,
) -> None:
    runner = FakeRunner(stdout="[]")
    search = make_search(tmp_path, runner)

    with pytest.raises(
        ValueError,
        match="invalid characters",
    ):
        search.search("atlas\nproject")

    with pytest.raises(
        ValueError,
        match="cannot exceed 200",
    ):
        search.search("a" * 201)

    with pytest.raises(
        ValueError,
        match="limit must be between",
    ):
        search.search("atlas", limit=0)

    with pytest.raises(
        ValueError,
        match="Invalid file extension",
    ):
        search.search(
            "atlas",
            extensions=["*.exe"],
        )

    assert runner.calls == []


def test_invalid_configuration_is_rejected(
    tmp_path,
) -> None:
    with pytest.raises(
        ValueError,
        match="identity file does not exist",
    ):
        WindowsFileSearch(
            host="192.168.50.2",
            username="wesle",
            identity_file=tmp_path / "missing-key",
            approved_remote_roots=[REMOTE_ROOT],
        )
