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


if __name__ == "__main__":
    unittest.main()
