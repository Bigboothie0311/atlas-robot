from atlas_agent.executor import ToolExecutor
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

    assert len(tools) == 2
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
