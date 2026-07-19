import unittest

import robot_hub


class DismissRouteTests(unittest.TestCase):
    def test_dismiss_restores_idle_without_touching_security_storage(self):
        with robot_hub.state_lock:
            original = dict(robot_hub.robot_state)
            robot_hub.robot_state["hud_layout"] = "security"
            robot_hub.robot_state["intruder_records"] = [{"id": "kept-on-disk"}]
            robot_hub.robot_state["active_intruder_photo"] = {"id": "kept-on-disk"}
            robot_hub.robot_state["activity_label"] = "BUSY"
            robot_hub.robot_state["expression"] = "thinking"

        try:
            response = robot_hub.app.test_client().post("/dismiss")

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["security_preserved"])

            with robot_hub.state_lock:
                self.assertEqual(robot_hub.robot_state["hud_layout"], "idle")
                self.assertEqual(robot_hub.robot_state["intruder_records"], [])
                self.assertIsNone(robot_hub.robot_state["active_intruder_photo"])
                self.assertIsNone(robot_hub.robot_state["activity_label"])
                self.assertEqual(robot_hub.robot_state["expression"], "happy")
        finally:
            with robot_hub.state_lock:
                robot_hub.robot_state.clear()
                robot_hub.robot_state.update(original)


if __name__ == "__main__":
    unittest.main()
