"""Countdown timers and focus mode for the hub.

State lives in the robot_hub process (the same one that owns /state and
speech), guarded by a lock. A 1-second watcher thread announces expiry —
the 120s proactive poll is far too coarse for a kitchen timer.

Focus mode is deliberately similar but separate: a timer speaks when it
ends and otherwise changes nothing; focus mode also mutes proactive
nudges while active (checked by robot_hub) and dims the HUD (rendered by
hud/app.js from /state).
"""
import threading
import time

MAX_TIMER_SECONDS = 12 * 3600
DEFAULT_FOCUS_MINUTES = 25
WATCHER_POLL_SECONDS = 1

_lock = threading.Lock()

_timer = None  # {"ends_at", "total_seconds", "label"}
_focus = None  # {"ends_at", "total_seconds"}


def start_timer(seconds, label=None):
    global _timer

    seconds = max(1, min(int(seconds), MAX_TIMER_SECONDS))

    with _lock:
        _timer = {
            "ends_at": time.time() + seconds,
            "total_seconds": seconds,
            "label": (label or "").strip() or None,
        }

    return seconds


def cancel_timer():
    """Returns True if there was a timer to cancel."""
    global _timer

    with _lock:
        had_timer = _timer is not None
        _timer = None

    return had_timer


def get_timer_remaining():
    """Returns (remaining_seconds, label) or None if no timer is running."""
    with _lock:
        if _timer is None:
            return None

        remaining = _timer["ends_at"] - time.time()

        if remaining <= 0:
            return None

        return round(remaining), _timer["label"]


def start_focus(minutes=DEFAULT_FOCUS_MINUTES):
    global _focus

    seconds = max(60, min(int(minutes) * 60, MAX_TIMER_SECONDS))

    with _lock:
        _focus = {
            "ends_at": time.time() + seconds,
            "total_seconds": seconds,
        }

    return seconds


def end_focus():
    """Returns True if focus mode was active."""
    global _focus

    with _lock:
        was_active = _focus is not None
        _focus = None

    return was_active


def in_focus():
    with _lock:
        return _focus is not None and time.time() < _focus["ends_at"]


def to_state_dict():
    """Timer/focus info for the /state payload, or Nones when inactive."""
    now = time.time()

    with _lock:
        timer_state = None
        focus_state = None

        if _timer is not None and now < _timer["ends_at"]:
            timer_state = {
                "remaining_seconds": round(_timer["ends_at"] - now),
                "total_seconds": _timer["total_seconds"],
                "label": _timer["label"],
            }

        if _focus is not None and now < _focus["ends_at"]:
            focus_state = {
                "remaining_seconds": round(_focus["ends_at"] - now),
                "total_seconds": _focus["total_seconds"],
            }

    return {"timer": timer_state, "focus": focus_state}


def watcher_loop(speak, log):
    """Announces timer/focus expiry. speak(text) and log(text) are injected
    by robot_hub so this module stays free of Flask/audio plumbing."""
    global _timer, _focus

    while True:
        time.sleep(WATCHER_POLL_SECONDS)

        announcement = None

        with _lock:
            now = time.time()

            if _timer is not None and now >= _timer["ends_at"]:
                label = _timer["label"]
                _timer = None
                announcement = (
                    f"Time's up: {label}." if label else "Your timer is done."
                )
            elif _focus is not None and now >= _focus["ends_at"]:
                minutes = round(_focus["total_seconds"] / 60)
                _focus = None
                minute_word = "minute" if minutes == 1 else "minutes"
                announcement = (
                    f"Focus session complete — that was {minutes} "
                    f"{minute_word}. Nice work."
                )

        if announcement is None:
            continue

        try:
            log(announcement)
            speak(announcement)
        except Exception as error:
            print("Timer announcement failed:", type(error).__name__, error, flush=True)
