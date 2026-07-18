"""Authoritative capability registry for A.T.L.A.S.

The single source of truth for every command A.T.L.A.S. is actually
allowed to run. It powers "what can you control?" and the command
simulator, and it is the reference for keeping the model from claiming
abilities that aren't implemented.

Each entry:
  id            stable identifier
  name          short spoken name
  description   what it actually does
  aliases       representative trigger phrases (not exhaustive)
  confirm       True if the action asks for confirmation first
  requires      "none" | "pc" | "phone"  (extra hardware/link needed)
  category      grouping for the spoken overview

Keep this in sync when adding or removing a command. Nothing here
executes anything — it only describes.
"""

REGISTRY = [
    # --- Everyday local (zero-token) --------------------------------
    {"id": "time", "name": "time & date", "description": "tells the time, date, or its uptime",
     "aliases": ["what time is it", "what's the date"], "confirm": False, "requires": "none", "category": "everyday"},
    {"id": "timer", "name": "timers", "description": "sets, cancels, or checks a countdown timer with a chime",
     "aliases": ["set a timer for 5 minutes", "cancel the timer", "how long left on the timer"], "confirm": False, "requires": "none", "category": "everyday"},
    {"id": "reminder", "name": "reminders", "description": "schedules a spoken reminder",
     "aliases": ["remind me in 20 minutes to..."], "confirm": False, "requires": "none", "category": "everyday"},
    {"id": "focus", "name": "focus mode", "description": "dims the HUD and mutes nudges for a focus session",
     "aliases": ["focus mode for 25 minutes", "end focus mode"], "confirm": False, "requires": "none", "category": "everyday"},
    {"id": "notes", "name": "notes", "description": "takes, reads back, or clears notes",
     "aliases": ["take a note...", "read my notes", "clear my notes"], "confirm": False, "requires": "none", "category": "everyday"},
    {"id": "goodbye", "name": "arrival & goodbye", "description": "greets you when your phone returns; 'I'm leaving' shuts down the PC and darkens the HUD",
     "aliases": ["I'm leaving", "goodbye atlas"], "confirm": False, "requires": "none", "category": "everyday"},

    # --- Memory & planning ------------------------------------------
    {"id": "memory", "name": "memory", "description": "remembers or forgets facts and preferences you tell it to",
     "aliases": ["remember that...", "what do you remember about...", "forget that..."], "confirm": False, "requires": "none", "category": "memory"},
    {"id": "chief_of_staff", "name": "planning", "description": "summarizes what you have coming up this week",
     "aliases": ["what am I forgetting this week", "what's on my plate"], "confirm": False, "requires": "none", "category": "memory"},

    # --- Information ------------------------------------------------
    {"id": "briefing", "name": "briefings", "description": "gives a morning rundown of weather, reminders, and headlines",
     "aliases": ["morning briefing", "brief me"], "confirm": False, "requires": "none", "category": "information"},
    {"id": "news", "name": "news", "description": "reads the top news headlines",
     "aliases": ["what's in the news"], "confirm": False, "requires": "none", "category": "information"},
    {"id": "sky_watch", "name": "sky watch", "description": "reports meteor showers, launches, moon phase, and stargazing conditions",
     "aliases": ["sky watch", "what's up in the sky", "next meteor shower"], "confirm": False, "requires": "none", "category": "information"},
    {"id": "weather", "name": "weather", "description": "answers weather questions for home or another city",
     "aliases": ["what's the weather", "will it rain tomorrow"], "confirm": False, "requires": "none", "category": "information"},

    # --- Network & security ----------------------------------------
    {"id": "network_devices", "name": "network devices", "description": "lists the devices on your network",
     "aliases": ["what's on my network", "list the devices on my network"], "confirm": False, "requires": "none", "category": "security"},
    {"id": "secure_network", "name": "network audit", "description": "audits the network for unknown devices and exposed services",
     "aliases": ["secure my network"], "confirm": False, "requires": "none", "category": "security"},
    {"id": "enroll_face", "name": "face enrollment", "description": "learns your face as the authorized user",
     "aliases": ["learn my face"], "confirm": False, "requires": "none", "category": "security"},
    {"id": "intruder_review", "name": "intruder alerts", "description": "reviews unauthorized-user captures with photos and what they tried",
     "aliases": ["do I have any intruder alerts", "were there any unauthorized users"], "confirm": False, "requires": "none", "category": "security"},
    {"id": "camera_gate", "name": "camera gate", "description": "turns face verification on or off",
     "aliases": ["camera gate on", "camera gate off"], "confirm": False, "requires": "none", "category": "security"},

    # --- Diagnostics & self ----------------------------------------
    {"id": "diagnostics", "name": "diagnostics", "description": "runs a self-check of services, sensors, and budget",
     "aliases": ["run diagnostics", "system check"], "confirm": False, "requires": "none", "category": "diagnostics"},
    {"id": "connections", "name": "connection health", "description": "checks Wi-Fi, the PC link, the companion, and Tailscale",
     "aliases": ["check connections", "is everything connected"], "confirm": False, "requires": "none", "category": "diagnostics"},
    {"id": "log_query", "name": "history", "description": "answers what went wrong or recent errors from its logs",
     "aliases": ["what went wrong", "any recent errors"], "confirm": False, "requires": "none", "category": "diagnostics"},
    {"id": "system_health", "name": "system healing", "description": "diagnoses and safely repairs the Pi, then reports",
     "aliases": ["get the whole system healthy"], "confirm": False, "requires": "none", "category": "diagnostics"},
    {"id": "internet_check", "name": "internet check", "description": "measures internet latency and packet loss",
     "aliases": ["how's the internet"], "confirm": False, "requires": "none", "category": "diagnostics"},
    {"id": "what_heard", "name": "hearing check", "description": "reports the last thing it transcribed",
     "aliases": ["what did you hear"], "confirm": False, "requires": "none", "category": "diagnostics"},
    {"id": "capabilities", "name": "capabilities", "description": "lists what it can actually control",
     "aliases": ["what can you control", "what can you do"], "confirm": False, "requires": "none", "category": "diagnostics"},
    {"id": "simulate", "name": "command simulator", "description": "explains what it would do for a phrase without doing it",
     "aliases": ["what would happen if I said..."], "confirm": False, "requires": "none", "category": "diagnostics"},

    # --- HUD --------------------------------------------------------
    {"id": "screen_dark", "name": "screen dimming", "description": "dims the HUD to near-black or restores it",
     "aliases": ["go dark", "lights out", "lights up"], "confirm": False, "requires": "none", "category": "hud"},
    {"id": "stand_down", "name": "alert acknowledge", "description": "clears a red alert",
     "aliases": ["stand down", "all clear"], "confirm": False, "requires": "none", "category": "hud"},

    # --- PC power (direct Ethernet link) ----------------------------
    {"id": "wake_pc", "name": "wake PC", "description": "wakes the PC over the direct Ethernet link",
     "aliases": ["boot my PC", "wake my PC"], "confirm": False, "requires": "pc", "category": "pc"},
    {"id": "shutdown_pc", "name": "shut down PC", "description": "shuts down the PC through the companion",
     "aliases": ["shut down my PC", "cancel PC shutdown"], "confirm": True, "requires": "pc", "category": "pc"},

    # --- PC apps & control (companion) ------------------------------
    {"id": "open_fusion", "name": "Fusion 360", "description": "launches Fusion 360 on the PC",
     "aliases": ["open Fusion"], "confirm": False, "requires": "pc", "category": "pc"},
    {"id": "open_spotify", "name": "Spotify", "description": "opens Spotify on the PC",
     "aliases": ["open Spotify"], "confirm": False, "requires": "pc", "category": "pc"},
    {"id": "open_claude", "name": "Claude", "description": "opens Claude on the PC",
     "aliases": ["open Claude"], "confirm": False, "requires": "pc", "category": "pc"},
    {"id": "youtube", "name": "YouTube search", "description": "searches YouTube on the PC and full-screens it",
     "aliases": ["find me videos showing how to..."], "confirm": False, "requires": "pc", "category": "pc"},
    {"id": "volume_media", "name": "volume & media", "description": "controls PC volume and media playback",
     "aliases": ["volume up", "mute", "play", "pause", "next track"], "confirm": False, "requires": "pc", "category": "pc"},
    {"id": "pc_screenshot", "name": "PC screenshot", "description": "captures the PC screen onto the HUD",
     "aliases": ["show me my PC screen", "show me the newest screenshot"], "confirm": False, "requires": "pc", "category": "pc"},
    {"id": "pc_apps", "name": "PC windows", "description": "lists what's open on the PC",
     "aliases": ["what's open on my PC"], "confirm": False, "requires": "pc", "category": "pc"},
    {"id": "pc_health", "name": "PC health", "description": "reports PC CPU, RAM, disk, and uptime",
     "aliases": ["how's my PC"], "confirm": False, "requires": "pc", "category": "pc"},
    {"id": "empty_recycle_bin", "name": "empty Recycle Bin", "description": "empties the PC Recycle Bin",
     "aliases": ["empty the recycle bin"], "confirm": True, "requires": "pc", "category": "pc"},
    {"id": "pc_profiles", "name": "app profiles", "description": "opens a set of apps and sets volume/focus for work, design, or game mode",
     "aliases": ["work mode", "design mode", "game mode"], "confirm": False, "requires": "pc", "category": "pc"},

    # --- Printer (status only; no new printer features) -------------
    {"id": "print_status", "name": "print status", "description": "reports 3D print progress and ETA",
     "aliases": ["how long left on the print"], "confirm": False, "requires": "none", "category": "printer"},

    # --- Emergency --------------------------------------------------
    {"id": "emergency_shutdown", "name": "emergency shutdown", "description": "runs the safe emergency shutdown sequence",
     "aliases": ["initiate emergency shutdown", "cancel shutdown"], "confirm": True, "requires": "none", "category": "emergency"},

    # --- Phone ------------------------------------------------------
    {"id": "phone_link", "name": "phone access", "description": "answers questions and shows away-mode events from your phone",
     "aliases": ["(from the phone app over Tailscale)"], "confirm": False, "requires": "phone", "category": "phone"},
]

CATEGORY_ORDER = [
    ("everyday", "everyday things"),
    ("memory", "memory and planning"),
    ("information", "information"),
    ("security", "network and security"),
    ("diagnostics", "diagnostics"),
    ("pc", "your PC"),
    ("hud", "the display"),
    ("printer", "the 3D printer"),
    ("emergency", "emergencies"),
    ("phone", "phone access"),
]


def all_capabilities():
    return list(REGISTRY)


def by_category():
    grouped = {}
    for entry in REGISTRY:
        grouped.setdefault(entry["category"], []).append(entry)
    return grouped


def describe_all():
    """Spoken answer for 'what can you control?' — grouped, honest, and
    flags which groups need the PC or phone."""
    grouped = by_category()
    parts = ["Here's what I can actually control"]

    for key, label in CATEGORY_ORDER:
        entries = grouped.get(key)
        if not entries:
            continue

        names = ", ".join(e["name"] for e in entries)
        suffix = ""
        if all(e["requires"] == "pc" for e in entries):
            suffix = " — these need your PC"
        elif key == "phone":
            suffix = " — from your phone over Tailscale"

        parts.append(f"For {label}: {names}{suffix}")

    parts.append(
        "If you ask for something not on this list, I'll tell you I can't "
        "do it rather than pretend"
    )
    return ". ".join(parts) + "."


def find_by_alias(normalized_text):
    """Best-effort match of a phrase to a capability, for the command
    simulator. Returns the entry or None."""
    for entry in REGISTRY:
        for alias in entry["aliases"]:
            core = alias.lower().replace("...", "").replace("(", "").replace(")", "").strip()
            if core and (core in normalized_text or normalized_text in core):
                return entry
        if entry["id"].replace("_", " ") in normalized_text:
            return entry
    return None


def instruction_summary():
    """A compact list injected into the model's system prompt so it won't
    claim unimplemented abilities. Names + PC/phone requirement only."""
    lines = []
    for entry in REGISTRY:
        req = "" if entry["requires"] == "none" else f" (needs {entry['requires']})"
        lines.append(f"- {entry['name']}: {entry['description']}{req}")
    return "\n".join(lines)
