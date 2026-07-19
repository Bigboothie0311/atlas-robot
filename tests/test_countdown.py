import unittest
from datetime import datetime

import countdown


class ParseCountdownTargetTests(unittest.TestCase):
    def test_how_many_days_until_form(self):
        self.assertEqual(
            countdown.parse_countdown_target("how many days until christmas"),
            "christmas",
        )

    def test_how_long_until_form(self):
        self.assertEqual(
            countdown.parse_countdown_target("how long until halloween"),
            "halloween",
        )

    def test_unrelated_text_returns_none(self):
        self.assertIsNone(countdown.parse_countdown_target("what's the weather"))


class ResolveMonthDayTests(unittest.TestCase):
    def test_named_holiday(self):
        self.assertEqual(countdown.resolve_month_day("christmas"), (12, 25))

    def test_month_day_phrase(self):
        self.assertEqual(countdown.resolve_month_day("march 5th"), (3, 5))
        self.assertEqual(countdown.resolve_month_day("march 5"), (3, 5))

    def test_unknown_phrase_returns_none(self):
        self.assertIsNone(countdown.resolve_month_day("my birthday"))

    def test_invalid_day_for_month_returns_none(self):
        self.assertIsNone(countdown.resolve_month_day("february 30"))


class DaysUntilTests(unittest.TestCase):
    def test_upcoming_date_this_year(self):
        now = datetime(2026, 7, 19)
        self.assertEqual(countdown.days_until(12, 25, now), 159)

    def test_date_already_passed_rolls_to_next_year(self):
        now = datetime(2026, 7, 19)
        self.assertEqual(countdown.days_until(3, 5, now), 229)

    def test_today_is_zero_days(self):
        now = datetime(2026, 7, 19)
        self.assertEqual(countdown.days_until(7, 19, now), 0)


class BuildCountdownAnswerTests(unittest.TestCase):
    def test_known_holiday_answer(self):
        now = datetime(2026, 7, 19)
        self.assertEqual(
            countdown.build_countdown_answer("christmas", now),
            "There are 159 days until christmas.",
        )

    def test_today_answer(self):
        now = datetime(2026, 7, 19)
        self.assertEqual(
            countdown.build_countdown_answer("july 19", now),
            "July 19 is today.",
        )

    def test_unresolvable_target_returns_none(self):
        self.assertIsNone(countdown.build_countdown_answer("my birthday"))


if __name__ == "__main__":
    unittest.main()
