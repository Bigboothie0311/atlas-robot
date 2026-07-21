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

import alerts
import camera_gate
import hud_stats
import instagram_stats
import memory_store
import network_sentinel
import pc_power
import pc_stats
import timers
import weather_hud

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


def _default_agent_state():
    return {
        "active": False,
        "phase": "idle",
        "status": "idle",
        "task_id": None,
        "goal": None,
        "source": None,
        "plan_id": None,
        "step_count": 0,
        "current_step": 0,
        "completed_steps": 0,
        "tool_name": None,
        "target": None,
        "description": None,
        "evidence": {},
        "error": None,
        "retry_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "last_event": None,
        "updated_at": 0.0,
        "visible_until": 0.0,
    }


AGENT_EVIDENCE_MAX_KEYS = 6
AGENT_EVIDENCE_MAX_CHARS = 160


def _sanitize_agent_evidence(value):
    """Scalar-only, bounded evidence — /agent/event is an HTTP surface,
    so the hub re-bounds whatever arrives."""
    if not isinstance(value, dict):
        return {}

    sanitized = {}

    for key, item in value.items():
        if len(sanitized) >= AGENT_EVIDENCE_MAX_KEYS:
            break

        if isinstance(item, str):
            sanitized[str(key)] = item[:AGENT_EVIDENCE_MAX_CHARS]
        elif item is None or isinstance(item, (bool, int, float)):
            sanitized[str(key)] = item

    return sanitized


def _default_diagnostics_report():
    return {
        "findings": [],
        "ts": 0.0,
        "visible_until": 0.0,
    }


DIAGNOSTICS_DISPLAY_SECONDS = 30.0
DIAGNOSTICS_MAX_FINDINGS = 20
DIAGNOSTICS_MAX_DETAIL_CHARS = 200


def _sanitize_diagnostics_findings(findings):
    sanitized = []

    for finding in findings[:DIAGNOSTICS_MAX_FINDINGS]:
        if not isinstance(finding, dict):
            continue

        component = finding.get("component")
        detail = finding.get("detail")

        if not isinstance(component, str) or not component:
            continue

        sanitized.append({
            "component": component,
            "ok": bool(finding.get("ok")),
            "detail": (
                str(detail)[:DIAGNOSTICS_MAX_DETAIL_CHARS]
                if detail is not None
                else ""
            ),
        })

    return sanitized


def _expire_diagnostics_report_locked(now=None):
    """Clears an expired diagnostics report and, if the diagnostics
    layout is still up because of it, returns the HUD to idle."""
    now = time.time() if now is None else now
    report = robot_state.get("diagnostics_report") or {}
    visible_until = report.get("visible_until", 0.0)

    if not visible_until or now < visible_until:
        return

    robot_state["diagnostics_report"] = (
        _default_diagnostics_report()
    )

    if robot_state.get("hud_layout") == "diagnostics":
        robot_state["hud_layout"] = "idle"


robot_state = {
    "expression": "happy",
    "speaking": False,
    "activity_label": None,
    "image_path": None,
    "image_caption": None,
    "gallery_image_paths": [],
    "gallery_caption": None,
    "qa_log": [],
    # Contextual HUD layout: idle | red_alert | security | diagnostics.
    # The renderer picks the layout; most panels stay shared across them.
    "hud_layout": "idle",
    "intruder_records": [],
    "active_intruder_photo": None,
    "agent": _default_agent_state(),
    "diagnostics_report": _default_diagnostics_report(),
}

QA_LOG_MAX_ENTRIES = 20
AGENT_TERMINAL_DISPLAY_SECONDS = 8.0

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

# HDMI1 (vc4hdmi1) — routes speech through the connected screen's
# speakers instead of the GPIO/I2S MAX98357A amp. Addressed BY NAME, not
# card number: plugging in the USB camera (which carries its own audio
# interface) shifted every card number down one, silently pointing the
# old "plughw:3,0" at the disconnected HDMI port — every /speak then
# died with "audio open error: Unknown error 524" while wake detection
# kept working, which presented as "Atlas hears but never answers."
# ALSA card NAMES are stable across enumeration order; numbers are not.
AUDIO_DEVICE = "plughw:CARD=vc4hdmi1,DEV=0"

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
    response = send_from_directory(HUD_DIR, "index.html")
    # The kiosk Chromium must never serve a stale HUD after a deploy.
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/hud/static/<path:filename>")
def hud_static(filename):
    response = send_from_directory(HUD_DIR, filename)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/network_devices")
def network_devices():
    return jsonify({
        "ok": True,
        "devices": network_sentinel.get_online_devices(),
    })


@app.get("/hud/stats")
def hud_stats_route():
    stats = hud_stats.get_hud_stats()
    stats["network"]["device_count"] = network_sentinel.get_online_device_count()
    stats["network"]["devices"] = network_sentinel.get_online_devices()
    stats["printer"] = hud_stats.get_printer_stats()
    stats["printer"]["eta_minutes"] = alerts.print_eta_minutes()
    # Cache-only read — a cold headline cache must never stall the HUD's
    # 5s stats poll on a network fetch (the refresher thread fills it).
    stats["headlines"] = [
        headline["title"]
        for headline in hud_stats.get_headlines(allow_fetch=False)
    ]
    return jsonify(stats)


@app.get("/hud/weather")
def hud_weather_route():
    """Extended forecast + radar loop for the full-screen weather screen.
    Offline-tolerant by design (weather_hud returns a stale-flagged payload
    rather than erroring), so the overlay never has to handle a 5xx."""
    return jsonify(weather_hud.get_weather_forecast())


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
        _expire_agent_state_locked()
        _expire_diagnostics_report_locked()
        state = robot_state.copy()
        state["gallery_until"] = gallery_until

    state.update(timers.to_state_dict())
    state["auth"] = camera_gate.hud_status()
    state["red_alert"] = alerts.red_alert_state()

    try:
        import jarvis
        state["threat"] = jarvis.threat_level()
    except Exception:
        state["threat"] = {"level": "green", "reasons": ["all clear"]}

    # Expire a finished full-screen intruder photo so the HUD returns to
    # the security list on its own even if the caller never clears it.
    active_photo = state.get("active_intruder_photo")
    if active_photo and time.time() >= active_photo.get("until", 0):
        with state_lock:
            robot_state["active_intruder_photo"] = None
        state["active_intruder_photo"] = None

    # Red alert forces its layout regardless of what else was set.
    if state["red_alert"]["active"]:
        state["hud_layout"] = "red_alert"

    with _screen_lock:
        state["screen_dark"] = _screen_dark

    with _weather_overlay_lock:
        state["weather_overlay"] = _weather_overlay_open

    with _brightness_boost_lock:
        state["brightness_boost"] = _brightness_boost

    with _recording_lock:
        state["recording_active"] = _recording_active

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


def _event_int(value, default=0):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _expire_agent_state_locked(now=None):
    agent = robot_state.get("agent")

    if not isinstance(agent, dict):
        robot_state["agent"] = _default_agent_state()
        return

    current_time = time.time() if now is None else now
    visible_until = float(agent.get("visible_until") or 0.0)

    if (
        not agent.get("active")
        and visible_until
        and current_time >= visible_until
    ):
        robot_state["agent"] = _default_agent_state()


@app.post("/agent/event")
def set_agent_event():
    payload = request.get_json(silent=True) or {}
    event_name = str(payload.get("name", "")).strip()
    event_data = payload.get("data")

    if not event_name.startswith("agent."):
        return jsonify({
            "ok": False,
            "error": "A valid agent event name is required",
        }), 400

    if not isinstance(event_data, dict):
        return jsonify({
            "ok": False,
            "error": "Agent event data must be an object",
        }), 400

    supported = {
        "agent.planning.started",
        "agent.planning.completed",
        "agent.planning.failed",
        "agent.workflow.started",
        "agent.step.started",
        "agent.step.completed",
        "agent.workflow.completed",
        "agent.workflow.failed",
        "agent.workflow.waiting_confirmation",
    }

    if event_name not in supported:
        return jsonify({
            "ok": True,
            "ignored": True,
            "event": event_name,
        })

    now = time.time()

    with state_lock:
        current = robot_state.get("agent")

        if not isinstance(current, dict):
            current = _default_agent_state()
        else:
            current = dict(current)

        incoming_task_id = event_data.get("task_id")

        if (
            incoming_task_id
            and current.get("task_id")
            and incoming_task_id != current.get("task_id")
        ):
            current = _default_agent_state()

        if incoming_task_id:
            current["task_id"] = str(incoming_task_id)

        current["last_event"] = event_name
        current["updated_at"] = now

        if event_data.get("plan_id"):
            current["plan_id"] = str(event_data["plan_id"])

        if event_data.get("goal"):
            current["goal"] = str(event_data["goal"])

        event_source = event_data.get("source")

        if event_source:
            current["source"] = str(event_source)

        if event_name == "agent.planning.started":
            current.update({
                "active": True,
                "phase": "planning",
                "status": "running",
                "goal": str(event_data.get("goal") or ""),
                "source": str(event_data.get("source") or ""),
                "plan_id": None,
                "step_count": 0,
                "current_step": 0,
                "completed_steps": 0,
                "tool_name": None,
                "target": None,
                "description": "BUILDING EXECUTION PLAN",
                "evidence": {},
                "error": None,
                "retry_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "visible_until": 0.0,
            })

        elif event_name == "agent.planning.completed":
            attempts = _event_int(
                event_data.get("attempts")
            )
            current.update({
                "active": True,
                "phase": "plan_ready",
                "status": "running",
                "step_count": _event_int(
                    event_data.get("step_count")
                ),
                "description": "PLAN VALIDATED",
                "error": None,
                "retry_count": max(0, attempts - 1),
                "input_tokens": _event_int(
                    event_data.get("input_tokens")
                ),
                "output_tokens": _event_int(
                    event_data.get("output_tokens")
                ),
                "visible_until": 0.0,
            })

        elif event_name == "agent.planning.failed":
            current.update({
                "active": False,
                "phase": "failed",
                "status": "failed",
                "description": "PLANNING FAILED",
                "error": str(event_data.get("error") or ""),
                "visible_until": (
                    now + AGENT_TERMINAL_DISPLAY_SECONDS
                ),
            })

        elif event_name == "agent.workflow.started":
            current.update({
                "active": True,
                "phase": "executing",
                "status": "running",
                "goal": str(
                    event_data.get("goal")
                    or current.get("goal")
                    or ""
                ),
                "step_count": _event_int(
                    event_data.get("step_count")
                ),
                "current_step": 0,
                "completed_steps": 0,
                "tool_name": None,
                "target": None,
                "description": "EXECUTION STARTED",
                "evidence": {},
                "error": None,
                "visible_until": 0.0,
            })

        elif event_name == "agent.step.started":
            current.update({
                "active": True,
                "phase": "executing",
                "status": "running",
                "current_step": _event_int(
                    event_data.get("position")
                ),
                "tool_name": str(
                    event_data.get("tool_name") or ""
                ),
                "target": (
                    str(event_data.get("target"))
                    if event_data.get("target")
                    else None
                ),
                "description": str(
                    event_data.get("description")
                    or "EXECUTING STEP"
                ),
                "evidence": {},
                "error": None,
                "visible_until": 0.0,
            })

        elif event_name == "agent.step.completed":
            position = _event_int(
                event_data.get("position")
            )
            current.update({
                "active": True,
                "phase": "executing",
                "status": "running",
                "current_step": position,
                "completed_steps": max(
                    _event_int(
                        current.get("completed_steps")
                    ),
                    position,
                ),
                "tool_name": str(
                    event_data.get("tool_name")
                    or current.get("tool_name")
                    or ""
                ),
                "target": (
                    str(event_data.get("target"))
                    if event_data.get("target")
                    else current.get("target")
                ),
                "evidence": _sanitize_agent_evidence(
                    event_data.get("evidence")
                ),
                "visible_until": 0.0,
            })

        elif event_name == "agent.workflow.waiting_confirmation":
            current.update({
                "active": True,
                "phase": "waiting_confirmation",
                "status": "waiting_confirmation",
                "completed_steps": _event_int(
                    event_data.get("completed_steps")
                ),
                "description": "OWNER CONFIRMATION REQUIRED",
                "error": str(event_data.get("error") or ""),
                "visible_until": 0.0,
            })

        elif event_name == "agent.workflow.completed":
            completed = _event_int(
                event_data.get("completed_steps")
            )
            step_count = max(
                _event_int(current.get("step_count")),
                _event_int(event_data.get("step_count")),
                completed,
            )
            current.update({
                "active": False,
                "phase": "completed",
                "status": "completed",
                "step_count": step_count,
                "completed_steps": completed,
                "current_step": completed,
                "description": "MISSION COMPLETE",
                "error": None,
                "visible_until": (
                    now + AGENT_TERMINAL_DISPLAY_SECONDS
                ),
            })

        elif event_name == "agent.workflow.failed":
            current.update({
                "active": False,
                "phase": "failed",
                "status": "failed",
                "completed_steps": _event_int(
                    event_data.get("completed_steps")
                ),
                "description": "MISSION FAILED",
                "error": str(event_data.get("error") or ""),
                "visible_until": (
                    now + AGENT_TERMINAL_DISPLAY_SECONDS
                ),
            })

        robot_state["agent"] = current

    return jsonify({
        "ok": True,
        "event": event_name,
        "agent": current,
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


@app.post("/show_local_image")
def show_local_image():
    """Displays an image already on this Pi's filesystem (e.g. a PC
    screenshot the companion sent, decoded locally). Restricted to /tmp to
    avoid serving arbitrary paths."""
    global image_until

    data = request.get_json(silent=True) or {}
    path = str(data.get("path", "")).strip()
    caption = str(data.get("caption", "")).strip() or None

    try:
        duration = float(data.get("duration", IMAGE_DEFAULT_DURATION))
    except (TypeError, ValueError):
        duration = IMAGE_DEFAULT_DURATION

    duration = max(IMAGE_MIN_DURATION, min(IMAGE_MAX_DURATION, duration))

    if not path.startswith("/tmp/") or not os.path.exists(path):
        return jsonify({"ok": False, "error": "path must be an existing /tmp file"}), 400

    with state_lock:
        clear_gallery_state_locked()
        robot_state["image_path"] = path
        robot_state["image_caption"] = caption
        image_until = time.time() + duration

    return jsonify({"ok": True, "duration": duration})


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


@app.post("/security_review")
def security_review():
    """Switches the HUD to the Away/Security layout and loads the current
    intruder records (photo id, timestamp, denied commands) for it."""
    records = camera_gate.unreviewed_intruders()

    with state_lock:
        robot_state["hud_layout"] = "security"
        robot_state["intruder_records"] = [
            {
                "id": r["id"],
                "timestamp": r["timestamp"],
                "denied_commands": [d["command"] for d in r.get("denied_commands", [])],
            }
            for r in records
        ]

    return jsonify({"ok": True, "count": len(records)})


@app.post("/security_review/close")
def security_review_close():
    with state_lock:
        robot_state["hud_layout"] = "idle"
        robot_state["intruder_records"] = []
        robot_state["active_intruder_photo"] = None

    return jsonify({"ok": True})


@app.post("/show_intruder_photo")
def show_intruder_photo():
    """Full-screens a single intruder photo by record id for `duration`
    seconds. The photo is served from data/intruders/ via
    /hud/intruder_photo/<id>; the caller deletes it after display."""
    data = request.get_json(silent=True) or {}
    record_id = str(data.get("id", "")).strip()

    try:
        duration = float(data.get("duration", 10))
    except (TypeError, ValueError):
        duration = 10

    record = next(
        (r for r in camera_gate.unreviewed_intruders() if r["id"] == record_id),
        None,
    )

    if record is None or not record.get("photo") or not os.path.exists(record["photo"]):
        return jsonify({"ok": False, "error": "No such intruder photo"}), 404

    with state_lock:
        robot_state["active_intruder_photo"] = {
            "id": record_id,
            "timestamp": record["timestamp"],
            "denied_commands": [d["command"] for d in record.get("denied_commands", [])],
            "until": time.time() + duration,
        }

    return jsonify({"ok": True})


@app.get("/hud/intruder_photo/<record_id>")
def hud_intruder_photo(record_id):
    record = next(
        (r for r in camera_gate.unreviewed_intruders() if r["id"] == record_id),
        None,
    )

    if record is None or not record.get("photo") or not os.path.exists(record["photo"]):
        return jsonify({"ok": False, "error": "No such photo"}), 404

    return send_file(record["photo"])


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


@app.post("/dismiss")
def dismiss():
    """Restores the idle HUD without changing security or saved evidence."""
    with state_lock:
        clear_image_state_locked()
        clear_gallery_state_locked()
        robot_state["hud_layout"] = "idle"
        robot_state["intruder_records"] = []
        robot_state["active_intruder_photo"] = None
        robot_state["activity_label"] = None
        robot_state["expression"] = "happy"

    return jsonify({
        "ok": True,
        "layout": "idle",
        "security_preserved": True,
    })


@app.post("/timer")
def set_timer():
    data = request.get_json(silent=True) or {}

    try:
        seconds = int(data.get("seconds", 0))
    except (TypeError, ValueError):
        seconds = 0

    if seconds <= 0:
        return jsonify({"ok": False, "error": "seconds must be positive"}), 400

    label = str(data.get("label", "")).strip() or None
    started = timers.start_timer(seconds, label)

    return jsonify({"ok": True, "seconds": started, "label": label})


@app.post("/timer/cancel")
def cancel_timer():
    return jsonify({"ok": True, "cancelled": timers.cancel_timer()})


@app.get("/timer")
def get_timer():
    remaining = timers.get_timer_remaining()

    if remaining is None:
        return jsonify({"ok": True, "running": False})

    seconds, label = remaining
    return jsonify({
        "ok": True,
        "running": True,
        "remaining_seconds": seconds,
        "label": label,
    })


@app.post("/focus")
def start_focus():
    data = request.get_json(silent=True) or {}

    try:
        minutes = int(data.get("minutes", timers.DEFAULT_FOCUS_MINUTES))
    except (TypeError, ValueError):
        minutes = timers.DEFAULT_FOCUS_MINUTES

    seconds = timers.start_focus(minutes)
    return jsonify({"ok": True, "seconds": seconds})


@app.post("/focus/end")
def end_focus():
    return jsonify({"ok": True, "ended": timers.end_focus()})


_screen_lock = threading.Lock()
_screen_dark = False

_weather_overlay_lock = threading.Lock()
_weather_overlay_open = False

_brightness_boost_lock = threading.Lock()
_brightness_boost = False


@app.post("/screen")
def set_screen():
    """'Go dark' / 'lights up' — the HUD fades to near-black (rendered
    client-side from this flag) rather than cutting HDMI power, so it
    recovers instantly and survives compositor quirks."""
    global _screen_dark

    data = request.get_json(silent=True) or {}

    with _screen_lock:
        _screen_dark = bool(data.get("dark", False))
        dark = _screen_dark

    return jsonify({"ok": True, "dark": dark})


@app.post("/hud/weather_overlay")
def set_weather_overlay():
    """'Pull up the weather radar' — opens/closes the full-screen weather
    + radar overlay (see hud/app.js openWeatherOverlay). Flag-based, same
    pattern as /screen, so a local 'w' keypress on the kiosk and a voice
    command both converge on the same state."""
    global _weather_overlay_open

    data = request.get_json(silent=True) or {}

    with _weather_overlay_lock:
        _weather_overlay_open = bool(data.get("open", False))
        open_ = _weather_overlay_open

    return jsonify({"ok": True, "open": open_})


@app.post("/hud/brightness_boost")
def set_brightness_boost():
    """'Brighten the screen' — overrides the quiet-hours HUD dimming back
    to full brightness (e.g. for a demo at night) without leaving quiet
    hours mode entirely."""
    global _brightness_boost

    data = request.get_json(silent=True) or {}

    with _brightness_boost_lock:
        _brightness_boost = bool(data.get("boost", False))
        boost = _brightness_boost

    return jsonify({"ok": True, "boost": boost})


_recording_lock = threading.Lock()
_recording_active = False


@app.post("/hud/recording")
def set_recording():
    """Flag-based indicator (same pattern as /screen) so the HUD can show
    a visible cue while camera.capture_clip is recording A.T.L.A.S.
    himself — set True right before the capture starts, False in a
    finally block once it ends either way."""
    global _recording_active

    data = request.get_json(silent=True) or {}

    with _recording_lock:
        _recording_active = bool(data.get("active", False))
        active = _recording_active

    return jsonify({"ok": True, "active": active})


@app.post("/stand_down")
def stand_down():
    return jsonify({"ok": True, "was_active": alerts.stand_down()})


@app.post("/diagnostics_report")
def set_diagnostics_report():
    data = request.get_json(silent=True) or {}
    findings = data.get("findings")

    if not isinstance(findings, list):
        return jsonify({
            "ok": False,
            "error": "findings must be a list",
        }), 400

    sanitized = _sanitize_diagnostics_findings(findings)
    now = time.time()

    with state_lock:
        robot_state["diagnostics_report"] = {
            "findings": sanitized,
            "ts": now,
            "visible_until": (
                now + DIAGNOSTICS_DISPLAY_SECONDS
            ),
        }
        robot_state["hud_layout"] = "diagnostics"

    return jsonify({
        "ok": True,
        "count": len(sanitized),
    })


VALID_LAYOUTS = {"idle", "security", "diagnostics", "red_alert"}


@app.post("/layout")
def set_layout():
    data = request.get_json(silent=True) or {}
    layout = str(data.get("layout", "idle")).strip()

    if layout not in VALID_LAYOUTS:
        return jsonify({"ok": False, "error": "unknown layout"}), 400

    with state_lock:
        robot_state["hud_layout"] = layout

    return jsonify({"ok": True, "layout": layout})


@app.get("/phone")
def phone():
    return jsonify({"ok": True, **network_sentinel.phone_presence()})


@app.post("/wake_pc")
def wake_pc():
    message = pc_power.send_wake_packet()

    # Only chase confirmation when a signal actually went out — "already
    # on" and "no MAC known" need no follow-up.
    if message.startswith("Wake signal sent"):
        threading.Thread(
            target=pc_power.verify_wake,
            args=(_speak_text, _log_proactive_qa),
            daemon=True,
        ).start()

    return jsonify({"ok": True, "message": message})


def _speak_text(text, play=True):
    """Synthesizes text via Piper and, by default, plays it aloud. Raises
    on failure. Shared by the /speak route and the proactive watcher
    thread, so both go through the same state/expression handling and the
    same piper_lock. Playback can be cut short by POST /interrupt (e.g. a
    barge-in wake word) — that's a normal early return, not a failure.

    play=False synthesizes to a WAV without touching the speaker or the
    HUD "talking" state, and returns the WAV path instead of deleting it
    — the caller owns cleanup. Used for narration that needs to exist as
    audio (e.g. muxed into a self-showcase recording) without being heard
    out loud in the room. There's no lingering mute state to reset
    afterward: it's a per-call flag, so the very next normal speak() call
    plays aloud as usual."""
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

            if not play:
                return wav_path

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
        if play:
            with state_lock:
                robot_state["speaking"] = False
                robot_state["expression"] = previous_expression

            if wav_path and os.path.exists(wav_path):
                os.remove(wav_path)


CHIME_PATH = "/home/atlas/atlas-robot/data/chime.wav"
LISTENING_EARCON_PATH = "/home/atlas/atlas-robot/data/listening_earcon_v3.wav"


def _ensure_listening_earcon_exists():
    """Creates a bright, HUD-style "system engaged" earcon.

    An upward electronic chirp (with a detuned overtone for a metallic,
    digital edge) resolves into a crisp high confirm-tick, evoking a
    sci-fi interface activating rather than a plain notification beep.
    Still short and clean so it doesn't add perceptible delay before
    the mic opens.
    """
    if os.path.exists(LISTENING_EARCON_PATH):
        return

    import math
    import struct

    sample_rate = 44100
    duration = 0.32
    sweep_duration = 0.14
    tick_start = 0.15
    frame_count = int(sample_rate * duration)
    samples = []

    for frame in range(frame_count):
        t = frame / sample_rate

        # Chirp: exponential rise from 650 Hz to 1900 Hz reads as a
        # system "spinning up," reinforced by a detuned 1.5x overtone
        # for a metallic, bell-like digital edge.
        chirp_pulse = 0.0

        if t <= sweep_duration:
            sweep_progress = t / sweep_duration

            # Phase integral of an exponential (650 -> 1900 Hz) chirp.
            phase = (
                2 * math.pi * 650.0 * sweep_duration
                / math.log(1900.0 / 650.0)
                * ((1900.0 / 650.0) ** sweep_progress - 1.0)
            )
            chirp_envelope = (
                min(1.0, t / 0.006)
                * min(1.0, (sweep_duration - t) / 0.03 + 0.15)
            )
            chirp_pulse = chirp_envelope * (
                0.22 * math.sin(phase)
                + 0.07 * math.sin(1.5 * phase + 0.4)
            )

        # Confirm tick: short, bright, and percussive — the "engaged"
        # punctuation mark at the end of the sweep.
        tick_t = t - tick_start
        tick_pulse = 0.0

        if tick_t >= 0.0:
            tick_envelope = (
                min(1.0, tick_t / 0.003)
                * math.exp(-40.0 * tick_t)
            )
            tick_phase = 2 * math.pi * 2400.0 * tick_t
            tick_pulse = tick_envelope * (
                0.20 * math.sin(tick_phase)
                + 0.08 * math.sin(1.5 * tick_phase)
            )

        master_fade = min(1.0, (duration - t) / 0.02)
        value = chirp_pulse + tick_pulse
        samples.append(int(max(-1.0, min(1.0, value)) * master_fade * 32767))

    os.makedirs(os.path.dirname(LISTENING_EARCON_PATH), exist_ok=True)

    with wave.open(LISTENING_EARCON_PATH, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def _play_listening_earcon():
    """Synchronously plays the listening cue through Atlas's HDMI audio."""
    _ensure_listening_earcon_exists()

    with piper_lock:
        result = subprocess.run(
            ["aplay", "-D", AUDIO_DEVICE, LISTENING_EARCON_PATH],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"listening earcon playback exited with code {result.returncode}"
        )


def _ensure_chime_exists():
    """Generates the timer chime on first use — a two-note ding (E5 then
    A5) with exponential decay, pure stdlib, so no binary asset needs to
    live in the repo."""
    if os.path.exists(CHIME_PATH):
        return

    import math
    import struct

    sample_rate = 22050
    notes = [(659.25, 0.28), (880.0, 0.42)]
    samples = []

    for frequency, duration in notes:
        count = int(sample_rate * duration)

        for i in range(count):
            t = i / sample_rate
            envelope = math.exp(-6.0 * t / duration)
            value = 0.55 * envelope * math.sin(2 * math.pi * frequency * t)
            samples.append(int(value * 32767))

    os.makedirs(os.path.dirname(CHIME_PATH), exist_ok=True)

    with wave.open(CHIME_PATH, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def _play_chime():
    """Audible ding through the same output device as speech. Best-effort —
    a missing/broken chime must never block the spoken announcement."""
    try:
        _ensure_chime_exists()
        subprocess.run(
            ["aplay", "-D", AUDIO_DEVICE, CHIME_PATH],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception as error:
        print("Chime playback failed:", type(error).__name__, error, flush=True)


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
    play = bool(data.get("play", True))

    if not text:
        return jsonify({
            "ok": False,
            "error": "No text provided"
        }), 400

    try:
        wav_path = _speak_text(text, play=play)

        return jsonify({
            "ok": True,
            "spoken": text,
            "voice": PIPER_MODEL,
            "wav_path": wav_path if not play else None
        })

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error)
        }), 500


@app.post("/listening_earcon")
def listening_earcon():
    try:
        _play_listening_earcon()
        return jsonify({"ok": True, "duration_seconds": 0.32})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


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
    "last_printer_state": None,
}

PRINTER_DONE_STATES = {"ready", "completed", "finished", "idle", "done"}
PRINTER_FAILED_STATES = {"error", "failed", "fault"}
# "building" is what the AD5X actually reports mid-print — confirmed live
# against a real running job (13%, layer 6/724) during development.
PRINTER_ACTIVE_STATES = {"printing", "busy", "running", "building"}


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

    # Red alert outranks quiet hours AND focus mode — a core overheating
    # or a failed print is exactly the thing worth interrupting for. It
    # announces once per episode (evaluate_red_alert handles that) and
    # the HUD stays in alert theme until "stand down" or self-clear.
    alert_printer = hud_stats.get_printer_stats()
    alert_announcement = alerts.evaluate_red_alert(
        hud_stats.get_cpu_stats().get("temp_c"),
        hud_stats.get_disk_stats()["percent"],
        bool(
            alert_printer.get("online")
            and alert_printer.get("state") in PRINTER_FAILED_STATES
        ),
    )

    if alert_announcement is not None:
        _play_chime()
        _play_chime()
        _log_proactive_qa(alert_announcement)
        _speak_text(alert_announcement)
        return

    # Focus mode mutes every autonomous nudge below — the user asked for
    # uninterrupted time; reminders above still fire since those are
    # explicitly scheduled.
    if timers.in_focus():
        return

    if _in_quiet_hours():
        return

    now = time.time()

    # Print-job transitions: announce a print finishing or failing. The
    # state only moves while atlas-hub is reachable and a job was seen
    # actively printing first, so a flaky printer link can't false-fire.
    printer = hud_stats.get_printer_stats()
    printer_state = printer.get("state") if printer.get("online") else None
    last_printer_state = _proactive_state["last_printer_state"]

    # Feed the ETA extrapolator every poll while a job runs.
    alerts.record_print_sample(printer_state, printer.get("progress_percent"))

    if printer_state is not None:
        _proactive_state["last_printer_state"] = printer_state

        if last_printer_state in PRINTER_ACTIVE_STATES:
            if printer_state in PRINTER_DONE_STATES:
                message = "Your 3D print just finished."
                _log_proactive_qa(message)
                _speak_text(message)
                return

            if printer_state in PRINTER_FAILED_STATES:
                message = "Heads up — your 3D print looks like it failed."
                _log_proactive_qa(message)
                _speak_text(message)
                return

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
        # Opportunistically learn the PC's MAC while it's reachable so
        # "power on my PC" works later when it's off (one `ip neigh show`
        # every poll — trivial).
        pc_power.refresh_mac_cache()

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


HEADLINES_REFRESH_SECONDS = 15 * 60
INSTAGRAM_REFRESH_SECONDS = 15 * 60


def headlines_refresher_loop():
    """Keeps the news-ticker cache warm off the request path — the
    /hud/stats route reads cache-only so a slow ddgs fetch can never
    stall the HUD's poll."""
    while True:
        try:
            hud_stats.get_headlines()
        except Exception as error:
            print("Headline refresh error:", type(error).__name__, error)

        time.sleep(HEADLINES_REFRESH_SECONDS)


def instagram_refresher_loop():
    """Keep cached social stats off the HUD request path."""
    while True:
        try:
            instagram_stats.get_stats()
        except Exception as error:
            print("Instagram refresh error:", type(error).__name__, error)

        time.sleep(INSTAGRAM_REFRESH_SECONDS)


def _sentinel_should_stay_quiet():
    return _in_quiet_hours() or timers.in_focus()


# Secure phone link — token-authed /phone/* routes (see PHONE_LINK.md).
# Registered unconditionally; the routes themselves refuse service with
# 503 until PHONE_TOKEN is configured, so this is inert until set up.
try:
    import pc_control
    import phone_api

    phone_api.register(
        app, _speak_text, _append_qa_log, camera_gate, hud_stats, pc_control
    )
except Exception as _phone_error:
    print("Phone link registration failed:", _phone_error, flush=True)


if __name__ == "__main__":
    threading.Thread(target=proactive_watcher_loop, daemon=True).start()
    threading.Thread(
        target=timers.watcher_loop,
        args=(_speak_text, _log_proactive_qa, _play_chime),
        daemon=True,
    ).start()
    threading.Thread(
        target=network_sentinel.sentinel_loop,
        args=(_speak_text, _log_proactive_qa, _sentinel_should_stay_quiet),
        daemon=True,
    ).start()
    threading.Thread(target=headlines_refresher_loop, daemon=True).start()
    threading.Thread(target=instagram_refresher_loop, daemon=True).start()

    # Self-healing core — silent unless it actually repairs something.
    try:
        import self_healing
        threading.Thread(
            target=self_healing.monitor_loop,
            args=(_speak_text, _log_proactive_qa, _sentinel_should_stay_quiet),
            daemon=True,
        ).start()
    except Exception as _heal_error:
        print("Self-healing thread failed to start:", _heal_error, flush=True)

    app.run(host="0.0.0.0", port=5051, threaded=True)
