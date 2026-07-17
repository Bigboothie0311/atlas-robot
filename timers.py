"""Countdown timers and focus mode for the hub.

State lives in the robot_hub process (the same one that owns /state and
speech), guarded by a lock. A 1-second watcher thread announces expiry —
the 120s proactive poll is far too coarse for a kitchen timer.

Focus mode is deliberately similar but separate: a timer speaks when it
ends and otherwise changes nothing; focus mode also mutes proactive
nudges while active (checked by robot_hub) and dims the HUD (rendered by
hud/app.js from /state).
"""
import json
import threading
import time
from pathlib import Path

MAX_TIMER_SECONDS = 12 * 3600
DEFAULT_FOCUS_MINUTES = 25
WATCHER_POLL_SECONDS = 1

# How long the HUD keeps flashing after a timer fires.
ALERT_VISIBLE_SECONDS = 8

STATE_PATH = Path("/home/atlas/atlas-robot/data/timer_state.json")

_lock = threading.Lock()

_timer = None  # {"ends_at", "total_seconds", "label"}
_focus = None  # {"ends_at", "total_seconds"}
_last_alert_at = 0.0


def _persist_locked():
    """Caller must hold _lock. Writes current timer/focus state to disk so
    a hub restart mid-timer doesn't silently eat it — on reload, an
    already-expired timer just fires immediately on the watcher's first
    tick."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = STATE_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps({"timer": _timer, "focus": _focus}))
    temporary_path.replace(STATE_PATH)


def _load_persisted_state():
    global _timer, _focus

    if not STATE_PATH.exists():
        return

    try:
        data = json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return

    with _lock:
        timer = data.get("timer")
        focus = data.get("focus")

        if isinstance(timer, dict) and "ends_at" in timer:
            _timer = timer

        if isinstance(focus, dict) and "ends_at" in focus:
            _focus = focus


_load_persisted_state()


def start_timer(seconds, label=None):
    global _timer

    seconds = max(1, min(int(seconds), MAX_TIMER_SECONDS))

    with _lock:
        _timer = {
            "ends_at": time.time() + seconds,
            "total_seconds": seconds,
            "label": (label or "").strip() or None,
        }
        _persist_locked()

    return seconds


def cancel_timer():
    """Returns True if there was a timer to cancel."""
    global _timer

    with _lock:
        had_timer = _timer is not None
        _timer = None
        _persist_locked()

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
        _persist_locked()

    return seconds


def end_focus():
    """Returns True if focus mode was active."""
    global _focus

    with _lock:
        was_active = _focus is not None
        _focus = None
        _persist_locked()

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

    return {
        "timer": timer_state,
        "focus": focus_state,
        # Drives the HUD's expiry flash — true briefly after a timer fires.
        "timer_alert": now - _last_alert_at < ALERT_VISIBLE_SECONDS,
    }


def watcher_loop(speak, log, chime=None):
    """Announces timer/focus expiry. speak(text), log(text), and the
    optional chime() are injected by robot_hub so this module stays free
    of Flask/audio plumbing. A timer expiry chimes audibly before the
    spoken line; focus wrap-ups stay speech-only (they're a wind-down,
    not an alarm)."""
    global _timer, _focus, _last_alert_at

    while True:
        time.sleep(WATCHER_POLL_SECONDS)

        announcement = None
        is_timer_expiry = False

        with _lock:
            now = time.time()

            if _timer is not None and now >= _timer["ends_at"]:
                label = _timer["label"]
                _timer = None
                _last_alert_at = now
                is_timer_expiry = True
                _persist_locked()
                announcement = (
                    f"Time's up: {label}." if label else "Your timer is done."
                )
            elif _focus is not None and now >= _focus["ends_at"]:
                minutes = round(_focus["total_seconds"] / 60)
                _focus = None
                _persist_locked()
                minute_word = "minute" if minutes == 1 else "minutes"
                announcement = (
                    f"Focus session complete — that was {minutes} "
                    f"{minute_word}. Nice work."
                )

        if announcement is None:
            continue

        try:
            if is_timer_expiry and chime is not None:
                chime()

            log(announcement)
            speak(announcement)
        except Exception as error:
            print("Timer announcement failed:", type(error).__name__, error, flush=True)
