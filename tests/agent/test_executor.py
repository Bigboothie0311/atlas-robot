from threading import Event
from typing import Any, Callable

import pytest

from atlas_agent.executor import ToolExecutor
from atlas_agent.results import ResultStatus
from atlas_agent.tasks import ToolCall
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool


def registry_with_tool(
    handler: Callable[..., Any],
    *,
    permission_level: int = 0,
    timeout_seconds: float = 1.0,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        AtlasTool(
            name="test.tool",
            description="Executor test tool.",
            runs_on="pi",
            handler=handler,
            permission_level=permission_level,
            timeout_seconds=timeout_seconds,
        )
    )
    return registry


def test_executor_runs_registered_tool() -> None:
    def add(left: int, right: int) -> int:
        return left + right

    call = ToolCall(
        tool_name="test.tool",
        arguments={"left": 3, "right": 4},
        task_id="task-123",
    )

    with ToolExecutor(registry_with_tool(add)) as executor:
        result = executor.execute(call)

    assert result.status is ResultStatus.SUCCESS
    assert result.success is True
    assert result.output == 7
    assert result.call_id == call.call_id
    assert result.task_id == "task-123"
    assert result.duration_ms is not None
    assert result.duration_ms >= 0
    assert result.metadata["permission_level"] == 0


def test_unregistered_tool_is_denied() -> None:
    call = ToolCall(tool_name="unknown.tool")

    with ToolExecutor(ToolRegistry()) as executor:
        result = executor.execute(call)

    assert result.status is ResultStatus.DENIED
    assert result.success is False
    assert result.error == "Unregistered tool: unknown.tool"


def test_level_two_tool_waits_for_confirmation() -> None:
    called = Event()

    def handler() -> str:
        called.set()
        return "completed"

    call = ToolCall(tool_name="test.tool")

    with ToolExecutor(
        registry_with_tool(handler, permission_level=2)
    ) as executor:
        waiting = executor.execute(call)
        assert called.is_set() is False

        completed = executor.execute(call, confirmed=True)

    assert waiting.status is ResultStatus.CONFIRMATION_REQUIRED
    assert completed.status is ResultStatus.SUCCESS
    assert completed.output == "completed"
    assert called.is_set() is True


def test_level_three_tool_remains_locked() -> None:
    called = Event()

    def handler() -> None:
        called.set()

    call = ToolCall(tool_name="test.tool")

    with ToolExecutor(
        registry_with_tool(handler, permission_level=3)
    ) as executor:
        result = executor.execute(call, confirmed=True)

    assert result.status is ResultStatus.DENIED
    assert called.is_set() is False


def test_handler_exception_becomes_error_result() -> None:
    def handler() -> None:
        raise RuntimeError("boom")

    call = ToolCall(tool_name="test.tool")

    with ToolExecutor(registry_with_tool(handler)) as executor:
        result = executor.execute(call)

    assert result.status is ResultStatus.ERROR
    assert result.success is False
    assert result.error == "RuntimeError: boom"


def test_handler_timeout_becomes_timeout_result() -> None:
    release_handler = Event()

    def handler() -> str:
        release_handler.wait(timeout=1)
        return "late result"

    call = ToolCall(tool_name="test.tool")
    executor = ToolExecutor(
        registry_with_tool(
            handler,
            timeout_seconds=0.01,
        ),
        max_workers=1,
    )

    try:
        result = executor.execute(call)
    finally:
        release_handler.set()
        executor.close()

    assert result.status is ResultStatus.TIMEOUT
    assert result.success is False
    assert "exceeded" in result.error
    assert "future_cancelled" in result.metadata
    assert "execution_may_continue" in result.metadata


def test_invalid_timeout_does_not_run_handler() -> None:
    called = Event()

    def handler() -> None:
        called.set()

    call = ToolCall(tool_name="test.tool")

    with ToolExecutor(
        registry_with_tool(handler, timeout_seconds=0)
    ) as executor:
        result = executor.execute(call)

    assert result.status is ResultStatus.ERROR
    assert called.is_set() is False
    assert "greater than zero" in result.error


def test_executor_rejects_invalid_worker_count() -> None:
    with pytest.raises(ValueError, match="max_workers must be at least 1"):
        ToolExecutor(ToolRegistry(), max_workers=0)
