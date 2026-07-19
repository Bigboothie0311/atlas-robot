"""Local unit conversion — temperature, distance, and weight. No API call,
no tokens; pure arithmetic, so it belongs alongside the other deterministic
local commands in listen_and_answer.py.
"""
import re


# Every alias maps to a canonical unit key. Keys within the same category
# convert through that category's base unit.
UNIT_ALIASES = {
    # temperature
    "f": "f", "fahrenheit": "f", "degrees fahrenheit": "f",
    "c": "c", "celsius": "c", "degrees celsius": "c",
    # distance (base: meters)
    "mi": "mi", "mile": "mi", "miles": "mi",
    "km": "km", "kilometer": "km", "kilometers": "km", "kilometre": "km", "kilometres": "km",
    "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "ft": "ft", "foot": "ft", "feet": "ft",
    # weight (base: grams)
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg",
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "g": "g", "gram": "g", "grams": "g",
}

TEMPERATURE_UNITS = {"f", "c"}
# Factor to convert 1 of this unit into the category's base unit.
DISTANCE_TO_METERS = {"mi": 1609.344, "km": 1000.0, "m": 1.0, "ft": 0.3048}
WEIGHT_TO_GRAMS = {"lb": 453.59237, "kg": 1000.0, "oz": 28.349523125, "g": 1.0}

UNIT_SPOKEN_NAMES = {
    "f": "degrees Fahrenheit", "c": "degrees Celsius",
    "mi": "miles", "km": "kilometers", "m": "meters", "ft": "feet",
    "lb": "pounds", "kg": "kilograms", "oz": "ounces", "g": "grams",
}

_NUMBER = r"(\d+\.?\d*)"
_UNIT = r"([a-z]+(?: [a-z]+)?)"

CONVERT_PATTERN = re.compile(rf"^convert {_NUMBER} {_UNIT} to {_UNIT}$")
WHAT_IS_PATTERN = re.compile(rf"^(?:what is|what's|whats) {_NUMBER} {_UNIT} in {_UNIT}$")
HOW_MANY_PATTERN = re.compile(rf"^how many {_UNIT} (?:is|are) {_NUMBER} {_UNIT}$")


def _resolve_unit(raw):
    return UNIT_ALIASES.get(raw.strip().lower())


def parse_conversion_command(normalized_text):
    """Returns (value, from_unit, to_unit) as canonical unit keys, or None
    if the text isn't a recognized conversion request or uses units this
    module doesn't know."""
    match = CONVERT_PATTERN.match(normalized_text) or WHAT_IS_PATTERN.match(normalized_text)

    if match:
        value, from_raw, to_raw = match.groups()
    else:
        match = HOW_MANY_PATTERN.match(normalized_text)

        if not match:
            return None

        to_raw, value, from_raw = match.groups()

    from_unit = _resolve_unit(from_raw)
    to_unit = _resolve_unit(to_raw)

    if from_unit is None or to_unit is None:
        return None

    return float(value), from_unit, to_unit


def convert(value, from_unit, to_unit):
    """Returns the converted value, or None if the units aren't in the
    same category (e.g. converting pounds to miles)."""
    if from_unit == to_unit:
        return value

    if from_unit in TEMPERATURE_UNITS and to_unit in TEMPERATURE_UNITS:
        if from_unit == "f":
            return (value - 32) * 5.0 / 9.0
        return value * 9.0 / 5.0 + 32

    if from_unit in DISTANCE_TO_METERS and to_unit in DISTANCE_TO_METERS:
        meters = value * DISTANCE_TO_METERS[from_unit]
        return meters / DISTANCE_TO_METERS[to_unit]

    if from_unit in WEIGHT_TO_GRAMS and to_unit in WEIGHT_TO_GRAMS:
        grams = value * WEIGHT_TO_GRAMS[from_unit]
        return grams / WEIGHT_TO_GRAMS[to_unit]

    return None


def run_conversion_command(value, from_unit, to_unit):
    result = convert(value, from_unit, to_unit)

    if result is None:
        return "Those units aren't compatible, so I can't convert between them."

    from_name = UNIT_SPOKEN_NAMES.get(from_unit, from_unit)
    to_name = UNIT_SPOKEN_NAMES.get(to_unit, to_unit)

    return f"{_format_number(value)} {from_name} is {_format_number(result)} {to_name}."


def _format_number(number):
    rounded = round(number, 2)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)
