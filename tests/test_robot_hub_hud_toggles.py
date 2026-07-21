import unittest

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
