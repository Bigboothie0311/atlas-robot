import unittest
from unittest import mock

import hud_stats


class GetStorageReportStatsTests(unittest.TestCase):
    def test_delegates_to_storage_monitor(self):
        expected = {"percent": 40.0, "level": "ok"}

        with mock.patch.object(
            hud_stats.storage_monitor,
            "get_storage_report",
            return_value=expected,
        ):
            result = hud_stats.get_storage_report_stats()

        self.assertEqual(result, expected)


class GetBudgetStatsTests(unittest.TestCase):
    def test_delegates_to_cost_ledger(self):
        expected = {"spent_usd": 1.0, "limit_usd": 8.0}

        with mock.patch.object(
            hud_stats.cost_ledger, "budget_summary", return_value=expected
        ):
            result = hud_stats.get_budget_stats()

        self.assertEqual(result, expected)


class GetHudStatsIncludesNewKeysTests(unittest.TestCase):
    def test_hud_stats_includes_storage_and_budget(self):
        with (
            mock.patch.object(
                hud_stats, "get_weather_stats", return_value={}
            ),
            mock.patch.object(
                hud_stats, "get_storage_report_stats", return_value={"level": "ok"}
            ),
            mock.patch.object(
                hud_stats, "get_budget_stats", return_value={"spent_usd": 0.0}
            ),
            mock.patch.object(hud_stats.pc_stats, "get_gaming_pc_stats", return_value={}),
            mock.patch.object(
                hud_stats.instagram_stats, "get_stats", return_value={}
            ),
        ):
            stats = hud_stats.get_hud_stats()

        self.assertIn("storage", stats)
        self.assertIn("budget", stats)
        self.assertEqual(stats["storage"], {"level": "ok"})
        self.assertEqual(stats["budget"], {"spent_usd": 0.0})


if __name__ == "__main__":
    unittest.main()
