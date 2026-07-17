"""LAN device watcher: announces when an unknown device joins the network.

Discovery is a parallel ping sweep of the Pi's own /24 followed by a read
of the kernel neighbor table (the pings populate it). The very first scan
establishes a silent baseline of every device already present; only MACs
never seen before after that get announced. Known devices persist across
restarts in data/known_devices.json.

Runs inside robot_hub (the process that owns speech), with speak/log/
should_stay_quiet callbacks injected so this module has no Flask or audio
dependencies.
"""
import ipaddress
import json
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import hud_stats

KNOWN_DEVICES_PATH = Path("/home/atlas/atlas-robot/data/known_devices.json")
OUI_DATABASE_PATH = Path("/usr/share/ieee-data/oui.txt")
SCAN_INTERVAL_SECONDS = 5 * 60
PING_WORKERS = 40
MAC_PATTERN = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")

_lock = threading.Lock()
_online_device_count = 0
_online_devices = []  # [{mac, ip, hostname, vendor}] from the latest scan

_oui_vendors = None


def get_online_device_count():
    with _lock:
        return _online_device_count


def get_online_devices():
    """Latest scan's devices, each {mac, ip, hostname, vendor}."""
    with _lock:
        return list(_online_devices)


def _load_oui_database():
    """Parses the IEEE OUI registry (ieee-data package) into a
    {prefix: vendor} dict once. ~35k entries, a few MB — fine to hold."""
    global _oui_vendors

    if _oui_vendors is not None:
        return _oui_vendors

    vendors = {}

    try:
        with open(OUI_DATABASE_PATH, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "(hex)" not in line:
                    continue

                prefix, _, vendor = line.partition("(hex)")
                prefix = prefix.strip().replace("-", ":").lower()

                if len(prefix) == 8:
                    vendors[prefix] = vendor.strip()
    except OSError:
        pass

    _oui_vendors = vendors
    return vendors


def _vendor_for_mac(mac):
    return _load_oui_database().get(mac[:8].lower())


def _hostname_for_ip(ip):
    """Best-effort name: reverse DNS first, then mDNS via avahi. Lots of
    LAN devices answer neither — that's fine, vendor fills the gap."""
    try:
        name = socket.gethostbyaddr(ip)[0]

        if name and name != ip:
            return name.removesuffix(".local").removesuffix(".lan")
    except OSError:
        pass

    try:
        output = subprocess.run(
            ["avahi-resolve-address", ip],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.split()

        if len(output) >= 2:
            return output[1].removesuffix(".local")
    except (subprocess.SubprocessError, OSError):
        pass

    return None


def _load_known_devices():
    if not KNOWN_DEVICES_PATH.exists():
        return {}

    try:
        data = json.loads(KNOWN_DEVICES_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

    return data if isinstance(data, dict) else {}


def _save_known_devices(devices):
    KNOWN_DEVICES_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = KNOWN_DEVICES_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(devices, indent=2))
    temporary_path.replace(KNOWN_DEVICES_PATH)


def _ping(ip):
    try:
        subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (subprocess.SubprocessError, OSError):
        pass


def _read_neighbors():
    """Returns {mac: ip} for every reachable neighbor after a sweep."""
    try:
        output = subprocess.run(
            ["ip", "neigh"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {}

    neighbors = {}

    for line in output.splitlines():
        parts = line.split()

        if "lladdr" not in parts or "FAILED" in parts:
            continue

        ip = parts[0]
        mac = parts[parts.index("lladdr") + 1].lower()

        if MAC_PATTERN.match(mac):
            neighbors[mac] = ip

    return neighbors


def scan_devices():
    """Ping-sweeps this Pi's /24 and returns {mac: ip} of live devices."""
    own_ip = hud_stats.get_network_stats().get("ip")

    if not own_ip:
        return {}

    network = ipaddress.ip_network(f"{own_ip}/24", strict=False)
    hosts = [str(host) for host in network.hosts()]

    with ThreadPoolExecutor(max_workers=PING_WORKERS) as pool:
        list(pool.map(_ping, hosts))

    return _read_neighbors()


def sentinel_loop(speak, log, should_stay_quiet):
    """Scans forever. Announces devices never seen before (except on the
    baseline-establishing first scan, and never while should_stay_quiet()
    — quiet hours / focus mode — returns True; those joins are still
    recorded, just not narrated)."""
    global _online_device_count, _online_devices

    known_devices = _load_known_devices()
    is_baseline_scan = not known_devices

    while True:
        try:
            online = scan_devices()

            now = time.time()
            new_macs = [mac for mac in online if mac not in known_devices]

            for mac in new_macs:
                known_devices[mac] = {
                    "ip": online[mac],
                    "first_seen": now,
                }

            for mac, ip in online.items():
                known_devices[mac]["ip"] = ip
                known_devices[mac]["last_seen"] = now

                # Enrich once and remember — hostname/vendor lookups are
                # slow-ish and device identity doesn't churn.
                if "vendor" not in known_devices[mac]:
                    known_devices[mac]["vendor"] = _vendor_for_mac(mac)

                if not known_devices[mac].get("hostname"):
                    known_devices[mac]["hostname"] = _hostname_for_ip(ip)

            device_list = [
                {
                    "mac": mac,
                    "ip": ip,
                    "hostname": known_devices[mac].get("hostname"),
                    "vendor": known_devices[mac].get("vendor"),
                }
                for mac, ip in sorted(
                    online.items(), key=lambda item: ipaddress.ip_address(item[1])
                )
            ]

            with _lock:
                _online_device_count = len(online)
                _online_devices = device_list

            _save_known_devices(known_devices)

            if new_macs and not is_baseline_scan and not should_stay_quiet():
                count = len(new_macs)
                suffix = online[new_macs[0]] if count == 1 else f"{count} of them"
                message = (
                    f"Heads up — a new device just joined the network, "
                    f"at {suffix}."
                    if count == 1
                    else f"Heads up — {count} new devices just joined the network."
                )
                log(message)
                speak(message)

            is_baseline_scan = False
        except Exception as error:
            print("Network sentinel error:", type(error).__name__, error, flush=True)

        time.sleep(SCAN_INTERVAL_SECONDS)
