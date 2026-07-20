import pytest

from atlas_agent.planner import (
    AgentPlanner,
    PlanValidationError,
)
from atlas_agent.router import (
    ExecutionTarget,
    ToolRouter,
)
from atlas_agent.tasks import (
    AtlasTask,
    TaskStatus,
)
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        AtlasTool(
            name="pi.status",
            description="Read A.T.L.A.S. status.",
            runs_on="pi",
            handler=lambda: None,
        )
    )
    registry.register(
        AtlasTool(
            name="pc.search_files",
            description="Search approved Windows folders.",
            runs_on="pc",
            handler=lambda query: query,
            permission_level=0,
            metadata={
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                        }
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                }
            },
        )
    )
    registry.register(
        AtlasTool(
            name="invalid.target",
            description="Invalid target test tool.",
            runs_on="somewhere",
            handler=lambda: None,
        )
    )
    return registry


def make_planner(
    *,
    max_steps: int = 20,
    max_argument_bytes: int = 32768,
) -> AgentPlanner:
    registry = make_registry()

    return AgentPlanner(
        registry,
        ToolRouter(registry),
        max_steps=max_steps,
        max_argument_bytes=max_argument_bytes,
    )


def test_valid_multistep_plan_is_structured() -> None:
    task = AtlasTask(
        goal="Check status and find Atlas files.",
        source="voice",
    )
    planner = make_planner()

    plan = planner.create_plan(
        task,
        [
            {
                "tool": "pi.status",
                "description": "Check robot status.",
                "arguments": {},
            },
            {
                "tool": "pc.search_files",
                "description": "Find Atlas files.",
                "arguments": {
                    "query": "atlas",
                },
            },
        ],
    )

    assert plan.task_id == task.task_id
    assert plan.goal == task.goal
    assert plan.planner_name == (
        "validated-agent-planner"
    )
    assert len(plan.steps) == 2
    assert [
        step.position
        for step in plan.steps
    ] == [1, 2]
    assert [
        step.target
        for step in plan.steps
    ] == [
        ExecutionTarget.PI,
        ExecutionTarget.PC,
    ]
    assert all(
        step.call.task_id == task.task_id
        for step in plan.steps
    )
    assert (
        plan.steps[1].call.arguments["query"]
        == "atlas"
    )


def test_tool_catalog_exposes_only_routable_tools() -> None:
    catalog = make_planner().tool_catalog()

    assert [
        entry["name"]
        for entry in catalog
    ] == [
        "pc.search_files",
        "pi.status",
    ]

    pc_tool = catalog[0]

    assert pc_tool["runs_on"] == "pc"
    assert pc_tool["permission_level"] == 0
    assert (
        pc_tool["parameters"]["required"]
        == ["query"]
    )

    assert catalog[1]["parameters"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


@pytest.mark.parametrize(
    ("tool_name", "expected_error"),
    [
        (
            "missing.tool",
            "not registered",
        ),
        (
            "invalid.target",
            "unsupported execution target",
        ),
    ],
)
def test_unroutable_tools_are_rejected(
    tool_name: str,
    expected_error: str,
) -> None:
    task = AtlasTask(
        goal="Run a tool.",
        source="test",
    )

    with pytest.raises(
        PlanValidationError,
        match=expected_error,
    ):
        make_planner().create_plan(
            task,
            [
                {
                    "tool": tool_name,
                    "description": "Run it.",
                    "arguments": {},
                }
            ],
        )


@pytest.mark.parametrize(
    ("proposed_steps", "expected_error"),
    [
        (
            "not a list",
            "must be a list",
        ),
        (
            [],
            "at least one step",
        ),
        (
            ["not an object"],
            "must be an object",
        ),
        (
            [
                {
                    "tool": "pi.status",
                    "description": "Check.",
                    "arguments": {},
                    "unexpected": True,
                }
            ],
            "unknown fields",
        ),
        (
            [
                {
                    "tool": "",
                    "description": "Check.",
                    "arguments": {},
                }
            ],
            "invalid tool",
        ),
        (
            [
                {
                    "tool": "pi.status",
                    "description": "   ",
                    "arguments": {},
                }
            ],
            "needs a description",
        ),
        (
            [
                {
                    "tool": "pi.status",
                    "description": "Check.",
                    "arguments": [],
                }
            ],
            "must be an object",
        ),
        (
            [
                {
                    "tool": "pi.status",
                    "description": "Check.",
                    "arguments": {
                        "invalid": object(),
                    },
                }
            ],
            "JSON serializable",
        ),
    ],
)
def test_malformed_plans_are_rejected(
    proposed_steps,
    expected_error: str,
) -> None:
    task = AtlasTask(
        goal="Test malformed plan.",
        source="test",
    )

    with pytest.raises(
        PlanValidationError,
        match=expected_error,
    ):
        make_planner().create_plan(
            task,
            proposed_steps,
        )


def test_plan_step_limit_is_enforced() -> None:
    task = AtlasTask(
        goal="Too many steps.",
        source="test",
    )
    proposed = [
        {
            "tool": "pi.status",
            "description": f"Step {number}.",
            "arguments": {},
        }
        for number in range(3)
    ]

    with pytest.raises(
        PlanValidationError,
        match="2-step limit",
    ):
        make_planner(
            max_steps=2
        ).create_plan(task, proposed)


def test_argument_size_limit_is_enforced() -> None:
    task = AtlasTask(
        goal="Oversized arguments.",
        source="test",
    )

    with pytest.raises(
        PlanValidationError,
        match="size limit",
    ):
        make_planner(
            max_argument_bytes=10
        ).create_plan(
            task,
            [
                {
                    "tool": "pc.search_files",
                    "description": "Search.",
                    "arguments": {
                        "query": "a very long query",
                    },
                }
            ],
        )


def test_nonqueued_task_cannot_be_planned() -> None:
    task = AtlasTask(
        goal="Already running.",
        source="test",
    )
    task.set_status(TaskStatus.RUNNING)

    with pytest.raises(
        PlanValidationError,
        match="Only queued tasks",
    ):
        make_planner().create_plan(
            task,
            [
                {
                    "tool": "pi.status",
                    "description": "Check.",
                    "arguments": {},
                }
            ],
        )


def test_invalid_planner_limits_are_rejected() -> None:
    registry = make_registry()
    router = ToolRouter(registry)

    with pytest.raises(
        ValueError,
        match="max_steps must be at least 1",
    ):
        AgentPlanner(
            registry,
            router,
            max_steps=0,
        )

    with pytest.raises(
        ValueError,
        match="max_argument_bytes must be at least 1",
    ):
        AgentPlanner(
            registry,
            router,
            max_argument_bytes=0,
        )
