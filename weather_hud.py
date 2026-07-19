"""Extended weather + radar forecast for the full-screen HUD weather
screen. Where hud_stats.get_weather_stats() drives the small always-on
weather panel (one temp, one high/low), this module powers the dedicated
full-screen view: current conditions, an hourly rain forecast, a multi-day
outlook, and a live radar loop.

Everything here is free / no API key — forecast data from open-meteo (same
source hud_stats already uses) and the radar loop from the US National
Weather Service RIDGE imagery, matching sky_watch.py's no-key ethos.
"""
from datetime import datetime

import requests

from ai_tools import WEATHER_CODE_DESCRIPTIONS
import robot_config

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# The robot lives in one physical place — read from config/robot.env
# (gitignored) so the real coordinates never land in tracked source.
HOME_LATITUDE, HOME_LONGITUDE, HOME_CITY = robot_config.home_location()

# NWS RIDGE radar loop (animated GIF, always current, no key). KNKX is the
# San Diego station, which covers Oceanside; override with RADAR_STATION in
# robot.env for a different site.
DEFAULT_RADAR_STATION = "KNKX"


def radar_loop_url():
    station = robot_config.get("RADAR_STATION", DEFAULT_RADAR_STATION) or DEFAULT_RADAR_STATION
    return f"https://radar.weather.gov/ridge/standard/{station}_loop.gif"


FORECAST_CACHE_SECONDS = 600
HOURLY_HOURS = 12
FORECAST_DAYS = 6

_forecast_cache = {"data": None, "fetched_at": 0.0}


def _label_hour(iso_time):
    """'2026-07-19T15:00' -> '3 PM'."""
    dt = datetime.fromisoformat(iso_time)
    return dt.strftime("%-I %p")


def _label_day(iso_date):
    """'2026-07-19' -> 'Sun'."""
    dt = datetime.fromisoformat(iso_date)
    return dt.strftime("%a")


def _round_or_none(value):
    return round(value) if value is not None else None


def _current_hour_index(hourly_times, current_time):
    """Index of the first hourly entry at or after the current hour, so the
    hourly strip starts 'now' rather than at midnight."""
    for index, iso_time in enumerate(hourly_times):
        if iso_time >= current_time:
            return index
    return 0


def _build_forecast(payload, city, radar_url):
    """Pure transform of an open-meteo payload into the HUD forecast shape.
    Kept free of network/cache so it is directly unit-testable."""
    current = payload.get("current", {})
    hourly = payload.get("hourly", {})
    daily = payload.get("daily", {})

    temp_f = current.get("temperature_2m")
    code = current.get("weather_code")

    if temp_f is None or code is None:
        raise ValueError("Incomplete weather payload")

    hourly_times = hourly.get("time") or []
    hourly_temps = hourly.get("temperature_2m") or []
    hourly_precip = hourly.get("precipitation_probability") or []
    hourly_codes = hourly.get("weather_code") or []

    start = _current_hour_index(hourly_times, current.get("time", ""))
    hourly_out = []
    for i in range(start, min(start + HOURLY_HOURS, len(hourly_times))):
        hourly_out.append({
            "label": _label_hour(hourly_times[i]),
            "temp_f": _round_or_none(hourly_temps[i]) if i < len(hourly_temps) else None,
            "precip_chance": hourly_precip[i] if i < len(hourly_precip) else None,
            "condition": WEATHER_CODE_DESCRIPTIONS.get(
                hourly_codes[i] if i < len(hourly_codes) else None, "unknown conditions"
            ),
        })

    daily_times = daily.get("time") or []
    daily_max = daily.get("temperature_2m_max") or []
    daily_min = daily.get("temperature_2m_min") or []
    daily_precip = daily.get("precipitation_probability_max") or []
    daily_codes = daily.get("weather_code") or []

    daily_out = []
    for i in range(len(daily_times)):
        daily_out.append({
            "label": _label_day(daily_times[i]),
            "high_f": _round_or_none(daily_max[i]) if i < len(daily_max) else None,
            "low_f": _round_or_none(daily_min[i]) if i < len(daily_min) else None,
            "precip_chance": daily_precip[i] if i < len(daily_precip) else None,
            "condition": WEATHER_CODE_DESCRIPTIONS.get(
                daily_codes[i] if i < len(daily_codes) else None, "unknown conditions"
            ),
        })

    return {
        "city": city,
        "current": {
            "temp_f": round(temp_f),
            "condition": WEATHER_CODE_DESCRIPTIONS.get(code, "unknown conditions"),
            "humidity": _round_or_none(current.get("relative_humidity_2m")),
            "wind_mph": _round_or_none(current.get("wind_speed_10m")),
            "precip": current.get("precipitation"),
        },
        "hourly": hourly_out,
        "daily": daily_out,
        "radar_loop_url": radar_url,
        "stale": False,
    }


def _fetch_forecast():
    response = requests.get(
        FORECAST_URL,
        params={
            "latitude": HOME_LATITUDE,
            "longitude": HOME_LONGITUDE,
            "current": (
                "temperature_2m,weather_code,relative_humidity_2m,"
                "wind_speed_10m,precipitation"
            ),
            "hourly": "temperature_2m,precipitation_probability,weather_code",
            "daily": (
                "temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,weather_code"
            ),
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "forecast_days": FORECAST_DAYS,
            "timezone": "America/Los_Angeles",
        },
        timeout=8,
    )
    response.raise_for_status()
    return _build_forecast(response.json(), HOME_CITY, radar_loop_url())


def _stale_placeholder():
    return {
        "city": HOME_CITY,
        "current": {
            "temp_f": None,
            "condition": "unavailable",
            "humidity": None,
            "wind_mph": None,
            "precip": None,
        },
        "hourly": [],
        "daily": [],
        "radar_loop_url": radar_loop_url(),
        "stale": True,
    }


def get_weather_forecast():
    """Cached, offline-tolerant extended forecast. A network failure falls
    back to the last good payload (flagged stale) or a neutral placeholder —
    the HUD weather screen never errors out, it just goes stale."""
    import time

    now = time.time()

    if (
        _forecast_cache["data"] is not None
        and now - _forecast_cache["fetched_at"] < FORECAST_CACHE_SECONDS
    ):
        return _forecast_cache["data"]

    try:
        data = _fetch_forecast()
    except (requests.RequestException, ValueError):
        if _forecast_cache["data"] is not None:
            stale = dict(_forecast_cache["data"])
            stale["stale"] = True
            return stale
        return _stale_placeholder()

    _forecast_cache["data"] = data
    _forecast_cache["fetched_at"] = now
    return data
