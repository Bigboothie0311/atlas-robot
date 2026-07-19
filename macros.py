"""Teachable commands: "when I say X, do Y [and then Z]" lets the user
define a phrase that replays one or more existing commands. Macros store
raw action phrases, not new behavior — execution replays each phrase
through the normal dispatch, so no command-handling logic is duplicated
here.
"""
import json
import re
import time
from pathlib import Path


MACROS_PATH = Path("/home/atlas/atlas-robot/data/macros.json")
MAX_MACROS = 100
MAX_ACTIONS_PER_MACRO = 5

_TEACH_PATTERNS = [
    re.compile(r"^teach you (?:that )?when i say (.+?) you should (.+)$"),
    re.compile(r"^when i say (.+?) you should (.+)$"),
    re.compile(r"^when i say (.+?) do (.+)$"),
    re.compile(r"^when i say (.+?) then (.+)$"),
    re.compile(r"^if i say (.+?) then (.+)$"),
    re.compile(r"^teach you (?:that )?(.+?) means (.+)$"),
    re.compile(r"^learn (?:this )?command (.+?) does (.+)$"),
]

_FORGET_PATTERNS = [
    re.compile(r"^forget (?:the )?macro (.+)$"),
    re.compile(r"^forget what (.+) means$"),
    re.compile(r"^forget (?:the )?command (.+)$"),
    re.compile(r"^delete (?:the )?macro (.+)$"),
    re.compile(r"^unlearn (.+)$"),
]

# Only an explicit sequence word splits one taught action into several —
# a bare "and" stays intact so "add milk and eggs to my list" isn't cut in
# half.
_ACTION_SPLIT_PATTERN = re.compile(r"\s*,?\s*(?:and then|then)\s+")

LIST_MACROS_PHRASES = {
    "list my macros", "list your macros", "what macros do you know",
    "what macros do i have", "what commands have i taught you",
    "what have i taught you",
}


def _normalize_phrase(text):
    normalized = text.lower().strip()

    for punctuation in [",", ".", "?", "!", ";", ":"]:
        normalized = normalized.replace(punctuation, " ")

    return " ".join(normalized.split())


def load_macros():
    if not MACROS_PATH.exists():
        return {}

    try:
        data = json.loads(MACROS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}

    return {
        trigger: entry
        for trigger, entry in data.items()
        if isinstance(entry, dict) and isinstance(entry.get("actions"), list) and entry["actions"]
    }


def save_macros(macros):
    MACROS_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = MACROS_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(macros, indent=2))
    temporary_path.replace(MACROS_PATH)


def _split_actions(actions_text):
    parts = _ACTION_SPLIT_PATTERN.split(actions_text.strip())
    return [part.strip() for part in parts if part.strip()][:MAX_ACTIONS_PER_MACRO]


def parse_teach_command(text):
    """Returns (trigger, [action, ...]) for a "when I say X do Y [and then
    Z]" style request, otherwise None."""
    normalized = _normalize_phrase(text)

    for pattern in _TEACH_PATTERNS:
        match = pattern.match(normalized)

        if not match:
            continue

        trigger = match.group(1).strip()
        actions = _split_actions(match.group(2))

        if trigger and actions:
            return trigger, actions

    return None


def teach_macro(trigger, actions):
    trigger = _normalize_phrase(trigger)
    macros = load_macros()
    macros[trigger] = {"actions": actions, "taught": time.time()}

    # Oldest-first eviction once the cap is hit, so a runaway teaching
    # loop can't grow the file forever.
    if len(macros) > MAX_MACROS:
        oldest = sorted(macros.items(), key=lambda item: item[1].get("taught", 0))
        macros = dict(oldest[-MAX_MACROS:])

    save_macros(macros)


def match_macro(normalized_phrase):
    entry = load_macros().get(normalized_phrase)
    return entry["actions"] if entry else None


def is_list_macros_command(normalized_phrase):
    return normalized_phrase in LIST_MACROS_PHRASES


def list_macros_summary():
    macros = load_macros()

    if not macros:
        return "You haven't taught me any macros yet."

    lines = [
        f"'{trigger}' does: {' then '.join(entry['actions'])}"
        for trigger, entry in macros.items()
    ]
    return "Here's what you've taught me: " + "; ".join(lines) + "."


def parse_forget_macro_command(text):
    normalized = _normalize_phrase(text)

    for pattern in _FORGET_PATTERNS:
        match = pattern.match(normalized)

        if match:
            return match.group(1).strip()

    return None


def forget_macro(trigger):
    trigger = _normalize_phrase(trigger)
    macros = load_macros()

    if trigger not in macros:
        return False

    del macros[trigger]
    save_macros(macros)
    return True
