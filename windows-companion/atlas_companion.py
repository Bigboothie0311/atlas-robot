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
    # name -> full command list. ONLY these predefined scripts can run.
    "maintenance_scripts": {
        "clear_temp": ["cmd", "/c", "del", "/q", "/s", r"%TEMP%\*"],
    },
    "slicer_status_url": "http://127.0.0.1:8899/status",
}


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        print(f"Wrote default config to {CONFIG_PATH} — edit it and restart.")
        return DEFAULT_CONFIG

    return {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text())}


CONFIG = load_config()


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
    "slicer_status": act_slicer_status,
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
    if CONFIG["token"] == "CHANGE_ME":
        print("Refusing to start with the default token — set one in companion_config.json.")
        return

    server = ThreadingHTTPServer((CONFIG["bind_host"], CONFIG["bind_port"]), Handler)
    print(f"A.T.L.A.S. companion listening on {CONFIG['bind_host']}:{CONFIG['bind_port']}")
    server.serve_forever()


if __name__ == "__main__":
    main()
