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
]


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


def run_tool_call(name, arguments):
    if name == "get_weather":
        return get_weather(arguments.get("location"), arguments.get("day"))

    return f"Unknown tool: {name}"
