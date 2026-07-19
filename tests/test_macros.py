import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import macros


class ParseTeachCommandTests(unittest.TestCase):
    def test_when_i_say_do_form(self):
        self.assertEqual(
            macros.parse_teach_command("when I say good morning do brief me"),
            ("good morning", ["brief me"]),
        )

    def test_when_i_say_you_should_form(self):
        self.assertEqual(
            macros.parse_teach_command(
                "when I say movie time you should go dark and then set a timer for two hours"
            ),
            ("movie time", ["go dark", "set a timer for two hours"]),
        )

    def test_if_i_say_then_form(self):
        self.assertEqual(
            macros.parse_teach_command("if I say wake up then boot my pc"),
            ("wake up", ["boot my pc"]),
        )

    def test_teach_you_means_form(self):
        self.assertEqual(
            macros.parse_teach_command("teach you that leaving means go dark and then lock up"),
            ("leaving", ["go dark", "lock up"]),
        )

    def test_bare_and_does_not_split_action(self):
        self.assertEqual(
            macros.parse_teach_command("when I say shopping do add milk and eggs to my shopping list"),
            ("shopping", ["add milk and eggs to my shopping list"]),
        )

    def test_unrelated_text_returns_none(self):
        self.assertIsNone(macros.parse_teach_command("what time is it"))

    def test_caps_action_count(self):
        actions_text = " and then ".join(f"action {i}" for i in range(8))
        trigger, actions = macros.parse_teach_command(f"when I say kitchen sink do {actions_text}")
        self.assertEqual(len(actions), macros.MAX_ACTIONS_PER_MACRO)


class ParseForgetMacroCommandTests(unittest.TestCase):
    def test_forget_macro_form(self):
        self.assertEqual(macros.parse_forget_macro_command("forget the macro good morning"), "good morning")

    def test_forget_what_means_form(self):
        self.assertEqual(macros.parse_forget_macro_command("forget what movie time means"), "movie time")

    def test_unlearn_form(self):
        self.assertEqual(macros.parse_forget_macro_command("unlearn wake up"), "wake up")

    def test_unrelated_text_returns_none(self):
        self.assertIsNone(macros.parse_forget_macro_command("what time is it"))


class ListMacrosPhraseTests(unittest.TestCase):
    def test_recognized_phrases(self):
        self.assertTrue(macros.is_list_macros_command("list my macros"))
        self.assertTrue(macros.is_list_macros_command("what commands have i taught you"))

    def test_unrelated_text_is_not_list_command(self):
        self.assertFalse(macros.is_list_macros_command("what time is it"))


class MacroStorageTests(unittest.TestCase):
    def test_teach_then_match_round_trips(self):
        with mock.patch.object(macros, "load_macros", return_value={}), \
                mock.patch.object(macros, "save_macros") as save_mock:
            macros.teach_macro("good morning", ["brief me"])

        saved = save_mock.call_args[0][0]
        self.assertEqual(saved["good morning"]["actions"], ["brief me"])

    def test_match_macro_returns_actions(self):
        existing = {"good morning": {"actions": ["brief me"], "taught": 1.0}}

        with mock.patch.object(macros, "load_macros", return_value=existing):
            self.assertEqual(macros.match_macro("good morning"), ["brief me"])

    def test_match_macro_missing_returns_none(self):
        with mock.patch.object(macros, "load_macros", return_value={}):
            self.assertIsNone(macros.match_macro("good morning"))

    def test_forget_macro_removes_entry(self):
        existing = {"good morning": {"actions": ["brief me"], "taught": 1.0}}

        with mock.patch.object(macros, "load_macros", return_value=existing), \
                mock.patch.object(macros, "save_macros") as save_mock:
            removed = macros.forget_macro("good morning")

        self.assertTrue(removed)
        save_mock.assert_called_once_with({})

    def test_forget_missing_macro_returns_false(self):
        with mock.patch.object(macros, "load_macros", return_value={}), \
                mock.patch.object(macros, "save_macros") as save_mock:
            removed = macros.forget_macro("good morning")

        self.assertFalse(removed)
        save_mock.assert_not_called()

    def test_teach_evicts_oldest_once_over_cap(self):
        existing = {
            f"trigger {i}": {"actions": ["do it"], "taught": float(i)}
            for i in range(macros.MAX_MACROS)
        }

        with mock.patch.object(macros, "load_macros", return_value=existing), \
                mock.patch.object(macros, "save_macros") as save_mock:
            macros.teach_macro("newest", ["do it"])

        saved = save_mock.call_args[0][0]
        self.assertEqual(len(saved), macros.MAX_MACROS)
        self.assertIn("newest", saved)
        self.assertNotIn("trigger 0", saved)

    def test_list_macros_summary_empty(self):
        with mock.patch.object(macros, "load_macros", return_value={}):
            self.assertEqual(
                macros.list_macros_summary(), "You haven't taught me any macros yet."
            )

    def test_list_macros_summary_includes_trigger_and_actions(self):
        existing = {"good morning": {"actions": ["brief me", "go dark"], "taught": 1.0}}

        with mock.patch.object(macros, "load_macros", return_value=existing):
            summary = macros.list_macros_summary()

        self.assertIn("good morning", summary)
        self.assertIn("brief me then go dark", summary)


class LoadMacrosTests(unittest.TestCase):
    def test_missing_file_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with mock.patch.object(macros, "MACROS_PATH", Path(tmp_dir) / "macros.json"):
                self.assertEqual(macros.load_macros(), {})

    def test_malformed_entries_are_skipped(self):
        raw = {
            "good": {"actions": ["brief me"], "taught": 1.0},
            "bad_shape": ["not", "a", "dict"],
            "bad_actions": {"actions": "not a list"},
            "empty_actions": {"actions": []},
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            macros_path = Path(tmp_dir) / "macros.json"
            macros_path.write_text(json.dumps(raw))

            with mock.patch.object(macros, "MACROS_PATH", macros_path):
                loaded = macros.load_macros()

        self.assertEqual(list(loaded.keys()), ["good"])


if __name__ == "__main__":
    unittest.main()
