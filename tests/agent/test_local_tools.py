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

    assert len(tools) == 1
    assert registry.get(
        "pi.list_directory"
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
