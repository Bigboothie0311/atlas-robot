from pathlib import Path

from atlas_agent.executor import ToolExecutor
from atlas_agent.pi_tools import register_pi_capture_tools
from atlas_agent.results import ResultStatus
from atlas_agent.sftp_client import FileTransferResult
from atlas_agent.tasks import ToolCall
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.verifier import ResultVerifier, VerificationStatus


class FakeSFTPClient:
    def __init__(self) -> None:
        self.arguments = None
        self.transfer_result = FileTransferResult(
            ok=True,
            verified=True,
            remote_path=r"C:\Users\wesle\Videos\AtlasRecordings\clip.mp4",
            local_path="/staging/clip.mp4",
            bytes_transferred=1200,
            remote_sha256="a" * 64,
            local_sha256="a" * 64,
            reused_existing=False,
            error=None,
            duration_ms=5,
        )

    def upload(self, local_path, remote_path):
        self.arguments = (str(local_path), remote_path)
        return self.transfer_result


def build_tools(tmp_path, *, hud_handler=None):
    registry = ToolRegistry()
    verifier = ResultVerifier()
    sftp_client = FakeSFTPClient()

    tools = register_pi_capture_tools(
        registry,
        verifier,
        sftp_client=sftp_client,
        recordings_remote_root=r"C:\Users\wesle\Videos\AtlasRecordings",
        staging_directory=tmp_path,
        hud_frame_handler=hud_handler,
    )

    return registry, verifier, sftp_client, tools


def execute(registry, call):
    with ToolExecutor(registry) as executor:
        return executor.execute(call)


def test_capture_tools_are_registered(tmp_path):
    """camera.capture_clip is deliberately NOT registered -- confirmed
    live 2026-07-21 that the physical USB camera faces the room, not
    Atlas, and merely hiding it from capabilities.py's system-prompt
    text wasn't enough to stop run_atlas_agent's own planner from still
    picking it. See pi_tools.register_pi_capture_tools's docstring."""
    registry, _verifier, _sftp, tools = build_tools(tmp_path)

    assert len(tools) == 1
    assert {tool.name for tool in registry.list_tools()} == {
        "pi.capture_hud_frame",
    }
    assert registry.get("pi.capture_hud_frame").runs_on == "pi"


def test_capture_hud_frame_uploads_and_deletes_local_copy(tmp_path):
    def fake_hud_handler(out_path):
        out_path.write_bytes(b"png bytes")
        return True

    registry, verifier, sftp_client, _tools = build_tools(
        tmp_path, hud_handler=fake_hud_handler
    )
    call = ToolCall(
        tool_name="pi.capture_hud_frame",
        arguments={"mission": "showcase"},
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["ok"] is True
    assert result.output["local_path"] is None
    assert sftp_client.arguments[1].startswith(
        r"C:\Users\wesle\Videos\AtlasRecordings"
    )
    assert verification.status is VerificationStatus.VERIFIED


def test_capture_hud_frame_reports_failure_without_uploading(tmp_path):
    registry, verifier, sftp_client, _tools = build_tools(
        tmp_path, hud_handler=lambda out_path: False
    )
    call = ToolCall(
        tool_name="pi.capture_hud_frame",
        arguments={"mission": None},
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.output["ok"] is False
    assert sftp_client.arguments is None
    assert verification.verified is False


def test_capture_hud_frame_keeps_local_copy_when_upload_unverified(tmp_path):
    def fake_hud_handler(out_path):
        out_path.write_bytes(b"png bytes")
        return True

    registry, verifier, sftp_client, _tools = build_tools(
        tmp_path, hud_handler=fake_hud_handler
    )
    sftp_client.transfer_result = FileTransferResult(
        ok=False, verified=False, remote_path="remote",
        local_path="/staging/hud_frame.png", bytes_transferred=0,
        remote_sha256=None, local_sha256=None, reused_existing=False,
        error="Hash mismatch.", duration_ms=1,
    )
    call = ToolCall(
        tool_name="pi.capture_hud_frame",
        arguments={"mission": None},
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.output["ok"] is False
    assert result.output["local_path"] == "/staging/hud_frame.png"
    assert verification.verified is False
