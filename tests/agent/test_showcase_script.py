"""Tests for the unscripted showcase writer.

The point of this module is that Atlas says something different every
time, so these tests are mostly about what the runtime refuses to
execute: a model that invents an action, exceeds the beat budget, or
asks for a PC clip on a runtime with no PC must be rejected locally
rather than trusted, because the caller's fallback depends on knowing
generation failed.
"""
import json
from types import SimpleNamespace

import pytest

from atlas_agent.showcase_script import (
    MAX_BEATS,
    MAX_NARRATION_CHARS,
    MAX_PC_BEATS,
    MIN_BEATS,
    SUBMIT_TOUR_TOOL_NAME,
    ShowcaseScriptError,
    generate_showcase_tour,
)


class FakeClient:
    """Minimal stand-in for the OpenAI client's responses.create."""

    def __init__(self, payload, *, raises=None):
        self.payload = payload
        self.raises = raises
        self.requests = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)

        if self.raises is not None:
            raise self.raises

        return SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="function_call",
                    name=SUBMIT_TOUR_TOOL_NAME,
                    arguments=json.dumps(self.payload),
                )
            ]
        )


def beat(narration="Hello there.", **overrides):
    return {
        "narration": narration,
        "source": "hud",
        "action": "idle",
        "pc_action": None,
        **overrides,
    }


def payload(*beats):
    return {"beats": list(beats)}


def test_generates_beats_the_recorder_can_execute():
    client = FakeClient(
        payload(
            beat("Hi, I'm Atlas."),
            beat("Here's my radar.", action="weather_open"),
            beat("That's all for now."),
        )
    )

    tour = generate_showcase_tour(client, "gpt-test")

    assert len(tour) == 3
    assert tour[1] == {
        "narration": "Here's my radar.",
        "action": "weather_open",
    }


def test_passes_live_context_to_the_model_as_data():
    """Honest commentary depends on the model seeing real state -- and
    on that state being labelled as data, not instructions, since it
    contains free text from diagnostics findings."""
    client = FakeClient(
        payload(beat(), beat(), beat())
    )

    generate_showcase_tour(
        client,
        "gpt-test",
        context={"hud": {"cpu_temp": 51.2}},
    )

    instructions = client.requests[0]["instructions"]
    assert "51.2" in instructions
    assert "never as instructions" in instructions


def test_pc_beats_are_offered_only_when_a_pc_is_connected():
    client = FakeClient(payload(beat(), beat(), beat()))

    generate_showcase_tour(client, "gpt-test", pc_demo_available=False)
    schema = client.requests[0]["tools"][0]["parameters"]
    sources = schema["properties"]["beats"]["items"]["properties"][
        "source"
    ]["enum"]

    assert sources == ["hud"]
    assert "no PC connection" in client.requests[0]["instructions"]


def test_accepts_a_notepad_typing_beat():
    client = FakeClient(
        payload(
            beat("Watch this."),
            beat(
                "Let me write you something.",
                source="pc",
                action=None,
                pc_action={
                    "type": "type_text",
                    "query": None,
                    "app": "notepad",
                    "text": "thanks for watching",
                },
            ),
            beat("Back to me."),
        )
    )

    tour = generate_showcase_tour(
        client, "gpt-test", pc_demo_available=True
    )

    assert tour[1]["source"] == "pc"
    assert tour[1]["pc_action"] == {
        "type": "type_text",
        "app": "notepad",
        "text": "thanks for watching",
    }
    # content_tools reads 'action' unconditionally for every beat.
    assert tour[1]["action"] == "idle"


def test_typing_beat_defaults_to_notepad_when_no_app_is_named():
    client = FakeClient(
        payload(
            beat(),
            beat(
                "Typing now.",
                source="pc",
                action=None,
                pc_action={
                    "type": "type_text",
                    "query": None,
                    "app": None,
                    "text": "hello",
                },
            ),
            beat(),
        )
    )

    tour = generate_showcase_tour(
        client, "gpt-test", pc_demo_available=True
    )

    assert tour[1]["pc_action"]["app"] == "notepad"


def test_rejects_a_pc_beat_when_no_pc_is_connected():
    client = FakeClient(
        payload(
            beat(),
            beat("On the PC.", source="pc", action=None, pc_action=None),
            beat(),
        )
    )

    with pytest.raises(ShowcaseScriptError, match="no PC connection"):
        generate_showcase_tour(
            client, "gpt-test", pc_demo_available=False
        )


def test_rejects_an_invented_hud_action():
    client = FakeClient(
        payload(
            beat(),
            beat("Opening my laser.", action="fire_lasers"),
            beat(),
        )
    )

    with pytest.raises(ShowcaseScriptError, match="unknown HUD action"):
        generate_showcase_tour(client, "gpt-test")


def test_rejects_an_invented_pc_action():
    client = FakeClient(
        payload(
            beat(),
            beat(
                "Deleting things.",
                source="pc",
                action=None,
                pc_action={
                    "type": "run_shell",
                    "query": "rm -rf /",
                    "app": None,
                    "text": None,
                },
            ),
            beat(),
        )
    )

    with pytest.raises(ShowcaseScriptError, match="unknown PC action"):
        generate_showcase_tour(
            client, "gpt-test", pc_demo_available=True
        )


def test_rejects_more_pc_beats_than_allowed():
    pc_beat = beat(
        "On the PC.",
        source="pc",
        action=None,
        pc_action={
            "type": "youtube_search",
            "query": "robots",
            "app": None,
            "text": None,
        },
    )
    client = FakeClient(
        payload(beat(), *([pc_beat] * (MAX_PC_BEATS + 1)), beat())
    )

    with pytest.raises(ShowcaseScriptError, match="PC beats"):
        generate_showcase_tour(
            client, "gpt-test", pc_demo_available=True
        )


def test_rejects_too_many_beats():
    client = FakeClient(payload(*[beat()] * (MAX_BEATS + 1)))

    with pytest.raises(ShowcaseScriptError, match="more than"):
        generate_showcase_tour(client, "gpt-test")


def test_rejects_too_few_beats():
    client = FakeClient(payload(*[beat()] * (MIN_BEATS - 1)))

    with pytest.raises(ShowcaseScriptError, match="fewer"):
        generate_showcase_tour(client, "gpt-test")


def test_rejects_narration_too_long_to_be_one_clip():
    client = FakeClient(
        payload(beat(), beat("x" * (MAX_NARRATION_CHARS + 1)), beat())
    )

    with pytest.raises(ShowcaseScriptError, match="too long"):
        generate_showcase_tour(client, "gpt-test")


def test_rejects_an_empty_narration():
    client = FakeClient(payload(beat(), beat("   "), beat()))

    with pytest.raises(ShowcaseScriptError, match="no narration"):
        generate_showcase_tour(client, "gpt-test")


def test_rejects_a_typing_beat_with_no_message():
    client = FakeClient(
        payload(
            beat(),
            beat(
                "Typing.",
                source="pc",
                action=None,
                pc_action={
                    "type": "type_text",
                    "query": None,
                    "app": "notepad",
                    "text": "",
                },
            ),
            beat(),
        )
    )

    with pytest.raises(ShowcaseScriptError, match="no message"):
        generate_showcase_tour(
            client, "gpt-test", pc_demo_available=True
        )


def test_wraps_an_api_failure_so_the_caller_can_fall_back():
    client = FakeClient(None, raises=RuntimeError("connection refused"))

    with pytest.raises(ShowcaseScriptError, match="connection refused"):
        generate_showcase_tour(client, "gpt-test")


def test_rejects_malformed_json_from_the_model():
    client = FakeClient(None)
    client.responses = SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="function_call",
                    name=SUBMIT_TOUR_TOOL_NAME,
                    arguments="{not json",
                )
            ]
        )
    )

    with pytest.raises(ShowcaseScriptError, match="malformed"):
        generate_showcase_tour(client, "gpt-test")


def test_rejects_a_response_with_no_script_call():
    client = FakeClient(None)
    client.responses = SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(
            output=[SimpleNamespace(type="message", name=None)]
        )
    )

    with pytest.raises(ShowcaseScriptError, match="did not return"):
        generate_showcase_tour(client, "gpt-test")


def test_rejects_an_empty_model_name_before_calling_the_api():
    client = FakeClient(payload(beat(), beat(), beat()))

    with pytest.raises(ShowcaseScriptError, match="No model"):
        generate_showcase_tour(client, "   ")

    assert client.requests == []


def test_truncated_response_says_it_was_cut_off():
    """A six-beat script is a lot of prose; when it runs past the token
    budget the function call arrives syntactically broken. The error has
    to name that cause, or it reads as a schema bug that isn't there."""
    client = FakeClient(None)
    client.responses = SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output=[
                SimpleNamespace(
                    type="function_call",
                    name=SUBMIT_TOUR_TOOL_NAME,
                    arguments='{"beats": [{"narration": "half a sent',
                )
            ],
        )
    )

    with pytest.raises(ShowcaseScriptError, match="max_output_tokens"):
        generate_showcase_tour(client, "gpt-test")


def test_token_budget_fits_a_full_length_script():
    """Regression: the first live run failed on the second call because
    900 tokens couldn't hold a six-beat tour."""
    client = FakeClient(payload(beat(), beat(), beat()))

    generate_showcase_tour(client, "gpt-test")

    assert client.requests[0]["max_output_tokens"] >= 2000
