"""Wake-on-LAN control for the gaming PC.

The magic packet needs the PC's MAC address, but the user only configures
its IP (GAMING_PC_IP in config/robot.env, shared with pc_stats). The MAC
is learned automatically from the kernel's neighbor (ARP) table whenever
the PC is reachable and cached to disk, so "power on my PC" still works
later when the machine is off and absent from the ARP table.
"""
import json
import re
import socket
import subprocess
import time
from pathlib import Path

import pc_stats

MAC_CACHE_PATH = Path("/home/atlas/atlas-robot/data/pc_mac.json")
WOL_PORT = 9
MAC_PATTERN = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


def _lookup_mac_from_neighbors(ip):
    """Returns the MAC for ip from the kernel neighbor table, or None."""
    try:
        output = subprocess.run(
            ["ip", "neigh", "show", ip],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None

    for line in output.splitlines():
        parts = line.split()

        if "lladdr" in parts:
            mac = parts[parts.index("lladdr") + 1].lower()

            if MAC_PATTERN.match(mac):
                return mac

    return None


def _load_cached_mac():
    if not MAC_CACHE_PATH.exists():
        return None

    try:
        data = json.loads(MAC_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    mac = str(data.get("mac", "")).lower()
    return mac if MAC_PATTERN.match(mac) else None


def _save_cached_mac(mac):
    MAC_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = MAC_CACHE_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps({"mac": mac, "learned_at": time.time()}))
    temporary_path.replace(MAC_CACHE_PATH)


def refresh_mac_cache():
    """Learns and caches the PC's MAC if it's currently in the neighbor
    table. Safe to call opportunistically — a miss changes nothing."""
    ip = pc_stats.load_gaming_pc_ip()

    if not ip:
        return None

    mac = _lookup_mac_from_neighbors(ip)

    if mac:
        _save_cached_mac(mac)

    return mac


def get_pc_mac():
    """Returns the PC's MAC — live from the neighbor table when possible
    (also refreshing the cache), else from the on-disk cache."""
    return refresh_mac_cache() or _load_cached_mac()


def send_wake_packet():
    """Sends a WoL magic packet to the gaming PC. Returns a spoken-ready
    status message."""
    mac = get_pc_mac()

    if mac is None:
        return (
            "I don't know your PC's hardware address yet. "
            "I learn it automatically while the PC is on, so once it's "
            "been online with me running, I'll be able to wake it."
        )

    if pc_stats.get_gaming_pc_stats().get("online"):
        return "Your PC already looks like it's on."

    payload = bytes.fromhex("ff" * 6 + mac.replace(":", "") * 16)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(payload, ("255.255.255.255", WOL_PORT))
    except OSError as error:
        print("WoL send failed:", error, flush=True)
        return "I couldn't send the wake signal."

    return "Wake signal sent. Your PC should be booting now."
