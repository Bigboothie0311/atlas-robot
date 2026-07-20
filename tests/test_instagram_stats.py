import unittest
from unittest import mock

import instagram_stats


class InstagramStatsTests(unittest.TestCase):
    def setUp(self):
        self.original_cache = instagram_stats._cache
        instagram_stats._cache = {"data": None, "fetched_at": 0.0}

    def tearDown(self):
        instagram_stats._cache = self.original_cache

    @mock.patch.object(instagram_stats, "_load_config", return_value={})
    def test_missing_config_is_safe(self, load_config):
        data = instagram_stats.get_stats()

        self.assertFalse(data["configured"])
        self.assertFalse(data["available"])
        load_config.assert_called_once_with()

    @mock.patch.object(
        instagram_stats,
        "_load_config",
        return_value={
            "INSTAGRAM_ACCESS_TOKEN": "secret",
            "INSTAGRAM_ACCOUNT_ID": "123",
        },
    )
    @mock.patch.object(instagram_stats, "_request")
    def test_fetches_profile_latest_media_and_insights(self, request, _load_config):
        request.side_effect = [
            {"username": "atlas", "followers_count": 7, "media_count": 2},
            {"data": [{"id": "media-1", "media_type": "VIDEO", "like_count": 3, "comments_count": 1}]},
            {"data": [
                {"name": "views", "values": [{"value": 44}]},
                {"name": "reach", "values": [{"value": 30}]},
            ]},
        ]

        data = instagram_stats.get_stats()

        self.assertTrue(data["available"])
        self.assertEqual(7, data["followers_count"])
        self.assertEqual(44, data["latest"]["views"])
        self.assertEqual(30, data["latest"]["reach"])
        self.assertEqual(3, data["latest"]["likes"])

    @mock.patch.object(
        instagram_stats,
        "_load_config",
        return_value={
            "INSTAGRAM_ACCESS_TOKEN": "secret",
            "INSTAGRAM_ACCOUNT_ID": "123",
        },
    )
    def test_cache_only_does_not_make_first_network_request(self, _load_config):
        data = instagram_stats.get_stats(allow_fetch=False)

        self.assertTrue(data["configured"])
        self.assertTrue(data["stale"])
        self.assertFalse(data["available"])


if __name__ == "__main__":
    unittest.main()
