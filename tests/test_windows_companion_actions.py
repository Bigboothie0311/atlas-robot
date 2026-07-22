"""Windows companion action tests, run from the Pi.

These exercise the pure whitelisted-action functions with subprocess
mocked out — the real companion only ever runs on Windows, but the
action logic itself is plain Python and testable here.
"""
import importlib
import json
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
    runs = []
    monkeypatch.setattr(
        companion.subprocess,
        "run",
        lambda *a, **k: runs.append(k)
        or SimpleNamespace(stdout="Fusion 360 - ATLAS.f3d\n"),
    )

    result = companion.act_active_window({})

    assert result == {"ok": True, "title": "Fusion 360 - ATLAS.f3d"}
    assert runs[0]["creationflags"] == companion._NO_WINDOW


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


def test_capture_screenshot_saves_file_and_sidecar(companion, monkeypatch, tmp_path):
    recordings = tmp_path / "recordings"
    companion.CONFIG = {**companion.CONFIG, "recordings_folder": str(recordings)}
    monkeypatch.setattr(
        companion, "act_active_window", lambda _body: {"ok": True, "title": "Chrome"}
    )

    def fake_powershell_run(command, **kwargs):
        recordings.mkdir(parents=True, exist_ok=True)
        # Extract the target path baked into the script (last quoted path).
        script = command[-1]
        target = script.rsplit("'", 2)[-2]
        Path(target).write_bytes(b"fake png bytes")
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(companion.subprocess, "run", fake_powershell_run)

    result = companion.act_capture_screenshot({"mission": "showcase"})

    assert result["ok"] is True
    assert result["mission"] == "showcase"
    assert result["window"] == "Chrome"
    saved = Path(result["path"])
    assert saved.is_file()
    sidecar = Path(str(saved) + ".json")
    assert sidecar.is_file()
    assert json.loads(sidecar.read_text())["kind"] == "screenshot"


def test_capture_screenshot_refuses_privacy_blocked_window(companion, monkeypatch):
    monkeypatch.setattr(
        companion, "act_active_window", lambda _body: {"ok": True, "title": "Gmail - Inbox"}
    )

    result = companion.act_capture_screenshot({})

    assert result["ok"] is False
    assert "privacy-blocked" in result["error"]


def test_capture_window_refuses_privacy_blocked_title(companion):
    result = companion.act_capture_window({"window_title": "1Password"})

    assert result == {
        "ok": False,
        "error": "privacy-blocked window requested: 1Password",
    }


def test_capture_window_reports_no_match(companion, monkeypatch):
    monkeypatch.setattr(
        companion.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="NO_MATCH\n", returncode=0),
    )

    result = companion.act_capture_window({"window_title": "Fusion 360"})

    assert result == {
        "ok": False,
        "error": "no open window matched 'Fusion 360'",
    }


def test_start_recording_refuses_second_concurrent_recording(companion, monkeypatch, tmp_path):
    monkeypatch.setattr(companion, "_recording_state_path", lambda: tmp_path / "state.json")
    companion._save_recording_state({"active": {"pid": 123, "path": "x.mp4"}})

    result = companion.act_start_recording({})

    assert result == {"ok": False, "error": "a recording is already in progress"}


def test_start_recording_launches_ffmpeg_and_saves_state(companion, monkeypatch, tmp_path):
    recordings = tmp_path / "recordings"
    companion.CONFIG = {**companion.CONFIG, "recordings_folder": str(recordings)}
    monkeypatch.setattr(companion, "_recording_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(
        companion, "act_active_window", lambda _body: {"ok": True, "title": "Desktop"}
    )
    launched = []

    class FakeProcess:
        pid = 4242

    monkeypatch.setattr(
        companion.subprocess, "Popen", lambda args, **kwargs: launched.append(args) or FakeProcess()
    )

    result = companion.act_start_recording({"mission": "demo", "max_seconds": 30})

    assert result["ok"] is True
    assert result["pid"] == 4242
    assert result["mission"] == "demo"
    assert result["max_seconds"] == 30
    assert launched[0][0] == "ffmpeg"
    state = companion._load_recording_state()
    assert state["active"]["pid"] == 4242


def test_start_recording_hides_ffmpeg_console(companion, monkeypatch, tmp_path):
    companion.CONFIG = {
        **companion.CONFIG,
        "recordings_folder": str(tmp_path / "recordings"),
    }
    monkeypatch.setattr(
        companion, "_recording_state_path", lambda: tmp_path / "state.json"
    )
    monkeypatch.setattr(
        companion,
        "act_active_window",
        lambda _body: {"ok": True, "title": "Notepad"},
    )
    launches = []

    class FakeProcess:
        pid = 4242

    monkeypatch.setattr(
        companion.subprocess,
        "Popen",
        lambda args, **kwargs: launches.append((args, kwargs)) or FakeProcess(),
    )

    result = companion.act_start_recording({"max_seconds": 5})

    assert result["ok"] is True
    assert launches[0][1]["creationflags"] == companion._NO_WINDOW


def test_start_recording_uses_broadly_compatible_encoder_flags(companion, monkeypatch, tmp_path):
    """gdigrab's default codec choice isn't guaranteed to be playable by
    Windows' stock players -- the command must pin libx264/yuv420p/
    faststart explicitly rather than relying on ffmpeg's default."""
    companion.CONFIG = {**companion.CONFIG, "recordings_folder": str(tmp_path / "recordings")}
    monkeypatch.setattr(companion, "_recording_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(
        companion, "act_active_window", lambda _body: {"ok": True, "title": "Desktop"}
    )
    launched = []

    class FakeProcess:
        pid = 1

    monkeypatch.setattr(
        companion.subprocess, "Popen", lambda args, **kwargs: launched.append(args) or FakeProcess()
    )

    companion.act_start_recording({})

    command = launched[0]
    assert "-c:v" in command and command[command.index("-c:v") + 1] == "libx264"
    assert "-pix_fmt" in command and command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert "-movflags" in command and command[command.index("-movflags") + 1] == "+faststart"


def test_start_recording_caps_max_seconds_to_config_ceiling(companion, monkeypatch, tmp_path):
    companion.CONFIG = {
        **companion.CONFIG,
        "recordings_folder": str(tmp_path / "recordings"),
        "max_recording_seconds": 60,
    }
    monkeypatch.setattr(companion, "_recording_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(
        companion, "act_active_window", lambda _body: {"ok": True, "title": "Desktop"}
    )

    class FakeProcess:
        pid = 1

    monkeypatch.setattr(companion.subprocess, "Popen", lambda args, **kwargs: FakeProcess())

    result = companion.act_start_recording({"max_seconds": 99999})

    assert result["max_seconds"] == 60


def test_stop_recording_verifies_file_and_clears_state(companion, monkeypatch, tmp_path):
    monkeypatch.setattr(companion, "_recording_state_path", lambda: tmp_path / "state.json")
    media = tmp_path / "recording.mp4"
    media.write_bytes(b"video bytes")
    companion._save_recording_state({
        "active": {
            "pid": 999, "path": str(media), "name": "recording.mp4",
            "mission": None, "target": "full", "window_title": None,
            "privacy": False, "max_seconds": 30, "started_at": "now",
        }
    })
    monkeypatch.setattr(companion, "_pid_running", lambda pid: False)

    result = companion.act_stop_recording({})

    assert result["ok"] is True
    assert result["size_bytes"] == len(b"video bytes")
    assert companion._load_recording_state()["active"] is None
    assert Path(str(media) + ".json").is_file()


def test_stop_recording_kills_process_still_running(companion, monkeypatch, tmp_path):
    monkeypatch.setattr(companion, "_recording_state_path", lambda: tmp_path / "state.json")
    media = tmp_path / "recording.mp4"
    media.write_bytes(b"video bytes")
    companion._save_recording_state({
        "active": {"pid": 555, "path": str(media), "name": "recording.mp4"}
    })
    monkeypatch.setattr(companion, "_pid_running", lambda pid: True)
    monkeypatch.setattr(companion.time, "sleep", lambda _seconds: None)
    killed = []
    monkeypatch.setattr(
        companion.subprocess, "run", lambda args, **kwargs: killed.append(args)
    )

    result = companion.act_stop_recording({})

    assert result["ok"] is True
    # _pid_running is stubbed True throughout, so the escalation runs to
    # the end. Plain "taskkill /PID" posts WM_CLOSE, which a windowless
    # ffmpeg never receives, so /F must be reached rather than assumed
    # unnecessary.
    assert killed == [
        ["taskkill", "/PID", "555", "/T"],
        ["taskkill", "/PID", "555", "/T", "/F"],
    ]


def test_stop_recording_reports_no_active_recording(companion, monkeypatch, tmp_path):
    monkeypatch.setattr(companion, "_recording_state_path", lambda: tmp_path / "state.json")

    result = companion.act_stop_recording({})

    assert result == {"ok": False, "error": "no recording is in progress"}


def test_list_recordings_returns_sidecars_newest_first(companion, tmp_path):
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    companion.CONFIG = {**companion.CONFIG, "recordings_folder": str(recordings)}
    (recordings / "a.mp4").write_bytes(b"a")
    (recordings / "a.mp4.json").write_text(json.dumps({"path": str(recordings / "a.mp4"), "name": "a.mp4"}))
    (recordings / "b.mp4").write_bytes(b"b")
    (recordings / "b.mp4.json").write_text(json.dumps({"path": str(recordings / "b.mp4"), "name": "b.mp4"}))

    result = companion.act_list_recordings({})

    assert result["ok"] is True
    assert {item["name"] for item in result["recordings"]} == {"a.mp4", "b.mp4"}
    assert all(item["exists"] for item in result["recordings"])


def test_list_recordings_empty_folder(companion, tmp_path):
    companion.CONFIG = {**companion.CONFIG, "recordings_folder": str(tmp_path / "missing")}

    result = companion.act_list_recordings({})

    assert result == {"ok": True, "recordings": []}


def test_reconcile_orphaned_recording_finalizes_dead_process(companion, monkeypatch, tmp_path):
    monkeypatch.setattr(companion, "_recording_state_path", lambda: tmp_path / "state.json")
    media = tmp_path / "orphan.mp4"
    media.write_bytes(b"orphaned bytes")
    companion._save_recording_state({
        "active": {"pid": 111, "path": str(media), "name": "orphan.mp4"}
    })
    monkeypatch.setattr(companion, "_pid_running", lambda pid: False)

    companion._reconcile_orphaned_recording()

    assert companion._load_recording_state()["active"] is None
    sidecar = json.loads(Path(str(media) + ".json").read_text())
    assert sidecar["orphaned"] is True


def test_reconcile_leaves_genuinely_active_recording_alone(companion, monkeypatch, tmp_path):
    monkeypatch.setattr(companion, "_recording_state_path", lambda: tmp_path / "state.json")
    companion._save_recording_state({
        "active": {"pid": 222, "path": str(tmp_path / "still_going.mp4"), "name": "still_going.mp4"}
    })
    monkeypatch.setattr(companion, "_pid_running", lambda pid: True)

    companion._reconcile_orphaned_recording()

    assert companion._load_recording_state()["active"]["pid"] == 222


def test_new_capture_actions_are_whitelisted(companion):
    for action in (
        "capture_screenshot", "capture_window",
        "start_recording", "stop_recording", "list_recordings",
    ):
        assert action in companion.ACTIONS


def test_pc_power_and_recycle_bin_actions_are_whitelisted(companion):
    for action in ("shutdown_pc", "cancel_pc_shutdown", "empty_recycle_bin", "youtube_search"):
        assert action in companion.ACTIONS


def test_private_youtube_search_uses_a_fresh_private_browser(
    companion, monkeypatch
):
    runs = []
    popens = []
    monkeypatch.setattr(
        companion.subprocess,
        "run",
        lambda args, **kwargs: runs.append(args)
        or SimpleNamespace(returncode=0, stderr=""),
    )
    monkeypatch.setattr(
        companion.subprocess,
        "Popen",
        lambda args, **kwargs: popens.append(args),
    )

    result = companion.act_youtube_search({
        "query": "robotics builds",
        "private": True,
        "fullscreen": False,
    })

    assert result == {"ok": True, "query": "robotics builds"}
    assert len(runs) == 1
    script = runs[0][-1]
    assert "--inprivate" in script
    assert "--incognito" in script
    assert "youtube.com/results" in script
    assert popens == []


def test_private_youtube_search_fails_closed_without_a_private_browser(
    companion, monkeypatch
):
    monkeypatch.setattr(
        companion.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stderr="No private-capable browser found.",
        ),
    )

    result = companion.act_youtube_search({
        "query": "robotics builds",
        "private": True,
        "fullscreen": False,
    })

    assert result == {
        "ok": False,
        "error": "No private-capable browser found.",
    }


def test_shutdown_pc_schedules_delayed_shutdown(companion, monkeypatch):
    calls = []
    monkeypatch.setattr(
        companion.subprocess, "run",
        lambda args, **kwargs: calls.append(args) or SimpleNamespace(returncode=0, stderr=""),
    )

    result = companion.act_shutdown_pc({})

    assert result == {"ok": True}
    assert calls == [["shutdown", "/s", "/t", "60"]]


def test_shutdown_pc_reports_failure(companion, monkeypatch):
    monkeypatch.setattr(
        companion.subprocess, "run",
        lambda args, **kwargs: SimpleNamespace(returncode=1, stderr="access denied"),
    )

    result = companion.act_shutdown_pc({})

    assert result == {"ok": False, "error": "access denied"}


def test_cancel_pc_shutdown_runs_shutdown_abort(companion, monkeypatch):
    calls = []
    monkeypatch.setattr(
        companion.subprocess, "run",
        lambda args, **kwargs: calls.append(args) or SimpleNamespace(returncode=0, stderr=""),
    )

    result = companion.act_cancel_pc_shutdown({})

    assert result == {"ok": True}
    assert calls == [["shutdown", "/a"]]


def test_cancel_pc_shutdown_reports_no_pending_shutdown(companion, monkeypatch):
    monkeypatch.setattr(
        companion.subprocess, "run",
        lambda args, **kwargs: SimpleNamespace(returncode=1, stderr=""),
    )

    result = companion.act_cancel_pc_shutdown({})

    assert result == {"ok": False, "error": "no shutdown was pending"}


def test_empty_recycle_bin_runs_powershell_clear(companion, monkeypatch):
    calls = []
    monkeypatch.setattr(
        companion.subprocess, "run",
        lambda args, **kwargs: calls.append(args) or SimpleNamespace(returncode=0, stderr=""),
    )

    result = companion.act_empty_recycle_bin({})

    assert result == {"ok": True}
    assert calls[0][0] == "powershell"
    assert "Clear-RecycleBin" in calls[0][-1]


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


def _typing_companion(companion, monkeypatch, *, foreground="Untitled - Notepad"):
    """Sets up a companion whose Notepad is already open and focused, and
    records every PowerShell command type_text issues."""
    companion.CONFIG = {
        **companion.CONFIG,
        "approved_apps": {
            "notepad": {"path": "notepad.exe", "match": "Notepad"},
        },
    }
    monkeypatch.setattr(
        companion, "_open_window_titles", lambda: [foreground]
    )
    monkeypatch.setattr(companion, "_focus_window", lambda match: None)
    monkeypatch.setattr(
        companion, "act_active_window", lambda _body: {
            "ok": True, "title": foreground,
        }
    )
    monkeypatch.setattr(companion.time, "sleep", lambda _seconds: None)

    commands = []
    monkeypatch.setattr(
        companion.subprocess,
        "run",
        lambda args, **kwargs: commands.append(args)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    return commands


def test_type_text_sends_the_message_to_the_focused_app(
    companion, monkeypatch
):
    commands = _typing_companion(companion, monkeypatch)

    result = companion.act_type_text(
        {"app": "notepad", "text": "hey viewers"}
    )

    assert result["ok"] is True
    assert result["app"] == "notepad"
    assert result["characters"] == len("hey viewers")
    script = commands[-1][-1]
    assert "SendKeys" in script
    assert "hey vi" in script


def test_type_text_refuses_an_app_outside_the_whitelist(
    companion, monkeypatch
):
    _typing_companion(companion, monkeypatch)

    result = companion.act_type_text({"app": "solitaire", "text": "hi"})

    assert result["ok"] is False
    assert "approved_apps" in result["error"]


def test_type_text_refuses_when_another_window_stole_focus(
    companion, monkeypatch
):
    """The whole safety story of type_text is that keystrokes only ever
    land in the approved window that was asked for -- if focus moved
    between the AppActivate and the send, it must refuse, not type the
    message into whatever is actually there."""
    commands = _typing_companion(
        companion, monkeypatch, foreground="Online Banking - Chrome"
    )
    monkeypatch.setattr(
        companion, "_open_window_titles", lambda: ["Untitled - Notepad"]
    )

    result = companion.act_type_text(
        {"app": "notepad", "text": "hey viewers"}
    )

    assert result["ok"] is False
    assert commands == []


def test_type_text_refuses_a_privacy_blocked_foreground_window(
    companion, monkeypatch
):
    commands = _typing_companion(
        companion, monkeypatch, foreground="1Password - Notepad"
    )

    result = companion.act_type_text({"app": "notepad", "text": "hi"})

    assert result["ok"] is False
    assert "privacy-blocked" in result["error"]
    assert commands == []


def test_type_text_refuses_text_over_the_configured_limit(
    companion, monkeypatch
):
    commands = _typing_companion(companion, monkeypatch)
    companion.CONFIG = {**companion.CONFIG, "max_type_text_chars": 10}

    result = companion.act_type_text(
        {"app": "notepad", "text": "x" * 11}
    )

    assert result["ok"] is False
    assert "10-character limit" in result["error"]
    assert commands == []


def test_type_text_refuses_control_characters(companion, monkeypatch):
    commands = _typing_companion(companion, monkeypatch)

    result = companion.act_type_text(
        {"app": "notepad", "text": "hi\x1b[2Jthere"}
    )

    assert result["ok"] is False
    assert "control characters" in result["error"]
    assert commands == []


def test_type_text_escapes_sendkeys_command_characters(
    companion, monkeypatch
):
    """A bare '+' or '%' in the message is a SendKeys modifier, not a
    character -- unescaped it would swallow the next keystroke instead
    of appearing on screen."""
    commands = _typing_companion(companion, monkeypatch)

    result = companion.act_type_text(
        {"app": "notepad", "text": "100% (up)"}
    )

    assert result["ok"] is True
    script = commands[-1][-1]
    assert "{%}" in script
    assert "{(}" in script
    assert "{)}" in script


def test_type_text_turns_newlines_into_enter_keys(companion, monkeypatch):
    commands = _typing_companion(companion, monkeypatch)

    result = companion.act_type_text(
        {"app": "notepad", "text": "line one\nline two"}
    )

    assert result["ok"] is True
    assert "{ENTER}" in commands[-1][-1]


def test_type_text_paces_keystrokes_to_the_requested_duration(
    companion, monkeypatch
):
    """The Reel syncs typing to the beat's narration length, so a longer
    duration must produce a slower per-chunk sleep."""
    commands = _typing_companion(companion, monkeypatch)
    text = "a message for the viewers at home"

    companion.act_type_text(
        {"app": "notepad", "text": text, "duration_seconds": 20}
    )
    slow = commands[-1][-1]

    companion.act_type_text(
        {"app": "notepad", "text": text, "duration_seconds": 5}
    )
    fast = commands[-1][-1]

    def sleep_ms(script):
        return int(script.split("Start-Sleep -Milliseconds ")[1].split()[0])

    assert sleep_ms(slow) > sleep_ms(fast)


def test_type_text_duration_is_capped_by_config(companion, monkeypatch):
    commands = _typing_companion(companion, monkeypatch)
    companion.CONFIG = {**companion.CONFIG, "max_type_text_seconds": 5}

    companion.act_type_text(
        {"app": "notepad", "text": "hello", "duration_seconds": 9999}
    )

    script = commands[-1][-1]
    chunk_ms = int(script.split("Start-Sleep -Milliseconds ")[1].split()[0])
    chunks = script.count("'") // 2
    assert chunk_ms * chunks <= 5000


def test_type_text_is_a_registered_action(companion):
    assert companion.ACTIONS["type_text"] is companion.act_type_text


def test_notepad_ships_in_the_default_approved_apps(companion):
    """Unlike the other approved_apps entries, notepad's path needs no
    per-PC editing -- the showcase Reel's "talk to viewers" beat depends
    on it being there out of the box."""
    entry = companion.DEFAULT_CONFIG["approved_apps"]["notepad"]

    assert entry["path"] == "notepad.exe"
    assert entry["match"] == "Notepad"


def test_general_control_actions_are_registered(companion):
    expected = {
        "control_status", "control_stop", "control_resume",
        "observe_desktop", "desktop_input", "window_control",
        "clipboard", "file_operation", "launch_process", "process_control",
    }

    assert expected <= set(companion.ACTIONS)


def test_emergency_stop_blocks_general_desktop_actions(companion):
    companion.act_control_stop({"reason": "test stop"})

    result = companion.act_desktop_input({"action": "click", "x": 1, "y": 2})

    assert result["ok"] is False
    assert "emergency-stopped" in result["error"]
    assert companion.act_control_status({})["stop_reason"] == "test stop"
    assert companion.act_control_resume({})["enabled"] is True


def test_general_file_operations_cover_user_files(companion, tmp_path):
    target = tmp_path / "notes" / "atlas.txt"

    written = companion.act_file_operation({
        "operation": "write",
        "path": str(target),
        "text": "Atlas made this.",
    })
    read = companion.act_file_operation({
        "operation": "read",
        "path": str(target),
    })
    deleted = companion.act_file_operation({
        "operation": "delete",
        "path": str(target),
    })

    assert written == {"ok": True, "path": str(target), "bytes": 16}
    assert read["ok"] is True
    assert read["data_b64"] == "QXRsYXMgbWFkZSB0aGlzLg=="
    assert deleted["deleted"] is True
    assert not target.exists()


def test_general_file_operations_reject_protected_roots(
    companion, tmp_path, monkeypatch
):
    protected = tmp_path / "Windows"
    protected.mkdir()
    monkeypatch.setattr(
        companion, "_protected_windows_roots", lambda: (str(protected),)
    )

    with pytest.raises(PermissionError, match="protected"):
        companion.act_file_operation({
            "operation": "write",
            "path": str(protected / "system.ini"),
            "text": "nope",
        })


def test_control_audit_omits_payload_contents(companion, tmp_path):
    companion._audit_control(
        "observe_desktop",
        {"text": "private words"},
        {"image_b64": "c2NyZWVu", "data_b64": "ZmlsZQ=="},
    )

    entry = json.loads(
        (tmp_path / "control_audit.jsonl").read_text().strip()
    )

    assert entry["request"]["text"]["characters"] == 13
    assert entry["result"]["image_b64"]["encoded_chars"] == 8
    assert entry["result"]["data_b64"]["encoded_chars"] == 8
    assert "private words" not in json.dumps(entry)
    assert "c2NyZWVu" not in json.dumps(entry)


# --- Regression: on-camera console-window spam -------------------------
#
# The desktop autonomy loop calls observe_desktop once per step, and
# observe_desktop fans out to act_screenshot + act_active_window +
# act_active_apps. Any of those that shells out to PowerShell without
# CREATE_NO_WINDOW pops a real console window onto the owner's desktop --
# which is exactly what got recorded into a Reel instead of the demo.


def _record_powershell_runs(companion, monkeypatch):
    runs = []

    def fake_run(command, **kwargs):
        runs.append({"command": command, **kwargs})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(companion.subprocess, "run", fake_run)
    return runs


@pytest.mark.parametrize(
    "action_name",
    [
        "act_screenshot",
        "act_active_apps",
        "act_active_window",
        "act_system_info",
    ],
)
def test_console_actions_never_pop_a_visible_window(
    companion, monkeypatch, action_name
):
    runs = _record_powershell_runs(companion, monkeypatch)

    getattr(companion, action_name)({})

    assert runs, f"{action_name} ran no subprocess"
    for run in runs:
        assert run.get("creationflags") == companion._NO_WINDOW, (
            f"{action_name} ran {run['command'][0]} with a visible console"
        )


def test_observe_desktop_pops_no_console_windows(companion, monkeypatch):
    runs = _record_powershell_runs(companion, monkeypatch)
    monkeypatch.setattr(companion.os, "name", "posix")

    companion.act_observe_desktop({})

    assert runs
    assert all(
        run.get("creationflags") == companion._NO_WINDOW for run in runs
    )


# --- Regression: Paint opened but nothing was ever drawn ---------------
#
# act_desktop_input exposed move/click/double_click/scroll only. Freehand
# drawing needs the button held down across a path, so the desktop agent
# could open Paint and then had no action available that could make a
# mark -- it clicked, saw an unchanged canvas, and stalled.


def test_desktop_input_supports_drag_for_freehand_drawing(
    companion, monkeypatch
):
    scripts = []

    def fake_hidden(script, **kwargs):
        scripts.append(script)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(companion, "_run_hidden_powershell", fake_hidden)

    result = companion.act_desktop_input({
        "action": "drag",
        "path": [[10, 20], [30, 40], [50, 60]],
    })

    assert result["ok"] is True
    assert result["action"] == "drag"
    assert result["points"] == 3
    script = scripts[0]
    # Button goes down once at the start, up once at the end, with the
    # pointer moved through the path in between. Flags are emitted as
    # decimal, matching the existing click path.
    assert script.count("mouse_event(2,") == 1
    assert script.count("mouse_event(4,") == 1
    # The path is resampled, so the endpoints must appear as move calls
    # even though intermediate samples are inserted between them.
    assert "m 10 20;" in script
    assert "m 50 60;" in script


def test_desktop_input_drag_rejects_a_degenerate_path(companion):
    result = companion.act_desktop_input({"action": "drag", "path": [[1, 2]]})

    assert result["ok"] is False
    assert "two" in result["error"].lower()


# --- Regression: stop_recording never actually stopped ffmpeg ----------
#
# taskkill without /F posts WM_CLOSE, which a CREATE_NO_WINDOW console
# process never receives. ffmpeg kept running and kept growing the file,
# so the Pi's size/hash verification failed every retry. Proven live on
# the PC: the mp4's last-write time was 45s after the Pi gave up.


def test_stop_recording_asks_ffmpeg_to_finalize_then_waits_for_exit(
    companion, tmp_path, monkeypatch
):
    media = tmp_path / "recording_x.mp4"
    media.write_bytes(b"video bytes")
    monkeypatch.setattr(
        companion, "_load_recording_state",
        lambda: {"active": {"pid": 4242, "path": str(media), "name": media.name}},
    )
    monkeypatch.setattr(companion, "_save_recording_state", lambda _s: None)
    monkeypatch.setattr(companion, "_write_sidecar", lambda *_a: None)

    class FakeFfmpeg:
        def __init__(self):
            self.stdin = self
            self.written = []
            self.waited = False
            self.killed = False
        def write(self, data):
            self.written.append(data)
        def flush(self):
            pass
        def close(self):
            pass
        def wait(self, timeout=None):
            self.waited = True
            return 0
        def poll(self):
            return 0 if self.waited else None

    handle = FakeFfmpeg()
    companion._RECORDING_PROCESSES[4242] = handle
    try:
        result = companion.act_stop_recording({})
    finally:
        companion._RECORDING_PROCESSES.pop(4242, None)

    assert result["ok"] is True
    # 'q' is how ffmpeg is told to finish and write the moov atom. A hard
    # kill here would leave an unplayable file.
    assert b"q" in b"".join(handle.written)
    assert handle.waited is True


def test_stop_recording_force_kills_when_ffmpeg_ignores_the_quit(
    companion, tmp_path, monkeypatch
):
    media = tmp_path / "recording_y.mp4"
    media.write_bytes(b"video bytes")
    monkeypatch.setattr(
        companion, "_load_recording_state",
        lambda: {"active": {"pid": 909, "path": str(media), "name": media.name}},
    )
    monkeypatch.setattr(companion, "_save_recording_state", lambda _s: None)
    monkeypatch.setattr(companion, "_write_sidecar", lambda *_a: None)
    monkeypatch.setattr(companion, "_pid_running", lambda _p: True)
    monkeypatch.setattr(companion.time, "sleep", lambda _s: None)

    runs = []

    def fake_run(command, **kwargs):
        runs.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(companion.subprocess, "run", fake_run)

    companion.act_stop_recording({})

    taskkills = [c for c in runs if c and c[0] == "taskkill"]
    assert taskkills, "no taskkill fallback was attempted"
    # Without /F the kill is a no-op against a windowless console process.
    assert any("/F" in c for c in taskkills), taskkills


# --- Regression: focus always reported failure, so the agent looped ----
#
# The companion runs as a background scheduled task, and Windows refuses
# SetForegroundWindow from a non-foreground process. Paint was restored
# and visible but never became the foreground window, so window_control
# focus returned ok:false forever and the desktop agent spent its whole
# step budget retrying instead of drawing.


def test_focus_script_attaches_thread_input_to_beat_foreground_lock(
    companion, monkeypatch
):
    scripts = []
    monkeypatch.setattr(
        companion, "_run_hidden_powershell",
        lambda script, **k: scripts.append(script)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    companion._focus_window("Untitled - Paint")

    script = scripts[0]
    assert "AttachThreadInput" in script
    assert "GetWindowThreadProcessId" in script
    assert "SetForegroundWindow" in script


def test_focus_verification_retries_before_declaring_failure(
    companion, monkeypatch
):
    monkeypatch.setattr(companion, "_focus_window", lambda _t: True)
    monkeypatch.setattr(companion.time, "sleep", lambda _s: None)
    titles = iter([None, "", "Untitled - Paint"])
    monkeypatch.setattr(
        companion, "act_active_window",
        lambda _b: {"ok": True, "title": next(titles, "Untitled - Paint")},
    )

    result = companion.act_window_control(
        {"action": "focus", "title": "Untitled - Paint"}
    )

    assert result["ok"] is True


def test_focus_never_raises_when_powershell_times_out(companion, monkeypatch):
    """A slow focus must degrade to ok:false, not a 500. Live: focusing a
    non-foreground Electron window made the companion return an error
    three times in a row instead of a clean failure."""
    def boom(script, **kwargs):
        raise companion.subprocess.TimeoutExpired(cmd="powershell", timeout=10)

    monkeypatch.setattr(companion, "_run_hidden_powershell", boom)

    assert companion._focus_window("Claude") is False

    monkeypatch.setattr(companion.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        companion, "act_active_window", lambda _b: {"ok": True, "title": "Other"}
    )
    result = companion.act_window_control({"action": "focus", "title": "Claude"})
    assert result["ok"] is False


def test_focus_uses_win32_only_and_not_the_blocking_com_call(
    companion, monkeypatch
):
    """WScript.Shell AppActivate can block on Electron windows. The
    AttachThreadInput + SetForegroundWindow path is what actually works."""
    scripts = []
    monkeypatch.setattr(
        companion, "_run_hidden_powershell",
        lambda script, **k: scripts.append(script)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    companion._focus_window("Claude")

    assert "AppActivate" not in scripts[0]
    assert "SetForegroundWindow" in scripts[0]


def test_drag_synthesizes_real_motion_and_interpolates_the_path(
    companion, monkeypatch
):
    """SetCursorPos teleports the cursor without emitting mouse-move
    input, so Paint drew a dot instead of the stroke. Real MOUSEEVENTF
    absolute moves plus interpolated points are what make a visible
    line."""
    scripts = []
    monkeypatch.setattr(
        companion, "_run_hidden_powershell",
        lambda script, **k: scripts.append(script)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    result = companion.act_desktop_input({
        "action": "drag",
        "path": [[100, 100], [400, 100]],
    })

    assert result["ok"] is True
    script = scripts[0]
    # Absolute virtual-desktop motion events, not bare SetCursorPos.
    assert "mouse_event" in script
    # MOUSEEVENTF_MOVE|ABSOLUTE|VIRTUALDESK, emitted as decimal.
    assert str(companion._MOUSE_MOVE_ABSOLUTE) in script
    assert companion._MOUSE_MOVE_ABSOLUTE == 0x0001 | 0x8000 | 0x4000
    # A 300px straight run must be broken into many samples.
    assert script.count(";m ") + script.count("m 100 100;") >= 10
    assert result["points"] >= 10
