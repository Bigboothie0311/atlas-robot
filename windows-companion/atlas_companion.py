"""A.T.L.A.S. Windows Companion — runs on the gaming PC, not the Pi.

A deliberately tiny, authenticated HTTP service exposing ONLY a fixed
whitelist of safe actions the Pi may request. There is no arbitrary
command execution, no arbitrary mouse control, no purchases, no deletes,
no messaging. Every request must carry the shared token; the service
binds to the LAN so only the local network (the Pi) can reach it.

Stdlib only — needs nothing but a Python 3 install on the PC.

Install: see windows-companion/README.md. Configure paths/token in
companion_config.json next to this file.
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# The companion itself runs under pythonw, but Windows can still create a
# visible console for CLI children. Besides looking rough on camera, a
# transient PowerShell/ffmpeg window can steal foreground focus between the
# approved-app check and a safe typing action. This flag is zero on non-
# Windows hosts so the module remains unit-testable on the Pi.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CONFIG_PATH = Path(__file__).with_name("companion_config.json")

DEFAULT_CONFIG = {
    "token": "CHANGE_ME",
    "bind_host": "0.0.0.0",
    "bind_port": 5060,
    "fusion_path": r"C:\Users\YOU\AppData\Local\Autodesk\webdeploy\production\Fusion360.exe",
    "projects": {
        "example": r"C:\Users\YOU\Documents\Fusion\example.f3d"
    },
    "screenshot_folder": r"C:\Users\YOU\Pictures\Screenshots",
    "approved_folders": {
        "downloads": r"C:\Users\YOU\Downloads"
    },
    # name -> {path to launch, window-title substring to match when
    # checking whether it's already open}. Edit paths for your PC.
    "approved_apps": {
        "spotify": {
            "path": r"C:\Users\YOU\AppData\Roaming\Spotify\Spotify.exe",
            "match": "Spotify",
        },
        "claude": {
            "path": r"C:\Users\YOU\AppData\Local\AnthropicClaude\claude.exe",
            "match": "Claude",
        },
        "codex": {
            "path": r"C:\Users\YOU\AppData\Local\Programs\codex\Codex.exe",
            "match": "Codex",
        },
        "terminal": {
            "path": "wt.exe",
            "match": "Windows Terminal",
        },
        "fusion": {
            "path": r"C:\Users\YOU\AppData\Local\Autodesk\webdeploy\production\Fusion360.exe",
            "match": "Fusion 360",
        },
        "browser": {
            "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "match": "Chrome",
        },
        # Notepad is what type_text writes into for on-camera "talking
        # to viewers" beats in the self-showcase Reel. It ships with
        # Windows, so unlike the entries above this path needs no
        # per-PC editing.
        "notepad": {
            "path": "notepad.exe",
            "match": "Notepad",
        },
    },
    # Hard ceiling on how much text one type_text call may send, and
    # how long it may spend pacing it out. Bounds the only action that
    # synthesizes keystrokes.
    "max_type_text_chars": 400,
    "max_type_text_seconds": 120,
    # name -> full command list. ONLY these predefined scripts can run.
    "maintenance_scripts": {
        "clear_temp": ["cmd", "/c", "del", "/q", "/s", r"%TEMP%\*"],
    },
    "slicer_status_url": "http://127.0.0.1:8899/status",
    # Screen recordings and standalone captures live here permanently —
    # the Pi only ever stages footage briefly before uploading it here.
    "recordings_folder": r"C:\Users\YOU\Videos\AtlasRecordings",
    "max_recording_seconds": 900,
    # General desktop autonomy runs in the currently logged-in interactive
    # session under this companion's existing *non-elevated* Windows token.
    # Windows/UAC remains the system-modification boundary.
    "general_control_enabled": True,
    "general_control_max_text_chars": 5000,
    "general_control_max_drag_points": 200,
    "general_control_max_file_bytes": 10 * 1024 * 1024,
    "general_control_process_timeout_seconds": 120,
    # Case-insensitive substrings of a window title that refuse a
    # screenshot/window-capture/recording of that window outright.
    "privacy_blocked_window_substrings": [
        "password", "1password", "bitwarden", "keychain",
        "gmail", "bank", "venmo", "paypal", "signal",
        "private browsing", "incognito",
    ],
}

# Live ffmpeg handles, keyed by pid, so stop_recording can ask ffmpeg to
# finish cleanly on stdin instead of killing it. taskkill without /F posts
# WM_CLOSE, which a CREATE_NO_WINDOW console process never receives, so the
# old kill silently did nothing and ffmpeg kept growing the file while the
# Pi tried to download it. A hard /F kill stops it but truncates the mp4
# before the moov atom is written, leaving an unplayable file -- so 'q'
# first, /F only as a last resort.
_RECORDING_PROCESSES: dict[int, object] = {}
_RECORDING_QUIT_TIMEOUT_SECONDS = 20
_RECORDING_SETTLE_SECONDS = 0.5
_RECORDING_STABLE_CHECKS = 3

# MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
_MOUSE_MOVE_ABSOLUTE = 0x0001 | 0x8000 | 0x4000
# How finely a drag path is resampled, and how long each sample rests.
# Dense samples are what make the stroke a line instead of a dot, and the
# pause keeps it visible on a 30fps screen recording.
_DRAG_SAMPLE_PIXELS = 16
_DRAG_STEP_MILLISECONDS = 7

_CONTROL_LOCK = threading.RLock()
_CONTROL_ENABLED = True
_CONTROL_STOP_REASON = None
_GENERAL_CONTROL_ACTIONS = {
    "observe_desktop", "desktop_input", "window_control", "clipboard",
    "file_operation", "launch_process", "process_control",
}


def load_config():
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)

    return {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text())}


def ensure_config_file():
    """Writes the default config file on first run if missing. Kept out
    of module import (unlike the old load_config()) so the module can
    be imported for testing without touching disk."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        print(f"Wrote default config to {CONFIG_PATH} — edit it and restart.")


CONFIG = load_config()


# ---------------------------------------------------------------------
# Recording/capture helpers
# ---------------------------------------------------------------------

def _recording_state_path():
    return CONFIG_PATH.with_name("recording_state.json")


def _load_recording_state():
    path = _recording_state_path()

    if not path.exists():
        return {"active": None}

    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"active": None}


def _save_recording_state(state):
    _recording_state_path().write_text(json.dumps(state, indent=2))


def _utc_now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _timestamp_slug():
    return time.strftime("%Y%m%d_%H%M%S", time.gmtime())


def _write_sidecar(media_path, meta):
    Path(str(media_path) + ".json").write_text(
        json.dumps(meta, indent=2)
    )


def _window_is_privacy_blocked(title):
    if not title:
        return False

    blocked = CONFIG.get("privacy_blocked_window_substrings", [])
    lowered = title.lower()
    return any(term.lower() in lowered for term in blocked)


def _control_audit_path():
    return CONFIG_PATH.with_name("control_audit.jsonl")


def _audit_control(action, request, result):
    """Append local evidence without duplicating screenshots or file contents."""
    def summarize(value):
        if isinstance(value, dict):
            cleaned = {}
            for key, item in value.items():
                if key in {"image_b64", "data_b64"} and isinstance(item, str):
                    cleaned[key] = {
                        "omitted": True,
                        "encoded_chars": len(item),
                        "sha256": hashlib.sha256(item.encode("ascii")).hexdigest(),
                    }
                elif key in {"text", "content"} and isinstance(item, str):
                    cleaned[key] = {
                        "omitted": True,
                        "characters": len(item),
                        "sha256": hashlib.sha256(item.encode("utf-8")).hexdigest(),
                    }
                else:
                    cleaned[key] = summarize(item)
            return cleaned
        if isinstance(value, list):
            return [summarize(item) for item in value]
        return value

    entry = {
        "ts": _utc_now_iso(),
        "action": action,
        "request": summarize(request),
        "result": summarize(result),
    }
    try:
        with _control_audit_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def _control_status_payload():
    with _CONTROL_LOCK:
        return {
            "ok": True,
            "enabled": bool(_CONTROL_ENABLED),
            "stop_reason": _CONTROL_STOP_REASON,
            "emergency_hotkey": "Ctrl+Alt+F12",
        }


def _require_control_enabled():
    with _CONTROL_LOCK:
        if not _CONTROL_ENABLED:
            return {
                "ok": False,
                "error": (
                    "general desktop control is emergency-stopped"
                    + (
                        f": {_CONTROL_STOP_REASON}"
                        if _CONTROL_STOP_REASON else ""
                    )
                ),
            }
    return None


def _set_control_enabled(enabled, reason=None):
    global _CONTROL_ENABLED, _CONTROL_STOP_REASON
    with _CONTROL_LOCK:
        _CONTROL_ENABLED = bool(enabled)
        _CONTROL_STOP_REASON = None if enabled else str(reason or "stopped")
    return _control_status_payload()


def _protected_windows_roots():
    system_drive = os.environ.get("SystemDrive", "C:")
    windows = os.environ.get("SystemRoot", rf"{system_drive}\Windows")
    program_files = os.environ.get(
        "ProgramFiles", rf"{system_drive}\Program Files"
    )
    program_files_x86 = os.environ.get(
        "ProgramFiles(x86)", rf"{system_drive}\Program Files (x86)"
    )
    program_data = os.environ.get(
        "ProgramData", rf"{system_drive}\ProgramData"
    )
    return (
        windows,
        program_files,
        program_files_x86,
        program_data,
        rf"{system_drive}\Recovery",
        rf"{system_drive}\System Volume Information",
        str(CONFIG_PATH),
    )


def _canonical_path(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path must be a non-empty string")
    expanded = os.path.expandvars(os.path.expanduser(value.strip()))
    return os.path.realpath(os.path.abspath(expanded))


def _path_is_protected(value):
    candidate = os.path.normcase(_canonical_path(value))
    for root in _protected_windows_roots():
        normalized_root = os.path.normcase(_canonical_path(root))
        try:
            if os.path.commonpath((candidate, normalized_root)) == normalized_root:
                return True
        except ValueError:
            continue
    return False


def _allowed_user_path(value):
    path = _canonical_path(value)
    if _path_is_protected(path):
        raise PermissionError("protected system/control path is excluded")
    return Path(path)


def _run_hidden_powershell(script, *, timeout=20):
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )


def _emergency_hotkey_monitor():
    """Independent local Ctrl+Alt+F12 latch; no network request needed."""
    if os.name != "nt":
        return
    import ctypes

    user32 = ctypes.windll.user32
    was_down = False
    while True:
        down = bool(user32.GetAsyncKeyState(0x11) & 0x8000) and bool(
            user32.GetAsyncKeyState(0x12) & 0x8000
        ) and bool(user32.GetAsyncKeyState(0x7B) & 0x8000)
        if down and not was_down:
            _set_control_enabled(False, "physical emergency hotkey")
        was_down = down
        time.sleep(0.05)


def _pid_running(pid):
    """Windows-safe liveness check via tasklist — no extra dependency."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True, text=True, timeout=10,
        )
        return str(pid) in result.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def _reconcile_orphaned_recording():
    """Called only from main() (never on import) — if the companion
    crashed or was restarted mid-recording, finalize the orphaned state
    instead of leaving a phantom 'active' recording that blocks every
    future start_recording call forever."""
    state = _load_recording_state()
    active = state.get("active")

    if not active:
        return

    pid = active.get("pid")
    if pid and _pid_running(pid):
        return

    path = Path(active.get("path", ""))
    meta = {
        **active,
        "kind": "screen_recording",
        "orphaned": True,
        "stopped_at": _utc_now_iso(),
    }

    if path.is_file() and path.stat().st_size > 0:
        meta["size_bytes"] = path.stat().st_size
        _write_sidecar(path, meta)

    _save_recording_state({"active": None})


# ---------------------------------------------------------------------
# Whitelisted actions — each returns a JSON-serializable dict.
# ---------------------------------------------------------------------

def act_open_fusion(_body):
    subprocess.Popen([CONFIG["fusion_path"]])
    return {"ok": True, "opened": "Fusion 360"}


def act_open_project(body):
    name = str(body.get("project", "")).strip()
    path = CONFIG["projects"].get(name)

    if not path:
        return {"ok": False, "error": f"unknown project '{name}'"}

    subprocess.Popen([CONFIG["fusion_path"], path])
    return {"ok": True, "opened": name}


def act_newest_screenshot(_body):
    folder = Path(CONFIG["screenshot_folder"])
    images = sorted(
        (p for p in folder.glob("*.*")
         if p.suffix.lower() in (".png", ".jpg", ".jpeg")),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )

    if not images:
        return {"ok": False, "error": "no screenshots found"}

    # Open it on the PC AND return it base64 so the Pi can analyze it.
    subprocess.Popen(["cmd", "/c", "start", "", str(images[0])], shell=False)
    return {
        "ok": True,
        "name": images[0].name,
        "image_b64": base64.b64encode(images[0].read_bytes()).decode(),
    }


def act_volume(body):
    action = str(body.get("action", "")).strip()
    # Media/volume keys via PowerShell SendKeys — no arbitrary input.
    keys = {
        "up": "[char]175", "down": "[char]174", "mute": "[char]173",
    }
    if action not in keys:
        return {"ok": False, "error": "action must be up/down/mute"}

    repeat = int(body.get("repeat", 1)) if action != "mute" else 1
    script = (
        "$w = New-Object -ComObject WScript.Shell; "
        + "".join(f"$w.SendKeys([char]{ {'up':175,'down':174,'mute':173}[action] }); "
                  for _ in range(max(1, min(repeat, 10))))
    )
    _run_hidden_powershell(script, timeout=10)
    return {"ok": True, "action": action}


def act_media(body):
    action = str(body.get("action", "")).strip()
    codes = {"playpause": 179, "next": 176, "previous": 177}

    if action not in codes:
        return {"ok": False, "error": "action must be playpause/next/previous"}

    script = (
        "$w = New-Object -ComObject WScript.Shell; "
        f"$w.SendKeys([char]{codes[action]})"
    )
    _run_hidden_powershell(script, timeout=10)
    return {"ok": True, "action": action}


def act_open_folder(body):
    name = str(body.get("folder", "")).strip()
    path = CONFIG["approved_folders"].get(name)

    if not path:
        return {"ok": False, "error": f"folder '{name}' not approved"}

    subprocess.Popen(["explorer", path])
    return {"ok": True, "opened": name}


def act_screenshot(_body):
    """Captures the screen and returns it base64 for the Pi to analyze."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        out = tmp.name

    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
        "$bmp=New-Object Drawing.Bitmap $b.Width,$b.Height; "
        "$g=[Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($b.Location,[Drawing.Point]::Empty,$b.Size); "
        f"$bmp.Save('{out}')"
    )
    _run_hidden_powershell(script, timeout=20)
    data = Path(out).read_bytes()
    Path(out).unlink(missing_ok=True)
    return {"ok": True, "image_b64": base64.b64encode(data).decode()}


def act_capture_screenshot(body):
    """Captures the full screen to recordings_folder with a JSON sidecar
    (mission, window, timestamp) — distinct from the legacy 'screenshot'
    action, which only pushes a base64 image to the HUD and saves
    nothing. Refuses if the focused window is privacy-blocked."""
    mission = str(body.get("mission", "")).strip() or None
    foreground = act_active_window({}).get("title")

    if _window_is_privacy_blocked(foreground):
        return {
            "ok": False,
            "error": f"privacy-blocked window is focused: {foreground}",
        }

    folder = Path(CONFIG["recordings_folder"])
    folder.mkdir(parents=True, exist_ok=True)
    name = f"screenshot_{_timestamp_slug()}.png"
    out = folder / name
    escaped_out = str(out).replace("'", "''")

    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
        "$bmp=New-Object Drawing.Bitmap $b.Width,$b.Height; "
        "$g=[Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($b.Location,[Drawing.Point]::Empty,$b.Size); "
        f"$bmp.Save('{escaped_out}')"
    )
    _run_hidden_powershell(script, timeout=20)

    if not out.is_file():
        return {"ok": False, "error": "screenshot capture failed"}

    meta = {
        "kind": "screenshot",
        "path": str(out),
        "name": name,
        "mission": mission,
        "captured_at": _utc_now_iso(),
        "window": foreground,
    }
    _write_sidecar(out, meta)
    return {"ok": True, **meta}


def act_capture_window(body):
    """Captures ONE named window (by title substring) via PrintWindow,
    not the whole screen. Refuses unapproved/privacy-blocked titles and
    reports clearly if nothing matched."""
    title_query = str(body.get("window_title", "")).strip()
    mission = str(body.get("mission", "")).strip() or None

    if not title_query:
        return {"ok": False, "error": "window_title is required"}

    if _window_is_privacy_blocked(title_query):
        return {
            "ok": False,
            "error": f"privacy-blocked window requested: {title_query}",
        }

    folder = Path(CONFIG["recordings_folder"])
    folder.mkdir(parents=True, exist_ok=True)
    name = f"window_{_timestamp_slug()}.png"
    out = folder / name
    escaped_query = title_query.replace("'", "''")
    escaped_out = str(out).replace("'", "''")

    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "Add-Type @'\n"
        "using System;\n"
        "using System.Runtime.InteropServices;\n"
        "public class AtlasWindowCapture {\n"
        "  [DllImport(\"user32.dll\")]\n"
        "  public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);\n"
        "  [DllImport(\"user32.dll\")]\n"
        "  public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);\n"
        "  public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }\n"
        "}\n"
        "'@ -ErrorAction SilentlyContinue; "
        "$p = Get-Process | Where-Object { $_.MainWindowTitle -like "
        f"'*{escaped_query}*' }} | Select-Object -First 1; "
        "if (-not $p) { Write-Output 'NO_MATCH'; exit }; "
        "$h = $p.MainWindowHandle; "
        "$rect = New-Object AtlasWindowCapture+RECT; "
        "[AtlasWindowCapture]::GetWindowRect($h, [ref]$rect) | Out-Null; "
        "$w = $rect.Right - $rect.Left; $ht = $rect.Bottom - $rect.Top; "
        "$bmp = New-Object Drawing.Bitmap $w, $ht; "
        "$g = [Drawing.Graphics]::FromImage($bmp); "
        "$hdc = $g.GetHdc(); "
        "[AtlasWindowCapture]::PrintWindow($h, $hdc, 2) | Out-Null; "
        "$g.ReleaseHdc($hdc); "
        f"$bmp.Save('{escaped_out}'); "
        "Write-Output $p.MainWindowTitle"
    )
    result = _run_hidden_powershell(script, timeout=20)
    matched_title = result.stdout.strip()

    if matched_title == "NO_MATCH" or not matched_title:
        return {"ok": False, "error": f"no open window matched '{title_query}'"}

    if not out.is_file():
        return {"ok": False, "error": "window capture failed"}

    meta = {
        "kind": "window_capture",
        "path": str(out),
        "name": name,
        "mission": mission,
        "captured_at": _utc_now_iso(),
        "window": matched_title,
    }
    _write_sidecar(out, meta)
    return {"ok": True, **meta}


def act_start_recording(body):
    """Starts an ffmpeg gdigrab screen recording of the full desktop or
    one named window. Duration is bounded up-front via ffmpeg's own
    -t flag (self-terminating), not a separate watchdog. Refuses a
    second concurrent recording and any privacy-blocked target."""
    mission = str(body.get("mission", "")).strip() or None
    target = str(body.get("target", "full")).strip() or "full"
    window_title = str(body.get("window_title", "")).strip() or None
    privacy = bool(body.get("privacy", False))
    configured_cap = int(CONFIG.get("max_recording_seconds", 900))
    requested_seconds = int(body.get("max_seconds") or configured_cap)
    max_seconds = max(1, min(requested_seconds, configured_cap))

    state = _load_recording_state()
    if state.get("active"):
        return {"ok": False, "error": "a recording is already in progress"}

    if target == "window":
        if not window_title:
            return {
                "ok": False,
                "error": "window_title is required when target is 'window'",
            }

        if _window_is_privacy_blocked(window_title):
            return {
                "ok": False,
                "error": f"privacy-blocked window requested: {window_title}",
            }
    elif _window_is_privacy_blocked(act_active_window({}).get("title")):
        return {
            "ok": False,
            "error": "the focused window is privacy-blocked; recording refused",
        }

    folder = Path(CONFIG["recordings_folder"])
    folder.mkdir(parents=True, exist_ok=True)
    name = f"recording_{_timestamp_slug()}.mp4"
    out = folder / name

    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "gdigrab", "-framerate", "30",
    ]
    if target == "window" and window_title:
        command.extend(["-i", f"title={window_title}"])
    else:
        command.extend(["-i", "desktop"])
    # Explicit encoder settings: gdigrab's default codec choice for an
    # .mp4 container isn't guaranteed to be something Windows' stock
    # players can decode. libx264/yuv420p/faststart is the safe,
    # broadly-compatible baseline every player and Instagram's own
    # upload pipeline expects.
    command.extend([
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-t", str(max_seconds), str(out),
    ])

    try:
        # stdin stays open so stop_recording can send 'q' and let ffmpeg
        # finalize the container itself.
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, creationflags=_NO_WINDOW
        )
    except OSError as error:
        return {"ok": False, "error": f"could not start ffmpeg: {error}"}

    _RECORDING_PROCESSES[process.pid] = process

    active = {
        "pid": process.pid,
        "path": str(out),
        "name": name,
        "mission": mission,
        "target": target,
        "window_title": window_title,
        "privacy": privacy,
        "max_seconds": max_seconds,
        "started_at": _utc_now_iso(),
    }
    _save_recording_state({"active": active})
    return {"ok": True, **active}


def _stop_ffmpeg(pid):
    """Stop the recording process, preferring a clean shutdown.

    ffmpeg finalizes the mp4 (writes the moov atom) when it is told to
    quit on stdin. Killing it instead leaves a file that will not play,
    and killing it *without* /F does not stop it at all when it was
    started with CREATE_NO_WINDOW.
    """
    process = _RECORDING_PROCESSES.pop(pid, None) if pid else None

    if process is not None:
        try:
            if process.stdin is not None:
                process.stdin.write(b"q")
                process.stdin.flush()
                process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=_RECORDING_QUIT_TIMEOUT_SECONDS)
            return
        except Exception:
            pass

    if not pid or not _pid_running(pid):
        return

    # Either the companion restarted and lost the handle, or ffmpeg
    # ignored the quit. /F is required: without it taskkill cannot stop a
    # windowless console process at all.
    for arguments in (
        ["taskkill", "/PID", str(pid), "/T"],
        ["taskkill", "/PID", str(pid), "/T", "/F"],
    ):
        try:
            subprocess.run(
                arguments,
                capture_output=True,
                timeout=10,
                creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(1)
        if not _pid_running(pid):
            return


def _wait_for_stable_file(path, checks=_RECORDING_STABLE_CHECKS):
    """Return True once the file's size stops changing."""
    previous = -1
    stable = 0
    for _ in range(40):
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size == previous and size > 0:
            stable += 1
            if stable >= checks:
                return True
        else:
            stable = 0
        previous = size
        time.sleep(_RECORDING_SETTLE_SECONDS)
    return False


def act_stop_recording(_body):
    """Stops the in-progress recording and verifies the file actually
    landed on disk with real bytes before reporting success."""
    state = _load_recording_state()
    active = state.get("active")

    if not active:
        return {"ok": False, "error": "no recording is in progress"}

    pid = active.get("pid")
    path = Path(active["path"])

    _stop_ffmpeg(pid)
    _save_recording_state({"active": None})

    if not path.is_file() or path.stat().st_size == 0:
        return {"ok": False, "error": "recording file is missing or empty"}

    # Only report success once the file has actually stopped growing.
    # The Pi verifies the download by size+hash, so handing back a path
    # that is still being written fails verification every retry.
    if not _wait_for_stable_file(path):
        return {"ok": False, "error": "recording file never stopped growing"}

    meta = {
        **active,
        "kind": "screen_recording",
        "stopped_at": _utc_now_iso(),
        "size_bytes": path.stat().st_size,
    }
    _write_sidecar(path, meta)
    return {"ok": True, **meta}


def act_list_recordings(_body):
    """Lists every captured screenshot/window-capture/recording from
    their JSON sidecars, newest first, each flagged with whether the
    media file still exists."""
    folder = Path(CONFIG["recordings_folder"])

    if not folder.is_dir():
        return {"ok": True, "recordings": []}

    items = []
    for sidecar in sorted(
        folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        try:
            meta = json.loads(sidecar.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        media_path = Path(meta.get("path", ""))
        meta["exists"] = media_path.is_file()
        items.append(meta)

    return {"ok": True, "recordings": items}


def act_active_apps(_body):
    script = (
        "Get-Process | Where-Object {$_.MainWindowTitle} | "
        "Select-Object -ExpandProperty MainWindowTitle"
    )
    result = _run_hidden_powershell(script, timeout=15)
    titles = [t.strip() for t in result.stdout.splitlines() if t.strip()]
    return {"ok": True, "windows": titles}


def act_run_script(body):
    name = str(body.get("script", "")).strip()
    command = CONFIG["maintenance_scripts"].get(name)

    if not command:
        return {"ok": False, "error": f"script '{name}' not in whitelist"}

    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    return {"ok": True, "script": name, "exit_code": result.returncode}


def act_youtube_search(body):
    """Opens the default browser to a YouTube search, biased toward longer
    tutorial videos (filters out Shorts), and full-screens it. Only builds
    a youtube.com search URL from the query — never an arbitrary URL."""
    import urllib.parse

    query = str(body.get("query", "")).strip()

    if not query:
        return {"ok": False, "error": "empty query"}

    # sp=EgIYAg%3D%3D = YouTube's "Duration: 20+ minutes" filter, which
    # excludes Shorts and favors full walkthroughs.
    encoded = urllib.parse.quote(query)
    url = f"https://www.youtube.com/results?search_query={encoded}&sp=EgIYAg%3D%3D"

    if body.get("private"):
        # Reel capture must not expose the owner's signed-in YouTube profile,
        # recommendations, notifications, or browsing history. Launch a new
        # InPrivate/Incognito window from a known browser binary and fail
        # closed if neither browser is available.
        escaped_url = url.replace("'", "''")
        private_script = (
            "$edge=@("
            "\"${env:ProgramFiles(x86)}\\Microsoft\\Edge\\Application\\msedge.exe\","
            "\"$env:ProgramFiles\\Microsoft\\Edge\\Application\\msedge.exe\""
            ")|Where-Object{Test-Path $_}|Select-Object -First 1;"
            "$chrome=@("
            "\"$env:ProgramFiles\\Google\\Chrome\\Application\\chrome.exe\","
            "\"$env:LocalAppData\\Google\\Chrome\\Application\\chrome.exe\""
            ")|Where-Object{Test-Path $_}|Select-Object -First 1;"
            "if($edge){"
            f"Start-Process $edge -ArgumentList '--inprivate','--new-window','{escaped_url}';"
            "exit 0};"
            "if($chrome){"
            f"Start-Process $chrome -ArgumentList '--incognito','--new-window','{escaped_url}';"
            "exit 0};"
            "[Console]::Error.Write('No private-capable browser found.');exit 1"
        )
        launch = subprocess.run(
            ["powershell", "-NoProfile", "-Command", private_script],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_NO_WINDOW,
        )
        if launch.returncode != 0:
            return {
                "ok": False,
                "error": launch.stderr.strip()
                or "could not open a private browser window",
            }
    else:
        subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)

    if body.get("fullscreen", True):
        # Give the browser a moment to open, then send F11.
        script = (
            "Start-Sleep -Seconds 3; "
            "$w = New-Object -ComObject WScript.Shell; "
            "$w.SendKeys('{F11}')"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", script],
            creationflags=_NO_WINDOW,
        )

    return {"ok": True, "query": query}


def act_shutdown_pc(_body):
    """Schedules a shutdown 60 seconds out (matches the Pi's spoken
    'shut down in one minute, say cancel to abort') instead of shutting
    down immediately, so a misheard/duplicate command is always
    recoverable via act_cancel_pc_shutdown."""
    result = subprocess.run(
        ["shutdown", "/s", "/t", "60"], capture_output=True, text=True, timeout=15
    )

    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or "shutdown command failed"}

    return {"ok": True}


def act_cancel_pc_shutdown(_body):
    result = subprocess.run(
        ["shutdown", "/a"], capture_output=True, text=True, timeout=15
    )

    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or "no shutdown was pending"}

    return {"ok": True}


def act_empty_recycle_bin(_body):
    script = "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"
    _run_hidden_powershell(script, timeout=20)
    return {"ok": True}


def act_open_app(body):
    """Opens an app from the approved_apps whitelist in the config — used
    by ATLAS app profiles. Never launches an arbitrary path."""
    name = str(body.get("app", "")).strip()
    approved = CONFIG.get("approved_apps", {})
    entry = approved.get(name)
    path = entry.get("path") if isinstance(entry, dict) else entry

    if not path:
        return {"ok": False, "error": f"app '{name}' not in approved_apps"}

    subprocess.Popen([path])
    return {"ok": True, "opened": name}


def _open_window_titles():
    script = (
        "Get-Process | Where-Object {$_.MainWindowTitle} | "
        "Select-Object -ExpandProperty MainWindowTitle"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=_NO_WINDOW,
    )
    return [t.strip() for t in result.stdout.splitlines() if t.strip()]


def _focus_window(match_substring):
    """Brings the first window whose title contains match_substring to
    the foreground. Title-based (WScript.Shell.AppActivate) — no
    coordinates, no clicks, nothing arbitrary."""
    escaped = match_substring.replace("'", "''")
    # The companion runs as a background scheduled task, and Windows
    # refuses SetForegroundWindow to a process that does not already own
    # the foreground. ShowWindow alone made the window visible but never
    # focused, so callers that verify focus saw failure forever -- the
    # desktop agent burned its whole step budget retrying instead of
    # working. Attaching to the current foreground thread's input queue
    # lifts that restriction for the duration of the call.
    script = (
        "Add-Type @'\nusing System;using System.Runtime.InteropServices;"
        "public class AtlasFocus{"
        "[DllImport(\"user32.dll\")]public static extern bool ShowWindow(IntPtr h,int n);"
        "[DllImport(\"user32.dll\")]public static extern bool SetForegroundWindow(IntPtr h);"
        "[DllImport(\"user32.dll\")]public static extern bool BringWindowToTop(IntPtr h);"
        "[DllImport(\"user32.dll\")]public static extern IntPtr GetForegroundWindow();"
        "[DllImport(\"user32.dll\")]public static extern uint GetWindowThreadProcessId(IntPtr h,IntPtr p);"
        "[DllImport(\"user32.dll\")]public static extern bool AttachThreadInput(uint a,uint b,bool f);"
        "[DllImport(\"kernel32.dll\")]public static extern uint GetCurrentThreadId();}\n'@;"
        "$p=Get-Process|Where-Object{$_.MainWindowTitle -like "
        f"'*{escaped}*'}}|Select-Object -First 1;"
        "if(-not $p){exit 3};"
        "$h=$p.MainWindowHandle;"
        "[AtlasFocus]::ShowWindow($h,9)|Out-Null;"
        "$fg=[AtlasFocus]::GetForegroundWindow();"
        "$ft=[AtlasFocus]::GetWindowThreadProcessId($fg,[IntPtr]::Zero);"
        "$ct=[AtlasFocus]::GetCurrentThreadId();"
        "$attached=$false;"
        "if($ft -ne $ct){$attached=[AtlasFocus]::AttachThreadInput($ct,$ft,$true)};"
        "[AtlasFocus]::BringWindowToTop($h)|Out-Null;"
        "[AtlasFocus]::SetForegroundWindow($h)|Out-Null;"
        "if($attached){[AtlasFocus]::AttachThreadInput($ct,$ft,$false)|Out-Null}"
    )
    # WScript.Shell's AppActivate used to run here too, but it can block
    # indefinitely on Electron windows; the timeout then escaped as a 500
    # instead of a clean "focus failed". The Win32 path above is what
    # actually beats the foreground lock, so the COM call is gone and any
    # remaining slowness degrades to False.
    try:
        return _run_hidden_powershell(script, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def act_focus_or_open_app(body):
    """Focuses an already-open approved app's window (matched by window
    title substring) instead of launching a duplicate instance; opens
    it only if no matching window is found. Never touches an
    unapproved path."""
    name = str(body.get("app", "")).strip()
    approved = CONFIG.get("approved_apps", {})
    entry = approved.get(name)

    if not isinstance(entry, dict):
        return {"ok": False, "error": f"app '{name}' not in approved_apps"}

    match = str(entry.get("match") or name)
    titles = _open_window_titles()
    already_open = any(match.lower() in title.lower() for title in titles)

    if already_open:
        _focus_window(match)
        return {"ok": True, "app": name, "action": "focused"}

    path = entry.get("path")

    if not path:
        return {"ok": False, "error": f"app '{name}' has no configured path"}

    subprocess.Popen([path])
    return {"ok": True, "app": name, "action": "launched"}


# SendKeys treats these as command characters; a literal one has to be
# wrapped in braces or it silently becomes a modifier/grouping token
# (e.g. a bare "+" means Shift and would swallow the next character).
_SENDKEYS_LITERALS = set("+^%~(){}[]")

# Chunk size for paced typing. Small enough that a chunk boundary looks
# like a natural pause on camera rather than a stall, large enough that
# a 400-character message doesn't need hundreds of PowerShell calls.
_TYPE_TEXT_CHUNK_CHARS = 6
_TYPE_TEXT_DEFAULT_CHUNK_MS = 90


def _escape_for_sendkeys(text):
    """Escapes one line of user text into a SendKeys-safe literal."""
    return "".join(
        "{" + char + "}" if char in _SENDKEYS_LITERALS else char
        for char in text
    )


def _sendkeys_chunks(text):
    """Splits text into SendKeys-escaped chunks, with newlines emitted
    as their own {ENTER} chunk so multi-line messages keep their
    line breaks."""
    chunks = []

    for index, line in enumerate(text.split("\n")):
        if index:
            chunks.append("{ENTER}")

        escaped = _escape_for_sendkeys(line)
        for start in range(0, len(escaped), _TYPE_TEXT_CHUNK_CHARS):
            piece = escaped[start:start + _TYPE_TEXT_CHUNK_CHARS]
            if piece:
                chunks.append(piece)

    return chunks


def act_type_text(body):
    """Types a message into an approved app's window — the on-camera
    "Atlas talks to viewers in Notepad" beat of the self-showcase Reel.

    This is the only action that synthesizes keystrokes, so it is
    deliberately fenced in:

      * the target must be a named entry in approved_apps, same
        whitelist open_app/focus_or_open_app use — never an arbitrary
        window title,
      * the window is focused first and then the FOREGROUND title is
        re-checked against that entry's match before a single key is
        sent, so if anything else stole focus in between the keystrokes
        are refused rather than typed into whatever is actually there,
      * a privacy-blocked foreground title refuses outright, same as
        screenshots and recordings,
      * length and duration are capped by config.

    Optional 'duration_seconds' paces the typing to finish at roughly
    that mark, which is what the Reel uses to sync typing to the length
    of the beat's narration.
    """
    name = str(body.get("app", "")).strip()
    text = str(body.get("text", ""))

    approved = CONFIG.get("approved_apps", {})
    entry = approved.get(name)

    if not isinstance(entry, dict):
        return {"ok": False, "error": f"app '{name}' not in approved_apps"}

    if not text.strip():
        return {"ok": False, "error": "text is empty"}

    max_chars = int(CONFIG.get("max_type_text_chars", 400))
    if len(text) > max_chars:
        return {
            "ok": False,
            "error": f"text is longer than the {max_chars}-character limit",
        }

    # Newline is the only control character a typed message may carry;
    # anything else is a terminal/agent escape sequence, not a message.
    if any(char < " " and char != "\n" for char in text):
        return {
            "ok": False,
            "error": "text contains control characters",
        }

    match = str(entry.get("match") or name)

    foreground = None
    for attempt in range(1, 4):
        # Windows can reject one SetForegroundWindow call while another
        # process is finishing startup. Re-open/refocus and verify up to
        # three times before the safety gate refuses to type.
        focus_result = act_focus_or_open_app({"app": name})
        if not focus_result.get("ok"):
            return focus_result
        if focus_result.get("action") == "launched":
            time.sleep(2)
        _focus_window(match)
        time.sleep(0.5 * attempt)
        foreground = act_active_window({}).get("title")
        if foreground and match.lower() in foreground.lower():
            break

    if _window_is_privacy_blocked(foreground):
        return {
            "ok": False,
            "error": f"privacy-blocked window is focused: {foreground}",
        }

    if not foreground or match.lower() not in foreground.lower():
        return {
            "ok": False,
            "error": (
                f"'{name}' is not the foreground window "
                f"(focused: {foreground or 'nothing'}); refused to type"
            ),
        }

    chunks = _sendkeys_chunks(text)

    if not chunks:
        return {"ok": False, "error": "text produced no typeable content"}

    max_seconds = int(CONFIG.get("max_type_text_seconds", 120))
    requested_seconds = float(body.get("duration_seconds") or 0)

    if requested_seconds > 0:
        pace_seconds = min(requested_seconds, max_seconds)
        chunk_ms = max(10, int(1000 * pace_seconds / len(chunks)))
    else:
        chunk_ms = _TYPE_TEXT_DEFAULT_CHUNK_MS

    quoted = ",".join(
        "'" + chunk.replace("'", "''") + "'" for chunk in chunks
    )
    script = (
        "$w = New-Object -ComObject WScript.Shell; "
        f"foreach ($c in @({quoted})) {{ "
        "$w.SendKeys($c); "
        f"Start-Sleep -Milliseconds {chunk_ms} }}"
    )

    # Bounded by chunk pacing above, plus headroom for PowerShell's own
    # startup so a slow COM handshake doesn't look like a hang.
    timeout_seconds = min(max_seconds, chunk_ms * len(chunks) / 1000) + 20

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "typing timed out"}

    return {
        "ok": True,
        "app": name,
        "window": foreground,
        "characters": len(text),
    }


def act_active_window(_body):
    """Returns the current foreground window's title via the Win32 API
    — the single focused window, distinct from act_active_apps' full
    list of open windows."""
    script = (
        "Add-Type @'\n"
        "using System;\n"
        "using System.Runtime.InteropServices;\n"
        "using System.Text;\n"
        "public class AtlasForeground {\n"
        "  [DllImport(\"user32.dll\")]\n"
        "  public static extern IntPtr GetForegroundWindow();\n"
        "  [DllImport(\"user32.dll\")]\n"
        "  public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);\n"
        "}\n"
        "'@ -ErrorAction SilentlyContinue; "
        "$h = [AtlasForeground]::GetForegroundWindow(); "
        "$sb = New-Object System.Text.StringBuilder 256; "
        "[AtlasForeground]::GetWindowText($h, $sb, 256) | Out-Null; "
        "Write-Output $sb.ToString()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=_NO_WINDOW,
    )
    title = result.stdout.strip()
    return {"ok": True, "title": title or None}


def act_control_status(_body):
    return _control_status_payload()


def act_control_stop(body):
    return _set_control_enabled(False, body.get("reason") or "remote stop")


def act_control_resume(_body):
    if not CONFIG.get("general_control_enabled", True):
        return {"ok": False, "error": "general control is disabled in config"}
    return _set_control_enabled(True)


def act_observe_desktop(_body):
    blocked = _require_control_enabled()
    if blocked:
        return blocked
    screenshot = act_screenshot({})
    if not screenshot.get("ok"):
        return screenshot
    cursor = {"x": None, "y": None}
    if os.name == "nt":
        import ctypes

        class Point(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = Point()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            cursor = {"x": point.x, "y": point.y}
    return {
        "ok": True,
        "image_b64": screenshot["image_b64"],
        "active_window": act_active_window({}).get("title"),
        "windows": act_active_apps({}).get("windows", []),
        "cursor": cursor,
    }


def act_desktop_input(body):
    blocked = _require_control_enabled()
    if blocked:
        return blocked
    action = str(body.get("action", "")).strip().lower()
    if action in {"move", "click", "double_click", "scroll"}:
        x = int(body.get("x", 0))
        y = int(body.get("y", 0))
        button = str(body.get("button", "left")).lower()
        if button not in {"left", "right", "middle"}:
            return {"ok": False, "error": "button must be left/right/middle"}
        flags = {
            "left": (0x0002, 0x0004),
            "right": (0x0008, 0x0010),
            "middle": (0x0020, 0x0040),
        }
        down, up = flags[button]
        clicks = 2 if action == "double_click" else 1
        delta = int(body.get("delta", 0))
        script = (
            "Add-Type @'\nusing System;using System.Runtime.InteropServices;"
            "public class AtlasInput{[DllImport(\"user32.dll\")]public static extern bool SetCursorPos(int X,int Y);"
            "[DllImport(\"user32.dll\")]public static extern void mouse_event(uint f,uint x,uint y,int d,UIntPtr e);}\n'@;"
            f"[AtlasInput]::SetCursorPos({x},{y})|Out-Null;"
        )
        if action in {"click", "double_click"}:
            script += "".join(
                f"[AtlasInput]::mouse_event({down},0,0,0,[UIntPtr]::Zero);"
                f"[AtlasInput]::mouse_event({up},0,0,0,[UIntPtr]::Zero);"
                for _ in range(clicks)
            )
        elif action == "scroll":
            script += (
                f"[AtlasInput]::mouse_event(0x0800,0,0,{delta},[UIntPtr]::Zero);"
            )
        result = _run_hidden_powershell(script)
        if result.returncode:
            return {"ok": False, "error": result.stderr.strip() or "input failed"}
        return {"ok": True, "action": action, "x": x, "y": y}

    if action == "drag":
        # Freehand drawing needs the button held down across a path. Without
        # this, the desktop agent could open Paint but had no action that
        # could leave a mark, so it clicked, saw an unchanged canvas, and
        # stalled out its whole step budget.
        raw_path = body.get("path")
        if not isinstance(raw_path, list) or len(raw_path) < 2:
            return {
                "ok": False,
                "error": "drag requires a path of at least two [x,y] points",
            }
        limit = int(CONFIG.get("general_control_max_drag_points", 200))
        if len(raw_path) > limit:
            return {"ok": False, "error": f"drag path exceeds {limit} points"}
        points = []
        for item in raw_path:
            if isinstance(item, dict):
                item = [item.get("x"), item.get("y")]
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                return {"ok": False, "error": "each drag point must be [x,y]"}
            try:
                points.append((int(item[0]), int(item[1])))
            except (TypeError, ValueError):
                return {"ok": False, "error": "drag coordinates must be integers"}

        button = str(body.get("button", "left")).lower()
        buttons = {
            "left": (0x0002, 0x0004),
            "right": (0x0008, 0x0010),
            "middle": (0x0020, 0x0040),
        }
        if button not in buttons:
            return {"ok": False, "error": "button must be left/right/middle"}
        down, up = buttons[button]

        # SetCursorPos teleports the pointer without emitting mouse-move
        # input, so Paint saw a press and a release at unrelated spots and
        # drew a dot. Real MOUSEEVENTF_MOVE|ABSOLUTE|VIRTUALDESK events are
        # what an app tracks as a stroke. Coordinates are normalized against
        # the virtual desktop, the same space observe_desktop screenshots use.
        dense: list[tuple[int, int]] = [points[0]]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            span = max(abs(x1 - x0), abs(y1 - y0))
            steps = max(1, min(120, span // _DRAG_SAMPLE_PIXELS))
            for step in range(1, steps + 1):
                dense.append((
                    int(round(x0 + (x1 - x0) * step / steps)),
                    int(round(y0 + (y1 - y0) * step / steps)),
                ))

        first_x, first_y = dense[0]
        moves = "".join(
            f"m {px} {py};" for px, py in dense[1:]
        )
        script = (
            "Add-Type @'\nusing System;using System.Runtime.InteropServices;"
            "public class AtlasInput{[DllImport(\"user32.dll\")]public static extern bool SetCursorPos(int X,int Y);"
            "[DllImport(\"user32.dll\")]public static extern void mouse_event(uint f,uint x,uint y,int d,UIntPtr e);}\n'@;"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$v=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "function m($x,$y){"
            "$nx=[int](($x-$v.Left)*65535/[Math]::Max(1,$v.Width-1));"
            "$ny=[int](($y-$v.Top)*65535/[Math]::Max(1,$v.Height-1));"
            f"[AtlasInput]::mouse_event({_MOUSE_MOVE_ABSOLUTE},$nx,$ny,0,[UIntPtr]::Zero);"
            "Start-Sleep -Milliseconds " + str(_DRAG_STEP_MILLISECONDS) + "};"
            f"m {first_x} {first_y};"
            "Start-Sleep -Milliseconds 120;"
            f"[AtlasInput]::mouse_event({down},0,0,0,[UIntPtr]::Zero);"
            "Start-Sleep -Milliseconds 80;"
            + moves +
            "Start-Sleep -Milliseconds 80;"
            f"[AtlasInput]::mouse_event({up},0,0,0,[UIntPtr]::Zero);"
        )

        result = _run_hidden_powershell(script, timeout=180)
        if result.returncode:
            return {"ok": False, "error": result.stderr.strip() or "drag failed"}
        return {
            "ok": True,
            "action": action,
            "points": len(dense),
            "start": {"x": first_x, "y": first_y},
            "end": {"x": dense[-1][0], "y": dense[-1][1]},
        }

    if action == "text":
        text = str(body.get("text", ""))
        limit = int(CONFIG.get("general_control_max_text_chars", 5000))
        if not text or len(text) > limit:
            return {"ok": False, "error": f"text must be 1-{limit} characters"}
        chunks = _sendkeys_chunks(text)
        quoted = ",".join(
            "'" + chunk.replace("'", "''") + "'" for chunk in chunks
        )
        script = (
            "$w=New-Object -ComObject WScript.Shell;"
            f"foreach($c in @({quoted})){{$w.SendKeys($c);Start-Sleep -Milliseconds 20}}"
        )
        result = _run_hidden_powershell(script, timeout=120)
        return {
            "ok": result.returncode == 0,
            "action": action,
            "characters": len(text),
            "error": result.stderr.strip() or None,
        }

    if action == "keys":
        keys = str(body.get("keys", "")).strip()
        if not keys or len(keys) > 200:
            return {"ok": False, "error": "keys must be 1-200 characters"}
        escaped = keys.replace("'", "''")
        result = _run_hidden_powershell(
            "$w=New-Object -ComObject WScript.Shell;"
            f"$w.SendKeys('{escaped}')"
        )
        return {
            "ok": result.returncode == 0,
            "action": action,
            "error": result.stderr.strip() or None,
        }
    return {
        "ok": False,
        "error": (
            "action must be move/click/double_click/drag/scroll/text/keys"
        ),
    }


def act_window_control(body):
    blocked = _require_control_enabled()
    if blocked:
        return blocked
    action = str(body.get("action", "focus")).strip().lower()
    title = str(body.get("title", "")).strip()
    if action == "list":
        return act_active_apps({})
    if not title:
        return {"ok": False, "error": "title is required"}
    if action == "focus":
        _focus_window(title)
        # Focus is asynchronous: the window manager needs a moment to
        # settle, and a single immediate check reported failure for a
        # window that did come forward.
        focused = False
        for _ in range(8):
            time.sleep(0.25)
            active = str(act_active_window({}).get("title") or "")
            if title.lower() in active.lower():
                focused = True
                break
        return {
            "ok": focused,
            "action": action,
            "title": title,
        }
    show_codes = {"minimize": 6, "maximize": 3, "restore": 9}
    escaped = title.replace("'", "''")
    if action == "close":
        operation = "$p.CloseMainWindow()|Out-Null"
    elif action in show_codes:
        operation = f"[AtlasWindow]::ShowWindow($p.MainWindowHandle,{show_codes[action]})|Out-Null"
    else:
        return {"ok": False, "error": "unknown window action"}
    script = (
        "Add-Type @'\nusing System;using System.Runtime.InteropServices;"
        "public class AtlasWindow{[DllImport(\"user32.dll\")]public static extern bool ShowWindow(IntPtr h,int n);}\n'@;"
        "$p=Get-Process|Where-Object{$_.MainWindowTitle -like "
        f"'*{escaped}*'}}|Select-Object -First 1;"
        "if(-not $p){exit 3};" + operation
    )
    result = _run_hidden_powershell(script)
    return {
        "ok": result.returncode == 0,
        "action": action,
        "title": title,
        "error": result.stderr.strip() or None,
    }


def act_clipboard(body):
    blocked = _require_control_enabled()
    if blocked:
        return blocked
    action = str(body.get("action", "read")).lower()
    if action == "read":
        result = _run_hidden_powershell("Get-Clipboard -Raw", timeout=15)
        return {
            "ok": result.returncode == 0,
            "text": result.stdout,
            "error": result.stderr.strip() or None,
        }
    if action == "write":
        text = str(body.get("text", ""))
        limit = int(CONFIG.get("general_control_max_text_chars", 5000))
        if len(text) > limit:
            return {"ok": False, "error": f"clipboard exceeds {limit} characters"}
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        script = (
            f"$b=[Convert]::FromBase64String('{encoded}');"
            "$t=[Text.Encoding]::UTF8.GetString($b);Set-Clipboard -Value $t"
        )
        result = _run_hidden_powershell(script, timeout=15)
        return {"ok": result.returncode == 0, "characters": len(text)}
    return {"ok": False, "error": "clipboard action must be read/write"}


def act_file_operation(body):
    blocked = _require_control_enabled()
    if blocked:
        return blocked
    operation = str(body.get("operation", "")).lower()
    path = _allowed_user_path(body.get("path"))
    limit = int(CONFIG.get("general_control_max_file_bytes", 10 * 1024 * 1024))
    if operation == "stat":
        return {
            "ok": True,
            "path": str(path),
            "exists": path.exists(),
            "is_dir": path.is_dir(),
            "size": path.stat().st_size if path.is_file() else None,
        }
    if operation == "list":
        if not path.is_dir():
            return {"ok": False, "error": "path is not a directory"}
        items = []
        for item in list(path.iterdir())[:500]:
            items.append({
                "name": item.name,
                "path": str(item),
                "is_dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else None,
            })
        return {"ok": True, "path": str(path), "items": items}
    if operation == "read":
        if not path.is_file() or path.stat().st_size > limit:
            return {"ok": False, "error": "file is missing or exceeds read limit"}
        data = path.read_bytes()
        return {
            "ok": True,
            "path": str(path),
            "data_b64": base64.b64encode(data).decode("ascii"),
        }
    if operation in {"write", "append"}:
        data_b64 = body.get("data_b64")
        text = body.get("text")
        data = (
            base64.b64decode(data_b64, validate=True)
            if isinstance(data_b64, str)
            else str(text or "").encode("utf-8")
        )
        if len(data) > limit:
            return {"ok": False, "error": "write exceeds configured limit"}
        _allowed_user_path(str(path.parent))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab" if operation == "append" else "wb") as handle:
            handle.write(data)
        return {"ok": True, "path": str(path), "bytes": len(data)}
    if operation == "mkdir":
        path.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(path)}
    if operation in {"copy", "move"}:
        destination = _allowed_user_path(body.get("destination"))
        _allowed_user_path(str(destination.parent))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if operation == "copy":
            if path.is_dir():
                shutil.copytree(path, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(path, destination)
        else:
            shutil.move(str(path), str(destination))
        return {"ok": True, "path": str(path), "destination": str(destination)}
    if operation == "delete":
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return {"ok": True, "path": str(path), "deleted": True}
    return {"ok": False, "error": "unknown file operation"}


def act_launch_process(body):
    blocked = _require_control_enabled()
    if blocked:
        return blocked
    executable = str(body.get("executable", "")).strip()
    arguments = body.get("arguments") or []
    if not executable or not isinstance(arguments, list) or not all(
        isinstance(item, str) for item in arguments
    ):
        return {"ok": False, "error": "executable and string arguments are required"}
    working_directory = body.get("working_directory")
    cwd = str(_allowed_user_path(working_directory)) if working_directory else None
    try:
        process = subprocess.Popen(
            [executable, *arguments],
            cwd=cwd,
            shell=False,
            creationflags=_NO_WINDOW if body.get("hidden") else 0,
        )
    except OSError as error:
        return {"ok": False, "error": str(error)}
    return {"ok": True, "pid": process.pid, "executable": executable}


def act_process_control(body):
    blocked = _require_control_enabled()
    if blocked:
        return blocked
    action = str(body.get("action", "list")).lower()
    if action == "list":
        script = (
            "Get-Process|Select-Object Id,ProcessName,MainWindowTitle|"
            "ConvertTo-Json -Compress"
        )
        result = _run_hidden_powershell(script, timeout=30)
        try:
            processes = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            processes = []
        return {"ok": result.returncode == 0, "processes": processes}
    if action == "stop":
        pid = int(body.get("pid", 0))
        if pid <= 0 or pid == os.getpid():
            return {"ok": False, "error": "invalid or protected process id"}
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_NO_WINDOW,
        )
        return {
            "ok": result.returncode == 0,
            "pid": pid,
            "error": result.stderr.strip() or None,
        }
    return {"ok": False, "error": "process action must be list/stop"}


def act_system_info(_body):
    """Read-only PC health: OS disk free %, CPU load %, RAM used %, and
    uptime. PowerShell/CIM only — no changes."""
    script = (
        "$os=Get-CimInstance Win32_OperatingSystem; "
        "$disk=Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='C:'\"; "
        "$cpu=(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average; "
        "$ramUsed=[math]::Round(100*($os.TotalVisibleMemorySize-$os.FreePhysicalMemory)/$os.TotalVisibleMemorySize); "
        "$diskFree=[math]::Round(100*$disk.FreeSpace/$disk.Size); "
        "$up=(Get-Date)-$os.LastBootUpTime; "
        "Write-Output (@{cpu=$cpu;ram_used=$ramUsed;disk_free=$diskFree;uptime_hours=[math]::Round($up.TotalHours)} | ConvertTo-Json -Compress)"
    )
    result = _run_hidden_powershell(script, timeout=20)
    try:
        return {"ok": True, **json.loads(result.stdout.strip())}
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "error": "could not read system info"}


def act_slicer_status(_body):
    import urllib.request
    try:
        with urllib.request.urlopen(CONFIG["slicer_status_url"], timeout=5) as response:
            return {"ok": True, "status": response.read().decode()[:2000]}
    except Exception as error:
        return {"ok": False, "error": f"slicer unreachable: {error}"}


ACTIONS = {
    "open_fusion": act_open_fusion,
    "open_project": act_open_project,
    "newest_screenshot": act_newest_screenshot,
    "volume": act_volume,
    "media": act_media,
    "open_folder": act_open_folder,
    "screenshot": act_screenshot,
    "active_apps": act_active_apps,
    "run_script": act_run_script,
    "youtube_search": act_youtube_search,
    "slicer_status": act_slicer_status,
    "system_info": act_system_info,
    "open_app": act_open_app,
    "focus_or_open_app": act_focus_or_open_app,
    "type_text": act_type_text,
    "active_window": act_active_window,
    "capture_screenshot": act_capture_screenshot,
    "capture_window": act_capture_window,
    "start_recording": act_start_recording,
    "stop_recording": act_stop_recording,
    "list_recordings": act_list_recordings,
    "shutdown_pc": act_shutdown_pc,
    "cancel_pc_shutdown": act_cancel_pc_shutdown,
    "empty_recycle_bin": act_empty_recycle_bin,
    "control_status": act_control_status,
    "control_stop": act_control_stop,
    "control_resume": act_control_resume,
    "observe_desktop": act_observe_desktop,
    "desktop_input": act_desktop_input,
    "window_control": act_window_control,
    "clipboard": act_clipboard,
    "file_operation": act_file_operation,
    "launch_process": act_launch_process,
    "process_control": act_process_control,
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        return self.headers.get("X-Companion-Token", "") == CONFIG["token"]

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "service": "atlas-companion"})
        self._send(404, {"ok": False, "error": "unknown path"})

    def do_POST(self):
        if not self._authed():
            return self._send(401, {"ok": False, "error": "invalid token"})

        action_name = self.path.lstrip("/")
        action = ACTIONS.get(action_name)

        if action is None:
            return self._send(404, {"ok": False, "error": "unknown action"})

        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = {}

        try:
            result = action(body)
            if action_name in _GENERAL_CONTROL_ACTIONS:
                _audit_control(action_name, body, result)
            self._send(200, result)
        except Exception as error:
            self._send(500, {"ok": False, "error": str(error)})

    def log_message(self, *args):
        pass  # quiet


def main():
    ensure_config_file()
    global CONFIG
    CONFIG = load_config()

    if CONFIG["token"] == "CHANGE_ME":
        print("Refusing to start with the default token — set one in companion_config.json.")
        return

    _reconcile_orphaned_recording()
    _set_control_enabled(CONFIG.get("general_control_enabled", True))
    threading.Thread(
        target=_emergency_hotkey_monitor,
        name="atlas-emergency-hotkey",
        daemon=True,
    ).start()

    server = ThreadingHTTPServer((CONFIG["bind_host"], CONFIG["bind_port"]), Handler)
    print(f"A.T.L.A.S. companion listening on {CONFIG['bind_host']}:{CONFIG['bind_port']}")
    server.serve_forever()


if __name__ == "__main__":
    main()
