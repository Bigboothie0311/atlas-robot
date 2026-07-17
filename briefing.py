"""Spoken briefings: the on-demand/morning rundown and the news brief.

Everything here is zero-token — weather comes from the cached open-meteo
fetch, reminders and notes are local files, PC status is the existing LAN
poll, and headlines are the free ddgs news search.
"""
import json
import time
from datetime import datetime
from pathlib import Path

import hud_stats
import memory_store
import pc_stats

LAST_BRIEFING_PATH = Path("/home/atlas/atlas-robot/data/last_briefing.json")

BRIEFING_HEADLINE_COUNT = 3


def _spoken_headlines(count):
    headlines = hud_stats.get_headlines()[:count]

    if not headlines:
        return None

    titles = [headline["title"].rstrip(".") for headline in headlines]
    return "In the news: " + ". ".join(titles) + "."


def build_news_brief():
    """Spoken top headlines, or a graceful miss."""
    spoken = _spoken_headlines(BRIEFING_HEADLINE_COUNT)
    return spoken or "I couldn't pull any headlines right now."


def build_briefing_text():
    """The full rundown: weather, reminders, notes, gaming PC, news."""
    parts = []

    weather = hud_stats.get_weather_stats()

    if weather.get("temp_f") is not None:
        line = (
            f"It's {weather['temp_f']} degrees and {weather['condition']}, "
            f"with a high of {weather['high_f']} and a low of {weather['low_f']}."
        )
        precip = weather.get("precip_chance")

        if precip is not None and precip >= 30:
            line += f" There's a {precip:.0f} percent chance of rain."

        parts.append(line)

    reminders = memory_store.load_reminders()

    if reminders:
        count = len(reminders)
        word = "reminder" if count == 1 else "reminders"
        parts.append(f"You have {count} {word} scheduled.")

    notes = memory_store.load_notes()

    if notes:
        count = len(notes)
        word = "note" if count == 1 else "notes"
        parts.append(f"You have {count} saved {word}.")

    if pc_stats.get_gaming_pc_stats().get("online"):
        parts.append("Your gaming PC is online.")

    news = _spoken_headlines(BRIEFING_HEADLINE_COUNT)

    if news:
        parts.append(news)

    if not parts:
        return "Nothing to report — all quiet."

    return " ".join(parts)


def was_briefed_today():
    if not LAST_BRIEFING_PATH.exists():
        return False

    try:
        data = json.loads(LAST_BRIEFING_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    return data.get("date") == datetime.now().strftime("%Y-%m-%d")


def mark_briefed_today():
    LAST_BRIEFING_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = LAST_BRIEFING_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "at": time.time(),
    }))
    temporary_path.replace(LAST_BRIEFING_PATH)
