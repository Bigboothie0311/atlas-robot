import subprocess
import unittest
from pathlib import Path
from unittest import mock

import hud_capture


class CaptureFrameTests(unittest.TestCase):
    @mock.patch.object(hud_capture.subprocess, "run")
    def test_returns_true_when_grim_writes_a_real_file(self, run, tmp_path=None):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "frame.png"

            def fake_run(command, **kwargs):
                out_path.write_bytes(b"fake png bytes")
                return mock.Mock(returncode=0)

            run.side_effect = fake_run

            result = hud_capture.capture_frame(out_path)

            self.assertTrue(result)
            _, kwargs = run.call_args
            self.assertEqual(
                kwargs["env"]["WAYLAND_DISPLAY"], hud_capture.WAYLAND_DISPLAY
            )

    @mock.patch.object(hud_capture.subprocess, "run")
    def test_returns_false_without_raising_on_subprocess_error(self, run):
        run.side_effect = subprocess.CalledProcessError(1, ["grim"])

        result = hud_capture.capture_frame("/tmp/nonexistent/frame.png")

        self.assertFalse(result)

    @mock.patch.object(hud_capture.subprocess, "run")
    def test_returns_false_when_file_never_appears(self, run):
        run.return_value = mock.Mock(returncode=0)

        result = hud_capture.capture_frame("/tmp/never_written_frame.png")

        self.assertFalse(result)


class RecordHudClipTests(unittest.TestCase):
    @mock.patch.object(hud_capture.time, "sleep")
    @mock.patch.object(hud_capture.Path, "is_file", return_value=True)
    @mock.patch.object(hud_capture.Path, "stat")
    @mock.patch.object(hud_capture.subprocess, "Popen")
    def test_records_via_wf_recorder_and_signals_stop(
        self, popen, stat, _is_file, _sleep
    ):
        stat.return_value = mock.Mock(st_size=999)
        process = mock.Mock()
        process.communicate.return_value = (b"", b"")
        popen.return_value = process

        result = hud_capture.record_hud_clip(2.0, "/tmp/hud_clip.mp4", fps=24)

        self.assertEqual(result, "/tmp/hud_clip.mp4")
        command = popen.call_args.args[0]
        self.assertEqual(command[0], "wf-recorder")
        self.assertIn("-r", command)
        self.assertIn("24", command)
        self.assertIn("/tmp/hud_clip.mp4", command)
        process.send_signal.assert_called_once_with(hud_capture.signal.SIGINT)
        process.communicate.assert_called_once()

    @mock.patch.object(hud_capture.subprocess, "Popen")
    def test_raises_when_recorder_fails_to_start(self, popen):
        popen.side_effect = OSError("no such file")

        with self.assertRaises(hud_capture.HudCaptureError):
            hud_capture.record_hud_clip(1.0, "/tmp/hud_clip.mp4", fps=24)

    @mock.patch.object(hud_capture.time, "sleep")
    @mock.patch.object(hud_capture.subprocess, "Popen")
    def test_raises_and_kills_process_when_exit_times_out(self, popen, _sleep):
        process = mock.Mock()
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd=["wf-recorder"], timeout=15),
            (b"", b""),
        ]
        popen.return_value = process

        with self.assertRaises(hud_capture.HudCaptureError):
            hud_capture.record_hud_clip(1.0, "/tmp/hud_clip.mp4", fps=24)

        process.kill.assert_called_once()

    @mock.patch.object(hud_capture.time, "sleep")
    @mock.patch.object(hud_capture.Path, "is_file", return_value=False)
    @mock.patch.object(hud_capture.subprocess, "Popen")
    def test_raises_when_output_missing(self, popen, _is_file, _sleep):
        process = mock.Mock()
        process.communicate.return_value = (b"", b"no such compositor")
        popen.return_value = process

        with self.assertRaises(hud_capture.HudCaptureError):
            hud_capture.record_hud_clip(1.0, "/tmp/hud_clip.mp4", fps=24)


if __name__ == "__main__":
    unittest.main()
