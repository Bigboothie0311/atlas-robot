"""pc_control.py Pi-side companion client tests.

_call is monkeypatched so no real network I/O happens. These pin the
real bug fixed in this milestone: open_spotify/open_claude used to POST
to companion actions ("open_spotify"/"open_claude") that don't exist
in atlas_companion.ACTIONS, so they always failed against a real
companion.
"""
import pc_control


def test_open_spotify_uses_focus_or_open_app_action(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pc_control,
        "_call",
        lambda action, body=None, timeout=25: (
            calls.append((action, body)),
            (True, {"ok": True, "app": "spotify", "action": "launched"}),
        )[1],
    )

    result = pc_control.open_spotify()

    assert calls == [("focus_or_open_app", {"app": "spotify"})]
    assert result == "Opening Spotify."


def test_open_claude_uses_focus_or_open_app_action(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pc_control,
        "_call",
        lambda action, body=None, timeout=25: (
            calls.append((action, body)),
            (True, {"ok": True, "app": "claude", "action": "launched"}),
        )[1],
    )

    result = pc_control.open_claude()

    assert calls == [("focus_or_open_app", {"app": "claude"})]
    assert result == "Opening Claude."


def test_focus_or_open_app_speaks_focus_when_already_open(monkeypatch):
    monkeypatch.setattr(
        pc_control,
        "_call",
        lambda action, body=None, timeout=25: (
            True,
            {"ok": True, "app": "codex", "action": "focused"},
        ),
    )

    result = pc_control.open_codex()

    assert result == (
        "Codex is already open — bringing it to the front."
    )


def test_focus_or_open_app_reports_companion_error(monkeypatch):
    monkeypatch.setattr(
        pc_control,
        "_call",
        lambda action, body=None, timeout=25: (
            False,
            "app 'terminal' not in approved_apps",
        ),
    )

    result = pc_control.open_terminal()

    assert result == "app 'terminal' not in approved_apps"


def test_open_fusion_no_longer_duplicates_windows(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pc_control,
        "_call",
        lambda action, body=None, timeout=25: (
            calls.append((action, body)),
            (True, {"ok": True, "app": "fusion", "action": "focused"}),
        )[1],
    )

    result = pc_control.open_fusion()

    assert calls == [("focus_or_open_app", {"app": "fusion"})]
    assert result == (
        "Fusion 360 is already open — bringing it to the front."
    )


def test_open_browser_uses_focus_or_open_app(monkeypatch):
    monkeypatch.setattr(
        pc_control,
        "_call",
        lambda action, body=None, timeout=25: (
            True,
            {"ok": True, "app": "browser", "action": "launched"},
        ),
    )

    assert pc_control.open_browser() == "Opening your browser."


def test_active_window_speaks_the_title(monkeypatch):
    monkeypatch.setattr(
        pc_control,
        "_call",
        lambda action, body=None, timeout=25: (
            True,
            {"ok": True, "title": "Fusion 360 - ATLAS.f3d"},
        ),
    )

    result = pc_control.active_window()

    assert result == (
        "You're focused on Fusion 360 - ATLAS.f3d on your PC."
    )


def test_active_window_handles_no_focused_window(monkeypatch):
    monkeypatch.setattr(
        pc_control,
        "_call",
        lambda action, body=None, timeout=25: (
            True,
            {"ok": True, "title": None},
        ),
    )

    result = pc_control.active_window()

    assert result == (
        "I can't tell what's focused on your PC right now."
    )


def test_active_window_reports_companion_error(monkeypatch):
    monkeypatch.setattr(
        pc_control,
        "_call",
        lambda action, body=None, timeout=25: (
            False,
            "I couldn't reach your PC. Is it on and the companion running?",
        ),
    )

    result = pc_control.active_window()

    assert "couldn't reach" in result


def test_only_one_definition_of_open_spotify_and_open_claude():
    """Pins the real bug: the original file had two definitions of
    open_spotify/open_claude, and the second (broken) one silently
    shadowed the first at import time."""
    import inspect

    source = inspect.getsource(pc_control)

    assert source.count("def open_spotify(") == 1
    assert source.count("def open_claude(") == 1
