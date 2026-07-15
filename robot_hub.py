from flask import Flask, request, jsonify, send_file, send_from_directory
import subprocess
import tempfile
import threading
import time
import os
import wave

import requests
from piper import PiperVoice, SynthesisConfig

import hud_stats

app = Flask(__name__)

state_lock = threading.Lock()

robot_state = {
    "expression": "happy",
    "speaking": False,
    "image_path": None,
    "image_caption": None,
    "gallery_image_paths": [],
    "gallery_caption": None,
    "qa_log": []
}

QA_LOG_MAX_ENTRIES = 20

HUD_DIR = os.path.join(os.path.dirname(__file__), "hud")

image_until = 0.0
gallery_until = 0.0

IMAGE_DISPLAY_PATH_BASE = "/tmp/atlas_robot_display_image"
GALLERY_PATH_BASE = "/tmp/atlas_robot_gallery_image_"
GALLERY_MAX_IMAGES = 6
GALLERY_DEFAULT_DURATION = 15
IMAGE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_MIN_DURATION = 3
IMAGE_MAX_DURATION = 60
IMAGE_DEFAULT_DURATION = 10
IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}


def clear_image_state_locked():
    """Caller must hold state_lock. Unconditionally clears any active single
    image, deleting its file if present. Used both for expiry and to keep
    the single-image and gallery overlays mutually exclusive."""
    global image_until

    old_path = robot_state["image_path"]
    robot_state["image_path"] = None
    robot_state["image_caption"] = None
    image_until = 0.0

    if old_path and os.path.exists(old_path):
        try:
            os.remove(old_path)
        except OSError:
            pass


def clear_gallery_state_locked():
    """Caller must hold state_lock. Unconditionally clears any active
    gallery, deleting its files if present. Used both for expiry and to
    keep the single-image and gallery overlays mutually exclusive."""
    global gallery_until

    old_paths = robot_state["gallery_image_paths"]
    robot_state["gallery_image_paths"] = []
    robot_state["gallery_caption"] = None
    gallery_until = 0.0

    for old_path in old_paths:
        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass


def clear_expired_image_locked():
    """Caller must hold state_lock."""
    if robot_state["image_path"] is not None and time.time() >= image_until:
        clear_image_state_locked()


def clear_expired_gallery_locked():
    """Caller must hold state_lock."""
    if robot_state["gallery_image_paths"] and time.time() >= gallery_until:
        clear_gallery_state_locked()


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


@app.get("/hud")
def hud_page():
    return send_from_directory(HUD_DIR, "index.html")


@app.get("/hud/static/<path:filename>")
def hud_static(filename):
    return send_from_directory(HUD_DIR, filename)


@app.get("/hud/stats")
def hud_stats_route():
    return jsonify(hud_stats.get_hud_stats())


@app.get("/hud/display_image")
def hud_display_image():
    with state_lock:
        clear_expired_image_locked()
        path = robot_state["image_path"]

    if not path or not os.path.exists(path):
        return jsonify({
            "ok": False,
            "error": "No image is currently displayed"
        }), 404

    return send_file(path)


@app.get("/state")
def get_state():
    with state_lock:
        clear_expired_image_locked()
        clear_expired_gallery_locked()
        state = robot_state.copy()
        state["gallery_until"] = gallery_until
        return jsonify(state)


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


@app.post("/qa_log")
def add_qa_log():
    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()
    answer = str(data.get("answer", "")).strip()

    if not question or not answer:
        return jsonify({
            "ok": False,
            "error": "question and answer are required"
        }), 400

    entry = {
        "question": question,
        "answer": answer,
        "timestamp": time.time()
    }

    with state_lock:
        robot_state["qa_log"].append(entry)
        robot_state["qa_log"] = robot_state["qa_log"][-QA_LOG_MAX_ENTRIES:]

    return jsonify({"ok": True, "entry": entry})


def _download_image(url, path_without_extension):
    """Downloads url as an image if it's valid and within the size cap.
    Returns the full path (with extension) on success, or None on failure."""
    try:
        response = requests.get(
            url,
            timeout=8,
            stream=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AtlasRobot/1.0)"}
        )
        response.raise_for_status()
    except requests.RequestException:
        return None

    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    extension = IMAGE_EXTENSIONS.get(content_type)

    if extension is None:
        response.close()
        return None

    path = path_without_extension + extension
    total_bytes = 0

    try:
        with open(path, "wb") as image_file:
            for chunk in response.iter_content(chunk_size=65536):
                total_bytes += len(chunk)

                if total_bytes > IMAGE_MAX_BYTES:
                    raise ValueError("Image exceeded the size limit")

                image_file.write(chunk)
        return path
    except (OSError, ValueError):
        if os.path.exists(path):
            os.remove(path)
        return None
    finally:
        response.close()


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

    new_path = _download_image(url, IMAGE_DISPLAY_PATH_BASE)

    if new_path is None:
        return jsonify({
            "ok": False,
            "error": "Could not download a valid image from that URL"
        }), 502

    with state_lock:
        clear_gallery_state_locked()

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


@app.post("/show_images")
def show_images():
    global gallery_until

    data = request.get_json(silent=True) or {}
    urls = data.get("urls", [])
    caption = str(data.get("caption", "")).strip() or None

    if not isinstance(urls, list) or not urls:
        return jsonify({
            "ok": False,
            "error": "urls must be a non-empty list"
        }), 400

    urls = [str(url).strip() for url in urls if str(url).strip()][:GALLERY_MAX_IMAGES]

    try:
        duration = float(data.get("duration", GALLERY_DEFAULT_DURATION))
    except (TypeError, ValueError):
        duration = GALLERY_DEFAULT_DURATION

    duration = max(IMAGE_MIN_DURATION, min(IMAGE_MAX_DURATION, duration))

    new_paths = []

    for index, url in enumerate(urls):
        if not url.lower().startswith(("http://", "https://")):
            continue

        path = _download_image(url, f"{GALLERY_PATH_BASE}{index}")

        if path is not None:
            new_paths.append(path)

    if not new_paths:
        return jsonify({
            "ok": False,
            "error": "No images could be downloaded"
        }), 502

    with state_lock:
        clear_image_state_locked()

        old_paths = robot_state["gallery_image_paths"]
        robot_state["gallery_image_paths"] = new_paths
        robot_state["gallery_caption"] = caption
        gallery_until = time.time() + duration

        for old_path in old_paths:
            if old_path and old_path not in new_paths and os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass

    return jsonify({
        "ok": True,
        "count": len(new_paths),
        "duration": duration
    })


@app.get("/hud/gallery_image/<int:index>")
def hud_gallery_image(index):
    with state_lock:
        clear_expired_gallery_locked()
        paths = list(robot_state["gallery_image_paths"])

    if index < 0 or index >= len(paths) or not os.path.exists(paths[index]):
        return jsonify({
            "ok": False,
            "error": "No gallery image at that index"
        }), 404

    return send_file(paths[index])


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
