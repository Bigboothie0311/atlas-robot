import array
import json
import math
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
    """Runs only during speak_with_barge_in's TTS window, when
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

            accepted, utterance_peak_rms, partial_hits = wake_detection.check_wake_phrase(
                recognizer, audio_data, utterance_peak_rms, partial_hits
            )

            if accepted:
                print("Barge-in wake phrase detected.", flush=True)
                return True

        return False
    finally:
        wake_detection.stop_recorder(recorder)


def speak_with_barge_in(text, model):
    """Speaks text aloud while concurrently listening for the wake word.
    If heard, interrupts playback immediately via the hub and returns True
    so the caller can jump straight into a new turn."""
    stop_event = threading.Event()
    result = {"interrupted": False}

    def watch():
        if listen_for_barge_in(model, stop_event):
            result["interrupted"] = True

            try:
                requests.post(f"{HUB}/interrupt", timeout=3)
            except requests.RequestException as error:
                print("Interrupt request failed:", error, flush=True)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()

    try:
        speak(text)
    finally:
        stop_event.set()
        watcher.join(timeout=2)

    return result["interrupted"]


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

    if weather.get("temp_f") is None:
        if MORNING_HOUR_START <= hour < MORNING_HOUR_END:
            greeting = f"Good morning, {owner_name}."
        else:
            greeting = f"Welcome back, {owner_name}."
    elif MORNING_HOUR_START <= hour < MORNING_HOUR_END:
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



def ask_atlas(question):
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

    client = OpenAI(
        api_key=load_api_key(),
        max_retries=0,
        timeout=20.0
    )

    today = datetime.now().strftime("%Y-%m-%d")
    owner_name = load_owner_name()
    quiet = _in_quiet_hours()
    max_tokens = 120 if quiet else 300

    instructions = (
        f"You are A.T.L.A.S., {owner_name}'s helpful desk robot assistant. "
        f"Today's date is {today}. "
        "Answer naturally in plain spoken English. "
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
        "Do not use markdown, headings, bullets, citations, or "
        "special formatting. Be friendly, useful, direct, and honest "
        "when uncertain. Use your tools when a question needs live or "
        "current information, such as weather or recent events. "
        "The microphone only stays open for a reply when your response "
        "ends in a literal question mark, so whenever you want the user "
        "to answer something or are inviting them to ask for more, phrase "
        "it as a direct question ending in '?' — never as a statement "
        "like 'if you want, I can...' or 'let me know if...', since "
        "those won't be heard."
    )

    memory_block = memory_store.build_memory_context_block()

    if memory_block:
        instructions += (
            "\n\nYou have access to memory of this conversation and prior "
            "remembered facts. Use it naturally when relevant, but don't "
            "mention that you have a memory system.\n\n" + memory_block
        )

    response = client.responses.create(
        model=MODEL_NAME,
        reasoning={"effort": "none"},
        instructions=instructions,
        input=question,
        tools=ai_tools.TOOLS,
        max_output_tokens=max_tokens
    )

    total_input_tokens = int(
        getattr(response.usage, "input_tokens", 0) or 0
    )
    total_output_tokens = int(
        getattr(response.usage, "output_tokens", 0) or 0
    )

    function_calls = [
        item for item in response.output
        if getattr(item, "type", None) == "function_call"
    ]

    if function_calls:
        call = function_calls[0]
        arguments = json.loads(call.arguments)
        result = ai_tools.run_tool_call(call.name, arguments)

        response = client.responses.create(
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
        )

        total_input_tokens += int(
            getattr(response.usage, "input_tokens", 0) or 0
        )
        total_output_tokens += int(
            getattr(response.usage, "output_tokens", 0) or 0
        )

    answer = response.output_text.strip()

    # Conservatively charge all input tokens at the full uncached rate.
    request_cost = (
        total_input_tokens * INPUT_PRICE_PER_TOKEN
        + total_output_tokens * OUTPUT_PRICE_PER_TOKEN
    )

    usage["spent_usd"] += request_cost
    usage["requests"] += 1
    save_usage(usage)

    print(
        f"Tokens: {total_input_tokens} input, "
        f"{total_output_tokens} output"
    )
    print(
        f"Estimated request cost: ${request_cost:.6f}"
    )
    print(
        f"Local monthly total: ${usage['spent_usd']:.6f}"
    )

    if not answer:
        return "I was unable to generate an answer."

    memory_store.record_turn(question, answer)

    return answer


# If Atlas asks a clarifying question, keep listening instead of going back
# to idle and forcing the user to say the wake word again just to answer.
# Capped so a model that keeps asking questions can't loop forever.
MAX_FOLLOW_UP_ROUNDS = 3


def _answer_and_speak(text, model):
    """Runs the ask-AI-and-speak-it sequence for one question. Returns
    (answer, interrupted) so callers can react to a barge-in."""
    answer = ask_atlas(text)

    print("A.T.L.A.S.:", answer)
    log_qa(text, answer)
    interrupted = speak_with_barge_in(answer, model)

    return answer, interrupted


def handle_turn(model):
    try:
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

        remembered_fact = memory_store.parse_remember_command(text)

        if remembered_fact is not None:
            memory_store.add_fact(remembered_fact)
            answer = "Got it, I'll remember that."
            log_qa(text, answer)
            speak(answer)
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
