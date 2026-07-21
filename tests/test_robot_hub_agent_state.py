import copy
import time
import unittest

import robot_hub


class AgentHudStateTests(unittest.TestCase):
    def setUp(self):
        with robot_hub.state_lock:
            self.original_state = copy.deepcopy(
                robot_hub.robot_state
            )
            robot_hub.robot_state["agent"] = (
                robot_hub._default_agent_state()
            )

        self.client = robot_hub.app.test_client()

    def tearDown(self):
        with robot_hub.state_lock:
            robot_hub.robot_state.clear()
            robot_hub.robot_state.update(
                self.original_state
            )

    def post_event(self, name, data):
        response = self.client.post(
            "/agent/event",
            json={
                "name": name,
                "source": "test",
                "data": data,
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["agent"]

    def test_agent_lifecycle_updates_structured_hud_state(self):
        task_id = "task-1"
        plan_id = "plan-1"

        agent = self.post_event(
            "agent.planning.started",
            {
                "task_id": task_id,
                "goal": "Find my Atlas handoff.",
                "source": "voice",
            },
        )
        self.assertTrue(agent["active"])
        self.assertEqual(agent["phase"], "planning")
        self.assertEqual(
            agent["goal"],
            "Find my Atlas handoff.",
        )

        agent = self.post_event(
            "agent.planning.completed",
            {
                "task_id": task_id,
                "plan_id": plan_id,
                "step_count": 2,
                "attempts": 2,
                "input_tokens": 1403,
                "output_tokens": 127,
            },
        )
        self.assertEqual(agent["phase"], "plan_ready")
        self.assertEqual(agent["step_count"], 2)
        self.assertEqual(agent["retry_count"], 1)
        self.assertEqual(agent["input_tokens"], 1403)
        self.assertEqual(agent["output_tokens"], 127)

        agent = self.post_event(
            "agent.workflow.started",
            {
                "task_id": task_id,
                "plan_id": plan_id,
                "goal": "Find my Atlas handoff.",
                "step_count": 2,
            },
        )
        self.assertEqual(agent["phase"], "executing")

        agent = self.post_event(
            "agent.step.started",
            {
                "task_id": task_id,
                "plan_id": plan_id,
                "position": 1,
                "tool_name": "pc.search_files",
                "description": "Search approved folders.",
                "target": "windows_pc",
            },
        )
        self.assertEqual(agent["current_step"], 1)
        self.assertEqual(
            agent["tool_name"],
            "pc.search_files",
        )
        self.assertEqual(agent["target"], "windows_pc")
        self.assertEqual(agent["evidence"], {})

        agent = self.post_event(
            "agent.step.completed",
            {
                "task_id": task_id,
                "plan_id": plan_id,
                "position": 1,
                "tool_name": "pc.search_files",
                "verified": True,
                "target": "windows_pc",
                "evidence": {
                    "count": 4,
                    "query": "atlas",
                },
            },
        )
        self.assertEqual(agent["completed_steps"], 1)
        self.assertEqual(
            agent["evidence"],
            {"count": 4, "query": "atlas"},
        )

        agent = self.post_event(
            "agent.workflow.completed",
            {
                "task_id": task_id,
                "plan_id": plan_id,
                "status": "completed",
                "completed_steps": 2,
                "failed_step": None,
                "confirmation_call_id": None,
                "error": None,
            },
        )
        self.assertFalse(agent["active"])
        self.assertEqual(agent["phase"], "completed")
        self.assertGreater(
            agent["visible_until"],
            time.time(),
        )

    def test_completed_event_records_supplied_step_count(self):
        agent = self.post_event(
            "agent.workflow.completed",
            {
                "task_id": "direct-completion",
                "goal": "Verify mission completion.",
                "source": "voice",
                "step_count": 1,
                "completed_steps": 1,
            },
        )

        self.assertEqual(agent["step_count"], 1)
        self.assertEqual(agent["completed_steps"], 1)
        self.assertEqual(agent["current_step"], 1)

    def test_step_evidence_is_sanitized_and_bounded(self):
        agent = self.post_event(
            "agent.step.started",
            {
                "task_id": "task-2",
                "position": 1,
                "tool_name": "pi.run_diagnostics",
                "target": "raspberry_pi",
            },
        )
        self.assertEqual(agent["target"], "raspberry_pi")

        agent = self.post_event(
            "agent.step.completed",
            {
                "task_id": "task-2",
                "position": 1,
                "tool_name": "pi.run_diagnostics",
                "verified": True,
                "evidence": {
                    "count": 14,
                    "oversized": "x" * 500,
                    "nested": {"dropped": True},
                    "listed": [1, 2, 3],
                    "flag": True,
                    "ratio": 0.5,
                    "extra_one": 1,
                    "extra_two": 2,
                },
            },
        )
        evidence = agent["evidence"]
        self.assertEqual(evidence["count"], 14)
        self.assertLessEqual(
            len(evidence["oversized"]), 160
        )
        self.assertNotIn("nested", evidence)
        self.assertNotIn("listed", evidence)
        self.assertLessEqual(len(evidence), 6)

    def test_step_completed_ignores_malformed_evidence(self):
        agent = self.post_event(
            "agent.step.completed",
            {
                "task_id": "task-3",
                "position": 1,
                "tool_name": "pi.run_diagnostics",
                "verified": True,
                "evidence": "not an object",
            },
        )
        self.assertEqual(agent["evidence"], {})

    def test_terminal_agent_state_expires_back_to_idle(self):
        with robot_hub.state_lock:
            robot_hub.robot_state["agent"] = {
                **robot_hub._default_agent_state(),
                "phase": "completed",
                "status": "completed",
                "visible_until": 10.0,
            }
            robot_hub._expire_agent_state_locked(
                now=11.0
            )

            self.assertEqual(
                robot_hub.robot_state["agent"],
                robot_hub._default_agent_state(),
            )


if __name__ == "__main__":
    unittest.main()
