"""'How many days until X' — local date arithmetic, no API call. Supports
a handful of named holidays plus any 'month day' phrase.

`now` is accepted as an optional override on every public function so tests
can pin the current date instead of depending on the real clock.
"""
import re
from datetime import date, datetime


COUNTDOWN_PATTERN = re.compile(
    r"^(?:how many days(?: is it| are there)? until|how long until|days until) (.+)$"
)

NAMED_DATES = {
    "christmas": (12, 25),
    "christmas day": (12, 25),
    "new year's day": (1, 1),
    "new years day": (1, 1),
    "new year's": (1, 1),
    "new years": (1, 1),
    "halloween": (10, 31),
    "valentine's day": (2, 14),
    "valentines day": (2, 14),
    "independence day": (7, 4),
    "the fourth of july": (7, 4),
    "fourth of july": (7, 4),
}

MONTH_NAMES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

MONTH_DAY_PATTERN = re.compile(r"^([a-z]+) (\d{1,2})(?:st|nd|rd|th)?$")


def parse_countdown_target(normalized_text):
    """Returns the free-text target of a 'how many days until X' request,
    or None if the text isn't shaped like one."""
    match = COUNTDOWN_PATTERN.match(normalized_text)
    return match.group(1).strip() if match else None


def resolve_month_day(target_text):
    """Returns (month, day) for a recognized holiday name or 'month day'
    phrase, otherwise None."""
    target_text = target_text.strip().lower()

    if target_text in NAMED_DATES:
        return NAMED_DATES[target_text]

    match = MONTH_DAY_PATTERN.match(target_text)

    if not match:
        return None

    month_name, day_text = match.groups()
    month = MONTH_NAMES.get(month_name)

    if month is None:
        return None

    try:
        day = int(day_text)
        date(2000, month, day)  # validates day is in range for that month
    except ValueError:
        return None

    return month, day


def days_until(month, day, now=None):
    """Days from today until the next occurrence of month/day (today
    counts as 0, and a date already passed this year rolls to next year)."""
    today = (now or datetime.now()).date()

    try:
        target = date(today.year, month, day)
    except ValueError:
        target = date(today.year, month, day - 1)  # Feb 29 in a non-leap year

    if target < today:
        try:
            target = date(today.year + 1, month, day)
        except ValueError:
            target = date(today.year + 1, month, day - 1)

    return (target - today).days


def build_countdown_answer(target_text, now=None):
    """Returns a spoken answer for a resolved countdown target, or None if
    the target date couldn't be understood."""
    resolved = resolve_month_day(target_text)

    if resolved is None:
        return None

    month, day = resolved
    remaining = days_until(month, day, now)

    if remaining == 0:
        return f"{target_text.title()} is today."

    if remaining == 1:
        return f"{target_text.title()} is tomorrow."

    return f"There are {remaining} days until {target_text}."
