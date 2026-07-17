"""Red-alert protocol + print ETA tracking for the hub.

Red alert: a small set of genuinely-critical conditions (Pi core
overheating, disk nearly full, a failed print) flips the HUD into an
alert theme and speaks the reason once — including during quiet hours,
since these are exactly the events worth waking someone for. "Stand
down" acknowledges; the alert also clears itself when the condition
does.

Print ETA: the hub already polls printer progress; sampling (time,
percent) pairs lets us extrapolate a finish time instead of just
parroting a percentage.
"""
import threading
import time

PI_TEMP_CRITICAL_C = 85
DISK_CRITICAL_PERCENT = 95

_lock = threading.Lock()

_red_alert = {
    "active": False,
    "reason": None,
    "since": 0.0,
    "announced": False,
}

_print_samples = []  # [(monotonic_time, percent)] while a job runs
PRINT_SAMPLE_LIMIT = 12


def evaluate_red_alert(cpu_temp_c, disk_percent, printer_failed):
    """Called from the hub's proactive loop. Returns an announcement
    string the first time an alert trips, else None."""
    reason = None

    if printer_failed:
        reason = "the 3D print has failed"
    elif cpu_temp_c is not None and cpu_temp_c >= PI_TEMP_CRITICAL_C:
        reason = f"my core temperature is {cpu_temp_c:.0f} degrees"
    elif disk_percent is not None and disk_percent >= DISK_CRITICAL_PERCENT:
        reason = f"disk usage is at {disk_percent:.0f} percent"

    with _lock:
        if reason is None:
            if _red_alert["active"]:
                _red_alert.update(
                    {"active": False, "reason": None, "announced": False}
                )
            return None

        if _red_alert["active"]:
            return None  # already alerting; don't re-announce

        _red_alert.update({
            "active": True,
            "reason": reason,
            "since": time.time(),
            "announced": True,
        })

    return f"Red alert — {reason}."


def stand_down():
    """Acknowledges and clears the alert display. Returns True if one
    was active."""
    with _lock:
        was_active = _red_alert["active"]
        _red_alert.update({"active": False, "reason": None, "announced": False})

    return was_active


def red_alert_state():
    with _lock:
        return {
            "active": _red_alert["active"],
            "reason": _red_alert["reason"],
        }


def record_print_sample(state, progress_percent):
    """Feeds one printer poll into the ETA tracker. Non-building states
    reset the window so a new job doesn't inherit the old job's rate."""
    global _print_samples

    if state not in ("building", "printing") or progress_percent is None:
        _print_samples = []
        return

    now = time.monotonic()

    if _print_samples and progress_percent < _print_samples[-1][1]:
        _print_samples = []  # progress went backwards -> new job

    _print_samples.append((now, progress_percent))
    _print_samples = _print_samples[-PRINT_SAMPLE_LIMIT:]


def print_eta_minutes():
    """Extrapolated minutes remaining, or None without enough signal."""
    if len(_print_samples) < 2:
        return None

    (t0, p0), (t1, p1) = _print_samples[0], _print_samples[-1]

    if p1 <= p0 or t1 <= t0:
        return None

    rate = (p1 - p0) / (t1 - t0)  # percent per second

    if rate <= 0:
        return None

    remaining_percent = 100 - p1
    return max(1, round(remaining_percent / rate / 60))
