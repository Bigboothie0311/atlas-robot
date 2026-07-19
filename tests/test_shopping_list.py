import unittest
from unittest import mock

import memory_store


class ParseAddShoppingItemCommandTests(unittest.TestCase):
    def test_add_to_shopping_list(self):
        self.assertEqual(
            memory_store.parse_add_shopping_item_command("add milk to my shopping list"),
            "milk",
        )

    def test_put_on_grocery_list(self):
        self.assertEqual(
            memory_store.parse_add_shopping_item_command("put eggs on the grocery list"),
            "eggs",
        )

    def test_unrelated_text_returns_none(self):
        self.assertIsNone(memory_store.parse_add_shopping_item_command("what time is it"))

    def test_add_without_shopping_list_suffix_is_not_matched(self):
        # This module only owns phrases that explicitly say "shopping/grocery
        # list" — a bare "add X to Y" belongs to whatever Y actually is.
        self.assertIsNone(
            memory_store.parse_add_shopping_item_command("add a reminder to my calendar")
        )


class ParseRemoveShoppingItemCommandTests(unittest.TestCase):
    def test_remove_from_shopping_list(self):
        self.assertEqual(
            memory_store.parse_remove_shopping_item_command("remove milk from my shopping list"),
            "milk",
        )

    def test_take_off_grocery_list(self):
        self.assertEqual(
            memory_store.parse_remove_shopping_item_command("take eggs off the grocery list"),
            "eggs",
        )

    def test_unrelated_text_returns_none(self):
        self.assertIsNone(memory_store.parse_remove_shopping_item_command("what time is it"))


class ShoppingListPhraseTests(unittest.TestCase):
    def test_read_phrases_recognized(self):
        self.assertTrue(memory_store.is_read_shopping_list_command("what's on my shopping list"))

    def test_clear_phrases_recognized(self):
        self.assertTrue(memory_store.is_clear_shopping_list_command("clear my shopping list"))

    def test_unrelated_text_is_not_read_or_clear(self):
        self.assertFalse(memory_store.is_read_shopping_list_command("what time is it"))
        self.assertFalse(memory_store.is_clear_shopping_list_command("what time is it"))


class ShoppingListStorageTests(unittest.TestCase):
    def test_add_then_load_round_trips(self):
        with mock.patch.object(memory_store, "load_shopping_list", return_value=[]), \
                mock.patch.object(memory_store, "save_shopping_list") as save_mock:
            memory_store.add_shopping_item("milk")

        saved_items = save_mock.call_args[0][0]
        self.assertEqual([item["text"] for item in saved_items], ["milk"])

    def test_add_skips_case_insensitive_duplicate(self):
        existing = [{"text": "Milk", "added": 1.0}]

        with mock.patch.object(memory_store, "load_shopping_list", return_value=existing), \
                mock.patch.object(memory_store, "save_shopping_list") as save_mock:
            memory_store.add_shopping_item("milk")

        save_mock.assert_not_called()

    def test_remove_matching_item_returns_true(self):
        existing = [{"text": "Milk", "added": 1.0}, {"text": "Eggs", "added": 2.0}]

        with mock.patch.object(memory_store, "load_shopping_list", return_value=existing), \
                mock.patch.object(memory_store, "save_shopping_list") as save_mock:
            removed = memory_store.remove_shopping_item("milk")

        self.assertTrue(removed)
        saved_items = save_mock.call_args[0][0]
        self.assertEqual([item["text"] for item in saved_items], ["Eggs"])

    def test_remove_missing_item_returns_false(self):
        with mock.patch.object(memory_store, "load_shopping_list", return_value=[]), \
                mock.patch.object(memory_store, "save_shopping_list") as save_mock:
            removed = memory_store.remove_shopping_item("milk")

        self.assertFalse(removed)
        save_mock.assert_not_called()

    def test_get_shopping_list_summary_returns_texts(self):
        existing = [{"text": "Milk", "added": 1.0}, {"text": "Eggs", "added": 2.0}]

        with mock.patch.object(memory_store, "load_shopping_list", return_value=existing):
            self.assertEqual(memory_store.get_shopping_list_summary(), ["Milk", "Eggs"])


if __name__ == "__main__":
    unittest.main()
