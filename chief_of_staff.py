"""'Atlas, what am I forgetting this week?' — the chief-of-staff view.

Aggregates commitments A.T.L.A.S. already holds locally (reminders, notes
that look like tasks, and an optional local calendar file) into a
deadline-aware weekly rundown. Fully local and zero-token by default.

Nothing here sends, schedules, submits, or purchases anything — it only
reads and reports. Connecting live external sources (email, a cloud
calendar) is a documented extension that requires credentials, explicit
approval, and would add on-demand model cost; see CHIEF_OF_STAFF.md. The
plumbing (aggregate_sources) is source-agnostic so those slot in cleanly.
"""
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import memory_store

DATA_DIR = Path("/home/atlas/atlas-robot/data")
CALENDAR_ICS_PATH = DATA_DIR / "calendar.ics"

WEEK_SECONDS = 7 * 24 * 3600

# Words in a note that suggest it's an actionable commitment/bill/task.
TASK_HINTS = re.compile(
    r"\b(pay|bill|due|deadline|call|email|send|buy|order|renew|submit|"
    r"appointment|meeting|finish|return|schedule|book|cancel)\b",
    re.IGNORECASE,
)


def _upcoming_reminders():
    """Scheduled reminders due within the week, as (due_ts, text)."""
    now = time.time()
    items = []

    for reminder in memory_store.load_reminders():
        due = reminder.get("due_at", 0)
        if now <= due <= now + WEEK_SECONDS:
            items.append((due, reminder.get("message", "")))

    return items


def _task_notes():
    """Saved notes that read like commitments."""
    return [
        note["text"] for note in memory_store.load_notes()
        if TASK_HINTS.search(note.get("text", ""))
    ]


def _parse_ics_events():
    """Minimal local-ICS parser (stdlib only): upcoming VEVENTs within the
    week as (start_ts, summary). Absent file -> no events."""
    if not CALENDAR_ICS_PATH.exists():
        return []

    try:
        text = CALENDAR_ICS_PATH.read_text()
    except OSError:
        return []

    events = []
    now = datetime.now()
    horizon = now + timedelta(days=7)

    summary = None
    for line in text.splitlines():
        line = line.strip()

        if line.startswith("SUMMARY:"):
            summary = line[len("SUMMARY:"):].strip()
        elif line.startswith("DTSTART"):
            value = line.split(":", 1)[-1].strip()
            parsed = _parse_ics_datetime(value)
            if parsed and now <= parsed <= horizon and summary:
                events.append((parsed.timestamp(), summary))
        elif line == "END:VEVENT":
            summary = None

    return events


def _parse_ics_datetime(value):
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M%SZ", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def aggregate_sources():
    """All commitments within the week, sorted by deadline. Each item is
    {when, text, source}. Extension point: append external-source items
    here once approved and configured."""
    items = []

    for due, text in _upcoming_reminders():
        items.append({"when": due, "text": text, "source": "reminder"})

    for start, summary in _parse_ics_events():
        items.append({"when": start, "text": summary, "source": "calendar"})

    for text in _task_notes():
        items.append({"when": None, "text": text, "source": "note"})

    dated = sorted((i for i in items if i["when"]), key=lambda i: i["when"])
    undated = [i for i in items if not i["when"]]
    return dated + undated


def _humanize_when(timestamp):
    when = datetime.fromtimestamp(timestamp)
    now = datetime.now()
    days = (when.date() - now.date()).days

    if days == 0:
        return f"today at {when.strftime('%-I:%M %p')}"
    if days == 1:
        return "tomorrow"
    if days < 7:
        return when.strftime("%A")
    return when.strftime("%b %-d")


def weekly_rundown():
    """Spoken 'what am I forgetting this week' summary."""
    items = aggregate_sources()

    if not items:
        return (
            "Nothing on your plate that I'm tracking this week. If you want "
            "me to watch a calendar or email, that can be set up."
        )

    dated = [i for i in items if i["when"]]
    undated = [i for i in items if not i["when"]]

    parts = [f"Here's what I'm tracking: {len(items)} thing{'s' if len(items) != 1 else ''}"]

    if dated:
        soon = dated[:4]
        listed = "; ".join(f"{i['text']} {_humanize_when(i['when'])}" for i in soon)
        parts.append(f"with deadlines — {listed}")

    if undated:
        listed = "; ".join(i["text"] for i in undated[:4])
        parts.append(f"and open items — {listed}")

    return ". ".join(parts) + "."
