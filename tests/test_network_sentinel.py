import unittest
from unittest import mock

import network_sentinel


class PhoneCurrentlyAwayTests(unittest.TestCase):
    def _set_state(self, **kwargs):
        state = {"present": False, "last_seen": 0.0, "last_missing_since": None}
        state.update(kwargs)
        return mock.patch.object(network_sentinel, "_phone_state", state)

    def test_away_when_configured_seen_before_and_not_present(self):
        with mock.patch.object(
            network_sentinel, "load_phone_mac", return_value="aa:bb:cc:dd:ee:ff"
        ), self._set_state(present=False, last_seen=1_000.0):
            self.assertTrue(network_sentinel.phone_currently_away())

    def test_not_away_when_phone_present(self):
        with mock.patch.object(
            network_sentinel, "load_phone_mac", return_value="aa:bb:cc:dd:ee:ff"
        ), self._set_state(present=True, last_seen=1_000.0):
            self.assertFalse(network_sentinel.phone_currently_away())

    def test_not_away_when_no_phone_configured(self):
        with mock.patch.object(
            network_sentinel, "load_phone_mac", return_value=None
        ), self._set_state(present=False, last_seen=1_000.0):
            self.assertFalse(network_sentinel.phone_currently_away())

    def test_cold_start_never_reads_as_away(self):
        """Before the phone has been seen even once this session, absence
        must not read as 'away' — otherwise a fresh boot would demand a
        face check."""
        with mock.patch.object(
            network_sentinel, "load_phone_mac", return_value="aa:bb:cc:dd:ee:ff"
        ), self._set_state(present=False, last_seen=0.0):
            self.assertFalse(network_sentinel.phone_currently_away())


class PhoneReturnDisarmsGateTests(unittest.TestCase):
    def test_phone_returning_disarms_phone_left_gate(self):
        network_sentinel._phone_state.update(
            {"present": False, "last_seen": 100.0, "last_missing_since": 100.0}
        )
        fake_gate = mock.MagicMock()

        with mock.patch.object(
            network_sentinel, "load_phone_mac", return_value="aa:bb:cc:dd:ee:ff"
        ), mock.patch.dict("sys.modules", {"camera_gate": fake_gate}):
            network_sentinel._update_phone_presence(
                {"aa:bb:cc:dd:ee:ff"}, now=200.0
            )

        fake_gate.disarm_if_reason.assert_called_once_with("phone_left")


if __name__ == "__main__":
    unittest.main()
