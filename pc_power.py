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

ROBOT_ENV_PATH = Path("/home/atlas/atlas-robot/config/robot.env")
MAC_CACHE_PATH = Path("/home/atlas/atlas-robot/data/pc_mac.json")
# Magic packets are conventionally sent to UDP 9 (discard) or 7 (echo);
# some NIC firmwares only listen on one, so send to both.
WOL_PORTS = (9, 7)
WOL_SEND_REPEATS = 3
MAC_PATTERN = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


def _load_configured_mac():
    """Optional WOL_MAC in config/robot.env overrides the learned MAC —
    for when the PC's wake-capable NIC differs from the one LibreHardware
    Monitor answers on, or the ARP-learned one is a USB dongle."""
    if not ROBOT_ENV_PATH.exists():
        return None

    for line in ROBOT_ENV_PATH.read_text().splitlines():
        line = line.strip()

        if line.startswith("WOL_MAC="):
            mac = line.split("=", 1)[1].strip().strip('"').strip("'").lower()
            mac = mac.replace("-", ":")

            if MAC_PATTERN.match(mac):
                return mac

    return None


def _broadcast_addresses():
    """The subnet-directed broadcast (e.g. 192.168.0.255) plus the global
    one. Directed broadcast is more reliable on networks where the OS or
    switch filters 255.255.255.255."""
    addresses = ["255.255.255.255"]

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            own_ip = sock.getsockname()[0]

        octets = own_ip.split(".")
        addresses.insert(0, ".".join(octets[:3] + ["255"]))
    except OSError:
        pass

    return addresses


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
    """Returns the PC's MAC — an explicit WOL_MAC config wins, then a live
    neighbor-table lookup (also refreshing the cache), then the on-disk
    cache."""
    return _load_configured_mac() or refresh_mac_cache() or _load_cached_mac()


def send_wake_packet():
    """Sends WoL magic packets to the gaming PC — both standard ports,
    directed + global broadcast, several repeats, since a single UDP
    datagram to one address is easy to lose. Returns a spoken-ready
    status message reporting exactly what happened."""
    mac = get_pc_mac()

    if mac is None:
        return (
            "I don't know your PC's hardware address yet. "
            "I learn it automatically while the PC is on, so once it's "
            "been online with me running, I'll be able to wake it. "
            "You can also set WOL_MAC in my config to skip the wait."
        )

    if pc_stats.get_gaming_pc_stats().get("online"):
        return "Your PC already looks like it's on."

    payload = bytes.fromhex("ff" * 6 + mac.replace(":", "") * 16)
    sent = 0

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            for _ in range(WOL_SEND_REPEATS):
                for address in _broadcast_addresses():
                    for port in WOL_PORTS:
                        sock.sendto(payload, (address, port))
                        sent += 1

                time.sleep(0.1)
    except OSError as error:
        print("WoL send failed:", error, flush=True)

        if sent == 0:
            return "I couldn't send the wake signal."

    print(f"WoL: {sent} magic packets sent for {mac}", flush=True)
    return "Wake signal sent. I'll tell you when your PC actually comes up."


WAKE_VERIFY_TIMEOUT_SECONDS = 90
WAKE_VERIFY_POLL_SECONDS = 5


def verify_wake(speak, log):
    """Polls until the PC reports online or the timeout passes, then
    speaks the honest outcome — 'signal sent' alone never confirmed the
    packet actually landed. Run in a background thread by the hub."""
    deadline = time.monotonic() + WAKE_VERIFY_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        time.sleep(WAKE_VERIFY_POLL_SECONDS)

        if pc_stats.get_gaming_pc_stats().get("online"):
            message = "Your PC is up."
            log(message)
            speak(message)
            return

    message = (
        "Your PC still isn't responding after the wake signal. Check that "
        "Wake-on-LAN is enabled in its BIOS and that Windows fast startup "
        "is turned off — that's the usual culprit."
    )
    log(message)
    speak(message)
