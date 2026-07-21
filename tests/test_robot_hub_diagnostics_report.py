import copy
import unittest

import robot_hub


class DiagnosticsReportTests(unittest.TestCase):
    def setUp(self):
        with robot_hub.state_lock:
            self.original_state = copy.deepcopy(
                robot_hub.robot_state
            )
            robot_hub.robot_state["hud_layout"] = "idle"
            robot_hub.robot_state["diagnostics_report"] = (
                robot_hub._default_diagnostics_report()
            )

        self.client = robot_hub.app.test_client()

    def tearDown(self):
        with robot_hub.state_lock:
            robot_hub.robot_state.clear()
            robot_hub.robot_state.update(
                self.original_state
            )

    def post_report(self, findings):
        return self.client.post(
            "/diagnostics_report",
            json={"findings": findings},
        )

    def test_report_sets_layout_and_stores_findings(self):
        response = self.post_report(
            [
                {
                    "component": "services",
                    "ok": True,
                    "detail": "all 5 services active",
                },
                {
                    "component": "camera",
                    "ok": False,
                    "detail": "no camera device connected",
                },
            ]
        )

        self.assertEqual(response.status_code, 200)

        with robot_hub.state_lock:
            state = copy.deepcopy(robot_hub.robot_state)

        self.assertEqual(
            state["hud_layout"], "diagnostics"
        )
        report = state["diagnostics_report"]
        self.assertEqual(len(report["findings"]), 2)
        self.assertEqual(
            report["findings"][1]["component"], "camera"
        )
        self.assertFalse(report["findings"][1]["ok"])
        self.assertGreater(report["visible_until"], 0)

    def test_report_sanitizes_malformed_findings(self):
        response = self.post_report(
            [
                {"component": "ok_one", "ok": True, "detail": "fine"},
                "not an object",
                {"component": 5, "ok": True, "detail": "bad name"},
                {
                    "component": "long_detail",
                    "ok": False,
                    "detail": "x" * 1000,
                },
            ]
        )

        self.assertEqual(response.status_code, 200)

        with robot_hub.state_lock:
            findings = copy.deepcopy(
                robot_hub.robot_state["diagnostics_report"][
                    "findings"
                ]
            )

        components = [f["component"] for f in findings]
        self.assertEqual(
            components, ["ok_one", "long_detail"]
        )
        self.assertLessEqual(
            len(findings[1]["detail"]), 200
        )

    def test_report_rejects_non_list_findings(self):
        response = self.client.post(
            "/diagnostics_report",
            json={"findings": "everything is great"},
        )

        self.assertEqual(response.status_code, 400)

    def test_expired_report_returns_layout_to_idle(self):
        self.post_report(
            [
                {
                    "component": "services",
                    "ok": True,
                    "detail": "fine",
                },
            ]
        )

        with robot_hub.state_lock:
            robot_hub.robot_state["diagnostics_report"][
                "visible_until"
            ] = 10.0
            robot_hub._expire_diagnostics_report_locked(
                now=11.0
            )
            state = copy.deepcopy(robot_hub.robot_state)

        self.assertEqual(state["hud_layout"], "idle")
        self.assertEqual(
            state["diagnostics_report"]["findings"], []
        )

    def test_expiry_leaves_other_layouts_alone(self):
        with robot_hub.state_lock:
            robot_hub.robot_state["hud_layout"] = "security"
            robot_hub.robot_state["diagnostics_report"] = {
                "findings": [],
                "ts": 0.0,
                "visible_until": 10.0,
            }
            robot_hub._expire_diagnostics_report_locked(
                now=11.0
            )
            layout = robot_hub.robot_state["hud_layout"]

        self.assertEqual(layout, "security")

    def test_hud_static_assets_are_not_cached(self):
        response = self.client.get("/hud/static/app.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "no-store",
            response.headers.get("Cache-Control", ""),
        )

    def test_hud_page_is_not_cached(self):
        response = self.client.get("/hud")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "no-store",
            response.headers.get("Cache-Control", ""),
        )

    def test_hud_ships_a_diagnostics_overlay(self):
        html = self.client.get("/hud").get_data(as_text=True)
        js = self.client.get(
            "/hud/static/app.js"
        ).get_data(as_text=True)

        self.assertIn("diagnostics-overlay", html)
        self.assertIn("diagnostics-grid", html)
        self.assertIn("renderDiagnostics", js)


if __name__ == "__main__":
    unittest.main()
