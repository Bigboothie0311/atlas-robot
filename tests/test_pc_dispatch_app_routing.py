"""Voice routing for the new/fixed PC app-open and active-window phrases."""
import listen_and_answer


def test_open_spotify_phrase_routes_to_pc_control(monkeypatch):
    calls = []
    monkeypatch.setattr(
        listen_and_answer.pc_control,
        "open_spotify",
        lambda: calls.append("spotify") or "Opening Spotify.",
    )

    result = listen_and_answer._pc_dispatch("open spotify")

    assert result == "Opening Spotify."
    assert calls == ["spotify"]


def test_open_claude_phrase_routes_to_pc_control(monkeypatch):
    monkeypatch.setattr(
        listen_and_answer.pc_control,
        "open_claude",
        lambda: "Opening Claude.",
    )

    assert listen_and_answer._pc_dispatch("open claude") == (
        "Opening Claude."
    )


def test_open_codex_phrase_routes_to_pc_control(monkeypatch):
    monkeypatch.setattr(
        listen_and_answer.pc_control,
        "open_codex",
        lambda: "Opening Codex.",
    )

    assert listen_and_answer._pc_dispatch("open codex") == (
        "Opening Codex."
    )


def test_open_terminal_phrase_routes_to_pc_control(monkeypatch):
    monkeypatch.setattr(
        listen_and_answer.pc_control,
        "open_terminal",
        lambda: "Opening the terminal.",
    )

    assert listen_and_answer._pc_dispatch("open powershell") == (
        "Opening the terminal."
    )


def test_open_browser_phrase_routes_to_pc_control(monkeypatch):
    monkeypatch.setattr(
        listen_and_answer.pc_control,
        "open_browser",
        lambda: "Opening your browser.",
    )

    assert listen_and_answer._pc_dispatch("open my browser") == (
        "Opening your browser."
    )


def test_active_window_phrase_routes_to_pc_control(monkeypatch):
    monkeypatch.setattr(
        listen_and_answer.pc_control,
        "active_window",
        lambda: "You're focused on Fusion 360 on your PC.",
    )

    result = listen_and_answer._pc_dispatch(
        "what's focused on my pc"
    )

    assert result == "You're focused on Fusion 360 on your PC."


def test_unrelated_phrase_is_not_a_pc_command():
    assert listen_and_answer._pc_dispatch("what time is it") is None


def test_take_a_picture_of_my_screen_routes_to_pc_screenshot(monkeypatch):
    monkeypatch.setattr(
        listen_and_answer.pc_control,
        "screenshot_to_hud",
        lambda: "Here's your PC screen.",
    )

    result = listen_and_answer._pc_dispatch("take a picture of my screen")

    assert result == "Here's your PC screen."


def test_take_a_picture_of_the_pc_screen_routes_to_pc_screenshot(monkeypatch):
    monkeypatch.setattr(
        listen_and_answer.pc_control,
        "screenshot_to_hud",
        lambda: "Here's your PC screen.",
    )

    result = listen_and_answer._pc_dispatch("take a picture of the pc screen")

    assert result == "Here's your PC screen."


def test_is_vision_command_does_not_swallow_screen_phrases():
    # Regression: "take a picture of my screen" was matching the Pi's
    # own camera-vision fuzzy rule (words {take, picture}) before
    # _pc_dispatch ever got a chance to route it to the PC screenshot
    # tool, so the Pi took a selfie instead of capturing the PC screen.
    assert listen_and_answer.is_vision_command(
        "take a picture of my screen"
    ) is False
    assert listen_and_answer.is_vision_command(
        "take a picture of the pc screen"
    ) is False
    # Genuine Pi camera requests must still work.
    assert listen_and_answer.is_vision_command("take a picture") is True
    assert listen_and_answer.is_vision_command("what do you see") is True
