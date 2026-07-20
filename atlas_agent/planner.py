from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from atlas_agent.argument_validation import (
    ArgumentValidationError,
    validate_tool_arguments,
)
from atlas_agent.router import (
    ExecutionTarget,
    ToolRouter,
)
from atlas_agent.tasks import (
    AtlasTask,
    TaskStatus,
    ToolCall,
    utc_now,
)
from atlas_agent.tool_registry import ToolRegistry


class PlanValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PlanStep:
    position: int
    description: str
    call: ToolCall
    target: ExecutionTarget


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    task_id: str
    goal: str
    steps: tuple[PlanStep, ...]
    planner_name: str
    plan_id: str = field(
        default_factory=lambda: str(uuid4())
    )
    created_at: str = field(default_factory=utc_now)


class AgentPlanner:
    def __init__(
        self,
        registry: ToolRegistry,
        router: ToolRouter,
        *,
        max_steps: int = 20,
        max_argument_bytes: int = 32768,
        planner_name: str = "validated-agent-planner",
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")

        if max_argument_bytes < 1:
            raise ValueError(
                "max_argument_bytes must be at least 1"
            )

        self._registry = registry
        self._router = router
        self.max_steps = max_steps
        self.max_argument_bytes = max_argument_bytes
        self.planner_name = planner_name

    def tool_catalog(self) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []

        for tool in self._registry.list_tools():
            try:
                target = ExecutionTarget(tool.runs_on)
            except ValueError:
                continue

            parameters = tool.metadata.get("parameters")

            if not isinstance(parameters, dict):
                parameters = {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                }

            catalog.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "runs_on": target.value,
                    "permission_level": (
                        tool.permission_level
                    ),
                    "parameters": parameters,
                }
            )

        return catalog

    def create_plan(
        self,
        task: AtlasTask,
        proposed_steps: Any,
    ) -> ExecutionPlan:
        if task.status is not TaskStatus.QUEUED:
            raise PlanValidationError(
                "Only queued tasks can be planned."
            )

        if not isinstance(proposed_steps, list):
            raise PlanValidationError(
                "Proposed steps must be a list."
            )

        if not proposed_steps:
            raise PlanValidationError(
                "A plan must contain at least one step."
            )

        if len(proposed_steps) > self.max_steps:
            raise PlanValidationError(
                f"Plan exceeds the {self.max_steps}-step limit."
            )

        steps: list[PlanStep] = []

        for index, proposed in enumerate(
            proposed_steps,
            start=1,
        ):
            if not isinstance(proposed, dict):
                raise PlanValidationError(
                    f"Plan step {index} must be an object."
                )

            unknown_fields = set(proposed) - {
                "tool",
                "description",
                "arguments",
            }

            if unknown_fields:
                raise PlanValidationError(
                    f"Plan step {index} contains unknown fields: "
                    f"{sorted(unknown_fields)}"
                )

            tool_name = proposed.get("tool")
            description = proposed.get("description")
            arguments = proposed.get("arguments", {})

            if (
                not isinstance(tool_name, str)
                or not tool_name.strip()
            ):
                raise PlanValidationError(
                    f"Plan step {index} has an invalid tool."
                )

            if (
                not isinstance(description, str)
                or not description.strip()
            ):
                raise PlanValidationError(
                    f"Plan step {index} needs a description."
                )

            if not isinstance(arguments, dict):
                raise PlanValidationError(
                    f"Plan step {index} arguments "
                    "must be an object."
                )

            try:
                encoded_arguments = json.dumps(
                    arguments,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise PlanValidationError(
                    f"Plan step {index} arguments "
                    "must be JSON serializable."
                ) from exc

            if (
                len(encoded_arguments)
                > self.max_argument_bytes
            ):
                raise PlanValidationError(
                    f"Plan step {index} arguments exceed "
                    "the size limit."
                )

            call = ToolCall(
                tool_name=tool_name.strip(),
                arguments=dict(arguments),
                task_id=task.task_id,
            )
            route = self._router.decide(call)

            if not route.ready or route.target is None:
                raise PlanValidationError(
                    f"Plan step {index} cannot be routed: "
                    f"{route.reason}"
                )

            tool = self._registry.get(call.tool_name)
            parameters = tool.metadata.get("parameters")

            if isinstance(parameters, dict):
                try:
                    validate_tool_arguments(
                        call.arguments,
                        parameters,
                    )
                except ArgumentValidationError as exc:
                    raise PlanValidationError(
                        f"Plan step {index} has invalid "
                        f"arguments: {exc}"
                    ) from exc

            steps.append(
                PlanStep(
                    position=index,
                    description=description.strip(),
                    call=call,
                    target=route.target,
                )
            )

        return ExecutionPlan(
            task_id=task.task_id,
            goal=task.goal,
            steps=tuple(steps),
            planner_name=self.planner_name,
        )
