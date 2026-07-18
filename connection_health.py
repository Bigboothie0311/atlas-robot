"""Deterministic connection-health checks — local, zero-token.

Covers the four links ATLAS depends on:
  - Pi Wi-Fi / internet (wlan0 + reachability)
  - the direct Pi<->PC Ethernet link (eth0, 192.168.50.x)
  - the Windows Companion (its /health endpoint)
  - Tailscale (for the phone link)

Key rule: NEVER declare the PC offline until BOTH the companion endpoint
and the direct-link ping have failed — a companion hiccup alone is not an
offline PC.
"""
import subprocess

import pc_control
import robot_config

DIRECT_LINK_PC_IP = "192.168.50.2"   # PC's address on the direct cable
DIRECT_LINK_IFACE = "eth0"


def _ping(host, iface=None, count=2, timeout=2):
    cmd = ["ping", "-c", str(count), "-W", str(timeout)]
    if iface:
        cmd += ["-I", iface]
    cmd.append(host)
    try:
        return subprocess.run(cmd, capture_output=True, timeout=count * timeout + 3).returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _iface_up(iface):
    try:
        state = subprocess.run(["cat", f"/sys/class/net/{iface}/operstate"],
                               capture_output=True, text=True, timeout=3).stdout.strip()
        return state == "up"
    except (subprocess.SubprocessError, OSError):
        return False


def check_wifi():
    up = _iface_up("wlan0")
    internet = _ping("8.8.8.8") if up else False
    return {
        "name": "Wi-Fi & internet",
        "ok": up and internet,
        "detail": "connected" if internet else ("Wi-Fi up but no internet" if up else "Wi-Fi down"),
        "recovery": None if internet else "Check the router; I can restart my Wi-Fi if you say get the system healthy.",
    }


def check_direct_link():
    up = _iface_up(DIRECT_LINK_IFACE)
    reachable = _ping(DIRECT_LINK_PC_IP, iface=DIRECT_LINK_IFACE) if up else False
    return {
        "name": "direct PC link",
        "ok": up and reachable,
        "detail": "PC reachable over the cable" if reachable else (
            "cable link up but PC not answering" if up else "Ethernet port is down"),
        "recovery": None if reachable else "Check the Ethernet cable between me and the PC, and that the PC is on.",
    }


def check_companion():
    configured = pc_control.is_configured()
    reachable = pc_control.pc_reachable() if configured else False
    return {
        "name": "PC companion",
        "ok": reachable,
        "detail": "companion responding" if reachable else (
            "companion not responding" if configured else "companion not set up"),
        "recovery": None if reachable else (
            "Start the companion on the PC (or check its firewall rule)." if configured
            else "Set PC_COMPANION_URL and PC_COMPANION_TOKEN in my config."),
    }


def check_tailscale():
    try:
        result = subprocess.run(["tailscale", "status", "--json"],
                                capture_output=True, text=True, timeout=6)
        up = '"BackendState":"Running"' in result.stdout or '"Online":true' in result.stdout
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        up = False
    return {
        "name": "Tailscale",
        "ok": up,
        "detail": "connected" if up else "not connected",
        "recovery": None if up else "Run tailscale up on the Pi to restore phone access.",
    }


def pc_is_truly_offline():
    """The PC counts as offline ONLY if the companion AND the direct link
    both fail — a single failure is inconclusive."""
    companion = check_companion()
    direct = check_direct_link()
    return not companion["ok"] and not direct["ok"]


def run_all():
    return [check_wifi(), check_direct_link(), check_companion(), check_tailscale()]


def spoken_report():
    checks = run_all()
    ok = [c for c in checks if c["ok"]]
    bad = [c for c in checks if not c["ok"]]

    if not bad:
        return "Everything's connected — Wi-Fi, the direct PC link, the companion, and Tailscale are all good."

    parts = [f"{len(ok)} of {len(checks)} connections are healthy"]
    for c in bad:
        line = f"{c['name']} is a problem: {c['detail']}"
        if c["recovery"]:
            line += f". {c['recovery']}"
        parts.append(line)

    # Honest PC-offline wording per the rule.
    if pc_is_truly_offline():
        parts.append("Both the companion and the direct link are down, so the PC really is unreachable.")

    return ". ".join(parts) + "."
