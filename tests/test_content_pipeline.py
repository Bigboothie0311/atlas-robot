import subprocess
import unittest
from unittest import mock

import requests

import content_pipeline


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


class RenderNarrationTests(unittest.TestCase):
    @mock.patch.object(content_pipeline.requests, "post")
    def test_returns_wav_path_on_success(self, post):
        post.return_value = FakeResponse(
            {"ok": True, "wav_path": "/tmp/narration123.wav"}
        )

        wav_path = content_pipeline.render_narration("Hello, I'm A.T.L.A.S.")

        self.assertEqual(wav_path, "/tmp/narration123.wav")
        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["play"], False)

    @mock.patch.object(content_pipeline.requests, "post")
    def test_raises_when_hub_reports_no_wav_path(self, post):
        post.return_value = FakeResponse({"ok": False, "error": "Piper voice is not loaded"})

        with self.assertRaises(content_pipeline.ContentPipelineError):
            content_pipeline.render_narration("Hello")

    @mock.patch.object(content_pipeline.requests, "post")
    def test_wraps_network_errors(self, post):
        post.side_effect = requests.ConnectionError("hub unreachable")

        with self.assertRaises(content_pipeline.ContentPipelineError):
            content_pipeline.render_narration("Hello")


class EditReelTests(unittest.TestCase):
    @mock.patch.object(content_pipeline.Path, "is_file", return_value=True)
    @mock.patch.object(content_pipeline.Path, "stat")
    @mock.patch.object(content_pipeline.subprocess, "run")
    def test_builds_expected_ffmpeg_command(self, run, stat, _is_file):
        stat.return_value = mock.Mock(st_size=1234)

        result = content_pipeline.edit_reel(
            "/tmp/recording.mp4", "/tmp/narration.wav", "/tmp/reel.mp4"
        )

        self.assertEqual(result, "/tmp/reel.mp4")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("/tmp/recording.mp4", command)
        self.assertIn("/tmp/narration.wav", command)
        self.assertIn("-shortest", command)
        self.assertIn("+faststart", command)
        filter_complex = command[command.index("-filter_complex") + 1]
        self.assertIn("1080:1920", filter_complex)
        self.assertIn("loudnorm", filter_complex)
        # loudnorm doesn't reliably preserve the input sample rate on
        # output (confirmed live: produced an unpinned 96kHz track that
        # Instagram's Reels processing silently rejected) -- -ar must be
        # pinned explicitly downstream of it.
        self.assertIn("-ar", command)
        self.assertEqual(command[command.index("-ar") + 1], "48000")

    @mock.patch.object(content_pipeline.subprocess, "run")
    def test_raises_on_ffmpeg_failure(self, run):
        run.side_effect = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Invalid data found"
        )

        with self.assertRaises(content_pipeline.ContentPipelineError):
            content_pipeline.edit_reel(
                "/tmp/recording.mp4", "/tmp/narration.wav", "/tmp/reel.mp4"
            )

    @mock.patch.object(content_pipeline.Path, "is_file", return_value=False)
    @mock.patch.object(content_pipeline.subprocess, "run")
    def test_raises_when_output_missing(self, run, _is_file):
        with self.assertRaises(content_pipeline.ContentPipelineError):
            content_pipeline.edit_reel(
                "/tmp/recording.mp4", "/tmp/narration.wav", "/tmp/reel.mp4"
            )


class ConcatClipsTests(unittest.TestCase):
    @mock.patch.object(content_pipeline.Path, "is_file", return_value=True)
    @mock.patch.object(content_pipeline.Path, "stat")
    @mock.patch.object(content_pipeline.subprocess, "run")
    def test_builds_concat_command_with_stream_copy(self, run, stat, _is_file):
        stat.return_value = mock.Mock(st_size=1234)

        result = content_pipeline.concat_clips(
            ["/tmp/beat0.mp4", "/tmp/beat1.mp4"], "/tmp/final.mp4"
        )

        self.assertEqual(result, "/tmp/final.mp4")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-f", command)
        self.assertIn("concat", command)
        self.assertIn("-c", command)
        self.assertIn("copy", command)

    def test_raises_on_empty_clip_list(self):
        with self.assertRaises(content_pipeline.ContentPipelineError):
            content_pipeline.concat_clips([], "/tmp/final.mp4")

    @mock.patch.object(content_pipeline.subprocess, "run")
    def test_raises_on_ffmpeg_failure(self, run):
        run.side_effect = subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="Invalid data found"
        )

        with self.assertRaises(content_pipeline.ContentPipelineError):
            content_pipeline.concat_clips(["/tmp/beat0.mp4"], "/tmp/final.mp4")


class BuildCaptionTests(unittest.TestCase):
    def test_includes_narration_and_hashtags(self):
        caption = content_pipeline.build_caption("A short demo of A.T.L.A.S.")

        self.assertIn("A short demo of A.T.L.A.S.", caption)
        self.assertIn("#atlas", caption)

    def test_truncates_long_narration(self):
        caption = content_pipeline.build_caption("x" * 3000)

        first_line = caption.splitlines()[0]
        self.assertLessEqual(len(first_line), content_pipeline.CAPTION_MAX_LENGTH)
        self.assertTrue(first_line.endswith("..."))

    def test_empty_narration_still_returns_hashtags(self):
        caption = content_pipeline.build_caption("   ")

        self.assertIn("#atlas", caption)


if __name__ == "__main__":
    unittest.main()
