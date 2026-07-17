"""Spoken briefings: the on-demand/morning rundown and the news brief.

Everything here is zero-token — weather comes from the cached open-meteo
fetch, reminders and notes are local files, PC status is the existing LAN
poll, and headlines are the free ddgs news search.
"""
import json
import time
from datetime import datetime
from pathlib import Path

import requests

import hud_stats
import memory_store
import pc_stats

LAST_BRIEFING_PATH = Path("/home/atlas/atlas-robot/data/last_briefing.json")

BRIEFING_HEADLINE_COUNT = 3

HUB = "http://127.0.0.1:5051"


def _fetch_hub_stats():
    """Cross-process data (network device count, printer) lives in the hub —
    fetch its stats endpoint rather than duplicating collection here.
    Returns {} on any failure so the briefing degrades instead of dying."""
    try:
        response = requests.get(f"{HUB}/hud/stats", timeout=5)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        return {}


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
    """The full ops rundown: weather, reminders, notes, gaming PC, printer,
    Pi health, network, news. Every section degrades independently — one
    failed source shortens the briefing rather than killing it."""
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

    hub_stats = _fetch_hub_stats()

    printer = hub_stats.get("printer", {})

    if printer.get("online"):
        state = printer.get("state")
        progress = printer.get("progress_percent")

        if state in ("building", "printing") and progress is not None:
            parts.append(f"The printer is mid-job, {progress} percent done.")
        else:
            parts.append("The printer is online and idle.")

    device_count = (hub_stats.get("network") or {}).get("device_count")

    if device_count:
        parts.append(f"{device_count} devices on the network.")

    cpu = hud_stats.get_cpu_stats()
    disk = hud_stats.get_disk_stats()
    health_bits = []

    if disk["percent"] >= 85:
        health_bits.append(f"disk is at {disk['percent']:.0f} percent")

    if cpu.get("temp_c") is not None and cpu["temp_c"] >= 75:
        health_bits.append(f"my core is at {cpu['temp_c']:.0f} degrees")

    if health_bits:
        parts.append("Heads up: " + " and ".join(health_bits) + ".")
    else:
        parts.append("All my systems are healthy.")

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
