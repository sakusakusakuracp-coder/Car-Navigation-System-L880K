import unittest

from sub1.reverse_signal import ReverseSignalMonitor
from sub2.turn_signal import TurnSignalMonitor
from subcontroller_common import DryRunInputDriver


class SubcontrollerTest(unittest.TestCase):
    def test_reverse_signal_is_unknown_until_debounced(self) -> None:
        driver = DryRunInputDriver(1)
        monitor = ReverseSignalMonitor(driver, gpio=23, active_level=1, debounce_ms=100)
        first = monitor.read()
        self.assertIsNone(first["reverse"])
        monitor.signal.candidate_started_ms -= 100
        second = monitor.read()
        self.assertTrue(second["reverse"])

    def test_turn_signal_separates_lamp_and_activity(self) -> None:
        driver = DryRunInputDriver(1)
        monitor = TurnSignalMonitor(driver, gpio=24, side="left", active_level=1, poll_ms=50, debounce_ms=0, max_read_gap_ms=500, activity_hold_ms=700, max_on_ms=5000)
        state = monitor.read()
        self.assertTrue(state["lamp"])
        self.assertEqual(state["activity"], "ACTIVE")
