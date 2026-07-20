import pytest

from atlas_agent.router import (
    ExecutionTarget,
    RouteStatus,
    ToolRouter,
)
from atlas_agent.tasks import ToolCall
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()

    for name, runs_on in [
        ("pi.status", "pi"),
        ("pc.search_files", "pc"),
        ("invalid.tool", "somewhere"),
    ]:
        registry.register(
            AtlasTool(
                name=name,
                description=f"Test tool {name}.",
                runs_on=runs_on,
                handler=lambda: None,
            )
        )

    return registry


@pytest.mark.parametrize(
    ("tool_name", "expected_target"),
    [
        ("pi.status", ExecutionTarget.PI),
        ("pc.search_files", ExecutionTarget.PC),
    ],
)
def test_registered_tool_routes_to_expected_target(
    tool_name: str,
    expected_target: ExecutionTarget,
) -> None:
    call = ToolCall(tool_name=tool_name)
    decision = ToolRouter(make_registry()).decide(call)

    assert decision.ready is True
    assert decision.status is RouteStatus.READY
    assert decision.target is expected_target
    assert decision.call_id == call.call_id
    assert decision.tool_name == tool_name


def test_unknown_tool_is_not_routable() -> None:
    call = ToolCall(tool_name="unknown.tool")
    decision = ToolRouter(make_registry()).decide(call)

    assert decision.ready is False
    assert decision.status is RouteStatus.UNKNOWN_TOOL
    assert decision.target is None
    assert "not registered" in decision.reason


def test_unsupported_execution_target_is_rejected() -> None:
    call = ToolCall(tool_name="invalid.tool")
    decision = ToolRouter(make_registry()).decide(call)

    assert decision.ready is False
    assert (
        decision.status
        is RouteStatus.UNSUPPORTED_TARGET
    )
    assert decision.target is None
    assert "unsupported execution target" in decision.reason


def test_multiple_decisions_preserve_call_order() -> None:
    calls = [
        ToolCall(tool_name="pc.search_files"),
        ToolCall(tool_name="pi.status"),
        ToolCall(tool_name="missing.tool"),
    ]

    decisions = ToolRouter(
        make_registry()
    ).decide_many(calls)

    assert [
        decision.call_id
        for decision in decisions
    ] == [
        call.call_id
        for call in calls
    ]
    assert [
        decision.status
        for decision in decisions
    ] == [
        RouteStatus.READY,
        RouteStatus.READY,
        RouteStatus.UNKNOWN_TOOL,
    ]
