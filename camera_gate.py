"""Authorized-user camera gate.

The camera runs ONLY on activation — a wake-up whose last successful
verification is older than the validity window triggers one capture-and-
verify pass; nothing streams continuously. Recognition is fully local
(OpenCV LBPH, zero tokens): the owner enrolls via "learn my face", crops
live in data/faces/authorized/, and the trained model in
data/face_model.yml.

Outcomes per verify: "authorized" (full functionality), "unauthorized"
(a face that isn't the owner — the frame is saved to data/intruders/ and
the turn is restricted to local-only commands), or "no_face" (nobody
visible — also restricted, but nothing is logged as an intruder).

State lives in data/auth_state.json so the hub can render camera-gate
status on the HUD and both processes agree on enablement.
"""
import json
import subprocess
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
CAPTURE_PATH = "/tmp/atlas_gate_capture.jpg"

# A successful verification is good for this long; the next wake after it
# lapses re-verifies.
VALIDITY_WINDOW_SECONDS = 10 * 60

# LBPH distance — LOWER is more similar. Accept below this.
LBPH_ACCEPT_THRESHOLD = 70.0

ENROLL_FRAME_COUNT = 8
VERIFY_ATTEMPTS = 3

FACE_CROP_SIZE = (200, 200)

_face_cascade = None
_recognizer = None
_model_loaded_at = 0.0


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
    """The gate can only run with OpenCV installed and a trained model."""
    return cv2 is not None and MODEL_PATH.exists()


def is_enabled():
    return bool(_load_state().get("enabled", True))


def set_enabled(enabled):
    _save_state({"enabled": bool(enabled)})


def is_verification_current():
    last = float(_load_state().get("last_verified_at", 0.0))
    return time.time() - last < VALIDITY_WINDOW_SECONDS


def mark_verified():
    _save_state({"last_verified_at": time.time()})


def capture_frame(path=CAPTURE_PATH):
    """One still off the USB camera via v4l2. Returns the path or None."""
    try:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "v4l2", "-input_format", "mjpeg",
                "-video_size", "640x480",
                "-i", CAMERA_DEVICE,
                "-frames:v", "1", "-update", "1",
                path,
            ],
            check=True,
            timeout=15,
        )
        return path
    except (subprocess.SubprocessError, OSError) as error:
        print("Camera capture failed:", type(error).__name__, error, flush=True)
        return None


# OpenCV 5 wheels no longer bundle the Haar cascades — this comes from
# Debian's opencv-data package instead (apt install opencv-data).
FACE_CASCADE_PATH = (
    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
)


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
    image = cv2.imread(image_path)

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


def enroll_frame(index):
    """Captures one enrollment frame. Returns True if a face was saved."""
    path = capture_frame()

    if path is None:
        return False

    crop = _detect_face_crop(path)

    if crop is None:
        return False

    FACES_DIR.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(FACES_DIR / f"face_{index:02d}.jpg"), crop)
    return True


def train_model():
    """(Re)trains LBPH from every enrolled crop. Returns the crop count."""
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


def _get_recognizer():
    global _recognizer, _model_loaded_at

    model_mtime = MODEL_PATH.stat().st_mtime

    if _recognizer is None or model_mtime > _model_loaded_at:
        _recognizer = cv2.face.LBPHFaceRecognizer_create()
        _recognizer.read(str(MODEL_PATH))
        _model_loaded_at = model_mtime

    return _recognizer


def _save_intruder_frame():
    INTRUDERS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = INTRUDERS_DIR / f"intruder_{stamp}.jpg"

    try:
        destination.write_bytes(Path(CAPTURE_PATH).read_bytes())
    except OSError as error:
        print("Intruder frame save failed:", error, flush=True)


def verify():
    """One activation-triggered check. Returns 'authorized',
    'unauthorized', or 'no_face'. A few attempts ride out blinks and
    mid-motion frames; a stranger's frame is kept as evidence."""
    if not is_available():
        return "authorized"  # No model/deps — gate can't gate.

    saw_face = False

    for _ in range(VERIFY_ATTEMPTS):
        path = capture_frame()

        if path is None:
            continue

        crop = _detect_face_crop(path)

        if crop is None:
            time.sleep(0.4)
            continue

        saw_face = True
        _, distance = _get_recognizer().predict(crop)
        print(f"Face gate: LBPH distance {distance:.1f} "
              f"(accept < {LBPH_ACCEPT_THRESHOLD})", flush=True)

        if distance < LBPH_ACCEPT_THRESHOLD:
            mark_verified()
            return "authorized"

    if saw_face:
        _save_intruder_frame()
        return "unauthorized"

    return "no_face"


def unreviewed_intruders():
    """Intruder captures newer than the last review, newest first."""
    last_review = float(_load_state().get("last_review_at", 0.0))

    if not INTRUDERS_DIR.exists():
        return []

    return sorted(
        (p for p in INTRUDERS_DIR.glob("*.jpg")
         if p.stat().st_mtime > last_review),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def mark_intruders_reviewed():
    _save_state({"last_review_at": time.time()})


def hud_status():
    """Camera-gate state for the HUD: OFF / UNTRAINED / VERIFIED / STALE,
    plus how many intruder shots await review."""
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
