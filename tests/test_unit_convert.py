import unittest

import unit_convert


class ParseConversionCommandTests(unittest.TestCase):
    def test_convert_form(self):
        self.assertEqual(
            unit_convert.parse_conversion_command("convert 10 miles to kilometers"),
            (10.0, "mi", "km"),
        )

    def test_what_is_form(self):
        self.assertEqual(
            unit_convert.parse_conversion_command("what is 100 fahrenheit in celsius"),
            (100.0, "f", "c"),
        )

    def test_how_many_form_reorders_units_and_value(self):
        self.assertEqual(
            unit_convert.parse_conversion_command("how many kilometers is 10 miles"),
            (10.0, "mi", "km"),
        )

    def test_unrelated_text_returns_none(self):
        self.assertIsNone(unit_convert.parse_conversion_command("what's the weather"))

    def test_unknown_unit_returns_none(self):
        self.assertIsNone(unit_convert.parse_conversion_command("convert 10 smoots to kilometers"))


class ConvertTests(unittest.TestCase):
    def test_fahrenheit_to_celsius(self):
        self.assertAlmostEqual(unit_convert.convert(212, "f", "c"), 100.0)

    def test_celsius_to_fahrenheit(self):
        self.assertAlmostEqual(unit_convert.convert(0, "c", "f"), 32.0)

    def test_miles_to_kilometers(self):
        self.assertAlmostEqual(unit_convert.convert(1, "mi", "km"), 1.609344, places=5)

    def test_pounds_to_kilograms(self):
        self.assertAlmostEqual(unit_convert.convert(1, "lb", "kg"), 0.45359237, places=5)

    def test_same_unit_is_a_no_op(self):
        self.assertEqual(unit_convert.convert(42, "km", "km"), 42)

    def test_incompatible_categories_return_none(self):
        self.assertIsNone(unit_convert.convert(10, "lb", "mi"))


class RunConversionCommandTests(unittest.TestCase):
    def test_formats_a_spoken_answer(self):
        self.assertEqual(
            unit_convert.run_conversion_command(212, "f", "c"),
            "212 degrees Fahrenheit is 100 degrees Celsius.",
        )

    def test_incompatible_units_produce_a_graceful_answer(self):
        answer = unit_convert.run_conversion_command(10, "lb", "mi")
        self.assertIn("can't convert", answer)


if __name__ == "__main__":
    unittest.main()
