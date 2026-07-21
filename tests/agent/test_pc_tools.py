from atlas_agent.executor import ToolExecutor
from atlas_agent.pc_client import (
    PCActionResult,
    PCConnectionResult,
)
from atlas_agent.pc_tools import register_pc_tools
from atlas_agent.results import ResultStatus
from atlas_agent.sftp_client import FileTransferResult
from atlas_agent.tasks import ToolCall
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.verifier import (
    ResultVerifier,
    VerificationStatus,
)
from atlas_agent.windows_file_search import (
    WindowsFileMatch,
)


class FakePCClient:
    def __init__(self) -> None:
        self.ensure_arguments = None
        self.action_arguments = None
        self.connection_result = PCConnectionResult(
            configured=True,
            online=True,
            wake_attempted=False,
            attempts=1,
            message="PC is online.",
            wake_response=None,
            error=None,
            duration_ms=1,
        )
        self.action_result = PCActionResult(
            action="active_apps",
            ok=True,
            data={
                "ok": True,
                "windows": ["Fusion 360"],
            },
            error=None,
            started_at="start",
            finished_at="finish",
            duration_ms=1,
        )

    def ensure_online(self, **kwargs):
        self.ensure_arguments = kwargs
        return self.connection_result

    def execute(self, action, arguments=None):
        self.action_arguments = (
            action,
            arguments,
        )
        return self.action_result


class FakeFileSearch:
    def __init__(self) -> None:
        self.arguments = None
        self.matches = [
            WindowsFileMatch(
                path=(
                    r"C:\Users\wesle\Documents"
                    r"\ATLAS.f3d"
                ),
                name="ATLAS.f3d",
                size=1200,
                modified_at="2026-07-20T04:00:00Z",
            )
        ]

    def search(self, query, **kwargs):
        self.arguments = (query, kwargs)
        return self.matches


class FakeSFTPClient:
    def __init__(self) -> None:
        self.arguments = None
        self.transfer_result = FileTransferResult(
            ok=True,
            verified=True,
            remote_path=(
                r"C:\Users\wesle\Documents"
                r"\ATLAS.f3d"
            ),
            local_path=(
                "/home/atlas/atlas-staging/"
                "incoming/ATLAS.f3d"
            ),
            bytes_transferred=1200,
            remote_sha256="a" * 64,
            local_sha256="a" * 64,
            reused_existing=False,
            error=None,
            duration_ms=5,
        )

    def download(self, remote_path, **kwargs):
        self.arguments = (remote_path, kwargs)
        return self.transfer_result


def build_tools():
    registry = ToolRegistry()
    verifier = ResultVerifier()
    pc_client = FakePCClient()
    file_search = FakeFileSearch()
    sftp_client = FakeSFTPClient()

    tools = register_pc_tools(
        registry,
        verifier,
        pc_client=pc_client,
        file_search=file_search,
        sftp_client=sftp_client,
    )

    return (
        registry,
        verifier,
        pc_client,
        file_search,
        sftp_client,
        tools,
    )


def execute(
    registry: ToolRegistry,
    call: ToolCall,
):
    with ToolExecutor(registry) as executor:
        return executor.execute(call)


def test_real_pc_tools_are_registered() -> None:
    (
        registry,
        _verifier,
        _pc_client,
        _file_search,
        _sftp_client,
        tools,
    ) = build_tools()

    assert len(tools) == 12
    assert [
        tool.name
        for tool in registry.list_tools()
    ] == [
        "pc.active_apps",
        "pc.active_window",
        "pc.capture_screenshot",
        "pc.capture_window",
        "pc.download_file",
        "pc.ensure_online",
        "pc.focus_or_open_app",
        "pc.list_recordings",
        "pc.open_app",
        "pc.search_files",
        "pc.start_screen_recording",
        "pc.stop_screen_recording",
    ]
    assert registry.get(
        "pc.ensure_online"
    ).runs_on == "pi"
    assert registry.get(
        "pc.search_files"
    ).runs_on == "pc"
    assert registry.get(
        "pc.download_file"
    ).permission_level == 0
    assert (
        registry.get(
            "pc.search_files"
        ).metadata["parameters"]["required"]
        == ["query", "extensions", "limit"]
    )


def test_ensure_online_executes_and_verifies() -> None:
    (
        registry,
        verifier,
        pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    call = ToolCall(
        tool_name="pc.ensure_online",
        arguments={
            "wake_if_needed": True,
        },
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["online"] is True
    assert pc_client.ensure_arguments == {
        "wake_if_needed": True,
        "timeout_seconds": 90,
        "poll_interval_seconds": 5,
    }
    assert (
        verification.status
        is VerificationStatus.VERIFIED
    )


def test_file_search_executes_and_verifies() -> None:
    (
        registry,
        verifier,
        _pc_client,
        file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    call = ToolCall(
        tool_name="pc.search_files",
        arguments={
            "query": "atlas",
            "extensions": ["f3d", "stl"],
            "limit": 10,
        },
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert result.output[0]["name"] == "ATLAS.f3d"
    assert file_search.arguments == (
        "atlas",
        {
            "extensions": ["f3d", "stl"],
            "limit": 10,
        },
    )
    assert verification.verified is True
    assert (
        verification.evidence["match_count"]
        == 1
    )


def test_verified_download_executes_and_verifies() -> None:
    (
        registry,
        verifier,
        _pc_client,
        _file_search,
        sftp_client,
        _tools,
    ) = build_tools()
    remote_path = (
        r"C:\Users\wesle\Documents\ATLAS.f3d"
    )
    call = ToolCall(
        tool_name="pc.download_file",
        arguments={
            "remote_path": remote_path,
            "local_name": None,
        },
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["verified"] is True
    assert sftp_client.arguments == (
        remote_path,
        {"local_name": None},
    )
    assert verification.verified is True
    assert (
        verification.evidence["verified"]
        is True
    )


def test_active_apps_and_open_app_use_companion() -> None:
    (
        registry,
        verifier,
        pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()

    active_call = ToolCall(
        tool_name="pc.active_apps"
    )
    active_result = execute(
        registry,
        active_call,
    )
    active_verification = verifier.verify(
        active_call,
        active_result,
    )

    assert active_verification.verified is True

    pc_client.action_result = PCActionResult(
        action="open_app",
        ok=True,
        data={
            "ok": True,
            "opened": "Fusion 360",
        },
        error=None,
        started_at="start",
        finished_at="finish",
        duration_ms=1,
    )
    open_call = ToolCall(
        tool_name="pc.open_app",
        arguments={
            "app": "fusion",
        },
    )
    open_result = execute(registry, open_call)
    open_verification = verifier.verify(
        open_call,
        open_result,
    )

    assert pc_client.action_arguments == (
        "open_app",
        {"app": "fusion"},
    )
    assert open_result.output["ok"] is True
    assert open_verification.verified is True


def test_unverified_transfer_is_not_accepted() -> None:
    (
        registry,
        verifier,
        _pc_client,
        _file_search,
        sftp_client,
        _tools,
    ) = build_tools()
    sftp_client.transfer_result = FileTransferResult(
        ok=False,
        verified=False,
        remote_path="remote",
        local_path=None,
        bytes_transferred=0,
        remote_sha256=None,
        local_sha256=None,
        reused_existing=False,
        error="Hash mismatch.",
        duration_ms=1,
    )
    call = ToolCall(
        tool_name="pc.download_file",
        arguments={
            "remote_path": "remote",
            "local_name": None,
        },
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert verification.verified is False
    assert (
        verification.status
        is VerificationStatus.FAILED
    )


def test_focus_or_open_app_launched_executes_and_verifies() -> None:
    (
        registry,
        verifier,
        pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    pc_client.action_result = PCActionResult(
        action="focus_or_open_app",
        ok=True,
        data={
            "ok": True,
            "app": "spotify",
            "action": "launched",
        },
        error=None,
        started_at="start",
        finished_at="finish",
        duration_ms=1,
    )
    call = ToolCall(
        tool_name="pc.focus_or_open_app",
        arguments={"app": "spotify"},
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert pc_client.action_arguments == (
        "focus_or_open_app",
        {"app": "spotify"},
    )
    assert result.output["data"]["action"] == "launched"
    assert verification.status is VerificationStatus.VERIFIED


def test_focus_or_open_app_focused_verifies() -> None:
    (
        registry,
        verifier,
        pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    pc_client.action_result = PCActionResult(
        action="focus_or_open_app",
        ok=True,
        data={
            "ok": True,
            "app": "claude",
            "action": "focused",
        },
        error=None,
        started_at="start",
        finished_at="finish",
        duration_ms=1,
    )
    call = ToolCall(
        tool_name="pc.focus_or_open_app",
        arguments={"app": "claude"},
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert verification.status is VerificationStatus.VERIFIED


def test_focus_or_open_app_companion_error_fails_verification() -> None:
    (
        registry,
        verifier,
        pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    pc_client.action_result = PCActionResult(
        action="focus_or_open_app",
        ok=True,
        data={
            "ok": False,
            "error": "app 'chrome_dev' not in approved_apps",
        },
        error=None,
        started_at="start",
        finished_at="finish",
        duration_ms=1,
    )
    call = ToolCall(
        tool_name="pc.focus_or_open_app",
        arguments={"app": "chrome_dev"},
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert verification.verified is False


def test_focus_or_open_app_rejects_empty_app() -> None:
    (
        registry,
        _verifier,
        _pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    call = ToolCall(
        tool_name="pc.focus_or_open_app",
        arguments={"app": "  "},
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR


def test_active_window_executes_and_verifies() -> None:
    (
        registry,
        verifier,
        pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    pc_client.action_result = PCActionResult(
        action="active_window",
        ok=True,
        data={
            "ok": True,
            "title": "Fusion 360 - ATLAS.f3d",
        },
        error=None,
        started_at="start",
        finished_at="finish",
        duration_ms=1,
    )
    call = ToolCall(tool_name="pc.active_window")

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert (
        result.output["data"]["title"]
        == "Fusion 360 - ATLAS.f3d"
    )
    assert verification.status is VerificationStatus.VERIFIED


def test_active_window_null_title_still_verifies() -> None:
    (
        registry,
        verifier,
        pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    pc_client.action_result = PCActionResult(
        action="active_window",
        ok=True,
        data={"ok": True, "title": None},
        error=None,
        started_at="start",
        finished_at="finish",
        duration_ms=1,
    )
    call = ToolCall(tool_name="pc.active_window")

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert verification.status is VerificationStatus.VERIFIED


def test_invalid_tool_arguments_become_error_result() -> None:
    (
        registry,
        _verifier,
        _pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    call = ToolCall(
        tool_name="pc.ensure_online",
        arguments={
            "wake_if_needed": "yes",
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR
    assert (
        result.error
        == "ValueError: wake_if_needed must be a boolean"
    )


def test_capture_screenshot_executes_and_verifies() -> None:
    (
        registry,
        verifier,
        pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    pc_client.action_result = PCActionResult(
        action="capture_screenshot",
        ok=True,
        data={
            "ok": True,
            "path": r"C:\Videos\AtlasRecordings\shot.png",
            "name": "shot.png",
        },
        error=None,
        started_at="start",
        finished_at="finish",
        duration_ms=1,
    )
    call = ToolCall(
        tool_name="pc.capture_screenshot",
        arguments={"mission": "showcase"},
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert pc_client.action_arguments == (
        "capture_screenshot",
        {"mission": "showcase"},
    )
    assert verification.status is VerificationStatus.VERIFIED


def test_capture_window_requires_non_empty_title() -> None:
    (
        registry,
        _verifier,
        _pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    call = ToolCall(
        tool_name="pc.capture_window",
        arguments={"window_title": "  ", "mission": None},
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR


def test_start_screen_recording_executes_and_verifies() -> None:
    (
        registry,
        verifier,
        pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    pc_client.action_result = PCActionResult(
        action="start_recording",
        ok=True,
        data={"ok": True, "pid": 4242, "path": r"C:\rec.mp4"},
        error=None,
        started_at="start",
        finished_at="finish",
        duration_ms=1,
    )
    call = ToolCall(
        tool_name="pc.start_screen_recording",
        arguments={
            "target": "full",
            "window_title": None,
            "mission": "showcase",
            "privacy": False,
            "max_seconds": 30,
        },
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert pc_client.action_arguments == (
        "start_recording",
        {
            "target": "full",
            "window_title": None,
            "mission": "showcase",
            "privacy": False,
            "max_seconds": 30,
        },
    )
    assert verification.status is VerificationStatus.VERIFIED


def test_start_screen_recording_rejects_window_target_without_title() -> None:
    (
        registry,
        _verifier,
        _pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    call = ToolCall(
        tool_name="pc.start_screen_recording",
        arguments={
            "target": "window",
            "window_title": None,
            "mission": None,
            "privacy": False,
            "max_seconds": None,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR


def test_stop_screen_recording_executes_and_verifies() -> None:
    (
        registry,
        verifier,
        pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    pc_client.action_result = PCActionResult(
        action="stop_recording",
        ok=True,
        data={"ok": True, "size_bytes": 1024, "path": r"C:\rec.mp4"},
        error=None,
        started_at="start",
        finished_at="finish",
        duration_ms=1,
    )
    call = ToolCall(tool_name="pc.stop_screen_recording")

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert verification.status is VerificationStatus.VERIFIED


def test_stop_screen_recording_unverified_without_recording() -> None:
    (
        registry,
        verifier,
        pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    pc_client.action_result = PCActionResult(
        action="stop_recording",
        ok=False,
        data=None,
        error="no recording is in progress",
        started_at="start",
        finished_at="finish",
        duration_ms=1,
    )
    call = ToolCall(tool_name="pc.stop_screen_recording")

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert verification.verified is False


def test_list_recordings_executes_and_verifies() -> None:
    (
        registry,
        verifier,
        pc_client,
        _file_search,
        _sftp_client,
        _tools,
    ) = build_tools()
    pc_client.action_result = PCActionResult(
        action="list_recordings",
        ok=True,
        data={"ok": True, "recordings": [{"name": "shot.png"}]},
        error=None,
        started_at="start",
        finished_at="finish",
        duration_ms=1,
    )
    call = ToolCall(tool_name="pc.list_recordings")

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert verification.status is VerificationStatus.VERIFIED
