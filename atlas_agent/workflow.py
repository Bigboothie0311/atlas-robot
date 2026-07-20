from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any

from atlas_agent.event_bus import EventBus
from atlas_agent.events import AtlasEvent
from atlas_agent.executor import ToolExecutor
from atlas_agent.planner import ExecutionPlan, PlanStep
from atlas_agent.results import (
    ResultStatus,
    ToolResult,
)
from atlas_agent.tasks import (
    AtlasTask,
    TaskStatus,
    ToolCall,
)
from atlas_agent.verifier import (
    ResultVerifier,
    VerificationResult,
)


class WorkflowError(RuntimeError):
    pass


class WorkflowStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_CONFIRMATION = "waiting_confirmation"


@dataclass(frozen=True, slots=True)
class StepOutcome:
    position: int
    description: str
    call: ToolCall
    result: ToolResult | None
    verification: VerificationResult | None
    error: str | None


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    task_id: str
    plan_id: str
    status: WorkflowStatus
    steps: tuple[StepOutcome, ...]
    failed_step: int | None
    confirmation_call_id: str | None
    error: str | None

    @property
    def success(self) -> bool:
        return self.status is WorkflowStatus.COMPLETED


class WorkflowRunner:
    def __init__(
        self,
        executor: ToolExecutor,
        verifier: ResultVerifier,
        *,
        event_bus: EventBus | None = None,
    ) -> None:
        self._executor = executor
        self._verifier = verifier
        self._event_bus = event_bus

    def run(
        self,
        task: AtlasTask,
        plan: ExecutionPlan,
        *,
        confirmed_call_ids: set[str] | None = None,
    ) -> WorkflowResult:
        if plan.task_id != task.task_id:
            raise WorkflowError(
                "Plan task ID does not match the task."
            )

        if task.status is TaskStatus.QUEUED:
            task.set_status(TaskStatus.RUNNING)
        elif task.status is not TaskStatus.RUNNING:
            raise WorkflowError(
                "Task must be queued or running."
            )

        confirmed = confirmed_call_ids or set()
        outcomes: list[StepOutcome] = []
        outputs: dict[int, Any] = {}

        self._publish(
            "agent.workflow.started",
            {
                "task_id": task.task_id,
                "plan_id": plan.plan_id,
                "goal": task.goal,
                "step_count": len(plan.steps),
            },
        )

        for step in plan.steps:
            self._publish(
                "agent.step.started",
                {
                    "task_id": task.task_id,
                    "plan_id": plan.plan_id,
                    "position": step.position,
                    "tool_name": step.call.tool_name,
                    "description": step.description,
                    "target": step.target.value,
                },
            )

            try:
                call = self._resolved_call(
                    step,
                    outputs,
                )
            except WorkflowError as exc:
                outcome = StepOutcome(
                    position=step.position,
                    description=step.description,
                    call=step.call,
                    result=None,
                    verification=None,
                    error=str(exc),
                )
                outcomes.append(outcome)
                task.set_status(TaskStatus.FAILED)

                return self._finish(
                    task,
                    plan,
                    WorkflowStatus.FAILED,
                    outcomes,
                    failed_step=step.position,
                    error=str(exc),
                )

            result = self._executor.execute(
                call,
                confirmed=call.call_id in confirmed,
            )

            if (
                result.status
                is ResultStatus.CONFIRMATION_REQUIRED
            ):
                outcome = StepOutcome(
                    position=step.position,
                    description=step.description,
                    call=call,
                    result=result,
                    verification=None,
                    error=result.error,
                )
                outcomes.append(outcome)
                task.set_status(
                    TaskStatus.WAITING_CONFIRMATION
                )

                return self._finish(
                    task,
                    plan,
                    WorkflowStatus.WAITING_CONFIRMATION,
                    outcomes,
                    failed_step=None,
                    confirmation_call_id=call.call_id,
                    error=result.error,
                )

            if not result.success:
                outcome = StepOutcome(
                    position=step.position,
                    description=step.description,
                    call=call,
                    result=result,
                    verification=None,
                    error=result.error,
                )
                outcomes.append(outcome)
                task.set_status(TaskStatus.FAILED)

                return self._finish(
                    task,
                    plan,
                    WorkflowStatus.FAILED,
                    outcomes,
                    failed_step=step.position,
                    error=result.error,
                )

            verification = self._verifier.verify(
                call,
                result,
            )
            outcome = StepOutcome(
                position=step.position,
                description=step.description,
                call=call,
                result=result,
                verification=verification,
                error=(
                    None
                    if verification.verified
                    else verification.reason
                ),
            )
            outcomes.append(outcome)

            if not verification.verified:
                task.set_status(TaskStatus.FAILED)

                return self._finish(
                    task,
                    plan,
                    WorkflowStatus.FAILED,
                    outcomes,
                    failed_step=step.position,
                    error=verification.reason,
                )

            outputs[step.position] = result.output

            self._publish(
                "agent.step.completed",
                {
                    "task_id": task.task_id,
                    "plan_id": plan.plan_id,
                    "position": step.position,
                    "tool_name": call.tool_name,
                    "verified": True,
                },
            )

        task.set_status(TaskStatus.COMPLETED)

        return self._finish(
            task,
            plan,
            WorkflowStatus.COMPLETED,
            outcomes,
        )

    def _resolved_call(
        self,
        step: PlanStep,
        outputs: dict[int, Any],
    ) -> ToolCall:
        arguments = self._resolve_value(
            step.call.arguments,
            outputs,
            current_position=step.position,
        )

        if not isinstance(arguments, dict):
            raise WorkflowError(
                "Resolved tool arguments must be an object."
            )

        return ToolCall(
            tool_name=step.call.tool_name,
            arguments=arguments,
            task_id=step.call.task_id,
            call_id=step.call.call_id,
            created_at=step.call.created_at,
        )

    def _resolve_value(
        self,
        value: Any,
        outputs: dict[int, Any],
        *,
        current_position: int,
    ) -> Any:
        if (
            isinstance(value, dict)
            and set(value) == {"$ref"}
        ):
            return self._resolve_reference(
                value["$ref"],
                outputs,
                current_position=current_position,
            )

        if isinstance(value, dict):
            return {
                key: self._resolve_value(
                    item,
                    outputs,
                    current_position=current_position,
                )
                for key, item in value.items()
            }

        if isinstance(value, list):
            return [
                self._resolve_value(
                    item,
                    outputs,
                    current_position=current_position,
                )
                for item in value
            ]

        return deepcopy(value)

    @staticmethod
    def _resolve_reference(
        reference: Any,
        outputs: dict[int, Any],
        *,
        current_position: int,
    ) -> Any:
        if not isinstance(reference, str):
            raise WorkflowError(
                "Step reference must be a string."
            )

        parts = reference.split(".")

        if (
            len(parts) < 3
            or parts[0] != "steps"
            or parts[2] != "output"
        ):
            raise WorkflowError(
                f"Invalid step reference: {reference}"
            )

        try:
            position = int(parts[1])
        except ValueError as exc:
            raise WorkflowError(
                f"Invalid step reference: {reference}"
            ) from exc

        if position >= current_position:
            raise WorkflowError(
                "A step can reference only earlier steps."
            )

        if position not in outputs:
            raise WorkflowError(
                f"Referenced step has no verified output: "
                f"{position}"
            )

        resolved = outputs[position]

        for part in parts[3:]:
            if isinstance(resolved, dict):
                if part not in resolved:
                    raise WorkflowError(
                        f"Reference key not found: {part}"
                    )

                resolved = resolved[part]
                continue

            if isinstance(resolved, list):
                try:
                    index = int(part)
                    resolved = resolved[index]
                except (
                    ValueError,
                    IndexError,
                ) as exc:
                    raise WorkflowError(
                        f"Reference list index is invalid: "
                        f"{part}"
                    ) from exc

                continue

            raise WorkflowError(
                f"Reference cannot traverse value at: "
                f"{part}"
            )

        return deepcopy(resolved)

    def _finish(
        self,
        task: AtlasTask,
        plan: ExecutionPlan,
        status: WorkflowStatus,
        outcomes: list[StepOutcome],
        *,
        failed_step: int | None = None,
        confirmation_call_id: str | None = None,
        error: str | None = None,
    ) -> WorkflowResult:
        self._publish(
            f"agent.workflow.{status.value}",
            {
                "task_id": task.task_id,
                "plan_id": plan.plan_id,
                "status": status.value,
                "completed_steps": len(outcomes),
                "failed_step": failed_step,
                "confirmation_call_id": (
                    confirmation_call_id
                ),
                "error": error,
            },
        )

        return WorkflowResult(
            task_id=task.task_id,
            plan_id=plan.plan_id,
            status=status,
            steps=tuple(outcomes),
            failed_step=failed_step,
            confirmation_call_id=(
                confirmation_call_id
            ),
            error=error,
        )

    def _publish(
        self,
        name: str,
        data: dict[str, Any],
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            AtlasEvent(
                name=name,
                source="workflow_runner",
                data=data,
            )
        )
