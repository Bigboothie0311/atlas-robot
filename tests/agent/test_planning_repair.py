from atlas_agent.openai_planner import (
    PlanGenerationResult,
    PlanProposal,
    PlanStepProposal,
)
from atlas_agent.planner import AgentPlanner
from atlas_agent.planning_service import (
    NaturalLanguagePlanningService,
)
from atlas_agent.router import ToolRouter
from atlas_agent.tasks import AtlasTask
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool


class SequenceGenerator:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def generate(self, goal, available_tools):
        tools = list(available_tools)
        self.calls.append(
            {
                "goal": goal,
                "tools": tools,
            }
        )
        return self.results[len(self.calls) - 1]


def make_generation(arguments, response_id, tokens):
    return PlanGenerationResult(
        proposal=PlanProposal(
            goal="Make sure my PC is online.",
            steps=(
                PlanStepProposal(
                    tool="pc.ensure_online",
                    description="Check and wake the PC.",
                    arguments=arguments,
                ),
            ),
        ),
        response_id=response_id,
        input_tokens=tokens,
        output_tokens=10,
    )


def test_invalid_first_plan_is_repaired_and_revalidated():
    registry = ToolRegistry()

    def forbidden_handler(**_arguments):
        raise AssertionError(
            "Planning must never execute the tool."
        )

    registry.register(
        AtlasTool(
            name="pc.ensure_online",
            description=(
                "Check whether the Windows PC is online."
            ),
            runs_on="pi",
            handler=forbidden_handler,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "wake_if_needed": {
                            "type": "boolean",
                        }
                    },
                    "required": ["wake_if_needed"],
                    "additionalProperties": False,
                }
            },
        )
    )

    generator = SequenceGenerator(
        [
            make_generation(
                {},
                "response-invalid",
                100,
            ),
            make_generation(
                {"wake_if_needed": True},
                "response-corrected",
                120,
            ),
        ]
    )
    planner = AgentPlanner(
        registry,
        ToolRouter(registry),
    )
    service = NaturalLanguagePlanningService(
        generator=generator,
        planner=planner,
        registry=registry,
        max_attempts=2,
    )
    task = AtlasTask(
        goal="Make sure my PC is online.",
        source="voice",
    )

    result = service.create_plan(task)

    assert result.attempts == 2
    assert result.generation.response_id == (
        "response-corrected"
    )
    assert result.total_input_tokens == 220
    assert result.total_output_tokens == 20
    assert len(result.validation_errors) == 1
    assert "wake_if_needed" in result.validation_errors[0]
    assert result.plan.steps[0].call.arguments == {
        "wake_if_needed": True,
    }

    assert len(generator.calls) == 2
    corrected_description = (
        generator.calls[1]["tools"][0].description
    )
    assert "LOCAL VALIDATION FEEDBACK" in (
        corrected_description
    )
    assert "wake_if_needed" in corrected_description
