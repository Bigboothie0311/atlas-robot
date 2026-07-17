"""Spoken self-diagnostics: "Atlas, run diagnostics".

Checks everything that can actually break and reports a one-breath
verdict — all local and zero-token. Reads the OpenAI usage file directly
rather than importing listen_and_answer (which imports this module's
callers) to stay dependency-light.
"""
import json
import socket
import subprocess
from pathlib import Path

import hud_stats
import pc_stats

USAGE_PATH = Path("/home/atlas/atlas-robot/data/openai_usage.json")
MONTHLY_LIMIT_USD = 8.00

SERVICES = [
    "atlas-robot.service",
    "atlas-wake.service",
    "atlas-hud.service",
    "atlas-hub.service",
]

MIC_CARD_NAME = "Device"  # SuziePi USB mic, matches MIC_DEVICE's CARD=


def _services_status():
    """Returns (active_count, total, [failed names])."""
    failed = []

    for service in SERVICES:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", service],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.stdout.strip() != "active":
                failed.append(service.replace(".service", ""))
        except (subprocess.SubprocessError, OSError):
            failed.append(service.replace(".service", ""))

    return len(SERVICES) - len(failed), len(SERVICES), failed


def _internet_reachable():
    try:
        with socket.create_connection(("8.8.8.8", 53), timeout=3):
            return True
    except OSError:
        return False


def _microphone_present():
    try:
        output = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return False

    return MIC_CARD_NAME in output


def _budget_spent_usd():
    if not USAGE_PATH.exists():
        return 0.0

    try:
        return float(json.loads(USAGE_PATH.read_text()).get("spent_usd", 0.0))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def build_diagnostics_report():
    """Runs every check and returns the spoken report."""
    parts = []
    problems = []

    active, total, failed = _services_status()

    if failed:
        problems.append(
            f"{len(failed)} of {total} services down: {', '.join(failed)}"
        )
    else:
        parts.append(f"All {total} services running.")

    cpu = hud_stats.get_cpu_stats()
    memory = hud_stats.get_memory_stats()
    disk = hud_stats.get_disk_stats()

    health_line = f"Disk at {disk['percent']:.0f} percent, memory {memory['percent']:.0f}"

    if cpu.get("temp_c") is not None:
        health_line += f", my core temperature is {cpu['temp_c']:.0f} degrees"

    parts.append(health_line + ".")

    if disk["percent"] >= 90:
        problems.append("disk is nearly full")

    if cpu.get("temp_c") is not None and cpu["temp_c"] >= 80:
        problems.append("I'm running hot")

    if _internet_reachable():
        parts.append("Internet is reachable.")
    else:
        problems.append("no internet connection")

    if not _microphone_present():
        problems.append("I can't see my microphone")

    if pc_stats.get_gaming_pc_stats().get("online"):
        parts.append("Your gaming PC is online.")

    printer = hud_stats.get_printer_stats()

    if printer.get("online"):
        state = printer.get("state")
        progress = printer.get("progress_percent")

        if state and progress is not None:
            parts.append(f"The printer is online, {state} at {progress} percent.")
        else:
            parts.append("The printer is online.")

    spent = _budget_spent_usd()

    if spent is not None:
        cents = round(spent * 100)

        if cents < 100:
            parts.append(
                f"I've used {cents} cents of my "
                f"{MONTHLY_LIMIT_USD:.0f} dollar budget this month."
            )
        else:
            parts.append(
                f"I've used {spent:.2f} dollars of my "
                f"{MONTHLY_LIMIT_USD:.0f} dollar budget this month."
            )

    if problems:
        verdict = "Diagnostics complete — issues found: " + "; ".join(problems) + "."
        return verdict + " Otherwise: " + " ".join(parts)

    return "Diagnostics complete, all systems nominal. " + " ".join(parts)
