"""'Atlas, secure my network' — locally affordable network defense.

All local and read-only — this NEVER blocks a device or changes the
router. It maintains a trusted-device database, flags unknown devices,
audits which local services are exposed, and reads repeated failed SSH
attempts from the journal. It explains evidence and proposes isolation;
acting on that proposal is left to the human (router isolation would
require explicit, ideally physical, confirmation).
"""
import json
import re
import subprocess
from pathlib import Path

import network_sentinel

DATA_DIR = Path("/home/atlas/atlas-robot/data")
TRUSTED_PATH = DATA_DIR / "trusted_devices.json"


def _load_trusted():
    if not TRUSTED_PATH.exists():
        return {}

    try:
        data = json.loads(TRUSTED_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_trusted(trusted):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = TRUSTED_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(trusted, indent=2))
    temporary_path.replace(TRUSTED_PATH)


def trust_device(mac, label=None):
    trusted = _load_trusted()
    trusted[mac.lower()] = {"label": label or "trusted"}
    _save_trusted(trusted)


def unknown_devices():
    """Devices currently on the LAN that aren't in the trusted list."""
    trusted = _load_trusted()
    return [
        d for d in network_sentinel.get_online_devices()
        if d["mac"].lower() not in trusted
    ]


def exposed_services():
    """Local listening services bound to non-loopback addresses — the ones
    actually reachable from the LAN. Read from `ss`, no scanning."""
    try:
        output = subprocess.run(
            ["ss", "-tlnH"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []

    exposed = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue

        local = parts[3]

        # Skip loopback-only binds (127.0.0.1, [::1]) — not LAN-reachable.
        if local.startswith("127.") or local.startswith("[::1]"):
            continue

        port = local.rsplit(":", 1)[-1]
        exposed.append({"address": local, "port": port})

    return exposed


def failed_ssh_attempts(window_hours=24):
    """Counts repeated failed SSH logins from the journal — the classic
    'someone's knocking' signal. Returns {ip: count} for repeat offenders."""
    try:
        output = subprocess.run(
            ["journalctl", "-u", "ssh", "--since", f"-{window_hours}h",
             "--no-pager"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {}

    counts = {}
    for match in re.finditer(r"Failed password for .* from ([\d.]+)", output):
        ip = match.group(1)
        counts[ip] = counts.get(ip, 0) + 1

    # Only repeat offenders are interesting.
    return {ip: n for ip, n in counts.items() if n >= 3}


def audit():
    """Full read-only security audit. Returns structured evidence."""
    return {
        "unknown_devices": unknown_devices(),
        "exposed_services": exposed_services(),
        "failed_ssh": failed_ssh_attempts(),
    }


def spoken_report(result):
    parts = []

    unknown = result["unknown_devices"]
    if unknown:
        names = [d.get("hostname") or d.get("vendor") or d["ip"] for d in unknown]
        parts.append(
            f"{len(unknown)} device{'s' if len(unknown) != 1 else ''} on the "
            f"network I don't recognize: {', '.join(names[:5])}"
        )
    else:
        parts.append("every device on the network is one I recognize")

    exposed = result["exposed_services"]
    if exposed:
        ports = sorted({e["port"] for e in exposed})
        parts.append(
            f"{len(exposed)} local service{'s' if len(exposed) != 1 else ''} "
            f"are reachable from the LAN, on ports {', '.join(ports)}"
        )

    ssh = result["failed_ssh"]
    if ssh:
        worst = max(ssh, key=ssh.get)
        parts.append(
            f"and I'm seeing repeated failed SSH logins, worst from {worst} "
            f"with {ssh[worst]} attempts"
        )

    report = "Network audit: " + ". ".join(parts) + "."

    if unknown or ssh:
        report += (
            " I can walk you through isolating anything suspicious, but I "
            "won't block a device or touch the router without your say-so."
        )

    return report
