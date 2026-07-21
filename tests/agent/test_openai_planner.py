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

def test_routes_atlas_project_listing_to_pi_without_api_call():
    client = FakeClient(
        [
            make_plan_call(
                [
                    {
                        "tool": "pc.search_files",
                        "description": (
                            "Incorrectly search Windows."
                        ),
                        "arguments": {
                            "query": "atlas",
                            "extensions": None,
                            "limit": 20,
                        },
                    }
                ]
            )
        ]
    )
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )
    list_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
            },
        },
        "required": [
            "path",
            "limit",
        ],
        "additionalProperties": False,
    }

    result = generator.generate(
        (
            "List all the files in the Atlas Robot "
            "project folder."
        ),
        [
            make_tool(
                "pc.search_files",
                parameters=SEARCH_SCHEMA,
            ),
            make_tool(
                "pi.list_directory",
                parameters=list_schema,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.response_id is None
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert len(result.proposal.steps) == 1
    assert result.proposal.steps[0].tool == (
        "pi.list_directory"
    )
    assert result.proposal.steps[0].arguments == {
        "path": "/home/atlas/atlas-robot",
        "limit": 200,
    }



def test_routes_explicit_pi_text_file_read_without_api_call():
    client = FakeClient(
        [
            make_plan_call(
                [
                    {
                        "tool": "pc.search_files",
                        "description": (
                            "Incorrectly search Windows."
                        ),
                        "arguments": {
                            "query": "robot_hub.py",
                            "extensions": [".py"],
                            "limit": 20,
                        },
                    }
                ]
            )
        ]
    )
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )
    read_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
            },
            "start_line": {
                "type": "integer",
                "minimum": 1,
            },
            "max_lines": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50_000,
            },
        },
        "required": [
            "path",
            "start_line",
            "max_lines",
            "max_chars",
        ],
        "additionalProperties": False,
    }

    result = generator.generate(
        (
            "Read the file "
            "/home/atlas/atlas-robot/robot_hub.py."
        ),
        [
            make_tool(
                "pc.search_files",
                parameters=SEARCH_SCHEMA,
            ),
            make_tool(
                "pi.read_text_file",
                parameters=read_schema,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.response_id is None
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert len(result.proposal.steps) == 1
    assert result.proposal.steps[0].tool == (
        "pi.read_text_file"
    )
    assert result.proposal.steps[0].arguments == {
        "path": (
            "/home/atlas/atlas-robot/robot_hub.py"
        ),
        "start_line": 1,
        "max_lines": 200,
        "max_chars": 12_000,
    }


SEARCH_FILES_SCHEMA = {
    "type": "object",
    "properties": {
        "root": {"type": "string"},
        "query": {"type": "string"},
        "extensions": {
            "type": ["array", "null"],
            "items": {"type": "string"},
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
        },
    },
    "required": [
        "root",
        "query",
        "extensions",
        "limit",
    ],
    "additionalProperties": False,
}


SEARCH_TEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "root": {"type": "string"},
        "query": {"type": "string"},
        "extensions": {
            "type": ["array", "null"],
            "items": {"type": "string"},
        },
        "case_sensitive": {"type": "boolean"},
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
        },
    },
    "required": [
        "root",
        "query",
        "extensions",
        "case_sensitive",
        "limit",
    ],
    "additionalProperties": False,
}


SERVICE_LOGS_SCHEMA = {
    "type": "object",
    "properties": {
        "service": {"type": "string"},
        "minutes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1440,
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 500,
        },
    },
    "required": ["service", "minutes", "limit"],
    "additionalProperties": False,
}


SERVICE_STATUS_SCHEMA = {
    "type": "object",
    "properties": {
        "service": {"type": "string"},
    },
    "required": ["service"],
    "additionalProperties": False,
}


def test_routes_pi_search_files_without_api_call():
    client = FakeClient(
        [
            make_plan_call(
                [
                    {
                        "tool": "pc.search_files",
                        "description": (
                            "Incorrectly search Windows."
                        ),
                        "arguments": {
                            "query": "Instagram",
                            "extensions": None,
                            "limit": 20,
                        },
                    }
                ]
            )
        ]
    )
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        (
            "Hey Atlas, search your project for "
            "files named Instagram."
        ),
        [
            make_tool(
                "pc.search_files",
                parameters=SEARCH_SCHEMA,
            ),
            make_tool(
                "pi.search_files",
                parameters=SEARCH_FILES_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.response_id is None
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert len(result.proposal.steps) == 1
    assert result.proposal.steps[0].tool == (
        "pi.search_files"
    )
    assert result.proposal.steps[0].arguments == {
        "root": "/home/atlas/atlas-robot",
        "query": "Instagram",
        "extensions": None,
        "limit": 50,
    }


def test_routes_pi_search_text_without_api_call():
    client = FakeClient(
        [
            make_plan_call(
                [
                    {
                        "tool": "pc.search_files",
                        "description": (
                            "Incorrectly search Windows."
                        ),
                        "arguments": {
                            "query": "atlas-wake.service",
                            "extensions": None,
                            "limit": 20,
                        },
                    }
                ]
            )
        ]
    )
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Search your code for atlas-wake.service.",
        [
            make_tool(
                "pc.search_files",
                parameters=SEARCH_SCHEMA,
            ),
            make_tool(
                "pi.search_text",
                parameters=SEARCH_TEXT_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.response_id is None
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert len(result.proposal.steps) == 1
    assert result.proposal.steps[0].tool == (
        "pi.search_text"
    )
    assert result.proposal.steps[0].arguments == {
        "root": "/home/atlas/atlas-robot",
        "query": "atlas-wake.service",
        "extensions": None,
        "case_sensitive": False,
        "limit": 50,
    }


def test_routes_pi_read_service_logs_without_api_call():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        (
            "Read your wake logs from the last "
            "ten minutes."
        ),
        [
            make_tool(
                "pi.read_service_logs",
                parameters=SERVICE_LOGS_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.response_id is None
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert len(result.proposal.steps) == 1
    assert result.proposal.steps[0].tool == (
        "pi.read_service_logs"
    )
    assert result.proposal.steps[0].arguments == {
        "service": "atlas-wake.service",
        "minutes": 10,
        "limit": 200,
    }


def test_routes_pi_get_service_status_without_api_call():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Is your wake service running?",
        [
            make_tool(
                "pi.get_service_status",
                parameters=SERVICE_STATUS_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.response_id is None
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert len(result.proposal.steps) == 1
    assert result.proposal.steps[0].tool == (
        "pi.get_service_status"
    )
    assert result.proposal.steps[0].arguments == {
        "service": "atlas-wake.service",
    }


def test_ambiguous_multi_service_status_falls_through_to_planning():
    client = FakeClient(
        [
            make_plan_call(
                [
                    {
                        "tool": "pi.get_service_status",
                        "description": (
                            "Check the robot hub status."
                        ),
                        "arguments": {
                            "service": "atlas-robot.service",
                        },
                    }
                ]
            )
        ]
    )
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        (
            "Check whether your robot hub and HUD "
            "are active."
        ),
        [
            make_tool(
                "pi.get_service_status",
                parameters=SERVICE_STATUS_SCHEMA,
            ),
        ],
    )

    assert len(client.responses.calls) == 1
    assert result.response_id == "response-123"
    assert result.proposal.steps[0].tool == (
        "pi.get_service_status"
    )


def test_windows_video_request_not_hijacked_by_new_rules():
    client = FakeClient(
        [
            make_plan_call(
                [
                    {
                        "tool": "pc.search_files",
                        "description": (
                            "Search Windows for the video."
                        ),
                        "arguments": {
                            "query": "atlas",
                            "extensions": None,
                            "limit": 20,
                        },
                    }
                ]
            )
        ]
    )
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Find my Atlas video on the PC.",
        [
            make_tool(
                "pc.search_files",
                parameters=SEARCH_SCHEMA,
            ),
            make_tool(
                "pi.search_files",
                parameters=SEARCH_FILES_SCHEMA,
            ),
            make_tool(
                "pi.search_text",
                parameters=SEARCH_TEXT_SCHEMA,
            ),
        ],
    )

    assert len(client.responses.calls) == 1
    assert result.proposal.steps[0].tool == (
        "pc.search_files"
    )


UPGRADE_STATUS_SCHEMA = {
    "type": "object",
    "properties": {
        "scope": {
            "type": "string",
            "enum": [
                "summary",
                "finished",
                "remaining",
                "blocked",
            ],
        },
    },
    "required": ["scope"],
    "additionalProperties": False,
}


def test_routes_pi_get_upgrade_status_summary_without_api_call():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "What is your upgrade status?",
        [
            make_tool(
                "pi.get_upgrade_status",
                parameters=UPGRADE_STATUS_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.response_id is None
    assert len(result.proposal.steps) == 1
    assert result.proposal.steps[0].tool == (
        "pi.get_upgrade_status"
    )
    assert result.proposal.steps[0].arguments == {
        "scope": "summary",
    }


def test_routes_pi_get_upgrade_status_finished_scope():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "What upgrades are finished so far?",
        [
            make_tool(
                "pi.get_upgrade_status",
                parameters=UPGRADE_STATUS_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.proposal.steps[0].arguments == {
        "scope": "finished",
    }


def test_routes_pi_get_upgrade_status_blocked_scope():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "What upgrades are blocked right now?",
        [
            make_tool(
                "pi.get_upgrade_status",
                parameters=UPGRADE_STATUS_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.proposal.steps[0].arguments == {
        "scope": "blocked",
    }


def test_routes_pi_get_upgrade_status_remaining_scope():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "What upgrades are left on the roadmap?",
        [
            make_tool(
                "pi.get_upgrade_status",
                parameters=UPGRADE_STATUS_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.proposal.steps[0].arguments == {
        "scope": "remaining",
    }


def test_upgrade_status_not_hijacked_without_tool_available():
    client = FakeClient(
        [
            make_plan_call(
                [
                    {
                        "tool": "pi.get_service_status",
                        "description": (
                            "Check the wake service instead."
                        ),
                        "arguments": {
                            "service": "atlas-wake.service",
                        },
                    }
                ]
            )
        ]
    )
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "What is your upgrade status?",
        [
            make_tool(
                "pi.get_service_status",
                parameters=SERVICE_STATUS_SCHEMA,
            ),
        ],
    )

    assert len(client.responses.calls) == 1
    assert result.proposal.steps[0].tool == (
        "pi.get_service_status"
    )


MISSION_HISTORY_SCHEMA = {
    "type": "object",
    "properties": {
        "scope": {
            "type": "string",
            "enum": ["last", "recent", "failed"],
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
        },
    },
    "required": ["scope", "limit"],
    "additionalProperties": False,
}

EXPLAIN_FAILURE_SCHEMA = {
    "type": "object",
    "properties": {
        "window": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
        },
    },
    "required": ["window"],
    "additionalProperties": False,
}


def test_routes_pi_get_mission_history_last_scope():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "What was your last mission?",
        [
            make_tool(
                "pi.get_mission_history",
                parameters=MISSION_HISTORY_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.response_id is None
    assert len(result.proposal.steps) == 1
    assert result.proposal.steps[0].tool == (
        "pi.get_mission_history"
    )
    assert result.proposal.steps[0].arguments == {
        "scope": "last",
        "limit": 5,
    }


def test_routes_pi_get_mission_history_recent_scope():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Show me your recent mission history",
        [
            make_tool(
                "pi.get_mission_history",
                parameters=MISSION_HISTORY_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.proposal.steps[0].arguments == {
        "scope": "recent",
        "limit": 5,
    }


def test_routes_pi_get_mission_history_failed_scope():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Did any recent missions fail?",
        [
            make_tool(
                "pi.get_mission_history",
                parameters=MISSION_HISTORY_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.proposal.steps[0].arguments == {
        "scope": "failed",
        "limit": 5,
    }


def test_routes_pi_explain_last_failure_from_why_question():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Check your logs and tell me why the last command failed",
        [
            make_tool(
                "pi.explain_last_failure",
                parameters=EXPLAIN_FAILURE_SCHEMA,
            ),
            make_tool(
                "pi.get_mission_history",
                parameters=MISSION_HISTORY_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.response_id is None
    assert len(result.proposal.steps) == 1
    assert result.proposal.steps[0].tool == (
        "pi.explain_last_failure"
    )
    assert result.proposal.steps[0].arguments == {
        "window": 25,
    }


def test_routes_pi_explain_last_failure_over_mission_history():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Why did the last mission fail?",
        [
            make_tool(
                "pi.explain_last_failure",
                parameters=EXPLAIN_FAILURE_SCHEMA,
            ),
            make_tool(
                "pi.get_mission_history",
                parameters=MISSION_HISTORY_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.proposal.steps[0].tool == (
        "pi.explain_last_failure"
    )


def test_routes_pi_explain_last_failure_what_went_wrong():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "What went wrong earlier?",
        [
            make_tool(
                "pi.explain_last_failure",
                parameters=EXPLAIN_FAILURE_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.proposal.steps[0].tool == (
        "pi.explain_last_failure"
    )


RUN_DIAGNOSTICS_SCHEMA = {
    "type": "object",
    "properties": {
        "components": {
            "type": ["array", "null"],
            "items": {"type": "string"},
        },
    },
    "required": ["components"],
    "additionalProperties": False,
}


RECOVER_COMPONENT_SCHEMA = {
    "type": "object",
    "properties": {
        "component": {
            "type": "string",
        },
    },
    "required": ["component"],
    "additionalProperties": False,
}


def test_routes_pi_run_diagnostics_without_api_call():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Atlas, run diagnostics",
        [
            make_tool(
                "pi.run_diagnostics",
                parameters=RUN_DIAGNOSTICS_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.response_id is None
    assert len(result.proposal.steps) == 1
    assert result.proposal.steps[0].tool == (
        "pi.run_diagnostics"
    )
    assert result.proposal.steps[0].arguments == {
        "components": None,
    }


def test_routes_pi_run_diagnostics_from_health_check():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Give me a full system health check",
        [
            make_tool(
                "pi.run_diagnostics",
                parameters=RUN_DIAGNOSTICS_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.proposal.steps[0].tool == (
        "pi.run_diagnostics"
    )


def test_routes_pi_run_diagnostics_from_check_your_systems():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Check your systems please",
        [
            make_tool(
                "pi.run_diagnostics",
                parameters=RUN_DIAGNOSTICS_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.proposal.steps[0].tool == (
        "pi.run_diagnostics"
    )


def test_routes_pi_recover_component_audio():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Fix the microphone audio",
        [
            make_tool(
                "pi.recover_component",
                parameters=RECOVER_COMPONENT_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.proposal.steps[0].tool == (
        "pi.recover_component"
    )
    assert result.proposal.steps[0].arguments == {
        "component": "audio",
    }


def test_routes_pi_recover_component_hud_restart():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Restart the HUD",
        [
            make_tool(
                "pi.recover_component",
                parameters=RECOVER_COMPONENT_SCHEMA,
            ),
            make_tool(
                "pi.get_service_status",
                parameters=SERVICE_STATUS_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.proposal.steps[0].tool == (
        "pi.recover_component"
    )
    assert result.proposal.steps[0].arguments == {
        "component": "hud",
    }


def test_routes_pi_recover_component_printer_hub():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Repair the printer hub",
        [
            make_tool(
                "pi.recover_component",
                parameters=RECOVER_COMPONENT_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.proposal.steps[0].arguments == {
        "component": "printer_hub",
    }


def test_hud_status_question_not_hijacked_by_recovery():
    client = FakeClient([])
    generator = OpenAIPlanGenerator(
        client=client,
        model="gpt-test",
    )

    result = generator.generate(
        "Is the hud service running?",
        [
            make_tool(
                "pi.recover_component",
                parameters=RECOVER_COMPONENT_SCHEMA,
            ),
            make_tool(
                "pi.get_service_status",
                parameters=SERVICE_STATUS_SCHEMA,
            ),
        ],
    )

    assert client.responses.calls == []
    assert result.proposal.steps[0].tool == (
        "pi.get_service_status"
    )
