import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cost_ledger


class LoadLedgerTests(unittest.TestCase):
    def test_returns_empty_ledger_when_file_missing(self):
        path = mock.Mock()
        path.exists.return_value = False

        with mock.patch.object(cost_ledger, "current_month", return_value="2026-07"):
            ledger = cost_ledger.load_ledger(path)

        self.assertEqual(
            ledger,
            {
                "month": "2026-07",
                "spent_usd": 0.0,
                "requests": 0,
                "by_purpose": {},
                "premium_voice_spent_usd": 0.0,
            },
        )

    def test_rolls_over_to_zero_on_a_new_month(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            path.write_text(
                json.dumps(
                    {
                        "month": "2026-06",
                        "spent_usd": 7.5,
                        "requests": 400,
                        "by_purpose": {"answer": 7.5},
                        "premium_voice_spent_usd": 4.0,
                    }
                )
            )

            with mock.patch.object(
                cost_ledger, "current_month", return_value="2026-07"
            ):
                ledger = cost_ledger.load_ledger(path)

        self.assertEqual(ledger["month"], "2026-07")
        self.assertEqual(ledger["spent_usd"], 0.0)
        self.assertEqual(ledger["by_purpose"], {})
        self.assertEqual(ledger["premium_voice_spent_usd"], 0.0)

    def test_backfills_missing_optional_keys_from_older_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            path.write_text(
                json.dumps(
                    {"month": "2026-07", "spent_usd": 1.2, "requests": 10}
                )
            )

            with mock.patch.object(
                cost_ledger, "current_month", return_value="2026-07"
            ):
                ledger = cost_ledger.load_ledger(path)

        self.assertEqual(ledger["spent_usd"], 1.2)
        self.assertEqual(ledger["by_purpose"], {})
        self.assertEqual(ledger["premium_voice_spent_usd"], 0.0)


class SaveLedgerTests(unittest.TestCase):
    def test_writes_ledger_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "usage.json"
            cost_ledger.save_ledger(
                {"month": "2026-07", "spent_usd": 1.0}, path
            )

            self.assertTrue(path.exists())
            self.assertEqual(
                json.loads(path.read_text())["spent_usd"], 1.0
            )
            self.assertFalse(path.with_suffix(".tmp").exists())


class RecordSpendTests(unittest.TestCase):
    def test_accumulates_spend_and_purpose_without_mutating_input(self):
        ledger = {
            "month": "2026-07",
            "spent_usd": 1.0,
            "requests": 5,
            "by_purpose": {"answer": 1.0},
            "premium_voice_spent_usd": 0.0,
        }

        updated = cost_ledger.record_spend(ledger, 0.5, purpose="answer")

        self.assertEqual(updated["spent_usd"], 1.5)
        self.assertEqual(updated["by_purpose"]["answer"], 1.5)
        self.assertEqual(ledger["spent_usd"], 1.0, "input must not mutate")

    def test_tracks_nested_and_retry_calls_under_distinct_purposes(self):
        ledger = cost_ledger._empty_ledger("2026-07")

        ledger = cost_ledger.record_spend(ledger, 0.10, purpose="planner")
        ledger = cost_ledger.record_spend(ledger, 0.20, purpose="answer")
        ledger = cost_ledger.record_spend(ledger, 0.05, purpose="retry")
        ledger = cost_ledger.record_spend(ledger, 0.05, purpose="retry")

        self.assertAlmostEqual(ledger["spent_usd"], 0.40)
        self.assertAlmostEqual(ledger["by_purpose"]["retry"], 0.10)
        self.assertEqual(
            set(ledger["by_purpose"]),
            {"planner", "answer", "retry"},
        )

    def test_premium_voice_spend_also_tracked_in_sub_budget(self):
        ledger = cost_ledger._empty_ledger("2026-07")

        ledger = cost_ledger.record_spend(
            ledger, 1.5, purpose="voice", is_premium_voice=True
        )

        self.assertEqual(ledger["premium_voice_spent_usd"], 1.5)
        self.assertEqual(ledger["spent_usd"], 1.5)


class CheckBudgetTests(unittest.TestCase):
    def test_allows_request_under_limit(self):
        ledger = {"spent_usd": 1.0}
        cost_ledger.check_budget(ledger, limit=8.0, reserve=0.01)

    def test_hard_cutoff_raises_when_reserve_would_exceed_limit(self):
        ledger = {"spent_usd": 7.995}

        with self.assertRaises(cost_ledger.BudgetExceeded):
            cost_ledger.check_budget(ledger, limit=8.0, reserve=0.01)

    def test_exact_limit_boundary_is_allowed(self):
        ledger = {"spent_usd": 7.99}
        cost_ledger.check_budget(ledger, limit=8.0, reserve=0.01)


class PremiumVoiceStatusTests(unittest.TestCase):
    def test_below_warn_threshold(self):
        with mock.patch.object(
            cost_ledger.robot_config, "get_float",
            side_effect=lambda key, default: default,
        ):
            status = cost_ledger.premium_voice_status(
                {"premium_voice_spent_usd": 1.0}
            )

        self.assertFalse(status["should_warn"])
        self.assertFalse(status["should_fallback_to_local"])

    def test_warn_at_configured_threshold(self):
        with mock.patch.object(
            cost_ledger.robot_config, "get_float",
            side_effect=lambda key, default: default,
        ):
            status = cost_ledger.premium_voice_status(
                {"premium_voice_spent_usd": 3.50}
            )

        self.assertTrue(status["should_warn"])
        self.assertFalse(status["should_fallback_to_local"])

    def test_hard_cutoff_triggers_local_fallback(self):
        with mock.patch.object(
            cost_ledger.robot_config, "get_float",
            side_effect=lambda key, default: default,
        ):
            status = cost_ledger.premium_voice_status(
                {"premium_voice_spent_usd": 5.00}
            )

        self.assertTrue(status["should_fallback_to_local"])


class BudgetSummaryTests(unittest.TestCase):
    def test_reports_remaining_and_fallback_state(self):
        ledger = {
            "month": "2026-07",
            "spent_usd": 7.5,
            "requests": 300,
            "by_purpose": {"answer": 7.5},
            "premium_voice_spent_usd": 1.0,
        }

        with mock.patch.object(
            cost_ledger.robot_config, "get_float",
            side_effect=lambda key, default: default,
        ):
            summary = cost_ledger.budget_summary(ledger)

        self.assertEqual(summary["limit_usd"], 8.00)
        self.assertAlmostEqual(summary["remaining_usd"], 0.5)
        self.assertFalse(summary["fallback_active"])

    def test_fallback_active_once_reserve_would_exceed_limit(self):
        ledger = {
            "month": "2026-07",
            "spent_usd": 7.999,
            "requests": 300,
            "by_purpose": {},
            "premium_voice_spent_usd": 0.0,
        }

        with mock.patch.object(
            cost_ledger.robot_config, "get_float",
            side_effect=lambda key, default: default,
        ):
            summary = cost_ledger.budget_summary(ledger)

        self.assertTrue(summary["fallback_active"])


if __name__ == "__main__":
    unittest.main()
