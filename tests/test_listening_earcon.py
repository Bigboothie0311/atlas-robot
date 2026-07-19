import array
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import robot_hub


class ListeningEarconTests(unittest.TestCase):
    def test_generated_earcon_is_short_clean_and_audible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "listening.wav"

            with mock.patch.object(
                robot_hub,
                "LISTENING_EARCON_PATH",
                str(path),
            ):
                robot_hub._ensure_listening_earcon_exists()

            with wave.open(str(path), "rb") as wav_file:
                self.assertEqual(wav_file.getnchannels(), 1)
                self.assertEqual(wav_file.getsampwidth(), 2)
                self.assertEqual(wav_file.getframerate(), 44100)
                duration = wav_file.getnframes() / wav_file.getframerate()
                samples = array.array("h", wav_file.readframes(-1))

            self.assertGreater(duration, 0.3)
            self.assertLess(duration, 0.35)
            peak = max(abs(sample) for sample in samples)
            self.assertGreater(peak, 2_500)
            self.assertLess(peak, 10_000)
            self.assertLess(abs(samples[-1]), 100)

    @mock.patch.object(robot_hub, "_play_listening_earcon")
    def test_route_plays_earcon_synchronously(self, play):
        response = robot_hub.app.test_client().post("/listening_earcon")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        play.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
