import pytest

from atlas_agent.planner import (
    AgentPlanner,
    PlanValidationError,
)
from atlas_agent.router import ToolRouter
from atlas_agent.tasks import AtlasTask
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "maxLength": 200,
        },
        "extensions": {
            "type": ["array", "null"],
            "items": {
                "type": "string",
            },
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
        },
    },
    "required": [
        "query",
        "extensions",
        "limit",
    ],
    "additionalProperties": False,
}


DOWNLOAD_SCHEMA = {
    "type": "object",
    "properties": {
        "remote_path": {
            "type": "string",
        },
        "local_name": {
            "type": ["string", "null"],
        },
    },
    "required": [
        "remote_path",
        "local_name",
    ],
    "additionalProperties": False,
}


def make_planner(
    tool_name,
    schema,
    *,
    runs_on="pc",
):
    registry = ToolRegistry()
    registry.register(
        AtlasTool(
            name=tool_name,
            description=f"Test tool {tool_name}",
            runs_on=runs_on,
            handler=lambda **_arguments: None,
            metadata={
                "parameters": schema,
            },
        )
    )

    return AgentPlanner(
        registry,
        ToolRouter(registry),
    )


def make_task():
    return AtlasTask(
        goal="Test validated planning.",
        source="test",
    )


def test_planner_accepts_valid_registered_arguments():
    planner = make_planner(
        "pc.search_files",
        SEARCH_SCHEMA,
    )

    plan = planner.create_plan(
        make_task(),
        [
            {
                "tool": "pc.search_files",
                "description": "Search for Atlas files.",
                "arguments": {
                    "query": "Atlas",
                    "extensions": None,
                    "limit": 20,
                },
            }
        ],
    )

    assert plan.steps[0].call.arguments == {
        "query": "Atlas",
        "extensions": None,
        "limit": 20,
    }


def test_planner_rejects_unknown_sort_argument():
    planner = make_planner(
        "pc.search_files",
        SEARCH_SCHEMA,
    )

    with pytest.raises(
        PlanValidationError,
        match="invalid arguments.*unknown fields.*sort",
    ):
        planner.create_plan(
            make_task(),
            [
                {
                    "tool": "pc.search_files",
                    "description": "Search for Atlas files.",
                    "arguments": {
                        "query": "Atlas",
                        "extensions": None,
                        "limit": 1,
                        "sort": "modified_desc",
                    },
                }
            ],
        )


def test_planner_rejects_missing_required_arguments():
    planner = make_planner(
        "pc.search_files",
        SEARCH_SCHEMA,
    )

    with pytest.raises(
        PlanValidationError,
        match="missing required fields.*extensions",
    ):
        planner.create_plan(
            make_task(),
            [
                {
                    "tool": "pc.search_files",
                    "description": "Search for Atlas files.",
                    "arguments": {
                        "query": "Atlas",
                        "limit": 1,
                    },
                }
            ],
        )


def test_planner_rejects_wrong_download_field_name():
    planner = make_planner(
        "pc.download_file",
        DOWNLOAD_SCHEMA,
        runs_on="pi",
    )

    with pytest.raises(
        PlanValidationError,
        match="missing required fields.*remote_path",
    ):
        planner.create_plan(
            make_task(),
            [
                {
                    "tool": "pc.download_file",
                    "description": "Download the file.",
                    "arguments": {
                        "file_path": {
                            "$ref": "steps.1.output.0.path",
                        },
                        "local_name": None,
                    },
                }
            ],
        )


def test_planner_accepts_deferred_reference_for_string_field():
    planner = make_planner(
        "pc.download_file",
        DOWNLOAD_SCHEMA,
        runs_on="pi",
    )

    plan = planner.create_plan(
        make_task(),
        [
            {
                "tool": "pc.download_file",
                "description": "Download the matched file.",
                "arguments": {
                    "remote_path": {
                        "$ref": "steps.1.output.0.path",
                    },
                    "local_name": None,
                },
            }
        ],
    )

    assert plan.steps[0].call.arguments["remote_path"] == {
        "$ref": "steps.1.output.0.path",
    }
