"""Authorized-user camera gate.

The camera runs ONLY on activation — a wake-up whose last successful
verification is older than the validity window triggers one burst-capture-
and-verify pass; nothing streams continuously. Recognition is fully local
(OpenCV LBPH, zero tokens): the owner enrolls via "learn my face", crops
live in data/faces/authorized/, the trained model in data/face_model.yml.

A verify grabs a short BURST of frames in a single camera session (one
ffmpeg spawn, not one per frame — that camera-open overhead was the
"long delay"), runs LBPH on every detectable face, and combines the
results by majority vote so a single bad frame (blink, motion, glance)
can't decide the outcome.

Outcomes: "authorized" (full functionality), "unauthorized" (a face that
isn't the owner — recorded to the intruder log with its photo and any
commands it then tries), or "no_face" (nobody visible — restricted but
not logged as an intruder).
"""
import json
import subprocess
import tempfile
import time
from pathlib import Path

try:
    import cv2
    import numpy
except ImportError:  # Gate silently unavailable rather than crashing voice.
    cv2 = None
    numpy = None

CAMERA_DEVICE = (
    "/dev/v4l/by-id/"
    "usb-icSpring_icspring_camera_202404260001-video-index0"
)

DATA_DIR = Path("/home/atlas/atlas-robot/data")
AUTH_STATE_PATH = DATA_DIR / "auth_state.json"
FACES_DIR = DATA_DIR / "faces" / "authorized"
MODEL_PATH = DATA_DIR / "face_model.yml"
INTRUDERS_DIR = DATA_DIR / "intruders"
INTRUDER_LOG_PATH = DATA_DIR / "intruder_log.json"
# Single rolling file for the most recent successful verification — the
# task requires these NOT to accumulate. Enrollment crops in FACES_DIR are
# never touched by rotation.
VERIFIED_CAPTURE_PATH = DATA_DIR / "last_verified.jpg"

VALIDITY_WINDOW_SECONDS = 60 * 60  # 1 hour (was 10 min)

# LBPH distance — LOWER is more similar. Accept below this.
LBPH_ACCEPT_THRESHOLD = 70.0

# Enrollment: capture a generous burst, keep the quality faces.
ENROLL_BURST_FRAMES = 20
ENROLL_MIN_CROPS = 5
ENROLL_MAX_CROPS = 15

# Verification: burst of frames, decide by majority vote over detected
# faces. Require a minimum number of usable (face-bearing) frames so a
# near-empty burst doesn't authorize on one lucky match.
VERIFY_BURST_FRAMES = 8
VERIFY_MIN_USABLE_FRAMES = 2

FACE_CROP_SIZE = (200, 200)

_face_cascade = None
_recognizer = None
_model_loaded_at = 0.0

FACE_CASCADE_PATH = (
    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
)


# ---------------------------------------------------------------------
# State
# ---------------------------------------------------------------------

def _load_state():
    if not AUTH_STATE_PATH.exists():
        return {}

    try:
        return json.loads(AUTH_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(updates):
    state = _load_state()
    state.update(updates)
    AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = AUTH_STATE_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(state, indent=2))
    temporary_path.replace(AUTH_STATE_PATH)


def is_available():
    return cv2 is not None and MODEL_PATH.exists()


def is_enabled():
    return bool(_load_state().get("enabled", True))


def set_enabled(enabled):
    _save_state({"enabled": bool(enabled)})


def is_verification_current():
    last = float(_load_state().get("last_verified_at", 0.0))
    return time.time() - last < VALIDITY_WINDOW_SECONDS


def arm_gate(reason="departure"):
    """Forces the NEXT interaction to re-verify — used when the owner says
    'I'm leaving' or the phone leaves the LAN. Idempotent."""
    _save_state({"armed": True, "armed_reason": reason})


def should_verify():
    """Decides whether this wake needs a face check. The gate stays quiet
    (trusts the last auth) UNLESS:
      - a departure armed it ('I'm leaving' / phone left), or
      - it's been over an hour since the last successful auth, or
      - the last face seen was unauthorized and no authorized user has
        cleared it yet — in that case it re-checks EVERY wake until an
        authorized user appears (or the stranger stops trying)."""
    state = _load_state()

    if state.get("pending_unauthorized"):
        return True
    if state.get("armed"):
        return True
    return not is_verification_current()


def mark_verified():
    """An authorized user is present — reset the window and clear both the
    departure arm and any pending-unauthorized state."""
    _save_state({
        "last_verified_at": time.time(),
        "armed": False,
        "armed_reason": None,
        "pending_unauthorized": False,
    })


def mark_unauthorized():
    """A stranger was seen — keep re-verifying every wake until an
    authorized user clears it."""
    _save_state({"pending_unauthorized": True})


# ---------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------

def capture_burst(frame_count, directory):
    """Grabs frame_count consecutive frames in ONE camera session. Returns
    a sorted list of frame paths (may be shorter than requested)."""
    pattern = str(Path(directory) / "frame_%03d.jpg")

    try:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "v4l2", "-input_format", "mjpeg",
                "-video_size", "640x480",
                "-i", CAMERA_DEVICE,
                "-frames:v", str(frame_count), "-vsync", "0",
                pattern,
            ],
            check=True,
            timeout=20,
        )
    except (subprocess.SubprocessError, OSError) as error:
        print("Camera burst failed:", type(error).__name__, error, flush=True)

    return sorted(Path(directory).glob("frame_*.jpg"))


def capture_frame(path=str(DATA_DIR / "tmp_capture.jpg")):
    """Single still — used only where one frame is genuinely enough."""
    with tempfile.TemporaryDirectory() as directory:
        frames = capture_burst(1, directory)

        if not frames:
            return None

        Path(path).write_bytes(frames[0].read_bytes())
        return path


# ---------------------------------------------------------------------
# Detection / recognition
# ---------------------------------------------------------------------

def _get_face_cascade():
    global _face_cascade

    if _face_cascade is None:
        _face_cascade = cv2.CascadeClassifier(FACE_CASCADE_PATH)

        if _face_cascade.empty():
            raise RuntimeError(
                f"Face cascade missing at {FACE_CASCADE_PATH} — "
                "install the opencv-data package"
            )

    return _face_cascade


def _detect_face_crop(image_path):
    """Largest face in the image as a normalized grayscale crop, or None."""
    image = cv2.imread(str(image_path))

    if image is None:
        return None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = _get_face_cascade().detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80)
    )

    if len(faces) == 0:
        return None

    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    return cv2.resize(gray[y:y + h, x:x + w], FACE_CROP_SIZE)


def _get_recognizer():
    global _recognizer, _model_loaded_at

    model_mtime = MODEL_PATH.stat().st_mtime

    if _recognizer is None or model_mtime > _model_loaded_at:
        _recognizer = cv2.face.LBPHFaceRecognizer_create()
        _recognizer.read(str(MODEL_PATH))
        _model_loaded_at = model_mtime

    return _recognizer


# ---------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------

def enroll(progress=None):
    """Burst-captures enrollment frames, keeps the quality face crops, and
    trains the model. progress(step) is an optional callback for spoken
    prompts. Returns the number of crops trained on (0 = failure)."""
    if cv2 is None:
        return 0

    FACES_DIR.mkdir(parents=True, exist_ok=True)

    for old in FACES_DIR.glob("*.jpg"):
        old.unlink()

    saved = 0

    with tempfile.TemporaryDirectory() as directory:
        if progress:
            progress("start")

        frames = capture_burst(ENROLL_BURST_FRAMES, directory)

        for frame in frames:
            if saved >= ENROLL_MAX_CROPS:
                break

            crop = _detect_face_crop(frame)

            if crop is not None:
                cv2.imwrite(str(FACES_DIR / f"face_{saved:02d}.jpg"), crop)
                saved += 1

    if saved < ENROLL_MIN_CROPS:
        return 0

    return _train_model()


def _train_model():
    crops = sorted(FACES_DIR.glob("*.jpg"))

    if not crops:
        return 0

    images = [cv2.imread(str(p), cv2.IMREAD_GRAYSCALE) for p in crops]
    images = [img for img in images if img is not None]
    labels = numpy.zeros(len(images), dtype=numpy.int32)

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(images, labels)
    recognizer.write(str(MODEL_PATH))
    return len(images)


# ---------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------

def _rotate_verified_capture(source_frame):
    """Keeps only the newest verified-user capture. Overwrites the single
    rolling file — never accumulates, never touches enrollment data."""
    if source_frame is None:
        return

    try:
        VERIFIED_CAPTURE_PATH.write_bytes(Path(source_frame).read_bytes())
    except OSError as error:
        print("Verified capture rotate failed:", error, flush=True)


def verify():
    """Burst-capture + majority-vote check. Returns 'authorized',
    'unauthorized', or 'no_face'. On unauthorized, opens a new intruder
    record; its id is stashed so a following restricted turn can attach
    the commands the stranger then tries."""
    global _last_intruder_id

    if not is_available():
        return "authorized"  # No model/deps — gate can't gate.

    with tempfile.TemporaryDirectory() as directory:
        frames = capture_burst(VERIFY_BURST_FRAMES, directory)

        accept_votes = 0
        reject_votes = 0
        best_accept_frame = None
        first_face_frame = None

        for frame in frames:
            crop = _detect_face_crop(frame)

            if crop is None:
                continue

            if first_face_frame is None:
                first_face_frame = frame

            _, distance = _get_recognizer().predict(crop)

            if distance < LBPH_ACCEPT_THRESHOLD:
                accept_votes += 1
                best_accept_frame = frame
            else:
                reject_votes += 1

        usable = accept_votes + reject_votes
        print(
            f"Face gate vote: {accept_votes} accept / {reject_votes} reject "
            f"over {usable} usable frames",
            flush=True,
        )

        if usable < VERIFY_MIN_USABLE_FRAMES:
            return "no_face"

        if accept_votes >= reject_votes:
            mark_verified()
            _rotate_verified_capture(best_accept_frame or first_face_frame)
            return "authorized"

        mark_unauthorized()
        _last_intruder_id = _record_intruder(first_face_frame)
        return "unauthorized"


# ---------------------------------------------------------------------
# Intruder records
# ---------------------------------------------------------------------

_last_intruder_id = None


def _load_intruder_log():
    if not INTRUDER_LOG_PATH.exists():
        return []

    try:
        data = json.loads(INTRUDER_LOG_PATH.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_intruder_log(records):
    INTRUDER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = INTRUDER_LOG_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(records, indent=2))
    temporary_path.replace(INTRUDER_LOG_PATH)


def _record_intruder(source_frame):
    """Saves the intruder's photo and opens a log record. Returns its id."""
    INTRUDERS_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    record_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    photo_path = INTRUDERS_DIR / f"intruder_{record_id}.jpg"

    try:
        if source_frame is not None:
            photo_path.write_bytes(Path(source_frame).read_bytes())
    except OSError as error:
        print("Intruder photo save failed:", error, flush=True)

    records = _load_intruder_log()
    records.append({
        "id": record_id,
        "photo": str(photo_path),
        "timestamp": now,
        "denied_commands": [],
        "reviewed": False,
    })
    _save_intruder_log(records)
    return record_id


def record_denied_command(command):
    """Attaches a denied command to the most recent open intruder record.
    Called by the restricted-turn handler when a stranger tries something
    beyond local basics."""
    if _last_intruder_id is None:
        return

    records = _load_intruder_log()

    for record in records:
        if record["id"] == _last_intruder_id:
            record["denied_commands"].append({
                "command": command,
                "at": time.time(),
            })
            _save_intruder_log(records)
            return


def unreviewed_intruders():
    """Open intruder records (newest first), each with photo, timestamp,
    and denied commands. Fast — reads one JSON file, no camera work."""
    records = [r for r in _load_intruder_log() if not r.get("reviewed")]
    return sorted(records, key=lambda r: r["timestamp"], reverse=True)


def delete_intruder_photo(record_id):
    """Deletes an intruder's photo after it has been displayed on request."""
    records = _load_intruder_log()

    for record in records:
        if record["id"] == record_id:
            photo = Path(record.get("photo") or "")

            if photo and photo.exists():
                try:
                    photo.unlink()
                except OSError:
                    pass

            record["photo"] = None
            _save_intruder_log(records)
            return


def mark_intruders_reviewed():
    """Scrubs the alert: marks every open record reviewed."""
    records = _load_intruder_log()

    for record in records:
        record["reviewed"] = True

    _save_intruder_log(records)


def hud_status():
    if cv2 is None or not MODEL_PATH.exists():
        status = "UNTRAINED"
    elif not is_enabled():
        status = "OFF"
    elif is_verification_current():
        status = "VERIFIED"
    else:
        status = "STALE"

    return {
        "status": status,
        "unreviewed_intruders": len(unreviewed_intruders()),
    }
