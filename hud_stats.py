import shutil
import socket
import time

import psutil
import requests

from ai_tools import WEATHER_CODE_DESCRIPTIONS
import pc_stats

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Home — hardcoded since the robot lives in one physical place;
# avoids a geocoding round-trip on every poll.
HOME_LATITUDE = 0.0
HOME_LONGITUDE = -0.0

WEATHER_CACHE_SECONDS = 600

_weather_cache = {"data": None, "fetched_at": 0.0}


def _fetch_weather():
    response = requests.get(
        FORECAST_URL,
        params={
            "latitude": HOME_LATITUDE,
            "longitude": HOME_LONGITUDE,
            "current": "temperature_2m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "temperature_unit": "fahrenheit",
            "timezone": "America/Los_Angeles",
        },
        timeout=8,
    )
    response.raise_for_status()
    payload = response.json()

    current = payload.get("current", {})
    daily = payload.get("daily", {})

    temp_f = current.get("temperature_2m")
    code = current.get("weather_code")
    high_f = (daily.get("temperature_2m_max") or [None])[0]
    low_f = (daily.get("temperature_2m_min") or [None])[0]
    precip_chance = (daily.get("precipitation_probability_max") or [None])[0]

    if temp_f is None or code is None:
        raise ValueError("Incomplete weather payload")

    return {
        "temp_f": round(temp_f),
        "high_f": round(high_f) if high_f is not None else None,
        "low_f": round(low_f) if low_f is not None else None,
        "precip_chance": precip_chance,
        "condition": WEATHER_CODE_DESCRIPTIONS.get(code, "unknown conditions"),
        "stale": False,
    }


def get_weather_stats():
    now = time.time()

    if (
        _weather_cache["data"] is not None
        and now - _weather_cache["fetched_at"] < WEATHER_CACHE_SECONDS
    ):
        return _weather_cache["data"]

    try:
        data = _fetch_weather()
    except (requests.RequestException, ValueError):
        if _weather_cache["data"] is not None:
            stale = dict(_weather_cache["data"])
            stale["stale"] = True
            return stale

        return {
            "temp_f": None,
            "high_f": None,
            "low_f": None,
            "precip_chance": None,
            "condition": "unavailable",
            "stale": True,
        }

    _weather_cache["data"] = data
    _weather_cache["fetched_at"] = now
    return data


def get_cpu_stats():
    percent = psutil.cpu_percent(interval=0.1)

    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as temp_file:
            temp_c = round(int(temp_file.read().strip()) / 1000, 1)
    except (OSError, ValueError):
        temp_c = None

    return {"percent": round(percent, 1), "temp_c": temp_c}


def get_disk_stats():
    usage = shutil.disk_usage("/")
    used_gb = (usage.total - usage.free) / (1024 ** 3)
    total_gb = usage.total / (1024 ** 3)

    return {
        "used_gb": round(used_gb, 1),
        "total_gb": round(total_gb, 1),
        "percent": round(used_gb / total_gb * 100, 1),
    }


def get_network_stats():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        # UDP connect() doesn't send anything on the wire — it just asks the
        # kernel to pick the local address that would be used to reach
        # 8.8.8.8, which is this Pi's real outbound IP.
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        ip = None
    finally:
        sock.close()

    return {"ip": ip}


def get_uptime_seconds():
    return round(time.time() - psutil.boot_time())


def get_hud_stats():
    return {
        "weather": get_weather_stats(),
        "cpu": get_cpu_stats(),
        "disk": get_disk_stats(),
        "network": get_network_stats(),
        "uptime_seconds": get_uptime_seconds(),
        "gaming_pc": pc_stats.get_gaming_pc_stats(),
    }
