"""Pi-side client for the Windows companion (windows-companion/).

Thin authenticated wrapper — every call posts to a whitelisted companion
action with the shared token. Reads PC_COMPANION_URL and
PC_COMPANION_TOKEN from config/robot.env (gitignored). If the companion
isn't configured or reachable, every call degrades to a spoken-friendly
error rather than raising.
"""
import base64

import requests

import robot_config

HUB = "http://127.0.0.1:5051"


def _companion_url():
    return robot_config.get("PC_COMPANION_URL")


def _token():
    return robot_config.get("PC_COMPANION_TOKEN")


def is_configured():
    return bool(_companion_url() and _token())


def _call(action, body=None, timeout=25):
    """Posts to a companion action. Returns (ok, data_or_error_string)."""
    if not is_configured():
        return False, "The PC companion isn't set up yet."

    try:
        response = requests.post(
            f"{_companion_url().rstrip('/')}/{action}",
            json=body or {},
            headers={"X-Companion-Token": _token()},
            timeout=timeout,
        )
    except requests.RequestException:
        return False, "I couldn't reach your PC. Is it on and the companion running?"

    if response.status_code == 401:
        return False, "The PC companion rejected my token."

    try:
        data = response.json()
    except ValueError:
        return False, "The PC companion sent back something I couldn't read."

    if not data.get("ok"):
        return False, data.get("error", "the PC companion reported an error")

    return True, data


def pc_reachable():
    """Quick health check for the companion (used before wake-dependent
    actions)."""
    if not is_configured():
        return False

    try:
        response = requests.get(f"{_companion_url().rstrip('/')}/health", timeout=4)
        return response.status_code == 200
    except requests.RequestException:
        return False


def open_fusion():
    ok, data = _call("open_fusion")
    return "Opening Fusion 360." if ok else data


def open_spotify():
    ok, data = _call("open_spotify")
    return "Opening Spotify." if ok else data


def open_claude():
    ok, data = _call("open_claude")
    return "Opening Claude." if ok else data


def open_app(app_name):
    """Opens an app from the companion's approved_apps whitelist (optional
    companion action). Degrades gracefully if unsupported."""
    ok, data = _call("open_app", {"app": app_name})
    return ok, (f"Opening {app_name}." if ok else data)


def set_volume_level(level):
    """Sets an absolute PC volume 0-100 via repeated volume-down then up
    steps (companion media keys are relative, so normalize from 0)."""
    # Floor to 0 first (many downs), then step up to target. Media-key
    # steps are ~2% each on Windows.
    _call("volume", {"action": "down", "repeat": 50})
    ups = max(0, min(100, int(level))) // 2
    if ups:
        _call("volume", {"action": "up", "repeat": ups})
    return True


def open_spotify():
    ok, data = _call("open_spotify")
    return "Opening Spotify." if ok else data


def open_claude():
    ok, data = _call("open_claude")
    return "Opening Claude." if ok else data


def empty_recycle_bin():
    ok, data = _call("empty_recycle_bin")
    return "The Recycle Bin is empty." if ok else data


def shutdown_pc():
    ok, data = _call("shutdown_pc")
    return "Your PC will shut down in one minute. Say cancel PC shutdown to abort." if ok else data


def cancel_pc_shutdown():
    ok, data = _call("cancel_pc_shutdown")
    return "PC shutdown cancelled." if ok else data


def open_project(name):
    ok, data = _call("open_project", {"project": name})
    return f"Opening {name}." if ok else data


def set_volume(action, repeat=2):
    ok, data = _call("volume", {"action": action, "repeat": repeat})
    return {"up": "Volume up.", "down": "Volume down.", "mute": "Muted."}.get(action) \
        if ok else data


def media(action):
    ok, data = _call("media", {"action": action})
    labels = {"playpause": "Done.", "next": "Next track.", "previous": "Previous track."}
    return labels.get(action, "Done.") if ok else data


def open_folder(name):
    ok, data = _call("open_folder", {"folder": name})
    return f"Opening {name}." if ok else data


def active_apps():
    ok, data = _call("active_apps")

    if not ok:
        return data

    windows = data.get("windows", [])

    if not windows:
        return "Nothing with a window is open on your PC right now."

    return f"You have {len(windows)} windows open: " + ", ".join(windows[:8]) + "."


def screenshot_to_hud(caption="PC screen"):
    """Grabs the PC screen and shows it on the HUD. Returns spoken text."""
    ok, data = _call("screenshot")

    if not ok:
        return data

    return _push_image_to_hud(data.get("image_b64"), caption)


def newest_screenshot_to_hud():
    ok, data = _call("newest_screenshot")

    if not ok:
        return data

    return _push_image_to_hud(data.get("image_b64"), data.get("name", "screenshot"))


def _push_image_to_hud(image_b64, caption):
    """Decodes a base64 image from the companion and displays it on the
    HUD via the hub's existing image overlay."""
    if not image_b64:
        return "The PC didn't send back an image."

    try:
        raw = base64.b64decode(image_b64)
        path = "/tmp/atlas_pc_image.png"
        with open(path, "wb") as image_file:
            image_file.write(raw)

        requests.post(f"{HUB}/show_local_image",
                      json={"path": path, "caption": caption, "duration": 20},
                      timeout=10)
        return f"Here's {caption} on my screen."
    except (ValueError, OSError, requests.RequestException) as error:
        print("PC image display failed:", error, flush=True)
        return "I got the image but couldn't display it."


def youtube_search(query):
    """Opens a YouTube search on the PC (long tutorials, no Shorts) and
    full-screens it. Wakes the PC first if the companion isn't reachable.
    Returns spoken text."""
    import time

    import pc_power

    if not is_configured():
        return "The PC companion isn't set up yet, so I can't drive the browser."

    if not pc_reachable():
        # Try to wake it, then wait for the companion to come up.
        pc_power.send_wake_packet()
        for _ in range(12):
            time.sleep(5)
            if pc_reachable():
                break
        else:
            return (
                "I couldn't reach your PC to run the search. It may be off, "
                "and wake-on-LAN isn't working through the current adapter."
            )

    ok, data = _call("youtube_search", {"query": query, "fullscreen": True})

    if not ok:
        return data

    return "I found several walkthroughs. Results are ready on your PC."


def run_maintenance(script):
    ok, data = _call("run_script", {"script": script}, timeout=130)

    if not ok:
        return data

    return f"Ran {script} on your PC. Exit code {data.get('exit_code')}."


def slicer_status():
    ok, data = _call("slicer_status")
    return data.get("status") if ok else data


def pc_health_report():
    """Read-only PC health via the companion, spoken. Flags anything that
    crosses a safe threshold and proposes the whitelisted maintenance
    script, but never runs it without a follow-up confirmation."""
    ok, data = _call("system_info")

    if not ok:
        return data

    cpu = data.get("cpu")
    ram = data.get("ram_used")
    disk_free = data.get("disk_free")
    uptime_hours = data.get("uptime_hours")

    parts = [f"Your PC's CPU is at {cpu} percent, RAM {ram} percent used, "
             f"and {disk_free} percent disk free"]

    concerns = []
    if disk_free is not None and disk_free < 10:
        concerns.append("disk is nearly full")
    if uptime_hours is not None and uptime_hours > 168:
        concerns.append(f"it's been up {uptime_hours // 24} days — a reboot might help")

    if concerns:
        parts.append("Worth noting: " + " and ".join(concerns))
        parts.append("I can run a cleanup if you want — just say clean up my pc")
    else:
        parts.append("It looks healthy")

    return ". ".join(parts) + "."


def run_pc_cleanup():
    """Runs the approved 'clear_temp' maintenance script. Only reached via
    an explicit cleanup command — the health report proposes, this acts."""
    return run_maintenance("clear_temp")
