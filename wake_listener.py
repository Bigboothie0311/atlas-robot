import json
import subprocess
import time
from pathlib import Path

import requests
from vosk import Model

import hearing
import listen_and_answer
import logbook
from wake_detection import (
    WAKE_PHRASE,
    MIN_WORD_CONFIDENCE,
    MIN_UTTERANCE_RMS,
    MIN_PARTIAL_HITS,
    AUDIO_CHUNK_BYTES,
    pcm_rms,
    stop_recorder,
    create_recognizer,
)


HUB = "http://127.0.0.1:5051"
MODEL_PATH = "/home/atlas/atlas-robot/models/vosk-model-small-en-us-0.15"
PYTHON = "/home/atlas/atlas-robot/venv/bin/python"
VISION_SCRIPT = "/home/atlas/atlas-robot/vision_test.py"

LISTEN_TRIGGER = Path("/tmp/atlas_robot_listen.trigger")
VISION_TRIGGER = Path("/tmp/atlas_robot_vision.trigger")

# Old USB webcam's built-in mic.
# MIC_DEVICE = "plughw:CARD=camera,DEV=0"
MIC_DEVICE = "plughw:CARD=Device,DEV=0"  # SuziePi USB mic

SPEAKER_COOLDOWN_SECONDS = 1.5


def set_face(expression):
    try:
        requests.post(
            f"{HUB}/face",
            json={"expression": expression},
            timeout=3
        )
    except requests.RequestException:
        pass


def robot_is_speaking():
    try:
        response = requests.get(
            f"{HUB}/state",
            timeout=1
        )
        response.raise_for_status()

        state = response.json()

        return bool(
            state.get("speaking", False)
            or state.get("expression") == "talking"
        )

    except requests.RequestException:
        return False


def pop_trigger(trigger_path):
    try:
        if not trigger_path.exists():
            return False

        trigger_path.unlink()
        return True

    except OSError as error:
        print(
            "Trigger read failed:",
            trigger_path,
            error,
            flush=True
        )
        return False


def listen_for_wake_word(model):
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

    recognizer = create_recognizer(model)

    speaker_was_active = False
    cooldown_until = 0.0
    last_state_check = 0.0
    speaking = False

    utterance_peak_rms = 0
    partial_hits = 0

    try:
        while True:
            audio_data = recorder.stdout.read(AUDIO_CHUNK_BYTES)

            if not audio_data:
                raise RuntimeError(
                    "Microphone audio stream stopped."
                )

            now = time.monotonic()

            if now - last_state_check >= 0.25:
                speaking = robot_is_speaking()
                last_state_check = now

            # Macro-pad requests wait until Atlas is not speaking.
            if not speaking:
                if pop_trigger(LISTEN_TRIGGER):
                    print(
                        "Macro pad requested listening.",
                        flush=True
                    )
                    return "listen"

                if pop_trigger(VISION_TRIGGER):
                    print(
                        "Macro pad requested camera vision.",
                        flush=True
                    )
                    return "vision"

            # Ignore all microphone audio while the robot is speaking.
            if speaking:
                speaker_was_active = True
                continue

            # Clear anything recognized from speaker echo.
            if speaker_was_active:
                recognizer = create_recognizer(model)
                speaker_was_active = False
                cooldown_until = (
                    now + SPEAKER_COOLDOWN_SECONDS
                )
                utterance_peak_rms = 0
                partial_hits = 0
                continue

            if now < cooldown_until:
                continue

            current_rms = pcm_rms(audio_data)
            utterance_peak_rms = max(
                utterance_peak_rms,
                current_rms
            )

            # Room-aware hearing: quiet chunks train the ambient noise
            # floor so the SNR gate below can reject accidental wakes.
            if current_rms < MIN_UTTERANCE_RMS:
                try:
                    hearing.observe_ambient(current_rms)
                except Exception:
                    pass

            finalized = recognizer.AcceptWaveform(audio_data)

            if not finalized:
                partial_data = json.loads(
                    recognizer.PartialResult()
                )

                partial_text = (
                    partial_data
                    .get("partial", "")
                    .strip()
                    .lower()
                )

                if partial_text == WAKE_PHRASE:
                    partial_hits += 1

                continue

            result_data = json.loads(recognizer.Result())

            text = (
                result_data
                .get("text", "")
                .strip()
                .lower()
            )

            word_results = result_data.get("result", [])

            confidences = [
                float(word.get("conf", 0.0))
                for word in word_results
                if word.get("word") in {"hey", "atlas"}
            ]

            minimum_confidence = (
                min(confidences)
                if confidences
                else 0.0
            )

            if text:
                print(
                    "Wake candidate:",
                    repr(text),
                    f"confidence={minimum_confidence:.2f}",
                    f"peak_rms={utterance_peak_rms}",
                    f"partial_hits={partial_hits}",
                    flush=True
                )

            # Additional SNR gate (room-aware hearing) — rejects wake
            # candidates that barely clear the fixed floor in a noisy room.
            # Fails OPEN: if it errors, the fixed thresholds alone decide,
            # so real detection is never weakened.
            try:
                snr_ok = hearing.passes_snr_gate(utterance_peak_rms)
            except Exception:
                snr_ok = True

            accepted = (
                text == WAKE_PHRASE
                and minimum_confidence
                    >= MIN_WORD_CONFIDENCE
                and utterance_peak_rms
                    >= MIN_UTTERANCE_RMS
                and partial_hits
                    >= MIN_PARTIAL_HITS
                and snr_ok
            )

            if text == WAKE_PHRASE and not snr_ok:
                print(
                    "Wake rejected by SNR gate:",
                    f"peak_rms={utterance_peak_rms}",
                    f"required={hearing.required_rms():.0f}",
                    f"ambient={hearing.ambient_floor():.0f}",
                    flush=True,
                )

            utterance_peak_rms = 0
            partial_hits = 0

            if accepted:
                print(
                    "Verified wake phrase detected.",
                    flush=True
                )
                # Hand the accepted wake's confidence/level to the logbook
                # so the turn it triggers records why it woke.
                logbook.set_pending_wake(
                    round(minimum_confidence, 2),
                    utterance_peak_rms,
                    MIC_DEVICE,
                )
                return "listen"

            if text == WAKE_PHRASE:
                print(
                    "Rejected weak wake candidate.",
                    flush=True
                )

    finally:
        stop_recorder(recorder)


def announce_waiting():
    print(
        'A.T.L.A.S. is waiting for "Hey Atlas".',
        flush=True
    )


def main():
    print("Loading wake-word model...", flush=True)
    model = Model(MODEL_PATH)

    set_face("happy")
    announce_waiting()

    while True:
        try:
            action = listen_for_wake_word(model)

            # Let arecord release the microphone.
            time.sleep(0.15)

            if action == "vision":
                subprocess.run(
                    [PYTHON, VISION_SCRIPT],
                    check=False
                )
            else:
                listen_and_answer.handle_turn(model)

            time.sleep(0.8)

            announce_waiting()

        except KeyboardInterrupt:
            print("\nWake listener stopped.", flush=True)
            set_face("happy")
            break

        except Exception as error:
            print(
                "Wake listener error:",
                type(error).__name__,
                error,
                flush=True
            )

            set_face("happy")
            time.sleep(3)


if __name__ == "__main__":
    main()
