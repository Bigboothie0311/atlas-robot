"""Coordinates temporary ownership of the shared USB mic
(plughw:CARD=Device,DEV=0) between listen_and_answer.watch_for_barge_in
(which normally holds an arecord open on it for the whole duration of a
streamed answer) and any tool that needs exclusive access to the same
device for a bounded window — currently only
camera_gate.capture_clip()'s audio branch, when a self-recording is
requested mid-turn.

Deliberately fail-open: if the barge-in listener never confirms release
(crashed, not running, or just running late), request_yield() times out
and the caller proceeds anyway rather than blocking a spoken response
indefinitely. Barge-in briefly not working during that window is an
acceptable trade-off; a hung voice turn is not.
"""
import threading

_yield_requested = threading.Event()
_released = threading.Event()


def request_yield(timeout=3.0):
    """Asks the barge-in listener to release the mic device and waits for
    it to confirm. Returns True if release was confirmed in time, False
    on timeout (caller should proceed anyway — see module docstring)."""
    _released.clear()
    _yield_requested.set()
    return _released.wait(timeout=timeout)


def resume():
    """Signals the barge-in listener it's safe to reopen the mic."""
    _yield_requested.clear()


def yield_is_requested():
    return _yield_requested.is_set()


def confirm_released():
    _released.set()


def reset():
    """Test/process-start hook — clears any stuck state."""
    _yield_requested.clear()
    _released.clear()
