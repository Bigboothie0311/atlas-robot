from datetime import datetime

import pytest

from atlas_agent.tasks import AtlasTask, TaskStatus, ToolCall


def test_atlas_task_defaults_and_status_update() -> None:
    task = AtlasTask(
        goal="Check the PC status.",
        source="voice",
    )
    original_updated_at = task.updated_at

    assert task.status is TaskStatus.QUEUED
    assert task.metadata == {}
    assert task.task_id
    assert datetime.fromisoformat(task.created_at).tzinfo is not None

    task.set_status(TaskStatus.RUNNING)

    assert task.status is TaskStatus.RUNNING
    assert task.updated_at >= original_updated_at


def test_atlas_tasks_receive_unique_ids() -> None:
    first = AtlasTask(goal="First task.", source="test")
    second = AtlasTask(goal="Second task.", source="test")

    assert first.task_id != second.task_id


def test_tool_call_defaults_are_independent() -> None:
    first = ToolCall(
        tool_name="system.status",
        arguments={"verbose": True},
        task_id="task-123",
    )
    second = ToolCall(tool_name="pc.search")

    assert first.arguments == {"verbose": True}
    assert first.task_id == "task-123"
    assert first.call_id != second.call_id
    assert second.arguments == {}


def test_empty_required_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="goal cannot be empty"):
        AtlasTask(goal="   ", source="voice")

    with pytest.raises(ValueError, match="source cannot be empty"):
        AtlasTask(goal="Check status.", source="   ")

    with pytest.raises(ValueError, match="tool_name cannot be empty"):
        ToolCall(tool_name="   ")
