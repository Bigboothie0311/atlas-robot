import tempfile
import unittest
from pathlib import Path
from unittest import mock

import camera_gate


class VerificationDecisionTests(unittest.TestCase):
    def test_too_few_usable_frames_is_no_face(self):
        outcome, accepts, rejects = camera_gate._verification_decision(
            [40.0, 45.0, 80.0, 85.0]
        )
        self.assertEqual("no_face", outcome)
        self.assertEqual((2, 2), (accepts, rejects))

    def test_real_majority_authorizes(self):
        outcome, accepts, rejects = camera_gate._verification_decision(
            [42.0, 48.0, 55.0, 62.0, 68.0, 74.0, 79.0]
        )
        self.assertEqual("authorized", outcome)
        self.assertEqual((5, 2), (accepts, rejects))

    def test_tie_never_authorizes(self):
        outcome, accepts, rejects = camera_gate._verification_decision(
            [45.0, 50.0, 55.0, 65.0, 75.0, 80.0, 85.0, 90.0]
        )
        self.assertEqual("unauthorized", outcome)
        self.assertEqual((4, 4), (accepts, rejects))

    def test_reject_majority_is_unauthorized(self):
        outcome, accepts, rejects = camera_gate._verification_decision(
            [45.0, 55.0, 72.0, 75.0, 80.0, 85.0]
        )
        self.assertEqual("unauthorized", outcome)
        self.assertEqual((2, 4), (accepts, rejects))

    def test_five_strong_matches_survive_later_glance_aways(self):
        outcome, accepts, rejects = camera_gate._verification_decision(
            [
                34.8, 33.1, 32.5, 33.7, 34.2, 64.7, 69.2, 82.1,
                88.6, 96.5, 97.6, 102.8, 101.8, 103.3, 102.5, 105.0,
            ]
        )
        self.assertEqual("authorized", outcome)
        self.assertEqual((7, 9), (accepts, rejects))

    def test_four_strong_matches_do_not_override_reject_majority(self):
        outcome, accepts, rejects = camera_gate._verification_decision(
            [35.0, 40.0, 45.0, 50.0, 75.0, 80.0, 85.0, 90.0, 95.0]
        )
        self.assertEqual("unauthorized", outcome)
        self.assertEqual((4, 5), (accepts, rejects))


class EnrollmentInstallTests(unittest.TestCase):
    def test_enrollment_uses_seven_distinct_poses(self):
        self.assertEqual(7, len(camera_gate.ENROLL_POSES))
        self.assertEqual(
            set(camera_gate.ENROLL_POSES),
            set(camera_gate.ENROLL_POSE_PROMPTS),
        )
        self.assertEqual(
            35,
            len(camera_gate.ENROLL_POSES)
            * camera_gate.ENROLL_CROPS_PER_POSE,
        )

    def test_completed_staging_replaces_old_enrollment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            faces = root / "faces" / "authorized"
            model = root / "face_model.yml"
            staged_faces = root / "staged" / "authorized"
            staged_model = root / "staged" / "face_model.yml"

            faces.mkdir(parents=True)
            staged_faces.mkdir(parents=True)
            faces.joinpath("old.jpg").write_bytes(b"old face")
            model.write_bytes(b"old model")
            staged_faces.joinpath("new.jpg").write_bytes(b"new face")
            staged_model.write_bytes(b"new model")

            with mock.patch.object(camera_gate, "FACES_DIR", faces), \
                    mock.patch.object(camera_gate, "MODEL_PATH", model):
                camera_gate._install_enrollment(staged_faces, staged_model)

            self.assertFalse(faces.joinpath("old.jpg").exists())
            self.assertEqual(b"new face", faces.joinpath("new.jpg").read_bytes())
            self.assertEqual(b"new model", model.read_bytes())

    def test_failed_model_install_restores_old_faces_and_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            faces = root / "faces" / "authorized"
            model = root / "face_model.yml"
            staged_faces = root / "staged" / "authorized"
            # A directory cannot replace the existing model file, forcing the
            # failure after the staged faces have already been moved.
            staged_model = root / "staged" / "invalid-model-directory"

            faces.mkdir(parents=True)
            staged_faces.mkdir(parents=True)
            staged_model.mkdir()
            faces.joinpath("old.jpg").write_bytes(b"old face")
            model.write_bytes(b"old model")
            staged_faces.joinpath("new.jpg").write_bytes(b"new face")

            with mock.patch.object(camera_gate, "FACES_DIR", faces), \
                    mock.patch.object(camera_gate, "MODEL_PATH", model):
                with self.assertRaises(OSError):
                    camera_gate._install_enrollment(staged_faces, staged_model)

            self.assertEqual(b"old face", faces.joinpath("old.jpg").read_bytes())
            self.assertFalse(faces.joinpath("new.jpg").exists())
            self.assertEqual(b"old model", model.read_bytes())


class CaptureCommandTests(unittest.TestCase):
    def test_sample_rate_is_applied_to_ffmpeg_burst(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(camera_gate.subprocess, "run") as run:
            camera_gate.capture_burst(18, directory, sample_fps=8)

        command = run.call_args.args[0]
        self.assertIn("-vf", command)
        self.assertEqual("fps=8", command[command.index("-vf") + 1])
        self.assertEqual("18", command[command.index("-frames:v") + 1])


if __name__ == "__main__":
    unittest.main()
