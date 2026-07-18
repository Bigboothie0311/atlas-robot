"""Controlled self-upgrade framework — 'Atlas, add X to yourself'.

This is the SAFE SCAFFOLDING only. It never launches an autonomous coding
agent on its own and never pushes to GitHub. The actual code-generation
step is intentionally a no-op stub (launch_agent) that returns
"needs approval" — running a real agent would incur API cost and must be
explicitly approved first.

What this DOES do, all locally and reversibly:
  1. snapshot()       — create a rollback point (git branch + commit sha)
  2. run regression   — capture a known-good baseline
  3. (agent step)     — gated; returns needs-approval
  4. show_diff()      — what changed vs the rollback point
  5. canary()         — restart services, re-run regression, and AUTO
                        ROLL BACK if wake/speech/HUD/printer/security or
                        any existing command regresses
  6. rollback()       — restore the snapshot and restart services

Nothing here deploys without passing regression; nothing pushes anywhere.
"""
import subprocess
import time
from pathlib import Path

import logbook
import regression

REPO = Path("/home/atlas/atlas-robot")


def _git(*args):
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, timeout=30,
    )


def snapshot(feature):
    """Creates a rollback point: records the current commit and opens a
    dedicated upgrade branch. Returns the base sha, or None on failure."""
    base = _git("rev-parse", "HEAD").stdout.strip()

    if not base:
        return None

    slug = "".join(c if c.isalnum() else "-" for c in feature.lower())[:32]
    branch = f"self-upgrade/{slug}-{int(time.time())}"
    _git("checkout", "-b", branch)

    return {"base_sha": base, "branch": branch}


def launch_agent(feature):
    """GATED. A real run would invoke an approved coding agent scoped to
    this repo — that costs API tokens, so it is NOT run automatically.
    Returns a needs-approval result the caller surfaces to the user."""
    return {
        "ran": False,
        "reason": "needs_approval",
        "message": (
            f"Ready to build '{feature}' on an isolated branch with "
            "regression-gated canary deploy and automatic rollback. "
            "Running the coding agent uses API tokens, so I need explicit "
            "approval before I start it."
        ),
    }


def show_diff(snap):
    """Diff stat of the working tree vs the rollback point."""
    result = _git("diff", "--stat", snap["base_sha"])
    return result.stdout.strip()


def _restart_services():
    for unit in ("atlas-robot.service", "atlas-wake.service", "atlas-hud.service"):
        subprocess.run(["sudo", "-n", "systemctl", "restart", unit],
                       capture_output=True, timeout=30)
    time.sleep(6)


def canary(snap):
    """Restarts services on the candidate code and re-runs regression. If
    anything load-bearing regressed, rolls back automatically. Returns a
    result dict."""
    _restart_services()
    passed, results = regression.run_all()
    failures = [r["name"] for r in results if not r["ok"]]

    if passed:
        logbook.record_incident(
            "self_upgrade", f"canary for {snap['branch']}",
            "deployed to canary", "regression passed", True,
        )
        return {"deployed": True, "rolled_back": False, "failures": []}

    # Regression failed — auto roll back.
    rollback(snap)
    logbook.record_incident(
        "self_upgrade", f"canary for {snap['branch']}",
        "auto rolled back", f"regression failed: {failures}", False,
    )
    return {"deployed": False, "rolled_back": True, "failures": failures}


def rollback(snap):
    """Restores the snapshot commit and restarts services."""
    _git("checkout", "-f", snap["base_sha"])
    _restart_services()
    return True
