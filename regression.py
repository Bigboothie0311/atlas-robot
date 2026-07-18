"""Regression checks — the safety net for controlled self-upgrades.

Verifies the load-bearing surfaces still work: services up, hub endpoints
responding, the voice command parsers still classify correctly, the HUD
serves, and the camera/security modules import. Deterministic and local
(zero tokens). run_all() returns (passed, results); a self-upgrade canary
auto-rolls-back if this regresses.

Runnable standalone:  venv/bin/python regression.py
"""
import subprocess

import requests

HUB = "http://127.0.0.1:5051"

SERVICES = [
    "atlas-robot.service",
    "atlas-wake.service",
    "atlas-hud.service",
]


def _check(name, ok, detail=""):
    return {"name": name, "ok": bool(ok), "detail": detail}


def check_services():
    results = []
    for unit in SERVICES:
        try:
            active = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip() == "active"
        except (subprocess.SubprocessError, OSError):
            active = False
        results.append(_check(f"service:{unit}", active,
                              "active" if active else "DOWN"))
    return results


def check_endpoints():
    results = []
    checks = [
        ("GET /status", lambda: requests.get(f"{HUB}/status", timeout=5)),
        ("GET /state", lambda: requests.get(f"{HUB}/state", timeout=5)),
        ("GET /hud/stats", lambda: requests.get(f"{HUB}/hud/stats", timeout=8)),
        ("GET /hud", lambda: requests.get(f"{HUB}/hud", timeout=5)),
        ("GET /hud/static/app.js",
         lambda: requests.get(f"{HUB}/hud/static/app.js", timeout=5)),
    ]
    for name, call in checks:
        try:
            ok = call().status_code == 200
        except requests.RequestException:
            ok = False
        results.append(_check(f"endpoint:{name}", ok))
    return results


def check_parsers():
    """The command parsers must still route the core intents correctly —
    this is what a bad self-upgrade would most likely break."""
    import listen_and_answer as la
    import memory_store as ms

    cases = [
        ("timer set", lambda: la.parse_timer_set_command("set a timer for five minutes") == 300),
        ("reminder", lambda: la.parse_reminder_command("remind me in ten minutes to eat") == (600, "eat")),
        ("focus", lambda: la.parse_focus_start_command("focus mode") == 25),
        ("note", lambda: ms.parse_note_command("take a note buy milk") == "buy milk"),
        ("instant time", lambda: la.parse_instant_answer("what time is it") is not None),
        ("wake pc phrase", lambda: la._normalize_phrase("boot my pc") in la.WAKE_PC_PHRASES),
        ("network intent", lambda: la.is_network_devices_command("list the devices on my network")),
        ("enroll intent", lambda: la.is_enroll_face_command("learn my face")),
        ("url strip", lambda: la.strip_spoken_urls("Hi. Source: https://x.com") == "Hi."),
        ("intent classify", lambda: la._classify_intent("set a timer for 3 minutes") == "timer"),
    ]
    results = []
    for name, test in cases:
        try:
            ok = bool(test())
        except Exception as error:
            ok = False
            name = f"{name} ({type(error).__name__})"
        results.append(_check(f"parser:{name}", ok))
    return results


def check_modules():
    """Security/health modules must import and expose their entry points."""
    results = []
    modules = [
        ("camera_gate", ["verify", "enroll", "unreviewed_intruders"]),
        ("logbook", ["start_turn", "diagnostic_summary"]),
        ("recovery", ["run_playbook"]),
        ("system_health", ["run_full_sweep"]),
        ("net_defense", ["audit"]),
    ]
    for module_name, attrs in modules:
        try:
            module = __import__(module_name)
            ok = all(hasattr(module, a) for a in attrs)
        except Exception:
            ok = False
        results.append(_check(f"module:{module_name}", ok))
    return results


def run_all():
    """Returns (all_passed, results)."""
    results = (
        check_services()
        + check_endpoints()
        + check_parsers()
        + check_modules()
    )
    passed = all(r["ok"] for r in results)
    return passed, results


if __name__ == "__main__":
    ok, results = run_all()
    for r in results:
        print(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['name']}"
              + (f"  ({r['detail']})" if r["detail"] else ""))
    print("REGRESSION:", "ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)
