"""Sky Watch — upcoming sky events, all free / no API key.

Sources:
  - meteor showers: hard-coded annual peaks (deterministic, offline)
  - moon phase: computed from date (offline)
  - stargazing tonight: open-meteo cloud cover + moon (reuses weather)
  - rocket launches: Launch Library 2 (thespacedevs, free, no key)
  - ISS position: wheretheiss.at (free, no key)

Everything is cached and degrades to a graceful message on failure.
Planet visibility needs an ephemeris library and is intentionally left as
a "check a sky map" pointer rather than a wrong guess.
"""
import time
from datetime import datetime, timedelta

import requests

import robot_config

# --- Meteor showers: (name, peak month, peak day, rough ZHR) ---------
METEOR_SHOWERS = [
    ("Quadrantids", 1, 3, 110),
    ("Lyrids", 4, 22, 18),
    ("Eta Aquariids", 5, 6, 50),
    ("Perseids", 8, 12, 100),
    ("Draconids", 10, 8, 10),
    ("Orionids", 10, 21, 20),
    ("Leonids", 11, 17, 15),
    ("Geminids", 12, 14, 150),
    ("Ursids", 12, 22, 10),
]

_cache = {}
_CACHE_TTL = 1800


def _cached(key, producer):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[1] < _CACHE_TTL:
        return hit[0]
    value = producer()
    _cache[key] = (value, now)
    return value


def next_meteor_shower():
    today = datetime.now()
    best = None
    for name, month, day, zhr in METEOR_SHOWERS:
        peak = datetime(today.year, month, day)
        if peak < today:
            peak = datetime(today.year + 1, month, day)
        days = (peak - today).days
        if best is None or days < best[1]:
            best = (name, days, zhr, peak)
    if best is None:
        return None
    name, days, zhr, peak = best
    return {"name": name, "days_away": days, "zhr": zhr,
            "date": peak.strftime("%B %-d")}


def moon_phase():
    """Approximate phase (0=new, 0.5=full) via days since a known new
    moon (2000-01-06), synodic month 29.53 days."""
    known_new = datetime(2000, 1, 6, 18, 14)
    days = (datetime.now() - known_new).total_seconds() / 86400.0
    frac = (days % 29.53059) / 29.53059
    names = [
        (0.03, "new moon"), (0.22, "waxing crescent"), (0.28, "first quarter"),
        (0.47, "waxing gibbous"), (0.53, "full moon"), (0.72, "waning gibbous"),
        (0.78, "last quarter"), (0.97, "waning crescent"), (1.0, "new moon"),
    ]
    name = next(n for threshold, n in names if frac <= threshold)
    illumination = round((1 - abs(0.5 - frac) * 2) * 100)
    return {"phase": name, "illumination_percent": illumination}


def _cloud_cover_tonight():
    lat, lon, _city = robot_config.home_location()
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat, "longitude": lon, "hourly": "cloud_cover",
            "timezone": "auto", "forecast_days": 1,
        }, timeout=8)
        r.raise_for_status()
        hourly = r.json().get("hourly", {})
        covers = hourly.get("cloud_cover", [])
        # Evening/night hours ~20:00-23:00.
        night = covers[20:24] if len(covers) >= 24 else covers
        return round(sum(night) / len(night)) if night else None
    except (requests.RequestException, ValueError, ZeroDivisionError):
        return None


def stargazing_tonight():
    cover = _cached("cloud", _cloud_cover_tonight)
    moon = moon_phase()
    if cover is None:
        return {"verdict": "unknown", "cloud_cover": None, "moon": moon}

    good = cover < 30 and moon["illumination_percent"] < 60
    verdict = "great" if cover < 15 else ("good" if good else ("okay" if cover < 50 else "poor"))
    return {"verdict": verdict, "cloud_cover": cover, "moon": moon}


def upcoming_launches(limit=3):
    def fetch():
        try:
            r = requests.get(
                "https://ll.thespacedevs.com/2.2.0/launch/upcoming/",
                params={"limit": limit, "mode": "list"}, timeout=10)
            r.raise_for_status()
            results = r.json().get("results", [])
            out = []
            for launch in results[:limit]:
                out.append({
                    "name": launch.get("name", "a launch"),
                    "when": launch.get("net", ""),
                    "provider": (launch.get("launch_service_provider") or {}).get("name", ""),
                })
            return out
        except (requests.RequestException, ValueError):
            return []
    return _cached("launches", fetch)


def iss_position():
    def fetch():
        try:
            r = requests.get("https://api.wheretheiss.at/v1/satellites/25544", timeout=8)
            r.raise_for_status()
            d = r.json()
            return {"lat": round(d["latitude"], 1), "lon": round(d["longitude"], 1)}
        except (requests.RequestException, ValueError, KeyError):
            return None
    return _cached("iss", fetch)


def spoken_summary():
    """Combined sky report."""
    parts = []

    shower = next_meteor_shower()
    if shower:
        if shower["days_away"] == 0:
            parts.append(f"the {shower['name']} meteor shower peaks tonight")
        elif shower["days_away"] <= 14:
            parts.append(f"the {shower['name']} meteor shower peaks in {shower['days_away']} days, on {shower['date']}")
        else:
            parts.append(f"the next meteor shower is the {shower['name']} on {shower['date']}")

    moon = moon_phase()
    parts.append(f"the moon is a {moon['phase']}, {moon['illumination_percent']} percent lit")

    star = stargazing_tonight()
    if star["verdict"] != "unknown":
        cover = star["cloud_cover"]
        parts.append(f"stargazing tonight looks {star['verdict']} with {cover} percent cloud cover")

    launches = upcoming_launches(1)
    if launches:
        parts.append(f"the next rocket launch is {launches[0]['name']}")

    if not parts:
        return "I couldn't reach the sky data right now."

    return "Sky watch. " + ". ".join(p[0].upper() + p[1:] for p in parts) + "."
