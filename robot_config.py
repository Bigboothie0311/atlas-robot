"""Shared reader for config/robot.env — keeps personal values (home
location, station name, device MACs) out of tracked source. The env file
itself is gitignored; tracked code carries only neutral defaults.
"""
from pathlib import Path

ROBOT_ENV_PATH = Path("/home/atlas/atlas-robot/config/robot.env")

# Neutral fallbacks used when robot.env is absent or a key is unset — a
# fresh clone runs without leaking the original operator's location.
DEFAULTS = {
    "HOME_LATITUDE": "0.0",
    "HOME_LONGITUDE": "0.0",
    "HOME_CITY": "HOME",
    "STATION_NAME": "STATION-01",
}

_cache = None


def _load():
    global _cache

    if _cache is not None:
        return _cache

    values = dict(DEFAULTS)

    if ROBOT_ENV_PATH.exists():
        for line in ROBOT_ENV_PATH.read_text().splitlines():
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")

    _cache = values
    return values


def get(key, default=None):
    return _load().get(key, default)


def get_float(key, default=0.0):
    try:
        return float(_load().get(key, default))
    except (TypeError, ValueError):
        return default


def home_location():
    """(latitude, longitude, city) for weather and the HUD label."""
    return (
        get_float("HOME_LATITUDE"),
        get_float("HOME_LONGITUDE"),
        get("HOME_CITY", "HOME"),
    )
