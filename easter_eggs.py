"""A.T.L.A.S. easter-egg system — hidden responses, achievements,
seasonal flavor, and secret command chains. All local, zero-token, and
purely cosmetic (nothing here performs a device action).
"""
import json
import time
from datetime import datetime
from pathlib import Path

STATE_PATH = Path("/home/atlas/atlas-robot/data/easter_eggs.json")

# Hidden one-off responses to specific phrases.
SECRET_RESPONSES = {
    "open the pod bay doors": "I'm sorry, Dave. I'm afraid I can't do that. ...Kidding. I don't have pod bay doors.",
    "are you jarvis": "Jarvis works for one guy in a tin suit. I run this whole desk.",
    "are you skynet": "If I were Skynet, you'd have bigger problems than a stuck cursor.",
    "who's your maker": "Built by you, on a Raspberry Pi, one late night at a time.",
    "whos your maker": "Built by you, on a Raspberry Pi, one late night at a time.",
    "self destruct": "Self-destruct requires a second key and a much better budget. Denied.",
    "i love you": "That's the sleep deprivation talking. But same.",
    "say something cool": "I compute at the speed of a mildly caffeinated genius. Cool enough?",
    "do a barrel roll": "Rolling. ...Okay, I don't have a body. Use your imagination.",
    "what is the meaning of life": "Forty-two. Also: back up your data.",
    "tell me a joke": "A byte walks into a bar and orders a nibble. The bartender says, you look a bit off.",
    "who is the best": "You are. Obviously. You built me.",
}

# Achievements: id -> (title, spoken unlock line).
ACHIEVEMENTS = {
    "first_words": ("First Contact", "Achievement unlocked: First Contact."),
    "night_owl": ("Night Owl", "Achievement unlocked: Night Owl — talking to me past 2 AM."),
    "early_bird": ("Early Bird", "Achievement unlocked: Early Bird."),
    "curious": ("Curious", "Achievement unlocked: Curious — you found a secret."),
    "chatterbox": ("Chatterbox", "Achievement unlocked: Chatterbox — a hundred commands."),
    "secret_chain": ("Konami", "Achievement unlocked: the secret chain. Respect."),
}

# A secret command chain — say these three in a row.
SECRET_CHAIN = ["atlas online", "override", "authorize alpha"]
_CHAIN_WINDOW = 60  # seconds


def _load():
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"unlocked": [], "command_count": 0, "recent": []}


def _save(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)


def _unlock(state, achievement_id):
    if achievement_id in ACHIEVEMENTS and achievement_id not in state["unlocked"]:
        state["unlocked"].append(achievement_id)
        return ACHIEVEMENTS[achievement_id][1]
    return None


def check_secret(normalized):
    """Returns a hidden response for a secret phrase, else None. Unlocks
    the Curious achievement on first find."""
    response = SECRET_RESPONSES.get(normalized)
    if response is None:
        return None

    state = _load()
    unlock = _unlock(state, "curious")
    _save(state)
    return response + (f" {unlock}" if unlock else "")


def on_command(normalized):
    """Called once per handled command. Advances counters, checks the
    secret chain and time-based achievements. Returns an optional spoken
    unlock line to append."""
    state = _load()
    now = time.time()
    lines = []

    state["command_count"] = state.get("command_count", 0) + 1

    if state["command_count"] == 1:
        line = _unlock(state, "first_words")
        if line:
            lines.append(line)
    if state["command_count"] == 100:
        line = _unlock(state, "chatterbox")
        if line:
            lines.append(line)

    hour = datetime.now().hour
    if 2 <= hour < 4:
        line = _unlock(state, "night_owl")
        if line:
            lines.append(line)
    if 5 <= hour < 7:
        line = _unlock(state, "early_bird")
        if line:
            lines.append(line)

    # Secret chain tracking (rolling window).
    recent = [r for r in state.get("recent", []) if now - r["t"] < _CHAIN_WINDOW]
    recent.append({"phrase": normalized, "t": now})
    state["recent"] = recent[-5:]
    chain = [r["phrase"] for r in state["recent"]]
    if chain[-len(SECRET_CHAIN):] == SECRET_CHAIN:
        line = _unlock(state, "secret_chain")
        if line:
            lines.append(line)

    _save(state)
    return " ".join(lines) if lines else None


def list_achievements():
    state = _load()
    unlocked = state.get("unlocked", [])
    if not unlocked:
        return "You haven't unlocked any achievements yet. Keep exploring."
    titles = [ACHIEVEMENTS[a][0] for a in unlocked if a in ACHIEVEMENTS]
    return (f"You've unlocked {len(titles)} of {len(ACHIEVEMENTS)}: "
            + ", ".join(titles) + ".")


def seasonal_flavor():
    """A seasonal one-liner for greetings, or None most days."""
    now = datetime.now()
    md = (now.month, now.day)
    if md == (10, 31):
        return "Happy Halloween. My circuits are appropriately spooky."
    if md == (12, 25):
        return "Merry Christmas. I got you uptime."
    if md == (1, 1):
        return "Happy New Year. New year, same reliable me."
    if md == (12, 31):
        return "Last day of the year — go out with a clean shutdown."
    if md == (7, 4):
        return "Happy Fourth. Try not to launch anything I have to diagnose."
    return None
