import unittest
from unittest import mock

import instagram_stats
from atlas_growth import GrowthStore


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

    @mock.patch.object(
        instagram_stats,
        "_load_config",
        return_value={
            "INSTAGRAM_ACCESS_TOKEN": "secret",
            "INSTAGRAM_ACCOUNT_ID": "123",
        },
    )
    @mock.patch.object(instagram_stats, "_request")
    def test_growth_snapshot_fetches_history_insights_and_comments(
        self, request, _load_config
    ):
        request.side_effect = [
            {"data": [{
                "id": "media-1",
                "timestamp": "2026-07-21T12:00:00+00:00",
                "like_count": 4,
                "comments_count": 1,
            }]},
            {"data": [
                {"name": "views", "values": [{"value": 120}]},
                {"name": "shares", "values": [{"value": 5}]},
            ]},
            {"data": [{
                "id": "comment-1",
                "text": "Can you test a Pi camera?",
                "username": "viewer",
            }]},
        ]

        snapshot = instagram_stats.fetch_growth_snapshot(media_limit=5)

        self.assertTrue(snapshot["configured"])
        self.assertEqual(120, snapshot["media"][0]["insights"]["views"])
        self.assertEqual(5, snapshot["media"][0]["insights"]["shares"])
        self.assertEqual(
            "comment-1", snapshot["media"][0]["public_comments"][0]["id"]
        )

    @mock.patch.object(instagram_stats, "fetch_growth_snapshot")
    def test_growth_refresh_records_metrics_and_drafts_requests(self, fetch, tmp_path=None):
        # unittest does not inject tmp_path; use a managed temporary directory.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            store = GrowthStore(Path(directory) / "growth.sqlite3")
            fetch.return_value = {
                "configured": True,
                "captured_at": 5000,
                "media": [{
                    "id": "media-1",
                    "insights": {"views": 20},
                    "public_comments": [{
                        "id": "comment-1",
                        "text": "Please build a Pi sensor next",
                        "username": "viewer",
                    }],
                }],
            }
            result = instagram_stats.refresh_growth_memory(
                store=store, force=True
            )

            self.assertTrue(result["refreshed"])
            self.assertEqual(1, result["new_mission_drafts"])
            self.assertEqual(1, store.report()["viewer_missions_waiting"])


if __name__ == "__main__":
    unittest.main()
