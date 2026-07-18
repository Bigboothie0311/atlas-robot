"""PC app profiles — 'work mode', 'design mode', 'game mode'.

Each profile opens a defined group of ALREADY-APPROVED apps and sets PC
volume + optional focus. Config lives in data/pc_profiles.json (editable);
sensible defaults are written on first use. Nothing here can launch an
arbitrary app — profiles only reference the companion's whitelisted
actions (open_fusion / open_spotify / open_claude / open_app-from-config).
"""
import json
import time
from pathlib import Path

import pc_control

PROFILES_PATH = Path("/home/atlas/atlas-robot/data/pc_profiles.json")

DEFAULT_PROFILES = {
    "work": {
        "apps": ["open_claude"],
        "volume": 45,
        "focus": True,
        "say": "Work mode. Opening Claude and setting a calm volume.",
    },
    "design": {
        "apps": ["open_fusion"],
        "volume": 40,
        "focus": True,
        "say": "Design mode. Opening Fusion 360.",
    },
    "game": {
        "apps": ["open_spotify"],
        "volume": 85,
        "focus": False,
        "say": "Game mode. Spotify up, volume loud, focus off.",
    },
}

# Map profile action tokens to pc_control calls. Only these are allowed.
_ACTION_DISPATCH = {
    "open_fusion": pc_control.open_fusion,
    "open_spotify": pc_control.open_spotify,
    "open_claude": pc_control.open_claude,
}


def load_profiles():
    if not PROFILES_PATH.exists():
        save_profiles(DEFAULT_PROFILES)
        return dict(DEFAULT_PROFILES)
    try:
        data = json.loads(PROFILES_PATH.read_text())
        return data if isinstance(data, dict) else dict(DEFAULT_PROFILES)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_PROFILES)


def save_profiles(profiles):
    PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(profiles, indent=2))
    tmp.replace(PROFILES_PATH)


def profile_names():
    return list(load_profiles().keys())


def activate(name, set_focus=None):
    """Runs a profile. set_focus is an injected callback (turn Pi focus
    mode on/off) so this module stays free of the hub client. Returns
    spoken text."""
    profiles = load_profiles()
    profile = profiles.get(name)

    if profile is None:
        return f"I don't have a {name} profile set up."

    if not pc_control.is_configured() or not pc_control.pc_reachable():
        return f"I can't reach your PC to start {name} mode."

    # Open the profile's apps (approved actions only).
    for action in profile.get("apps", []):
        handler = _ACTION_DISPATCH.get(action)
        if handler:
            handler()
        elif isinstance(action, dict) and "app" in action:
            pc_control.open_app(action["app"])
        time.sleep(0.5)

    # Volume.
    if "volume" in profile:
        pc_control.set_volume_level(profile["volume"])

    # Pi-side focus mode.
    if set_focus is not None and "focus" in profile:
        set_focus(bool(profile["focus"]))

    return profile.get("say", f"{name.title()} mode active.")
