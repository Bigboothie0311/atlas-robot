import unittest
from unittest import mock

import self_healing


def _fake_path(exists_return=None, exists_side_effect=None):
    """Path.exists is a read-only slot on PosixPath, so it can't be
    mock.patch.object'd on a real instance — swap the whole module
    attribute for a MagicMock standing in for the Path instead."""
    fake = mock.MagicMock()
    if exists_side_effect is not None:
        fake.exists.side_effect = exists_side_effect
    else:
        fake.exists.return_value = exists_return
    return fake


class HealSpeechEngineTests(unittest.TestCase):
    def setUp(self):
        self_healing.recovery._last_repair.clear()

    def test_already_present_is_reported_resolved_with_no_action(self):
        with mock.patch.object(self_healing, "WHISPER_CLI", _fake_path(True)), \
                mock.patch.object(self_healing, "WHISPER_MODEL", _fake_path(True)):
            incident = self_healing._heal_speech_engine()

        self.assertTrue(incident["resolved"])
        self.assertEqual(incident["action"], "none")

    def test_missing_whisper_dir_is_reported_not_resolved(self):
        with mock.patch.object(self_healing, "WHISPER_CLI", _fake_path(False)), \
                mock.patch.object(self_healing, "WHISPER_MODEL", _fake_path(False)), \
                mock.patch.object(self_healing, "WHISPER_DIR", _fake_path(False)):
            incident = self_healing._heal_speech_engine()

        self.assertFalse(incident["resolved"])
        self.assertIn("isn't vendored", incident["action"] + incident["cause"])

    def test_cooldown_skips_repeated_rebuild_attempts(self):
        self_healing.recovery._mark_repair("speech_engine")

        with mock.patch.object(self_healing, "WHISPER_CLI", _fake_path(False)), \
                mock.patch.object(self_healing, "WHISPER_MODEL", _fake_path(False)), \
                mock.patch.object(self_healing, "WHISPER_DIR", _fake_path(True)), \
                mock.patch.object(self_healing, "_rebuild_whisper_binary") as rebuild_mock:
            incident = self_healing._heal_speech_engine()

        self.assertFalse(incident["resolved"])
        self.assertEqual(incident["action"], "skipped (cooldown)")
        rebuild_mock.assert_not_called()

    def test_missing_binary_triggers_rebuild_and_verifies(self):
        # Binary starts missing; the rebuild call "succeeds" and a second
        # existence check (post-rebuild) reports it landed.
        with mock.patch.object(
                    self_healing, "WHISPER_CLI",
                    _fake_path(exists_side_effect=[False, False, True]),
                ), \
                mock.patch.object(self_healing, "WHISPER_MODEL", _fake_path(True)), \
                mock.patch.object(self_healing, "WHISPER_DIR", _fake_path(True)), \
                mock.patch.object(self_healing, "_rebuild_whisper_binary", return_value=True) as rebuild_mock:
            incident = self_healing._heal_speech_engine()

        rebuild_mock.assert_called_once_with()
        self.assertIn("rebuilt whisper-cli", incident["action"])
        self.assertTrue(incident["resolved"])

    def test_missing_model_triggers_download_and_reports_failure_honestly(self):
        with mock.patch.object(self_healing, "WHISPER_CLI", _fake_path(True)), \
                mock.patch.object(
                    self_healing, "WHISPER_MODEL",
                    _fake_path(exists_side_effect=[False, False, False]),
                ), \
                mock.patch.object(self_healing, "WHISPER_DIR", _fake_path(True)), \
                mock.patch.object(self_healing, "_fetch_whisper_model", return_value=False) as fetch_mock:
            incident = self_healing._heal_speech_engine()

        fetch_mock.assert_called_once_with()
        self.assertIn("model download failed", incident["action"])
        self.assertFalse(incident["resolved"])


class RecentCommandMissesTests(unittest.TestCase):
    def test_ai_question_matching_a_real_capability_is_a_miss(self):
        records = [
            {"intent": "ai_question", "transcript": "heal yourself"},
            {"intent": "diagnostics", "transcript": "run diagnostics"},
            {"intent": "ai_question", "transcript": "what's the meaning of life"},
        ]

        with mock.patch.object(self_healing.logbook, "read_interactions", return_value=records):
            misses = self_healing._recent_command_misses()

        self.assertEqual(len(misses), 1)
        self.assertEqual(misses[0][0], "heal yourself")

    def test_no_interactions_returns_empty(self):
        with mock.patch.object(self_healing.logbook, "read_interactions", return_value=[]):
            self.assertEqual(self_healing._recent_command_misses(), [])

    def test_blank_transcript_is_skipped(self):
        records = [{"intent": "ai_question", "transcript": ""}]

        with mock.patch.object(self_healing.logbook, "read_interactions", return_value=records):
            self.assertEqual(self_healing._recent_command_misses(), [])


class HealNowReportTests(unittest.TestCase):
    def test_report_includes_recent_misses_when_present(self):
        with mock.patch.object(self_healing, "check_and_heal", return_value=[]), \
                mock.patch.object(self_healing.logbook, "record_incident"), \
                mock.patch.object(
                    self_healing, "_recent_command_misses",
                    return_value=[("heal yourself", "self-healing")],
                ):
            report = self_healing.heal_now()

        self.assertIn("everything's already healthy", report)
        self.assertIn("heal yourself", report)
        self.assertIn("self-healing", report)

    def test_report_omits_misses_section_when_none_found(self):
        with mock.patch.object(self_healing, "check_and_heal", return_value=[]), \
                mock.patch.object(self_healing.logbook, "record_incident"), \
                mock.patch.object(self_healing, "_recent_command_misses", return_value=[]):
            report = self_healing.heal_now()

        self.assertNotIn("Also,", report)


class ActionableFilterTests(unittest.TestCase):
    def test_no_op_and_deferred_actions_are_not_announced(self):
        incidents = [
            {"action": "none (start the companion on the PC)"},
            {"action": "none"},
            {"action": "skipped (cooldown)"},
            {"action": "restarted atlas-hud.service"},
        ]
        acted = self_healing._actionable(incidents)
        self.assertEqual([i["action"] for i in acted], ["restarted atlas-hud.service"])


class CompanionOfflineDedupeTests(unittest.TestCase):
    def setUp(self):
        self_healing._companion_offline = False
        # Everything else healthy so only the companion branch can fire.
        self._patches = [
            mock.patch.object(self_healing, "_service_active", return_value=True),
            mock.patch.object(
                self_healing.connection_health, "check_direct_link",
                return_value={"ok": True, "detail": ""},
            ),
            mock.patch.object(
                self_healing, "WHISPER_CLI",
                mock.Mock(**{"exists.return_value": True}),
            ),
            mock.patch.object(
                self_healing, "WHISPER_MODEL",
                mock.Mock(**{"exists.return_value": True}),
            ),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in self._patches:
            patch.stop()
        self_healing._companion_offline = False

    def test_offline_pc_is_reported_once_then_stays_silent(self):
        with mock.patch.object(
            self_healing.connection_health, "pc_is_truly_offline", return_value=True
        ), mock.patch.object(
            self_healing.recovery, "_incident",
            side_effect=lambda *a, **k: {"component": "companion", "action": a[2]},
        ) as incident:
            first = self_healing.check_and_heal()
            second = self_healing.check_and_heal()

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["component"], "companion")
        self.assertEqual(second, [])
        self.assertEqual(incident.call_count, 1)

    def test_offline_reports_again_after_pc_recovers(self):
        with mock.patch.object(
            self_healing.recovery, "_incident",
            side_effect=lambda *a, **k: {"component": "companion", "action": a[2]},
        ) as incident:
            with mock.patch.object(
                self_healing.connection_health, "pc_is_truly_offline",
                return_value=True,
            ):
                self_healing.check_and_heal()
            with mock.patch.object(
                self_healing.connection_health, "pc_is_truly_offline",
                return_value=False,
            ):
                self_healing.check_and_heal()  # PC back -> resets latch
            with mock.patch.object(
                self_healing.connection_health, "pc_is_truly_offline",
                return_value=True,
            ):
                self_healing.check_and_heal()  # offline again -> reports again

        self.assertEqual(incident.call_count, 2)


if __name__ == "__main__":
    unittest.main()
