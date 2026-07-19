import unittest

import weather_hud


SAMPLE_PAYLOAD = {
    "current": {
        "time": "2026-07-19T14:00",
        "temperature_2m": 72.4,
        "weather_code": 3,
        "relative_humidity_2m": 61,
        "wind_speed_10m": 8.3,
        "precipitation": 0.0,
    },
    "hourly": {
        "time": [
            "2026-07-19T13:00",
            "2026-07-19T14:00",
            "2026-07-19T15:00",
            "2026-07-19T16:00",
            "2026-07-19T17:00",
        ],
        "temperature_2m": [70.0, 72.4, 73.1, 71.9, 69.5],
        "precipitation_probability": [5, 10, 40, 55, 20],
        "weather_code": [1, 3, 61, 63, 3],
    },
    "daily": {
        "time": ["2026-07-19", "2026-07-20", "2026-07-21"],
        "temperature_2m_max": [75.2, 73.8, 70.1],
        "temperature_2m_min": [61.0, 60.4, 58.9],
        "precipitation_probability_max": [55, 80, 10],
        "weather_code": [63, 65, 2],
    },
}


class BuildForecastTests(unittest.TestCase):
    def test_current_conditions_are_rounded_and_labelled(self):
        result = weather_hud._build_forecast(SAMPLE_PAYLOAD, "Oceanside", "http://radar")

        self.assertEqual(result["city"], "Oceanside")
        self.assertEqual(result["current"]["temp_f"], 72)
        self.assertEqual(result["current"]["humidity"], 61)
        self.assertEqual(result["current"]["wind_mph"], 8)
        self.assertEqual(result["current"]["condition"], "overcast")
        self.assertFalse(result["stale"])
        self.assertEqual(result["radar_loop_url"], "http://radar")

    def test_hourly_starts_at_current_hour_and_is_bounded(self):
        result = weather_hud._build_forecast(SAMPLE_PAYLOAD, "Oceanside", "http://radar")

        hourly = result["hourly"]
        # Slice starts at the current hour (14:00), never before it.
        self.assertEqual(hourly[0]["precip_chance"], 10)
        self.assertEqual(hourly[0]["temp_f"], 72)
        self.assertLessEqual(len(hourly), weather_hud.HOURLY_HOURS)
        self.assertEqual(hourly[1]["label"], "3 PM")

    def test_daily_forecast_has_weekday_and_range(self):
        result = weather_hud._build_forecast(SAMPLE_PAYLOAD, "Oceanside", "http://radar")

        first = result["daily"][0]
        self.assertEqual(first["high_f"], 75)
        self.assertEqual(first["low_f"], 61)
        self.assertEqual(first["precip_chance"], 55)
        self.assertEqual(first["condition"], "moderate rain")
        # 2026-07-19 is a Sunday.
        self.assertEqual(first["label"], "Sun")

    def test_incomplete_payload_raises(self):
        with self.assertRaises(ValueError):
            weather_hud._build_forecast({"current": {}, "hourly": {}, "daily": {}},
                                        "Oceanside", "http://radar")


class GetForecastCacheTests(unittest.TestCase):
    def setUp(self):
        weather_hud._forecast_cache["data"] = None
        weather_hud._forecast_cache["fetched_at"] = 0.0

    def test_offline_with_no_cache_returns_stale_placeholder(self):
        def boom():
            raise weather_hud.requests.RequestException("no network")

        original = weather_hud._fetch_forecast
        weather_hud._fetch_forecast = boom
        try:
            result = weather_hud.get_weather_forecast()
        finally:
            weather_hud._fetch_forecast = original

        self.assertTrue(result["stale"])
        self.assertEqual(result["current"]["condition"], "unavailable")
        self.assertEqual(result["hourly"], [])
        self.assertEqual(result["daily"], [])

    def test_offline_falls_back_to_stale_cache(self):
        weather_hud._forecast_cache["data"] = {
            "city": "Oceanside",
            "current": {"temp_f": 70, "condition": "clear sky"},
            "hourly": [],
            "daily": [],
            "radar_loop_url": "http://radar",
            "stale": False,
        }
        weather_hud._forecast_cache["fetched_at"] = 0.0  # force refresh

        def boom():
            raise weather_hud.requests.RequestException("no network")

        original = weather_hud._fetch_forecast
        weather_hud._fetch_forecast = boom
        try:
            result = weather_hud.get_weather_forecast()
        finally:
            weather_hud._fetch_forecast = original

        self.assertTrue(result["stale"])
        self.assertEqual(result["current"]["temp_f"], 70)


if __name__ == "__main__":
    unittest.main()
