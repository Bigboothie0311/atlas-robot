import json
from types import SimpleNamespace

import pytest

from atlas_agent.openai_planner import (
    OpenAIPlanGenerator,
    OpenAIPlanningError,
    SUBMIT_PLAN_TOOL_NAME,
)
from atlas_agent.tools import AtlasTool


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


def make_tool(name, description="Test tool", permission_level=0):
    def handler(**_arguments):
        raise AssertionError("The plan generator must never execute tools.")

    return AtlasTool(
        name=name,
        description=description,
        runs_on="pi",
        handler=handler,
        permission_level=permission_level,
    )


def make_plan_call(steps):
    return SimpleNamespace(
        type="function_call",
        name=SUBMIT_PLAN_TOOL_NAME,
        call_id="call-123",
        arguments=json.dumps({"steps": steps}),
    )


def test_generates_constrained_plan_without_executing_tools():
    output = [
        make_plan_call(
            [
                {
                    "tool": "pc.ensure_online",
                    "description": "Make sure the Windows PC is reachable.",
                    "arguments_json": "{}",
                },
                {
                    "tool": "pc.search_files",
                    "description": "Find matching Atlas files.",
                    "arguments_json": json.dumps(
                        {
                            "query": "atlas",
                            "limit": 5,
                        }
                    ),
                },
                {
                    "tool": "pc.download_file",
                    "description": "Download and verify the newest match.",
                    "arguments_json": json.dumps(
                        {
                            "remote_path": {
                                "$ref": "steps.2.output.0.path"
                            }
                        }
                    ),
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
        make_tool("pc.search_files", permission_level=1),
        make_tool("pc.download_file", permission_level=1),
        make_tool("pc.ensure_online"),
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
    assert result.proposal.steps[1].arguments == {
        "query": "atlas",
        "limit": 5,
    }
    assert result.proposal.steps[2].arguments == {
        "remote_path": {
            "$ref": "steps.2.output.0.path",
        }
    }

    request = client.responses.calls[0]
    assert request["model"] == "gpt-test"
    assert request["reasoning"] == {"effort": "none"}
    assert request["tool_choice"] == {
        "type": "function",
        "name": SUBMIT_PLAN_TOOL_NAME,
    }
    assert request["tools"][0]["strict"] is True

    tool_enum = request["tools"][0]["parameters"]["properties"][
        "steps"
    ]["items"]["properties"]["tool"]["enum"]

    assert tool_enum == [
        "pc.download_file",
        "pc.ensure_online",
        "pc.search_files",
    ]


def test_rejects_tool_that_is_not_available():
    output = [
        make_plan_call(
            [
                {
                    "tool": "pc.delete_everything",
                    "description": "Use an invented tool.",
                    "arguments_json": "{}",
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


def test_rejects_malformed_step_argument_json():
    output = [
        make_plan_call(
            [
                {
                    "tool": "pc.search_files",
                    "description": "Search for a file.",
                    "arguments_json": "{not valid json",
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
        match="malformed argument JSON",
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


def test_rejects_empty_goal_before_calling_api():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    with pytest.raises(OpenAIPlanningError, match="goal is empty"):
        generator.generate(
            "   ",
            [make_tool("pc.ensure_online")],
        )

    assert client.responses.calls == []


def test_rejects_empty_tool_catalog_before_calling_api():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    with pytest.raises(OpenAIPlanningError, match="No tools"):
        generator.generate(
            "Check the PC.",
            [],
        )

    assert client.responses.calls == []
