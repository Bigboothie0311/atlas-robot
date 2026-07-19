import unittest

import listen_and_answer as la


def norm(text):
    return la._normalize_phrase(text)


class WeatherHudIntentTests(unittest.TestCase):
    def test_radar_and_show_verbs_open(self):
        for phrase in [
            "pull up the weather radar",
            "can you pull up the radar",
            "hey can you pull the radar up",
            "show me the radar",
            "show me the weather radar",
            "bring up the weather",
            "weather hud",
            "open the weather screen",
            "give me the live radar",
            "let me see the radar",
            "full screen weather",
            "show me the weather",
        ]:
            self.assertEqual(la._wants_weather_hud(norm(phrase)), "open", phrase)

    def test_close_verbs_close(self):
        for phrase in [
            "close the weather",
            "hide the radar",
            "close the weather screen",
            "get rid of the radar",
        ]:
            self.assertEqual(la._wants_weather_hud(norm(phrase)), "close", phrase)

    def test_weather_questions_do_not_open_hud(self):
        # These must fall through to the get_weather tool, not the HUD.
        for phrase in [
            "what's the weather",
            "will it rain tomorrow",
            "how's the weather today",
            "is it going to rain",
        ]:
            self.assertIsNone(la._wants_weather_hud(norm(phrase)), phrase)


class BrightnessIntentTests(unittest.TestCase):
    def test_boost_phrasings(self):
        for phrase in [
            "raise the brightness",
            "brighten the screen",
            "turn the brightness up",
            "make the screen brighter",
            "can you raise the brightness on the hud",
            "brightness",
            "full brightness",
            "max brightness",
        ]:
            self.assertEqual(la._wants_brightness_change(norm(phrase)), "boost", phrase)

    def test_normal_phrasings(self):
        for phrase in [
            "normal brightness",
            "lower the brightness",
            "turn the brightness down",
            "dim it back down",
        ]:
            self.assertEqual(la._wants_brightness_change(norm(phrase)), "normal", phrase)

    def test_unrelated_phrases_ignored(self):
        for phrase in ["go dark", "lights up", "what's on my network", "wake my pc"]:
            self.assertIsNone(la._wants_brightness_change(norm(phrase)), phrase)


if __name__ == "__main__":
    unittest.main()
