from types import SimpleNamespace

import pytest

from atlas_agent.runtime import AgentRuntime
from atlas_agent.task_queue import TaskQueue
from atlas_agent.tasks import TaskStatus


class FakePlanningService:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def create_plan(self, task):
        self.calls.append(task)

        if self.error is not None:
            raise self.error

        return self.result


class FakeWorkflowRunner:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def run(
        self,
        task,
        plan,
        *,
        confirmed_call_ids=None,
    ):
        self.calls.append(
            {
                "task": task,
                "plan": plan,
                "confirmed_call_ids": (
                    confirmed_call_ids
                ),
            }
        )

        if self.error is not None:
            raise self.error

        return self.result


class FakeMissionStore:
    def __init__(self):
        self.snapshots = []

    def save(self, tasks):
        self.snapshots.append(
            [
                {
                    "task_id": task.task_id,
                    "status": task.status,
                    "goal": task.goal,
                }
                for task in tasks
            ]
        )


def test_runtime_connects_goal_to_planning_and_workflow():
    queue = TaskQueue()
    store = FakeMissionStore()
    plan = object()
    planning_result = SimpleNamespace(
        plan=plan,
    )
    workflow_result = SimpleNamespace(
        status="completed",
    )
    planning = FakePlanningService(
        result=planning_result,
    )
    workflow = FakeWorkflowRunner(
        result=workflow_result,
    )
    runtime = AgentRuntime(
        planning_service=planning,
        workflow_runner=workflow,
        task_queue=queue,
        mission_store=store,
    )
    confirmations = {
        "call-confirmed",
    }

    result = runtime.run_goal(
        "  Find my Atlas file.  ",
        source="  voice  ",
        metadata={
            "surface": "desk",
        },
        confirmed_call_ids=confirmations,
    )

    assert result.task.goal == "Find my Atlas file."
    assert result.task.source == "voice"
    assert result.task.metadata == {
        "surface": "desk",
    }
    assert result.planning is planning_result
    assert result.workflow is workflow_result

    assert planning.calls == [result.task]
    assert workflow.calls == [
        {
            "task": result.task,
            "plan": plan,
            "confirmed_call_ids": {
                "call-confirmed",
            },
        }
    ]
    assert queue.get(result.task.task_id) is result.task
    assert len(store.snapshots) == 2
    assert store.snapshots[0][0]["status"] is (
        TaskStatus.QUEUED
    )


def test_planning_failure_marks_task_failed_and_persists():
    queue = TaskQueue()
    store = FakeMissionStore()
    planning = FakePlanningService(
        error=RuntimeError("planning failed"),
    )
    workflow = FakeWorkflowRunner(
        result=object(),
    )
    runtime = AgentRuntime(
        planning_service=planning,
        workflow_runner=workflow,
        task_queue=queue,
        mission_store=store,
    )

    with pytest.raises(
        RuntimeError,
        match="planning failed",
    ):
        runtime.run_goal(
            "Check the PC.",
            source="phone",
        )

    tasks = queue.list_tasks()

    assert len(tasks) == 1
    assert tasks[0].status is TaskStatus.FAILED
    assert workflow.calls == []
    assert len(store.snapshots) == 2
    assert store.snapshots[-1][0]["status"] is (
        TaskStatus.FAILED
    )


def test_unexpected_workflow_failure_marks_task_failed():
    queue = TaskQueue()
    planning = FakePlanningService(
        result=SimpleNamespace(
            plan=object(),
        )
    )
    workflow = FakeWorkflowRunner(
        error=RuntimeError("workflow crashed"),
    )
    runtime = AgentRuntime(
        planning_service=planning,
        workflow_runner=workflow,
        task_queue=queue,
    )

    with pytest.raises(
        RuntimeError,
        match="workflow crashed",
    ):
        runtime.run_goal(
            "Run a workflow.",
        )

    task = queue.list_tasks()[0]

    assert task.status is TaskStatus.FAILED


@pytest.mark.parametrize(
    ("goal", "source", "message"),
    [
        ("   ", "voice", "goal must not be empty"),
        ("Check the PC.", "   ", "source must not be empty"),
    ],
)
def test_invalid_request_is_rejected_before_queueing(
    goal,
    source,
    message,
):
    queue = TaskQueue()
    runtime = AgentRuntime(
        planning_service=FakePlanningService(
            result=object(),
        ),
        workflow_runner=FakeWorkflowRunner(
            result=object(),
        ),
        task_queue=queue,
    )

    with pytest.raises(ValueError, match=message):
        runtime.run_goal(
            goal,
            source=source,
        )

    assert queue.list_tasks() == []


def test_non_object_metadata_is_rejected():
    queue = TaskQueue()
    runtime = AgentRuntime(
        planning_service=FakePlanningService(
            result=object(),
        ),
        workflow_runner=FakeWorkflowRunner(
            result=object(),
        ),
        task_queue=queue,
    )

    with pytest.raises(
        TypeError,
        match="metadata must be an object",
    ):
        runtime.run_goal(
            "Check the PC.",
            metadata=["not", "an", "object"],
        )

    assert queue.list_tasks() == []
