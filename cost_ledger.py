"""Shared, additive monthly spend ledger.

Wraps the same usage file `listen_and_answer.py` already uses
(`data/openai_usage.json`) so there is exactly one authoritative ledger —
this module does not create a second, disconnected budget tracker. It
extends the existing schema with optional, backward-compatible per-purpose
accounting and a separate premium-voice sub-budget, while leaving
`listen_and_answer.load_usage()`/`save_usage()` and their existing
`spent_usd`/`requests`/`month` contract untouched.

Any subsystem that wants its spend counted against the real budget (nested
planner calls, retries, premium voice, coding-agent usage, mission cost
attribution) can call `record_spend()` and `save_ledger()` here against the
same file listen_and_answer.py reads.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import robot_config

USAGE_PATH = Path("/home/atlas/atlas-robot/data/openai_usage.json")

MONTHLY_LIMIT_USD = 8.00
NEXT_REQUEST_RESERVE_USD = 0.01

PREMIUM_VOICE_WARN_USD = 3.50
PREMIUM_VOICE_CUTOFF_USD = 5.00


class BudgetExceeded(Exception):
    pass


def current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _empty_ledger(month: str) -> dict[str, Any]:
    return {
        "month": month,
        "spent_usd": 0.0,
        "requests": 0,
        "by_purpose": {},
        "premium_voice_spent_usd": 0.0,
    }


def load_ledger(path: Path = USAGE_PATH) -> dict[str, Any]:
    """Read the shared usage ledger, rolling over to zero on a new month.
    Missing optional keys are backfilled so a file written before this
    module existed (just month/spent_usd/requests) still loads cleanly."""
    month = current_month()

    if not path.exists():
        return _empty_ledger(month)

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        data = {}

    if data.get("month") != month:
        return _empty_ledger(month)

    return {
        "month": month,
        "spent_usd": float(data.get("spent_usd", 0.0)),
        "requests": int(data.get("requests", 0)),
        "by_purpose": dict(data.get("by_purpose", {})),
        "premium_voice_spent_usd": float(
            data.get("premium_voice_spent_usd", 0.0)
        ),
    }


def save_ledger(data: dict[str, Any], path: Path = USAGE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(data, indent=2))
    temporary_path.replace(path)


def record_spend(
    ledger: dict[str, Any],
    amount_usd: float,
    purpose: str = "general",
    is_premium_voice: bool = False,
) -> dict[str, Any]:
    """Returns a new ledger dict — never mutates `ledger` — with
    `amount_usd` attributed to spent_usd, by_purpose[purpose], and, when
    is_premium_voice is set, the separate premium-voice sub-budget."""
    by_purpose = dict(ledger.get("by_purpose", {}))
    by_purpose[purpose] = by_purpose.get(purpose, 0.0) + amount_usd

    updated = dict(ledger)
    updated["spent_usd"] = ledger.get("spent_usd", 0.0) + amount_usd
    updated["by_purpose"] = by_purpose

    if is_premium_voice:
        updated["premium_voice_spent_usd"] = (
            ledger.get("premium_voice_spent_usd", 0.0) + amount_usd
        )

    return updated


def check_budget(
    ledger: dict[str, Any],
    limit: float = MONTHLY_LIMIT_USD,
    reserve: float = NEXT_REQUEST_RESERVE_USD,
) -> None:
    """Raise BudgetExceeded when starting another request would push spend
    past `limit` — the same reserve-before-you-start check
    listen_and_answer.py already performs, exposed generically so any
    subsystem can gate itself against the one shared ledger."""
    if ledger.get("spent_usd", 0.0) + reserve > limit:
        raise BudgetExceeded("Monthly API budget reached.")


def premium_voice_status(ledger: dict[str, Any]) -> dict[str, Any]:
    """Warn/cutoff/fallback state for the separate premium-voice
    sub-budget. Reports caps only — it does not authorize any spending or
    switch providers itself; a caller uses `should_fallback_to_local` to
    decide whether to speak through the local voice instead."""
    warn_usd = robot_config.get_float(
        "PREMIUM_VOICE_WARN_USD", PREMIUM_VOICE_WARN_USD
    )
    cutoff_usd = robot_config.get_float(
        "PREMIUM_VOICE_CUTOFF_USD", PREMIUM_VOICE_CUTOFF_USD
    )
    spent = ledger.get("premium_voice_spent_usd", 0.0)

    return {
        "spent_usd": round(spent, 6),
        "warn_usd": warn_usd,
        "cutoff_usd": cutoff_usd,
        "should_warn": spent >= warn_usd,
        "should_fallback_to_local": spent >= cutoff_usd,
    }


def budget_summary(ledger: dict[str, Any] | None = None) -> dict[str, Any]:
    """HUD-facing summary: monthly spend, limit, remaining, per-purpose
    breakdown, and premium-voice sub-budget state."""
    ledger = ledger if ledger is not None else load_ledger()
    limit = robot_config.get_float(
        "MONTHLY_BUDGET_LIMIT_USD", MONTHLY_LIMIT_USD
    )
    spent = ledger.get("spent_usd", 0.0)

    return {
        "month": ledger.get("month", current_month()),
        "spent_usd": round(spent, 6),
        "limit_usd": limit,
        "remaining_usd": round(max(0.0, limit - spent), 6),
        "requests": ledger.get("requests", 0),
        "by_purpose": ledger.get("by_purpose", {}),
        "premium_voice": premium_voice_status(ledger),
        "fallback_active": (
            spent + NEXT_REQUEST_RESERVE_USD > limit
        ),
    }
