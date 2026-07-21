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
