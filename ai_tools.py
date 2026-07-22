import atexit
import threading
from collections.abc import Callable
from typing import Any

import requests


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODE_DESCRIPTIONS = {
    0: "clear sky",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "foggy with frost",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    80: "light rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    95: "thunderstorms",
    96: "thunderstorms with light hail",
    99: "thunderstorms with heavy hail",
}

# The robot's own physical location, used as the default so the model
# never has to ask where the user is just to answer "what's the weather".
# Read from config/robot.env (gitignored) so real coordinates stay out of
# tracked source — see robot_config.py.
import robot_config

HOME_LATITUDE, HOME_LONGITUDE, _HOME_CITY = robot_config.home_location()
HOME_LOCATION_NAME = "home"

TOOLS = [
    {
        "type": "function",
        "name": "get_weather",
        "description": (
            "Get the weather forecast for today or tomorrow, for a "
            "specific city or the user's home location."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": ["string", "null"],
                    "description": (
                        "City name, optionally with state or country for "
                        "disambiguation, e.g. 'Denver, Colorado' or "
                        "'Paris, France'. Omit (pass null) to use the "
                        "user's home location — always do this unless the "
                        "user names a different place."
                    ),
                },
                "day": {
                    "type": ["string", "null"],
                    "enum": ["today", "tomorrow", None],
                    "description": (
                        "Which day's forecast to get. Defaults to today "
                        "if omitted."
                    ),
                },
            },
            "required": ["location", "day"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {"type": "web_search"},
    {
        "type": "function",
        "name": "run_atlas_agent",
        "description": (
            "Use Atlas's autonomous runtime for a concrete owner-requested "
            "action that requires real tools, multi-step execution, and "
            "verification -- such as finding or copying a file on the PC, "
            "checking visible PC apps, opening an approved app, recording "
            "a narrated self-showcase video of Atlas's own HUD screen "
            "('record a video of yourself', 'make a promo video', 'make "
            "an Instagram reel', ...), or publishing a finished video to "
            "Instagram. This is the ONLY path to those recording/"
            "publishing capabilities -- do not answer them with "
            "run_atlas_diagnostic_or_repair, which cannot record or "
            "publish anything. After a recording finishes, the result "
            "will already say whether the owner wants it published and, "
            "if so, will name the exact saved file -- if the owner "
            "later says something like 'post it' or 'yes, publish that' "
            "in direct reply, use respond_to_pending_confirmation "
            "instead of calling run_atlas_agent again with a new goal; "
            "a brand new goal has no way to know which exact file was "
            "just recorded. Do not use this for ordinary questions, "
            "explanations, weather, or Atlas diagnostics."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "The owner's complete concrete goal, preserving "
                        "important filenames, locations, and constraints."
                    ),
                },
            },
            "required": ["goal"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_atlas_diagnostic_or_repair",
        "description": (
            "Runs one of Atlas's own real local diagnostic, health-check, "
            "or self-repair capabilities and returns what actually "
            "happened. Use this whenever the user asks Atlas to check its "
            "health, run diagnostics, heal or repair itself, check its "
            "connections, check storage, review recent errors, check its "
            "tool versions, or list what it can do — these are real "
            "capabilities available here, in every conversation, so call "
            "this instead of saying you don't have access to them. Not "
            "for recording or publishing a video, even though that's "
            "also a real capability -- use run_atlas_agent for that."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "capability": {
                    "type": "string",
                    "enum": [
                        "diagnostics", "self_heal", "system_health", "connections",
                        "status_report", "storage", "log_query", "internet_check",
                        "capabilities", "tool_status",
                    ],
                    "description": "Which capability to run.",
                },
            },
            "required": ["capability"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "respond_to_pending_confirmation",
        "description": (
            "Use this ONLY when Atlas's own previous message just asked "
            "you to confirm or cancel a specific pending action (for "
            "example: after recording a self-showcase Reel, it will ask "
            "whether to post, save, or delete it) and the owner's reply "
            "is a direct answer to that. Use action='post' for 'post it' "
            "or 'go ahead'; action='save' for 'save it' or 'keep it'; and "
            "action='delete' for 'delete it' or 'throw it away'. Post and "
            "save both require a verified Windows Desktop copy. This "
            "resumes the exact pending action (e.g. publishing the "
            "exact Reel just recorded); it does not start anything new "
            "and does not need a goal. Do not use this for a fresh, "
            "unrelated request -- use run_atlas_agent for that instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["post", "save", "delete"],
                    "description": (
                        "Exactly how to resolve the finished Reel."
                    ),
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


_AGENT_RUNTIME_OWNER_FACTORY: Callable[[], Any] | None = None
_AGENT_RUNTIME_OWNER: Any | None = None
_AGENT_RUNTIME_LOCK = threading.RLock()
_AGENT_USAGE_LOCAL = threading.local()


def configure_agent_runtime_owner_factory(
    factory: Callable[[], Any],
) -> None:
    """Configure lazy owner construction without creating the runtime."""
    if not callable(factory):
        raise TypeError("factory must be callable")

    global _AGENT_RUNTIME_OWNER_FACTORY
    global _AGENT_RUNTIME_OWNER

    with _AGENT_RUNTIME_LOCK:
        previous_owner = _AGENT_RUNTIME_OWNER
        _AGENT_RUNTIME_OWNER = None
        _AGENT_RUNTIME_OWNER_FACTORY = factory

    if previous_owner is not None:
        previous_owner.close()


def _get_agent_runtime_owner():
    global _AGENT_RUNTIME_OWNER

    with _AGENT_RUNTIME_LOCK:
        if _AGENT_RUNTIME_OWNER_FACTORY is None:
            raise RuntimeError(
                "Atlas agent runtime has not been configured."
            )

        if _AGENT_RUNTIME_OWNER is None:
            _AGENT_RUNTIME_OWNER = (
                _AGENT_RUNTIME_OWNER_FACTORY()
            )

        return _AGENT_RUNTIME_OWNER


def close_agent_runtime_owner() -> None:
    """Close an initialized owner without creating one during shutdown."""
    global _AGENT_RUNTIME_OWNER

    with _AGENT_RUNTIME_LOCK:
        owner = _AGENT_RUNTIME_OWNER
        _AGENT_RUNTIME_OWNER = None

    if owner is not None:
        owner.close()


def run_agent_goal(goal: str, *, source: str = "proactive"):
    """Public shared-runtime entry point for background autonomous goals."""
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("goal must be a non-empty string")
    return _get_agent_runtime_owner().handle_goal(
        goal.strip(), source=source
    )


def clear_agent_usage() -> None:
    _AGENT_USAGE_LOCAL.input_tokens = 0
    _AGENT_USAGE_LOCAL.output_tokens = 0


def _record_agent_usage(
    input_tokens: int,
    output_tokens: int,
) -> None:
    current_input = int(
        getattr(_AGENT_USAGE_LOCAL, "input_tokens", 0) or 0
    )
    current_output = int(
        getattr(_AGENT_USAGE_LOCAL, "output_tokens", 0) or 0
    )

    _AGENT_USAGE_LOCAL.input_tokens = (
        current_input + max(0, int(input_tokens or 0))
    )
    _AGENT_USAGE_LOCAL.output_tokens = (
        current_output + max(0, int(output_tokens or 0))
    )


def consume_agent_usage() -> tuple[int, int]:
    input_tokens = int(
        getattr(_AGENT_USAGE_LOCAL, "input_tokens", 0) or 0
    )
    output_tokens = int(
        getattr(_AGENT_USAGE_LOCAL, "output_tokens", 0) or 0
    )
    clear_agent_usage()
    return input_tokens, output_tokens


atexit.register(close_agent_runtime_owner)


def geocode(location):
    try:
        geo_response = requests.get(
            GEOCODING_URL,
            params={"name": location, "count": 1},
            timeout=8,
        )
        geo_response.raise_for_status()
    except requests.RequestException:
        return None

    # Open-Meteo omits the "results" key entirely (not an empty list) when
    # there are zero matches, so normalize that to [] here. None is
    # reserved for actual transport failures, checked above.
    return geo_response.json().get("results") or []


def get_weather(location, day):
    day = (day or "today").strip().lower()

    if location:
        # Open-Meteo's geocoding search does not handle "City, US-State"
        # (e.g. "Denver, Colorado" returns nothing, though "Denver" alone
        # and "Paris, France" both work). Fall back to the text before the
        # first comma when the full string comes up empty.
        results = geocode(location)

        if results is None:
            return "I could not check the weather right now."

        if not results and "," in location:
            results = geocode(location.split(",", 1)[0].strip())

            if results is None:
                return "I could not check the weather right now."

        if not results:
            return f"I could not find a location called {location}."

        place = results[0]
        latitude, longitude = place["latitude"], place["longitude"]
        place_name = place.get("name", location)
    else:
        latitude, longitude = HOME_LATITUDE, HOME_LONGITUDE
        place_name = HOME_LOCATION_NAME

    try:
        forecast_response = requests.get(
            FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,weather_code",
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max"
                ),
                "temperature_unit": "fahrenheit",
                "forecast_days": 2,
            },
            timeout=8,
        )
        forecast_response.raise_for_status()
    except requests.RequestException:
        return "I could not check the weather right now."

    payload = forecast_response.json()
    location_phrase = "" if place_name == HOME_LOCATION_NAME else f" in {place_name}"

    if day == "tomorrow":
        daily = payload.get("daily", {})
        codes = daily.get("weather_code") or []
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        precip = daily.get("precipitation_probability_max") or []

        if len(codes) < 2 or len(highs) < 2 or len(lows) < 2:
            return "I could not check tomorrow's weather right now."

        condition = WEATHER_CODE_DESCRIPTIONS.get(codes[1], "unknown conditions")
        rain_chance = precip[1] if len(precip) > 1 else None
        rain_phrase = (
            f" There's a {round(rain_chance)} percent chance of rain."
            if rain_chance is not None else ""
        )

        return (
            f"Tomorrow{location_phrase} looks {condition}, with a high of "
            f"{round(highs[1])} and a low of {round(lows[1])} degrees "
            f"Fahrenheit.{rain_phrase}"
        )

    current = payload.get("current", {})
    temperature = current.get("temperature_2m")
    code = current.get("weather_code")

    if temperature is None or code is None:
        return "I could not check the weather right now."

    condition = WEATHER_CODE_DESCRIPTIONS.get(code, "unknown conditions")

    return (
        f"It is currently {round(temperature)} degrees Fahrenheit "
        f"and {condition}{location_phrase}."
    )


def run_tool_call(
    name,
    arguments,
    *,
    source="voice",
):
    if name == "get_weather":
        return get_weather(
            arguments.get("location"),
            arguments.get("day"),
        )

    if name == "run_atlas_agent":
        goal = arguments.get("goal")

        if not isinstance(goal, str) or not goal.strip():
            return (
                "I need a specific concrete goal before I can "
                "start an agent mission."
            )

        owner = _get_agent_runtime_owner()
        response = owner.handle_goal(
            goal.strip(),
            source=source,
        )
        _record_agent_usage(
            response.input_tokens,
            response.output_tokens,
        )
        return response.text

    if name == "run_atlas_diagnostic_or_repair":
        # Lazy import: listen_and_answer imports this module at load time,
        # so importing it back at module scope here would be circular.
        import listen_and_answer
        return listen_and_answer.run_diagnostic_capability(
            arguments.get("capability")
        )

    if name == "respond_to_pending_confirmation":
        owner = _get_agent_runtime_owner()
        response = owner.resolve_pending(action=str(arguments.get("action") or ""))
        _record_agent_usage(
            response.input_tokens,
            response.output_tokens,
        )
        return response.text

    return f"Unknown tool: {name}"
