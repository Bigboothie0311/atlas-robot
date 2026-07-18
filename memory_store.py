import json
import time
from pathlib import Path


FACTS_PATH = Path("/home/atlas/atlas-robot/data/memory_facts.json")
MAX_FACTS = 50

LAST_INTERACTION_PATH = Path("/home/atlas/atlas-robot/data/last_interaction.json")

REMINDERS_PATH = Path("/home/atlas/atlas-robot/data/reminders.json")

# Session turns live in-memory only (wake_listener.py runs as one long-lived
# process across wake-ups, so this survives between turns but resets on
# service restart — that's the intended "session" scope).
SESSION_TIMEOUT_SECONDS = 300
MAX_SESSION_TURNS = 6

_session_turns = []

REMEMBER_PREFIXES = [
    "remember that ",
    "remember this ",
    "please remember that ",
    "please remember ",
    "remember ",
]

FORGET_PHRASES = {
    "forget everything",
    "forget everything you know about me",
    "clear your memory",
    "forget what you know about me",
}


def record_turn(question, answer):
    now = time.monotonic()
    _session_turns.append({"question": question, "answer": answer, "time": now})

    while len(_session_turns) > MAX_SESSION_TURNS:
        _session_turns.pop(0)


def get_recent_context():
    """Returns recent in-session Q&A turns as a list of (question, answer)
    tuples, pruning anything older than SESSION_TIMEOUT_SECONDS."""
    now = time.monotonic()

    while _session_turns and now - _session_turns[0]["time"] > SESSION_TIMEOUT_SECONDS:
        _session_turns.pop(0)

    return [(turn["question"], turn["answer"]) for turn in _session_turns]


def load_facts():
    if not FACTS_PATH.exists():
        return []

    try:
        data = json.loads(FACTS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [fact for fact in data if isinstance(fact, dict) and fact.get("text")]


def save_facts(facts):
    FACTS_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = FACTS_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(facts, indent=2))
    temporary_path.replace(FACTS_PATH)


def add_fact(text):
    text = text.strip()

    if not text:
        return

    facts = load_facts()
    facts.append({"text": text, "added": time.time()})
    facts = facts[-MAX_FACTS:]
    save_facts(facts)


def search_facts(keyword):
    """Facts mentioning keyword — powers 'what do you remember about X'."""
    keyword = keyword.lower().strip()
    if not keyword:
        return []
    return [f["text"] for f in load_facts() if keyword in f["text"].lower()]


def forget_matching(keyword):
    """Removes facts mentioning keyword — 'forget that X'. Returns how many
    were removed."""
    keyword = keyword.lower().strip()
    if not keyword:
        return 0
    facts = load_facts()
    kept = [f for f in facts if keyword not in f["text"].lower()]
    removed = len(facts) - len(kept)
    if removed:
        save_facts(kept)
    return removed


PRIORITIES_PATH = Path("/home/atlas/atlas-robot/data/priorities.json")
MAX_PRIORITIES = 20


def load_priorities():
    if not PRIORITIES_PATH.exists():
        return []
    try:
        data = json.loads(PRIORITIES_PATH.read_text())
        return [p for p in data if isinstance(p, dict) and p.get("text")] if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_priorities(priorities):
    PRIORITIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PRIORITIES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(priorities, indent=2))
    tmp.replace(PRIORITIES_PATH)


def add_priority(text):
    text = text.strip()
    if not text:
        return
    priorities = load_priorities()
    priorities.append({"text": text, "added": time.time()})
    save_priorities(priorities[-MAX_PRIORITIES:])


def clear_priorities():
    save_priorities([])


def get_priorities_summary():
    return [p["text"] for p in load_priorities()]


def clear_facts():
    save_facts([])


def get_facts_summary():
    return [fact["text"] for fact in load_facts()]


def parse_remember_command(text):
    """Returns the fact to store if text is a 'remember that ...' style
    request, otherwise None."""
    normalized = text.lower().strip()

    for punctuation in [",", ".", "?", "!", ";", ":"]:
        normalized = normalized.replace(punctuation, " ")

    normalized = " ".join(normalized.split())

    if not normalized:
        return None

    for prefix in sorted(REMEMBER_PREFIXES, key=len, reverse=True):
        if normalized.startswith(prefix):
            fact = normalized[len(prefix):].strip()
            return fact or None

    return None


def is_forget_command(text):
    normalized = text.lower().strip()

    for punctuation in [",", ".", "?", "!", ";", ":"]:
        normalized = normalized.replace(punctuation, " ")

    normalized = " ".join(normalized.split())

    return normalized in FORGET_PHRASES


NOTES_PATH = Path("/home/atlas/atlas-robot/data/notes.json")
MAX_NOTES = 100

NOTE_PREFIXES = [
    "take a note that ",
    "take a note ",
    "make a note that ",
    "make a note ",
    "note that ",
    "write down that ",
    "write down ",
]

READ_NOTES_PHRASES = {
    "read my notes",
    "read my notes back",
    "read me my notes",
    "read back my notes",
    "what are my notes",
    "what notes do i have",
}

CLEAR_NOTES_PHRASES = {
    "clear my notes",
    "delete my notes",
    "erase my notes",
    "delete all my notes",
}


def _normalize_phrase(text):
    normalized = text.lower().strip()

    for punctuation in [",", ".", "?", "!", ";", ":"]:
        normalized = normalized.replace(punctuation, " ")

    return " ".join(normalized.split())


def load_notes():
    if not NOTES_PATH.exists():
        return []

    try:
        data = json.loads(NOTES_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [note for note in data if isinstance(note, dict) and note.get("text")]


def save_notes(notes):
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = NOTES_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(notes, indent=2))
    temporary_path.replace(NOTES_PATH)


def add_note(text):
    text = text.strip()

    if not text:
        return

    notes = load_notes()
    notes.append({"text": text, "added": time.time()})
    save_notes(notes[-MAX_NOTES:])


def clear_notes():
    save_notes([])


def parse_note_command(text):
    """Returns the note text for a 'take a note ...' request, else None."""
    normalized = _normalize_phrase(text)

    if not normalized:
        return None

    for prefix in sorted(NOTE_PREFIXES, key=len, reverse=True):
        if normalized.startswith(prefix):
            note = normalized[len(prefix):].strip()
            return note or None

    return None


def is_read_notes_command(text):
    return _normalize_phrase(text) in READ_NOTES_PHRASES


def is_clear_notes_command(text):
    return _normalize_phrase(text) in CLEAR_NOTES_PHRASES


def mark_interaction_now():
    LAST_INTERACTION_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = LAST_INTERACTION_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps({"last_at": time.time()}))
    temporary_path.replace(LAST_INTERACTION_PATH)


def get_last_interaction_gap_seconds():
    """Returns seconds since the last recorded interaction, or None if
    there's no prior record."""
    if not LAST_INTERACTION_PATH.exists():
        return None

    try:
        data = json.loads(LAST_INTERACTION_PATH.read_text())
        last_at = float(data["last_at"])
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
        return None

    return time.time() - last_at


def load_reminders():
    if not REMINDERS_PATH.exists():
        return []

    try:
        data = json.loads(REMINDERS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [
        r for r in data
        if isinstance(r, dict) and "due_at" in r and "message" in r
    ]


def save_reminders(reminders):
    REMINDERS_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = REMINDERS_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(reminders, indent=2))
    temporary_path.replace(REMINDERS_PATH)


def add_reminder(delay_seconds, message):
    reminders = load_reminders()
    reminders.append({"due_at": time.time() + delay_seconds, "message": message})
    save_reminders(reminders)


def pop_due_reminders():
    """Returns reminders that are due now, removing them from storage."""
    reminders = load_reminders()
    now = time.time()

    due = [r for r in reminders if r["due_at"] <= now]
    remaining = [r for r in reminders if r["due_at"] > now]

    if due:
        save_reminders(remaining)

    return due


def build_memory_context_block():
    """Returns a prompt-ready string summarizing recent conversation and
    remembered facts, or an empty string if there's nothing to add."""
    recent = get_recent_context()
    facts = get_facts_summary()

    if not recent and not facts:
        return ""

    parts = []

    if recent:
        lines = ["Recent conversation in this session (most recent last):"]

        for question, answer in recent:
            lines.append(f"> {question}")
            lines.append(f"  {answer}")

        parts.append("\n".join(lines))

    if facts:
        lines = ["Remembered facts about the user or situation:"]

        for fact in facts:
            lines.append(f"- {fact}")

        parts.append("\n".join(lines))

    return "\n\n".join(parts)
