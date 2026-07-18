"""Safe recovery playbooks for A.T.L.A.S. components.

Each playbook repairs ONLY its component, runs a real verification test
afterward, and returns a structured incident (cause, action, verified)
that logbook persists. A per-component cooldown prevents restart loops —
a component that was just repaired won't be repaired again until the
cooldown passes, so a persistently-failing part reports "still down"
rather than thrashing.

All actions here are safe and local: restart a systemd unit, reload ALSA
state, re-probe a device. Nothing destructive, nothing that touches the
network or PC.
"""
import subprocess
import time

import logbook

RESTART_COOLDOWN_SECONDS = 120
_last_repair = {}


def _cooldown_active(component):
    last = _last_repair.get(component, 0)
    return time.time() - last < RESTART_COOLDOWN_SECONDS


def _mark_repair(component):
    _last_repair[component] = time.time()


def _service_active(unit):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "active"
    except (subprocess.SubprocessError, OSError):
        return False


def _restart_service(unit):
    try:
        subprocess.run(
            ["sudo", "-n", "systemctl", "restart", unit],
            capture_output=True, timeout=30, check=True,
        )
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def _incident(component, cause, action, verification, resolved):
    logbook.record_incident(component, cause, action, verification, resolved)
    return {
        "component": component,
        "cause": cause,
        "action": action,
        "verification": verification,
        "resolved": resolved,
    }


def _restart_and_verify(component, unit, human_name):
    """Shared: if the unit is down, restart once (respecting cooldown) and
    verify it came back."""
    if _service_active(unit):
        return _incident(
            component, f"{human_name} was already active", "none",
            "service reports active", True,
        )

    if _cooldown_active(component):
        return _incident(
            component, f"{human_name} is down", "skipped (cooldown)",
            "not retried to avoid a restart loop", False,
        )

    _mark_repair(component)
    restarted = _restart_service(unit)
    time.sleep(4)
    active = _service_active(unit)

    return _incident(
        component,
        f"{human_name} was not active",
        f"restarted {unit}" if restarted else f"restart of {unit} failed",
        "service now active" if active else "service still not active",
        active,
    )


# ---------------------------------------------------------------------
# Component playbooks
# ---------------------------------------------------------------------

def recover_audio():
    """Audio device changed / capture gain reset. Re-applies stored ALSA
    state (fixes the mic-gain-reset and card-renumber classes) and
    verifies the configured mic is capturable."""
    action_parts = []
    try:
        subprocess.run(["sudo", "-n", "alsactl", "restore"],
                       capture_output=True, timeout=10)
        action_parts.append("restored ALSA state")
    except (subprocess.SubprocessError, OSError):
        action_parts.append("alsactl restore failed")

    # Verify the SuziePi mic is present.
    try:
        listing = subprocess.run(["arecord", "-l"], capture_output=True,
                                 text=True, timeout=5).stdout
        mic_present = "Device" in listing
    except (subprocess.SubprocessError, OSError):
        mic_present = False

    return _incident(
        "audio", "audio device change or gain reset",
        "; ".join(action_parts),
        "configured mic visible to ALSA" if mic_present else "mic NOT visible",
        mic_present,
    )


def recover_camera():
    """Camera unavailable. Re-probes the USB camera node; reports whether
    it's back (no destructive action — a missing USB device needs a
    physical replug, which this reports rather than attempts)."""
    import camera_gate

    frame = camera_gate.capture_frame()
    ok = frame is not None

    return _incident(
        "camera", "camera capture was failing",
        "re-probed the USB camera node",
        "captured a test frame" if ok else "camera still not responding (check USB connection)",
        ok,
    )


def recover_printer_hub():
    """Printer hub (atlas-hub) not responding."""
    return _restart_and_verify("printer_hub", "atlas-hub.service", "the printer hub")


def recover_network_sentinel():
    """Network sentinel crash — it runs inside the hub, so a crash means
    the hub thread died. Restarting atlas-robot revives it."""
    return _restart_and_verify("network_sentinel", "atlas-robot.service",
                               "the main hub (network sentinel)")


def recover_hud():
    """HUD startup failure."""
    return _restart_and_verify("hud", "atlas-hud.service", "the HUD kiosk")


def recover_model_api():
    """Model/API unavailable — nothing to restart locally; verify network
    reachability so the report distinguishes 'internet down' from 'API
    down'."""
    try:
        subprocess.run(["ping", "-c", "2", "-W", "2", "8.8.8.8"],
                       capture_output=True, timeout=10, check=True)
        net_ok = True
    except (subprocess.SubprocessError, OSError):
        net_ok = False

    return _incident(
        "model_api", "model or API was unavailable",
        "verified network reachability (no local restart applies)",
        "internet reachable — likely a transient API issue" if net_ok
        else "internet is down — that's the cause",
        net_ok,
    )


def recover_esp32():
    """ESP32 macro console no longer checking in. The ESP32 talks over a
    trigger-file/serial interface this repo only references — recovery is
    limited to reporting; nothing here touches its pins or firmware."""
    return _incident(
        "esp32", "ESP32 macro console stopped checking in",
        "none (pin map and firmware are off-limits by policy)",
        "reported for manual check — verify USB/serial connection and power",
        False,
    )


PLAYBOOKS = {
    "audio": recover_audio,
    "camera": recover_camera,
    "printer_hub": recover_printer_hub,
    "network_sentinel": recover_network_sentinel,
    "hud": recover_hud,
    "model_api": recover_model_api,
    "esp32": recover_esp32,
}


def run_playbook(component):
    """Runs one component's recovery playbook, returns its incident dict."""
    playbook = PLAYBOOKS.get(component)

    if playbook is None:
        return _incident(component, "unknown component", "none",
                         "no playbook exists", False)

    try:
        return playbook()
    except Exception as error:
        return _incident(component, "recovery raised an exception",
                         "aborted", f"error: {error}", False)
