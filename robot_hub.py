from flask import Flask, request, jsonify, send_file, send_from_directory
import hmac
import subprocess
import tempfile
import threading
import time
import os
import wave
from pathlib import Path

import requests
from piper import PiperVoice, SynthesisConfig

import hud_stats
import memory_store
import pc_stats

ROBOT_ENV_PATH = Path("/home/atlas/atlas-robot/config/robot.env")


def load_notify_token():
    if not ROBOT_ENV_PATH.exists():
        return None

    for line in ROBOT_ENV_PATH.read_text().splitlines():
        line = line.strip()

        if line.startswith("NOTIFY_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"').strip("'")

            if token:
                return token

    return None


NOTIFY_TOKEN = load_notify_token()

app = Flask(__name__)

state_lock = threading.Lock()

robot_state = {
    "expression": "happy",
    "speaking": False,
    "activity_label": None,
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

PIPER_MODEL = "en_US-joe-medium"
PIPER_DATA_DIR = "/home/atlas/atlas-robot/voices"
PIPER_VOLUME = 0.75
PIPER_MODEL_PATH = f"{PIPER_DATA_DIR}/{PIPER_MODEL}.onnx"

# HDMI1 (vc4hdmi1, card 3) — routes speech through the connected screen's
# speakers instead of the GPIO/I2S MAX98357A amp (card 0). Switched from
# card 2 (vc4hdmi0) on 2026-07-16 after the HDMI cable moved to the other
# port (confirmed via /sys/class/drm/card1-HDMI-A-*/status — A-1 read
# "disconnected", A-2 read "connected"). Check `aplay -l` if the card
# number ever shifts again (e.g. after a reboot or hardware change).
AUDIO_DEVICE = "plughw:3,0"

piper_lock = threading.Lock()
playback_lock = threading.Lock()
_current_playback_process = None
# aplay exits with code 1 both for a genuine device error and for being
# killed via /interrupt (confirmed empirically — SIGTERM doesn't produce a
# distinguishable negative returncode, aplay catches it and exits 1 itself).
# This flag is the only reliable way to tell the two apart.
_playback_was_interrupted = False

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


@app.post("/activity")
def set_activity():
    """Lets the HUD show what Atlas is actually doing during a tool call
    (e.g. "CHECKING WEATHER") instead of a generic "THINKING" label. Pass
    {"label": null} to clear it back to the default."""
    data = request.get_json(silent=True) or {}
    label = data.get("label")
    label = str(label).strip() if label else None

    with state_lock:
        robot_state["activity_label"] = label or None

    return jsonify({"ok": True, "activity_label": label})


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


def _check_notify_token():
    """Returns an error (jsonify(...), status) tuple if the request's token
    is missing/invalid, otherwise None."""
    if not NOTIFY_TOKEN:
        return jsonify({
            "ok": False,
            "error": "Notifications are not configured on this device"
        }), 503

    provided_token = request.headers.get("X-Notify-Token", "")

    if not hmac.compare_digest(provided_token, NOTIFY_TOKEN):
        return jsonify({"ok": False, "error": "Invalid token"}), 401

    return None


def _append_qa_log(question, answer):
    entry = {
        "question": question,
        "answer": answer,
        "timestamp": time.time()
    }

    with state_lock:
        robot_state["qa_log"].append(entry)
        robot_state["qa_log"] = robot_state["qa_log"][-QA_LOG_MAX_ENTRIES:]

    return entry


@app.post("/notify")
def notify():
    auth_error = _check_notify_token()

    if auth_error is not None:
        return auth_error

    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()

    if not message:
        return jsonify({
            "ok": False,
            "error": "message is required"
        }), 400

    _append_qa_log("[notification]", message)

    try:
        _speak_text(message)
    except Exception as error:
        return jsonify({
            "ok": False,
            "error": f"Logged but could not speak it: {error}"
        }), 500

    return jsonify({"ok": True, "spoken": message})


@app.post("/remember")
def remember():
    auth_error = _check_notify_token()

    if auth_error is not None:
        return auth_error

    data = request.get_json(silent=True) or {}
    note = str(data.get("message", "")).strip()

    if not note:
        return jsonify({
            "ok": False,
            "error": "message is required"
        }), 400

    memory_store.add_fact(note)
    _append_qa_log("[note-to-self]", note)

    try:
        _speak_text(f"Got it, I'll remember: {note}")
    except Exception as error:
        return jsonify({
            "ok": True,
            "remembered": note,
            "speak_error": str(error)
        })

    return jsonify({"ok": True, "remembered": note})


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


def _speak_text(text):
    """Synthesizes and plays text aloud. Raises on failure. Shared by the
    /speak route and the proactive watcher thread, so both go through the
    same state/expression handling and the same piper_lock. Playback can be
    cut short by POST /interrupt (e.g. a barge-in wake word) — that's a
    normal early return, not a failure."""
    global _current_playback_process, _playback_was_interrupted

    if piper_voice is None:
        raise RuntimeError("Piper voice is not loaded")

    wav_path = None
    previous_expression = "happy"

    with playback_lock:
        _playback_was_interrupted = False

    try:
        with tempfile.NamedTemporaryFile(
            prefix="atlas_robot_",
            suffix=".wav",
            delete=False
        ) as temp_file:
            wav_path = temp_file.name

        # Softer overnight — matches the dimmer HUD look during the same
        # window (see QUIET_HOURS_START/END below).
        volume = PIPER_VOLUME * 0.7 if _in_quiet_hours() else PIPER_VOLUME

        with piper_lock:
            with wave.open(wav_path, "wb") as wav_file:
                piper_voice.synthesize_wav(
                    text,
                    wav_file,
                    syn_config=SynthesisConfig(volume=volume)
                )

            # Only flip to "talking" once synthesis is actually done and
            # playback is about to start — synthesis alone measured 1.3s+
            # for a typical answer on this hardware, which was previously
            # showing as "SPEAKING" on the HUD well before any sound played.
            with state_lock:
                previous_expression = robot_state["expression"]
                robot_state["expression"] = "talking"
                robot_state["speaking"] = True

            process = subprocess.Popen([
                "aplay",
                "-D", AUDIO_DEVICE,
                wav_path
            ])

            with playback_lock:
                _current_playback_process = process

            process.wait()

            with playback_lock:
                _current_playback_process = None
                was_interrupted = _playback_was_interrupted

            # aplay exits with the same code 1 whether it was killed via
            # /interrupt (an intentional barge-in) or genuinely failed to
            # open the device — the exit code alone can't tell them apart,
            # so the explicit flag set by /interrupt is what decides
            # whether a nonzero exit here is expected or a real failure
            # that needs to surface instead of a silent "ok".
            if (
                process.returncode is not None
                and process.returncode != 0
                and not was_interrupted
            ):
                raise RuntimeError(
                    f"aplay exited with code {process.returncode} — "
                    f"check that AUDIO_DEVICE ({AUDIO_DEVICE}) is still "
                    "valid with 'aplay -l'"
                )
    finally:
        with state_lock:
            robot_state["speaking"] = False
            robot_state["expression"] = previous_expression

        if wav_path and os.path.exists(wav_path):
            os.remove(wav_path)


@app.post("/interrupt")
def interrupt():
    global _playback_was_interrupted

    with playback_lock:
        process = _current_playback_process

    if process is None or process.poll() is not None:
        return jsonify({"ok": True, "interrupted": False})

    with playback_lock:
        _playback_was_interrupted = True

    process.terminate()

    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

    return jsonify({"ok": True, "interrupted": True})


@app.post("/speak")
def speak():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()

    if not text:
        return jsonify({
            "ok": False,
            "error": "No text provided"
        }), 400

    try:
        _speak_text(text)

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


# Proactive nudges: unprompted speech triggered by notable state, not by a
# wake word. Runs on its own timer, separate from the request/response
# routes above.
PROACTIVE_POLL_SECONDS = 120
PC_TEMP_ALERT_C = 85
PC_TEMP_COOLDOWN_SECONDS = 30 * 60
RAIN_ALERT_PERCENT = 50
# This Pi's own CPU, not the gaming PC's — matches hud/app.js's HUD status
# word threshold, though the HUD flips to "WARNING" immediately while the
# voice only speaks up after this has held sustained, to avoid narrating
# every brief spike.
CPU_WARNING_THRESHOLD = 75
CPU_WARNING_SUSTAINED_SECONDS = 3 * 60
# Don't speak unprompted overnight — a temp alert at 3am would be worse than
# the problem it's warning about.
QUIET_HOURS_START = 23
QUIET_HOURS_END = 6

_proactive_state = {
    "last_cpu_alert": 0.0,
    "last_gpu_alert": 0.0,
    "last_rain_alert_date": None,
    "cpu_high_since": None,
    "cpu_warning_sent_for_episode": False,
}


def _in_quiet_hours():
    hour = time.localtime().tm_hour
    return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END


def _log_proactive_qa(message):
    entry = {
        "question": "[proactive]",
        "answer": message,
        "timestamp": time.time()
    }

    with state_lock:
        robot_state["qa_log"].append(entry)
        robot_state["qa_log"] = robot_state["qa_log"][-QA_LOG_MAX_ENTRIES:]


def _run_proactive_checks():
    with state_lock:
        if robot_state["speaking"]:
            return

    # User-scheduled reminders fire even during quiet hours — unlike the
    # autonomous nudges below, this is something the user explicitly asked
    # for at a specific time, not Atlas volunteering something.
    due_reminders = memory_store.pop_due_reminders()

    for reminder in due_reminders:
        message = f"Reminder: {reminder['message']}"
        _log_proactive_qa(message)
        _speak_text(message)

    if due_reminders:
        return

    if _in_quiet_hours():
        return

    now = time.time()

    cpu_percent = hud_stats.get_cpu_stats()["percent"]

    if cpu_percent >= CPU_WARNING_THRESHOLD:
        if _proactive_state["cpu_high_since"] is None:
            _proactive_state["cpu_high_since"] = now
            _proactive_state["cpu_warning_sent_for_episode"] = False
        elif (
            not _proactive_state["cpu_warning_sent_for_episode"]
            and now - _proactive_state["cpu_high_since"] >= CPU_WARNING_SUSTAINED_SECONDS
        ):
            _proactive_state["cpu_warning_sent_for_episode"] = True
            message = (
                f"Heads up, my own CPU usage has been above "
                f"{CPU_WARNING_THRESHOLD} percent for a few minutes."
            )
            _log_proactive_qa(message)
            _speak_text(message)
            return
    else:
        _proactive_state["cpu_high_since"] = None
        _proactive_state["cpu_warning_sent_for_episode"] = False

    pc = pc_stats.get_gaming_pc_stats()

    if pc.get("online"):
        cpu_temp = pc.get("cpu_temp_c")
        gpu_temp = pc.get("gpu_temp_c")

        if (
            cpu_temp is not None and cpu_temp >= PC_TEMP_ALERT_C
            and now - _proactive_state["last_cpu_alert"] >= PC_TEMP_COOLDOWN_SECONDS
        ):
            _proactive_state["last_cpu_alert"] = now
            message = (
                f"Heads up, your gaming PC's CPU is running at "
                f"{cpu_temp:.0f} degrees Celsius."
            )
            _log_proactive_qa(message)
            _speak_text(message)
            return

        if (
            gpu_temp is not None and gpu_temp >= PC_TEMP_ALERT_C
            and now - _proactive_state["last_gpu_alert"] >= PC_TEMP_COOLDOWN_SECONDS
        ):
            _proactive_state["last_gpu_alert"] = now
            message = (
                f"Heads up, your gaming PC's GPU is running at "
                f"{gpu_temp:.0f} degrees Celsius."
            )
            _log_proactive_qa(message)
            _speak_text(message)
            return

    weather = hud_stats.get_weather_stats()
    precip = weather.get("precip_chance")
    today = time.strftime("%Y-%m-%d")

    if (
        precip is not None and precip >= RAIN_ALERT_PERCENT
        and _proactive_state["last_rain_alert_date"] != today
    ):
        _proactive_state["last_rain_alert_date"] = today
        message = (
            f"Just so you know, there's a {precip:.0f} percent "
            "chance of rain today."
        )
        _log_proactive_qa(message)
        _speak_text(message)


def proactive_watcher_loop():
    while True:
        time.sleep(PROACTIVE_POLL_SECONDS)

        try:
            _run_proactive_checks()
        except Exception as error:
            print("Proactive watcher error:", type(error).__name__, error)


if __name__ == "__main__":
    threading.Thread(target=proactive_watcher_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5051, threaded=True)
