import pytest

from atlas_agent.task_queue import TaskQueue
from atlas_agent.tasks import AtlasTask, TaskStatus


def make_task(number: int) -> AtlasTask:
    return AtlasTask(
        goal=f"Task {number}",
        source="test",
    )


def test_queue_claims_tasks_in_fifo_order() -> None:
    queue = TaskQueue()
    first = make_task(1)
    second = make_task(2)

    queue.enqueue(first)
    queue.enqueue(second)

    assert len(queue) == 2
    assert queue.pending_count == 2

    assert queue.claim_next() is first
    assert first.status is TaskStatus.RUNNING
    assert queue.pending_count == 1

    assert queue.claim_next() is second
    assert second.status is TaskStatus.RUNNING
    assert queue.pending_count == 0
    assert queue.claim_next() is None


def test_queue_rejects_duplicate_and_nonqueued_tasks() -> None:
    queue = TaskQueue()
    task = make_task(1)
    queue.enqueue(task)

    with pytest.raises(ValueError, match="already exists"):
        queue.enqueue(task)

    running_task = make_task(2)
    running_task.set_status(TaskStatus.RUNNING)

    with pytest.raises(ValueError, match="queued status"):
        queue.enqueue(running_task)


def test_restore_preserves_history_and_only_queues_pending_tasks() -> None:
    queue = TaskQueue()

    completed = make_task(1)
    completed.set_status(TaskStatus.RUNNING)
    completed.set_status(TaskStatus.COMPLETED)

    queued = make_task(2)

    queue.restore(completed)
    queue.restore(queued)

    assert queue.get(completed.task_id) is completed
    assert completed.status is TaskStatus.COMPLETED
    assert queue.pending_count == 1

    assert queue.claim_next() is queued
    assert queue.claim_next() is None


def test_waiting_task_can_be_requeued_and_completed() -> None:
    queue = TaskQueue()
    task = make_task(1)
    queue.enqueue(task)
    queue.claim_next()

    queue.set_status(
        task.task_id,
        TaskStatus.WAITING_CONFIRMATION,
    )
    assert task.status is TaskStatus.WAITING_CONFIRMATION

    queue.set_status(task.task_id, TaskStatus.QUEUED)
    assert queue.pending_count == 1

    assert queue.claim_next() is task
    queue.set_status(task.task_id, TaskStatus.COMPLETED)

    assert task.status is TaskStatus.COMPLETED

    with pytest.raises(ValueError, match="Invalid task transition"):
        queue.set_status(task.task_id, TaskStatus.RUNNING)


def test_cancelled_queued_task_is_skipped() -> None:
    queue = TaskQueue()
    cancelled = make_task(1)
    available = make_task(2)
    queue.enqueue(cancelled)
    queue.enqueue(available)

    queue.set_status(
        cancelled.task_id,
        TaskStatus.CANCELLED,
    )

    assert queue.claim_next() is available
    assert cancelled.status is TaskStatus.CANCELLED


def test_tasks_can_be_listed_and_filtered_by_status() -> None:
    queue = TaskQueue()
    completed = make_task(1)
    queued = make_task(2)
    queue.enqueue(completed)
    queue.enqueue(queued)

    queue.claim_next()
    queue.set_status(
        completed.task_id,
        TaskStatus.COMPLETED,
    )

    assert queue.list_tasks() == [completed, queued]
    assert queue.list_tasks(
        TaskStatus.COMPLETED
    ) == [completed]
    assert queue.list_tasks(
        TaskStatus.QUEUED
    ) == [queued]


def test_unknown_task_raises_clear_error() -> None:
    queue = TaskQueue()

    with pytest.raises(KeyError, match="Unknown task"):
        queue.get("missing-task")

    with pytest.raises(KeyError, match="Unknown task"):
        queue.set_status(
            "missing-task",
            TaskStatus.CANCELLED,
        )
