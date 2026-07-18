"""Wake-on-LAN control for the gaming PC.

The magic packet needs the PC's MAC address, but the user only configures
its IP (GAMING_PC_IP in config/robot.env, shared with pc_stats). The MAC
is learned automatically from the kernel's neighbor (ARP) table whenever
the PC is reachable and cached to disk, so "power on my PC" still works
later when the machine is off and absent from the ARP table.
"""
import ipaddress
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


def _directed_broadcast_for_pc():
    """Finds the broadcast address on the interface that routes to the PC."""
    target_ip = pc_stats.load_gaming_pc_ip()

    if not target_ip:
        return None

    try:
        target = ipaddress.ip_address(target_ip)
        route_output = subprocess.run(
            ["ip", "-4", "route", "get", target_ip],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        route_parts = route_output.split()

        if "dev" not in route_parts:
            return None

        interface = route_parts[route_parts.index("dev") + 1]
        address_output = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", interface],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (ValueError, subprocess.SubprocessError, OSError, IndexError):
        return None

    for line in address_output.splitlines():
        parts = line.split()

        if "inet" not in parts:
            continue

        try:
            network = ipaddress.ip_interface(
                parts[parts.index("inet") + 1]
            ).network
        except (ValueError, IndexError):
            continue

        if target in network:
            return str(network.broadcast_address)

    return None


def _broadcast_addresses():
    """Uses the PC's routed network first, then global broadcast."""
    directed_broadcast = _directed_broadcast_for_pc()
    return (
        [directed_broadcast, "255.255.255.255"]
        if directed_broadcast
        else ["255.255.255.255"]
    )


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


def _pc_is_online():
    """Checks either the hardware monitor or authenticated PC companion."""
    if pc_stats.get_gaming_pc_stats().get("online"):
        return True

    try:
        import pc_control
        return pc_control.pc_reachable()
    except Exception as error:
        print("PC reachability check failed:", error, flush=True)
        return False



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

    if _pc_is_online():
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


# MAC OUI prefixes known to belong to USB network-adapter makers. A USB
# NIC generally can't do Wake-on-LAN because it loses bus power when the
# PC sleeps or shuts down — so a WoL target on one of these is the most
# common "boot my PC does nothing" cause, and it's a hardware fact no
# software change on the Pi can fix.
USB_ADAPTER_OUIS = {
    "80:3f:5d": "Winstars Technology",
    "00:e0:4c": "Realtek USB",
    "00:13:3b": "USB NIC",
    "00:05:1b": "USB NIC",
}


def diagnose_wol():
    """Read-only Wake-on-LAN diagnosis from the Pi's side. Explains what
    can and can't be fixed in software vs. what needs PC-side work.
    Returns (spoken_report, evidence_dict)."""
    import network_sentinel

    mac = get_pc_mac()
    evidence = {"mac": mac}

    # Which interface would the packet leave from, and is it wired?
    try:
        route = subprocess.run(
            ["ip", "route", "get", pc_stats.load_gaming_pc_ip() or "192.168.0.1"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        iface = route.split("dev", 1)[1].split()[0] if "dev" in route else "?"
    except (subprocess.SubprocessError, OSError, IndexError):
        iface = "?"

    evidence["egress_iface"] = iface
    evidence["egress_is_wifi"] = iface.startswith(("wlan", "wl"))

    parts = []

    if mac is None:
        return (
            "I don't have your PC's hardware address yet, so I can't wake "
            "it. I learn it automatically while the PC is on.",
            evidence,
        )

    oui = mac[:8].lower()
    vendor = network_sentinel._vendor_for_mac(mac) or ""
    evidence["vendor"] = vendor
    is_usb_adapter = oui in USB_ADAPTER_OUIS or "winstars" in vendor.lower()
    evidence["target_is_usb_adapter"] = is_usb_adapter

    parts.append("From my side, the magic packet sends fine")

    if evidence["egress_is_wifi"]:
        parts.append(
            "though I'm on Wi-Fi, so the packet reaches your PC through "
            "your router rather than a direct cable — that's usually okay "
            "but less reliable than wired"
        )

    if is_usb_adapter:
        parts.append(
            f"but here's the real problem: the network address I wake is a "
            f"{vendor or 'USB'} adapter, which is a USB network dongle. USB "
            "adapters almost never support Wake-on-LAN because they lose "
            "power when the PC sleeps or shuts off. No change on my end can "
            "fix that — you'd need to wake the PC through its built-in "
            "Ethernet port instead, with a cable, and enable Wake-on-LAN in "
            "the BIOS plus turn off Windows fast startup"
        )
    else:
        parts.append(
            "so if it's not waking, the cause is on the PC: enable "
            "Wake-on-LAN in the BIOS, turn off Windows fast startup, and "
            "allow the network card to wake the machine in its power "
            "settings"
        )

    return ". ".join(parts) + ".", evidence


WAKE_VERIFY_TIMEOUT_SECONDS = 90
WAKE_VERIFY_POLL_SECONDS = 5


def verify_wake(speak, log):
    """Polls until the PC reports online or the timeout passes, then
    speaks the honest outcome — 'signal sent' alone never confirmed the
    packet actually landed. Run in a background thread by the hub."""
    deadline = time.monotonic() + WAKE_VERIFY_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        time.sleep(WAKE_VERIFY_POLL_SECONDS)

        if _pc_is_online():
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
