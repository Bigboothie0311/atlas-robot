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
    @mock.patch.object(hud_capture, "capture_frame")
    @mock.patch.object(hud_capture.Path, "is_file", return_value=True)
    @mock.patch.object(hud_capture.Path, "stat")
    @mock.patch.object(hud_capture.subprocess, "run")
    def test_stitches_captured_frames_into_a_clip(
        self, run, stat, _is_file, capture_frame, _sleep
    ):
        capture_frame.return_value = True
        stat.return_value = mock.Mock(st_size=999)

        result = hud_capture.record_hud_clip(2.0, "/tmp/hud_clip.mp4", fps=2)

        self.assertEqual(result, "/tmp/hud_clip.mp4")
        self.assertEqual(capture_frame.call_count, 4)  # 2s * 2fps
        command = run.call_args.args[0]
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-framerate", command)

    @mock.patch.object(hud_capture.time, "sleep")
    @mock.patch.object(hud_capture, "capture_frame", return_value=False)
    def test_raises_when_no_frames_captured(self, _capture_frame, _sleep):
        with self.assertRaises(hud_capture.HudCaptureError):
            hud_capture.record_hud_clip(1.0, "/tmp/hud_clip.mp4", fps=2)

    @mock.patch.object(hud_capture.time, "sleep")
    @mock.patch.object(hud_capture, "capture_frame", return_value=True)
    @mock.patch.object(hud_capture.subprocess, "run")
    def test_raises_on_ffmpeg_stitch_failure(self, run, _capture_frame, _sleep):
        run.side_effect = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="no frames"
        )

        with self.assertRaises(hud_capture.HudCaptureError):
            hud_capture.record_hud_clip(1.0, "/tmp/hud_clip.mp4", fps=2)


if __name__ == "__main__":
    unittest.main()
