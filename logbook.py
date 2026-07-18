"""Persistent, structured, locally-rotated interaction + incident logs.

Replaces the RAM-only diagnostic history. Every voice turn appends one
JSON object to data/logs/interactions.jsonl; incidents (recovery actions)
go to data/logs/incidents.jsonl. Both rotate by size so they can't grow
unbounded on the Pi's SD card. All local, zero tokens — A.T.L.A.S. reads
these back to answer diagnostic questions ("why didn't you hear me",
"what went wrong earlier").
"""
import json
import time
from pathlib import Path

LOG_DIR = Path("/home/atlas/atlas-robot/data/logs")
INTERACTIONS_PATH = LOG_DIR / "interactions.jsonl"
INCIDENTS_PATH = LOG_DIR / "incidents.jsonl"

ROTATE_BYTES = 2 * 1024 * 1024  # 2 MB per file
ROTATE_KEEP = 5                 # keep this many rotated generations

# wake_listener stashes the accepted wake word's confidence/RMS here right
# before handing off to a turn, so the turn logger can include it without
# threading the value through the call chain.
_pending_wake = {}


def set_pending_wake(confidence, peak_rms, mic_device):
    _pending_wake.clear()
    _pending_wake.update({
        "wake_confidence": confidence,
        "wake_peak_rms": peak_rms,
        "microphone": mic_device,
    })


def _rotate_if_needed(path):
    try:
        if not path.exists() or path.stat().st_size < ROTATE_BYTES:
            return

        # Shift .N -> .N+1, dropping the oldest.
        oldest = path.with_suffix(path.suffix + f".{ROTATE_KEEP}")
        if oldest.exists():
            oldest.unlink()

        for generation in range(ROTATE_KEEP - 1, 0, -1):
            src = path.with_suffix(path.suffix + f".{generation}")
            if src.exists():
                src.rename(path.with_suffix(path.suffix + f".{generation + 1}"))

        path.rename(path.with_suffix(path.suffix + ".1"))
    except OSError as error:
        print("Log rotation failed:", error, flush=True)


def _append(path, record):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed(path)

    try:
        with open(path, "a") as log_file:
            log_file.write(json.dumps(record) + "\n")
    except OSError as error:
        print("Log append failed:", error, flush=True)


# ---------------------------------------------------------------------
# Per-turn logging
# ---------------------------------------------------------------------

class TurnRecord:
    """Accumulates one voice turn's fields; written on finish()."""

    def __init__(self):
        self.start = time.monotonic()
        self.data = {
            "ts": time.time(),
            "wake_confidence": _pending_wake.get("wake_confidence"),
            "wake_peak_rms": _pending_wake.get("wake_peak_rms"),
            "microphone": _pending_wake.get("microphone"),
            "audio_rms": None,
            "transcript": None,
            "intent": None,
            "tools": [],
            "output_device": None,
            "errors": [],
            "latency_ms": None,
            "outcome": None,
        }

    def set(self, **fields):
        self.data.update(fields)

    def add_tool(self, tool):
        self.data["tools"].append(tool)

    def add_error(self, error):
        self.data["errors"].append(str(error))

    def finish(self, outcome):
        self.data["outcome"] = outcome
        self.data["latency_ms"] = round((time.monotonic() - self.start) * 1000)
        _append(INTERACTIONS_PATH, self.data)


def start_turn():
    return TurnRecord()


def read_interactions(limit=50):
    """Most recent interaction records, newest last."""
    if not INTERACTIONS_PATH.exists():
        return []

    try:
        lines = INTERACTIONS_PATH.read_text().splitlines()
    except OSError:
        return []

    records = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return records


def diagnostic_summary(window=50):
    """Aggregates recent turns for a spoken diagnostic answer."""
    records = read_interactions(window)

    if not records:
        return {
            "count": 0,
            "errors": 0,
            "avg_latency_ms": None,
            "avg_wake_confidence": None,
            "last_error": None,
        }

    errors = [r for r in records if r.get("errors")]
    latencies = [r["latency_ms"] for r in records if r.get("latency_ms")]
    confidences = [
        r["wake_confidence"] for r in records
        if isinstance(r.get("wake_confidence"), (int, float))
    ]

    return {
        "count": len(records),
        "errors": len(errors),
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "avg_wake_confidence": round(sum(confidences) / len(confidences), 2) if confidences else None,
        "last_error": errors[-1]["errors"][0] if errors else None,
        "last_transcript": records[-1].get("transcript"),
    }


# ---------------------------------------------------------------------
# Incident logging (recovery playbooks)
# ---------------------------------------------------------------------

def record_incident(component, cause, action, verification, resolved):
    """Persists one recovery incident report."""
    _append(INCIDENTS_PATH, {
        "ts": time.time(),
        "component": component,
        "cause": cause,
        "action": action,
        "verification": verification,
        "resolved": bool(resolved),
    })


def read_incidents(limit=20):
    if not INCIDENTS_PATH.exists():
        return []

    try:
        lines = INCIDENTS_PATH.read_text().splitlines()
    except OSError:
        return []

    records = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return records
