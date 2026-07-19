import unittest
from unittest import mock

import ai_tools


class RunToolCallTests(unittest.TestCase):
    def test_unknown_tool_name_is_reported(self):
        self.assertEqual(ai_tools.run_tool_call("not_a_real_tool", {}), "Unknown tool: not_a_real_tool")

    def test_get_weather_dispatches_with_arguments(self):
        with mock.patch.object(ai_tools, "get_weather", return_value="sunny") as get_weather:
            result = ai_tools.run_tool_call("get_weather", {"location": "Denver", "day": "today"})

        get_weather.assert_called_once_with("Denver", "today")
        self.assertEqual(result, "sunny")

    def test_diagnostic_tool_dispatches_to_listen_and_answer(self):
        import listen_and_answer

        with mock.patch.object(
            listen_and_answer, "run_diagnostic_capability", return_value="all good"
        ) as handler:
            result = ai_tools.run_tool_call(
                "run_atlas_diagnostic_or_repair", {"capability": "diagnostics"}
            )

        handler.assert_called_once_with("diagnostics")
        self.assertEqual(result, "all good")


class ToolRegistrySchemaTests(unittest.TestCase):
    def test_diagnostic_tool_is_registered_with_expected_capabilities(self):
        tool = next(
            t for t in ai_tools.TOOLS
            if isinstance(t, dict) and t.get("name") == "run_atlas_diagnostic_or_repair"
        )
        enum_values = tool["parameters"]["properties"]["capability"]["enum"]

        for expected in ("diagnostics", "self_heal", "system_health", "storage"):
            self.assertIn(expected, enum_values)


if __name__ == "__main__":
    unittest.main()
