from flask import Flask, request, jsonify
import subprocess
import tempfile
import threading
import os
import wave

from piper import PiperVoice, SynthesisConfig

app = Flask(__name__)

state_lock = threading.Lock()

robot_state = {
    "expression": "happy",
    "speaking": False
}

VALID_EXPRESSIONS = {
    "happy",
    "angry",
    "surprised",
    "sleeping",
    "listening",
    "thinking",
    "talking"
}

PIPER_MODEL = "en_US-ryan-medium"
PIPER_DATA_DIR = "/home/atlas/atlas-robot/voices"
PIPER_VOLUME = 0.75
PIPER_MODEL_PATH = f"{PIPER_DATA_DIR}/{PIPER_MODEL}.onnx"

piper_lock = threading.Lock()

try:
    piper_voice = PiperVoice.load(PIPER_MODEL_PATH)
except Exception as error:
    print("Piper voice failed to load:", type(error).__name__, error)
    piper_voice = None


@app.get("/status")
def status():
    return "A.T.L.A.S. ROBOT HUB ONLINE\n", 200


@app.get("/state")
def get_state():
    with state_lock:
        return jsonify(robot_state.copy())


@app.post("/face")
def set_face():
    data = request.get_json(silent=True) or {}
    expression = str(data.get("expression", "")).strip().lower()

    if expression not in VALID_EXPRESSIONS:
        return jsonify({
            "ok": False,
            "error": "Invalid expression",
            "valid": sorted(VALID_EXPRESSIONS)
        }), 400

    with state_lock:
        robot_state["expression"] = expression

    return jsonify({
        "ok": True,
        "expression": expression
    })


@app.post("/speak")
def speak():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()

    if not text:
        return jsonify({
            "ok": False,
            "error": "No text provided"
        }), 400

    if piper_voice is None:
        return jsonify({
            "ok": False,
            "error": "Piper voice is not loaded"
        }), 500

    wav_path = None
    previous_expression = "happy"

    try:
        with state_lock:
            previous_expression = robot_state["expression"]
            robot_state["expression"] = "talking"
            robot_state["speaking"] = True

        with tempfile.NamedTemporaryFile(
            prefix="atlas_robot_",
            suffix=".wav",
            delete=False
        ) as temp_file:
            wav_path = temp_file.name

        with piper_lock:
            with wave.open(wav_path, "wb") as wav_file:
                piper_voice.synthesize_wav(
                    text,
                    wav_file,
                    syn_config=SynthesisConfig(volume=PIPER_VOLUME)
                )

            subprocess.run(
                [
                    "aplay",
                    "-D", "plughw:0,0",
                    wav_path
                ],
                check=True
            )

        return jsonify({
            "ok": True,
            "spoken": text,
            "voice": PIPER_MODEL
        })

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error)
        }), 500

    finally:
        with state_lock:
            robot_state["speaking"] = False
            robot_state["expression"] = previous_expression

        if wav_path and os.path.exists(wav_path):
            os.remove(wav_path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051, threaded=True)
