"""'Atlas, get the whole system healthy' — Pi-side.

Diagnoses the Pi, local services, printer hub, network, audio, camera,
and storage; runs ONLY safe repairs (via recovery.py's playbooks, which
have their own cooldowns); verifies; and returns a spoken summary plus a
persisted incident trail. All local, zero tokens.

Config/data backup snapshots and an apt update check are included where
safe (read-only for updates — it reports what's available, it does not
auto-install).
"""
import json
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import hud_stats
import logbook
import recovery

DATA_DIR = Path("/home/atlas/atlas-robot/data")
BACKUP_DIR = DATA_DIR / "backups"
CONFIG_DIR = Path("/home/atlas/atlas-robot/config")

SERVICES = {
    "atlas-robot.service": "hub",
    "atlas-wake.service": "wake",
    "atlas-hud.service": "hud",
    "atlas-hub.service": "printer_hub",
}

DISK_WARN_PERCENT = 90
TEMP_WARN_C = 80


def _service_active(unit):
    try:
        return subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() == "active"
    except (subprocess.SubprocessError, OSError):
        return False


def diagnose():
    """Read-only health scan. Returns a list of {component, ok, detail}."""
    findings = []

    for unit, component in SERVICES.items():
        active = _service_active(unit)
        findings.append({
            "component": component,
            "ok": active,
            "detail": f"{unit} {'active' if active else 'DOWN'}",
        })

    cpu = hud_stats.get_cpu_stats()
    temp = cpu.get("temp_c")
    findings.append({
        "component": "cpu_temp",
        "ok": temp is None or temp < TEMP_WARN_C,
        "detail": f"core {temp}C" if temp is not None else "temp unreadable",
    })

    disk = hud_stats.get_disk_stats()
    findings.append({
        "component": "disk",
        "ok": disk["percent"] < DISK_WARN_PERCENT,
        "detail": f"disk {disk['percent']:.0f}% used",
    })

    net = hud_stats.get_network_stats()
    findings.append({
        "component": "network",
        "ok": net.get("ip") is not None,
        "detail": "network up" if net.get("ip") else "no network address",
    })

    printer = hud_stats.get_printer_stats()
    findings.append({
        "component": "printer",
        "ok": True,  # offline printer isn't a fault
        "detail": "printer online" if printer.get("online") else "printer offline",
    })

    return findings


def create_backup():
    """Snapshots config + key data (facts, notes, reminders, logs) to a
    timestamped tarball. Rotates to the last 5. Returns the path or None."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    archive = BACKUP_DIR / f"snapshot-{stamp}.tar.gz"

    include = [
        CONFIG_DIR / "robot.env",
        DATA_DIR / "memory_facts.json",
        DATA_DIR / "notes.json",
        DATA_DIR / "reminders.json",
        DATA_DIR / "known_devices.json",
    ]

    try:
        with tarfile.open(archive, "w:gz") as tar:
            for path in include:
                if path.exists():
                    tar.add(path, arcname=path.name)
    except OSError as error:
        print("Backup failed:", error, flush=True)
        return None

    snapshots = sorted(BACKUP_DIR.glob("snapshot-*.tar.gz"))
    for old in snapshots[:-5]:
        try:
            old.unlink()
        except OSError:
            pass

    return archive


def check_updates():
    """Read-only apt update check — reports upgradable package count, does
    NOT install anything."""
    try:
        subprocess.run(["sudo", "-n", "apt-get", "update"],
                       capture_output=True, timeout=60)
        result = subprocess.run(
            ["apt-get", "-s", "upgrade"],
            capture_output=True, text=True, timeout=30,
        )
        upgradable = sum(
            1 for line in result.stdout.splitlines() if line.startswith("Inst ")
        )
        return upgradable
    except (subprocess.SubprocessError, OSError):
        return None


def run_full_sweep():
    """Diagnose → repair only what's broken → verify → report. Returns a
    dict with findings, repairs, backup, and a spoken summary."""
    findings = diagnose()
    broken = [f for f in findings if not f["ok"]]

    repairs = []
    repair_map = {
        "hub": "network_sentinel",
        "wake": None,  # wake has no safe auto-repair beyond service restart
        "hud": "hud",
        "printer_hub": "printer_hub",
        "network": "model_api",
    }

    for finding in broken:
        component = finding["component"]

        # Map a down service to its recovery playbook.
        playbook_key = repair_map.get(component)

        if component in ("hub", "wake", "hud", "printer_hub"):
            unit = next((u for u, c in SERVICES.items() if c == component), None)
            if unit:
                incident = recovery._restart_and_verify(
                    component, unit, component
                )
                repairs.append(incident)
        elif playbook_key:
            repairs.append(recovery.run_playbook(playbook_key))

    backup = create_backup()
    upgradable = check_updates()

    logbook.record_incident(
        "system_health", "full sweep requested",
        f"{len(repairs)} repairs attempted",
        f"{sum(1 for r in repairs if r['resolved'])} resolved",
        all(r["resolved"] for r in repairs) if repairs else True,
    )

    return {
        "findings": findings,
        "broken": broken,
        "repairs": repairs,
        "backup": str(backup) if backup else None,
        "upgradable": upgradable,
    }


def spoken_summary(result):
    findings = result["findings"]
    broken = result["broken"]
    repairs = result["repairs"]

    healthy = len(findings) - len(broken)
    parts = [f"System health sweep done. {healthy} of {len(findings)} checks passed"]

    if not broken:
        parts.append("everything's healthy")
    else:
        resolved = [r for r in repairs if r["resolved"]]
        parts.append(f"{len(broken)} issues found, {len(resolved)} repaired")

        still_broken = [r for r in repairs if not r["resolved"]]
        if still_broken:
            names = ", ".join(r["component"] for r in still_broken)
            parts.append(f"still needing attention: {names}")

    if result["backup"]:
        parts.append("I saved a config and data backup")

    if result["upgradable"]:
        parts.append(f"{result['upgradable']} system updates are available")

    return ". ".join(parts) + "."
