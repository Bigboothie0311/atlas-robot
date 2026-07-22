import json
import subprocess
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import requests

import instagram_publish


CONFIGURED = {
    "INSTAGRAM_ACCESS_TOKEN": "secret-token",
    "INSTAGRAM_ACCOUNT_ID": "123",
}


class FakeResponse:
    def __init__(
        self,
        payload,
        status_code=200,
        text="",
        headers=None,
    ):
        self._payload = payload
        self.status_code = status_code
        self.text = text or json.dumps(payload)
        self.headers = headers or {}

    def json(self):
        return self._payload


def load_config_patch():
    return mock.patch.object(
        instagram_publish, "_load_config", return_value=CONFIGURED
    )


class ConfigTests(unittest.TestCase):
    @mock.patch.object(instagram_publish, "_load_config", return_value={})
    def test_missing_config_raises_clear_error(self, _load_config):
        with self.assertRaises(instagram_publish.InstagramPublishError):
            instagram_publish.create_container_with_video_url(
                "https://example.ts.net/x", "caption"
            )


class CreateContainerWithVideoUrlTests(unittest.TestCase):
    @load_config_patch()
    @mock.patch.object(instagram_publish.requests, "post")
    def test_returns_container_id(self, post, _cfg):
        post.return_value = FakeResponse({"id": "container-1"})

        container_id = instagram_publish.create_container_with_video_url(
            "https://atlaspi.example.ts.net/token/reel.mp4", "caption text"
        )

        self.assertEqual(container_id, "container-1")
        _, kwargs = post.call_args
        self.assertEqual(kwargs["data"]["media_type"], "REELS")
        self.assertEqual(
            kwargs["data"]["video_url"],
            "https://atlaspi.example.ts.net/token/reel.mp4",
        )

    @load_config_patch()
    @mock.patch.object(instagram_publish.requests, "post")
    def test_missing_permission_surfaces_as_clear_diagnostic(self, post, _cfg):
        """This is the one thing we can't verify without a live call --
        the code must fail with a readable error, not crash or silently
        report success."""
        post.return_value = FakeResponse(
            {
                "error": {
                    "message": (
                        "(#200) Permissions error: requires "
                        "instagram_content_publish"
                    )
                }
            },
            status_code=400,
        )

        with self.assertRaises(instagram_publish.InstagramPublishError) as ctx:
            instagram_publish.create_container_with_video_url(
                "https://example.ts.net/x", "caption text"
            )

        self.assertIn("instagram_content_publish", str(ctx.exception))

    @load_config_patch()
    @mock.patch.object(instagram_publish.requests, "post")
    def test_network_error_wrapped(self, post, _cfg):
        post.side_effect = requests.ConnectionError("no route")

        with self.assertRaises(instagram_publish.InstagramPublishError):
            instagram_publish.create_container_with_video_url(
                "https://example.ts.net/x", "caption text"
            )

    @load_config_patch()
    @mock.patch.object(instagram_publish.requests, "post")
    def test_missing_id_in_response_raises(self, post, _cfg):
        post.return_value = FakeResponse({})

        with self.assertRaises(instagram_publish.InstagramPublishError):
            instagram_publish.create_container_with_video_url(
                "https://example.ts.net/x", "caption text"
            )


class PollContainerStatusTests(unittest.TestCase):
    @load_config_patch()
    @mock.patch.object(instagram_publish.requests, "get")
    def test_returns_finished_immediately(self, get, _cfg):
        get.return_value = FakeResponse({"status_code": "FINISHED"})

        status = instagram_publish.poll_container_status("container-1")

        self.assertEqual(status, "FINISHED")

    @load_config_patch()
    @mock.patch.object(instagram_publish.requests, "get")
    def test_raises_on_error_status(self, get, _cfg):
        get.return_value = FakeResponse({"status_code": "ERROR"})

        with self.assertRaises(instagram_publish.InstagramPublishError):
            instagram_publish.poll_container_status("container-1")

    @load_config_patch()
    @mock.patch.object(instagram_publish.time, "sleep")
    @mock.patch.object(instagram_publish.requests, "get")
    def test_gives_up_after_max_attempts(self, get, sleep, _cfg):
        get.return_value = FakeResponse({"status_code": "IN_PROGRESS"})

        with self.assertRaises(instagram_publish.InstagramPublishError):
            instagram_publish.poll_container_status("container-1")

        self.assertEqual(
            get.call_count, instagram_publish.STATUS_POLL_MAX_ATTEMPTS
        )


class PublishAndVerifyTests(unittest.TestCase):
    @load_config_patch()
    @mock.patch.object(instagram_publish.requests, "post")
    def test_publish_returns_media_id(self, post, _cfg):
        post.return_value = FakeResponse({"id": "media-1"})

        media_id = instagram_publish.publish("container-1")

        self.assertEqual(media_id, "media-1")

    @load_config_patch()
    @mock.patch.object(instagram_publish.requests, "get")
    def test_verify_returns_permalink_and_timestamp(self, get, _cfg):
        get.return_value = FakeResponse(
            {"permalink": "https://instagram.com/p/abc", "timestamp": "2026-07-20"}
        )

        details = instagram_publish.verify("media-1")

        self.assertEqual(details["permalink"], "https://instagram.com/p/abc")


class ServeFileLocallyTests(unittest.TestCase):
    def test_serves_the_file_and_rejects_other_paths(self, tmp_path=None):
        import tempfile
        import urllib.error
        import urllib.request

        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / "reel.mp4"
            video_path.write_bytes(b"fake video bytes")

            with instagram_publish._serve_file_locally(video_path) as (
                port,
                url_path,
            ):
                response = urllib.request.urlopen(
                    f"http://127.0.0.1:{port}{url_path}", timeout=5
                )
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"fake video bytes")

                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/other/path", timeout=5
                    )
                self.assertEqual(ctx.exception.code, 404)

    def test_supports_head_requests(self):
        import tempfile
        import urllib.request

        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / "reel.mp4"
            video_path.write_bytes(b"fake video bytes")

            with instagram_publish._serve_file_locally(video_path) as (
                port,
                url_path,
            ):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}{url_path}", method="HEAD"
                )
                response = urllib.request.urlopen(request, timeout=5)
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.headers["Content-Length"], "16"
                )
                self.assertEqual(response.read(), b"")

    def test_supports_range_requests(self):
        import tempfile
        import urllib.request

        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / "reel.mp4"
            video_path.write_bytes(b"fake video bytes")

            with instagram_publish._serve_file_locally(video_path) as (
                port,
                url_path,
            ):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}{url_path}",
                    headers={"Range": "bytes=5-9"},
                )
                response = urllib.request.urlopen(request, timeout=5)
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), b"video")
                self.assertEqual(
                    response.headers["Content-Range"], "bytes 5-9/16"
                )


class FunnelEnabledTests(unittest.TestCase):
    @mock.patch.object(instagram_publish.subprocess, "run")
    def test_starts_and_always_tears_down(self, run):
        run.return_value = mock.Mock(returncode=0)

        with instagram_publish._funnel_enabled(12345):
            pass

        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["tailscale", "funnel", "--bg", "12345"], commands)
        self.assertIn(["tailscale", "funnel", "reset"], commands)

    @mock.patch.object(instagram_publish.subprocess, "run")
    def test_tears_down_even_when_body_raises(self, run):
        def fake_run(command, **kwargs):
            if command[:2] == ["tailscale", "funnel"] and "--bg" in command:
                return mock.Mock(returncode=0)
            return mock.Mock(returncode=0)

        run.side_effect = fake_run

        with self.assertRaises(ValueError):
            with instagram_publish._funnel_enabled(12345):
                raise ValueError("boom")

        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["tailscale", "funnel", "reset"], commands)

    @mock.patch.object(instagram_publish.subprocess, "run")
    def test_raises_clear_error_when_funnel_not_enabled_for_tailnet(self, run):
        run.side_effect = subprocess.CalledProcessError(
            1, ["tailscale", "funnel", "--bg", "12345"],
            stderr="Funnel is not enabled on your tailnet.",
        )

        with self.assertRaises(instagram_publish.InstagramPublishError) as ctx:
            with instagram_publish._funnel_enabled(12345):
                pass

        self.assertIn("Funnel is not enabled", str(ctx.exception))


class FunnelReadinessTests(unittest.TestCase):
    @mock.patch.object(instagram_publish.requests, "head")
    def test_accepts_public_url_with_expected_file_size(self, head):
        head.return_value = FakeResponse(
            {},
            headers={"Content-Length": "16"},
        )

        instagram_publish._wait_for_public_video(
            "https://atlaspi.example.ts.net/token/reel.mp4",
            expected_size=16,
        )

        head.assert_called_once()

    @mock.patch.object(instagram_publish.time, "sleep")
    @mock.patch.object(instagram_publish.requests, "head")
    def test_retries_until_public_url_is_ready(self, head, sleep):
        head.side_effect = [
            requests.ConnectionError("edge not ready"),
            FakeResponse({}, headers={"Content-Length": "16"}),
        ]

        instagram_publish._wait_for_public_video(
            "https://atlaspi.example.ts.net/token/reel.mp4",
            expected_size=16,
        )

        self.assertEqual(head.call_count, 2)
        sleep.assert_called_once_with(
            instagram_publish.FUNNEL_READINESS_RETRY_SECONDS
        )

    @mock.patch.object(instagram_publish, "FUNNEL_READINESS_ATTEMPTS", 2)
    @mock.patch.object(instagram_publish.time, "sleep")
    @mock.patch.object(instagram_publish.requests, "head")
    def test_raises_when_public_url_never_becomes_ready(
        self,
        head,
        _sleep,
    ):
        head.return_value = FakeResponse(
            {},
            status_code=503,
            headers={"Content-Length": "0"},
        )

        with self.assertRaises(
            instagram_publish.InstagramPublishError
        ) as context:
            instagram_publish._wait_for_public_video(
                "https://atlaspi.example.ts.net/token/reel.mp4",
                expected_size=16,
            )

        self.assertIn("did not become ready", str(context.exception))


class OwnTailnetDnsNameTests(unittest.TestCase):
    @mock.patch.object(instagram_publish.subprocess, "run")
    def test_parses_dns_name_from_status_json(self, run):
        run.return_value = mock.Mock(
            stdout=json.dumps({"Self": {"DNSName": "atlaspi.tail74a0c8.ts.net."}})
        )

        dns_name = instagram_publish._own_tailnet_dns_name()

        self.assertEqual(dns_name, "atlaspi.tail74a0c8.ts.net")

    @mock.patch.object(instagram_publish.subprocess, "run")
    def test_raises_clear_error_on_bad_output(self, run):
        run.return_value = mock.Mock(stdout="not json")

        with self.assertRaises(instagram_publish.InstagramPublishError):
            instagram_publish._own_tailnet_dns_name()


class PublishReelOrchestrationTests(unittest.TestCase):
    def setUp(self):
        patcher = load_config_patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    @contextmanager
    def _fake_video_url(self, path):
        yield "https://atlaspi.example.ts.net/token/reel.mp4"

    @mock.patch.object(
        instagram_publish, "create_container_with_video_url",
        return_value="container-1",
    )
    @mock.patch.object(
        instagram_publish, "poll_container_status", return_value="FINISHED"
    )
    @mock.patch.object(instagram_publish, "publish")
    @mock.patch.object(instagram_publish, "verify")
    def test_dry_run_stops_before_publish(
        self, verify, publish, _poll, create_container, tmp_path=None
    ):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp4") as video_file:
            video_file.write(b"video bytes")
            video_file.flush()

            with mock.patch.object(
                instagram_publish, "_funnel_video_url", self._fake_video_url
            ):
                result = instagram_publish.publish_reel(
                    video_file.name, "caption", dry_run=True
                )

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["container_id"], "container-1")
        create_container.assert_called_once_with(
            "https://atlaspi.example.ts.net/token/reel.mp4", "caption"
        )
        publish.assert_not_called()
        verify.assert_not_called()

    def test_raises_when_video_file_missing(self):
        with self.assertRaises(instagram_publish.InstagramPublishError):
            instagram_publish.publish_reel(
                "/nonexistent/reel.mp4", "caption", dry_run=True
            )

    @mock.patch.object(
        instagram_publish, "create_container_with_video_url",
        return_value="container-1",
    )
    @mock.patch.object(
        instagram_publish, "poll_container_status", return_value="FINISHED"
    )
    @mock.patch.object(instagram_publish, "publish", return_value="media-1")
    @mock.patch.object(
        instagram_publish,
        "verify",
        return_value={
            "permalink": "https://instagram.com/reel/media-1",
            "timestamp": "2026-07-20T00:00:00+0000",
        },
    )
    def test_real_publish_records_ledger_entry(
        self, _verify, publish, _poll, _create_container
    ):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp4") as video_file:
            video_file.write(b"video bytes")
            video_file.flush()

            with mock.patch.object(
                instagram_publish, "_funnel_video_url", self._fake_video_url
            ), mock.patch.object(
                instagram_publish, "LEDGER_PATH", self._tmp_ledger_path()
            ):
                result = instagram_publish.publish_reel(
                    video_file.name, "caption", dry_run=False, mission="test-post"
                )

                self.assertFalse(result["dry_run"])
                self.assertEqual(result["media_id"], "media-1")
                publish.assert_called_once_with("container-1")

                ledger = json.loads(instagram_publish.LEDGER_PATH.read_text())
                self.assertEqual(len(ledger), 1)
                self.assertEqual(ledger[0]["media_id"], "media-1")
                self.assertEqual(ledger[0]["mission"], "test-post")
                self.assertEqual(
                    ledger[0]["video_path"],
                    str(Path(video_file.name).resolve()),
                )

    @mock.patch.object(instagram_publish.time, "sleep")
    @mock.patch.object(
        instagram_publish,
        "create_container_with_video_url",
        side_effect=["container-1", "container-2"],
    )
    @mock.patch.object(
        instagram_publish,
        "poll_container_status",
        side_effect=[
            instagram_publish.ContainerProcessingError(
                "container-1", "ERROR"
            ),
            "FINISHED",
        ],
    )
    @mock.patch.object(
        instagram_publish,
        "publish",
        return_value="media-2",
    )
    @mock.patch.object(
        instagram_publish,
        "verify",
        return_value={
            "permalink": "https://instagram.com/reel/media-2",
            "timestamp": "2026-07-21T00:00:00+0000",
        },
    )
    def test_retries_one_terminal_container_error_before_publishing(
        self,
        _verify,
        publish,
        poll,
        create_container,
        sleep,
    ):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp4") as video_file:
            video_file.write(b"video bytes")
            video_file.flush()

            with mock.patch.object(
                instagram_publish, "_funnel_video_url", self._fake_video_url
            ), mock.patch.object(
                instagram_publish, "LEDGER_PATH", self._tmp_ledger_path()
            ):
                result = instagram_publish.publish_reel(
                    video_file.name,
                    "caption",
                    dry_run=False,
                )

        self.assertEqual(result["media_id"], "media-2")
        self.assertEqual(create_container.call_count, 2)
        self.assertEqual(poll.call_count, 2)
        publish.assert_called_once_with("container-2")
        sleep.assert_called_once_with(
            instagram_publish.CONTAINER_RETRY_SECONDS
        )

    def _tmp_ledger_path(self):
        import tempfile

        directory = Path(tempfile.mkdtemp())
        return directory / "instagram_posts.json"


if __name__ == "__main__":
    unittest.main()
