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

If a bad existing model prevents voice re-enrollment, the owner can run
``venv/bin/python camera_gate.py enroll`` from the local/SSH shell. That
recovery path is intentionally unavailable to an unverified voice user.
"""
import argparse
import json
import shutil
import statistics
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
# A handful of much stronger matches can also authorize even when the owner
# looks away during the latter part of the burst. This is deliberately far
# stricter than the normal threshold and still requires several frames.
LBPH_STRONG_THRESHOLD = 55.0

# Enrollment deliberately pauses at several small pose changes. The old
# implementation captured 20 consecutive frames while the owner held still,
# which produced many near-duplicates of one pose instead of useful coverage.
ENROLL_POSES = (
    "center",
    "left",
    "right",
    "up",
    "down",
    "tilt_left",
    "tilt_right",
)
ENROLL_POSE_PROMPTS = {
    "center": "First, look straight at the camera.",
    "left": "Turn your head slightly to your left.",
    "right": "Now turn your head slightly to your right.",
    "up": "Face forward and lift your chin slightly.",
    "down": "Face forward and lower your chin slightly.",
    "tilt_left": "Tilt your head slightly toward your left shoulder.",
    "tilt_right": "Last one. Tilt slightly toward your right shoulder.",
}
ENROLL_FRAMES_PER_POSE = 8
ENROLL_SAMPLE_FPS = 4
ENROLL_CROPS_PER_POSE = 5
ENROLL_MIN_CROPS = 20
ENROLL_MIN_POSES = 4

# Verification: burst of frames, decide by majority vote over detected
# faces. Sampling over a little more than two seconds gives exposure and
# autofocus time to settle and avoids deciding from a handful of adjacent
# frames. Ties never authorize.
VERIFY_BURST_FRAMES = 18
VERIFY_SAMPLE_FPS = 8
VERIFY_MIN_USABLE_FRAMES = 5
VERIFY_MIN_ACCEPT_VOTES = 3
VERIFY_MIN_STRONG_VOTES = 5

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

def capture_burst(frame_count, directory, sample_fps=None):
    """Grabs frame_count consecutive frames in ONE camera session. Returns
    a sorted list of frame paths (may be shorter than requested).

    sample_fps spaces the saved frames across time instead of retaining a
    cluster of nearly identical native-rate camera frames.
    """
    output_dir = Path(directory)
    output_dir.mkdir(parents=True, exist_ok=True)

    for old_frame in output_dir.glob("frame_*.jpg"):
        old_frame.unlink()

    pattern = str(output_dir / "frame_%03d.jpg")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "v4l2", "-input_format", "mjpeg",
        "-video_size", "640x480",
        "-i", CAMERA_DEVICE,
    ]

    if sample_fps:
        command.extend(["-vf", f"fps={sample_fps}"])

    command.extend([
        "-frames:v", str(frame_count), "-vsync", "0",
        pattern,
    ])

    expected_seconds = frame_count / sample_fps if sample_fps else 0
    timeout_seconds = max(20, int(expected_seconds) + 15)

    try:
        subprocess.run(
            command,
            check=True,
            timeout=timeout_seconds,
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


def _detect_face_crop_with_quality(image_path):
    """Returns (normalized largest-face crop, quality score), or None.

    The score is used only to retain the sharpest samples from each pose;
    it is not an identity score and cannot authorize anybody.
    """
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
    crop = cv2.resize(gray[y:y + h, x:x + w], FACE_CROP_SIZE)
    sharpness = float(cv2.Laplacian(crop, cv2.CV_64F).var())
    contrast = float(crop.std())
    return crop, sharpness + contrast


def _detect_face_crop(image_path):
    """Largest face in the image as a normalized grayscale crop, or None."""
    detected = _detect_face_crop_with_quality(image_path)
    return detected[0] if detected is not None else None


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

def _install_enrollment(staged_faces, staged_model):
    """Atomically installs a fully trained enrollment.

    Existing crops stay in place until the replacement model and its entire
    crop set are ready. A capture or training failure therefore cannot erase
    the last known-good owner enrollment.
    """
    FACES_DIR.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup_faces = FACES_DIR.with_name(
        f".{FACES_DIR.name}.backup-{time.time_ns()}"
    )
    moved_old_faces = False
    installed_new_faces = False

    try:
        if FACES_DIR.exists():
            FACES_DIR.replace(backup_faces)
            moved_old_faces = True

        staged_faces.replace(FACES_DIR)
        installed_new_faces = True
        staged_model.replace(MODEL_PATH)
    except Exception:
        if installed_new_faces and FACES_DIR.exists():
            shutil.rmtree(FACES_DIR, ignore_errors=True)

        if moved_old_faces and backup_faces.exists():
            backup_faces.replace(FACES_DIR)

        raise
    else:
        if backup_faces.exists():
            shutil.rmtree(backup_faces, ignore_errors=True)


def enroll(progress=None):
    """Guided multi-angle enrollment with transactional installation.

    Seven prompted poses are sampled over time. The sharpest crops from
    each successful pose are trained as a new model in a staging directory;
    the previous enrollment is replaced only after all checks pass.
    progress(step) receives values such as ``pose:center``.
    """
    global _recognizer, _model_loaded_at

    if cv2 is None:
        return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=".face-enroll-", dir=DATA_DIR))
    staged_faces = staging_root / "authorized"
    staged_model = staging_root / "face_model.yml"
    staged_faces.mkdir()
    saved = 0
    pose_counts = {}

    try:
        for pose in ENROLL_POSES:
            if progress:
                progress(f"pose:{pose}")

            pose_dir = staging_root / f"frames-{pose}"
            frames = capture_burst(
                ENROLL_FRAMES_PER_POSE,
                pose_dir,
                sample_fps=ENROLL_SAMPLE_FPS,
            )
            candidates = []

            for frame in frames:
                detected = _detect_face_crop_with_quality(frame)

                if detected is not None:
                    candidates.append(detected)

            candidates.sort(key=lambda item: item[1], reverse=True)
            selected = candidates[:ENROLL_CROPS_PER_POSE]
            pose_counts[pose] = len(selected)

            for crop, _quality in selected:
                path = staged_faces / f"face_{saved:02d}_{pose}.jpg"

                if cv2.imwrite(str(path), crop):
                    saved += 1

        completed_poses = sum(count > 0 for count in pose_counts.values())

        if saved < ENROLL_MIN_CROPS or completed_poses < ENROLL_MIN_POSES:
            print(
                "Face enrollment rejected: "
                f"{saved} crops across {completed_poses} poses; "
                f"pose counts={pose_counts}",
                flush=True,
            )
            return 0

        trained = _train_model(
            crops=sorted(staged_faces.glob("*.jpg")),
            model_path=staged_model,
        )

        if trained < ENROLL_MIN_CROPS:
            return 0

        _install_enrollment(staged_faces, staged_model)
        _recognizer = None
        _model_loaded_at = 0.0
        _save_state({
            "enrolled_at": time.time(),
            "enrollment_crop_count": trained,
            "enrollment_pose_counts": pose_counts,
        })
        print(
            f"Face enrollment installed: {trained} crops; "
            f"pose counts={pose_counts}",
            flush=True,
        )
        return trained
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _train_model(crops=None, model_path=None):
    crops = sorted(FACES_DIR.glob("*.jpg")) if crops is None else list(crops)
    model_path = MODEL_PATH if model_path is None else Path(model_path)

    if not crops:
        return 0

    images = [cv2.imread(str(p), cv2.IMREAD_GRAYSCALE) for p in crops]
    images = [img for img in images if img is not None]
    labels = numpy.zeros(len(images), dtype=numpy.int32)

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(images, labels)
    recognizer.write(str(model_path))
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


def _verification_decision(distances):
    """Pure vote rule used by verify() and deterministic tests.

    A real majority and at least three normal matches authorize. Five much
    stronger matches also authorize even if the owner looks away during the
    rest of the burst. The strong path uses a substantially lower distance
    threshold rather than broadly making identity matching more permissive.
    """
    accept_votes = sum(
        distance < LBPH_ACCEPT_THRESHOLD for distance in distances
    )
    strong_votes = sum(
        distance < LBPH_STRONG_THRESHOLD for distance in distances
    )
    reject_votes = len(distances) - accept_votes

    if len(distances) < VERIFY_MIN_USABLE_FRAMES:
        outcome = "no_face"
    elif (
        (
            accept_votes >= VERIFY_MIN_ACCEPT_VOTES
            and accept_votes > reject_votes
        )
        or strong_votes >= VERIFY_MIN_STRONG_VOTES
    ):
        outcome = "authorized"
    else:
        outcome = "unauthorized"

    return outcome, accept_votes, reject_votes


def _record_verification_metrics(outcome, distances):
    metrics = {
        "at": time.time(),
        "outcome": outcome,
        "usable_frames": len(distances),
        "accept_threshold": LBPH_ACCEPT_THRESHOLD,
        "strong_threshold": LBPH_STRONG_THRESHOLD,
        "strong_votes": sum(
            distance < LBPH_STRONG_THRESHOLD for distance in distances
        ),
    }

    if distances:
        metrics.update({
            "best_distance": round(min(distances), 2),
            "median_distance": round(statistics.median(distances), 2),
            "worst_distance": round(max(distances), 2),
        })

    _save_state({"last_verification": metrics})


def verify():
    """Burst-capture + majority-vote check. Returns 'authorized',
    'unauthorized', or 'no_face'. On unauthorized, opens a new intruder
    record; its id is stashed so a following restricted turn can attach
    the commands the stranger then tries."""
    global _last_intruder_id

    if not is_available():
        return "authorized"  # No model/deps — gate can't gate.

    _last_intruder_id = None

    with tempfile.TemporaryDirectory() as directory:
        frames = capture_burst(
            VERIFY_BURST_FRAMES,
            directory,
            sample_fps=VERIFY_SAMPLE_FPS,
        )
        distances = []
        best_accept_frame = None
        best_accept_distance = float("inf")
        best_face_frame = None
        best_face_quality = float("-inf")

        for frame in frames:
            detected = _detect_face_crop_with_quality(frame)

            if detected is None:
                continue

            crop, quality = detected

            if quality > best_face_quality:
                best_face_quality = quality
                best_face_frame = frame

            _, distance = _get_recognizer().predict(crop)
            distance = float(distance)
            distances.append(distance)

            if distance < LBPH_ACCEPT_THRESHOLD:
                if distance < best_accept_distance:
                    best_accept_distance = distance
                    best_accept_frame = frame

        outcome, accept_votes, reject_votes = _verification_decision(distances)
        strong_votes = sum(
            distance < LBPH_STRONG_THRESHOLD for distance in distances
        )
        _record_verification_metrics(outcome, distances)
        distance_summary = "no distances"

        if distances:
            distance_summary = (
                f"best={min(distances):.1f} "
                f"median={statistics.median(distances):.1f} "
                f"worst={max(distances):.1f} "
                f"threshold={LBPH_ACCEPT_THRESHOLD:.1f}"
            )

        print(
            f"Face gate vote: {accept_votes} accept / {reject_votes} reject "
            f"over {len(distances)} usable frames; strong={strong_votes}; "
            f"{distance_summary}; "
            f"outcome={outcome}",
            flush=True,
        )

        if outcome == "no_face":
            return "no_face"

        if outcome == "authorized":
            mark_verified()
            _rotate_verified_capture(best_accept_frame or best_face_frame)
            return "authorized"

        mark_unauthorized()
        _last_intruder_id = _record_intruder(best_face_frame)
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


# ---------------------------------------------------------------------
# Local/SSH recovery command
# ---------------------------------------------------------------------

def _console_enrollment_progress(step):
    if not step.startswith("pose:"):
        return

    pose = step.split(":", 1)[1]
    prompt = ENROLL_POSE_PROMPTS.get(pose, "Hold the next position.")
    print(f"\n{prompt} Hold that position...", flush=True)
    # Give the owner time to follow the terminal prompt before capture.
    time.sleep(1.5)


def _main(argv=None):
    parser = argparse.ArgumentParser(description="A.T.L.A.S. camera gate")
    parser.add_argument(
        "action",
        choices=("enroll", "status"),
        help="enroll the owner from the local shell, or show gate status",
    )
    args = parser.parse_args(argv)

    if args.action == "status":
        print(json.dumps({**hud_status(), **_load_state()}, indent=2))
        return 0

    if cv2 is None:
        print("OpenCV is unavailable; enrollment cannot run.", flush=True)
        return 2

    print(
        "Starting secure owner enrollment. Stay in view and follow each "
        "pose prompt; the previous model remains intact unless this finishes.",
        flush=True,
    )
    count = enroll(progress=_console_enrollment_progress)

    if count == 0:
        print(
            "Enrollment failed: not enough clear angles were captured. "
            "The previous model was kept.",
            flush=True,
        )
        return 1

    mark_verified()
    print(
        f"Enrollment complete: {count} multi-angle owner crops installed.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
