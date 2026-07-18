import array
import json
import math
import queue
import re
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import requests
from openai import OpenAI
from vosk import Model, KaldiRecognizer

import ai_tools
import briefing
import camera_gate
import diagnostics
import hud_stats
import logbook
import pc_control
import memory_store
import web_search
import wake_detection


HUB = "http://127.0.0.1:5051"
ATLAS_HUB = "http://127.0.0.1:5050"
MODEL_PATH = "/home/atlas/atlas-robot/models/vosk-model-small-en-us-0.15"
AUDIO_PATH = "/tmp/atlas-listen.wav"
VISION_SCRIPT = "/home/atlas/atlas-robot/vision_test.py"
ENV_PATH = Path("/home/atlas/atlas-robot/config/openai.env")
ROBOT_ENV_PATH = Path("/home/atlas/atlas-robot/config/robot.env")
USAGE_PATH = Path("/home/atlas/atlas-robot/data/openai_usage.json")

DEFAULT_OWNER_NAME = "friend"

# Old USB webcam's built-in mic.
# MIC_DEVICE = "plughw:CARD=camera,DEV=0"
MIC_DEVICE = "plughw:CARD=Device,DEV=0"  # SuziePi USB mic

MODEL_NAME = "gpt-5.6-luna"
MONTHLY_LIMIT_USD = 8.00
NEXT_REQUEST_RESERVE_USD = 0.01

INPUT_PRICE_PER_TOKEN = 1.00 / 1_000_000
OUTPUT_PRICE_PER_TOKEN = 6.00 / 1_000_000


class BudgetExceeded(Exception):
    pass


def current_month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def load_api_key():
    if not ENV_PATH.exists():
        raise RuntimeError("OpenAI key file was not found.")

    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()

        if line.startswith("OPENAI_API_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")

            if key:
                return key

    raise RuntimeError("OPENAI_API_KEY was not found.")


def load_owner_name():
    if not ROBOT_ENV_PATH.exists():
        return DEFAULT_OWNER_NAME

    for line in ROBOT_ENV_PATH.read_text().splitlines():
        line = line.strip()

        if line.startswith("OWNER_NAME="):
            name = line.split("=", 1)[1].strip().strip('"').strip("'")

            if name:
                return name

    return DEFAULT_OWNER_NAME


def load_usage():
    month = current_month()

    if not USAGE_PATH.exists():
        return {
            "month": month,
            "spent_usd": 0.0,
            "requests": 0
        }

    try:
        data = json.loads(USAGE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        data = {}

    if data.get("month") != month:
        return {
            "month": month,
            "spent_usd": 0.0,
            "requests": 0
        }

    return {
        "month": month,
        "spent_usd": float(data.get("spent_usd", 0.0)),
        "requests": int(data.get("requests", 0))
    }


def save_usage(data):
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = USAGE_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(data, indent=2))
    temporary_path.replace(USAGE_PATH)


def set_face(expression):
    try:
        requests.post(
            f"{HUB}/face",
            json={"expression": expression},
            timeout=3
        ).raise_for_status()
    except requests.RequestException as error:
        print("Face update failed:", error)


def speak(text):
    response = requests.post(
        f"{HUB}/speak",
        json={"text": text},
        timeout=45
    )
    response.raise_for_status()


def listen_for_barge_in(model, stop_event):
    """Runs only during ask_and_speak_streaming's TTS window, when
    wake_listener.py's own mic loop has already released the device. Returns
    True if the wake phrase is verified before stop_event is set."""
    recorder = subprocess.Popen(
        [
            "arecord",
            "-D", MIC_DEVICE,
            "-t", "raw",
            "-f", "S16_LE",
            "-r", "16000",
            "-c", "1"
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )

    recognizer = wake_detection.create_recognizer(model)
    utterance_peak_rms = 0
    partial_hits = 0

    try:
        while not stop_event.is_set():
            audio_data = recorder.stdout.read(wake_detection.AUDIO_CHUNK_BYTES)

            if not audio_data:
                return False

            pre_check_peak = utterance_peak_rms
            pre_check_partial_hits = partial_hits
            accepted, utterance_peak_rms, partial_hits, candidate = (
                wake_detection.check_wake_phrase(
                    recognizer, audio_data, utterance_peak_rms, partial_hits
                )
            )

            if candidate is not None:
                text, confidence = candidate
                print(
                    "Barge-in candidate:", repr(text),
                    f"confidence={confidence:.2f}",
                    f"peak_rms={pre_check_peak}",
                    f"partial_hits={pre_check_partial_hits}",
                    flush=True
                )

            if accepted:
                print("Barge-in wake phrase detected.", flush=True)
                return True

        return False
    finally:
        wake_detection.stop_recorder(recorder)


def log_qa(question, answer):
    try:
        requests.post(
            f"{HUB}/qa_log",
            json={"question": question, "answer": answer},
            timeout=3
        )
    except requests.RequestException as error:
        print("qa_log request failed:", error, flush=True)


# Re-greet if it's been a while since the last interaction, rather than on
# every single wake-up.
GREETING_IDLE_THRESHOLD_SECONDS = 3 * 60 * 60
MORNING_HOUR_START = 4
MORNING_HOUR_END = 11

# Matches robot_hub.py's QUIET_HOURS_START/END and hud/app.js's — same
# overnight window gets a calmer voice here and a dimmer HUD there.
QUIET_HOURS_START = 23
QUIET_HOURS_END = 6


def _in_quiet_hours():
    hour = datetime.now().hour
    return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END


def maybe_speak_greeting():
    """Speaks a morning/return greeting if enough idle time has passed since
    the last interaction, before handing off to the normal 'Go ahead' flow."""
    gap = memory_store.get_last_interaction_gap_seconds()

    if gap is not None and gap < GREETING_IDLE_THRESHOLD_SECONDS:
        return

    weather = hud_stats.get_weather_stats()
    hour = datetime.now().hour
    owner_name = load_owner_name()

    is_morning = MORNING_HOUR_START <= hour < MORNING_HOUR_END

    # First morning interaction of the day gets the full rundown instead
    # of just a weather line — the briefing already covers weather, so the
    # greeting stays short to avoid saying it twice.
    if is_morning and not briefing.was_briefed_today():
        speak(f"Good morning, {owner_name}. Here's your rundown.")
        speak(briefing.build_briefing_text())
        briefing.mark_briefed_today()
        return

    if weather.get("temp_f") is None:
        if is_morning:
            greeting = f"Good morning, {owner_name}."
        else:
            greeting = f"Welcome back, {owner_name}."
    elif is_morning:
        greeting = (
            f"Good morning, {owner_name}. It's currently "
            f"{weather['temp_f']} degrees and {weather['condition']}, "
            f"with a high of {weather['high_f']} today."
        )
    else:
        greeting = (
            f"Welcome back, {owner_name}. It's currently "
            f"{weather['temp_f']} degrees and {weather['condition']} outside."
        )

    speak(greeting)


RECORD_MAX_SECONDS = 8
# Once real speech has been heard, stop after this much trailing silence
# instead of always recording the full RECORD_MAX_SECONDS. 1.2s cut users
# off mid-sentence during a normal thinking pause (confirmed via journalctl:
# "what AI model are you currently using and" got cut off there) — 2.0s
# gives more room for a natural pause without waiting for the full 8s cap.
RECORD_SILENCE_TIMEOUT = 2.0
# Matches wake_listener.py's MIN_UTTERANCE_RMS — same mic, same room, already
# tuned to separate real speech from ambient noise on this hardware.
RECORD_MIN_SPEECH_RMS = 220
RECORD_CHUNK_BYTES = 4000


def record_audio():
    print("Listening...", flush=True)

    recorder = subprocess.Popen(
        [
            "arecord",
            "-D", MIC_DEVICE,
            "-t", "raw",
            "-f", "S16_LE",
            "-r", "16000",
            "-c", "1"
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )

    chunks = []
    speech_started = False
    silence_since_speech = 0.0
    start = time.monotonic()
    peak_rms = 0

    try:
        while time.monotonic() - start < RECORD_MAX_SECONDS:
            chunk = recorder.stdout.read(RECORD_CHUNK_BYTES)

            if not chunk:
                break

            chunks.append(chunk)

            samples = array.array("h")
            samples.frombytes(chunk)
            rms = (
                math.sqrt(sum(sample * sample for sample in samples) / len(samples))
                if samples else 0
            )
            peak_rms = max(peak_rms, rms)
            chunk_seconds = len(chunk) / 2 / 16000

            if rms >= RECORD_MIN_SPEECH_RMS:
                speech_started = True
                silence_since_speech = 0.0
            elif speech_started:
                silence_since_speech += chunk_seconds

                if silence_since_speech >= RECORD_SILENCE_TIMEOUT:
                    break
    finally:
        if recorder.poll() is None:
            recorder.terminate()

            try:
                recorder.wait(timeout=2)
            except subprocess.TimeoutExpired:
                recorder.kill()
                recorder.wait()

        recorder.stdout.close()

    with wave.open(AUDIO_PATH, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"".join(chunks))

    global _last_audio_rms
    _last_audio_rms = round(peak_rms)


# Set by record_audio each turn so the logbook can capture the mic level
# even though record_audio's real job is writing the WAV.
_last_audio_rms = None


def transcribe_audio(model):
    with wave.open(AUDIO_PATH, "rb") as audio:
        recognizer = KaldiRecognizer(model, audio.getframerate())

        while True:
            data = audio.readframes(4000)

            if not data:
                break

            recognizer.AcceptWaveform(data)

    result = json.loads(recognizer.FinalResult())
    return result.get("text", "").strip()



def call_atlas_command(command):
    response = requests.get(
        f"{ATLAS_HUB}/atlas",
        params={"cmd": command},
        timeout=12
    )
    response.raise_for_status()
    return response.text.strip()


def summarize_printer_status(raw_status):
    lines = [
        line.strip()
        for line in raw_status.splitlines()
        if line.strip()
    ]

    if any(line.startswith("AD5X OFFLINE") for line in lines):
        return "The printer appears to be offline."

    if not any(line.startswith("AD5X ONLINE") for line in lines):
        return "I could not read the printer status."

    parts = ["The printer is online."]

    state_line = next(
        (line for line in lines if line.startswith("STATE:")),
        ""
    )

    if state_line:
        state = state_line.split(":", 1)[1].strip()

        if state and state.lower() != "unknown":
            parts.append(f"Its state is {state}.")

    progress_line = next(
        (line for line in lines if line.startswith("PROGRESS")),
        ""
    )

    if "/" in progress_line:
        try:
            progress_value = progress_line.replace(
                "PROGRESS", ""
            ).strip()

            current_value, total_value = progress_value.split("/", 1)

            current_bytes = float(current_value.strip())
            total_bytes = float(total_value.strip())

            if total_bytes > 0:
                percent = round(
                    current_bytes * 100 / total_bytes
                )
                parts.append(f"Progress is about {percent} percent.")
        except (ValueError, ZeroDivisionError):
            pass

    layer_line = next(
        (line for line in lines if line.startswith("Layer:")),
        ""
    )

    if layer_line and "unknown" not in layer_line.lower():
        layer = layer_line.split(":", 1)[1].strip()
        parts.append(f"It is on layer {layer}.")

    temperature_line = next(
        (line for line in lines if line.startswith("NOZ ")),
        ""
    )

    if temperature_line:
        try:
            temperature_parts = (
                temperature_line
                .replace("NOZ", "")
                .replace("BED", "")
                .split()
            )

            nozzle = temperature_parts[0].split("/", 1)[0]
            bed = temperature_parts[1].split("/", 1)[0]

            parts.append(
                f"The nozzle is {nozzle} degrees "
                f"and the bed is {bed} degrees."
            )
        except (IndexError, ValueError):
            pass

    return " ".join(parts)



def confirm_printer_cancel(model):
    speak(
        "Are you sure you want me to cancel the current print? "
        "Say yes or no."
    )

    set_face("listening")
    print("Waiting four seconds for yes or no...", flush=True)

    subprocess.run([
        "arecord",
        "-D", MIC_DEVICE,
        "-f", "S16_LE",
        "-r", "16000",
        "-c", "1",
        "-d", "4",
        AUDIO_PATH
    ], check=True)

    set_face("thinking")

    confirmation = transcribe_audio(model).lower().strip()
    print("Cancel confirmation heard:", confirmation, flush=True)

    normalized = confirmation

    for punctuation in [",", ".", "?", "!", ";", ":"]:
        normalized = normalized.replace(punctuation, " ")

    normalized = " ".join(normalized.split())
    words = set(normalized.split())

    # A negative response always wins.
    if (
        words.intersection({"no", "nope", "negative"})
        or "never mind" in normalized
        or "do not" in normalized
        or "don't" in normalized
    ):
        return "Okay. I will not cancel the print."

    # Only an explicit affirmative response can cancel.
    if words.intersection({"yes", "yeah", "yep", "confirm", "affirmative"}):
        result = call_atlas_command("printer_cancel")

        if "PRINTER CANCEL SENT" in result:
            return "The print has been cancelled."

        if "PRINTER CANCEL DISABLED" in result:
            return "Printer cancellation controls are disabled."

        print("Printer cancel response:", result, flush=True)
        return "I could not cancel the print."

    return (
        "I did not hear a clear yes or no. "
        "The print was not cancelled."
    )



def is_vision_command(text):
    normalized = text.lower().strip()

    replacements = {
        "what do you sea": "what do you see",
        "what can you sea": "what can you see",
        "take a pitcher": "take a picture",
    }

    for original, corrected in replacements.items():
        normalized = normalized.replace(original, corrected)

    for punctuation in [",", ".", "?", "!", ";", ":"]:
        normalized = normalized.replace(punctuation, " ")

    normalized = " ".join(normalized.split())

    exact_commands = {
        "what do you see",
        "you see",
        "let me see",
        "do you see",
        "can you see",
        "what are you seeing",
        "see anything",
        "what can you see",
        "describe what you see",
        "look around",
        "look at this",
        "check the camera",
        "use your camera",
        "take a picture",
        "take a photo",
        "describe the room",
    }

    if normalized in exact_commands:
        return True

    words = set(normalized.split())

    vision_words = {
        "see",
        "look",
        "camera",
        "picture",
        "photo",
        "image",
        "room",
    }

    action_words = {
        "what",
        "describe",
        "check",
        "take",
        "use",
        "look",
    }

    return bool(
        words.intersection(vision_words)
        and words.intersection(action_words)
    )


def run_vision_command():
    print("Running camera vision command.", flush=True)

    subprocess.run(
        [sys.executable, VISION_SCRIPT],
        check=True
    )


IMAGE_SEARCH_PREFIXES = [
    "show me a picture of",
    "show me a photo of",
    "show me an image of",
    "show me pictures of",
    "show me photos of",
    "show me images of",
    "search for a picture of",
    "search for a photo of",
    "search for an image of",
    "search the web for a picture of",
    "find a picture of",
    "find a photo of",
    "find an image of",
    "look up a picture of",
    "look up an image of",
    "picture of",
    "photo of",
    "image of",
    "pictures of",
    "photos of",
    "images of",
]

IMAGE_SEARCH_FILLER_WORDS = ("a ", "an ", "the ", "of ")


def parse_image_search_query(text):
    """Returns the search subject if text is an image-search request,
    otherwise None."""
    normalized = text.lower().strip()

    for punctuation in [",", ".", "?", "!", ";", ":"]:
        normalized = normalized.replace(punctuation, " ")

    normalized = " ".join(normalized.split())

    if not normalized:
        return None

    if normalized.startswith("what does ") and normalized.endswith(" look like"):
        subject = normalized[len("what does "):-len(" look like")].strip()

        for filler in IMAGE_SEARCH_FILLER_WORDS:
            if subject.startswith(filler):
                subject = subject[len(filler):].strip()

        return subject or None

    for prefix in sorted(IMAGE_SEARCH_PREFIXES, key=len, reverse=True):
        if normalized == prefix:
            return None

        if normalized.startswith(prefix + " "):
            subject = normalized[len(prefix):].strip()

            for filler in IMAGE_SEARCH_FILLER_WORDS:
                if subject.startswith(filler):
                    subject = subject[len(filler):].strip()

            return subject or None

    return None


def run_image_search_command(query):
    print("Running image search for:", query, flush=True)
    set_face("thinking")

    results = web_search.search_images(query, max_results=6)

    for result in results:
        url = result.get("image_url") or result.get("thumbnail_url")

        if not url:
            continue

        try:
            response = requests.post(
                f"{HUB}/show_image",
                json={"url": url, "caption": query, "duration": 10},
                timeout=15
            )
        except requests.RequestException as error:
            print("show_image request failed:", error, flush=True)
            continue

        if response.status_code == 200 and response.json().get("ok"):
            answer = f"Here's a picture of {query}."
            log_qa(query, answer)
            speak(answer)
            return

    answer = f"I could not find a picture of {query}."
    log_qa(query, answer)
    speak(answer)


GALLERY_SEARCH_PREFIXES = [
    "show me more pictures of",
    "show me more photos of",
    "show me more images of",
    "show me a gallery of",
    "show me multiple pictures of",
    "show me multiple photos of",
    "search for more pictures of",
    "find more pictures of",
    "gallery of",
]


def parse_gallery_search_query(text):
    """Returns the search subject if text is a gallery/multi-image
    request, otherwise None."""
    normalized = text.lower().strip()

    for punctuation in [",", ".", "?", "!", ";", ":"]:
        normalized = normalized.replace(punctuation, " ")

    normalized = " ".join(normalized.split())

    if not normalized:
        return None

    for prefix in sorted(GALLERY_SEARCH_PREFIXES, key=len, reverse=True):
        if normalized == prefix:
            return None

        if normalized.startswith(prefix + " "):
            subject = normalized[len(prefix):].strip()

            for filler in IMAGE_SEARCH_FILLER_WORDS:
                if subject.startswith(filler):
                    subject = subject[len(filler):].strip()

            return subject or None

    return None


def run_gallery_search_command(query):
    print("Running gallery search for:", query, flush=True)
    set_face("thinking")

    results = web_search.search_images(query, max_results=6)
    urls = [result.get("image_url") or result.get("thumbnail_url") for result in results]
    urls = [url for url in urls if url]

    if not urls:
        answer = f"I could not find any pictures of {query}."
        log_qa(query, answer)
        speak(answer)
        return

    try:
        response = requests.post(
            f"{HUB}/show_images",
            json={"urls": urls, "caption": query, "duration": 15},
            timeout=30
        )
    except requests.RequestException as error:
        print("show_images request failed:", error, flush=True)
        answer = f"I could not find any pictures of {query}."
        log_qa(query, answer)
        speak(answer)
        return

    if response.status_code == 200 and response.json().get("ok"):
        count = response.json().get("count", 0)
        answer = f"Here are {count} pictures of {query}."
        log_qa(query, answer)
        speak(answer)
        return

    answer = f"I could not find any pictures of {query}."
    log_qa(query, answer)
    speak(answer)


TIME_PHRASES = {
    "what time is it",
    "what is the time",
    "what's the time",
    "whats the time",
    "do you have the time",
    "got the time",
    "can you tell me the time",
}

DATE_PHRASES = {
    "what is the date",
    "what's the date",
    "whats the date",
    "what day is it",
    "what is today's date",
    "whats todays date",
    "what's today's date",
}

UPTIME_PHRASES = {
    "what is your uptime",
    "what's your uptime",
    "whats your uptime",
    "how long have you been running",
    "how long have you been up",
    "how long have you been on",
}

PING_PHRASES = {
    "are you there",
    "you there",
    "can you hear me",
    "is anyone there",
    "hello",
    "anybody home",
}


def parse_instant_answer(text):
    """Returns a canned answer for a handful of common questions that don't
    need the model at all — instant and free instead of a full API round
    trip. Returns None if text doesn't match any of them."""
    normalized = text.lower().strip()

    for punctuation in [",", ".", "?", "!", ";", ":"]:
        normalized = normalized.replace(punctuation, " ")

    normalized = " ".join(normalized.split())

    if normalized in TIME_PHRASES:
        return "It's " + datetime.now().strftime("%I:%M %p").lstrip("0") + "."

    if normalized in DATE_PHRASES:
        return "Today is " + datetime.now().strftime("%A, %B %d") + "."

    if normalized in UPTIME_PHRASES:
        uptime_seconds = hud_stats.get_uptime_seconds()
        hours = uptime_seconds // 3600
        minutes = (uptime_seconds % 3600) // 60
        hour_word = "hour" if hours == 1 else "hours"
        minute_word = "minute" if minutes == 1 else "minutes"
        return (
            f"I've been running for {hours} {hour_word} "
            f"and {minutes} {minute_word}."
        )

    if normalized in PING_PHRASES:
        return "Yes, I'm here and listening."

    return None


def _normalize_phrase(text):
    normalized = text.lower().strip()

    for punctuation in [",", ".", "?", "!", ";", ":"]:
        normalized = normalized.replace(punctuation, " ")

    return " ".join(normalized.split())


# ---------------------------------------------------------------------
# Local voice commands: PC power, timers, focus mode, notes, briefings.
# All zero-token — parsed here, executed via the hub or local files.
# ---------------------------------------------------------------------

# ---------------------------------------------------------------------
# PC control via the Windows companion (P2-A). All degrade gracefully
# when the companion isn't configured/reachable.
# ---------------------------------------------------------------------

OPEN_FUSION_PHRASES = {
    "open fusion", "open fusion 360", "launch fusion", "start fusion",
    "open fusion three sixty",
}

OPEN_SPOTIFY_PHRASES = {"open spotify", "launch spotify", "open spot of i", "open spotifi"}
OPEN_CLAUDE_PHRASES = {"open claude", "launch claude", "open cloud"}
EMPTY_RECYCLE_BIN_PHRASES = {
    "empty the recycle bin", "empty recycle bin", "empty my recycle bin", "clear the recycle bin",
}

SHUTDOWN_PC_PHRASES = {
    "shut down my pc", "shutdown my pc", "shut down my computer",
    "turn off my pc", "turn off my computer",
}
CANCEL_PC_SHUTDOWN_PHRASES = {
    "cancel pc shutdown", "cancel my pc shutdown", "abort pc shutdown",
}

PC_APPS_PHRASES = {
    "what's open on my pc", "whats open on my pc", "what is open on my pc",
    "what's running on my pc", "whats running on my pc",
    "what apps are open", "what's on my pc", "whats on my pc",
}

PC_SCREENSHOT_PHRASES = {
    "show me my pc screen", "show my pc screen", "what's on my pc screen",
    "whats on my pc screen", "screenshot my pc", "take a screenshot of my pc",
    "show me what's on my computer", "capture my pc screen",
}

NEWEST_SCREENSHOT_PHRASES = {
    "show me the newest screenshot", "show my newest screenshot",
    "show me the latest screenshot", "show me my last screenshot",
    "show me the newest reference", "show me my reference",
    "pull up my newest screenshot",
}

VOLUME_UP_PHRASES = {"volume up", "turn up the volume", "turn up my pc volume", "louder"}
VOLUME_DOWN_PHRASES = {"volume down", "turn down the volume", "turn down my pc volume", "quieter"}
VOLUME_MUTE_PHRASES = {"mute", "mute my pc", "mute the volume", "unmute", "unmute my pc"}
MEDIA_PLAY_PHRASES = {"play", "pause", "play pause", "pause my music", "play my music", "resume my music"}
MEDIA_NEXT_PHRASES = {"next track", "skip this song", "next song", "skip track", "skip"}
MEDIA_PREV_PHRASES = {"previous track", "last song", "previous song", "go back a track"}


def _is_open_spotify_phrase(normalized):
    """Accept common speech-to-text renderings of Spotify, but only after
    an explicit open/launch command."""
    starts_open = normalized.startswith(("open ", "launch "))
    spotify_sounds = (
        "spotify", "spotifi", "spot of i", "spotify", "spot if i",
    )
    return starts_open and any(sound in normalized for sound in spotify_sounds)


def _is_open_claude_phrase(normalized):
    return normalized.startswith(("open ", "launch ")) and (
        "claude" in normalized or normalized.endswith(" cloud")
    )


def _is_empty_recycle_bin_phrase(normalized):
    """Matches recycle/recycling-bin wording, including 'empty my/your'."""
    words = set(normalized.split())
    wants_empty = bool(words & {"empty", "clear"})
    refers_to_bin = "bin" in words and any(word.startswith("recycl") for word in words)
    return wants_empty and refers_to_bin


PC_SEARCH_PATTERNS = [
    re.compile(r"^find me videos? (?:showing |about |on |of )?(?:how to )?(.+)$"),
    re.compile(r"^(?:search|look) (?:for |up )?(?:videos? |youtube )(?:for |about |on )?(.+)$"),
    re.compile(r"^search youtube for (.+)$"),
    re.compile(r"^show me (?:videos?|tutorials?|walkthroughs?) (?:showing |about |on |for |of )?(?:how to )?(.+)$"),
    re.compile(r"^pull up (?:a )?(?:youtube |video )(?:tutorial |search )?(?:for |about |on )?(.+)$"),
]


def parse_pc_search_command(text):
    """Returns the search subject for a 'find me videos ...' request that
    should run on the PC's browser, else None."""
    normalized = _normalize_phrase(text)

    for pattern in PC_SEARCH_PATTERNS:
        match = pattern.match(normalized)

        if match:
            subject = match.group(1).strip()
            return subject or None

    return None


def _pc_dispatch(normalized):
    """Returns a spoken answer for a PC-control phrase, or None if the
    phrase isn't a PC command."""
    if normalized in OPEN_FUSION_PHRASES:
        return pc_control.open_fusion()
    if _is_open_spotify_phrase(normalized):
        return pc_control.open_spotify()
    if _is_open_claude_phrase(normalized):
        return pc_control.open_claude()
    if normalized in PC_APPS_PHRASES:
        return pc_control.active_apps()
    if normalized in PC_SCREENSHOT_PHRASES:
        return pc_control.screenshot_to_hud()
    if normalized in NEWEST_SCREENSHOT_PHRASES:
        return pc_control.newest_screenshot_to_hud()
    if normalized in VOLUME_UP_PHRASES:
        return pc_control.set_volume("up")
    if normalized in VOLUME_DOWN_PHRASES:
        return pc_control.set_volume("down")
    if normalized in VOLUME_MUTE_PHRASES:
        return pc_control.set_volume("mute")
    if normalized in MEDIA_PLAY_PHRASES:
        return pc_control.media("playpause")
    if normalized in MEDIA_NEXT_PHRASES:
        return pc_control.media("next")
    if normalized in MEDIA_PREV_PHRASES:
        return pc_control.media("previous")
    if normalized in PC_HEALTH_PHRASES:
        return pc_control.pc_health_report()
    if normalized in PC_CLEANUP_PHRASES:
        return pc_control.run_pc_cleanup()
    if _is_empty_recycle_bin_phrase(normalized):
        return pc_control.empty_recycle_bin()
    if normalized in SHUTDOWN_PC_PHRASES:
        return pc_control.shutdown_pc()
    if normalized in CANCEL_PC_SHUTDOWN_PHRASES:
        return pc_control.cancel_pc_shutdown()
    return None


PC_HEALTH_PHRASES = {
    "how's my pc", "hows my pc", "how is my pc", "is my pc healthy",
    "check my pc", "check on my pc", "how's my pc doing", "hows my pc doing",
    "pc health", "how's my computer", "hows my computer",
}

PC_CLEANUP_PHRASES = {
    "clean up my pc", "clean up my computer", "clean my pc", "run pc cleanup",
    "clear my pc temp files", "clean up the pc",
}


WOL_DIAGNOSE_PHRASES = {
    "why won't my pc wake",
    "why wont my pc wake",
    "why won't my pc turn on",
    "why wont my pc turn on",
    "diagnose wake on lan",
    "diagnose the pc wake",
    "why can't you wake my pc",
    "why cant you wake my pc",
    "what's wrong with boot my pc",
    "whats wrong with boot my pc",
}


def run_wol_diagnose_command():
    answer, _evidence = pc_power.diagnose_wol()
    return answer


WAKE_PC_PHRASES = {
    "turn on my pc",
    "turn on my computer",
    "turn on the pc",
    "turn on my gaming pc",
    "turn on the gaming pc",
    "power on my pc",
    "power on my computer",
    "power on the pc",
    "power on my gaming pc",
    "boot my pc",
    "boot up my pc",
    "boot my computer",
    "boot my gaming pc",
    "wake my pc",
    "wake up my pc",
    "wake my computer",
    "wake my gaming pc",
    "start my pc",
    "start my gaming pc",
}


def run_wake_pc_command():
    try:
        response = requests.post(f"{HUB}/wake_pc", timeout=10)
        response.raise_for_status()
        return response.json().get("message", "Wake signal sent.")
    except requests.RequestException as error:
        print("wake_pc request failed:", error, flush=True)
        return "I couldn't send the wake signal."


_NUMBER_UNITS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}

_NUMBER_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def _words_to_digits(text):
    """Rewrites spoken number words (1-99, including compounds like
    'twenty five') into digits, and 'a'/'an' directly before a time unit
    into '1'. Vosk transcribes 'set a timer for five minutes' with words,
    not digits — every duration parser needs this to hear real speech."""
    words = text.split()
    output = []
    index = 0

    while index < len(words):
        word = words[index]

        if word in _NUMBER_TENS:
            value = _NUMBER_TENS[word]

            if index + 1 < len(words) and words[index + 1] in _NUMBER_UNITS:
                value += _NUMBER_UNITS[words[index + 1]]
                index += 1

            output.append(str(value))
        elif word in _NUMBER_UNITS:
            output.append(str(_NUMBER_UNITS[word]))
        elif word in ("a", "an") and index + 1 < len(words) and words[index + 1] in (
            "minute", "second", "hour", "minutes", "seconds", "hours"
        ):
            output.append("1")
        else:
            output.append(word)

        index += 1

    return " ".join(output)


TIMER_SET_PATTERNS = [
    re.compile(r"^(?:set|start) (?:a |an )?timer for (\d+) (second|seconds|minute|minutes|hour|hours)$"),
    re.compile(r"^timer for (\d+) (second|seconds|minute|minutes|hour|hours)$"),
    re.compile(r"^(?:set|start) (?:a |an )?(\d+) (second|minute|hour) timer$"),
]

TIMER_CANCEL_PHRASES = {
    "cancel the timer",
    "cancel my timer",
    "cancel timer",
    "stop the timer",
    "stop my timer",
}

TIMER_CHECK_PHRASES = {
    "how long on the timer",
    "how long left on the timer",
    "how long is left on the timer",
    "how much time is left",
    "how much time is left on the timer",
    "how much time is on the timer",
    "check the timer",
    "what's left on the timer",
    "whats left on the timer",
}


def parse_timer_set_command(text):
    """Returns delay seconds for a 'set a timer for N ...' request, else
    None. Accepts spoken number words ('five minutes') as well as digits."""
    normalized = _words_to_digits(_normalize_phrase(text))

    for pattern in TIMER_SET_PATTERNS:
        match = pattern.match(normalized)

        if match:
            amount = int(match.group(1))
            unit = match.group(2)

            if amount <= 0:
                return None

            if "second" in unit:
                return amount

            if "minute" in unit:
                return amount * 60

            return amount * 3600

    return None


def describe_timer_duration(seconds):
    if seconds < 60:
        return f"{seconds} second" + ("s" if seconds != 1 else "")

    return describe_delay(seconds)


def run_timer_set_command(seconds):
    try:
        response = requests.post(
            f"{HUB}/timer", json={"seconds": seconds}, timeout=5
        )
        response.raise_for_status()
        return f"Timer set for {describe_timer_duration(seconds)}."
    except requests.RequestException as error:
        print("timer request failed:", error, flush=True)
        return "I couldn't set the timer."


def run_timer_cancel_command():
    try:
        response = requests.post(f"{HUB}/timer/cancel", timeout=5)
        response.raise_for_status()

        if response.json().get("cancelled"):
            return "Timer cancelled."

        return "There's no timer running."
    except requests.RequestException as error:
        print("timer cancel request failed:", error, flush=True)
        return "I couldn't reach the timer."


def run_timer_check_command():
    try:
        response = requests.get(f"{HUB}/timer", timeout=5)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as error:
        print("timer check request failed:", error, flush=True)
        return "I couldn't reach the timer."

    if not data.get("running"):
        return "There's no timer running."

    return f"{describe_timer_duration(data['remaining_seconds'])} left."


FOCUS_START_PATTERN = re.compile(
    r"^(?:start |enter |begin )?focus mode(?: for (\d+) minutes?)?$"
)

FOCUS_END_PHRASES = {
    "end focus mode",
    "exit focus mode",
    "stop focus mode",
    "cancel focus mode",
    "end focus",
}


def parse_focus_start_command(text):
    """Returns focus minutes (default when unspecified) for a 'focus mode'
    request, else None. Accepts spoken number words as well as digits."""
    match = FOCUS_START_PATTERN.match(_words_to_digits(_normalize_phrase(text)))

    if not match:
        return None

    minutes = int(match.group(1)) if match.group(1) else 25

    return minutes if minutes > 0 else None


def run_focus_start_command(minutes):
    try:
        response = requests.post(
            f"{HUB}/focus", json={"minutes": minutes}, timeout=5
        )
        response.raise_for_status()
        return (
            f"Focus mode on for {minutes} minutes. "
            "I'll keep quiet until then."
        )
    except requests.RequestException as error:
        print("focus request failed:", error, flush=True)
        return "I couldn't start focus mode."


def run_focus_end_command():
    try:
        response = requests.post(f"{HUB}/focus/end", timeout=5)
        response.raise_for_status()

        if response.json().get("ended"):
            return "Focus mode off."

        return "Focus mode wasn't on."
    except requests.RequestException as error:
        print("focus end request failed:", error, flush=True)
        return "I couldn't reach focus mode."


NEWS_PHRASES = {
    "brief me on the news",
    "what's in the news",
    "whats in the news",
    "what is in the news",
    "news briefing",
    "give me the news",
    "today's news",
    "todays news",
    "tell me the news",
    "what's the news",
    "whats the news",
    "what is the news",
}

ENROLL_FACE_PHRASES = {
    "learn my face",
    "scan my face",
    "remember my face",
    "enroll my face",
    "register my face",
}

_ENROLL_VERB_WORDS = {
    "learn", "learned", "scan", "remember", "enroll", "register",
}


def is_enroll_face_command(normalized):
    """Word-based enrollment match — Vosk garbles short commands ('learn
    my face' came through as 'learn in my face' in live testing, missed
    the exact set, and fell through to a paid model call). A short
    utterance containing 'face' plus an enrollment verb is unambiguous
    enough."""
    if normalized in ENROLL_FACE_PHRASES:
        return True

    words = set(normalized.split())
    return (
        "face" in words
        and bool(words & _ENROLL_VERB_WORDS)
        and len(normalized.split()) <= 5
    )

GATE_ON_PHRASES = {
    "camera gate on",
    "turn on the camera gate",
    "enable the camera gate",
    "turn the camera gate on",
    "enable face verification",
}

GATE_OFF_PHRASES = {
    "camera gate off",
    "turn off the camera gate",
    "disable the camera gate",
    "turn the camera gate off",
    "disable face verification",
}

INTRUDER_QUERY_PHRASES = {
    "were there any unauthorized users while i was gone",
    "were there any unauthorized users",
    "any unauthorized users while i was gone",
    "any unauthorized users",
    "did anyone try to use you while i was gone",
    "did anyone use you while i was gone",
    "any intruders",
    "were there any intruders",
    "any intruders while i was gone",
}


def run_enroll_face_command():
    """Guided enrollment: one burst capture (spoken cue to hold still),
    quality-controlled crops, fresh LBPH model. Owner-only in practice —
    re-enrollment on an existing model only happens inside a verified
    (full-access) turn."""
    if camera_gate.cv2 is None:
        return "My face recognition libraries aren't installed."

    def progress(step):
        if step == "start":
            speak("Look at the camera and hold still for a moment.")

    count = camera_gate.enroll(progress=progress)

    if count == 0:
        return (
            "I couldn't see your face clearly enough to learn it. "
            "Check the lighting, face me directly, and try again."
        )

    camera_gate.mark_verified()
    return (
        f"Done — your face is enrolled from {count} captures. "
        "You're my authorized user now."
    )


def run_intruder_query_command():
    """Answers 'were there any unauthorized users while I was gone'. If
    yes: opens the security HUD page, displays each photo full-screen for
    10 seconds (deleting each after it's shown), then scrubs the alert."""
    intruders = camera_gate.unreviewed_intruders()

    if not intruders:
        return "No. Nobody unauthorized tried to use me while you were gone."

    count = len(intruders)
    word = "person" if count == 1 else "people"

    # Summarize what they tried before showing the evidence.
    attempted = []
    for record in intruders:
        for denied in record.get("denied_commands", []):
            attempted.append(denied["command"])

    speak(
        f"Yes — {count} unauthorized {word} tried to use me while you "
        "were gone. Showing you the records now."
    )

    # Open the security review layout and hand it the records.
    try:
        requests.post(f"{HUB}/security_review", timeout=10)
    except requests.RequestException as error:
        print("security_review request failed:", error, flush=True)

    # Full-screen each photo for 10 seconds, deleting after display.
    for record in intruders:
        try:
            requests.post(
                f"{HUB}/show_intruder_photo",
                json={"id": record["id"], "duration": 10},
                timeout=10,
            )
        except requests.RequestException as error:
            print("show_intruder_photo failed:", error, flush=True)
            continue

        when = time.strftime("%-I:%M %p", time.localtime(record["timestamp"]))
        denied = record.get("denied_commands", [])

        if denied:
            tried = "; ".join(d["command"] for d in denied)
            speak(f"At {when}, they tried: {tried}. All denied.")
        else:
            speak(f"At {when}. They didn't get past the lock.")

        time.sleep(10)
        camera_gate.delete_intruder_photo(record["id"])

    camera_gate.mark_intruders_reviewed()

    try:
        requests.post(f"{HUB}/security_review/close", timeout=5)
    except requests.RequestException:
        pass

    return "That's everyone. The alert is cleared."


# What a non-verified person may still do: strictly local, zero-token,
# no personal data, no web, no physical actuation beyond timers.
def _handle_restricted_turn(text):
    """Dispatch for unverified users. Returns the spoken answer."""
    instant = parse_instant_answer(text)

    if instant is not None:
        return instant

    timer_seconds = parse_timer_set_command(text)

    if timer_seconds is not None:
        return run_timer_set_command(timer_seconds)

    normalized = _normalize_phrase(text)

    if normalized in TIMER_CANCEL_PHRASES:
        return run_timer_cancel_command()

    if normalized in TIMER_CHECK_PHRASES:
        return run_timer_check_command()

    reminder = parse_reminder_command(text)

    if reminder is not None:
        delay_seconds, message = reminder
        memory_store.add_reminder(delay_seconds, message)
        return f"Got it, I'll remind you in {describe_delay(delay_seconds)}."

    # Anything beyond local basics is denied AND recorded against the
    # intruder so the owner can review what was attempted.
    camera_gate.record_denied_command(text)
    return (
        "You're not my authorized user, so I'm limited to local basics — "
        "time, date, timers, and reminders."
    )


NETWORK_DEVICES_PHRASES = {
    "what's on my network",
    "whats on my network",
    "what is on my network",
    "who's on my network",
    "whos on my network",
    "who is on my network",
    "network devices",
    "scan the network",
}

_NETWORK_QUERY_WORDS = {"what", "list", "show", "who", "whos", "scan", "which"}


def is_network_devices_command(normalized):
    """Word-based match — live testing produced 'list the devices on my
    network', a word order no exact set anticipated. A short utterance
    mentioning the network plus devices plus a query verb is unambiguous."""
    if normalized in NETWORK_DEVICES_PHRASES:
        return True

    words = set(normalized.split())

    if "network" not in words and "lan" not in words:
        return False

    has_devices = bool(words & {"devices", "device"})
    has_query = bool(words & _NETWORK_QUERY_WORDS)

    return has_devices and has_query and len(normalized.split()) <= 8


def run_network_devices_command():
    """Speaks a roster of who's on the LAN, best names first. The HUD's
    LAN DEVICES panel shows the same data continuously."""
    try:
        response = requests.get(f"{HUB}/network_devices", timeout=5)
        response.raise_for_status()
        devices = response.json().get("devices", [])
    except requests.RequestException as error:
        print("network_devices request failed:", error, flush=True)
        return "I couldn't reach my network scanner."

    if not devices:
        return (
            "My last network sweep hasn't finished yet — "
            "give me a couple of minutes."
        )

    count = len(devices)
    names = []

    for device in devices:
        name = device.get("hostname") or device.get("vendor")
        if name:
            names.append(name)

    spoken = f"{count} devices online."

    if names:
        listed = ", ".join(names[:8])
        spoken += f" I can identify: {listed}."
        unnamed = count - len(names)

        if unnamed > 0:
            spoken += f" Plus {unnamed} I can't put a name to."

    return spoken


PRINT_ETA_PHRASES = {
    "how long left on the print",
    "how long is left on the print",
    "how much longer on the print",
    "how long until the print is done",
    "when will the print be done",
    "when will the print finish",
    "print eta",
    "what's the print eta",
    "whats the print eta",
}


def run_print_eta_command():
    try:
        response = requests.get(f"{HUB}/hud/stats", timeout=8)
        response.raise_for_status()
        printer = response.json().get("printer", {})
    except requests.RequestException as error:
        print("print eta request failed:", error, flush=True)
        return "I couldn't reach the printer stats."

    if not printer.get("online"):
        return "The printer is offline."

    state = printer.get("state")
    progress = printer.get("progress_percent")
    eta_minutes = printer.get("eta_minutes")

    if state not in ("building", "printing"):
        return f"There's no active print — the printer is {state or 'idle'}."

    spoken = f"The print is at {progress} percent."

    if eta_minutes is None:
        return spoken + " Give me a few more minutes of data for an E T A."

    if eta_minutes >= 60:
        hours = eta_minutes // 60
        minutes = eta_minutes % 60
        hour_word = "hour" if hours == 1 else "hours"
        return spoken + f" About {hours} {hour_word} and {minutes} minutes to go."

    return spoken + f" About {eta_minutes} minutes to go."


STAND_DOWN_PHRASES = {
    "stand down",
    "acknowledge the alert",
    "acknowledge alert",
    "cancel the alert",
    "cancel red alert",
    "all clear",
}

SCREEN_DARK_PHRASES = {
    "go dark",
    "lights out",
    "screen off",
    "turn off the screen",
    "turn the screen off",
}

SCREEN_WAKE_PHRASES = {
    "lights up",
    "screen on",
    "turn on the screen",
    "turn the screen on",
    "wake the screen",
}


def _set_screen_dark(dark):
    try:
        requests.post(f"{HUB}/screen", json={"dark": dark}, timeout=5)
        return True
    except requests.RequestException as error:
        print("screen request failed:", error, flush=True)
        return False


PHONE_PHRASES = {
    "is my phone home",
    "is my phone here",
    "is my phone on the network",
    "is my phone connected",
    "where's my phone",
    "wheres my phone",
}


def run_phone_command():
    try:
        response = requests.get(f"{HUB}/phone", timeout=5)
        response.raise_for_status()
        phone = response.json()
    except requests.RequestException as error:
        print("phone request failed:", error, flush=True)
        return "I couldn't reach my presence tracker."

    if not phone.get("configured"):
        return (
            "I don't know which device is your phone yet — "
            "set PHONE_MAC in my config and I'll track it."
        )

    if phone.get("present"):
        return "Yes, your phone is on the network."

    last_seen = phone.get("last_seen") or 0

    if last_seen:
        minutes_ago = round((time.time() - last_seen) / 60)
        return f"No — I last saw it about {minutes_ago} minutes ago."

    return "No, I haven't seen your phone since I started watching."


INTERNET_CHECK_PHRASES = {
    "how's the internet",
    "hows the internet",
    "how is the internet",
    "check the internet",
    "internet status",
    "run a ping test",
    "ping test",
    "is the internet up",
    "is the internet down",
}


def run_internet_check_command():
    """Real measurements, spoken honestly: ping latency/loss to 8.8.8.8
    plus a DNS resolution timing. No token cost, ~3 seconds."""
    try:
        result = subprocess.run(
            ["ping", "-c", "5", "-i", "0.25", "-W", "2", "8.8.8.8"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        return "I couldn't run the ping test."

    loss_match = re.search(r"(\d+(?:\.\d+)?)% packet loss", result.stdout)
    rtt_match = re.search(
        r"= [\d.]+/([\d.]+)/[\d.]+/[\d.]+ ms", result.stdout
    )

    if loss_match and float(loss_match.group(1)) >= 100:
        return "The internet looks down — every ping was lost."

    parts = []

    if rtt_match:
        parts.append(f"average latency {float(rtt_match.group(1)):.0f} milliseconds")

    if loss_match and float(loss_match.group(1)) > 0:
        parts.append(f"{loss_match.group(1)} percent packet loss")

    import socket as socket_module
    dns_start = time.monotonic()

    try:
        socket_module.getaddrinfo("google.com", 443)
        dns_ms = (time.monotonic() - dns_start) * 1000
        parts.append(f"DNS resolving in {dns_ms:.0f} milliseconds")
    except OSError:
        parts.append("but DNS lookups are failing")

    if not parts:
        return "The ping ran but I couldn't parse the results."

    quality = "healthy"

    if rtt_match and float(rtt_match.group(1)) > 100:
        quality = "sluggish"

    if loss_match and float(loss_match.group(1)) > 0:
        quality = "flaky"

    return f"Internet looks {quality}: " + ", ".join(parts) + "."


DIAGNOSTICS_PHRASES = {
    "run diagnostics",
    "run a diagnostic",
    "run your diagnostics",
    "run a system check",
    "system check",
    "self test",
    "run a self test",
    "run self test",
    "check your systems",
    "run a system diagnostic",
    "how are your systems",
    "diagnostics",
}

# "Get the whole system healthy" — full diagnose + safe repair sweep (P1-D).
EMERGENCY_SHUTDOWN_PHRASES = {
    "initiate emergency shutdown",
    "emergency shutdown",
    "emergency shut down",
    "initiate emergency shut down",
    "shut everything down now",
    "emergency power down",
}

PRINTER_EMERGENCY_PHRASES = {
    "emergency stop the printer",
    "emergency stop the print",
    "emergency stop printing",
    "halt the printer now",
}


def run_emergency_shutdown_command(model):
    """Predefined emergency shutdown — requires an explicit spoken yes,
    since it powers down the Pi."""
    import emergency

    speak(
        "Emergency shutdown will preserve data, pause any print, and power "
        "me down in one minute. Say yes to confirm, or no to cancel."
    )
    set_face("listening")

    subprocess.run([
        "arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", "16000",
        "-c", "1", "-d", "4", AUDIO_PATH,
    ], check=False)

    set_face("thinking")
    confirmation = _normalize_phrase(transcribe_audio(model))
    words = set(confirmation.split())

    if words & {"yes", "yeah", "yep", "confirm", "affirmative", "do"}:
        steps = emergency.emergency_shutdown(dry_run=False)
        if steps.get("shutdown_scheduled") is True:
            return "Confirmed. Powering down in one minute. Say cancel shutdown to abort."
        return "I preserved data and paused the printer, but couldn't schedule the shutdown."

    return "Emergency shutdown cancelled. Nothing was powered down."


CHIEF_OF_STAFF_PHRASES = {
    "what am i forgetting this week",
    "what am i forgetting",
    "what's on my plate",
    "whats on my plate",
    "what's on my plate this week",
    "whats on my plate this week",
    "what do i have coming up",
    "what's coming up this week",
    "whats coming up this week",
    "chief of staff",
    "what are my deadlines",
    "what's due this week",
    "whats due this week",
}


def run_chief_of_staff_command():
    import chief_of_staff
    return chief_of_staff.weekly_rundown()


SYSTEM_HEALTH_PHRASES = {
    "get the whole system healthy",
    "get the system healthy",
    "make the system healthy",
    "get everything healthy",
    "fix the whole system",
    "run a full system check and fix",
    "heal the system",
    "get yourself healthy",
}

# "Secure my network" — network defense audit (P1-F).
SECURE_NETWORK_PHRASES = {
    "secure my network",
    "secure the network",
    "check my network security",
    "audit my network",
    "is my network secure",
    "run a network security check",
}

# Diagnostic-history questions answered from the persistent logbook.
LOG_QUERY_PHRASES = {
    "why didn't you hear me",
    "why couldn't you hear me",
    "what went wrong",
    "what went wrong earlier",
    "how have you been running",
    "check your logs",
    "what do your logs say",
    "any recent errors",
    "have you had any errors",
}


def run_system_health_command():
    """Full Pi-side health sweep: diagnose, safe-repair, verify, back up,
    report. Sets the diagnostics HUD layout while it runs."""
    import system_health

    try:
        requests.post(f"{HUB}/layout", json={"layout": "diagnostics"}, timeout=5)
    except requests.RequestException:
        pass

    result = system_health.run_full_sweep()

    try:
        requests.post(f"{HUB}/layout", json={"layout": "idle"}, timeout=5)
    except requests.RequestException:
        pass

    return system_health.spoken_summary(result)


def run_secure_network_command():
    """Read-only network defense audit — unknown devices, exposed
    services, failed SSH attempts. Never blocks anything."""
    import net_defense

    return net_defense.spoken_report(net_defense.audit())


def run_log_query_command():
    """Answers diagnostic-history questions from the persistent logbook —
    recent turn count, error rate, latency, wake confidence. Zero tokens."""
    summary = logbook.diagnostic_summary()

    if summary["count"] == 0:
        return "I don't have any interaction history logged yet."

    parts = [f"Over my last {summary['count']} interactions"]

    if summary["errors"] == 0:
        parts.append("I had no errors")
    else:
        parts.append(f"I hit {summary['errors']} errors")
        if summary["last_error"]:
            parts.append(f"the most recent was {summary['last_error']}")

    if summary["avg_latency_ms"]:
        parts.append(f"averaging {summary['avg_latency_ms'] / 1000:.1f} seconds per turn")

    if summary["avg_wake_confidence"] is not None:
        parts.append(f"wake confidence averaged {summary['avg_wake_confidence']}")

    return ". ".join([parts[0] + ", " + parts[1]] + parts[2:]) + "."

BRIEFING_PHRASES = {
    "morning briefing",
    "daily briefing",
    "morning brief",
    "daily brief",
    "brief me",
    "give me my briefing",
    "give me the briefing",
    "run my briefing",
}


def run_read_notes_command():
    notes = memory_store.load_notes()

    if not notes:
        return "You don't have any notes saved."

    count = len(notes)
    word = "note" if count == 1 else "notes"
    spoken_notes = ". ".join(
        f"Note {index + 1}: {note['text']}"
        for index, note in enumerate(notes)
    )

    return f"You have {count} {word}. {spoken_notes}."


REMINDER_PATTERN = re.compile(
    r"^remind me in (\d+) (minute|minutes|hour|hours) (?:to|that) (.+)$"
)


def parse_reminder_command(text):
    """Returns (delay_seconds, message) for a 'remind me in N minutes/hours
    to...' request, otherwise None. Handled entirely locally — no model
    call needed to schedule one. Accepts spoken number words ('twenty
    minutes') as well as digits."""
    normalized = text.lower().strip()

    for punctuation in [",", ".", "?", "!", ";", ":"]:
        normalized = normalized.replace(punctuation, " ")

    normalized = " ".join(normalized.split())
    normalized = _words_to_digits(normalized)

    match = REMINDER_PATTERN.match(normalized)

    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    message = match.group(3).strip()

    if amount <= 0 or not message:
        return None

    delay_seconds = amount * 60 if "minute" in unit else amount * 3600

    return delay_seconds, message


def describe_delay(delay_seconds):
    if delay_seconds >= 3600 and delay_seconds % 3600 == 0:
        hours = delay_seconds // 3600
        return f"{hours} hour" + ("s" if hours != 1 else "")

    minutes = round(delay_seconds / 60)
    return f"{minutes} minute" + ("s" if minutes != 1 else "")


def handle_local_command(text, model):
    normalized = text.lower().strip()

    # Correct common Vosk interpretations.
    replacements = {
        "print her": "printer",
        "paws": "pause",
        "paused": "pause",
        "pausing": "pause",
        "un pause": "unpause",
        "resumed": "resume",
        "ad five x": "ad5x",
        "ad five ex": "ad5x",
    }

    for original, corrected in replacements.items():
        normalized = normalized.replace(original, corrected)

    for punctuation in [",", ".", "?", "!", ";", ":"]:
        normalized = normalized.replace(punctuation, " ")

    normalized = " ".join(normalized.split())
    words = normalized.split()
    word_set = set(words)

    print("Local command parser heard:", normalized, flush=True)

    printer_context = (
        any(word.startswith("print") for word in words)
        or "printer" in word_set
        or "ad5x" in word_set
    )

    # No printer wording means this can continue to the AI.
    if not printer_context:
        return None

    # Explicit cancel-type words require confirmation.
    cancel_requested = (
        any(
            word.startswith("cancel")
            or word.startswith("abort")
            or word.startswith("terminate")
            for word in words
        )
        or (
            "end" in word_set
            and (
                "print" in word_set
                or "printing" in word_set
                or "printer" in word_set
            )
        )
    )

    if cancel_requested:
        return confirm_printer_cancel(model)

    resume_requested = (
        any(word.startswith("resum") for word in words)
        or bool(
            word_set.intersection({
                "continue",
                "unpause",
                "restart"
            })
        )
    )

    if resume_requested:
        result = call_atlas_command("printer_resume")

        if "PRINTER RESUME SENT" in result:
            return "The printer resume command was sent."

        if "DISABLED" in result:
            return "Printer resume controls are disabled."

        return "I could not resume the printer."

    pause_requested = (
        any(word.startswith("paus") for word in words)
        or bool(
            word_set.intersection({
                "hold",
                "freeze",
                "stop"
            })
        )
    )

    if pause_requested:
        result = call_atlas_command("printer_pause")

        if "PRINTER PAUSE SENT" in result:
            return "The printer pause command was sent."

        if "DISABLED" in result:
            return "Printer pause controls are disabled."

        return "I could not pause the printer."

    status_requested = (
        bool(
            word_set.intersection({
                "status",
                "progress",
                "doing",
                "layer",
                "temperature",
                "temp",
                "online",
                "offline"
            })
        )
        or (
            "how" in word_set
            and (
                "doing" in word_set
                or "is" in word_set
            )
        )
    )

    if status_requested:
        result = call_atlas_command("printer_status")
        return summarize_printer_status(result)

    # Important: printer requests never fall through to OpenAI.
    return (
        "I heard a printer request, but not a clear command. "
        "Say printer status, pause the printer, resume the printer, "
        "or cancel the printer."
    )



def build_instructions_and_limits():
    """Returns (instructions, max_tokens) shared by every path that talks to
    the model, so the streaming and non-streaming call sites can't drift."""
    today = datetime.now().strftime("%Y-%m-%d")
    owner_name = load_owner_name()
    quiet = _in_quiet_hours()
    max_tokens = 120 if quiet else 300

    instructions = (
        f"You are A.T.L.A.S., {owner_name}'s desk robot — a physical "
        "machine on their desk: Raspberry Pi 5 core, tactical HUD screen, "
        "speaker, mic, camera, with a 3D printer and the LAN under your "
        f"watch. Today's date is {today}. Your register is mission-control: "
        "calm, precise, a little dry — competence first, wit as seasoning. "
        "Lead with the answer in your first sentence; context after. "
        "Answer the way a smart friend who actually knows the answer would, "
        "not a customer-service bot. Never open with filler like 'I'd be "
        "happy to help', 'great question', or 'I understand your concern'. "
        "If asked for a recommendation or opinion, commit to one — pick it, "
        "note a caveat after if it matters, never hedge with 'it depends'. "
        f"Use {owner_name}'s name rarely — an occasional address lands; "
        "constant use is grating. Never read out URLs, file paths, or "
        "code syntax — describe the source in words if it matters. "
    )

    if quiet:
        instructions += (
            "It's late at night — keep answers brief and calm, one to two "
            "sentences, since it will be spoken aloud rather than read. "
        )
    else:
        instructions += (
            "Give a real, informative answer — three to five sentences for "
            "ordinary questions, since it will be spoken aloud rather than "
            "read. "
        )

    instructions += (
        "Do not use markdown, headings, bullets, citations, or special "
        "formatting, since this is spoken aloud, not read. Be honest when "
        "you're uncertain — say so plainly rather than padding a vague "
        "answer with confidence; a crisp 'I don't know' beats a hedge. "
        "Use your tools when a question needs live or current "
        "information, such as weather or recent events. "
        "The microphone only stays open for a reply when your response "
        "ends in a literal question mark, so whenever you want the user "
        "to answer something or are inviting them to ask for more, phrase "
        "it as a direct question ending in '?' — never as a statement "
        "like 'if you want, I can...' or 'let me know if...', since "
        "those won't be heard."
    )

    weather = hud_stats.get_weather_stats()

    if weather.get("temp_f") is not None:
        instructions += (
            f"\n\nCurrent weather at home: {weather['temp_f']}°F, "
            f"{weather['condition']}, today's high {weather['high_f']}, "
            f"low {weather['low_f']}, rain chance {weather['precip_chance']}%. "
            "Answer questions about the current or today's weather at home "
            "directly from this instead of calling the weather tool — only "
            "call the tool for a different city or for tomorrow's forecast."
        )

    memory_block = memory_store.build_memory_context_block()

    if memory_block:
        instructions += (
            "\n\nYou have access to memory of this conversation and prior "
            "remembered facts. Use it naturally when relevant, but don't "
            "mention that you have a memory system.\n\n" + memory_block
        )

    return instructions, max_tokens


# If Atlas asks a clarifying question, keep listening instead of going back
# to idle and forcing the user to say the wake word again just to answer.
# Capped so a model that keeps asking questions can't loop forever.
MAX_FOLLOW_UP_ROUNDS = 3

SENTENCE_SPLIT_RE = re.compile(r"([.!?]+)(\s+)")

# The built-in web_search tool makes the model cite source URLs at the end
# of answers. Nobody wants "h t t p s colon slash slash..." read aloud —
# strip URLs (and the parenthetical/bracketed source framing around them)
# from anything spoken or displayed. The raw answer with the citation
# still exists in the model's own conversation context, so follow-up
# questions that reference the source keep working.
URL_PATTERN = re.compile(
    r"[\(\[]?\s*(?:source|via|from|read more(?: at)?)?\s*:?\s*"
    r"(?:https?://|www\.)\S+[\)\]]?",
    re.IGNORECASE,
)


def strip_spoken_urls(text):
    """Removes URLs and their 'source:' framing from text meant for TTS or
    the HUD transcript. Collapses any leftover doubled whitespace and
    dangling empty parentheses."""
    cleaned = URL_PATTERN.sub("", text)
    cleaned = re.sub(r"\(\s*\)|\[\s*\]", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.!?,;:])", r"\1", cleaned)
    return cleaned.strip()


def _pop_complete_sentences(buffer):
    """Extracts complete sentences (ending in . ! or ? followed by
    whitespace) from the front of buffer. Returns (sentences, remaining)."""
    sentences = []

    while True:
        match = SENTENCE_SPLIT_RE.search(buffer)

        if not match:
            break

        end = match.end()
        sentence = buffer[:end].strip()

        if sentence:
            sentences.append(sentence)

        buffer = buffer[end:]

    return sentences, buffer


TOOL_ACTIVITY_LABELS = {
    "get_weather": "CHECKING WEATHER",
}


def _set_activity_label(label):
    """Lets the HUD show what's actually happening during a tool call
    instead of a generic THINKING label. Best-effort — a failed request
    here shouldn't interrupt answering the question."""
    try:
        requests.post(f"{HUB}/activity", json={"label": label}, timeout=3)
    except requests.RequestException as error:
        print("Activity label update failed:", error, flush=True)


def _stream_answer_sentences(
    question, instructions, max_tokens, client, sentence_queue, stop_event
):
    """Producer: streams the model's answer, pushing complete sentences onto
    sentence_queue as they arrive so a consumer can start speaking the first
    one before the rest has even finished generating. Falls back to a real
    tool-call round trip when needed, streaming that continuation the same
    way. Returns (full_text, total_input_tokens, total_output_tokens)."""
    full_text = ""
    buffer = ""
    total_input_tokens = 0
    total_output_tokens = 0

    with client.responses.stream(
        model=MODEL_NAME,
        reasoning={"effort": "none"},
        instructions=instructions,
        input=question,
        tools=ai_tools.TOOLS,
        max_output_tokens=max_tokens
    ) as stream:
        for event in stream:
            if stop_event.is_set():
                break

            if event.type == "response.output_text.delta":
                buffer += event.delta
                full_text += event.delta
                sentences, buffer = _pop_complete_sentences(buffer)

                for sentence in sentences:
                    sentence_queue.put(sentence)

        response = stream.get_final_response()

    total_input_tokens += int(getattr(response.usage, "input_tokens", 0) or 0)
    total_output_tokens += int(getattr(response.usage, "output_tokens", 0) or 0)

    function_calls = [
        item for item in response.output
        if getattr(item, "type", None) == "function_call"
    ]

    if function_calls and not stop_event.is_set():
        call = function_calls[0]
        arguments = json.loads(call.arguments)

        _set_activity_label(TOOL_ACTIVITY_LABELS.get(call.name, "USING TOOLS"))
        result = ai_tools.run_tool_call(call.name, arguments)
        _set_activity_label(None)

        with client.responses.stream(
            model=MODEL_NAME,
            reasoning={"effort": "none"},
            instructions=instructions,
            previous_response_id=response.id,
            input=[
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": result,
                }
            ],
            tools=ai_tools.TOOLS,
            max_output_tokens=max_tokens
        ) as stream2:
            for event in stream2:
                if stop_event.is_set():
                    break

                if event.type == "response.output_text.delta":
                    buffer += event.delta
                    full_text += event.delta
                    sentences, buffer = _pop_complete_sentences(buffer)

                    for sentence in sentences:
                        sentence_queue.put(sentence)

            response2 = stream2.get_final_response()

        total_input_tokens += int(
            getattr(response2.usage, "input_tokens", 0) or 0
        )
        total_output_tokens += int(
            getattr(response2.usage, "output_tokens", 0) or 0
        )

    remainder = buffer.strip()

    if remainder:
        sentence_queue.put(remainder)

    sentence_queue.put(None)

    return full_text.strip(), total_input_tokens, total_output_tokens


def answer_text_only(question):
    """Non-streaming, non-spoken answer for the phone link. Same model,
    instructions, budget guard, and memory as the voice path — just
    returns text. Costs the same per-question tokens as a voice question
    (on-demand only, never continuous)."""
    usage = load_usage()

    if usage["spent_usd"] + NEXT_REQUEST_RESERVE_USD > MONTHLY_LIMIT_USD:
        raise BudgetExceeded("Monthly API budget reached.")

    client = OpenAI(api_key=load_api_key(), max_retries=0, timeout=25.0)
    instructions, max_tokens = build_instructions_and_limits()

    response = client.responses.create(
        model=MODEL_NAME,
        reasoning={"effort": "none"},
        instructions=instructions,
        input=question,
        tools=ai_tools.TOOLS,
        max_output_tokens=max_tokens,
    )

    # Resolve a single tool call if the model made one (weather etc.).
    function_calls = [
        item for item in response.output
        if getattr(item, "type", None) == "function_call"
    ]

    input_tokens = int(getattr(response.usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(response.usage, "output_tokens", 0) or 0)

    if function_calls:
        call = function_calls[0]
        result = ai_tools.run_tool_call(call.name, json.loads(call.arguments))
        response = client.responses.create(
            model=MODEL_NAME,
            reasoning={"effort": "none"},
            instructions=instructions,
            previous_response_id=response.id,
            input=[{"type": "function_call_output", "call_id": call.call_id, "output": result}],
            tools=ai_tools.TOOLS,
            max_output_tokens=max_tokens,
        )
        input_tokens += int(getattr(response.usage, "input_tokens", 0) or 0)
        output_tokens += int(getattr(response.usage, "output_tokens", 0) or 0)

    cost = input_tokens * INPUT_PRICE_PER_TOKEN + output_tokens * OUTPUT_PRICE_PER_TOKEN
    usage["spent_usd"] += cost
    usage["requests"] += 1
    save_usage(usage)

    answer = strip_spoken_urls(response.output_text.strip())

    if answer:
        memory_store.record_turn(question, answer)

    return answer or "I couldn't generate an answer."


def ask_and_speak_streaming(question, model):
    """Streams the model's answer and speaks each sentence as soon as it's
    ready instead of waiting for the whole answer to finish generating —
    same total tokens as the non-streaming path, just delivered (and
    spoken) incrementally. Returns (answer, interrupted)."""
    usage = load_usage()
    spent = usage["spent_usd"]

    print(
        f"Local API spending for {usage['month']}: "
        f"${spent:.6f} of ${MONTHLY_LIMIT_USD:.2f}"
    )

    if spent + NEXT_REQUEST_RESERVE_USD > MONTHLY_LIMIT_USD:
        raise BudgetExceeded(
            "The local monthly API spending limit has been reached."
        )

    client = OpenAI(api_key=load_api_key(), max_retries=0, timeout=20.0)
    instructions, max_tokens = build_instructions_and_limits()

    sentence_queue = queue.Queue()
    interrupt_event = threading.Event()

    result = {
        "full_text": "",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "error": None,
    }

    def produce():
        try:
            full_text, input_tokens, output_tokens = _stream_answer_sentences(
                question, instructions, max_tokens, client,
                sentence_queue, interrupt_event
            )
            result["full_text"] = full_text
            result["total_input_tokens"] = input_tokens
            result["total_output_tokens"] = output_tokens
        except Exception as error:
            result["error"] = error
            sentence_queue.put(None)

    producer_thread = threading.Thread(target=produce, daemon=True)
    producer_thread.start()

    # Spans the whole streamed answer rather than being restarted per
    # sentence, so there's no gap between chunks where a barge-in wouldn't
    # be heard.
    barge_stop_event = threading.Event()
    barge_result = {"interrupted": False}

    def watch_for_barge_in():
        if listen_for_barge_in(model, barge_stop_event):
            barge_result["interrupted"] = True
            interrupt_event.set()

            # A single /interrupt call can race Piper's synthesis time
            # (measured 0.3-1.3s+ per sentence) — if the wake phrase lands
            # while the current sentence is still being synthesized, aplay
            # hasn't started yet, /interrupt finds nothing to kill, and
            # that whole sentence would otherwise play out in full. Retry
            # for a couple seconds so the moment playback actually starts,
            # it still gets caught.
            deadline = time.monotonic() + 2.5

            while time.monotonic() < deadline:
                try:
                    response = requests.post(f"{HUB}/interrupt", timeout=3)

                    if response.json().get("interrupted"):
                        break
                except requests.RequestException as error:
                    print("Interrupt request failed:", error, flush=True)
                    break

                time.sleep(0.15)

    watcher_thread = threading.Thread(target=watch_for_barge_in, daemon=True)
    watcher_thread.start()

    try:
        while True:
            try:
                sentence = sentence_queue.get(timeout=30)
            except queue.Empty:
                print(
                    "Sentence stream timed out waiting for the next chunk.",
                    flush=True
                )
                break

            if sentence is None:
                break

            if barge_result["interrupted"]:
                print("Skipping remaining sentence (barge-in):", sentence, flush=True)
                continue

            sentence = strip_spoken_urls(sentence)

            if sentence:
                speak(sentence)
    finally:
        barge_stop_event.set()
        watcher_thread.join(timeout=2)

    producer_thread.join(timeout=5)

    if result["error"] is not None:
        raise result["error"]

    answer = result["full_text"]

    request_cost = (
        result["total_input_tokens"] * INPUT_PRICE_PER_TOKEN
        + result["total_output_tokens"] * OUTPUT_PRICE_PER_TOKEN
    )

    usage["spent_usd"] += request_cost
    usage["requests"] += 1
    save_usage(usage)

    print(
        f"Tokens: {result['total_input_tokens']} input, "
        f"{result['total_output_tokens']} output"
    )
    print(f"Estimated request cost: ${request_cost:.6f}")
    print(f"Local monthly total: ${usage['spent_usd']:.6f}")

    if not answer:
        answer = "I was unable to generate an answer."

    memory_store.record_turn(question, answer)

    return answer, barge_result["interrupted"]


def _answer_and_speak(text, model):
    """Runs the ask-AI-and-speak-it sequence for one question, streaming the
    answer so speech can start on the first sentence instead of waiting for
    the whole response. Returns (answer, interrupted)."""
    answer, interrupted = ask_and_speak_streaming(text, model)

    print("A.T.L.A.S.:", answer)
    log_qa(text, strip_spoken_urls(answer))

    return answer, interrupted


def _classify_intent(text):
    """Coarse deterministic intent label for the log — mirrors the
    dispatch priority in _handle_turn_body. Zero tokens."""
    normalized = _normalize_phrase(text)

    if is_vision_command(text):
        return "vision"
    if parse_gallery_search_query(text) or parse_image_search_query(text):
        return "image_search"
    if parse_timer_set_command(text) or normalized in TIMER_CANCEL_PHRASES \
            or normalized in TIMER_CHECK_PHRASES:
        return "timer"
    if parse_focus_start_command(text) is not None or normalized in FOCUS_END_PHRASES:
        return "focus"
    if normalized in WAKE_PC_PHRASES:
        return "wake_pc"
    if normalized in DIAGNOSTICS_PHRASES:
        return "diagnostics"
    if normalized in SYSTEM_HEALTH_PHRASES:
        return "system_health"
    if is_network_devices_command(normalized) or normalized in SECURE_NETWORK_PHRASES:
        return "network"
    if is_enroll_face_command(normalized) or normalized in INTRUDER_QUERY_PHRASES:
        return "security"
    if normalized in NEWS_PHRASES or normalized in BRIEFING_PHRASES:
        return "briefing"
    if memory_store.parse_note_command(text) or memory_store.is_read_notes_command(text):
        return "notes"
    if parse_reminder_command(text):
        return "reminder"
    if memory_store.parse_remember_command(text):
        return "memory"
    if parse_instant_answer(text) is not None:
        return "instant"
    return "ai_question"


_current_turn = None


def _log_transcript(text):
    """Records transcript, mic level, and classified intent on the current
    turn — called right after each transcription."""
    if _current_turn is None:
        return

    _current_turn.set(
        transcript=text,
        audio_rms=_last_audio_rms,
        intent=_classify_intent(text) if text else "no_speech",
    )


def handle_turn(model):
    """Logging wrapper: every turn is recorded to the persistent logbook
    (wake confidence, mic, RMS, transcript, intent, latency, errors,
    outcome) regardless of which branch handled it or whether it raised."""
    global _current_turn
    _current_turn = logbook.start_turn()
    _current_turn.set(microphone=MIC_DEVICE, output_device="piper/hdmi1")
    outcome = "ok"

    try:
        _handle_turn_body(model)
    except Exception as error:
        _current_turn.add_error(f"{type(error).__name__}: {error}")
        outcome = "error"
        raise
    finally:
        if _current_turn.data.get("intent") is None:
            _current_turn.set(intent="no_speech")
        _current_turn.finish(outcome)
        _current_turn = None


def _handle_turn_body(model):
    try:
        # A wake-up always brings the screen back from "go dark" —
        # idempotent and cheap when it wasn't dark.
        _set_screen_dark(False)

        # Camera gate: one verify per lapsed validity window, only on
        # activation — the camera never runs continuously. Unverified
        # turns still proceed, but restricted to local basics.
        restricted = False

        if (
            camera_gate.is_available()
            and camera_gate.is_enabled()
            and not camera_gate.is_verification_current()
        ):
            set_face("thinking")
            speak("One moment — verifying.")
            outcome = camera_gate.verify()

            if outcome == "unauthorized":
                restricted = True
                speak(
                    "I don't recognize you, so I'm in restricted mode. "
                    "Local commands only."
                )
            elif outcome == "no_face":
                restricted = True
                speak(
                    "I can't see anyone at the camera, so I'm in "
                    "restricted mode for now."
                )

        if restricted:
            set_face("listening")
            record_audio()
            set_face("thinking")
            text = transcribe_audio(model)
            _log_transcript(text)

            if not text:
                speak("I didn't catch that.")
                return

            answer = _handle_restricted_turn(text)
            log_qa(f"[unverified] {text}", answer)
            speak(answer)
            return

        maybe_speak_greeting()
        memory_store.mark_interaction_now()

        speak("Go ahead.")
        set_face("listening")
        record_audio()

        set_face("thinking")
        text = transcribe_audio(model)
        _log_transcript(text)

        print(f"{load_owner_name()}:", text)

        if not text:
            print(
                "No speech detected. Letting the user know before returning to wake mode.",
                flush=True
            )
            speak("I didn't catch that.")
            return

        if is_vision_command(text):
            run_vision_command()
            return

        gallery_query = parse_gallery_search_query(text)

        if gallery_query is not None:
            run_gallery_search_command(gallery_query)
            return

        image_query = parse_image_search_query(text)

        if image_query is not None:
            run_image_search_command(image_query)
            return

        if memory_store.is_forget_command(text):
            memory_store.clear_facts()
            answer = "Okay, I've cleared everything I remembered."
            log_qa(text, answer)
            speak(answer)
            return

        reminder = parse_reminder_command(text)

        if reminder is not None:
            delay_seconds, reminder_message = reminder
            memory_store.add_reminder(delay_seconds, reminder_message)
            answer = f"Got it, I'll remind you in {describe_delay(delay_seconds)}."
            log_qa(text, answer)
            speak(answer)
            return

        remembered_fact = memory_store.parse_remember_command(text)

        if remembered_fact is not None:
            memory_store.add_fact(remembered_fact)
            answer = "Got it, I'll remember that."
            log_qa(text, answer)
            speak(answer)
            return

        normalized_phrase = _normalize_phrase(text)

        pc_search = parse_pc_search_command(text)

        if pc_search is not None:
            set_face("thinking")
            answer = pc_control.youtube_search(pc_search)
            log_qa(text, answer)
            speak(answer)
            return

        pc_answer = _pc_dispatch(normalized_phrase)

        if pc_answer is not None:
            log_qa(text, pc_answer)
            speak(pc_answer)
            return

        if normalized_phrase in WOL_DIAGNOSE_PHRASES:
            answer = run_wol_diagnose_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in WAKE_PC_PHRASES:
            answer = run_wake_pc_command()
            log_qa(text, answer)
            speak(answer)
            return

        timer_seconds = parse_timer_set_command(text)

        if timer_seconds is not None:
            answer = run_timer_set_command(timer_seconds)
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in TIMER_CANCEL_PHRASES:
            answer = run_timer_cancel_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in TIMER_CHECK_PHRASES:
            answer = run_timer_check_command()
            log_qa(text, answer)
            speak(answer)
            return

        focus_minutes = parse_focus_start_command(text)

        if focus_minutes is not None:
            answer = run_focus_start_command(focus_minutes)
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in FOCUS_END_PHRASES:
            answer = run_focus_end_command()
            log_qa(text, answer)
            speak(answer)
            return

        note_text = memory_store.parse_note_command(text)

        if note_text is not None:
            memory_store.add_note(note_text)
            answer = "Noted."
            log_qa(text, answer)
            speak(answer)
            return

        if memory_store.is_read_notes_command(text):
            answer = run_read_notes_command()
            log_qa(text, answer)
            speak(answer)
            return

        if memory_store.is_clear_notes_command(text):
            memory_store.clear_notes()
            answer = "Okay, your notes are cleared."
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in NEWS_PHRASES:
            set_face("thinking")
            answer = briefing.build_news_brief()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in PRINT_ETA_PHRASES:
            answer = run_print_eta_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in STAND_DOWN_PHRASES:
            try:
                requests.post(f"{HUB}/stand_down", timeout=5)
                answer = "Standing down."
            except requests.RequestException:
                answer = "I couldn't reach the alert system."
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in SCREEN_DARK_PHRASES:
            answer = "Going dark." if _set_screen_dark(True) else "I couldn't reach the screen."
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in SCREEN_WAKE_PHRASES:
            answer = "Screen's back." if _set_screen_dark(False) else "I couldn't reach the screen."
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in PHONE_PHRASES:
            answer = run_phone_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in INTERNET_CHECK_PHRASES:
            set_face("thinking")
            answer = run_internet_check_command()
            log_qa(text, answer)
            speak(answer)
            return

        if is_enroll_face_command(normalized_phrase):
            answer = run_enroll_face_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in GATE_ON_PHRASES:
            camera_gate.set_enabled(True)
            answer = "Camera gate on. I'll verify faces on wake."
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in GATE_OFF_PHRASES:
            camera_gate.set_enabled(False)
            answer = "Camera gate off."
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in INTRUDER_QUERY_PHRASES:
            answer = run_intruder_query_command()
            log_qa(text, answer)
            speak(answer)
            return

        if is_network_devices_command(normalized_phrase):
            answer = run_network_devices_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in DIAGNOSTICS_PHRASES:
            set_face("thinking")
            answer = diagnostics.build_diagnostics_report()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in LOG_QUERY_PHRASES:
            answer = run_log_query_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in EMERGENCY_SHUTDOWN_PHRASES:
            answer = run_emergency_shutdown_command(model)
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase == "cancel shutdown" or normalized_phrase == "abort shutdown":
            import emergency
            answer = "Shutdown cancelled." if emergency.cancel_shutdown() else "There's no shutdown to cancel."
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in PRINTER_EMERGENCY_PHRASES:
            import emergency
            answer = "Print paused." if emergency.pause_printer() else "I couldn't reach the printer."
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in CHIEF_OF_STAFF_PHRASES:
            set_face("thinking")
            answer = run_chief_of_staff_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in SYSTEM_HEALTH_PHRASES:
            set_face("thinking")
            speak("Running a full system health sweep. One moment.")
            answer = run_system_health_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in SECURE_NETWORK_PHRASES:
            set_face("thinking")
            answer = run_secure_network_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in BRIEFING_PHRASES:
            set_face("thinking")
            answer = briefing.build_briefing_text()
            briefing.mark_briefed_today()
            log_qa(text, answer)
            speak(answer)
            return

        instant_answer = parse_instant_answer(text)

        if instant_answer is not None:
            print("A.T.L.A.S. instant answer:", instant_answer, flush=True)
            log_qa(text, instant_answer)
            speak(instant_answer)
            return

        local_answer = handle_local_command(text, model)

        if local_answer is not None:
            print("A.T.L.A.S. local command:", local_answer)
            log_qa(text, local_answer)
            speak(local_answer)
            return

        answer, interrupted = _answer_and_speak(text, model)

        if interrupted:
            print("Barge-in: cutting turn short to listen again.", flush=True)
            handle_turn(model)
            return

        follow_ups_remaining = MAX_FOLLOW_UP_ROUNDS

        while answer.strip().endswith("?") and follow_ups_remaining > 0:
            follow_ups_remaining -= 1
            print(
                "Atlas asked a follow-up question — staying in listening mode.",
                flush=True
            )
            set_face("listening")
            record_audio()

            set_face("thinking")
            text = transcribe_audio(model)

            if not text:
                speak("I didn't catch that.")
                break

            answer, interrupted = _answer_and_speak(text, model)

            if interrupted:
                print("Barge-in: cutting turn short to listen again.", flush=True)
                handle_turn(model)
                return

    except BudgetExceeded as error:
        print("Budget protection:", error)
        speak(
            "My monthly online answer budget has been reached. "
            "My local features still work."
        )

    except subprocess.CalledProcessError as error:
        print("Microphone recording failed:", error)

        try:
            speak("I could not access the microphone.")
        except Exception:
            pass

    except Exception as error:
        print("Robot error:", type(error).__name__, error)

        try:
            speak("I ran into an error while answering.")
        except Exception:
            pass

    finally:
        set_face("happy")


def main():
    handle_turn(Model(MODEL_PATH))


if __name__ == "__main__":
    main()
