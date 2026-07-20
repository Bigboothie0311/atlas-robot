from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

from atlas_agent.tasks import AtlasTask, TaskStatus, utc_now


class MissionStoreError(RuntimeError):
    pass


class MissionStore:
    VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def save(self, tasks: Iterable[AtlasTask]) -> None:
        task_list = list(tasks)
        task_ids = [task.task_id for task in task_list]

        if len(task_ids) != len(set(task_ids)):
            raise MissionStoreError(
                "Cannot save duplicate task IDs."
            )

        payload = {
            "version": self.VERSION,
            "saved_at": utc_now(),
            "tasks": [
                self._serialize_task(task)
                for task in task_list
            ],
        }

        parent = self._path.parent
        temporary_path = parent / (
            f".{self._path.name}.{uuid4().hex}.tmp"
        )

        try:
            parent.mkdir(
                mode=0o700,
                parents=True,
                exist_ok=True,
            )

            with temporary_path.open(
                "x",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    payload,
                    handle,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self._path)
        except (OSError, TypeError, ValueError) as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

            raise MissionStoreError(
                f"Could not save mission store: {exc}"
            ) from exc

    def load(
        self,
        *,
        recover_interrupted: bool = True,
    ) -> list[AtlasTask]:
        try:
            raw_payload = self._path.read_text(
                encoding="utf-8"
            )
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise MissionStoreError(
                f"Could not read mission store: {exc}"
            ) from exc

        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise MissionStoreError(
                f"Mission store contains invalid JSON: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise MissionStoreError(
                "Mission store root must be an object."
            )

        if payload.get("version") != self.VERSION:
            raise MissionStoreError(
                "Unsupported mission store version: "
                f"{payload.get('version')}"
            )

        raw_tasks = payload.get("tasks")

        if not isinstance(raw_tasks, list):
            raise MissionStoreError(
                "Mission store tasks must be a list."
            )

        tasks: list[AtlasTask] = []
        seen_ids: set[str] = set()

        for index, raw_task in enumerate(raw_tasks):
            task = self._deserialize_task(
                raw_task,
                index=index,
            )

            if task.task_id in seen_ids:
                raise MissionStoreError(
                    f"Duplicate task ID in mission store: "
                    f"{task.task_id}"
                )

            seen_ids.add(task.task_id)

            if (
                recover_interrupted
                and task.status is TaskStatus.RUNNING
            ):
                task.metadata = dict(task.metadata)
                task.metadata["recovery_reason"] = (
                    "Task was interrupted before completion."
                )
                task.status = TaskStatus.FAILED
                task.updated_at = utc_now()

            tasks.append(task)

        return tasks

    @staticmethod
    def _serialize_task(
        task: AtlasTask,
    ) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "goal": task.goal,
            "source": task.source,
            "status": task.status.value,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "metadata": task.metadata,
        }

    @staticmethod
    def _deserialize_task(
        raw_task: Any,
        *,
        index: int,
    ) -> AtlasTask:
        if not isinstance(raw_task, dict):
            raise MissionStoreError(
                f"Task entry {index} must be an object."
            )

        try:
            metadata = raw_task.get("metadata", {})

            if not isinstance(metadata, dict):
                raise TypeError("metadata must be an object")

            return AtlasTask(
                task_id=raw_task["task_id"],
                goal=raw_task["goal"],
                source=raw_task["source"],
                status=TaskStatus(raw_task["status"]),
                created_at=raw_task["created_at"],
                updated_at=raw_task["updated_at"],
                metadata=metadata,
            )
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise MissionStoreError(
                f"Invalid task entry {index}: {exc}"
            ) from exc
