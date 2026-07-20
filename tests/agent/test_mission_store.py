import json
import stat

import pytest

from atlas_agent.mission_store import (
    MissionStore,
    MissionStoreError,
)
from atlas_agent.tasks import AtlasTask, TaskStatus


def make_task(
    goal: str,
    *,
    status: TaskStatus = TaskStatus.QUEUED,
) -> AtlasTask:
    return AtlasTask(
        goal=goal,
        source="test",
        status=status,
        metadata={"project": "atlas"},
    )


def test_missing_store_loads_as_empty(tmp_path) -> None:
    store = MissionStore(tmp_path / "missions.json")

    assert store.load() == []


def test_tasks_round_trip_with_private_file_permissions(
    tmp_path,
) -> None:
    path = tmp_path / "state" / "missions.json"
    store = MissionStore(path)
    first = make_task("First mission")
    second = make_task(
        "Waiting mission",
        status=TaskStatus.WAITING_CONFIRMATION,
    )

    store.save([first, second])
    loaded = store.load()

    assert loaded == [first, second]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(
        path.read_text(encoding="utf-8")
    )["version"] == 1


def test_interrupted_running_task_is_recovered_safely(
    tmp_path,
) -> None:
    store = MissionStore(tmp_path / "missions.json")
    running = make_task(
        "Interrupted mission",
        status=TaskStatus.RUNNING,
    )
    store.save([running])

    recovered = store.load()
    unchanged = store.load(recover_interrupted=False)

    assert recovered[0].status is TaskStatus.FAILED
    assert (
        recovered[0].metadata["recovery_reason"]
        == "Task was interrupted before completion."
    )
    assert unchanged[0].status is TaskStatus.RUNNING


def test_corrupt_json_is_not_silently_discarded(
    tmp_path,
) -> None:
    path = tmp_path / "missions.json"
    path.write_text("{broken json", encoding="utf-8")
    store = MissionStore(path)

    with pytest.raises(
        MissionStoreError,
        match="invalid JSON",
    ):
        store.load()

    assert path.read_text(
        encoding="utf-8"
    ) == "{broken json"


def test_unsupported_store_version_is_rejected(
    tmp_path,
) -> None:
    path = tmp_path / "missions.json"
    path.write_text(
        json.dumps(
            {
                "version": 999,
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        MissionStoreError,
        match="Unsupported mission store version",
    ):
        MissionStore(path).load()


def test_duplicate_task_ids_are_rejected(
    tmp_path,
) -> None:
    store = MissionStore(tmp_path / "missions.json")
    first = make_task("First")
    duplicate = make_task("Duplicate")
    duplicate.task_id = first.task_id

    with pytest.raises(
        MissionStoreError,
        match="duplicate task IDs",
    ):
        store.save([first, duplicate])


def test_failed_save_preserves_previous_store(
    tmp_path,
) -> None:
    path = tmp_path / "missions.json"
    store = MissionStore(path)
    original = make_task("Original mission")
    store.save([original])

    invalid = make_task("Invalid mission")
    invalid.metadata["not_json"] = object()

    with pytest.raises(
        MissionStoreError,
        match="Could not save mission store",
    ):
        store.save([invalid])

    assert store.load() == [original]
    assert list(
        path.parent.glob(f".{path.name}.*.tmp")
    ) == []
