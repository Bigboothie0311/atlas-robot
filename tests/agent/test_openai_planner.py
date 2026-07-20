import json
from types import SimpleNamespace

import pytest

from atlas_agent.openai_planner import (
    OpenAIPlanGenerator,
    OpenAIPlanningError,
    SUBMIT_PLAN_TOOL_NAME,
)
from atlas_agent.tools import AtlasTool


ENSURE_SCHEMA = {
    "type": "object",
    "properties": {
        "wake_if_needed": {
            "type": "boolean",
        }
    },
    "required": ["wake_if_needed"],
    "additionalProperties": False,
}


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
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


class FakeResponses:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="response-123",
            output=self.output,
            usage=SimpleNamespace(
                input_tokens=120,
                output_tokens=35,
            ),
        )


class FakeClient:
    def __init__(self, output):
        self.responses = FakeResponses(output)


def make_tool(
    name,
    *,
    parameters=None,
    permission_level=0,
):
    def handler(**_arguments):
        raise AssertionError(
            "The plan generator must never execute tools."
        )

    metadata = {}

    if parameters is not None:
        metadata["parameters"] = parameters

    return AtlasTool(
        name=name,
        description=f"Tool {name}",
        runs_on="pi",
        handler=handler,
        permission_level=permission_level,
        metadata=metadata,
    )


def make_plan_call(steps):
    return SimpleNamespace(
        type="function_call",
        name=SUBMIT_PLAN_TOOL_NAME,
        call_id="call-123",
        arguments=json.dumps(
            {
                "steps": steps,
            }
        ),
    )


def test_generates_strict_tool_specific_plan():
    output = [
        make_plan_call(
            [
                {
                    "tool": "pc.ensure_online",
                    "description": (
                        "Make sure the Windows PC is reachable."
                    ),
                    "arguments": {
                        "wake_if_needed": True,
                    },
                },
                {
                    "tool": "pc.search_files",
                    "description": (
                        "Find matching Atlas files."
                    ),
                    "arguments": {
                        "query": "atlas",
                        "extensions": None,
                        "limit": 5,
                    },
                },
                {
                    "tool": "pc.download_file",
                    "description": (
                        "Download and verify the newest match."
                    ),
                    "arguments": {
                        "remote_path": {
                            "$ref": (
                                "steps.2.output.0.path"
                            )
                        },
                        "local_name": None,
                    },
                },
            ]
        )
    ]
    client = FakeClient(output)
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )
    tools = [
        make_tool(
            "pc.search_files",
            parameters=SEARCH_SCHEMA,
        ),
        make_tool(
            "pc.download_file",
            parameters=DOWNLOAD_SCHEMA,
        ),
        make_tool(
            "pc.ensure_online",
            parameters=ENSURE_SCHEMA,
        ),
    ]

    result = generator.generate(
        "Find my newest Atlas file and bring it to the Pi.",
        tools,
    )

    assert result.response_id == "response-123"
    assert result.input_tokens == 120
    assert result.output_tokens == 35
    assert result.proposal.goal == (
        "Find my newest Atlas file and bring it to the Pi."
    )
    assert [step.tool for step in result.proposal.steps] == [
        "pc.ensure_online",
        "pc.search_files",
        "pc.download_file",
    ]
    assert result.proposal.steps[0].arguments == {
        "wake_if_needed": True,
    }
    assert result.proposal.steps[1].arguments == {
        "query": "atlas",
        "extensions": None,
        "limit": 5,
    }
    assert result.proposal.steps[2].arguments == {
        "remote_path": {
            "$ref": "steps.2.output.0.path",
        },
        "local_name": None,
    }

    request = client.responses.calls[0]
    assert request["model"] == "gpt-test"
    assert request["reasoning"] == {
        "effort": "none",
    }
    assert request["tool_choice"] == {
        "type": "function",
        "name": SUBMIT_PLAN_TOOL_NAME,
    }

    submission_tool = request["tools"][0]
    assert submission_tool["strict"] is True

    item_schema = submission_tool[
        "parameters"
    ]["properties"]["steps"]["items"]
    variants = item_schema["anyOf"]
    variants_by_tool = {
        variant["properties"]["tool"]["enum"][0]: (
            variant
        )
        for variant in variants
    }

    assert set(variants_by_tool) == {
        "pc.download_file",
        "pc.ensure_online",
        "pc.search_files",
    }

    ensure_arguments = variants_by_tool[
        "pc.ensure_online"
    ]["properties"]["arguments"]

    assert ensure_arguments["required"] == [
        "wake_if_needed"
    ]
    assert (
        ensure_arguments["properties"][
            "wake_if_needed"
        ]["anyOf"][0]["type"]
        == "boolean"
    )

    download_arguments = variants_by_tool[
        "pc.download_file"
    ]["properties"]["arguments"]
    remote_path_schema = download_arguments[
        "properties"
    ]["remote_path"]

    assert remote_path_schema["anyOf"][0] == {
        "type": "string",
    }
    assert remote_path_schema["anyOf"][1][
        "properties"
    ]["$ref"]["type"] == "string"


def test_rejects_tool_that_is_not_available():
    output = [
        make_plan_call(
            [
                {
                    "tool": "pc.delete_everything",
                    "description": "Use an invented tool.",
                    "arguments": {},
                }
            ]
        )
    ]
    generator = OpenAIPlanGenerator(
        client=FakeClient(output),
        model="gpt-test",
    )

    with pytest.raises(
        OpenAIPlanningError,
        match="selected an unavailable tool",
    ):
        generator.generate(
            "Delete everything.",
            [make_tool("pc.ensure_online")],
        )


def test_rejects_non_object_step_arguments():
    output = [
        make_plan_call(
            [
                {
                    "tool": "pc.search_files",
                    "description": "Search for a file.",
                    "arguments": "not-an-object",
                }
            ]
        )
    ]
    generator = OpenAIPlanGenerator(
        client=FakeClient(output),
        model="gpt-test",
    )

    with pytest.raises(
        OpenAIPlanningError,
        match="arguments must be an object",
    ):
        generator.generate(
            "Find a file.",
            [make_tool("pc.search_files")],
        )


def test_rejects_response_without_plan_function_call():
    output = [
        SimpleNamespace(
            type="message",
            content=[],
        )
    ]
    generator = OpenAIPlanGenerator(
        client=FakeClient(output),
        model="gpt-test",
    )

    with pytest.raises(
        OpenAIPlanningError,
        match="did not return a submitted agent plan",
    ):
        generator.generate(
            "Check the PC.",
            [make_tool("pc.ensure_online")],
        )


def test_rejects_empty_goal_before_api_call():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    with pytest.raises(
        OpenAIPlanningError,
        match="goal is empty",
    ):
        generator.generate(
            "   ",
            [make_tool("pc.ensure_online")],
        )

    assert client.responses.calls == []


def test_rejects_empty_tool_catalog_before_api_call():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    with pytest.raises(
        OpenAIPlanningError,
        match="No tools",
    ):
        generator.generate(
            "Check the PC.",
            [],
        )

    assert client.responses.calls == []


def test_rejects_schema_that_is_not_strict():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )
    incomplete_schema = {
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
            }
        },
        "required": [],
        "additionalProperties": False,
    }

    with pytest.raises(
        OpenAIPlanningError,
        match="must list every property as required",
    ):
        generator.generate(
            "Open an app.",
            [
                make_tool(
                    "pc.open_app",
                    parameters=incomplete_schema,
                )
            ],
        )

    assert client.responses.calls == []
