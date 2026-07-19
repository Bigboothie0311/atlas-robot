"""Self-Healing Core — detect failures and run APPROVED recovery.

Watches the things that actually break: the four A.T.L.A.S. services, the
direct Pi<->PC Ethernet link, the Windows Companion, and the speech
engine (Whisper binary/model + the wake listener). On a real failure it
runs only safe, cooldown-guarded recovery steps (via recovery.py) and
reports exactly what it repaired. It never loops on a persistent failure
(recovery.py enforces per-component cooldowns) and it never touches the
companion or router from the Pi — it reports those.
"""
import subprocess
import time
from pathlib import Path

import connection_health
import logbook
import recovery

WHISPER_CLI = Path("/home/atlas/atlas-robot/tools/whisper.cpp/build/bin/whisper-cli")
WHISPER_MODEL = Path("/home/atlas/atlas-robot/tools/whisper.cpp/models/ggml-base.en.bin")

MONITOR_INTERVAL_SECONDS = 5 * 60


def _service_active(unit):
    try:
        return subprocess.run(["systemctl", "is-active", unit],
                              capture_output=True, text=True, timeout=5).stdout.strip() == "active"
    except (subprocess.SubprocessError, OSError):
        return False


def _bring_up_eth0():
    try:
        subprocess.run(["sudo", "-n", "ip", "link", "set", "eth0", "up"],
                       capture_output=True, timeout=10)
        time.sleep(2)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def check_and_heal():
    """One healing pass. Returns a list of incident dicts for whatever was
    found and acted on (empty if all healthy)."""
    incidents = []

    # 1. A.T.L.A.S. services.
    services = {
        "atlas-robot.service": "the hub",
        "atlas-wake.service": "the wake listener",
        "atlas-hud.service": "the HUD",
        "atlas-hub.service": "the printer hub",
    }
    for unit, label in services.items():
        if not _service_active(unit):
            component = unit.replace("atlas-", "").replace(".service", "")
            incidents.append(recovery._restart_and_verify(component, unit, label))

    # 2. Direct Ethernet link (only if eth0 is actually down — a PC that's
    #    simply off is not an ethernet fault).
    direct = connection_health.check_direct_link()
    if not direct["ok"] and "port is down" in direct["detail"]:
        if not recovery._cooldown_active("eth0"):
            recovery._mark_repair("eth0")
            brought_up = _bring_up_eth0()
            now_up = connection_health.check_direct_link()["ok"]
            incidents.append(recovery._incident(
                "eth0", "direct ethernet link was down",
                "brought eth0 up" if brought_up else "eth0 up failed",
                "link restored" if now_up else "still down — check the cable",
                now_up,
            ))

    # 3. Speech engine — Whisper assets + the wake listener.
    if not WHISPER_CLI.exists() or not WHISPER_MODEL.exists():
        incidents.append(recovery._incident(
            "speech_engine", "Whisper binary or model missing",
            "none (needs a rebuild of tools/whisper.cpp)",
            "Whisper unavailable; Vosk fallback still handles commands", False,
        ))

    # 4. Companion — report only (never restart the PC from here); and
    #    only flag it as a problem when the PC is truly offline.
    if connection_health.pc_is_truly_offline():
        incidents.append(recovery._incident(
            "companion", "PC companion and direct link both unreachable",
            "none (start the companion on the PC)",
            "PC is genuinely offline", False,
        ))

    return incidents


def spoken_report(incidents):
    if not incidents:
        return "Self-heal complete — everything's already healthy, nothing to repair."

    fixed = [i for i in incidents if i["resolved"]]
    unfixed = [i for i in incidents if not i["resolved"]]

    parts = [f"Self-heal ran. I found {len(incidents)} issue{'s' if len(incidents) != 1 else ''}"]
    for i in fixed:
        parts.append(f"repaired {i['component']}: {i['action']}, {i['verification']}")
    for i in unfixed:
        parts.append(f"couldn't fully fix {i['component']}: {i['verification']}")
    return ". ".join(parts) + "."


def heal_now():
    """On-demand heal. Logs an incident summary and returns spoken text."""
    incidents = check_and_heal()
    logbook.record_incident(
        "self_healing", "on-demand self-heal",
        f"{len(incidents)} issues handled",
        f"{sum(1 for i in incidents if i['resolved'])} resolved",
        all(i["resolved"] for i in incidents) if incidents else True,
    )
    return spoken_report(incidents)


def monitor_loop(speak, log, should_stay_quiet):
    """Background self-healing. Only speaks when it actually repairs or
    fails to repair something — silent when healthy. speak/log/
    should_stay_quiet injected by the hub."""
    # Grace period so a normal boot settles before the first sweep.
    time.sleep(90)
    while True:
        try:
            incidents = check_and_heal()
            acted = [i for i in incidents if i["action"] not in ("none", "skipped (cooldown)")]
            if acted and not should_stay_quiet():
                report = spoken_report(incidents)
                log(report)
                speak(report)
        except Exception as error:
            print("Self-healing error:", type(error).__name__, error, flush=True)
        time.sleep(MONITOR_INTERVAL_SECONDS)
