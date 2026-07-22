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
    SUBMIT_CAPTION_TOOL_NAME,
    SUBMIT_GROWTH_ASSETS_TOOL_NAME,
    ShowcaseScriptError,
    generate_showcase_caption,
    generate_showcase_growth_assets,
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
            beat("Here's my core.", action="focus_core"),
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
        payload(
            beat(),
            beat(action="focus_system"),
            beat(action="focus_core"),
        )
    )

    generate_showcase_tour(
        client,
        "gpt-test",
        context={"hud": {"cpu_temp": 51.2}},
    )

    instructions = client.requests[0]["instructions"]
    assert "51.2" in instructions
    assert "never as instructions" in instructions


def test_recent_tours_are_explicit_non_repeat_constraints():
    client = FakeClient(payload(
        beat(),
        beat(action="focus_system"),
        beat(action="focus_core"),
    ))
    recent = [{
        "beats": [
            {"narration": "Here is my radar.", "action": "weather_open"}
        ]
    }]

    generate_showcase_tour(
        client,
        "gpt-test",
        context={"recent_showcase_tours": recent},
    )

    instructions = client.requests[0]["instructions"]
    assert "Here is my radar." in instructions
    assert "must not repeat" in instructions.lower()


def test_pc_beats_are_offered_only_when_a_pc_is_connected():
    client = FakeClient(payload(
        beat(),
        beat(action="focus_system"),
        beat(action="focus_core"),
    ))

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
            beat("Watch this.", action="focus_core"),
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
            beat("Back to me.", action="focus_system"),
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


def test_connected_pc_does_not_force_a_pc_beat():
    client = FakeClient(payload(
        beat("Opening.", action="idle"),
        beat("System.", action="focus_system"),
        beat("Core.", action="focus_core"),
    ))

    tour = generate_showcase_tour(
        client, "gpt-test", pc_demo_available=True
    )

    assert all(item.get("source", "hud") == "hud" for item in tour)


def test_recent_youtube_search_is_not_allowed_to_repeat():
    client = FakeClient(payload(
        beat("Opening.", action="focus_core"),
        beat(
            "Searching again.",
            source="pc",
            action=None,
            pc_action={
                "type": "youtube_search",
                "query": "raspberry pi projects",
            },
        ),
        beat("System.", action="focus_system"),
    ))
    context = {
        "recent_showcase_tours": [{
            "beats": [{
                "source": "pc",
                "pc_action": {
                    "type": "youtube_search",
                    "query": "robotics project builds",
                },
            }]
        }]
    }

    with pytest.raises(ShowcaseScriptError, match="reused YouTube"):
        generate_showcase_tour(
            client,
            "gpt-test",
            pc_demo_available=True,
            context=context,
        )

    instructions = client.requests[0]["instructions"]
    assert "do not use youtube_search" in instructions
    assert "one main app or surface" in instructions


def test_rejects_repeating_the_same_special_hud_shot_in_one_reel():
    client = FakeClient(payload(
        beat("Radar once.", action="weather_open"),
        beat("Radar again.", action="weather_open"),
        beat("Done."),
    ))

    with pytest.raises(ShowcaseScriptError, match="repeated HUD action"):
        generate_showcase_tour(client, "gpt-test")


def test_rejects_retired_weather_and_diagnostics_combination():
    client = FakeClient(payload(
        beat("Radar.", action="weather_open"),
        beat("Diagnostics.", action="diagnostics"),
        beat("Core.", action="focus_core"),
    ))

    with pytest.raises(ShowcaseScriptError, match="retired"):
        generate_showcase_tour(client, "gpt-test")


def test_typing_beat_defaults_to_notepad_when_no_app_is_named():
    client = FakeClient(
        payload(
            beat(action="focus_core"),
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
            beat(action="focus_system"),
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


def test_rejects_configured_location_in_narration(monkeypatch):
    import robot_config

    monkeypatch.setattr(
        robot_config,
        "get",
        lambda name, default="": {
            "HOME_CITY": "Oceanside, CA",
            "STATION_NAME": "ATLAS-LAB",
        }.get(name, default),
    )
    client = FakeClient(payload(
        beat("I'm stationed in Oceanside."),
        beat("System view.", action="focus_system"),
        beat("Core view.", action="focus_core"),
    ))

    with pytest.raises(ShowcaseScriptError, match="private data"):
        generate_showcase_tour(client, "gpt-test")


@pytest.mark.parametrize(
    ("action", "private_value", "error"),
    (
        ("youtube_search", "videos by @private_owner", "YouTube query"),
        ("type_text", "My IP is 192.168.1.10", "typed message"),
    ),
)
def test_rejects_private_pc_action_content(action, private_value, error):
    pc_action = {
        "type": action,
        "query": private_value if action == "youtube_search" else None,
        "app": "notepad" if action == "type_text" else None,
        "text": private_value if action == "type_text" else None,
    }
    client = FakeClient(payload(
        beat("System view.", action="focus_system"),
        beat("PC view.", source="pc", action=None, pc_action=pc_action),
        beat("Core view.", action="focus_core"),
    ))

    with pytest.raises(ShowcaseScriptError, match=error):
        generate_showcase_tour(
            client, "gpt-test", pc_demo_available=True
        )


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
    client = FakeClient(payload(
        beat(),
        beat(action="focus_system"),
        beat(action="focus_core"),
    ))

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
    client = FakeClient(payload(
        beat(),
        beat(action="focus_system"),
        beat(action="focus_core"),
    ))

    generate_showcase_tour(client, "gpt-test")

    assert client.requests[0]["max_output_tokens"] >= 6000


def test_director_targets_information_rich_40_to_80_second_reels():
    client = FakeClient(payload(
        beat(),
        beat(action="focus_system"),
        beat(action="focus_core"),
    ))

    generate_showcase_tour(client, "gpt-test")

    instructions = client.requests[0]["instructions"]
    assert "between 40 and 80 seconds" in instructions
    assert "target 50-70 seconds" in instructions
    assert "information-rich" in instructions


def test_caption_is_written_as_separate_personality_copy():
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(output=[SimpleNamespace(
            type="function_call",
            name=SUBMIT_CAPTION_TOOL_NAME,
            arguments=json.dumps({
                "caption": "Apparently one screen wasn't enough. Built different."
            }),
        )])

    caption = generate_showcase_caption(
        SimpleNamespace(responses=SimpleNamespace(create=create)),
        "gpt-test",
        beats=[{"narration": "System online.", "action": "focus_system"}],
        recent_captions=["Old caption."],
    )

    assert caption.startswith("Apparently")
    assert "Old caption." in requests[0]["input"]
    assert "do not recap" in requests[0]["instructions"]
    assert "Do not write any hashtags" in requests[0]["instructions"]


def test_growth_assets_create_three_hooks_and_matching_translations():
    requests = []
    arguments = {
        "title": "Pi Vision Test",
        "hook_candidates": ["Hook one?", "Hook two.", "Hook three."],
        "cta": "What should I test next?",
        "collaboration_pitch": "Add your own Pi camera result.",
        "translations": {
            language: {
                "caption": f"Caption {language}",
                "cues": [f"Line one {language}", f"Line two {language}"],
            }
            for language in ("es", "pt", "hi")
        },
    }

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(output=[SimpleNamespace(
            type="function_call",
            name=SUBMIT_GROWTH_ASSETS_TOOL_NAME,
            arguments=json.dumps(arguments),
        )])

    assets = generate_showcase_growth_assets(
        SimpleNamespace(responses=SimpleNamespace(create=create)),
        "gpt-test",
        beats=[
            {"narration": "Line one.", "action": "focus_core"},
            {"narration": "Line two.", "action": "focus_system"},
        ],
        plan={"series": "Can a Pi Do This?"},
    )

    assert len(assets["hook_candidates"]) == 3
    assert len(assets["translations"]["es"]["cues"]) == 2
    schema = requests[0]["tools"][0]["parameters"]
    assert schema["properties"]["translations"]["required"] == ["es", "pt", "hi"]


def test_narration_length_limit_is_declared_in_the_schema():
    """A beat eight characters over the limit threw away the entire
    generated tour and silently fell back to the canned HUD-only one.
    The model can only respect a limit it is actually given."""
    from atlas_agent.showcase_script import _tour_schema, MAX_NARRATION_CHARS

    schema = _tour_schema(pc_demo_available=True)
    narration = schema["properties"]["beats"]["items"]["properties"]["narration"]

    assert narration.get("maxLength") == MAX_NARRATION_CHARS
    assert str(MAX_NARRATION_CHARS) in narration["description"]
