import os
import unittest
from unittest import mock

import robot_hub


class FakePiperVoice:
    """Writes a minimal valid WAV instead of running real synthesis."""

    def synthesize_wav(self, text, wav_file, syn_config=None):
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 10)


class SpeakTextPlayFlagTests(unittest.TestCase):
    def setUp(self):
        self._original_voice = robot_hub.piper_voice
        robot_hub.piper_voice = FakePiperVoice()

    def tearDown(self):
        robot_hub.piper_voice = self._original_voice

    def test_play_false_synthesizes_without_playing_and_returns_wav_path(self):
        with mock.patch.object(robot_hub.subprocess, "Popen") as popen:
            wav_path = robot_hub._speak_text("narration line", play=False)

        popen.assert_not_called()
        self.assertTrue(os.path.exists(wav_path))
        self.assertFalse(robot_hub.robot_state["speaking"])

        os.remove(wav_path)

    def test_play_true_still_plays_and_cleans_up_as_before(self):
        process = mock.Mock()
        process.returncode = 0
        process.wait = mock.Mock()

        with mock.patch.object(
            robot_hub.subprocess, "Popen", return_value=process
        ) as popen:
            wav_path = robot_hub._speak_text("spoken line", play=True)

        popen.assert_called_once()
        self.assertIsNone(wav_path)
        self.assertFalse(robot_hub.robot_state["speaking"])

    def test_normal_speak_after_a_silent_render_plays_normally(self):
        """A play=False call must not leave any lingering mute state —
        the very next call should play aloud like normal."""
        with mock.patch.object(robot_hub.subprocess, "Popen") as popen:
            silent_wav = robot_hub._speak_text("silent narration", play=False)
        popen.assert_not_called()
        os.remove(silent_wav)

        process = mock.Mock()
        process.returncode = 0
        process.wait = mock.Mock()
        with mock.patch.object(
            robot_hub.subprocess, "Popen", return_value=process
        ) as popen:
            robot_hub._speak_text("normal line", play=True)

        popen.assert_called_once()


class SpeakRoutePlayFlagTests(unittest.TestCase):
    def setUp(self):
        self._original_voice = robot_hub.piper_voice
        robot_hub.piper_voice = FakePiperVoice()

    def tearDown(self):
        robot_hub.piper_voice = self._original_voice

    def test_speak_route_with_play_false_does_not_call_aplay(self):
        client = robot_hub.app.test_client()

        with mock.patch.object(robot_hub.subprocess, "Popen") as popen:
            response = client.post(
                "/speak", json={"text": "narration line", "play": False}
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertIsNotNone(payload["wav_path"])
        popen.assert_not_called()

        os.remove(payload["wav_path"])

    def test_speak_route_defaults_to_playing(self):
        client = robot_hub.app.test_client()
        process = mock.Mock()
        process.returncode = 0
        process.wait = mock.Mock()

        with mock.patch.object(
            robot_hub.subprocess, "Popen", return_value=process
        ) as popen:
            response = client.post("/speak", json={"text": "spoken line"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["wav_path"])
        popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
