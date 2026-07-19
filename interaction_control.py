"""Small, dependency-free helpers for harmless interaction cancellation."""


SAFE_CANCEL_PHRASES = frozenset({
    "stop",
    "cancel",
    "never mind",
    "nevermind",
    "stop listening",
    "dismiss",
    "close the hud",
    "close hud",
    "close that",
    "go idle",
})


def is_safe_cancel_phrase(normalized_phrase):
    """True only for exact, harmless cancel/dismiss commands.

    Exact matching is deliberate: phrases such as "cancel shutdown" and
    "stop the printer" have separate, safety-sensitive handlers.
    """
    return normalized_phrase in SAFE_CANCEL_PHRASES
