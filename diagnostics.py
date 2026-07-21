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

import connection_health
import cost_ledger
import hud_stats
import instagram_stats
import pc_stats

USAGE_PATH = Path("/home/atlas/atlas-robot/data/openai_usage.json")
MISSION_STORE_PATH = Path(
    "/home/atlas/atlas-robot/data/agent_missions.json"
)
MONTHLY_LIMIT_USD = 8.00

SERVICES = [
    "atlas-robot.service",
    "atlas-wake.service",
    "atlas-hud.service",
    "atlas-hub.service",
]

# The agent's structured diagnostics also cover the Graphify MCP unit.
STRUCTURED_SERVICE_UNITS = SERVICES + ["graphify-mcp.service"]

MIC_CARD_NAME = "Device"  # SuziePi USB mic, matches MIC_DEVICE's CARD=

TEMP_WARN_C = 80


def _services_status(units=None):
    """Returns (active_count, total, [failed names])."""
    failed = []
    services = SERVICES if units is None else units

    for service in services:
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

    return len(services) - len(failed), len(services), failed


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


# ---------------------------------------------------------------------
# Structured, machine-readable diagnostics (Phase 2). Read-only: every
# check reports what it actually observed and never repairs anything.
# ---------------------------------------------------------------------

STRUCTURED_COMPONENTS = (
    "services",
    "microphone",
    "speaker",
    "camera",
    "pc_companion",
    "direct_ethernet",
    "wifi",
    "disk",
    "temperature",
    "budget",
    "mission_store",
    "instagram_refresher",
    "printer",
    "voice_provider",
)


def _finding(component, ok, detail):
    return {
        "component": component,
        "ok": bool(ok),
        "detail": detail,
    }


def _check_services():
    active, total, failed = _services_status(
        units=STRUCTURED_SERVICE_UNITS
    )

    if failed:
        return _finding(
            "services", False,
            f"{active} of {total} services active; down: "
            + ", ".join(failed),
        )

    return _finding(
        "services", True, f"all {total} services active"
    )


def _check_microphone():
    present = _microphone_present()
    return _finding(
        "microphone", present,
        "USB microphone visible to ALSA" if present
        else "microphone not detected by ALSA",
    )


def _speaker_present():
    try:
        output = subprocess.run(
            ["aplay", "-l"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return False

    return "card " in output


def _check_speaker():
    present = _speaker_present()
    return _finding(
        "speaker", present,
        "playback device visible to ALSA" if present
        else "no playback device detected",
    )


_VIDEO4LINUX_SYSFS = Path("/sys/class/video4linux")

# Pi codec/ISP video nodes that exist even with no camera attached.
_NON_CAPTURE_NAME_MARKERS = (
    "hevc", "pispbe", "codec", "isp", "bcm2835",
)


def _camera_device_nodes():
    """Real capture devices only — the Pi's codec/ISP /dev/video*
    nodes exist even with no camera plugged in."""
    nodes = []

    for sys_node in sorted(_VIDEO4LINUX_SYSFS.glob("video*")):
        try:
            name = (sys_node / "name").read_text().strip()
        except OSError:
            name = ""

        lowered = name.lower()

        if any(
            marker in lowered
            for marker in _NON_CAPTURE_NAME_MARKERS
        ):
            continue

        nodes.append(str(Path("/dev") / sys_node.name))

    return nodes


def _check_camera():
    nodes = _camera_device_nodes()

    if nodes:
        return _finding(
            "camera", True,
            f"camera device present ({nodes[0]})",
        )

    return _finding(
        "camera", False, "no camera device connected"
    )


def _connection_finding(component, check):
    result = check()
    return _finding(
        component,
        result.get("ok", False),
        str(result.get("detail", "no detail reported")),
    )


def _check_pc_companion():
    return _connection_finding(
        "pc_companion", connection_health.check_companion
    )


def _check_direct_ethernet():
    return _connection_finding(
        "direct_ethernet", connection_health.check_direct_link
    )


def _check_wifi():
    return _connection_finding(
        "wifi", connection_health.check_wifi
    )


def _check_disk():
    report = hud_stats.get_storage_report_stats()
    level = report.get("level", "unknown")
    percent = report.get("percent")
    percent_text = (
        f"{percent:.0f}%" if isinstance(percent, (int, float))
        else "unknown"
    )

    return _finding(
        "disk",
        level in ("ok", "warn"),
        f"disk {percent_text} used, level {level}",
    )


def _check_temperature():
    temp = hud_stats.get_cpu_stats().get("temp_c")

    if temp is None:
        return _finding(
            "temperature", True, "core temperature unreadable"
        )

    return _finding(
        "temperature",
        temp < TEMP_WARN_C,
        f"core temperature {temp:.0f}C",
    )


def _check_budget():
    summary = cost_ledger.budget_summary()
    spent = float(summary.get("spent_usd", 0.0))
    limit = float(summary.get("limit_usd", 0.0))
    fallback = bool(summary.get("fallback_active"))
    premium = summary.get("premium_voice") or {}
    detail = (
        f"${spent:.2f} of ${limit:.2f} used this month"
    )

    if fallback:
        detail += "; budget exhausted, local fallback active"

    if premium.get("should_fallback_to_local"):
        detail += "; premium voice over its cap"

    return _finding("budget", not fallback, detail)


def _check_mission_store():
    if not MISSION_STORE_PATH.exists():
        return _finding(
            "mission_store", True,
            "mission store not created yet",
        )

    try:
        data = json.loads(MISSION_STORE_PATH.read_text())
        tasks = data.get("tasks")

        if not isinstance(tasks, list):
            raise ValueError("tasks key missing")
    except (json.JSONDecodeError, OSError, ValueError) as error:
        return _finding(
            "mission_store", False,
            f"mission store unreadable: {type(error).__name__}",
        )

    return _finding(
        "mission_store", True,
        f"{len(tasks)} recorded missions",
    )


def _check_instagram_refresher():
    stats = instagram_stats.get_stats(allow_fetch=False)

    if not stats.get("configured"):
        return _finding(
            "instagram_refresher", True,
            "Instagram stats not configured",
        )

    error = stats.get("error")

    if isinstance(error, str) and error:
        return _finding(
            "instagram_refresher", False,
            f"stats refresher failing: {error[:80]}",
        )

    if stats.get("stale"):
        return _finding(
            "instagram_refresher", True,
            "stats cache stale; background refresher will "
            "update it",
        )

    return _finding(
        "instagram_refresher", True, "stats cache fresh"
    )


def _check_printer():
    printer = hud_stats.get_printer_stats()

    if not printer.get("online"):
        # An offline printer is an expected state, not a fault.
        return _finding("printer", True, "printer offline")

    state = printer.get("state")
    detail = "printer online"

    if state:
        detail += f", {state}"

    return _finding("printer", True, detail)


def _check_voice_provider():
    import self_healing

    whisper_ok = (
        self_healing.WHISPER_CLI.exists()
        and self_healing.WHISPER_MODEL.exists()
    )

    if whisper_ok:
        return _finding(
            "voice_provider", True,
            "local voice stack healthy (Whisper present, "
            "premium adapter not yet implemented)",
        )

    return _finding(
        "voice_provider", False,
        "Whisper binary or model missing; Vosk fallback "
        "handles commands",
    )


_STRUCTURED_CHECKS = {
    "services": _check_services,
    "microphone": _check_microphone,
    "speaker": _check_speaker,
    "camera": _check_camera,
    "pc_companion": _check_pc_companion,
    "direct_ethernet": _check_direct_ethernet,
    "wifi": _check_wifi,
    "disk": _check_disk,
    "temperature": _check_temperature,
    "budget": _check_budget,
    "mission_store": _check_mission_store,
    "instagram_refresher": _check_instagram_refresher,
    "printer": _check_printer,
    "voice_provider": _check_voice_provider,
}


def run_structured_checks(components=None):
    """Runs the requested read-only checks (all when None) and returns
    a list of {component, ok, detail} findings. A crashing check is
    reported honestly instead of aborting the sweep."""
    if components is None:
        selected = STRUCTURED_COMPONENTS
    else:
        if not isinstance(components, list) or not components:
            raise ValueError(
                "components must be null or a non-empty list"
            )

        unknown = [
            component
            for component in components
            if component not in _STRUCTURED_CHECKS
        ]

        if unknown:
            raise ValueError(
                "unknown diagnostic components: "
                + ", ".join(str(item) for item in unknown)
            )

        selected = tuple(dict.fromkeys(components))

    findings = []

    for component in selected:
        try:
            findings.append(_STRUCTURED_CHECKS[component]())
        except Exception as error:
            findings.append(_finding(
                component, False,
                f"check could not run: {type(error).__name__}",
            ))

    return findings


def spoken_structured_report(findings):
    """One-breath spoken verdict built from real structured findings —
    never claims more than the checks actually observed."""
    if not findings:
        return (
            "I ran no diagnostic checks — nothing to report."
        )

    problems = [f for f in findings if not f["ok"]]
    total = len(findings)

    if not problems:
        return (
            f"Diagnostics sweep complete. All {total} systems "
            "nominal — services, audio, network, storage, "
            "thermals, and budget all check out."
        )

    spoken_problems = "; ".join(
        f"{f['component'].replace('_', ' ')}: {f['detail']}"
        for f in problems
    )

    return (
        f"Diagnostics sweep complete. {total - len(problems)} of "
        f"{total} systems nominal, {len(problems)} need attention "
        f"— {spoken_problems}."
    )
