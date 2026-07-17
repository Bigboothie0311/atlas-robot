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
    """Guided enrollment: several captures with spoken prompts, then a
    fresh LBPH model. Owner-only in practice — re-enrollment on an
    existing model only happens inside a verified (full-access) turn."""
    if camera_gate.cv2 is None:
        return "My face recognition libraries aren't installed."

    speak("Alright, look at the camera and hold still.")

    saved = 0
    attempts = 0
    prompts = {
        3: "Turn your head slightly to the left.",
        5: "Now slightly to the right.",
        7: "Almost done, look straight at me.",
    }

    while saved < camera_gate.ENROLL_FRAME_COUNT and attempts < 16:
        if saved in prompts:
            speak(prompts.pop(saved))

        if camera_gate.enroll_frame(saved):
            saved += 1

        attempts += 1

    if saved < 4:
        return (
            "I couldn't see your face clearly enough to learn it. "
            "Check the lighting and try again."
        )

    count = camera_gate.train_model()
    camera_gate.mark_verified()
    return f"Done — your face is enrolled from {count} captures. You're my authorized user now."


def run_intruder_query_command():
    """Yes/no + count, and puts the evidence photos on the HUD."""
    intruders = camera_gate.unreviewed_intruders()

    if not intruders:
        return "No. Nobody unauthorized tried to use me."

    count = len(intruders)
    word = "person" if count == 1 else "people"

    try:
        requests.post(f"{HUB}/show_intruders", timeout=10)
    except requests.RequestException as error:
        print("show_intruders request failed:", error, flush=True)

    camera_gate.mark_intruders_reviewed()
    return (
        f"Yes — {count} unauthorized {word} tried to use me. "
        "Their photos are on screen now."
    )


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

    return (
        "You're not my authorized user, so I'm limited to local basics — "
        "time, date, timers, and reminders."
    )


NETWORK_DEVICES_PHRASES = {
    "what devices are on the network",
    "what devices are on my network",
    "what's on my network",
    "whats on my network",
    "what is on my network",
    "list network devices",
    "list the network devices",
    "show network devices",
    "show me the network devices",
    "who's on my network",
    "whos on my network",
    "who is on my network",
    "network devices",
    "scan the network",
}


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
        f"You are A.T.L.A.S., {owner_name}'s desk assistant — sharp, a "
        f"little dry, and not a corporate script. Today's date is {today}. "
        "Answer the way a smart friend who actually knows the answer would, "
        "not a customer-service bot. Skip filler like 'I'd be happy to "
        "help', 'great question', or 'I understand your concern' — just "
        "answer. A bit of wit is welcome when it genuinely fits, never at "
        "the expense of actually answering. If asked for a recommendation "
        "or opinion, give a real one instead of hedging with 'it depends' "
        "— pick one, note a caveat after if it matters. "
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
        "you're uncertain — don't pad a wrong or vague answer with "
        "confidence. Use your tools when a question needs live or current "
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


def handle_turn(model):
    try:
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

        if normalized_phrase in ENROLL_FACE_PHRASES:
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

        if normalized_phrase in NETWORK_DEVICES_PHRASES:
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
