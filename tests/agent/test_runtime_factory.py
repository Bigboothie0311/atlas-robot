import pytest

from atlas_agent.mission_store import MissionStore
from atlas_agent.runtime_factory import (
    build_pc_agent_runtime,
)
from atlas_agent.tasks import AtlasTask, TaskStatus


EXPECTED_PC_TOOLS = {
    "pc.ensure_online",
    "pc.search_files",
    "pc.download_file",
    "pc.active_apps",
    "pc.open_app",
    "pi.list_directory",
    "pi.read_text_file",
    "pi.search_files",
    "pi.search_text",
    "pi.read_service_logs",
    "pi.get_service_status",
    "pi.get_upgrade_status",
    "pi.get_mission_history",
    "pi.explain_last_failure",
    "pi.run_diagnostics",
    "pi.recover_component",
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
        assert bundle.runtime.event_bus is bundle.event_bus
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


def test_factory_recovers_completed_task_history(tmp_path):
    mission_path = tmp_path / "missions.json"
    completed_task = AtlasTask(
        goal="Completed historical mission.",
        source="test",
    )
    completed_task.set_status(TaskStatus.RUNNING)
    completed_task.set_status(TaskStatus.COMPLETED)

    MissionStore(mission_path).save([completed_task])

    bundle = build_bundle(tmp_path)

    try:
        recovered = bundle.task_queue.get(
            completed_task.task_id
        )

        assert recovered.status is TaskStatus.COMPLETED
        assert bundle.task_queue.pending_count == 0
        assert bundle.task_queue.claim_next() is None
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


def test_factory_wires_tool_audit_sink(tmp_path):
    bundle = build_bundle(tmp_path)

    try:
        assert bundle.executor.audit_sink is not None
    finally:
        bundle.close()
