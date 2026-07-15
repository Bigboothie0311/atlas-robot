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

TOOLS = [
    {
        "type": "function",
        "name": "get_weather",
        "description": (
            "Get the current weather conditions and temperature for a "
            "specific city or location."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": (
                        "City name, optionally with state or country for "
                        "disambiguation, e.g. 'Denver, Colorado' or "
                        "'Paris, France'."
                    ),
                }
            },
            "required": ["location"],
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


def get_weather(location):
    # Open-Meteo's geocoding search does not handle "City, US-State"
    # (e.g. "Denver, Colorado" returns nothing, though "Denver" alone and
    # "Paris, France" both work). Fall back to the text before the first
    # comma when the full string comes up empty.
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

    try:
        forecast_response = requests.get(
            FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,weather_code",
                "temperature_unit": "fahrenheit",
            },
            timeout=8,
        )
        forecast_response.raise_for_status()
    except requests.RequestException:
        return "I could not check the weather right now."

    current = forecast_response.json().get("current", {})
    temperature = current.get("temperature_2m")
    code = current.get("weather_code")

    if temperature is None or code is None:
        return "I could not check the weather right now."

    condition = WEATHER_CODE_DESCRIPTIONS.get(code, "unknown conditions")
    place_name = place.get("name", location)

    return (
        f"It is currently {round(temperature)} degrees Fahrenheit "
        f"and {condition} in {place_name}."
    )


def run_tool_call(name, arguments):
    if name == "get_weather":
        return get_weather(arguments.get("location", ""))

    return f"Unknown tool: {name}"
