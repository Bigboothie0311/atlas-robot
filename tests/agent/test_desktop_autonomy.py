import json
from types import SimpleNamespace

from atlas_agent.desktop_autonomy import (
    ACTION_ARGUMENT_GUIDE,
    DesktopAutonomyAgent,
    _normalize_action_arguments,
    register_desktop_autonomy_tool,
)
from atlas_agent.pc_client import PCActionResult
from atlas_agent.tasks import ToolCall
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.verifier import ResultVerifier


def result(action, data):
    return PCActionResult(
        action=action,
        ok=bool(data.get("ok")),
        data=data,
        error=None,
        started_at="start",
        finished_at="finish",
        duration_ms=1,
    )


class FakePC:
    def __init__(self):
        self.calls = []

    def execute(self, action, arguments=None, **kwargs):
        self.calls.append((action, arguments, kwargs))
        if action == "observe_desktop":
            return result(action, {
                "ok": True,
                "image_b64": "aW1hZ2U=",
                "active_window": "Desktop",
                "cursor": {"x": 0, "y": 0},
            })
        return result(action, {"ok": True, "action": arguments.get("action")})


class FakeResponses:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        decision = self.decisions.pop(0)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            output=[SimpleNamespace(
                type="function_call",
                name="submit_desktop_step",
                arguments=json.dumps(decision),
            )],
        )


def test_desktop_agent_observes_acts_and_verifies_completion():
    responses = FakeResponses([
        {
            "status": "act",
            "action": "input",
            "arguments_json": json.dumps({"action": "click", "x": 4, "y": 8}),
            "summary": "Click the button.",
        },
        {
            "status": "complete",
            "action": None,
            "arguments_json": "{}",
            "summary": "The button is active.",
        },
    ])
    client = SimpleNamespace(responses=responses)
    pc = FakePC()

    output = DesktopAutonomyAgent(client, "gpt-test", pc).run(
        "Activate the visible button", max_steps=4
    )

    assert output["ok"] is True
    assert output["completed"] is True
    assert output["step_count"] == 1
    assert output["summary"] == "The button is active."
    assert [call[0] for call in pc.calls] == [
        "observe_desktop", "desktop_input", "observe_desktop"
    ]
    assert responses.requests[0]["input"][0]["content"][1]["type"] == "input_image"


def test_desktop_agent_stops_at_step_budget():
    responses = FakeResponses([
        {
            "status": "act",
            "action": "input",
            "arguments_json": json.dumps(
                {"action": "keys", "keys": "{TAB}"}
            ),
            "summary": "Keep moving.",
        },
        {
            "status": "act",
            "action": "input",
            "arguments_json": json.dumps(
                {"action": "keys", "keys": "{TAB}"}
            ),
            "summary": "Still not done.",
        },
    ])

    output = DesktopAutonomyAgent(
        SimpleNamespace(responses=responses), "gpt-test", FakePC()
    ).run("Find something", max_steps=1)

    assert output["ok"] is False
    assert output["step_count"] == 1


def test_desktop_agent_can_verify_after_its_last_permitted_action():
    responses = FakeResponses([
        {
            "status": "act",
            "action": "launch",
            "arguments_json": json.dumps({"path": "mspaint.exe"}),
            "summary": "Open Paint.",
        },
        {
            "status": "complete",
            "action": None,
            "arguments_json": "{}",
            "summary": "Paint is visibly open.",
        },
    ])
    pc = FakePC()

    output = DesktopAutonomyAgent(
        SimpleNamespace(responses=responses), "gpt-test", pc
    ).run("Open Paint", max_steps=1)

    assert output["ok"] is True
    assert output["step_count"] == 1
    assert pc.calls[1][0] == "launch_process"
    assert pc.calls[1][1] == {
        "executable": "mspaint.exe",
        "arguments": [],
    }
    assert "ACTION BUDGET EXHAUSTED" in (
        responses.requests[1]["input"][0]["content"][0]["text"]
    )


def test_normalizes_argument_aliases_seen_in_live_companion_log():
    assert _normalize_action_arguments(
        "input", {"keys": "%{TAB}"}
    ) == {"keys": "%{TAB}", "action": "keys"}
    assert _normalize_action_arguments(
        "window", {"mode": "list"}
    ) == {"action": "list"}
    assert _normalize_action_arguments(
        "window", {"title": "Atlas", "state": "maximize"}
    ) == {"title": "Atlas", "action": "maximize"}
    assert _normalize_action_arguments(
        "launch", {"command": "mspaint.exe"}
    ) == {"executable": "mspaint.exe", "arguments": []}


def test_desktop_prompt_contains_exact_companion_contracts():
    assert '{"action":"list"}' in ACTION_ARGUMENT_GUIDE
    assert '"executable":"mspaint.exe"' in ACTION_ARGUMENT_GUIDE
    assert "Never relaunch an application" in ACTION_ARGUMENT_GUIDE


def test_registers_logged_autonomous_desktop_tool():
    registry = ToolRegistry()
    verifier = ResultVerifier()

    tool = register_desktop_autonomy_tool(
        registry,
        verifier,
        client=SimpleNamespace(),
        model="gpt-test",
        pc_client=FakePC(),
    )

    assert tool.name == "pc.autonomous_desktop"
    assert tool.permission_level == 1
    assert registry.get(tool.name) is tool
    assert tool.metadata["parameters"]["required"] == ["goal", "max_steps"]


def test_desktop_prompt_teaches_the_drag_contract():
    """The agent can only use an action it is told about. Without drag it
    opened Paint, clicked at a canvas that never changed, and stalled."""
    from atlas_agent.desktop_autonomy import ACTION_ARGUMENT_GUIDE

    assert '"action":"drag"' in ACTION_ARGUMENT_GUIDE
    assert '"path"' in ACTION_ARGUMENT_GUIDE
    # It must be explicit that clicking cannot draw, or the model will
    # keep reaching for the click it already knows.
    assert "click" in ACTION_ARGUMENT_GUIDE.lower()
    assert "drag" in ACTION_ARGUMENT_GUIDE.lower()


def test_repeated_failing_action_is_called_out_to_the_model():
    """Live failure: focus returned ok:false every time, and the agent
    spent its entire step budget on focus -> list -> focus -> list
    instead of drawing. A repeat of a known-failed action must be named
    explicitly so the model changes approach."""
    from atlas_agent.desktop_autonomy import DesktopAutonomyAgent

    prompts = []

    class Responses:
        def create(self, **kwargs):
            prompts.append(kwargs["input"][0]["content"][0]["text"])
            return SimpleNamespace(
                output=[SimpleNamespace(
                    type="function_call",
                    name="submit_desktop_step",
                    arguments=json.dumps({
                        "status": "act",
                        "action": "window",
                        "arguments_json": json.dumps(
                            {"action": "focus", "title": "Untitled - Paint"}
                        ),
                        "summary": "focus paint",
                    }),
                )],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    class FailingFocusPC:
        def execute(self, action, arguments=None, timeout_seconds=None):
            if action == "observe_desktop":
                return SimpleNamespace(
                    ok=True,
                    data={"ok": True, "image_b64": "x", "active_window": "Other",
                          "windows": ["Untitled - Paint"], "cursor": {}},
                    error=None,
                )
            return SimpleNamespace(
                ok=True, data={"ok": False, "action": "focus"}, error=None
            )

    DesktopAutonomyAgent(
        SimpleNamespace(responses=Responses()), "gpt-test", FailingFocusPC()
    ).run("Draw in Paint", max_steps=4)

    later = " ".join(prompts[2:]).lower()
    assert "already failed" in later or "repeat" in later, later
