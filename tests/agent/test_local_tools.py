from types import SimpleNamespace

from atlas_agent.executor import ToolExecutor
from atlas_agent import local_tools
from atlas_agent.local_tools import (
    register_local_tools,
)
from atlas_agent.results import ResultStatus
from atlas_agent.tasks import ToolCall
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.verifier import (
    ResultVerifier,
    VerificationStatus,
)


def build_tools(tmp_path):
    registry = ToolRegistry()
    verifier = ResultVerifier()
    tools = register_local_tools(
        registry,
        verifier,
        approved_roots=[tmp_path],
    )
    return registry, verifier, tools


def execute(registry, call):
    with ToolExecutor(registry) as executor:
        return executor.execute(call)


def test_pi_directory_tool_lists_names_and_verifies(
    tmp_path,
):
    (tmp_path / "docs").mkdir()
    (tmp_path / "alpha.py").write_text("print('ok')")
    (tmp_path / "zeta.txt").write_text("test")

    registry, verifier, tools = build_tools(
        tmp_path
    )

    assert len(tools) == 7
    assert registry.get(
        "pi.list_directory"
    ).runs_on == "pi"
    assert registry.get(
        "pi.read_text_file"
    ).runs_on == "pi"

    call = ToolCall(
        tool_name="pi.list_directory",
        arguments={
            "path": str(tmp_path),
            "limit": 20,
        },
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert [
        entry["name"]
        for entry in result.output["entries"]
    ] == [
        "docs",
        "alpha.py",
        "zeta.txt",
    ]
    assert result.output["total_count"] == 3
    assert result.output["truncated"] is False
    assert (
        verification.status
        is VerificationStatus.VERIFIED
    )
    assert verification.evidence["entry_count"] == 3


def test_pi_directory_tool_rejects_unapproved_path(
    tmp_path,
):
    approved = tmp_path / "approved"
    approved.mkdir()

    registry, _verifier, _tools = build_tools(
        approved
    )
    call = ToolCall(
        tool_name="pi.list_directory",
        arguments={
            "path": str(tmp_path),
            "limit": 20,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR
    assert "PermissionError" in result.error



def test_pi_text_file_reads_bounded_lines_and_verifies(
    tmp_path,
):
    source = tmp_path / "notes.txt"
    source.write_text(
        "one\ntwo\nthree\nfour\n",
        encoding="utf-8",
    )

    registry, verifier, _tools = build_tools(
        tmp_path
    )
    call = ToolCall(
        tool_name="pi.read_text_file",
        arguments={
            "path": str(source),
            "start_line": 2,
            "max_lines": 2,
            "max_chars": 100,
        },
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["content"] == "two\nthree"
    assert result.output["start_line"] == 2
    assert result.output["end_line"] == 3
    assert result.output["line_count"] == 2
    assert result.output["total_lines"] == 4
    assert result.output["truncated"] is True
    assert (
        verification.status
        is VerificationStatus.VERIFIED
    )


def test_pi_text_file_rejects_sensitive_file(
    tmp_path,
):
    sensitive = tmp_path / ".env"
    sensitive.write_text(
        "TOKEN=test-only",
        encoding="utf-8",
    )

    registry, _verifier, _tools = build_tools(
        tmp_path
    )
    call = ToolCall(
        tool_name="pi.read_text_file",
        arguments={
            "path": str(sensitive),
            "start_line": 1,
            "max_lines": 20,
            "max_chars": 1000,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR
    assert "PermissionError" in result.error


def test_pi_text_file_rejects_binary_file(
    tmp_path,
):
    binary = tmp_path / "image.bin"
    binary.write_bytes(b"test\x00binary")

    registry, _verifier, _tools = build_tools(
        tmp_path
    )
    call = ToolCall(
        tool_name="pi.read_text_file",
        arguments={
            "path": str(binary),
            "start_line": 1,
            "max_lines": 20,
            "max_chars": 1000,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR
    assert "not text" in result.error


def test_pi_search_files_finds_matching_names_and_verifies(
    tmp_path,
):
    (tmp_path / "atlas_agent").mkdir()
    (tmp_path / "atlas_agent" / "openai_planner.py").write_text(
        "planner"
    )
    (tmp_path / "atlas_agent" / "voice_controller.py").write_text(
        "voice"
    )
    (tmp_path / "robot_hub.py").write_text("hub")

    registry, verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.search_files",
        arguments={
            "root": str(tmp_path),
            "query": "planner",
            "extensions": None,
            "limit": 50,
        },
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["count"] == 1
    assert result.output["entries"][0]["name"] == (
        "openai_planner.py"
    )
    assert result.output["entries"][0]["relative_path"] == (
        "atlas_agent/openai_planner.py"
    )
    assert result.output["truncated"] is False
    assert (
        verification.status
        is VerificationStatus.VERIFIED
    )


def test_pi_search_files_filters_by_extension(tmp_path):
    (tmp_path / "notes.py").write_text("code")
    (tmp_path / "notes.txt").write_text("text")

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.search_files",
        arguments={
            "root": str(tmp_path),
            "query": "notes",
            "extensions": [".py"],
            "limit": 50,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.SUCCESS
    assert [
        entry["name"] for entry in result.output["entries"]
    ] == ["notes.py"]


def test_pi_search_files_rejects_unapproved_root(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()

    registry, _verifier, _tools = build_tools(approved)
    call = ToolCall(
        tool_name="pi.search_files",
        arguments={
            "root": str(tmp_path),
            "query": "anything",
            "extensions": None,
            "limit": 50,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR
    assert "PermissionError" in result.error


def test_pi_search_files_does_not_follow_symlink_escape(
    tmp_path,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret_target.py").write_text("secret")

    approved = tmp_path / "approved"
    approved.mkdir()
    (approved / "escape_link").symlink_to(
        outside,
        target_is_directory=True,
    )

    registry, _verifier, _tools = build_tools(approved)
    call = ToolCall(
        tool_name="pi.search_files",
        arguments={
            "root": str(approved),
            "query": "secret_target",
            "extensions": None,
            "limit": 50,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["entries"] == []


def test_pi_search_files_respects_limit(tmp_path):
    for index in range(5):
        (tmp_path / f"match_{index}.py").write_text("x")

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.search_files",
        arguments={
            "root": str(tmp_path),
            "query": "match",
            "extensions": None,
            "limit": 2,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["count"] == 2
    assert result.output["truncated"] is True


def test_pi_search_files_skips_excluded_directories(
    tmp_path,
):
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "planner.cpython.pyc").write_text("x")
    (tmp_path / "planner.py").write_text("real")

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.search_files",
        arguments={
            "root": str(tmp_path),
            "query": "planner",
            "extensions": None,
            "limit": 50,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.SUCCESS
    assert [
        entry["name"] for entry in result.output["entries"]
    ] == ["planner.py"]


def test_pi_search_text_returns_line_and_verifies(tmp_path):
    source = tmp_path / "voice_controller.py"
    source.write_text(
        "first line\n"
        "if tool_name == \"pc.open_app\":\n"
        "third line\n",
        encoding="utf-8",
    )

    registry, verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.search_text",
        arguments={
            "root": str(tmp_path),
            "query": "pc.open_app",
            "extensions": None,
            "case_sensitive": False,
            "limit": 50,
        },
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["count"] == 1
    match = result.output["matches"][0]
    assert match["line_number"] == 2
    assert match["relative_path"] == "voice_controller.py"
    assert "pc.open_app" in match["line"]
    assert (
        verification.status
        is VerificationStatus.VERIFIED
    )


def test_pi_search_text_case_insensitive_by_default(
    tmp_path,
):
    (tmp_path / "notes.txt").write_text(
        "Hello World\n",
        encoding="utf-8",
    )

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.search_text",
        arguments={
            "root": str(tmp_path),
            "query": "hello world",
            "extensions": None,
            "case_sensitive": False,
            "limit": 50,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["count"] == 1


def test_pi_search_text_case_sensitive_excludes_mismatch(
    tmp_path,
):
    (tmp_path / "notes.txt").write_text(
        "Hello World\n",
        encoding="utf-8",
    )

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.search_text",
        arguments={
            "root": str(tmp_path),
            "query": "hello world",
            "extensions": None,
            "case_sensitive": True,
            "limit": 50,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["count"] == 0


def test_pi_search_text_rejects_unapproved_root(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()

    registry, _verifier, _tools = build_tools(approved)
    call = ToolCall(
        tool_name="pi.search_text",
        arguments={
            "root": str(tmp_path),
            "query": "anything",
            "extensions": None,
            "case_sensitive": False,
            "limit": 50,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR
    assert "PermissionError" in result.error


def test_pi_search_text_skips_sensitive_file(tmp_path):
    (tmp_path / ".env").write_text(
        "TOKEN=test-only\n",
        encoding="utf-8",
    )

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.search_text",
        arguments={
            "root": str(tmp_path),
            "query": "TOKEN",
            "extensions": None,
            "case_sensitive": False,
            "limit": 50,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["count"] == 0
    assert result.output["files_scanned"] == 0


def test_pi_search_text_skips_binary_file(tmp_path):
    (tmp_path / "image.bin").write_bytes(
        b"marker\x00binary"
    )

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.search_text",
        arguments={
            "root": str(tmp_path),
            "query": "marker",
            "extensions": None,
            "case_sensitive": False,
            "limit": 50,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["count"] == 0


def test_pi_search_text_skips_oversized_file(tmp_path):
    big_file = tmp_path / "huge.txt"
    big_file.write_text(
        "needle " + ("x" * (1_048_576 + 10)),
        encoding="utf-8",
    )

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.search_text",
        arguments={
            "root": str(tmp_path),
            "query": "needle",
            "extensions": None,
            "case_sensitive": False,
            "limit": 50,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["count"] == 0
    assert result.output["files_scanned"] == 0


def test_pi_search_text_respects_limit(tmp_path):
    lines = "\n".join(
        f"needle {index}" for index in range(5)
    )
    (tmp_path / "many.txt").write_text(
        lines,
        encoding="utf-8",
    )

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.search_text",
        arguments={
            "root": str(tmp_path),
            "query": "needle",
            "extensions": None,
            "case_sensitive": False,
            "limit": 2,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["count"] == 2
    assert result.output["truncated"] is True


def test_pi_search_text_skips_excluded_directories(
    tmp_path,
):
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "stale.txt").write_text(
        "needle",
        encoding="utf-8",
    )

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.search_text",
        arguments={
            "root": str(tmp_path),
            "query": "needle",
            "extensions": None,
            "case_sensitive": False,
            "limit": 50,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["count"] == 0


def _fake_completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


def test_pi_read_service_logs_rejects_unallowlisted_service(
    tmp_path,
):
    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.read_service_logs",
        arguments={
            "service": "sshd.service",
            "minutes": 10,
            "limit": 200,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR
    assert "ValueError" in result.error


def test_pi_read_service_logs_builds_safe_args_and_parses_output(
    tmp_path,
    monkeypatch,
):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _fake_completed(
            stdout=(
                "2026-07-20T10:00:00 wake listener ready\n"
                "2026-07-20T10:00:05 heard: hey atlas\n"
            )
        )

    monkeypatch.setattr(
        local_tools.subprocess,
        "run",
        fake_run,
    )

    registry, verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.read_service_logs",
        arguments={
            "service": "atlas-wake.service",
            "minutes": 10,
            "limit": 200,
        },
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert captured["args"][0] == "journalctl"
    assert "shell" not in captured["kwargs"]
    assert "atlas-wake.service" in captured["args"]
    assert result.output["count"] == 2
    assert result.output["lines"][0] == (
        "2026-07-20T10:00:00 wake listener ready"
    )
    assert (
        verification.status
        is VerificationStatus.VERIFIED
    )


def test_pi_read_service_logs_handles_no_entries(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        local_tools.subprocess,
        "run",
        lambda args, **kwargs: _fake_completed(stdout=""),
    )

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.read_service_logs",
        arguments={
            "service": "atlas-wake.service",
            "minutes": 10,
            "limit": 200,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["lines"] == []
    assert result.output["count"] == 0


def test_pi_read_service_logs_handles_subprocess_failure(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        local_tools.subprocess,
        "run",
        lambda args, **kwargs: _fake_completed(
            stderr="no such unit",
            returncode=1,
        ),
    )

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.read_service_logs",
        arguments={
            "service": "atlas-wake.service",
            "minutes": 10,
            "limit": 200,
        },
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR
    assert "RuntimeError" in result.error


def test_pi_get_service_status_rejects_unallowlisted_service(
    tmp_path,
):
    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.get_service_status",
        arguments={"service": "sshd.service"},
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR
    assert "ValueError" in result.error


def test_pi_get_service_status_parses_active_state_and_verifies(
    tmp_path,
    monkeypatch,
):
    def fake_run(args, **kwargs):
        assert "shell" not in kwargs
        assert args[0] == "systemctl"
        return _fake_completed(
            stdout=(
                "Id=atlas-wake.service\n"
                "Description=A.T.L.A.S. Wake Word Listener\n"
                "LoadState=loaded\n"
                "ActiveState=active\n"
                "SubState=running\n"
                "MainPID=1234\n"
            )
        )

    monkeypatch.setattr(
        local_tools.subprocess,
        "run",
        fake_run,
    )

    registry, verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.get_service_status",
        arguments={"service": "atlas-wake.service"},
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["active_state"] == "active"
    assert result.output["sub_state"] == "running"
    assert result.output["main_pid"] == 1234
    assert (
        verification.status
        is VerificationStatus.VERIFIED
    )


def test_pi_get_service_status_reports_failed_state_truthfully(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        local_tools.subprocess,
        "run",
        lambda args, **kwargs: _fake_completed(
            stdout=(
                "Id=atlas-wake.service\n"
                "Description=A.T.L.A.S. Wake Word Listener\n"
                "LoadState=loaded\n"
                "ActiveState=failed\n"
                "SubState=failed\n"
                "MainPID=0\n"
            )
        ),
    )

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.get_service_status",
        arguments={"service": "atlas-wake.service"},
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["active_state"] == "failed"
    assert result.output["sub_state"] == "failed"
    assert result.output["main_pid"] is None


def test_pi_get_service_status_handles_subprocess_failure(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        local_tools.subprocess,
        "run",
        lambda args, **kwargs: _fake_completed(
            stderr="unit not found",
            returncode=1,
        ),
    )

    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.get_service_status",
        arguments={"service": "atlas-wake.service"},
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR
    assert "RuntimeError" in result.error


def test_pi_get_upgrade_status_rejects_unknown_scope(tmp_path):
    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.get_upgrade_status",
        arguments={"scope": "everything"},
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR
    assert "ValueError" in result.error


def test_pi_get_upgrade_status_summary_scope_and_verifies(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        local_tools.implementation_ledger,
        "summarize",
        lambda: {
            "finished": [{"feature_id": "phase1a", "title": "Storage"}],
            "remaining": [],
            "blocked": [],
            "last_updated_feature": {
                "feature_id": "phase1a",
                "title": "Storage monitoring",
            },
            "counts": {
                "finished": 1,
                "remaining": 16,
                "blocked": 0,
                "total": 17,
            },
        },
    )

    registry, verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.get_upgrade_status",
        arguments={"scope": "summary"},
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["finished_count"] == 1
    assert result.output["total_count"] == 17
    assert (
        result.output["last_updated_feature"] == "Storage monitoring"
    )
    assert verification.status is VerificationStatus.VERIFIED


def test_pi_get_upgrade_status_blocked_scope_lists_items(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        local_tools.implementation_ledger,
        "summarize",
        lambda: {
            "finished": [],
            "remaining": [],
            "blocked": [
                {"feature_id": "phase7_gmail_agent", "title": "Gmail agent"}
            ],
            "last_updated_feature": None,
            "counts": {
                "finished": 0,
                "remaining": 16,
                "blocked": 1,
                "total": 17,
            },
        },
    )

    registry, verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="pi.get_upgrade_status",
        arguments={"scope": "blocked"},
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["count"] == 1
    assert result.output["items"][0]["feature_id"] == "phase7_gmail_agent"
    assert verification.status is VerificationStatus.VERIFIED
