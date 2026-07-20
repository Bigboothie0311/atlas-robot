import pytest

from atlas_agent.mission_store import MissionStore
from atlas_agent.runtime_factory import (
    build_pc_agent_runtime,
)
from atlas_agent.tasks import AtlasTask


EXPECTED_PC_TOOLS = {
    "pc.ensure_online",
    "pc.search_files",
    "pc.download_file",
    "pc.active_apps",
    "pc.open_app",
}


def build_bundle(
    tmp_path,
    *,
    host="192.168.50.2",
    username="wesle",
    roots=None,
):
    identity_file = tmp_path / "test_identity"
    identity_file.write_text(
        "test-only-placeholder"
    )

    return build_pc_agent_runtime(
        openai_client=object(),
        model="gpt-test",
        host=host,
        username=username,
        identity_file=identity_file,
        approved_remote_roots=(
            roots
            if roots is not None
            else [r"C:\Users\wesle"]
        ),
        staging_directory=(
            tmp_path / "staging"
        ),
        mission_store_path=(
            tmp_path / "missions.json"
        ),
    )


def test_factory_builds_complete_pc_runtime(tmp_path):
    bundle = build_bundle(tmp_path)

    try:
        registered_names = {
            tool.name
            for tool in bundle.registry.list_tools()
        }

        assert registered_names == EXPECTED_PC_TOOLS
        assert bundle.runtime.task_queue is (
            bundle.task_queue
        )
        assert bundle.runtime.mission_store is (
            bundle.mission_store
        )
        assert bundle.task_queue.list_tasks() == []
    finally:
        bundle.close()


def test_factory_recovers_persisted_tasks(tmp_path):
    mission_path = tmp_path / "missions.json"
    original_task = AtlasTask(
        goal="Remember this mission.",
        source="test",
    )
    MissionStore(mission_path).save(
        [original_task]
    )
    identity_file = tmp_path / "test_identity"
    identity_file.write_text(
        "test-only-placeholder"
    )

    bundle = build_pc_agent_runtime(
        openai_client=object(),
        model="gpt-test",
        host="192.168.50.2",
        username="wesle",
        identity_file=identity_file,
        approved_remote_roots=[
            r"C:\Users\wesle",
        ],
        staging_directory=(
            tmp_path / "staging"
        ),
        mission_store_path=mission_path,
    )

    try:
        recovered = bundle.task_queue.get(
            original_task.task_id
        )

        assert recovered.task_id == (
            original_task.task_id
        )
        assert recovered.goal == (
            "Remember this mission."
        )
    finally:
        bundle.close()


@pytest.mark.parametrize(
    ("host", "username", "roots", "message"),
    [
        (
            "   ",
            "wesle",
            [r"C:\Users\wesle"],
            "host must not be empty",
        ),
        (
            "192.168.50.2",
            "   ",
            [r"C:\Users\wesle"],
            "username must not be empty",
        ),
        (
            "192.168.50.2",
            "wesle",
            [],
            "approved_remote_roots must not be empty",
        ),
    ],
)
def test_factory_rejects_invalid_configuration(
    tmp_path,
    host,
    username,
    roots,
    message,
):
    with pytest.raises(
        ValueError,
        match=message,
    ):
        build_bundle(
            tmp_path,
            host=host,
            username=username,
            roots=roots,
        )
