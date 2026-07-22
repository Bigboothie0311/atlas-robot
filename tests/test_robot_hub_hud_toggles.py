import unittest
import tempfile
from pathlib import Path
from unittest import mock

import robot_hub


class WeatherOverlayRouteTests(unittest.TestCase):
    def test_open_and_close_reflected_in_state(self):
        client = robot_hub.app.test_client()

        try:
            response = client.post("/hud/weather_overlay", json={"open": True})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["open"])
            self.assertTrue(client.get("/state").get_json()["weather_overlay"])

            response = client.post("/hud/weather_overlay", json={"open": False})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.get_json()["open"])
            self.assertFalse(client.get("/state").get_json()["weather_overlay"])
        finally:
            with robot_hub._weather_overlay_lock:
                robot_hub._weather_overlay_open = False

    def test_missing_body_defaults_to_closed(self):
        client = robot_hub.app.test_client()

        try:
            response = client.post("/hud/weather_overlay")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.get_json()["open"])
        finally:
            with robot_hub._weather_overlay_lock:
                robot_hub._weather_overlay_open = False


class BrightnessBoostRouteTests(unittest.TestCase):
    def test_boost_and_restore_reflected_in_state(self):
        client = robot_hub.app.test_client()

        try:
            response = client.post("/hud/brightness_boost", json={"boost": True})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["boost"])
            self.assertTrue(client.get("/state").get_json()["brightness_boost"])

            response = client.post("/hud/brightness_boost", json={"boost": False})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.get_json()["boost"])
            self.assertFalse(client.get("/state").get_json()["brightness_boost"])
        finally:
            with robot_hub._brightness_boost_lock:
                robot_hub._brightness_boost = False


class RecordingIndicatorRouteTests(unittest.TestCase):
    def test_active_and_inactive_reflected_in_state(self):
        client = robot_hub.app.test_client()

        try:
            response = client.post("/hud/recording", json={"active": True})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["active"])
            self.assertTrue(client.get("/state").get_json()["recording_active"])

            response = client.post("/hud/recording", json={"active": False})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.get_json()["active"])
            self.assertFalse(client.get("/state").get_json()["recording_active"])
        finally:
            with robot_hub._recording_lock:
                robot_hub._recording_active = False


class ShowcaseFocusRouteTests(unittest.TestCase):
    def test_focus_and_clear_are_reflected_in_state(self):
        client = robot_hub.app.test_client()

        try:
            response = client.post(
                "/hud/showcase_focus", json={"focus": "instagram"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                client.get("/state").get_json()["showcase_focus"],
                "instagram",
            )

            response = client.post(
                "/hud/showcase_focus", json={"focus": None}
            )
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(
                client.get("/state").get_json()["showcase_focus"]
            )
        finally:
            with robot_hub._showcase_focus_lock:
                robot_hub._showcase_focus = None

    def test_unknown_focus_is_rejected(self):
        response = robot_hub.app.test_client().post(
            "/hud/showcase_focus", json={"focus": "warp_core"}
        )
        self.assertEqual(response.status_code, 400)


class ReelPreviewRouteTests(unittest.TestCase):
    def test_preview_serves_exact_media_and_blocks_through_audio(self):
        observed = {}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "reel.mp4"
            video.write_bytes(b"video")

            def fake_ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b"wav")
                return mock.Mock(returncode=0, stderr="")

            class FakePlayback:
                returncode = 0

                def wait(self):
                    state = robot_hub.app.test_client().get(
                        "/state"
                    ).get_json()["reel_preview"]
                    observed["active"] = state["active"]
                    media = robot_hub.app.test_client().get(
                        "/hud/reel_preview/media",
                        query_string={"token": state["token"]},
                    )
                    observed["media_status"] = media.status_code
                    observed["media"] = media.data

            with (
                mock.patch.object(robot_hub, "REEL_PREVIEW_ROOT", root),
                mock.patch.object(robot_hub.subprocess, "run", fake_ffmpeg),
                mock.patch.object(
                    robot_hub.subprocess,
                    "Popen",
                    return_value=FakePlayback(),
                ),
                mock.patch.object(robot_hub.time, "sleep"),
            ):
                response = robot_hub.app.test_client().post(
                    "/hud/reel_preview", json={"video_path": str(video)}
                )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["played"])
            self.assertTrue(observed["active"])
            self.assertEqual(observed["media_status"], 200)
            self.assertEqual(observed["media"], b"video")
            self.assertFalse(
                robot_hub.app.test_client().get("/state").get_json()[
                    "reel_preview"
                ]["active"]
            )

    def test_preview_rejects_media_outside_staging_root(self):
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as other:
            video = Path(other) / "reel.mp4"
            video.write_bytes(b"video")
            with mock.patch.object(
                robot_hub, "REEL_PREVIEW_ROOT", Path(allowed)
            ):
                response = robot_hub.app.test_client().post(
                    "/hud/reel_preview", json={"video_path": str(video)}
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("outside", response.get_json()["error"])

    def test_missing_body_defaults_to_inactive(self):
        client = robot_hub.app.test_client()

        try:
            response = client.post("/hud/recording")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.get_json()["active"])
        finally:
            with robot_hub._recording_lock:
                robot_hub._recording_active = False


if __name__ == "__main__":
    unittest.main()
