"""Coin flip and dice roll — fully local, zero-token fun commands.

random_module is injectable so tests can pass a seeded `random.Random`
instance instead of patching the global `random` module.
"""
import random
import re


COIN_FLIP_PHRASES = {
    "flip a coin", "flip a coin for me", "toss a coin", "heads or tails",
}

DICE_ROLL_PATTERN = re.compile(
    r"^roll (?:(\d+) )?(?:a |an )?(?:die|dice|d(\d+))$"
)
DICE_WORD_PATTERN = re.compile(
    r"^roll (a|an|one|two|three|four|five|six) (?:die|dice)$"
)
NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3,
    "four": 4, "five": 5, "six": 6,
}

DEFAULT_SIDES = 6
MAX_DICE_COUNT = 6
MAX_DICE_SIDES = 1000


def is_coin_flip_command(normalized_text):
    return normalized_text in COIN_FLIP_PHRASES


def flip_coin(random_module=random):
    return random_module.choice(["heads", "tails"])


def run_coin_flip_command(random_module=random):
    return f"{flip_coin(random_module).capitalize()}."


def parse_dice_roll_command(normalized_text):
    """Returns (count, sides) for a 'roll a die' / 'roll two dice' /
    'roll a d20' / 'roll 2 d6' style request, otherwise None."""
    match = DICE_ROLL_PATTERN.match(normalized_text)

    if match:
        count = int(match.group(1)) if match.group(1) else 1
        sides = int(match.group(2)) if match.group(2) else DEFAULT_SIDES
        return _clamp_dice(count, sides)

    match = DICE_WORD_PATTERN.match(normalized_text)

    if match:
        count = NUMBER_WORDS[match.group(1)]
        return _clamp_dice(count, DEFAULT_SIDES)

    return None


def _clamp_dice(count, sides):
    count = max(1, min(MAX_DICE_COUNT, count))
    sides = max(2, min(MAX_DICE_SIDES, sides))
    return count, sides


def roll_dice(count, sides, random_module=random):
    return [random_module.randint(1, sides) for _ in range(count)]


def run_dice_roll_command(count, sides, random_module=random):
    rolls = roll_dice(count, sides, random_module)

    if count == 1:
        return f"You rolled a {rolls[0]}."

    total = sum(rolls)
    rolled = ", ".join(str(roll) for roll in rolls)
    return f"You rolled {rolled} — total {total}."
