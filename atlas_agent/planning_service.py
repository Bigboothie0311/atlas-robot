from __future__ import annotations

from dataclasses import dataclass

from atlas_agent.openai_planner import (
    OpenAIPlanGenerator,
    PlanGenerationResult,
)
from atlas_agent.planner import AgentPlanner, ExecutionPlan
from atlas_agent.tasks import AtlasTask
from atlas_agent.tool_registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ValidatedPlanResult:
    generation: PlanGenerationResult
    plan: ExecutionPlan


class NaturalLanguagePlanningService:
    """Generates a proposal and passes it through local validation.

    The language model can only propose steps. AgentPlanner remains the
    authority for tool existence, routing, limits, and plan construction.
    This service never executes the resulting plan.
    """

    def __init__(
        self,
        generator: OpenAIPlanGenerator,
        planner: AgentPlanner,
        registry: ToolRegistry,
    ) -> None:
        self.generator = generator
        self.planner = planner
        self.registry = registry

    def create_plan(
        self,
        task: AtlasTask,
    ) -> ValidatedPlanResult:
        generation = self.generator.generate(
            task.goal,
            self.registry.list_tools(),
        )

        proposed_steps = [
            {
                "tool": step.tool,
                "description": step.description,
                "arguments": dict(step.arguments),
            }
            for step in generation.proposal.steps
        ]

        plan = self.planner.create_plan(
            task,
            proposed_steps,
        )

        return ValidatedPlanResult(
            generation=generation,
            plan=plan,
        )
