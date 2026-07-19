import random
import unittest

import chance


class CoinFlipTests(unittest.TestCase):
    def test_is_coin_flip_command_matches_known_phrases(self):
        self.assertTrue(chance.is_coin_flip_command("flip a coin"))
        self.assertTrue(chance.is_coin_flip_command("heads or tails"))

    def test_is_coin_flip_command_rejects_unrelated_text(self):
        self.assertFalse(chance.is_coin_flip_command("what time is it"))

    def test_run_coin_flip_command_reports_the_injected_result(self):
        rng = random.Random(0)
        rng.choice = lambda options: "heads"
        self.assertEqual(chance.run_coin_flip_command(rng), "Heads.")


class DiceRollParsingTests(unittest.TestCase):
    def test_roll_a_die_defaults_to_one_d6(self):
        self.assertEqual(chance.parse_dice_roll_command("roll a die"), (1, 6))

    def test_roll_a_d20(self):
        self.assertEqual(chance.parse_dice_roll_command("roll a d20"), (1, 20))

    def test_roll_two_dice_word_form(self):
        self.assertEqual(chance.parse_dice_roll_command("roll two dice"), (2, 6))

    def test_roll_n_dn_form(self):
        self.assertEqual(chance.parse_dice_roll_command("roll 3 d6"), (3, 6))

    def test_unrelated_text_returns_none(self):
        self.assertIsNone(chance.parse_dice_roll_command("what's the weather"))

    def test_dice_count_and_sides_are_clamped(self):
        self.assertEqual(chance.parse_dice_roll_command("roll 50 d99999"), (6, 1000))


class DiceRollRunTests(unittest.TestCase):
    def test_single_die_reports_one_value(self):
        rng = random.Random(0)
        rng.randint = lambda a, b: 4
        self.assertEqual(chance.run_dice_roll_command(1, 6, rng), "You rolled a 4.")

    def test_multiple_dice_reports_total(self):
        rng = random.Random(0)
        values = iter([2, 5])
        rng.randint = lambda a, b: next(values)
        self.assertEqual(
            chance.run_dice_roll_command(2, 6, rng),
            "You rolled 2, 5 — total 7.",
        )


if __name__ == "__main__":
    unittest.main()
