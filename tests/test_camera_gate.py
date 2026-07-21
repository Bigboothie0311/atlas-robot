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

    def test_owner_left_command_arm_always_verifies(self):
        """An explicit 'I'm leaving' arm re-verifies the next interaction
        regardless of phone presence."""
        state = {"armed": True, "armed_reason": "owner_left"}
        with mock.patch.object(camera_gate, "_load_state", return_value=state):
            self.assertTrue(camera_gate.should_verify())

    def test_phone_left_arm_verifies_only_while_phone_still_away(self):
        """A 'phone_left' arm only demands a face check while the phone is
        STILL gone from the LAN. Once it's back, walking past the camera
        does not trigger verification."""
        import network_sentinel

        state = {"armed": True, "armed_reason": "phone_left"}

        with mock.patch.object(camera_gate, "_load_state", return_value=state):
            with mock.patch.object(
                network_sentinel, "phone_currently_away", return_value=True
            ):
                self.assertTrue(camera_gate.should_verify())

            with mock.patch.object(
                network_sentinel, "phone_currently_away", return_value=False
            ):
                self.assertFalse(camera_gate.should_verify())

    def test_phone_left_arm_still_verifies_if_presence_unknown(self):
        """If presence can't be determined, fail safe to verifying."""
        import network_sentinel

        state = {"armed": True, "armed_reason": "phone_left"}
        with mock.patch.object(camera_gate, "_load_state", return_value=state), \
                mock.patch.object(
                    network_sentinel, "phone_currently_away",
                    side_effect=RuntimeError("boom"),
                ):
            self.assertTrue(camera_gate.should_verify())

    def test_disarm_if_reason_clears_matching_phone_left_arm(self):
        state = {"armed": True, "armed_reason": "phone_left"}
        with mock.patch.object(camera_gate, "_load_state", return_value=state), \
                mock.patch.object(camera_gate, "_save_state") as save:
            camera_gate.disarm_if_reason("phone_left")
        save.assert_called_once_with({"armed": False, "armed_reason": None})

    def test_disarm_if_reason_leaves_owner_left_command_arm_intact(self):
        state = {"armed": True, "armed_reason": "owner_left"}
        with mock.patch.object(camera_gate, "_load_state", return_value=state), \
                mock.patch.object(camera_gate, "_save_state") as save:
            camera_gate.disarm_if_reason("phone_left")
        save.assert_not_called()

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


class CaptureClipTests(unittest.TestCase):
    def setUp(self):
        # Real mic_arbiter.request_yield() waits up to 3s for a barge-in
        # listener that isn't running in tests -- stub it out so every
        # test stays fast and deterministic, and track calls so the
        # coordination tests below can assert on them.
        self.mic_arbiter_calls = []
        patcher = mock.patch.object(
            camera_gate.mic_arbiter, "request_yield",
            side_effect=lambda *a, **k: self.mic_arbiter_calls.append("request_yield") or True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher = mock.patch.object(
            camera_gate.mic_arbiter, "resume",
            side_effect=lambda: self.mic_arbiter_calls.append("resume"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _fake_run_writing_output(command, **kwargs):
        Path(command[-1]).write_bytes(b"fake mp4 bytes")

    def test_capture_clip_builds_muxed_command_and_returns_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            clips_dir = Path(directory) / "clips"
            with mock.patch.object(camera_gate, "CLIPS_DIR", clips_dir), \
                    mock.patch.object(camera_gate.subprocess, "run",
                                       side_effect=self._fake_run_writing_output) as run:
                result = camera_gate.capture_clip(10, mission="showcase")

            command = run.call_args.args[0]
            self.assertIn("alsa", command)
            self.assertIn(camera_gate.AUDIO_DEVICE, command)
            self.assertEqual("10", command[command.index("-t") + 1])
            self.assertEqual("yuv420p", command[command.index("-pix_fmt") + 1])
            self.assertTrue(result["has_audio"])
            self.assertEqual(result["mission"], "showcase")
            self.assertEqual(result["duration_seconds"], 10)
            self.assertTrue(Path(result["path"]).is_file())
            self.assertEqual(self.mic_arbiter_calls, ["request_yield", "resume"])

    def test_capture_clip_mute_audio_skips_alsa_input(self):
        with tempfile.TemporaryDirectory() as directory:
            clips_dir = Path(directory) / "clips"
            with mock.patch.object(camera_gate, "CLIPS_DIR", clips_dir), \
                    mock.patch.object(camera_gate.subprocess, "run",
                                       side_effect=self._fake_run_writing_output) as run:
                result = camera_gate.capture_clip(5, mute_audio=True)

            command = run.call_args.args[0]
            self.assertNotIn("alsa", command)
            self.assertEqual("yuv420p", command[command.index("-pix_fmt") + 1])
            self.assertFalse(result["has_audio"])
            self.assertEqual(self.mic_arbiter_calls, [])

    def test_capture_clip_caps_duration_to_max(self):
        with tempfile.TemporaryDirectory() as directory:
            clips_dir = Path(directory) / "clips"
            with mock.patch.object(camera_gate, "CLIPS_DIR", clips_dir), \
                    mock.patch.object(camera_gate.subprocess, "run",
                                       side_effect=self._fake_run_writing_output):
                result = camera_gate.capture_clip(99999)

            self.assertEqual(result["duration_seconds"], camera_gate.MAX_CLIP_SECONDS)

    def test_capture_clip_returns_none_on_ffmpeg_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            clips_dir = Path(directory) / "clips"
            with mock.patch.object(camera_gate, "CLIPS_DIR", clips_dir), \
                    mock.patch.object(
                        camera_gate.subprocess, "run",
                        side_effect=camera_gate.subprocess.SubprocessError("boom"),
                    ):
                result = camera_gate.capture_clip(5)

            self.assertIsNone(result)

    def test_capture_clip_returns_none_when_file_never_appears(self):
        with tempfile.TemporaryDirectory() as directory:
            clips_dir = Path(directory) / "clips"
            with mock.patch.object(camera_gate, "CLIPS_DIR", clips_dir), \
                    mock.patch.object(camera_gate.subprocess, "run"):
                result = camera_gate.capture_clip(5)

            self.assertIsNone(result)

    def test_capture_clip_retries_once_then_succeeds_on_busy_mic(self):
        busy_error = camera_gate.subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="cannot open audio device (Device or resource busy)"
        )
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                raise busy_error
            self._fake_run_writing_output(command, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            clips_dir = Path(directory) / "clips"
            with mock.patch.object(camera_gate, "CLIPS_DIR", clips_dir), \
                    mock.patch.object(camera_gate.subprocess, "run", side_effect=fake_run), \
                    mock.patch.object(camera_gate.time, "sleep") as sleep:
                result = camera_gate.capture_clip(10)

            self.assertEqual(len(calls), 2)
            sleep.assert_called_once_with(camera_gate.MIC_BUSY_RETRY_DELAY_SECONDS)
            self.assertTrue(result["has_audio"])
            self.assertNotIn("audio_fallback", result)

    def test_capture_clip_falls_back_to_muted_after_repeated_busy_mic(self):
        busy_error = camera_gate.subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="cannot open audio device (Device or resource busy)"
        )
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if len(calls) <= 2:
                raise busy_error
            self._fake_run_writing_output(command, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            clips_dir = Path(directory) / "clips"
            with mock.patch.object(camera_gate, "CLIPS_DIR", clips_dir), \
                    mock.patch.object(camera_gate.subprocess, "run", side_effect=fake_run), \
                    mock.patch.object(camera_gate.time, "sleep") as sleep:
                result = camera_gate.capture_clip(10)

            self.assertEqual(len(calls), 3)
            self.assertNotIn("alsa", calls[2])
            sleep.assert_called_once()
            self.assertFalse(result["has_audio"])
            self.assertTrue(result["audio_fallback"])

    def test_capture_clip_gives_up_after_muted_fallback_also_fails(self):
        busy_error = camera_gate.subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="cannot open audio device (Device or resource busy)"
        )
        other_error = camera_gate.subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="no such device"
        )
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            raise busy_error if len(calls) <= 2 else other_error

        with tempfile.TemporaryDirectory() as directory:
            clips_dir = Path(directory) / "clips"
            with mock.patch.object(camera_gate, "CLIPS_DIR", clips_dir), \
                    mock.patch.object(camera_gate.subprocess, "run", side_effect=fake_run), \
                    mock.patch.object(camera_gate.time, "sleep"):
                result = camera_gate.capture_clip(10)

            self.assertEqual(len(calls), 3)
            self.assertIsNone(result)

    def test_capture_clip_does_not_retry_non_busy_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            clips_dir = Path(directory) / "clips"
            with mock.patch.object(camera_gate, "CLIPS_DIR", clips_dir), \
                    mock.patch.object(
                        camera_gate.subprocess, "run",
                        side_effect=camera_gate.subprocess.CalledProcessError(
                            1, ["ffmpeg"], stderr="no such device"
                        ),
                    ) as run:
                result = camera_gate.capture_clip(5)

            self.assertEqual(run.call_count, 1)
            self.assertIsNone(result)


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
