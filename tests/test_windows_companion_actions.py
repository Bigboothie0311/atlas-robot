"""Windows companion action tests, run from the Pi.

These exercise the pure whitelisted-action functions with subprocess
mocked out — the real companion only ever runs on Windows, but the
action logic itself is plain Python and testable here.
"""
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

COMPANION_DIR = str(
    Path(__file__).resolve().parent.parent / "windows-companion"
)


@pytest.fixture()
def companion(tmp_path, monkeypatch):
    """Imports atlas_companion pointed at a throwaway config path so no
    test run can ever write into the real windows-companion/ folder."""
    if COMPANION_DIR not in sys.path:
        sys.path.insert(0, COMPANION_DIR)

    if "atlas_companion" in sys.modules:
        del sys.modules["atlas_companion"]

    import atlas_companion as module

    importlib.reload(module)
    monkeypatch.setattr(
        module, "CONFIG_PATH", tmp_path / "companion_config.json"
    )
    module.CONFIG = dict(module.DEFAULT_CONFIG)
    return module


def test_import_does_not_write_a_config_file(companion, tmp_path):
    assert not (tmp_path / "companion_config.json").exists()


def test_focus_or_open_app_launches_when_not_running(
    companion, monkeypatch
):
    companion.CONFIG = {
        **companion.CONFIG,
        "approved_apps": {
            "spotify": {
                "path": r"C:\Spotify\Spotify.exe",
                "match": "Spotify",
            },
        },
    }
    monkeypatch.setattr(
        companion, "_open_window_titles", lambda: ["Chrome", "Explorer"]
    )
    launched = []
    monkeypatch.setattr(
        companion.subprocess,
        "Popen",
        lambda args, **kwargs: launched.append(args),
    )
    focused = []
    monkeypatch.setattr(
        companion, "_focus_window", lambda match: focused.append(match)
    )

    result = companion.act_focus_or_open_app({"app": "spotify"})

    assert result == {
        "ok": True,
        "app": "spotify",
        "action": "launched",
    }
    assert launched == [[r"C:\Spotify\Spotify.exe"]]
    assert focused == []


def test_focus_or_open_app_focuses_when_already_running(
    companion, monkeypatch
):
    companion.CONFIG = {
        **companion.CONFIG,
        "approved_apps": {
            "spotify": {
                "path": r"C:\Spotify\Spotify.exe",
                "match": "Spotify",
            },
        },
    }
    monkeypatch.setattr(
        companion,
        "_open_window_titles",
        lambda: ["Spotify Premium", "Chrome"],
    )
    launched = []
    monkeypatch.setattr(
        companion.subprocess,
        "Popen",
        lambda args, **kwargs: launched.append(args),
    )
    focused = []
    monkeypatch.setattr(
        companion, "_focus_window", lambda match: focused.append(match)
    )

    result = companion.act_focus_or_open_app({"app": "spotify"})

    assert result == {
        "ok": True,
        "app": "spotify",
        "action": "focused",
    }
    assert focused == ["Spotify"]
    assert launched == []


def test_focus_or_open_app_rejects_unapproved_name(companion):
    companion.CONFIG = {**companion.CONFIG, "approved_apps": {}}

    result = companion.act_focus_or_open_app({"app": "chrome_dev"})

    assert result == {
        "ok": False,
        "error": "app 'chrome_dev' not in approved_apps",
    }


def test_focus_or_open_app_rejects_unconfigured_path(companion, monkeypatch):
    companion.CONFIG = {
        **companion.CONFIG,
        "approved_apps": {"codex": {"match": "Codex"}},
    }
    monkeypatch.setattr(companion, "_open_window_titles", lambda: [])

    result = companion.act_focus_or_open_app({"app": "codex"})

    assert result == {
        "ok": False,
        "error": "app 'codex' has no configured path",
    }


def test_active_window_returns_foreground_title(companion, monkeypatch):
    monkeypatch.setattr(
        companion.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="Fusion 360 - ATLAS.f3d\n"),
    )

    result = companion.act_active_window({})

    assert result == {"ok": True, "title": "Fusion 360 - ATLAS.f3d"}


def test_active_window_handles_empty_title(companion, monkeypatch):
    monkeypatch.setattr(
        companion.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="\n"),
    )

    result = companion.act_active_window({})

    assert result == {"ok": True, "title": None}


def test_focus_and_active_window_are_whitelisted_actions(companion):
    assert companion.ACTIONS["focus_or_open_app"] is (
        companion.act_focus_or_open_app
    )
    assert companion.ACTIONS["active_window"] is (
        companion.act_active_window
    )


def test_open_app_still_accepts_legacy_string_paths(companion, monkeypatch):
    """approved_apps entries used to be bare path strings; open_app must
    keep working for anyone who hasn't migrated to the dict form."""
    companion.CONFIG = {
        **companion.CONFIG,
        "approved_apps": {"legacy": r"C:\Legacy\app.exe"},
    }
    launched = []
    monkeypatch.setattr(
        companion.subprocess,
        "Popen",
        lambda args, **kwargs: launched.append(args),
    )

    result = companion.act_open_app({"app": "legacy"})

    assert result == {"ok": True, "opened": "legacy"}
    assert launched == [[r"C:\Legacy\app.exe"]]
