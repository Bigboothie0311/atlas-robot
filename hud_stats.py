import shutil
import socket
import time

import psutil
import requests

from ai_tools import WEATHER_CODE_DESCRIPTIONS
import pc_stats

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# The robot lives in one physical place — read from config/robot.env
# (gitignored) so the real coordinates never land in tracked source.
import robot_config

HOME_LATITUDE, HOME_LONGITUDE, HOME_CITY = robot_config.home_location()

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
        "city": HOME_CITY,
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


ATLAS_HUB_URL = "http://127.0.0.1:5050"
PRINTER_CACHE_SECONDS = 15

_printer_cache = {"data": None, "fetched_at": 0.0}


def _parse_printer_status(raw_status):
    """Parses atlas-hub's printer_status text into a structured dict. The
    raw format is the same one listen_and_answer.summarize_printer_status
    reads for speech (AD5X ONLINE/OFFLINE, STATE:, PROGRESS cur/total,
    Layer:)."""
    lines = [line.strip() for line in raw_status.splitlines() if line.strip()]

    if not any(line.startswith("AD5X ONLINE") for line in lines):
        return {"online": False}

    stats = {"online": True, "state": None, "progress_percent": None, "layer": None}

    state_line = next((line for line in lines if line.startswith("STATE:")), "")

    if state_line:
        state = state_line.split(":", 1)[1].strip().lower()
        stats["state"] = state or None

    progress_line = next((line for line in lines if line.startswith("PROGRESS")), "")

    if "/" in progress_line:
        try:
            current_value, total_value = (
                progress_line.replace("PROGRESS", "").strip().split("/", 1)
            )
            total = float(total_value.strip())

            if total > 0:
                stats["progress_percent"] = round(
                    float(current_value.strip()) * 100 / total
                )
        except (ValueError, ZeroDivisionError):
            pass

    layer_line = next((line for line in lines if line.startswith("Layer:")), "")

    if layer_line and "unknown" not in layer_line.lower():
        stats["layer"] = layer_line.split(":", 1)[1].strip()

    return stats


def get_printer_stats():
    """Structured 3D-printer status via the local atlas-hub service.
    Offline-tolerant and cached — atlas-hub being down or absent just
    reads as an offline printer, never an error."""
    now = time.time()

    if (
        _printer_cache["data"] is not None
        and now - _printer_cache["fetched_at"] < PRINTER_CACHE_SECONDS
    ):
        return _printer_cache["data"]

    try:
        response = requests.get(
            f"{ATLAS_HUB_URL}/atlas",
            params={"cmd": "printer_status"},
            timeout=3,
        )
        response.raise_for_status()
        data = _parse_printer_status(response.text)
    except requests.RequestException:
        data = {"online": False}

    _printer_cache["data"] = data
    _printer_cache["fetched_at"] = now
    return data


HEADLINES_CACHE_SECONDS = 30 * 60

_headlines_cache = {"data": [], "fetched_at": 0.0}


def get_headlines(max_results=6, allow_fetch=True):
    """Cached top news headlines (free, via ddgs). Returns [] on failure —
    the ticker just stays hidden. Pass allow_fetch=False from latency-
    sensitive callers (the /hud/stats route) so a cold cache never blocks
    them on a network fetch; robot_hub refreshes the cache on a timer."""
    import web_search

    now = time.time()

    if (
        _headlines_cache["data"]
        and now - _headlines_cache["fetched_at"] < HEADLINES_CACHE_SECONDS
    ):
        return _headlines_cache["data"]

    if not allow_fetch:
        return _headlines_cache["data"]

    headlines = web_search.search_news(max_results=max_results)

    if headlines:
        _headlines_cache["data"] = headlines
        _headlines_cache["fetched_at"] = now

    return _headlines_cache["data"]


def get_cpu_stats():
    percent = psutil.cpu_percent(interval=0.1)

    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as temp_file:
            temp_c = round(int(temp_file.read().strip()) / 1000, 1)
    except (OSError, ValueError):
        temp_c = None

    return {"percent": round(percent, 1), "temp_c": temp_c}


def get_memory_stats():
    memory = psutil.virtual_memory()
    return {"percent": round(memory.percent, 1)}


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
        "memory": get_memory_stats(),
        "disk": get_disk_stats(),
        "network": get_network_stats(),
        "uptime_seconds": get_uptime_seconds(),
        "gaming_pc": pc_stats.get_gaming_pc_stats(),
        "station_name": robot_config.get("STATION_NAME", "STATION-01"),
    }
