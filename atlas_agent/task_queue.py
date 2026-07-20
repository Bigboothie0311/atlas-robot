from __future__ import annotations

from collections import deque
from threading import RLock

from atlas_agent.tasks import AtlasTask, TaskStatus


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {
        TaskStatus.RUNNING,
        TaskStatus.CANCELLED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.WAITING_CONFIRMATION,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.WAITING_CONFIRMATION: {
        TaskStatus.QUEUED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}


class TaskQueue:
    def __init__(self) -> None:
        self._tasks: dict[str, AtlasTask] = {}
        self._queued_ids: deque[str] = deque()
        self._lock = RLock()

    def enqueue(self, task: AtlasTask) -> None:
        with self._lock:
            if task.task_id in self._tasks:
                raise ValueError(
                    f"Task already exists: {task.task_id}"
                )

            if task.status is not TaskStatus.QUEUED:
                raise ValueError(
                    "Only tasks with queued status can be enqueued."
                )

            self._tasks[task.task_id] = task
            self._queued_ids.append(task.task_id)

    def claim_next(self) -> AtlasTask | None:
        with self._lock:
            while self._queued_ids:
                task_id = self._queued_ids.popleft()
                task = self._tasks.get(task_id)

                if task is None:
                    continue

                if task.status is not TaskStatus.QUEUED:
                    continue

                task.set_status(TaskStatus.RUNNING)
                return task

            return None

    def get(self, task_id: str) -> AtlasTask:
        with self._lock:
            try:
                return self._tasks[task_id]
            except KeyError:
                raise KeyError(
                    f"Unknown task: {task_id}"
                ) from None

    def set_status(
        self,
        task_id: str,
        status: TaskStatus,
    ) -> AtlasTask:
        with self._lock:
            task = self.get(task_id)

            if task.status is status:
                return task

            allowed = ALLOWED_TRANSITIONS[task.status]

            if status not in allowed:
                raise ValueError(
                    f"Invalid task transition: "
                    f"{task.status.value} -> {status.value}"
                )

            task.set_status(status)

            if status is TaskStatus.QUEUED:
                self._queued_ids.append(task.task_id)

            return task

    def list_tasks(
        self,
        status: TaskStatus | None = None,
    ) -> list[AtlasTask]:
        with self._lock:
            tasks = list(self._tasks.values())

            if status is not None:
                tasks = [
                    task
                    for task in tasks
                    if task.status is status
                ]

            return tasks

    @property
    def pending_count(self) -> int:
        with self._lock:
            return sum(
                task.status is TaskStatus.QUEUED
                for task in self._tasks.values()
            )

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)
