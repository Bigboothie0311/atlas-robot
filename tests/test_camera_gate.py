import tempfile
import unittest
from pathlib import Path
from unittest import mock

import camera_gate


class VerificationWindowTests(unittest.TestCase):
    def test_recent_authorized_prompt_extends_old_face_verification(self):
        state = {
            "last_verified_at": 1_000.0,
            "last_authorized_interaction_at": 4_500.0,
            "armed": False,
            "pending_unauthorized": False,
        }

        with mock.patch.object(camera_gate, "_load_state", return_value=state), \
                mock.patch.object(camera_gate.time, "time", return_value=5_000.0):
            self.assertFalse(camera_gate.should_verify())

    def test_idle_time_alone_does_not_require_verification(self):
        """Being home doesn't expire trust — only a real departure (MAC gone
        >20 min, see network_sentinel.PHONE_GRACE_SECONDS) or an unauthorized
        face re-arms the check."""
        state = {
            "last_verified_at": 1_000.0,
            "last_authorized_interaction_at": 1_500.0,
            "armed": False,
            "pending_unauthorized": False,
        }

        with mock.patch.object(camera_gate, "_load_state", return_value=state), \
                mock.patch.object(camera_gate.time, "time", return_value=50_000.0):
            self.assertFalse(camera_gate.should_verify())

    def test_departure_and_unauthorized_state_override_recent_activity(self):
        base = {
            "last_verified_at": 4_900.0,
            "last_authorized_interaction_at": 4_950.0,
        }

        with mock.patch.object(camera_gate.time, "time", return_value=5_000.0):
            with mock.patch.object(
                camera_gate,
                "_load_state",
                return_value={**base, "armed": True},
            ):
                self.assertTrue(camera_gate.should_verify())

            with mock.patch.object(
                camera_gate,
                "_load_state",
                return_value={**base, "pending_unauthorized": True},
            ):
                self.assertTrue(camera_gate.should_verify())

    def test_trusted_prompt_slides_window_without_clearing_security_flags(self):
        state = {
            "last_verified_at": 4_000.0,
            "last_authorized_interaction_at": 4_500.0,
            "armed": False,
            "pending_unauthorized": False,
        }

        with mock.patch.object(camera_gate, "_load_state", return_value=state), \
                mock.patch.object(camera_gate, "_save_state") as save, \
                mock.patch.object(camera_gate.time, "time", return_value=5_000.0):
            self.assertTrue(camera_gate.mark_authorized_interaction())

        save.assert_called_once_with({"last_authorized_interaction_at": 5_000.0})

    def test_pending_unauthorized_cannot_slide_window(self):
        state = {
            "last_verified_at": 4_900.0,
            "last_authorized_interaction_at": 4_950.0,
            "pending_unauthorized": True,
        }

        with mock.patch.object(camera_gate, "_load_state", return_value=state), \
                mock.patch.object(camera_gate, "_save_state") as save:
            self.assertFalse(camera_gate.mark_authorized_interaction())

        save.assert_not_called()

    def test_face_verification_starts_both_trust_timestamps(self):
        with mock.patch.object(camera_gate, "_save_state") as save, \
                mock.patch.object(camera_gate.time, "time", return_value=5_000.0):
            camera_gate.mark_verified()

        save.assert_called_once_with({
            "last_verified_at": 5_000.0,
            "last_authorized_interaction_at": 5_000.0,
            "armed": False,
            "armed_reason": None,
            "pending_unauthorized": False,
        })


class VerificationDecisionTests(unittest.TestCase):
    def test_too_few_usable_frames_is_no_face(self):
        outcome, accepts, rejects = camera_gate._verification_decision(
            [40.0, 45.0, 90.0, 95.0]
        )
        self.assertEqual("no_face", outcome)
        self.assertEqual((2, 2), (accepts, rejects))

    def test_real_majority_authorizes(self):
        outcome, accepts, rejects = camera_gate._verification_decision(
            [42.0, 48.0, 55.0, 62.0, 68.0, 90.0, 95.0]
        )
        self.assertEqual("authorized", outcome)
        self.assertEqual((5, 2), (accepts, rejects))

    def test_tie_never_authorizes(self):
        outcome, accepts, rejects = camera_gate._verification_decision(
            [45.0, 50.0, 55.0, 65.0, 90.0, 95.0, 100.0, 105.0]
        )
        self.assertEqual("unauthorized", outcome)
        self.assertEqual((4, 4), (accepts, rejects))

    def test_reject_majority_is_unauthorized(self):
        outcome, accepts, rejects = camera_gate._verification_decision(
            [45.0, 55.0, 90.0, 95.0, 100.0, 105.0]
        )
        self.assertEqual("unauthorized", outcome)
        self.assertEqual((2, 4), (accepts, rejects))

    def test_five_strong_matches_survive_later_glance_aways(self):
        outcome, accepts, rejects = camera_gate._verification_decision(
            [
                30.0, 31.0, 32.0, 33.0, 34.0, 90.0, 95.0, 100.0,
                105.0, 110.0, 115.0, 120.0, 125.0, 130.0,
            ]
        )
        self.assertEqual("authorized", outcome)
        self.assertEqual((5, 9), (accepts, rejects))

    def test_four_strong_matches_do_not_override_reject_majority(self):
        outcome, accepts, rejects = camera_gate._verification_decision(
            [30.0, 35.0, 40.0, 45.0, 90.0, 95.0, 100.0, 105.0, 110.0]
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


class DismissIntruderAlertsTests(unittest.TestCase):
    def test_dismiss_deletes_photos_marks_reviewed_and_keeps_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "intruder_log.json"
            photo_one = root / "intruder_a.jpg"
            photo_two = root / "intruder_b.jpg"
            photo_one.write_bytes(b"face a")
            photo_two.write_bytes(b"face b")

            with mock.patch.object(camera_gate, "INTRUDER_LOG_PATH", log):
                camera_gate._save_intruder_log([
                    {
                        "id": "a",
                        "photo": str(photo_one),
                        "timestamp": 1.0,
                        "denied_commands": [{"command": "unlock", "at": 1.0}],
                        "reviewed": False,
                    },
                    {
                        "id": "b",
                        "photo": str(photo_two),
                        "timestamp": 2.0,
                        "denied_commands": [],
                        "reviewed": False,
                    },
                ])

                cleared = camera_gate.dismiss_intruder_alerts()
                records = camera_gate._load_intruder_log()

            # Both alerts cleared, photos gone from disk.
            self.assertEqual(2, cleared)
            self.assertFalse(photo_one.exists())
            self.assertFalse(photo_two.exists())

            # The report survives: log rows kept, marked reviewed, photo
            # pointers dropped, denied-command history preserved.
            self.assertEqual(2, len(records))
            self.assertTrue(all(r["reviewed"] for r in records))
            self.assertTrue(all(r["photo"] is None for r in records))
            self.assertEqual(
                "unlock", records[0]["denied_commands"][0]["command"]
            )

    def test_dismiss_is_a_no_op_when_nothing_is_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "intruder_log.json"

            with mock.patch.object(camera_gate, "INTRUDER_LOG_PATH", log):
                camera_gate._save_intruder_log([
                    {
                        "id": "a",
                        "photo": None,
                        "timestamp": 1.0,
                        "denied_commands": [],
                        "reviewed": True,
                    },
                ])

                cleared = camera_gate.dismiss_intruder_alerts()

            self.assertEqual(0, cleared)


if __name__ == "__main__":
    unittest.main()
