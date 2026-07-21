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
import json
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
    },
    # name -> full command list. ONLY these predefined scripts can run.
    "maintenance_scripts": {
        "clear_temp": ["cmd", "/c", "del", "/q", "/s", r"%TEMP%\*"],
    },
    "slicer_status_url": "http://127.0.0.1:8899/status",
    # Screen recordings and standalone captures live here permanently —
    # the Pi only ever stages footage briefly before uploading it here.
    "recordings_folder": r"C:\Users\YOU\Videos\AtlasRecordings",
    "max_recording_seconds": 900,
    # Case-insensitive substrings of a window title that refuse a
    # screenshot/window-capture/recording of that window outright.
    "privacy_blocked_window_substrings": [
        "password", "1password", "bitwarden", "keychain",
        "gmail", "bank", "venmo", "paypal", "signal",
        "private browsing", "incognito",
    ],
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
    subprocess.run(["powershell", "-NoProfile", "-Command", script], timeout=10)
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
    subprocess.run(["powershell", "-NoProfile", "-Command", script], timeout=10)
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
    subprocess.run(["powershell", "-NoProfile", "-Command", script], timeout=20)
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
    subprocess.run(["powershell", "-NoProfile", "-Command", script], timeout=20)

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
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=20,
    )
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
    command.extend(["-t", str(max_seconds), str(out)])

    try:
        process = subprocess.Popen(command)
    except OSError as error:
        return {"ok": False, "error": f"could not start ffmpeg: {error}"}

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


def act_stop_recording(_body):
    """Stops the in-progress recording and verifies the file actually
    landed on disk with real bytes before reporting success."""
    state = _load_recording_state()
    active = state.get("active")

    if not active:
        return {"ok": False, "error": "no recording is in progress"}

    pid = active.get("pid")
    path = Path(active["path"])

    if pid and _pid_running(pid):
        try:
            subprocess.run(["taskkill", "/PID", str(pid)], timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(1)  # let ffmpeg flush the moov atom cleanly

    _save_recording_state({"active": None})

    if not path.is_file() or path.stat().st_size == 0:
        return {"ok": False, "error": "recording file is missing or empty"}

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
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True, timeout=15)
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

    subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)

    if body.get("fullscreen", True):
        # Give the browser a moment to open, then send F11.
        script = (
            "Start-Sleep -Seconds 3; "
            "$w = New-Object -ComObject WScript.Shell; "
            "$w.SendKeys('{F11}')"
        )
        subprocess.Popen(["powershell", "-NoProfile", "-Command", script])

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
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=20,
    )
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
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True, timeout=15)
    return [t.strip() for t in result.stdout.splitlines() if t.strip()]


def _focus_window(match_substring):
    """Brings the first window whose title contains match_substring to
    the foreground. Title-based (WScript.Shell.AppActivate) — no
    coordinates, no clicks, nothing arbitrary."""
    escaped = match_substring.replace("'", "''")
    script = (
        "$w = New-Object -ComObject WScript.Shell; "
        f"$w.AppActivate('{escaped}')"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], timeout=10)


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
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True, timeout=15)
    title = result.stdout.strip()
    return {"ok": True, "title": title or None}


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
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True, timeout=20)
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
    "active_window": act_active_window,
    "capture_screenshot": act_capture_screenshot,
    "capture_window": act_capture_window,
    "start_recording": act_start_recording,
    "stop_recording": act_stop_recording,
    "list_recordings": act_list_recordings,
    "shutdown_pc": act_shutdown_pc,
    "cancel_pc_shutdown": act_cancel_pc_shutdown,
    "empty_recycle_bin": act_empty_recycle_bin,
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
            self._send(200, action(body))
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

    server = ThreadingHTTPServer((CONFIG["bind_host"], CONFIG["bind_port"]), Handler)
    print(f"A.T.L.A.S. companion listening on {CONFIG['bind_host']}:{CONFIG['bind_port']}")
    server.serve_forever()


if __name__ == "__main__":
    main()
