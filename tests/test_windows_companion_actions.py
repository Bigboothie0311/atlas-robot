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
    assert killed == [["taskkill", "/PID", "555"]]


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
