import threading
import time
import unittest

import mic_arbiter


class MicArbiterTests(unittest.TestCase):
    def tearDown(self):
        mic_arbiter.reset()

    def test_request_yield_returns_true_when_released_promptly(self):
        def confirm_soon():
            time.sleep(0.05)
            mic_arbiter.confirm_released()

        threading.Thread(target=confirm_soon, daemon=True).start()

        self.assertTrue(mic_arbiter.request_yield(timeout=2))
        self.assertTrue(mic_arbiter.yield_is_requested())

    def test_request_yield_fails_open_on_timeout(self):
        self.assertFalse(mic_arbiter.request_yield(timeout=0.05))
        # Still marked requested -- caller proceeds anyway, but a late
        # listener should still see the request and cooperate.
        self.assertTrue(mic_arbiter.yield_is_requested())

    def test_resume_clears_the_request(self):
        mic_arbiter.request_yield(timeout=0.01)
        mic_arbiter.resume()

        self.assertFalse(mic_arbiter.yield_is_requested())

    def test_reset_clears_all_state(self):
        mic_arbiter.request_yield(timeout=0.01)
        mic_arbiter.confirm_released()

        mic_arbiter.reset()

        self.assertFalse(mic_arbiter.yield_is_requested())


if __name__ == "__main__":
    unittest.main()
