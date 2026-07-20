from datetime import datetime

import pytest

from atlas_agent.results import ResultStatus, ToolResult


def test_successful_result_defaults() -> None:
    result = ToolResult(
        tool_name="system.status",
        call_id="call-123",
        task_id="task-456",
        status=ResultStatus.SUCCESS,
        output={"online": True},
    )

    assert result.success is True
    assert result.error is None
    assert result.metadata == {}
    assert result.output == {"online": True}
    assert datetime.fromisoformat(result.started_at).tzinfo is not None
    assert datetime.fromisoformat(result.finished_at).tzinfo is not None


@pytest.mark.parametrize(
    "status",
    [
        ResultStatus.ERROR,
        ResultStatus.DENIED,
        ResultStatus.CONFIRMATION_REQUIRED,
        ResultStatus.TIMEOUT,
    ],
)
def test_non_success_statuses_are_not_successful(
    status: ResultStatus,
) -> None:
    result = ToolResult(
        tool_name="pc.search",
        call_id="call-123",
        status=status,
        error="Action did not complete.",
    )

    assert result.success is False


def test_result_metadata_defaults_are_independent() -> None:
    first = ToolResult(
        tool_name="system.status",
        call_id="call-1",
        status=ResultStatus.SUCCESS,
    )
    second = ToolResult(
        tool_name="system.status",
        call_id="call-2",
        status=ResultStatus.SUCCESS,
    )

    first.metadata["verified"] = True

    assert second.metadata == {}


def test_invalid_result_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="tool_name cannot be empty"):
        ToolResult(
            tool_name="   ",
            call_id="call-1",
            status=ResultStatus.ERROR,
        )

    with pytest.raises(ValueError, match="call_id cannot be empty"):
        ToolResult(
            tool_name="system.status",
            call_id="   ",
            status=ResultStatus.ERROR,
        )

    with pytest.raises(ValueError, match="duration_ms cannot be negative"):
        ToolResult(
            tool_name="system.status",
            call_id="call-1",
            status=ResultStatus.SUCCESS,
            duration_ms=-1,
        )
