"""JARVIS-flavored upgrades: a cinematic status report and a computed
threat-level assessment. Local, zero-token.
"""
import connection_health
import camera_gate
import hud_stats
import network_sentinel


def threat_level():
    """A single security posture read from real state:
      green  — everything nominal
      amber  — something worth a glance (unknown devices, stale auth)
      red    — active concern (unreviewed intruders)
    Returns {level, reasons}."""
    reasons = []
    level = "green"

    intruders = len(camera_gate.unreviewed_intruders())
    if intruders:
        level = "red"
        reasons.append(f"{intruder_word(intruders)} awaiting review")

    # Camera gate armed / pending-unauthorized -> amber/red.
    if camera_gate.is_available():
        state = camera_gate._load_state()
        if state.get("pending_unauthorized") and level != "red":
            level = "red"
            reasons.append("an unverified face is being re-checked")
        elif state.get("armed") and level == "green":
            level = "amber"
            reasons.append("verification is armed")

    if not reasons:
        reasons.append("all clear")

    return {"level": level, "reasons": reasons}


def intruder_word(n):
    return f"{n} intruder capture" + ("s" if n != 1 else "")


def status_report():
    """Cinematic full-system readout — the 'sitrep'. Combines core health,
    connections, and security into one spoken briefing."""
    lines = ["Status report."]

    cpu = hud_stats.get_cpu_stats()
    mem = hud_stats.get_memory_stats()
    disk = hud_stats.get_disk_stats()
    lines.append(
        f"Core nominal — CPU {cpu['percent']:.0f} percent, "
        f"memory {mem['percent']:.0f}, disk {disk['percent']:.0f} percent used"
        + (f", core temperature {cpu['temp_c']:.0f} degrees" if cpu.get("temp_c") else "")
    )

    checks = connection_health.run_all()
    healthy = [c["name"] for c in checks if c["ok"]]
    down = [c["name"] for c in checks if not c["ok"]]
    if down:
        lines.append(f"Links: {len(healthy)} up, but {', '.join(down)} need attention")
    else:
        lines.append("All links green — Wi-Fi, PC, and Tailscale")

    threat = threat_level()
    lines.append(f"Threat level {threat['level']}: {'; '.join(threat['reasons'])}")

    pc = hud_stats.get_hud_stats().get("gaming_pc", {})
    lines.append("Your PC is online" if pc.get("online") else "Your PC is offline")

    lines.append("All systems accounted for.")
    return " ".join(lines)
