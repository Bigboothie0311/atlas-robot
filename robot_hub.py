from flask import Flask, request, jsonify
import subprocess
import tempfile
import threading
import time
import os
import wave

import requests
from piper import PiperVoice, SynthesisConfig

app = Flask(__name__)

state_lock = threading.Lock()

robot_state = {
    "expression": "happy",
    "speaking": False,
    "image_path": None,
    "image_caption": None
}

image_until = 0.0

IMAGE_DISPLAY_PATH_BASE = "/tmp/atlas_robot_display_image"
IMAGE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_MIN_DURATION = 3
IMAGE_MAX_DURATION = 60
IMAGE_DEFAULT_DURATION = 15
IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}


def clear_expired_image_locked():
    """Caller must hold state_lock."""
    global image_until

    if robot_state["image_path"] is not None and time.time() >= image_until:
        old_path = robot_state["image_path"]
        robot_state["image_path"] = None
        robot_state["image_caption"] = None
        image_until = 0.0

        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass


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

# HDMI0 (vc4hdmi0, card 2) — routes speech through the connected screen's
# speakers instead of the GPIO/I2S MAX98357A amp (card 0). Check `aplay -l`
# if the card number ever shifts (e.g. after a reboot or hardware change).
AUDIO_DEVICE = "plughw:2,0"

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
        clear_expired_image_locked()
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


@app.post("/show_image")
def show_image():
    global image_until

    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    caption = str(data.get("caption", "")).strip() or None

    try:
        duration = float(data.get("duration", IMAGE_DEFAULT_DURATION))
    except (TypeError, ValueError):
        duration = IMAGE_DEFAULT_DURATION

    duration = max(IMAGE_MIN_DURATION, min(IMAGE_MAX_DURATION, duration))

    if not url.lower().startswith(("http://", "https://")):
        return jsonify({
            "ok": False,
            "error": "URL must be http or https"
        }), 400

    try:
        response = requests.get(
            url,
            timeout=8,
            stream=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AtlasRobot/1.0)"}
        )
        response.raise_for_status()
    except requests.RequestException as error:
        return jsonify({
            "ok": False,
            "error": f"Download failed: {error}"
        }), 502

    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    extension = IMAGE_EXTENSIONS.get(content_type)

    if extension is None:
        response.close()
        return jsonify({
            "ok": False,
            "error": f"Unsupported image type: {content_type or 'unknown'}"
        }), 415

    new_path = IMAGE_DISPLAY_PATH_BASE + extension
    total_bytes = 0

    try:
        with open(new_path, "wb") as image_file:
            for chunk in response.iter_content(chunk_size=65536):
                total_bytes += len(chunk)

                if total_bytes > IMAGE_MAX_BYTES:
                    raise ValueError("Image exceeded the size limit")

                image_file.write(chunk)
    except (OSError, ValueError) as error:
        if os.path.exists(new_path):
            os.remove(new_path)

        return jsonify({
            "ok": False,
            "error": str(error)
        }), 502
    finally:
        response.close()

    with state_lock:
        old_path = robot_state["image_path"]
        robot_state["image_path"] = new_path
        robot_state["image_caption"] = caption
        image_until = time.time() + duration

        if old_path and old_path != new_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    return jsonify({
        "ok": True,
        "image_path": new_path,
        "duration": duration
    })


@app.post("/clear_image")
def clear_image():
    global image_until

    with state_lock:
        old_path = robot_state["image_path"]
        robot_state["image_path"] = None
        robot_state["image_caption"] = None
        image_until = 0.0

        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    return jsonify({"ok": True})


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
                    "-D", AUDIO_DEVICE,
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
