import json
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

import requests
from openai import OpenAI
from vosk import Model, KaldiRecognizer

import ai_tools


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


def record_audio():
    print("Listening for up to 4 seconds...")

    subprocess.run([
        "arecord",
        "-D", MIC_DEVICE,
        "-f", "S16_LE",
        "-r", "16000",
        "-c", "1",
        "-d", "4",
        AUDIO_PATH
    ], check=True)


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

    instructions = (
        f"You are A.T.L.A.S., {owner_name}'s helpful desk robot assistant. "
        f"Today's date is {today}. "
        "Answer naturally in plain spoken English. "
        "Keep ordinary answers to one or two concise sentences because "
        "they will be spoken aloud. Do not use markdown, headings, "
        "bullets, citations, or special formatting. Be friendly, useful, "
        "direct, and honest when uncertain. Use your tools when a "
        "question needs live or current information, such as weather "
        "or recent events."
    )

    response = client.responses.create(
        model=MODEL_NAME,
        reasoning={"effort": "none"},
        instructions=instructions,
        input=question,
        tools=ai_tools.TOOLS,
        max_output_tokens=120
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
            max_output_tokens=120
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

    return answer


def handle_turn(model):
    try:
        speak("Go ahead.")
        set_face("listening")
        record_audio()

        set_face("thinking")
        text = transcribe_audio(model)

        print(f"{load_owner_name()}:", text)

        if not text:
            print(
                "No speech detected. Returning silently to wake mode.",
                flush=True
            )
            return

        if is_vision_command(text):
            run_vision_command()
            return

        local_answer = handle_local_command(text, model)

        if local_answer is not None:
            print("A.T.L.A.S. local command:", local_answer)
            speak(local_answer)
            return

        answer = ask_atlas(text)

        print("A.T.L.A.S.:", answer)
        speak(answer)

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
