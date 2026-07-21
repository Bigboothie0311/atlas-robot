"""Persistent, code-readable roadmap/feature ledger for the A.T.L.A.S.
V2/V3 upgrade catalog.

This is separate from conversational handoffs — it is the one place that
honestly records, per feature, whether something is not_started,
in_progress, implemented (code + tests exist), live_verified (actually
exercised against a running service/device), or blocked_external (waiting
on credentials, account authorization, public-posting approval, or
hardware that cannot be exercised from the terminal). Nothing may be
marked live_verified from unit tests alone — that field is only set when a
real service/endpoint check backed the claim.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEDGER_PATH = Path(
    "/home/atlas/atlas-robot/data/implementation_ledger.json"
)

VALID_STATES = {
    "not_started",
    "in_progress",
    "implemented",
    "live_verified",
    "blocked_external",
}

# One entry per phase from the master handoff, except Phase 1 which is
# broken into its four sub-features so real progress on the foundational
# phase is visible at the same granularity it was actually built.
DEFAULT_FEATURES: tuple[dict[str, Any], ...] = (
    {
        "feature_id": "phase1a_storage_monitoring",
        "phase": 1,
        "title": "Storage monitoring, thresholds, and safe cleanup",
    },
    {
        "feature_id": "phase1b_budget_ledger",
        "phase": 1,
        "title": "Shared monthly spend ledger with per-purpose accounting",
    },
    {
        "feature_id": "phase1c_premium_voice_subbudget",
        "phase": 1,
        "title": "Premium-voice sub-budget (warn/cutoff/local fallback)",
    },
    {
        "feature_id": "phase1d_implementation_ledger",
        "phase": 1,
        "title": "Machine-readable roadmap/implementation ledger",
    },
    {
        "feature_id": "phase2_observability",
        "phase": 2,
        "title": "Mission history, failure diagnosis, self-recovery",
    },
    {
        "feature_id": "phase3_pc_companion",
        "phase": 3,
        "title": "Complete Windows PC companion controls",
    },
    {
        "feature_id": "phase4_screen_capture",
        "phase": 4,
        "title": "Screen recording and capture foundation",
    },
    {
        "feature_id": "phase5_cinematic_voice",
        "phase": 5,
        "title": "Cinematic voice upgrade",
    },
    {
        "feature_id": "phase6_hud_overhaul",
        "phase": 6,
        "title": "Full cinematic JARVIS HUD overhaul",
    },
    {
        "feature_id": "phase7_gmail_agent",
        "phase": 7,
        "title": "Gmail agent",
    },
    {
        "feature_id": "phase8_calendar",
        "phase": 8,
        "title": "Calendar, reminders, and daily brief",
    },
    {
        "feature_id": "phase9_browser_agent",
        "phase": 9,
        "title": "Controlled browser agent",
    },
    {
        "feature_id": "phase10_coding_orchestration",
        "phase": 10,
        "title": "Codex and Claude Code orchestration",
    },
    {
        "feature_id": "phase11_showcase_media",
        "phase": 11,
        "title": "Self-showcase media pipeline",
    },
    {
        "feature_id": "phase12_instagram",
        "phase": 12,
        "title": "Instagram publishing and analytics",
    },
    {
        "feature_id": "phase13_camera_security",
        "phase": 13,
        "title": "Camera/security completion",
    },
    {
        "feature_id": "phase14_phone_workshop_routines",
        "phase": 14,
        "title": "Phone, workshop, printer, routines, proactive behavior",
    },
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_entry(feature: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature_id": feature["feature_id"],
        "phase": feature["phase"],
        "title": feature["title"],
        "state": "not_started",
        "commits": [],
        "tests": [],
        "live_verification": None,
        "external_blockers": [],
        "owner_approval_required": False,
        "last_updated": _now(),
        "evidence": [],
    }


def _seed_default_ledger() -> dict[str, dict[str, Any]]:
    return {
        feature["feature_id"]: _seed_entry(feature)
        for feature in DEFAULT_FEATURES
    }


def load_ledger(path: Path = LEDGER_PATH) -> dict[str, dict[str, Any]]:
    """Load the ledger, seeding it with all known phases on first use.
    Newly added default features are merged in without disturbing any
    feature the file already tracks."""
    seeded = _seed_default_ledger()

    if not path.exists():
        return seeded

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        data = {}

    if not isinstance(data, dict):
        return seeded

    merged = dict(seeded)
    merged.update(data)
    return merged


def save_ledger(
    ledger: dict[str, dict[str, Any]], path: Path = LEDGER_PATH
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(ledger, indent=2, sort_keys=True))
    temporary_path.replace(path)


def upsert_feature(
    feature_id: str,
    path: Path = LEDGER_PATH,
    **updates: Any,
) -> dict[str, Any]:
    """Update one feature's fields and persist the ledger. Unknown
    `feature_id`s are rejected rather than silently created, since the
    ledger's feature set is meant to track the fixed 14-phase roadmap."""
    ledger = load_ledger(path)

    if feature_id not in ledger:
        raise KeyError(f"Unknown ledger feature: {feature_id}")

    state = updates.get("state")

    if state is not None and state not in VALID_STATES:
        raise ValueError(
            f"Invalid ledger state {state!r}; must be one of "
            f"{sorted(VALID_STATES)}"
        )

    entry = dict(ledger[feature_id])
    entry.update(updates)
    entry["last_updated"] = _now()

    ledger[feature_id] = entry
    save_ledger(ledger, path)
    return entry


def get_feature(
    feature_id: str, path: Path = LEDGER_PATH
) -> dict[str, Any] | None:
    return load_ledger(path).get(feature_id)


def list_by_state(
    state: str, path: Path = LEDGER_PATH
) -> list[dict[str, Any]]:
    ledger = load_ledger(path)
    return sorted(
        (entry for entry in ledger.values() if entry["state"] == state),
        key=lambda entry: (entry["phase"], entry["feature_id"]),
    )


def summarize(path: Path = LEDGER_PATH) -> dict[str, Any]:
    """Answers 'what's finished', 'what remains', 'what's blocked', and
    'what did you implement last' from the ledger's actual state."""
    ledger = load_ledger(path)
    entries = sorted(
        ledger.values(), key=lambda entry: (entry["phase"], entry["feature_id"])
    )

    finished = [
        entry
        for entry in entries
        if entry["state"] in ("implemented", "live_verified")
    ]
    remaining = [
        entry
        for entry in entries
        if entry["state"] in ("not_started", "in_progress")
    ]
    blocked = [
        entry for entry in entries if entry["state"] == "blocked_external"
    ]

    dated_entries = [
        entry for entry in entries if entry.get("last_updated")
    ]
    last_updated_feature = (
        max(dated_entries, key=lambda entry: entry["last_updated"])
        if dated_entries
        else None
    )

    return {
        "finished": finished,
        "remaining": remaining,
        "blocked": blocked,
        "last_updated_feature": last_updated_feature,
        "counts": {
            "finished": len(finished),
            "remaining": len(remaining),
            "blocked": len(blocked),
            "total": len(entries),
        },
    }


def spoken_summary(path: Path = LEDGER_PATH) -> str:
    """A short, bounded spoken status for 'what upgrades are finished /
    what remains / what's blocked'."""
    summary = summarize(path)
    counts = summary["counts"]

    message = (
        f"{counts['finished']} of {counts['total']} upgrade items are "
        f"implemented, {counts['remaining']} remain, and "
        f"{counts['blocked']} are blocked on something external."
    )

    last = summary["last_updated_feature"]
    if last is not None:
        message += f" The last thing I finished was: {last['title']}."

    return message
