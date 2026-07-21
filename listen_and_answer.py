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
import capabilities
import chance
import countdown
import diagnostics
import hud_stats
import instagram_stats
import interaction_control
import logbook
import macros
import mic_arbiter
import pc_control
import pc_power
import memory_store
import speech_repair
import stream_resilience
import unit_convert
import web_search
import wake_detection


HUB = "http://127.0.0.1:5051"
ATLAS_HUB = "http://127.0.0.1:5050"
MODEL_PATH = "/home/atlas/atlas-robot/models/vosk-model-small-en-us-0.15"
AUDIO_PATH = "/tmp/atlas-listen.wav"
WHISPER_CLI = "/home/atlas/atlas-robot/tools/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL = "/home/atlas/atlas-robot/tools/whisper.cpp/models/ggml-base.en.bin"
WHISPER_OUTPUT = "/tmp/atlas-whisper.txt"
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


def _forward_agent_event_to_hud(event):
    """Best-effort bridge from the agent process to robot-hub state."""
    try:
        requests.post(
            f"{HUB}/agent/event",
            json={
                "name": event.name,
                "source": event.source,
                "data": event.data,
                "event_id": event.event_id,
                "created_at": event.created_at,
            },
            timeout=1,
        )
    except requests.RequestException as error:
        print(
            "Agent HUD event update failed:",
            error,
            flush=True,
        )


def _build_agent_voice_runtime_owner():
    """Build the lightweight owner; its real runtime remains lazy."""
    from atlas_agent.runtime_factory import build_pc_agent_runtime
    from atlas_agent.voice_runtime_owner import VoiceRuntimeOwner

    def build_bundle():
        bundle = build_pc_agent_runtime(
            openai_client=OpenAI(
                api_key=load_api_key(),
                max_retries=0,
                timeout=25.0,
            ),
            model=MODEL_NAME,
            host="192.168.50.2",
            username="wesle",
            identity_file=(
                "/home/atlas/.ssh/atlas_pc_ed25519"
            ),
            approved_remote_roots=(
                r"C:\Users\wesle",
            ),
            staging_directory=(
                "/home/atlas/atlas-staging/incoming"
            ),
            mission_store_path=(
                USAGE_PATH.parent / "agent_missions.json"
            ),
            recordings_remote_root=(
                r"C:\Users\wesle\Videos\AtlasRecordings"
            ),
        )
        bundle.event_bus.subscribe(
            "*",
            _forward_agent_event_to_hud,
        )
        return bundle

    return VoiceRuntimeOwner(build_bundle)


ai_tools.configure_agent_runtime_owner_factory(
    _build_agent_voice_runtime_owner
)


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


def speak(text, play=True):
    """play=False synthesizes narration to a WAV without playing it over
    the Pi speaker or touching the HUD "talking" state, and returns the
    resulting wav_path (same host, so any process on the Pi can read it) —
    used by the self-showcase content pipeline. A plain speak(text) call
    right after still plays normally; there's no lingering mute state."""
    response = requests.post(
        f"{HUB}/speak",
        json={"text": text, "play": play},
        timeout=45
    )
    response.raise_for_status()
    return response.json().get("wav_path")


def cue_listening():
    """Plays the short system-ready earcon, with speech as a fallback.

    Best-effort end to end: if the earcon fails AND the voice fallback
    also fails (e.g. the hub is briefly overloaded right after a spoken
    verification message), the turn must still proceed to record_audio()
    rather than dying silently before the mic ever opens.
    """
    try:
        response = requests.post(f"{HUB}/listening_earcon", timeout=3)
        response.raise_for_status()
    except requests.RequestException as error:
        print("Listening earcon failed; using voice cue:", error, flush=True)

        try:
            speak("Go ahead.")
        except requests.RequestException as speak_error:
            print("Voice cue fallback also failed:", speak_error, flush=True)


def dismiss_current_interaction():
    """Silently stops harmless output and restores the idle HUD.

    This deliberately does not cancel timers, disarm security, clear
    intruder evidence, stop services, or change configuration.
    """
    for endpoint in ("/interrupt", "/dismiss"):
        try:
            response = requests.post(f"{HUB}{endpoint}", timeout=3)
            response.raise_for_status()
        except requests.RequestException as error:
            print(
                f"Interaction dismissal failed at {endpoint}:",
                error,
                flush=True,
            )


def _open_barge_in_recorder():
    return subprocess.Popen(
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


def listen_for_barge_in(model, stop_event):
    """Runs only during ask_and_speak_streaming's TTS window, when
    wake_listener.py's own mic loop has already released the device.
    Returns True if the wake phrase is verified before stop_event is
    set.

    Cooperates with mic_arbiter: if a tool needs the same physical mic
    mid-answer (e.g. camera.capture_clip's audio branch, so a
    self-recording actually gets Atlas's own narration instead of
    routinely falling back to muted), this closes its own arecord,
    confirms the release, and waits for the request to clear before
    reopening — rather than holding the device for the whole answer
    regardless of what else needs it."""
    recorder = _open_barge_in_recorder()
    recognizer = wake_detection.create_recognizer(model)
    utterance_peak_rms = 0
    partial_hits = 0

    try:
        while not stop_event.is_set():
            if mic_arbiter.yield_is_requested():
                wake_detection.stop_recorder(recorder)
                recorder = None
                mic_arbiter.confirm_released()

                while mic_arbiter.yield_is_requested() and not stop_event.is_set():
                    time.sleep(0.1)

                if stop_event.is_set():
                    return False

                recorder = _open_barge_in_recorder()
                continue

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
        if recorder is not None:
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
# The room can sit above that fixed floor, which previously made every turn
# hit the full eight-second cap. After real speech creates a much higher peak,
# treat audio below this fraction of that peak as trailing silence. The cap
# prevents a single loud click from making normal speech look silent.
RECORD_SILENCE_PEAK_RATIO = 0.25
RECORD_MAX_SILENCE_RMS = 900
RECORD_CHUNK_BYTES = 4000


def _recording_speech_threshold(peak_rms):
    return min(
        RECORD_MAX_SILENCE_RMS,
        max(RECORD_MIN_SPEECH_RMS, peak_rms * RECORD_SILENCE_PEAK_RATIO),
    )


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
            speech_threshold = _recording_speech_threshold(peak_rms)

            if rms >= speech_threshold:
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

    captured_seconds = sum(len(chunk) for chunk in chunks) / 2 / 16000
    print(
        f"Captured {captured_seconds:.2f}s of command audio; "
        f"peak_rms={peak_rms:.0f}; "
        f"silence_threshold={_recording_speech_threshold(peak_rms):.0f}",
        flush=True,
    )


# Set by record_audio each turn so the logbook can capture the mic level
# even though record_audio's real job is writing the WAV.
_last_audio_rms = None
_last_transcription_alternatives = []


def _transcribe_with_whisper():
    """Higher-quality local command transcription. Vosk remains the fallback."""
    executable = Path(WHISPER_CLI)
    model = Path(WHISPER_MODEL)
    output = Path(WHISPER_OUTPUT)

    if not executable.exists() or not model.exists():
        return ""

    output.unlink(missing_ok=True)

    try:
        result = subprocess.run(
            [
                str(executable),
                "-m", str(model),
                "-f", AUDIO_PATH,
                "-l", "en",
                "-t", "4",
                "-nt",
                "-otxt",
                "-of", str(output.with_suffix("")),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as error:
        print("Whisper transcription failed:", error, flush=True)
        return ""

    if result.returncode != 0:
        print("Whisper transcription returned an error:", result.stderr[-500:], flush=True)
        return ""

    try:
        return output.read_text().strip()
    except OSError:
        return ""


def _transcribe_with_vosk(model):
    """Fast in-memory transcription used before spawning Whisper."""
    with wave.open(AUDIO_PATH, "rb") as audio:
        recognizer = KaldiRecognizer(model, audio.getframerate())
        recognizer.SetMaxAlternatives(5)

        while True:
            data = audio.readframes(4000)

            if not data:
                break

            recognizer.AcceptWaveform(data)

    result = json.loads(recognizer.FinalResult())
    alternatives = []

    for alternative in result.get("alternatives", []):
        candidate = str(alternative.get("text", "")).strip()

        if candidate and candidate not in alternatives:
            alternatives.append(candidate)

    primary = str(result.get("text", "")).strip()

    if primary and primary not in alternatives:
        alternatives.insert(0, primary)

    return alternatives


VOSK_FAST_PATH_INTENTS = frozenset({
    "instant",
    "storage",
    "diagnostics",
    "instagram_stats",
})


def _is_safe_vosk_fast_path(text):
    """Only bypasses Whisper for harmless deterministic local actions."""
    normalized = _normalize_phrase(text)
    return (
        interaction_control.is_safe_cancel_phrase(normalized)
        or _classify_intent(normalized) in VOSK_FAST_PATH_INTENTS
    )


def transcribe_audio(model):
    global _last_transcription_alternatives

    vosk_alternatives = _transcribe_with_vosk(model)
    vosk_text = vosk_alternatives[0] if vosk_alternatives else ""

    if vosk_text and _is_safe_vosk_fast_path(vosk_text):
        _last_transcription_alternatives = vosk_alternatives
        print("Vosk fast path heard:", vosk_text, flush=True)
        return vosk_text

    whisper_text = _transcribe_with_whisper()

    if whisper_text:
        _last_transcription_alternatives = [
            whisper_text,
            *(
                candidate for candidate in vosk_alternatives
                if candidate != whisper_text
            ),
        ]
        print("Whisper heard:", whisper_text, flush=True)
        return whisper_text

    print("Whisper unavailable; falling back to Vosk.", flush=True)
    _last_transcription_alternatives = vosk_alternatives
    return vosk_text



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

    # "screen" always means the PC monitor, never the room the Pi's own
    # camera looks at — route those to _pc_dispatch's screenshot handling
    # instead of hijacking them into a Pi selfie via run_vision_command().
    if "screen" in normalized.split():
        return False

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
OPEN_CODEX_PHRASES = {"open codex", "launch codex", "start codex"}
OPEN_TERMINAL_PHRASES = {
    "open terminal", "open the terminal", "launch terminal",
    "open windows terminal", "open powershell", "launch powershell",
    "open a terminal on my pc", "open my terminal",
}
OPEN_BROWSER_PHRASES = {
    "open my browser", "open the browser", "launch my browser",
    "open chrome", "launch chrome",
}
ACTIVE_WINDOW_PHRASES = {
    "what's focused on my pc", "whats focused on my pc",
    "what am i focused on", "what window is focused",
    "what app is focused on my pc", "what's active on my pc",
    "whats active on my pc",
}
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
    "take a picture of my screen", "take a picture of the pc screen",
    "take a photo of my screen", "take a photo of the pc screen",
    "take a screenshot of my screen", "take a screenshot",
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
    if normalized in OPEN_CODEX_PHRASES:
        return pc_control.open_codex()
    if normalized in OPEN_TERMINAL_PHRASES:
        return pc_control.open_terminal()
    if normalized in OPEN_BROWSER_PHRASES:
        return pc_control.open_browser()
    if normalized in ACTIVE_WINDOW_PHRASES:
        return pc_control.active_window()
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

_INTRUDER_QUERY_VERBS = {
    "any", "were", "was", "did", "do", "show", "check", "see", "have",
    "pull", "what", "who", "my", "review", "read", "give", "tell",
}


def is_intruder_query(normalized):
    """Word-based match for intruder/security-alert questions — the exact
    phrase set kept missing natural wordings ('intruder alerts', 'show me
    my alerts', 'did anyone break in'), so those leaked to the paid model
    which answered 'I don't have access to that.' Any short utterance
    mentioning intruders/unauthorized users, or 'alert(s)' with a query
    verb, routes to the local review command."""
    if normalized in INTRUDER_QUERY_PHRASES:
        return True

    words = set(normalized.split())

    if len(normalized.split()) > 10:
        return False

    if words & {"intruder", "intruders", "unauthorized"}:
        return True

    # "alert(s)" is only intruder-ish in a short command ("any alerts",
    # "show my alerts") or alongside a security-context word — never in a
    # long general question like "what causes a security alert in X".
    security_context = words & {
        "intruder", "unauthorized", "gone", "away",
        "someone", "anyone", "stranger",
    }
    if words & {"alert", "alerts"} and words & _INTRUDER_QUERY_VERBS:
        if len(normalized.split()) <= 5 or security_context:
            return True

    if "break in" in normalized or "broke in" in normalized:
        return True

    return False


_CLEAR_INTRUDER_VERBS = {"clear", "dismiss", "scrub", "wipe", "reset", "clean"}


def is_clear_intruder_alerts_command(normalized):
    """Owner shortcut to clear the pending intruder alert without sitting
    through the photo-by-photo review: 'clear intruder alerts', 'dismiss
    the intruder alert', 'clear security alerts'. Deletes the photographs
    and marks the records reviewed, but keeps the log entries so the
    intruder report is still available on request.

    Must be checked before is_intruder_query(), which also matches the
    word 'intruder' and would otherwise trigger the full review."""
    words = set(normalized.split())

    if not (words & _CLEAR_INTRUDER_VERBS):
        return False

    if len(normalized.split()) > 6:
        return False

    return bool(words & {"intruder", "intruders", "alert", "alerts"})


def run_enroll_face_command():
    """Guided seven-pose enrollment with a transactional model update.
    Re-enrollment on an existing model remains owner-only in the voice
    path; shell recovery is available through ``camera_gate.py enroll``.
    """
    if camera_gate.cv2 is None:
        return "My face recognition libraries aren't installed."

    def progress(step):
        pose = step.split(":", 1)[1] if step.startswith("pose:") else None
        prompt = camera_gate.ENROLL_POSE_PROMPTS.get(pose)

        if prompt:
            speak(prompt + " Hold that position.")

    count = camera_gate.enroll(progress=progress)

    if count == 0:
        return (
            "I couldn't collect enough clear angles to replace your "
            "enrollment. Your previous face model is still intact."
        )

    camera_gate.mark_verified()
    return (
        f"Done — your face is enrolled from {count} multi-angle captures. "
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


def run_clear_intruder_alerts_command():
    """Clears the pending intruder alert without the visual review: deletes
    the unreviewed photos and marks those records reviewed. The log entries
    survive, so 'were there any intruders' can still recap them later."""
    cleared = camera_gate.dismiss_intruder_alerts()

    if not cleared:
        return "There are no intruder alerts to clear."

    # Drop any photo the HUD is still holding and close the review layout.
    try:
        requests.post(f"{HUB}/security_review/close", timeout=5)
    except requests.RequestException:
        pass

    word = "alert" if cleared == 1 else "alerts"
    return (
        f"Cleared {cleared} intruder {word} without the review. The report "
        "is still on file if you want it later."
    )


# What a non-verified person may still do: strictly local, zero-token,
# no personal data, no web, no physical actuation beyond timers.
def _handle_restricted_turn(text):
    """Dispatch for unverified users.

    Returns ``None`` for a silent, harmless cancel; otherwise returns the
    spoken answer. Cancel is intentionally available before authorization.
    """
    normalized = _normalize_phrase(text)

    if interaction_control.is_safe_cancel_phrase(normalized):
        dismiss_current_interaction()
        return None

    instant = parse_instant_answer(text)

    if instant is not None:
        return instant

    timer_seconds = parse_timer_set_command(text)

    if timer_seconds is not None:
        return run_timer_set_command(timer_seconds)

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

# Spoken input almost never matches a phrase verbatim, so these two intents
# are detected by keyword rather than exact-set membership. That's the whole
# reason the earlier exact-match version fell through to the LLM ("I can't
# pull up the radar") — a live transcription of "hey can you pull the radar
# up" never equals any fixed string.

def _wants_weather_hud(normalized):
    """Full-screen weather + radar HUD intent. Returns 'open', 'close', or
    None. 'radar' unambiguously means this screen. A bare weather *question*
    ('what's the weather', 'will it rain') must still reach the get_weather
    tool, so plain 'weather' only opens the HUD alongside a show/surface verb."""
    has_radar = "radar" in normalized
    has_weather = "weather" in normalized

    if not (has_radar or has_weather):
        return None

    if any(word in normalized for word in ("close", "hide", "dismiss", "get rid", "take down")):
        return "close"

    if has_radar:
        return "open"

    surface_words = (
        "pull up", "pull the", "bring up", "show", "open", "display",
        "put up", "hud", "screen", "full", "overlay", "map", "forecast screen",
        "let me see", "let's see", "can i see", "give me",
    )
    if any(word in normalized for word in surface_words):
        return "open"
    return None


def _wants_brightness_change(normalized):
    """Quiet-hours brightness override. Returns 'boost', 'normal', or None.
    Gated on a brightness word so unrelated 'up/down' never trip it."""
    if "bright" not in normalized and "dim" not in normalized:
        return None

    # 'dim'/'dimmer' or an explicit down word -> back to normal dimming.
    if "dim" in normalized:
        return "normal"
    down_words = ("lower", " down", "normal", "decrease", "reduce", "back to normal", "restore")
    if any(word in normalized for word in down_words):
        return "normal"

    up_words = ("brighten", "brighter", "raise", " up", "increase", "boost", "full", "max", "brightest")
    if any(word in normalized for word in up_words):
        return "boost"

    # Bare 'brightness' with no direction — the showcase ask is "brightness"
    # meaning "make it brighter", so default to boosting.
    return "boost"


def _set_screen_dark(dark):
    try:
        requests.post(f"{HUB}/screen", json={"dark": dark}, timeout=5)
        return True
    except requests.RequestException as error:
        print("screen request failed:", error, flush=True)
        return False


def _set_weather_overlay(open_):
    try:
        requests.post(f"{HUB}/hud/weather_overlay", json={"open": open_}, timeout=5)
        return True
    except requests.RequestException as error:
        print("weather overlay request failed:", error, flush=True)
        return False


def _set_brightness_boost(boost):
    try:
        requests.post(f"{HUB}/hud/brightness_boost", json={"boost": boost}, timeout=5)
        return True
    except requests.RequestException as error:
        print("brightness boost request failed:", error, flush=True)
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


STATUS_REPORT_PHRASES = {
    "status report", "sitrep", "give me a status report", "system status",
    "full status", "report status", "situation report", "status",
}


def run_status_report_command():
    import jarvis
    return jarvis.status_report()


SELF_HEAL_PHRASES = {
    "heal yourself", "self heal", "fix yourself", "repair yourself",
    "run self healing", "check and heal", "heal", "recover yourself",
}


def run_self_heal_command():
    import self_healing
    return self_healing.heal_now()


GOODBYE_PHRASES = {
    "i'm leaving", "im leaving", "i am leaving", "i'm heading out",
    "im heading out", "i'm going out", "goodbye atlas", "i'm off",
    "im off", "see you later", "i'm leaving now", "im leaving now",
}


def run_goodbye_routine():
    """'I'm leaving' — shut down the PC, darken the HUD, and arm the
    camera gate so the next person to use ATLAS gets verified."""
    results = []

    # Arm face verification for whoever's next (you'll re-auth on return).
    camera_gate.arm_gate(reason="owner_left")

    # Darken the HUD.
    if _set_screen_dark(True):
        results.append("darkening the display")

    # Shut down the PC if reachable.
    if pc_control.is_configured():
        pc_result = pc_control.shutdown_pc()
        if "shut" in pc_result.lower() or "down" in pc_result.lower():
            results.append("shutting down your PC")

    if results:
        return "Take care. I'm " + " and ".join(results) + "."
    return "Take care. See you soon."


SKY_WATCH_PHRASES = {
    "sky watch", "what's up in the sky", "whats up in the sky",
    "what's in the sky", "whats in the sky", "sky report",
    "anything in the sky", "what's happening in the sky",
}
STARGAZING_PHRASES = {
    "is it good for stargazing", "how's stargazing tonight",
    "hows stargazing tonight", "can i see stars tonight", "stargazing",
}
METEOR_PHRASES = {
    "any meteor showers", "when's the next meteor shower",
    "whens the next meteor shower", "next meteor shower",
}
LAUNCH_PHRASES = {
    "when's the next rocket launch", "whens the next rocket launch",
    "next rocket launch", "any rocket launches", "upcoming launches",
}
MOON_PHRASES = {
    "what's the moon phase", "whats the moon phase", "what phase is the moon",
    "moon phase", "what's the moon doing", "whats the moon doing",
}


def run_sky_watch_command(kind):
    import sky_watch
    if kind == "summary":
        return sky_watch.spoken_summary()
    if kind == "stargazing":
        s = sky_watch.stargazing_tonight()
        if s["verdict"] == "unknown":
            return "I couldn't get tonight's cloud cover."
        return (f"Stargazing tonight looks {s['verdict']} — {s['cloud_cover']} percent "
                f"cloud cover, and the moon is {s['moon']['illumination_percent']} percent lit.")
    if kind == "meteor":
        m = sky_watch.next_meteor_shower()
        return (f"The {m['name']} meteor shower peaks on {m['date']}, "
                f"in {m['days_away']} days." if m else "I couldn't find a meteor shower.")
    if kind == "launch":
        launches = sky_watch.upcoming_launches(2)
        if not launches:
            return "I couldn't reach the launch schedule."
        return "Upcoming launches: " + "; ".join(l["name"] for l in launches) + "."
    if kind == "moon":
        mp = sky_watch.moon_phase()
        return f"The moon is a {mp['phase']}, {mp['illumination_percent']} percent illuminated."
    return "I couldn't read the sky."


TOOL_STATUS_PHRASES = {
    "check your tools", "check for tool updates", "what tools can you upgrade",
    "check your versions", "what version are your tools", "check for updates",
    "are your tools up to date",
}
TOOL_UPGRADE_PATTERN = re.compile(r"^(?:propose|check|upgrade) (?:an? )?(?:upgrade (?:for |to )?)?(whisper|whisper\.cpp|opencv|piper|vosk)(?: upgrade)?$")


def run_tool_status_command():
    import tool_manifest
    return tool_manifest.spoken_status()


def parse_tool_upgrade(text):
    m = TOOL_UPGRADE_PATTERN.match(_normalize_phrase(text))
    if not m:
        return None
    name = m.group(1)
    return "whisper.cpp" if name.startswith("whisper") else name


def run_tool_upgrade_proposal(tool):
    import tool_manifest
    return tool_manifest.propose(tool)


PROFILE_PATTERN = re.compile(
    r"^(?:activate |start |switch to |enter )?(work|design|game) mode$"
)


def parse_profile_command(text):
    m = PROFILE_PATTERN.match(_normalize_phrase(text))
    return m.group(1) if m else None


def run_profile_command(name):
    import pc_profiles

    def _set_focus(on):
        try:
            requests.post(f"{HUB}/focus" if on else f"{HUB}/focus/end",
                          json={"minutes": 60} if on else {}, timeout=5)
        except requests.RequestException:
            pass

    return pc_profiles.activate(name, set_focus=_set_focus)


MEMORY_QUERY_PATTERN = re.compile(r"^what do you (?:remember|know) about (.+)$")
FORGET_ABOUT_PATTERN = re.compile(r"^forget (?:that |about |everything about )(.+)$")
ADD_PRIORITY_PATTERN = re.compile(
    r"^(?:make|my|a) (?:top )?priority (?:is )?(.+)$|"
    r"^(?:make|set) (.+?) (?:a |my )?(?:top )?priority$|"
    r"^prioriti[sz]e (.+)$"
)
PRIORITIES_QUERY_PHRASES = {
    "what are my priorities", "what's my priority", "whats my priority",
    "list my priorities", "what are my top priorities",
}
TODAY_PHRASES = {
    "what's my day", "whats my day", "what does my day look like",
    "what's on today", "whats on today", "today", "my day",
    "what do i have today",
}


def parse_memory_query(text):
    m = MEMORY_QUERY_PATTERN.match(_normalize_phrase(text))
    return m.group(1).strip() if m else None


def parse_forget_about(text):
    normalized = _normalize_phrase(text)
    if memory_store.is_forget_command(text):  # "forget everything" handled elsewhere
        return None
    m = FORGET_ABOUT_PATTERN.match(normalized)
    return m.group(1).strip() if m else None


def parse_add_priority(text):
    m = ADD_PRIORITY_PATTERN.match(_normalize_phrase(text))
    if not m:
        return None
    return next((g for g in m.groups() if g), None)


def run_memory_query_command(topic):
    facts = memory_store.search_facts(topic)
    if not facts:
        return f"I don't have anything remembered about {topic}."
    return f"About {topic}, I remember: " + "; ".join(facts) + "."


def run_today_command():
    parts = []
    reminders = memory_store.load_reminders()
    if reminders:
        parts.append(f"{len(reminders)} reminder{'s' if len(reminders) != 1 else ''} scheduled")
    priorities = memory_store.get_priorities_summary()
    if priorities:
        parts.append("your priorities are " + "; ".join(priorities[:5]))
    notes = memory_store.load_notes()
    if notes:
        parts.append(f"{len(notes)} note{'s' if len(notes) != 1 else ''} saved")
    weather = hud_stats.get_weather_stats()
    if weather.get("temp_f") is not None:
        parts.insert(0, f"it's {weather['temp_f']} degrees and {weather['condition']}")
    if not parts:
        return "Your day looks clear — nothing I'm tracking."
    return "Today: " + ". ".join(p[0].upper() + p[1:] for p in parts) + "."


CONNECTION_PHRASES = {
    "check connections",
    "check my connections",
    "is everything connected",
    "are you connected",
    "connection status",
    "connection health",
    "check the connections",
    "how are your connections",
}


def run_connection_health_command():
    import connection_health
    return connection_health.spoken_report()


SIMULATE_PATTERNS = [
    re.compile(r"^what would happen if i (?:said|say|asked|ask you)\s+(.+)$"),
    re.compile(r"^simulate\s+(.+)$"),
    re.compile(r"^what would you do if i said\s+(.+)$"),
]


def parse_simulate_command(text):
    normalized = _normalize_phrase(text)
    for pattern in SIMULATE_PATTERNS:
        match = pattern.match(normalized)
        if match:
            return match.group(1).strip().strip('"').strip("'")
    return None


def run_simulate_command(target_phrase):
    """Command simulator — explains the action ATLAS WOULD take for a
    phrase, without executing it. Reads from the capability registry."""
    entry = capabilities.find_by_alias(_normalize_phrase(target_phrase))

    if entry is None:
        return (
            f"If you said '{target_phrase}', I wouldn't recognize it as a "
            "command, so I'd treat it as a general question and answer it."
        )

    parts = [f"If you said '{target_phrase}', I would: {entry['description']}"]

    if entry["requires"] == "pc":
        parts.append("This needs your PC")
    elif entry["requires"] == "phone":
        parts.append("This works from your phone")

    if entry["confirm"]:
        parts.append("and I'd ask you to confirm first before doing it")

    parts.append("I wouldn't actually do it just now — you asked me to simulate")
    return ". ".join(parts) + "."


CAPABILITIES_PHRASES = {
    "what can you control",
    "what can you do",
    "what are you able to do",
    "what can you help me with",
    "what commands do you have",
    "what are your commands",
    "list your commands",
    "what can i ask you",
    "what can i ask you to do",
}


def run_capabilities_command():
    return capabilities.describe_all()


DIAGNOSTICS_PHRASES = {
    "run diagnostics",
    "run a diagnostic",
    "run your diagnostics",
    "health check",
    "run a health check",
    "do a health check",
    "system health check",
    "run a system health check",
    "do a system health check",
    "check system health",
    "check your health",
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


STORAGE_QUERY_PHRASES = {
    "how much storage do you have free on your drive",
    "how much storage do you have free",
    "how much free storage do you have",
    "how much storage is free",
    "how much storage is left",
    "how much disk space is free",
    "how much free disk space do you have",
    "how much disk space do you have",
    "how much space is left",
    "how full is your drive",
    "how full is your disk",
    "check storage",
    "check disk space",
    "storage status",
    "disk space",
    "drive space",
}


def is_storage_query(text):
    """Recognizes read-only questions about this Pi's system drive."""
    normalized = _normalize_phrase(text)

    if normalized in STORAGE_QUERY_PHRASES:
        return True

    has_storage_subject = (
        "storage" in normalized
        or "disk space" in normalized
        or ("drive" in normalized and "space" in normalized)
        or (
            "disk" in normalized
            and any(term in normalized for term in (
                "free",
                "available",
                "left",
                "used",
                "usage",
                "full",
            ))
        )
    )
    asks_for_status = any(term in normalized for term in (
        "how much",
        "free",
        "available",
        "left",
        "used",
        "usage",
        "full",
        "status",
        "check",
    ))
    return has_storage_subject and asks_for_status


def run_storage_status_command():
    """Returns local root-drive usage without an API request."""
    try:
        stats = hud_stats.get_disk_stats()
        used_gb = float(stats["used_gb"])
        total_gb = float(stats["total_gb"])
        percent = float(stats["percent"])

        if total_gb <= 0:
            raise ValueError("disk total must be positive")
    except (KeyError, OSError, TypeError, ValueError) as error:
        print("Local storage check failed:", error, flush=True)
        return "I couldn't read my local drive storage just now."

    free_gb = max(0.0, total_gb - used_gb)
    return (
        f"I have {free_gb:.1f} gigabytes free out of {total_gb:.1f}. "
        f"{used_gb:.1f} gigabytes are used, so the drive is "
        f"{percent:.1f} percent full."
    )


INSTAGRAM_STATS_PHRASES = {
    "how is atlas doing online",
    "how is atlas doing on instagram",
    "how is atlas doing on insta",
    "give me atlas social stats",
    "give me instagram stats",
    "give me atlas instagram stats",
    "how did the latest atlas post do",
    "how did the latest instagram post do",
    "how is the latest atlas post doing",
    "how is the latest instagram post doing",
    "atlas social stats",
    "instagram stats",
}


def is_instagram_stats_query(text):
    normalized = _normalize_phrase(text)
    if normalized in INSTAGRAM_STATS_PHRASES:
        return True

    social_context = "instagram" in normalized or "insta" in normalized
    asks_for_metrics = any(term in normalized for term in (
        "stats", "statistics", "followers", "reach", "views", "likes",
        "post", "online", "doing", "performance",
    ))
    return social_context and asks_for_metrics


def _format_count(value):
    if value is None:
        return None
    return f"{int(value):,}"


def run_instagram_stats_command():
    """Read cached Instagram data locally; never invoke the paid model."""
    data = instagram_stats.get_stats()

    if not data.get("configured"):
        return "My Instagram insights link is not configured yet."
    if not data.get("available"):
        return "I couldn't reach Instagram insights just now. I'll keep the last good reading when one is available."

    followers = _format_count(data.get("followers_count")) or "an unknown number of"
    posts = _format_count(data.get("media_count"))
    account = data.get("username") or "Atlas"
    parts = [f"{account} has {followers} followers"]
    if posts is not None:
        parts.append(f"{posts} posts live")

    latest = data.get("latest")
    if latest:
        latest_parts = []
        if latest.get("views") is not None:
            latest_parts.append(f"{_format_count(latest['views'])} views")
        if latest.get("reach") is not None:
            latest_parts.append(f"{_format_count(latest['reach'])} reached")
        if latest.get("likes") is not None:
            latest_parts.append(f"{_format_count(latest['likes'])} likes")
        if latest.get("comments") is not None:
            latest_parts.append(f"{_format_count(latest['comments'])} comments")
        if latest.get("shares") is not None:
            latest_parts.append(f"{_format_count(latest['shares'])} shares")
        if latest.get("saved") is not None:
            latest_parts.append(f"{_format_count(latest['saved'])} saves")
        if latest_parts:
            parts.append("The latest post has " + ", ".join(latest_parts))

    answer = ". ".join(parts) + "."
    if data.get("stale"):
        answer += " That is my last cached reading."
    return answer


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


def run_diagnostics_command():
    """Spoken 'run diagnostics': the full structured 14-component sweep.
    Pushes the findings to the hub so the HUD switches into its
    diagnostics view, then speaks a verdict built only from what the
    checks actually observed. Zero tokens."""
    findings = diagnostics.run_structured_checks()

    try:
        requests.post(
            f"{HUB}/diagnostics_report",
            json={"findings": findings},
            timeout=5,
        )
    except requests.RequestException as error:
        print(
            "Diagnostics HUD update failed:",
            error,
            flush=True,
        )

    return diagnostics.spoken_structured_report(findings)


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


def run_read_shopping_list_command():
    items = memory_store.get_shopping_list_summary()

    if not items:
        return "Your shopping list is empty."

    return "Your shopping list: " + ", ".join(items) + "."


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

    # Ground the model in what A.T.L.A.S. can ACTUALLY do, so it never
    # claims an unimplemented ability. This is the authoritative registry.
    instructions += (
        "\n\nThese are the ONLY device actions you can actually perform. "
        "If asked to do something not in this list, say plainly that you "
        "can't do it yet — never imply you can:\n"
        + capabilities.instruction_summary()
    )

    instructions += (
        "\n\nYou are already running inside the A.T.L.A.S. application on "
        "the Raspberry Pi described above. Never claim that this is an "
        "external chat lacking access to the Pi, and never tell the owner "
        "to build a companion service for capabilities listed above. This "
        "is true no matter which surface the owner is talking to you from "
        "— voice at the desk or the phone link — the same real Pi, the "
        "same real capabilities, every time. For diagnostics, health "
        "checks, self-healing/repair, connection checks, storage, recent "
        "errors, tool versions, or listing capabilities, call the "
        "run_atlas_diagnostic_or_repair tool and report exactly what it "
        "returns — do not say you lack access or that the command wasn't "
        "recognized when that tool can just run it."
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


def _consume_openai_stream(stream, text_state, sentence_queue, stop_event):
    """Consumes one Responses stream and returns its completed response.

    The SDK's ``get_final_response()`` only accepts ``response.completed``.
    Capture failed/incomplete/error events first so logs contain the real
    cause and the caller can apply a safe one-retry policy.
    """
    terminal_error = None

    try:
        for event in stream:
            if stop_event.is_set():
                return None

            if event.type == "response.output_text.delta":
                text_state["buffer"] += event.delta
                text_state["full_text"] += event.delta
                sentences, text_state["buffer"] = _pop_complete_sentences(
                    text_state["buffer"]
                )

                for sentence in sentences:
                    sentence_queue.put(sentence)
            elif event.type in {
                "response.failed",
                "response.incomplete",
                "error",
            }:
                terminal_error = stream_resilience.from_terminal_event(
                    event,
                    text_state["full_text"],
                )

        if terminal_error is not None:
            raise terminal_error

        try:
            return stream.get_final_response()
        except Exception as error:
            raise stream_resilience.from_exception(
                error,
                text_state["full_text"],
            ) from error
    except stream_resilience.StreamResponseError:
        raise
    except Exception as error:
        raise stream_resilience.from_exception(
            error,
            text_state["full_text"],
        ) from error


def _stream_answer_sentences(
    question, instructions, max_tokens, client, sentence_queue, stop_event
):
    """Producer: streams the model's answer, pushing complete sentences onto
    sentence_queue as they arrive so a consumer can start speaking the first
    one before the rest has even finished generating. Falls back to a real
    tool-call round trip when needed, streaming that continuation the same
    way. Returns (full_text, total_input_tokens, total_output_tokens)."""
    text_state = {"full_text": "", "buffer": ""}
    total_input_tokens = 0
    total_output_tokens = 0
    ai_tools.clear_agent_usage()

    with client.responses.stream(
        model=MODEL_NAME,
        reasoning={"effort": "none"},
        instructions=instructions,
        input=question,
        tools=ai_tools.TOOLS,
        max_output_tokens=max_tokens
    ) as stream:
        response = _consume_openai_stream(
            stream,
            text_state,
            sentence_queue,
            stop_event,
        )

    if response is None:
        sentence_queue.put(None)
        return text_state["full_text"].strip(), 0, 0

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
        result = ai_tools.run_tool_call(
            call.name,
            arguments,
            source="voice",
        )
        nested_input, nested_output = (
            ai_tools.consume_agent_usage()
        )
        total_input_tokens += nested_input
        total_output_tokens += nested_output
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
            response2 = _consume_openai_stream(
                stream2,
                text_state,
                sentence_queue,
                stop_event,
            )

        if response2 is None:
            sentence_queue.put(None)
            return (
                text_state["full_text"].strip(),
                total_input_tokens,
                total_output_tokens,
            )

        total_input_tokens += int(
            getattr(response2.usage, "input_tokens", 0) or 0
        )
        total_output_tokens += int(
            getattr(response2.usage, "output_tokens", 0) or 0
        )

    remainder = text_state["buffer"].strip()

    if remainder:
        sentence_queue.put(remainder)

    sentence_queue.put(None)

    return text_state["full_text"].strip(), total_input_tokens, total_output_tokens


def answer_text_only(question):
    """Non-streaming, non-spoken answer for the phone link. Same model,
    instructions, budget guard, and memory as the voice path — just
    returns text. Costs the same per-question tokens as a voice question
    (on-demand only, never continuous)."""
    ai_tools.clear_agent_usage()
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
        result = ai_tools.run_tool_call(
            call.name,
            json.loads(call.arguments),
            source="phone",
        )
        nested_input, nested_output = (
            ai_tools.consume_agent_usage()
        )
        input_tokens += nested_input
        output_tokens += nested_output
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


# How long the consumer waits for the next streamed sentence before
# giving up. Confirmed live 2026-07-21: a real content.record_self_showcase
# call (registered timeout_seconds=300 -- the longest of any agent tool)
# produces zero streamed text while it runs, since the whole recording
# happens synchronously inside the model's tool-call handling. The old
# 30s value abandoned the turn ("Sentence stream timed out...") and told
# the owner it failed while the recording was still genuinely in
# progress in the background -- it finished seconds later (a real,
# valid Reel), just with nobody told. Set comfortably above the longest
# registered agent tool timeout so no legitimate slow tool call gets
# orphaned this way again.
SENTENCE_STREAM_IDLE_TIMEOUT_SECONDS = 320


def ask_and_speak_streaming(question, model, retry_attempted=False):
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
            error = stream_resilience.from_exception(error)
            result["error"] = error

            result["full_text"] = error.partial_text
            result["total_input_tokens"] = error.input_tokens
            result["total_output_tokens"] = error.output_tokens

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
    spoken_any = False

    try:
        while True:
            try:
                sentence = sentence_queue.get(
                    timeout=SENTENCE_STREAM_IDLE_TIMEOUT_SECONDS
                )
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
                spoken_any = True
    finally:
        barge_stop_event.set()
        watcher_thread.join(timeout=2)

    producer_thread.join(timeout=5)

    if result["error"] is not None:
        error = result["error"]
        print(
            "Online response stream failed:",
            type(error).__name__,
            str(error),
            flush=True,
        )

        # Failed/incomplete terminal events can still include usage. Keep the
        # local budget honest before a possible retry.
        failed_cost = (
            result["total_input_tokens"] * INPUT_PRICE_PER_TOKEN
            + result["total_output_tokens"] * OUTPUT_PRICE_PER_TOKEN
        )
        usage["spent_usd"] += failed_cost
        usage["requests"] += 1
        save_usage(usage)

        if stream_resilience.should_retry(
            error,
            spoken_any=spoken_any,
            interrupted=barge_result["interrupted"],
            retry_attempted=retry_attempted,
        ):
            print(
                "Retrying online response once before any speech was played.",
                flush=True,
            )
            return ask_and_speak_streaming(
                question,
                model,
                retry_attempted=True,
            )

        if spoken_any:
            notice = "The connection dropped before I could finish."
            speak(notice)
            partial = result["full_text"].strip()
            return (partial + " " + notice).strip(), False

        failure_answer = (
            "My online answer connection failed. Try that again in a moment."
        )
        speak(failure_answer)
        return failure_answer, False

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


# Non-destructive, non-confirm-gated capabilities the AI model can invoke
# directly as a tool call (ai_tools.run_atlas_diagnostic_or_repair) — the
# same ones already voice-callable without spoken confirmation. Exposing
# these as real tools means the model can run them wherever it's asked
# (voice fallback, phone link) instead of claiming it has no access when a
# phrasing doesn't match one of the fixed trigger phrases below.
DIAGNOSTIC_CAPABILITY_HANDLERS = {
    "diagnostics": run_diagnostics_command,
    "self_heal": run_self_heal_command,
    "system_health": run_system_health_command,
    "connections": run_connection_health_command,
    "status_report": run_status_report_command,
    "storage": run_storage_status_command,
    "log_query": run_log_query_command,
    "internet_check": run_internet_check_command,
    "capabilities": run_capabilities_command,
    "tool_status": run_tool_status_command,
}


def run_diagnostic_capability(capability):
    handler = DIAGNOSTIC_CAPABILITY_HANDLERS.get(capability)

    if handler is None:
        return f"'{capability}' isn't one of my real capabilities."

    try:
        return handler()
    except Exception as error:
        return f"That capability hit an error: {type(error).__name__}: {error}"


def _classify_intent(text):
    """Coarse deterministic intent label for the log — mirrors the
    dispatch priority in _handle_turn_body. Zero tokens."""
    normalized = _normalize_phrase(text)

    if is_vision_command(text):
        return "vision"
    if parse_gallery_search_query(text) or parse_image_search_query(text):
        return "image_search"
    # Checked ahead of every other category below: a taught trigger or
    # "when I say X do Y" phrase can otherwise contain a builtin phrase as
    # a substring (e.g. teaching an action of "storage status") and get
    # misclassified by one of those checks first.
    if macros.parse_teach_command(text) is not None:
        return "macro_teach"
    if macros.match_macro(normalized) is not None:
        return "macro"
    if parse_timer_set_command(text) or normalized in TIMER_CANCEL_PHRASES \
            or normalized in TIMER_CHECK_PHRASES:
        return "timer"
    if parse_focus_start_command(text) is not None or normalized in FOCUS_END_PHRASES:
        return "focus"
    if normalized in WAKE_PC_PHRASES:
        return "wake_pc"
    if is_storage_query(normalized):
        return "storage"
    if is_instagram_stats_query(normalized):
        return "instagram_stats"
    if normalized in DIAGNOSTICS_PHRASES:
        return "diagnostics"
    if normalized in SYSTEM_HEALTH_PHRASES:
        return "system_health"
    if is_network_devices_command(normalized) or normalized in SECURE_NETWORK_PHRASES:
        return "network"
    if is_enroll_face_command(normalized) or is_intruder_query(normalized):
        return "security"
    if normalized in NEWS_PHRASES or normalized in BRIEFING_PHRASES:
        return "briefing"
    if memory_store.parse_note_command(text) or memory_store.is_read_notes_command(text):
        return "notes"
    if parse_reminder_command(text):
        return "reminder"
    if memory_store.parse_remember_command(text):
        return "memory"
    if (
        memory_store.parse_add_shopping_item_command(text)
        or memory_store.parse_remove_shopping_item_command(text)
        or memory_store.is_read_shopping_list_command(text)
        or memory_store.is_clear_shopping_list_command(text)
    ):
        return "shopping_list"
    if chance.is_coin_flip_command(normalized) or chance.parse_dice_roll_command(normalized):
        return "chance"
    if unit_convert.parse_conversion_command(normalized) is not None:
        return "unit_convert"
    if countdown.parse_countdown_target(normalized) is not None:
        return "countdown"
    if parse_instant_answer(text) is not None:
        return "instant"
    return "ai_question"


_current_turn = None


def _log_transcript(text, raw_text=None, corrections=None):
    """Records transcript, mic level, and classified intent on the current
    turn — called right after each transcription."""
    if _current_turn is None:
        return

    fields = {
        "transcript": text,
        "audio_rms": _last_audio_rms,
        "intent": _classify_intent(text) if text else "no_speech",
        "transcription_alternatives": _last_transcription_alternatives,
    }
    if raw_text is not None:
        fields["raw_transcript"] = raw_text
    if corrections:
        fields["speech_corrections"] = corrections
    _current_turn.set(**fields)


SPEECH_DIAGNOSTIC_PHRASES = {
    "what did you hear", "what did atlas hear", "what did you think i said",
    "show me what you heard", "why did you misunderstand me",
}
SPEECH_TEACH_PATTERN = re.compile(
    r"^(?:(?:teach|learn) (?:atlas )?(?:that )?)?when i say (.+?) "
    r"i (?:mean|meant)"
    r" (.+)$"
)
# Older/explicit phrasing kept working too.
SPEECH_TEACH_PATTERN_ALT = re.compile(
    r"^(?:teach|learn) (?:atlas )?(?:that )?(.+?) (?:means|should mean) (.+)$"
)
# Aliases may only point at these real, safe command phrases.
APPROVED_SPEECH_ALIAS_TARGETS = {
    "open spotify", "open claude", "open fusion", "open fusion 360",
    "boot my pc", "shut down my pc", "empty the recycle bin",
    "what's on my network", "check connections", "run diagnostics",
    "how's my pc", "go dark", "lights up",
}

def _repair_turn_transcript(raw_text):
    repaired, corrections = speech_repair.repair(raw_text)
    speech_repair.record(
        raw_text, repaired, corrections, _last_transcription_alternatives
    )
    if corrections:
        print("Speech repair:", corrections, flush=True)
    return repaired, corrections

def _speech_teach_request(normalized):
    match = SPEECH_TEACH_PATTERN.match(normalized) or SPEECH_TEACH_PATTERN_ALT.match(normalized)
    if not match:
        return None
    alias, target = match.groups()
    target = _normalize_phrase(target)
    return (alias, target) if target in APPROVED_SPEECH_ALIAS_TARGETS else None


# --- "Did you mean X?" one-shot clarification -----------------------
import difflib

# Set when a confirmed suggestion should be re-run without re-recording.
_injected_command = None

# Triggers of macros currently being expanded, on this call stack — guards
# against a macro whose actions (directly or via another macro) loop back
# to itself.
_active_macro_triggers = set()

# Canonical short command phrases the clarifier can suggest. Built from
# the capability registry aliases plus a few high-frequency exact phrases.
def _canonical_command_phrases():
    phrases = set()
    for entry in capabilities.REGISTRY:
        for alias in entry["aliases"]:
            core = alias.lower().replace("...", "").replace("(", "").replace(")", "").strip()
            # Keep only concrete short phrases (skip templated ones).
            if core and 2 <= len(core.split()) <= 5 and "from the phone" not in core:
                phrases.add(core)
    phrases.update({
        "open spotify", "open claude", "open fusion", "boot my pc",
        "shut down my pc", "empty the recycle bin", "what's on my network",
        "check connections", "run diagnostics", "what can you control",
    })
    return phrases


_CLARIFY_MIN_RATIO = 0.72
_CLARIFY_MAX_RATIO = 0.97


def _maybe_clarify_command(text, model):
    """If text is close to a known command but not exact, ask 'did you
    mean X?'. On yes, re-run that command. Returns True if it handled the
    turn (asked + acted or declined), False to let the AI answer."""
    global _injected_command

    normalized = _normalize_phrase(text)
    candidates = _canonical_command_phrases()

    best = None
    best_ratio = 0.0
    for phrase in candidates:
        ratio = difflib.SequenceMatcher(None, normalized, phrase).ratio()
        if ratio > best_ratio:
            best_ratio, best = ratio, phrase

    if best is None or not (_CLARIFY_MIN_RATIO <= best_ratio < _CLARIFY_MAX_RATIO):
        return False

    speak(f"Did you mean: {best}? Say yes or no.")
    set_face("listening")
    subprocess.run([
        "arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", "16000",
        "-c", "1", "-d", "3", AUDIO_PATH,
    ], check=False)
    set_face("thinking")

    reply = _normalize_phrase(transcribe_audio(model))
    if reply.split() and reply.split()[0] in {"yes", "yeah", "yep", "correct", "right", "sure"}:
        _injected_command = best
        _handle_turn_body(model)
        return True

    speak("Okay, never mind.")
    return True


_AFFIRMATIVE_WORDS = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "correct", "right",
    "please", "do", "go", "download", "install", "get",
}


def _reply_is_affirmative(reply):
    """A short spoken confirmation counts as yes if any of its words is an
    affirmative — covers 'yes', 'do it', 'go ahead', 'download it'."""
    return any(word in _AFFIRMATIVE_WORDS for word in _normalize_phrase(reply).split())


def _record_short_reply(model):
    """Record and transcribe a brief yes/no answer, same pattern as the
    'did you mean' clarifier."""
    set_face("listening")
    subprocess.run([
        "arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", "16000",
        "-c", "1", "-d", "3", AUDIO_PATH,
    ], check=False)
    set_face("thinking")
    return transcribe_audio(model)


def _maybe_offer_tool_install(text, model):
    """If the request needs a catalog tool A.T.L.A.S. doesn't have yet,
    offer to acquire it. On a spoken yes: vet it against PyPI, install it,
    say where it landed, and refresh the knowledge graph. Returns True if
    it handled the turn, False to let the normal answer path run."""
    import tool_installer

    tool = tool_installer.find_missing_tool_for_request(text)
    if tool is None:
        return False

    desc = tool_installer.describe(tool)
    speak(f"I don't have the {tool} tool installed, but I can get it — "
          f"it would let me {desc}. Should I download it? Say yes or no.")

    if not _reply_is_affirmative(_record_short_reply(model)):
        answer = f"Okay, I'll leave {tool} alone for now."
        log_qa(text, answer)
        speak(answer)
        return True

    speak("On it — let me do my due diligence first.")
    check = tool_installer.due_diligence(tool)
    if not check["ok"]:
        log_qa(text, check["reason"])
        speak(check["reason"])
        return True

    speak(f"{check['reason']} Installing now.")
    result = tool_installer.install(tool)
    if not result["ok"]:
        log_qa(text, result["message"])
        speak(result["message"])
        return True

    location = tool_installer.where_is(tool)
    graph_ok = tool_installer.update_graph()

    where = f" It lives at {location}." if location else ""
    graph_note = (" I've refreshed my knowledge graph so it's on the map."
                  if graph_ok else "")
    answer = f"Done — {tool} is installed and ready.{where}{graph_note}"
    log_qa(text, answer)
    speak(answer)
    return True


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
        gate_active = camera_gate.is_available() and camera_gate.is_enabled()

        if gate_active and camera_gate.should_verify():
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
            raw_text = transcribe_audio(model)
            text, corrections = _repair_turn_transcript(raw_text)
            _log_transcript(text, raw_text=raw_text, corrections=corrections)

            if not text:
                speak("I didn't catch that.")
                return

            answer = _handle_restricted_turn(text)

            if answer is None:
                log_qa(f"[unverified] {text}", "[cancelled silently]")
                return

            log_qa(f"[unverified] {text}", answer)
            speak(answer)
            return

        maybe_speak_greeting()
        memory_store.mark_interaction_now()

        global _injected_command
        if _injected_command is not None:
            # A confirmed "did you mean X?" — run that phrase without
            # re-recording the mic.
            text = _injected_command
            _injected_command = None
            corrections = []
        else:
            cue_listening()
            set_face("listening")
            record_audio()

            set_face("thinking")
            raw_text = transcribe_audio(model)
            text, corrections = _repair_turn_transcript(raw_text)
            _log_transcript(text, raw_text=raw_text, corrections=corrections)

        print(f"{load_owner_name()}:", text)

        if not text:
            print(
                "No speech detected. Letting the user know before returning to wake mode.",
                flush=True
            )
            speak("I didn't catch that.")
            return

        # Only a real, non-empty prompt inside an active trusted camera
        # session slides the one-hour idle window. Armed/pending states can
        # never be cleared or extended here.
        if gate_active:
            camera_gate.mark_authorized_interaction()

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

        if interaction_control.is_safe_cancel_phrase(normalized_phrase):
            dismiss_current_interaction()
            log_qa(text, "[cancelled silently]")
            return

        # Easter eggs: count the command (achievements) and intercept any
        # secret phrase. Both are cosmetic and zero-token.
        try:
            import easter_eggs
            _unlock_line = easter_eggs.on_command(normalized_phrase)
            if _unlock_line:
                speak(_unlock_line)

            if normalized_phrase in {"list my achievements", "what have i unlocked",
                                     "my achievements", "what achievements do i have"}:
                answer = easter_eggs.list_achievements()
                log_qa(text, answer)
                speak(answer)
                return

            _secret = easter_eggs.check_secret(normalized_phrase)
            if _secret is not None:
                log_qa(text, _secret)
                speak(_secret)
                return
        except Exception as _egg_error:
            print("easter egg error:", _egg_error, flush=True)

        if normalized_phrase in SPEECH_DIAGNOSTIC_PHRASES:
            answer = speech_repair.previous_report()
            log_qa(text, answer)
            speak(answer)
            return

        teach_request = _speech_teach_request(normalized_phrase)
        if teach_request is not None:
            alias, target = teach_request
            speech_repair.teach(alias, target)
            answer = f"Okay. I'll treat {alias} as {target}."
            log_qa(text, answer)
            speak(answer)
            return

        macro_teach = macros.parse_teach_command(text)
        if macro_teach is not None:
            trigger, actions = macro_teach
            existing_intent = _classify_intent(trigger)

            if existing_intent not in ("ai_question", "macro"):
                answer = "I already know how to do that, so I'll leave it as is."
            else:
                macros.teach_macro(trigger, actions)
                answer = f"Got it. When you say '{trigger}', I'll {' then '.join(actions)}."

            log_qa(text, answer)
            speak(answer)
            return

        if macros.is_list_macros_command(normalized_phrase):
            answer = macros.list_macros_summary()
            log_qa(text, answer)
            speak(answer)
            return

        forget_macro_trigger = macros.parse_forget_macro_command(text)
        if forget_macro_trigger is not None:
            removed = macros.forget_macro(forget_macro_trigger)
            answer = (
                f"Okay, I've forgotten the macro for {forget_macro_trigger}."
                if removed else
                f"I didn't have a macro for {forget_macro_trigger}."
            )
            log_qa(text, answer)
            speak(answer)
            return

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

        weather_hud_intent = _wants_weather_hud(normalized_phrase)
        if weather_hud_intent == "open":
            answer = "Pulling up the weather radar." if _set_weather_overlay(True) else "I couldn't reach the screen."
            log_qa(text, answer)
            speak(answer)
            return
        if weather_hud_intent == "close":
            answer = "Closing the weather screen." if _set_weather_overlay(False) else "I couldn't reach the screen."
            log_qa(text, answer)
            speak(answer)
            return

        brightness_intent = _wants_brightness_change(normalized_phrase)
        if brightness_intent == "boost":
            answer = "Brightening the screen." if _set_brightness_boost(True) else "I couldn't reach the screen."
            log_qa(text, answer)
            speak(answer)
            return
        if brightness_intent == "normal":
            answer = "Back to normal brightness." if _set_brightness_boost(False) else "I couldn't reach the screen."
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

        if is_clear_intruder_alerts_command(normalized_phrase):
            answer = run_clear_intruder_alerts_command()
            log_qa(text, answer)
            speak(answer)
            return

        if is_intruder_query(normalized_phrase):
            answer = run_intruder_query_command()
            log_qa(text, answer)
            speak(answer)
            return

        if is_network_devices_command(normalized_phrase):
            answer = run_network_devices_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in CAPABILITIES_PHRASES:
            answer = run_capabilities_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in CONNECTION_PHRASES:
            set_face("thinking")
            answer = run_connection_health_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in STATUS_REPORT_PHRASES:
            set_face("thinking")
            answer = run_status_report_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in SELF_HEAL_PHRASES:
            set_face("thinking")
            speak("Running a self-heal. One moment.")
            answer = run_self_heal_command()
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in GOODBYE_PHRASES:
            set_face("thinking")
            answer = run_goodbye_routine()
            log_qa(text, answer)
            speak(answer)
            return

        _sky_kind = (
            "summary" if normalized_phrase in SKY_WATCH_PHRASES else
            "stargazing" if normalized_phrase in STARGAZING_PHRASES else
            "meteor" if normalized_phrase in METEOR_PHRASES else
            "launch" if normalized_phrase in LAUNCH_PHRASES else
            "moon" if normalized_phrase in MOON_PHRASES else None
        )
        if _sky_kind is not None:
            set_face("thinking")
            answer = run_sky_watch_command(_sky_kind)
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in TOOL_STATUS_PHRASES:
            set_face("thinking")
            answer = run_tool_status_command()
            log_qa(text, answer)
            speak(answer)
            return

        tool_upgrade = parse_tool_upgrade(text)
        if tool_upgrade is not None:
            set_face("thinking")
            answer = run_tool_upgrade_proposal(tool_upgrade)
            log_qa(text, answer)
            speak(answer)
            return

        profile_name = parse_profile_command(text)
        if profile_name is not None:
            set_face("thinking")
            answer = run_profile_command(profile_name)
            log_qa(text, answer)
            speak(answer)
            return

        memory_topic = parse_memory_query(text)
        if memory_topic is not None:
            answer = run_memory_query_command(memory_topic)
            log_qa(text, answer)
            speak(answer)
            return

        forget_topic = parse_forget_about(text)
        if forget_topic is not None:
            removed = memory_store.forget_matching(forget_topic)
            answer = (f"Okay, I've forgotten what I knew about {forget_topic}."
                      if removed else f"I didn't have anything about {forget_topic}.")
            log_qa(text, answer)
            speak(answer)
            return

        priority_text = parse_add_priority(text)
        if priority_text is not None:
            memory_store.add_priority(priority_text)
            answer = f"Got it — {priority_text} is a priority now."
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in PRIORITIES_QUERY_PHRASES:
            prios = memory_store.get_priorities_summary()
            answer = ("Your priorities: " + "; ".join(prios) + "."
                      if prios else "You haven't set any priorities.")
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in TODAY_PHRASES:
            set_face("thinking")
            answer = run_today_command()
            log_qa(text, answer)
            speak(answer)
            return

        simulate_target = parse_simulate_command(text)
        if simulate_target is not None:
            answer = run_simulate_command(simulate_target)
            log_qa(text, answer)
            speak(answer)
            return

        if normalized_phrase in DIAGNOSTICS_PHRASES:
            set_face("thinking")
            answer = run_diagnostics_command()
            log_qa(text, answer)
            speak(answer)
            return

        if is_storage_query(normalized_phrase):
            answer = run_storage_status_command()
            log_qa(text, answer)
            speak(answer)
            return

        if is_instagram_stats_query(normalized_phrase):
            answer = run_instagram_stats_command()
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

        shopping_item = memory_store.parse_add_shopping_item_command(text)

        if shopping_item is not None:
            memory_store.add_shopping_item(shopping_item)
            answer = f"Added {shopping_item} to your shopping list."
            log_qa(text, answer)
            speak(answer)
            return

        remove_shopping_item = memory_store.parse_remove_shopping_item_command(text)

        if remove_shopping_item is not None:
            removed = memory_store.remove_shopping_item(remove_shopping_item)
            answer = (
                f"Removed {remove_shopping_item} from your shopping list."
                if removed else
                f"{remove_shopping_item} wasn't on your shopping list."
            )
            log_qa(text, answer)
            speak(answer)
            return

        if memory_store.is_read_shopping_list_command(text):
            answer = run_read_shopping_list_command()
            log_qa(text, answer)
            speak(answer)
            return

        if memory_store.is_clear_shopping_list_command(text):
            memory_store.clear_shopping_list()
            answer = "Okay, your shopping list is cleared."
            log_qa(text, answer)
            speak(answer)
            return

        if chance.is_coin_flip_command(normalized_phrase):
            answer = chance.run_coin_flip_command()
            log_qa(text, answer)
            speak(answer)
            return

        dice_roll = chance.parse_dice_roll_command(normalized_phrase)

        if dice_roll is not None:
            answer = chance.run_dice_roll_command(*dice_roll)
            log_qa(text, answer)
            speak(answer)
            return

        conversion = unit_convert.parse_conversion_command(normalized_phrase)

        if conversion is not None:
            answer = unit_convert.run_conversion_command(*conversion)
            log_qa(text, answer)
            speak(answer)
            return

        countdown_target = countdown.parse_countdown_target(normalized_phrase)

        if countdown_target is not None:
            countdown_answer = countdown.build_countdown_answer(countdown_target)

            if countdown_answer is not None:
                log_qa(text, countdown_answer)
                speak(countdown_answer)
                return

        macro_actions = macros.match_macro(normalized_phrase)
        if macro_actions is not None:
            if normalized_phrase in _active_macro_triggers:
                answer = "That macro loops back on itself, so I'm stopping there."
                log_qa(text, answer)
                speak(answer)
                return

            _active_macro_triggers.add(normalized_phrase)
            try:
                for action in macro_actions:
                    _injected_command = action
                    _handle_turn_body(model)
            finally:
                _active_macro_triggers.discard(normalized_phrase)
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

        # Nothing matched a command. If the request needs a tool A.T.L.A.S.
        # doesn't have yet, offer to acquire it (gated on a spoken yes and a
        # PyPI due-diligence check) before falling through to a paid answer.
        if _maybe_offer_tool_install(text, model):
            return

        # Before spending a paid model call, see if the transcript is CLOSE
        # to a real command (mis-heard) and offer a one-shot "did you mean
        # X?" — a yes re-runs that command.
        if _maybe_clarify_command(text, model):
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
