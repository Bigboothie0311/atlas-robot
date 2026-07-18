"""Emergency protocols — predefined procedures ONLY.

Every response here is a fixed, auditable sequence. Nothing is decided by
the model at runtime: "initiate emergency shutdown" always runs exactly
these steps in this order. Destructive procedures (Pi shutdown) require an
explicit confirmation from the caller — this module exposes the steps; the
voice layer gates them.

Split by hardware:
  POSSIBLE NOW (current hardware): data-preservation snapshot, printer
    pause on fault, over-temp response, spoken/logged/notified alarms,
    safe Pi shutdown.
  NEEDS NEW HARDWARE (planned, not built — see docstrings): smoke/water/
    air-quality sensors, smart plugs to cut power, a UPS for power-loss
    detection and battery-backed shutdown.
"""
import subprocess

import requests

import logbook

HUB = "http://127.0.0.1:5051"
ATLAS_HUB = "http://127.0.0.1:5050"


def _speak(text):
    try:
        requests.post(f"{HUB}/speak", json={"text": text}, timeout=30)
    except requests.RequestException:
        pass


def _red_alert_hud():
    try:
        requests.post(f"{HUB}/layout", json={"layout": "red_alert"}, timeout=5)
    except requests.RequestException:
        pass


def preserve_data():
    """Snapshot config + key data before anything drastic."""
    import system_health
    backup = system_health.create_backup()
    return backup is not None


def pause_printer():
    """Halt an active print — the safe response to a printer fault."""
    try:
        response = requests.get(f"{ATLAS_HUB}/atlas",
                                params={"cmd": "printer_pause"}, timeout=8)
        return "PRINTER PAUSE SENT" in response.text
    except requests.RequestException:
        return False


def emergency_shutdown(dry_run=False):
    """Predefined safe-shutdown sequence:
      1. red-alert the HUD + announce
      2. preserve data (backup snapshot)
      3. pause any active print
      4. log the incident
      5. safe-shutdown the Pi (skipped when dry_run)
    Returns a result dict. The voice layer must confirm before calling
    with dry_run=False."""
    steps = {}

    _red_alert_hud()
    _speak("Emergency shutdown initiated. Preserving data and powering down safely.")

    steps["data_preserved"] = preserve_data()
    steps["printer_paused"] = pause_printer()

    logbook.record_incident(
        "emergency", "emergency shutdown initiated",
        "preserved data, paused printer, shutting down",
        "sequence executed", True,
    )

    if not dry_run:
        try:
            subprocess.Popen(["sudo", "-n", "shutdown", "-h", "+1"])
            steps["shutdown_scheduled"] = True
        except (subprocess.SubprocessError, OSError):
            steps["shutdown_scheduled"] = False
    else:
        steps["shutdown_scheduled"] = "dry_run"

    return steps


def cancel_shutdown():
    """Aborts a scheduled shutdown (the +1 minute delay allows this)."""
    try:
        subprocess.run(["sudo", "-n", "shutdown", "-c"], timeout=10)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def overtemp_response(temp_c):
    """Predefined response to a dangerous Pi core temperature: alarm +
    log. Does NOT auto-shutdown (that stays a confirmed action) but tells
    the user it's imminent territory."""
    _red_alert_hud()
    message = (
        f"Warning: my core temperature is {temp_c:.0f} degrees, which is "
        "dangerously high. Reduce load or improve cooling now."
    )
    _speak(message)
    logbook.record_incident(
        "emergency", f"over-temperature {temp_c:.0f}C",
        "alarm raised", "reported to operator", True,
    )
    return message


# ---------------------------------------------------------------------
# NEEDS NEW HARDWARE — documented stubs, intentionally not active.
# ---------------------------------------------------------------------

def power_loss_response():
    """Requires a UPS with USB monitoring (e.g. apcupsd). With one, this
    would detect mains loss, announce, preserve data, and do a battery-
    backed shutdown before the battery dies. No UPS is connected."""
    return {"available": False, "needs": "UPS with USB monitoring"}


def environmental_alarm(kind):
    """Smoke / water / air-quality alarms need physical sensors (none
    connected). With sensors wired to the Pi's GPIO or the ESP32, this
    would trip the same alarm+preserve+notify sequence."""
    return {"available": False, "needs": f"{kind} sensor hardware"}


def cut_power(target):
    """Cutting power to the printer or PC needs a controllable smart plug
    (none connected). Would integrate as a confirmed action only."""
    return {"available": False, "needs": "smart plug"}
