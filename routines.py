"""Arrival and goodbye routines.

Arrival: when the owner's phone rejoins the LAN after being away, greet
them with pending reminders and a quick status (built here, spoken by the
network sentinel that detects the return).

Goodbye: "I'm leaving" shuts down the PC and darkens the HUD.

Both are local; the arrival greeting is zero-token.
"""
import memory_store


def arrival_greeting(owner_name="friend"):
    """Spoken greeting for a returning phone. Includes pending reminders
    and note count so nothing is missed."""
    parts = [f"Welcome home, {owner_name}"]

    reminders = memory_store.load_reminders()
    if reminders:
        count = len(reminders)
        parts.append(f"you have {count} reminder{'s' if count != 1 else ''} still pending")

    priorities = memory_store.get_priorities_summary()
    if priorities:
        parts.append(f"top of your list: {priorities[0]}")

    notes = memory_store.load_notes()
    if notes:
        parts.append(f"and {len(notes)} note{'s' if len(notes) != 1 else ''} saved")

    return ". ".join(parts) + "."
