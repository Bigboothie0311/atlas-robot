from threading import Event

import pytest

from atlas_agent.event_bus import EventBus
from atlas_agent.executor import ToolExecutor
from atlas_agent.planner import AgentPlanner
from atlas_agent.router import ToolRouter
from atlas_agent.tasks import (
    AtlasTask,
    TaskStatus,
)
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool
from atlas_agent.verifier import (
    ResultVerifier,
    VerificationCheck,
)
from atlas_agent.workflow import (
    WorkflowError,
    WorkflowRunner,
    WorkflowStatus,
)


def make_plan(
    registry: ToolRegistry,
    task: AtlasTask,
    steps,
):
    return AgentPlanner(
        registry,
        ToolRouter(registry),
    ).create_plan(task, steps)


def test_multistep_workflow_resolves_verified_outputs() -> None:
    registry = ToolRegistry()
    verifier = ResultVerifier()
    transferred: dict[str, object] = {}

    registry.register(
        AtlasTool(
            name="pc.ensure_online",
            description="Ensure PC is online.",
            runs_on="pi",
            handler=lambda: {"online": True},
        )
    )
    registry.register(
        AtlasTool(
            name="pc.search_files",
            description="Search Windows files.",
            runs_on="pc",
            handler=lambda query: [
                {
                    "path": (
                        r"C:\Users\wesle"
                        r"\Documents\ATLAS.f3d"
                    ),
                    "name": "ATLAS.f3d",
                    "size": 1200,
                }
            ],
        )
    )

    def download_file(
        remote_path,
        local_name=None,
    ):
        transferred["remote_path"] = remote_path
        transferred["local_name"] = local_name
        return {
            "verified": True,
            "local_path": (
                "/home/atlas/atlas-staging/"
                "ATLAS.f3d"
            ),
        }

    registry.register(
        AtlasTool(
            name="pc.download_file",
            description="Download a Windows file.",
            runs_on="pi",
            handler=download_file,
        )
    )

    verifier.register(
        "pc.ensure_online",
        lambda call, result: VerificationCheck(
            result.output.get("online") is True,
            "PC reachability checked.",
        ),
    )
    verifier.register(
        "pc.search_files",
        lambda call, result: VerificationCheck(
            isinstance(result.output, list),
            "Search output checked.",
        ),
    )
    verifier.register(
        "pc.download_file",
        lambda call, result: VerificationCheck(
            result.output.get("verified") is True,
            "Transfer hash checked.",
        ),
    )

    task = AtlasTask(
        goal="Find and copy the latest Atlas file.",
        source="voice",
    )
    plan = make_plan(
        registry,
        task,
        [
            {
                "tool": "pc.ensure_online",
                "description": "Ensure the PC is online.",
                "arguments": {},
            },
            {
                "tool": "pc.search_files",
                "description": "Find the newest Atlas file.",
                "arguments": {
                    "query": "atlas",
                },
            },
            {
                "tool": "pc.download_file",
                "description": "Copy and verify the file.",
                "arguments": {
                    "remote_path": {
                        "$ref": (
                            "steps.2.output.0.path"
                        )
                    },
                    "local_name": None,
                },
            },
        ],
    )
    event_bus = EventBus()
    event_names: list[str] = []
    event_bus.subscribe(
        "*",
        lambda event: event_names.append(
            event.name
        ),
    )

    with ToolExecutor(registry) as executor:
        result = WorkflowRunner(
            executor,
            verifier,
            event_bus=event_bus,
        ).run(task, plan)

    assert result.success is True
    assert result.status is WorkflowStatus.COMPLETED
    assert task.status is TaskStatus.COMPLETED
    assert len(result.steps) == 3
    assert all(
        step.verification.verified
        for step in result.steps
    )
    assert transferred["remote_path"] == (
        r"C:\Users\wesle\Documents\ATLAS.f3d"
    )
    assert transferred["local_name"] is None
    assert event_names[0] == (
        "agent.workflow.started"
    )
    assert event_names[-1] == (
        "agent.workflow.completed"
    )
    assert event_names.count(
        "agent.step.completed"
    ) == 3


def test_tool_error_stops_later_steps() -> None:
    registry = ToolRegistry()
    verifier = ResultVerifier()
    later_called = Event()

    def fail() -> None:
        raise RuntimeError("first step broke")

    registry.register(
        AtlasTool(
            name="pi.fail",
            description="Fail.",
            runs_on="pi",
            handler=fail,
        )
    )
    registry.register(
        AtlasTool(
            name="pi.later",
            description="Later step.",
            runs_on="pi",
            handler=lambda: later_called.set(),
        )
    )
    verifier.register(
        "pi.fail",
        lambda call, result: VerificationCheck(
            True,
            "Not reached.",
        ),
    )
    verifier.register(
        "pi.later",
        lambda call, result: VerificationCheck(
            True,
            "Verified.",
        ),
    )
    task = AtlasTask(
        goal="Test failure.",
        source="test",
    )
    plan = make_plan(
        registry,
        task,
        [
            {
                "tool": "pi.fail",
                "description": "Fail.",
                "arguments": {},
            },
            {
                "tool": "pi.later",
                "description": "Do not run.",
                "arguments": {},
            },
        ],
    )

    with ToolExecutor(registry) as executor:
        result = WorkflowRunner(
            executor,
            verifier,
        ).run(task, plan)

    assert result.status is WorkflowStatus.FAILED
    assert result.failed_step == 1
    assert task.status is TaskStatus.FAILED
    assert later_called.is_set() is False
    assert "RuntimeError" in result.error


def test_verification_failure_stops_workflow() -> None:
    registry = ToolRegistry()
    verifier = ResultVerifier()
    registry.register(
        AtlasTool(
            name="pi.unverified",
            description="Unverified action.",
            runs_on="pi",
            handler=lambda: {"worked": False},
        )
    )
    verifier.register(
        "pi.unverified",
        lambda call, result: VerificationCheck(
            verified=False,
            reason="Independent check failed.",
        ),
    )
    task = AtlasTask(
        goal="Test verification.",
        source="test",
    )
    plan = make_plan(
        registry,
        task,
        [
            {
                "tool": "pi.unverified",
                "description": "Run action.",
                "arguments": {},
            }
        ],
    )

    with ToolExecutor(registry) as executor:
        result = WorkflowRunner(
            executor,
            verifier,
        ).run(task, plan)

    assert result.status is WorkflowStatus.FAILED
    assert result.failed_step == 1
    assert result.error == "Independent check failed."
    assert task.status is TaskStatus.FAILED


def test_level_two_step_waits_without_running() -> None:
    registry = ToolRegistry()
    verifier = ResultVerifier()
    handler_called = Event()
    registry.register(
        AtlasTool(
            name="pc.shutdown",
            description="Shut down PC.",
            runs_on="pc",
            handler=lambda: handler_called.set(),
            permission_level=2,
        )
    )
    verifier.register(
        "pc.shutdown",
        lambda call, result: VerificationCheck(
            True,
            "Verified.",
        ),
    )
    task = AtlasTask(
        goal="Shut down the PC.",
        source="voice",
    )
    plan = make_plan(
        registry,
        task,
        [
            {
                "tool": "pc.shutdown",
                "description": "Shut down PC.",
                "arguments": {},
            }
        ],
    )

    with ToolExecutor(registry) as executor:
        result = WorkflowRunner(
            executor,
            verifier,
        ).run(task, plan)

    assert (
        result.status
        is WorkflowStatus.WAITING_CONFIRMATION
    )
    assert task.status is (
        TaskStatus.WAITING_CONFIRMATION
    )
    assert result.confirmation_call_id == (
        plan.steps[0].call.call_id
    )
    assert handler_called.is_set() is False


def test_confirmed_level_two_step_executes() -> None:
    registry = ToolRegistry()
    verifier = ResultVerifier()
    handler_called = Event()

    def handler():
        handler_called.set()
        return {"confirmed": True}

    registry.register(
        AtlasTool(
            name="pc.shutdown",
            description="Shut down PC.",
            runs_on="pc",
            handler=handler,
            permission_level=2,
        )
    )
    verifier.register(
        "pc.shutdown",
        lambda call, result: VerificationCheck(
            result.output.get("confirmed") is True,
            "Confirmed action verified.",
        ),
    )
    task = AtlasTask(
        goal="Shut down the PC.",
        source="voice",
    )
    plan = make_plan(
        registry,
        task,
        [
            {
                "tool": "pc.shutdown",
                "description": "Shut down PC.",
                "arguments": {},
            }
        ],
    )

    with ToolExecutor(registry) as executor:
        result = WorkflowRunner(
            executor,
            verifier,
        ).run(
            task,
            plan,
            confirmed_call_ids={
                plan.steps[0].call.call_id
            },
        )

    assert result.status is WorkflowStatus.COMPLETED
    assert handler_called.is_set() is True
    assert task.status is TaskStatus.COMPLETED


def test_invalid_step_reference_fails_safely() -> None:
    registry = ToolRegistry()
    verifier = ResultVerifier()
    download_called = Event()
    registry.register(
        AtlasTool(
            name="pc.search",
            description="Search.",
            runs_on="pc",
            handler=lambda: [],
        )
    )
    registry.register(
        AtlasTool(
            name="pc.download",
            description="Download.",
            runs_on="pi",
            handler=lambda remote_path: (
                download_called.set()
            ),
        )
    )
    verifier.register(
        "pc.search",
        lambda call, result: VerificationCheck(
            True,
            "Search completed.",
        ),
    )
    verifier.register(
        "pc.download",
        lambda call, result: VerificationCheck(
            True,
            "Download completed.",
        ),
    )
    task = AtlasTask(
        goal="Reference missing match.",
        source="test",
    )
    plan = make_plan(
        registry,
        task,
        [
            {
                "tool": "pc.search",
                "description": "Search.",
                "arguments": {},
            },
            {
                "tool": "pc.download",
                "description": "Download.",
                "arguments": {
                    "remote_path": {
                        "$ref": (
                            "steps.1.output.0.path"
                        )
                    }
                },
            },
        ],
    )

    with ToolExecutor(registry) as executor:
        result = WorkflowRunner(
            executor,
            verifier,
        ).run(task, plan)

    assert result.status is WorkflowStatus.FAILED
    assert result.failed_step == 2
    assert "list index is invalid" in result.error
    assert download_called.is_set() is False


def test_task_and_plan_identity_must_match() -> None:
    registry = ToolRegistry()
    verifier = ResultVerifier()
    registry.register(
        AtlasTool(
            name="pi.status",
            description="Status.",
            runs_on="pi",
            handler=lambda: {"ok": True},
        )
    )
    verifier.register(
        "pi.status",
        lambda call, result: VerificationCheck(
            True,
            "Verified.",
        ),
    )
    planned_task = AtlasTask(
        goal="Planned task.",
        source="test",
    )
    other_task = AtlasTask(
        goal="Different task.",
        source="test",
    )
    plan = make_plan(
        registry,
        planned_task,
        [
            {
                "tool": "pi.status",
                "description": "Status.",
                "arguments": {},
            }
        ],
    )

    with ToolExecutor(registry) as executor:
        runner = WorkflowRunner(
            executor,
            verifier,
        )

        with pytest.raises(
            WorkflowError,
            match="does not match",
        ):
            runner.run(other_task, plan)
