from __future__ import annotations

from dataclasses import dataclass, replace

from atlas_agent.openai_planner import (
    OpenAIPlanGenerator,
    PlanGenerationResult,
)
from atlas_agent.planner import (
    AgentPlanner,
    ExecutionPlan,
    PlanValidationError,
)
from atlas_agent.tasks import AtlasTask
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool


@dataclass(frozen=True, slots=True)
class ValidatedPlanResult:
    generation: PlanGenerationResult
    plan: ExecutionPlan
    attempts: int
    total_input_tokens: int
    total_output_tokens: int
    validation_errors: tuple[str, ...]


class NaturalLanguagePlanningService:
    """Generate, validate, and optionally repair a proposed plan.

    No tool execution occurs here. If local validation rejects the first
    proposal, one corrected proposal may be requested using the exact
    local validation error as planning feedback.
    """

    def __init__(
        self,
        generator: OpenAIPlanGenerator,
        planner: AgentPlanner,
        registry: ToolRegistry,
        *,
        max_attempts: int = 2,
    ) -> None:
        if max_attempts < 1:
            raise ValueError(
                "max_attempts must be at least 1"
            )

        if max_attempts > 3:
            raise ValueError(
                "max_attempts must not exceed 3"
            )

        self.generator = generator
        self.planner = planner
        self.registry = registry
        self.max_attempts = max_attempts

    def create_plan(
        self,
        task: AtlasTask,
    ) -> ValidatedPlanResult:
        base_tools = self.registry.list_tools()
        planning_tools = list(base_tools)
        validation_errors: list[str] = []
        total_input_tokens = 0
        total_output_tokens = 0

        for attempt in range(1, self.max_attempts + 1):
            generation = self.generator.generate(
                task.goal,
                planning_tools,
            )
            total_input_tokens += generation.input_tokens
            total_output_tokens += generation.output_tokens

            proposed_steps = [
                {
                    "tool": step.tool,
                    "description": step.description,
                    "arguments": dict(step.arguments),
                }
                for step in generation.proposal.steps
            ]

            try:
                plan = self.planner.create_plan(
                    task,
                    proposed_steps,
                )
            except PlanValidationError as error:
                validation_errors.append(str(error))

                if attempt >= self.max_attempts:
                    raise

                planning_tools = self._repair_catalog(
                    base_tools,
                    str(error),
                )
                continue

            return ValidatedPlanResult(
                generation=generation,
                plan=plan,
                attempts=attempt,
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                validation_errors=tuple(validation_errors),
            )

        raise RuntimeError(
            "Planning attempts ended without a plan or error."
        )

    @staticmethod
    def _repair_catalog(
        tools: list[AtlasTool],
        validation_error: str,
    ) -> list[AtlasTool]:
        feedback = validation_error.strip()[:1000]

        return [
            replace(
                tool,
                description=(
                    f"{tool.description} LOCAL VALIDATION "
                    f"FEEDBACK FROM THE PREVIOUS PROPOSAL: "
                    f"{feedback}. Correct the complete plan. "
                    "Use exact parameter names, include every "
                    "required field, use null for nullable fields "
                    "without a value, and add no undeclared fields."
                ),
            )
            for tool in tools
        ]
