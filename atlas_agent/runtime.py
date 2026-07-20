from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atlas_agent.event_bus import EventBus
from atlas_agent.events import AtlasEvent
from atlas_agent.mission_store import MissionStore
from atlas_agent.planning_service import (
    NaturalLanguagePlanningService,
    ValidatedPlanResult,
)
from atlas_agent.task_queue import TaskQueue
from atlas_agent.tasks import (
    AtlasTask,
    TaskStatus,
)
from atlas_agent.workflow import (
    WorkflowResult,
    WorkflowRunner,
)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    task: AtlasTask
    planning: ValidatedPlanResult
    workflow: WorkflowResult


class AgentRuntime:
    """Single entry point from a user goal to verified execution.

    The runtime creates and queues the task, persists it when configured,
    requests a locally validated plan, runs the verified workflow, and
    returns all structured results. Permission decisions remain inside
    ToolExecutor and WorkflowRunner.
    """

    def __init__(
        self,
        planning_service: NaturalLanguagePlanningService,
        workflow_runner: WorkflowRunner,
        *,
        task_queue: TaskQueue | None = None,
        mission_store: MissionStore | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.planning_service = planning_service
        self.workflow_runner = workflow_runner
        self.task_queue = (
            task_queue
            if task_queue is not None
            else TaskQueue()
        )
        self.mission_store = mission_store
        self.event_bus = event_bus

    def run_goal(
        self,
        goal: str,
        *,
        source: str = "voice",
        metadata: dict[str, Any] | None = None,
        confirmed_call_ids: set[str] | None = None,
    ) -> AgentRunResult:
        normalized_goal = goal.strip()
        normalized_source = source.strip()

        if not normalized_goal:
            raise ValueError("goal must not be empty")

        if not normalized_source:
            raise ValueError("source must not be empty")

        if (
            metadata is not None
            and not isinstance(metadata, dict)
        ):
            raise TypeError("metadata must be an object")

        task = AtlasTask(
            goal=normalized_goal,
            source=normalized_source,
            metadata=dict(metadata or {}),
        )
        self.task_queue.enqueue(task)
        self._persist()

        self._publish(
            "agent.planning.started",
            {
                "task_id": task.task_id,
                "goal": task.goal,
                "source": task.source,
            },
        )

        try:
            planning = (
                self.planning_service.create_plan(task)
            )
        except Exception as exc:
            self._publish(
                "agent.planning.failed",
                {
                    "task_id": task.task_id,
                    "goal": task.goal,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            self._mark_failed(task.task_id)
            self._persist()
            raise

        plan_id = getattr(planning.plan, "plan_id", None)
        plan_steps = getattr(planning.plan, "steps", ())

        self._publish(
            "agent.planning.completed",
            {
                "task_id": task.task_id,
                "plan_id": plan_id,
                "step_count": len(plan_steps),
            },
        )

        try:
            workflow = self.workflow_runner.run(
                task,
                planning.plan,
                confirmed_call_ids=set(
                    confirmed_call_ids or set()
                ),
            )
        except Exception as exc:
            self._publish(
                "agent.workflow.failed",
                {
                    "task_id": task.task_id,
                    "plan_id": plan_id,
                    "status": "failed",
                    "completed_steps": 0,
                    "failed_step": None,
                    "confirmation_call_id": None,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            self._mark_failed(task.task_id)
            self._persist()
            raise

        self._persist()

        return AgentRunResult(
            task=task,
            planning=planning,
            workflow=workflow,
        )

    def _mark_failed(
        self,
        task_id: str,
    ) -> None:
        try:
            task = self.task_queue.get(task_id)
        except KeyError:
            return

        if task.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            return

        try:
            if task.status is TaskStatus.QUEUED:
                task = self.task_queue.set_status(
                    task_id,
                    TaskStatus.RUNNING,
                )

            self.task_queue.set_status(
                task_id,
                TaskStatus.FAILED,
            )
        except (KeyError, ValueError):
            return

    def _persist(self) -> None:
        if self.mission_store is None:
            return

        self.mission_store.save(
            self.task_queue.list_tasks()
        )

    def _publish(
        self,
        name: str,
        data: dict[str, Any],
    ) -> None:
        if self.event_bus is None:
            return

        self.event_bus.publish(
            AtlasEvent(
                name=name,
                source="agent_runtime",
                data=data,
            )
        )
