from types import SimpleNamespace

import pytest

from atlas_agent.openai_planner import (
    OpenAIPlanningError,
    PlanGenerationResult,
    PlanProposal,
    PlanStepProposal,
)
from atlas_agent.planner import PlanValidationError
from atlas_agent.planning_service import (
    NaturalLanguagePlanningService,
)
from atlas_agent.tools import AtlasTool


def make_tool(name):
    return AtlasTool(
        name=name,
        description=f"Tool {name}",
        runs_on="pi",
        handler=lambda **_arguments: None,
    )


class FakeRegistry:
    def __init__(self, tools):
        self.tools = list(tools)
        self.calls = 0

    def list_tools(self):
        self.calls += 1
        return list(self.tools)


class FakeGenerator:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def generate(self, goal, available_tools):
        tools = list(available_tools)
        self.calls.append(
            {
                "goal": goal,
                "tools": tools,
            }
        )

        if self.error is not None:
            raise self.error

        return self.result


class FakePlanner:
    def __init__(self, plan=None, error=None):
        self.plan = plan
        self.error = error
        self.calls = []

    def create_plan(self, task, proposed_steps):
        self.calls.append(
            {
                "task": task,
                "proposed_steps": proposed_steps,
            }
        )

        if self.error is not None:
            raise self.error

        return self.plan


def make_generation_result():
    return PlanGenerationResult(
        proposal=PlanProposal(
            goal="Find my newest Atlas file.",
            steps=(
                PlanStepProposal(
                    tool="pc.ensure_online",
                    description="Make sure the PC is online.",
                    arguments={},
                ),
                PlanStepProposal(
                    tool="pc.search_files",
                    description="Find matching files.",
                    arguments={
                        "query": "atlas",
                        "limit": 5,
                    },
                ),
                PlanStepProposal(
                    tool="pc.download_file",
                    description="Download the newest match.",
                    arguments={
                        "remote_path": {
                            "$ref": "steps.2.output.0.path",
                        }
                    },
                ),
            ),
        ),
        response_id="response-123",
        input_tokens=100,
        output_tokens=30,
    )


def test_generated_proposal_is_passed_to_local_planner():
    tools = [
        make_tool("pc.ensure_online"),
        make_tool("pc.search_files"),
        make_tool("pc.download_file"),
    ]
    registry = FakeRegistry(tools)
    generation = make_generation_result()
    generator = FakeGenerator(result=generation)
    validated_plan = object()
    planner = FakePlanner(plan=validated_plan)
    task = SimpleNamespace(
        task_id="task-123",
        goal="Find my newest Atlas file.",
    )
    service = NaturalLanguagePlanningService(
        generator=generator,
        planner=planner,
        registry=registry,
    )

    result = service.create_plan(task)

    assert result.generation is generation
    assert result.plan is validated_plan
    assert registry.calls == 1
    assert generator.calls == [
        {
            "goal": "Find my newest Atlas file.",
            "tools": tools,
        }
    ]
    assert planner.calls == [
        {
            "task": task,
            "proposed_steps": [
                {
                    "tool": "pc.ensure_online",
                    "description": "Make sure the PC is online.",
                    "arguments": {},
                },
                {
                    "tool": "pc.search_files",
                    "description": "Find matching files.",
                    "arguments": {
                        "query": "atlas",
                        "limit": 5,
                    },
                },
                {
                    "tool": "pc.download_file",
                    "description": "Download the newest match.",
                    "arguments": {
                        "remote_path": {
                            "$ref": "steps.2.output.0.path",
                        }
                    },
                },
            ],
        }
    ]


def test_local_plan_rejection_propagates_without_execution():
    registry = FakeRegistry(
        [make_tool("pc.ensure_online")]
    )
    generator = FakeGenerator(
        result=make_generation_result()
    )
    planner = FakePlanner(
        error=PlanValidationError(
            "The proposed plan is not routable."
        )
    )
    task = SimpleNamespace(
        task_id="task-123",
        goal="Do something invalid.",
    )
    service = NaturalLanguagePlanningService(
        generator=generator,
        planner=planner,
        registry=registry,
    )

    with pytest.raises(
        PlanValidationError,
        match="not routable",
    ):
        service.create_plan(task)

    assert len(planner.calls) == 1


def test_generation_failure_never_reaches_local_planner():
    registry = FakeRegistry(
        [make_tool("pc.ensure_online")]
    )
    generator = FakeGenerator(
        error=OpenAIPlanningError(
            "The model did not submit a plan."
        )
    )
    planner = FakePlanner(plan=object())
    task = SimpleNamespace(
        task_id="task-123",
        goal="Check the PC.",
    )
    service = NaturalLanguagePlanningService(
        generator=generator,
        planner=planner,
        registry=registry,
    )

    with pytest.raises(
        OpenAIPlanningError,
        match="did not submit",
    ):
        service.create_plan(task)

    assert planner.calls == []
