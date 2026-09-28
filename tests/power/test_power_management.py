"""GPIOを操作せず、時間と信号を模擬して電源管理の誤操作を検証する。"""

import contextlib
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pi5_power_daemon import Pi5PowerDaemon
import pi5_power_daemon


class FakePin:
    IN, OUT, OPEN_DRAIN, PULL_DOWN = range(4)
    levels = {}
    writes = []

    def __init__(self, gpio, mode, pull=None, value=None):
        self.gpio, self.mode = gpio, mode
        if value is not None:
            self.value(value)

    def value(self, value=None):
        if value is not None:
            self.levels[self.gpio] = value
            self.writes.append((self.gpio, value))
        return self.levels.get(self.gpio, 0)


class PicoTests(unittest.TestCase):
    def setUp(self):
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        path = Path(__file__).resolve().parents[2] / "src/raspberry_pico/pico_power_manager.py"
        spec = importlib.util.spec_from_file_location("pico_under_test", path)
        self.module = importlib.util.module_from_spec(spec)
        FakePin.levels, FakePin.writes = {}, []
        with patch.dict("sys.modules", {"machine": SimpleNamespace(Pin=FakePin)}):
            spec.loader.exec_module(self.module)
        # import自体では実機初期化しない。
        self.assertEqual(FakePin.writes, [])
        self.now = 0
        self.module.time = SimpleNamespace(
            ticks_ms=lambda: self.now,
            ticks_diff=lambda a, b: (a - b + (1 << 29)) % (1 << 30) - (1 << 29),
            sleep_ms=self.sleep,
        )
        self.manager = self.module.PicoPowerManager()

    def tearDown(self):
        self.output.__exit__(None, None, None)

    def sleep(self, ms):
        self.now = (self.now + ms) % (1 << 30)

    def advance(self, ms):
        for _ in range(ms // 50):
            self.sleep(50)
            self.manager.step()

    def inputs(self, acc, status):
        FakePin.levels[17], FakePin.levels[10] = acc, status

    def pulses(self):
        return sum(gpio == 14 and value == 1 for gpio, value in FakePin.writes)

    def running(self):
        self.inputs(1, 1)
        self.advance(2300)
        self.assertEqual(self.manager.state, "RUNNING")

    def test_initial_low_is_unknown_and_does_not_start(self):
        self.inputs(1, 0)
        self.advance(100_000)
        self.assertEqual(self.manager.state, "UNKNOWN")
        self.assertEqual(self.pulses(), 0)

    def test_initial_inputs_are_not_immediately_accepted(self):
        self.inputs(0, 1)
        self.manager.step()
        self.assertIsNone(self.manager.pi_status.value())
        self.assertEqual(self.pulses(), 0)

    def test_service_restart_does_not_generate_start_pulse(self):
        self.running()
        self.inputs(1, 0)
        self.advance(500)
        self.assertEqual(self.manager.state, "UNKNOWN")
        self.inputs(1, 1)
        self.advance(2300)
        self.assertEqual(self.manager.state, "RUNNING")
        self.assertEqual(self.pulses(), 0)

    def test_shutdown_is_sent_once_and_low_is_not_halt(self):
        self.running()
        self.inputs(0, 1)
        self.advance(300)
        self.assertEqual(self.manager.state, "STOPPING")
        self.assertEqual(self.pulses(), 2)
        self.inputs(0, 0)
        self.advance(300)
        self.assertEqual(self.manager.state, "HALT_UNCONFIRMED")
        for acc, status in [(1, 0), (0, 1), (1, 1), (0, 0)]:
            self.inputs(acc, status)
            self.advance(3000)
        self.assertEqual(self.manager.state, "HALT_UNCONFIRMED")
        self.assertEqual(self.pulses(), 2)

    def test_acc_on_during_shutdown_does_not_cancel_operation(self):
        self.running()
        self.inputs(0, 1)
        self.advance(300)
        self.inputs(1, 1)
        self.advance(1000)
        self.assertEqual(self.manager.state, "STOPPING")
        self.inputs(1, 0)
        self.advance(300)
        self.assertEqual(self.manager.state, "HALT_UNCONFIRMED")
        self.assertEqual(self.pulses(), 2)

    def test_shutdown_timeout_survives_acc_changes_and_notification_recovery(self):
        self.running()
        self.inputs(0, 1)
        self.advance(91_000)
        self.assertEqual(self.manager.state, "FAULT")
        self.inputs(1, 0)
        self.advance(1000)
        self.inputs(0, 1)
        self.advance(3000)
        self.assertEqual(self.manager.state, "FAULT")
        self.assertEqual(self.pulses(), 2)

    def test_manual_confirmation_allows_one_boot_then_acc_off_is_processed(self):
        self.inputs(1, 0)
        self.advance(300)
        self.manager.confirm_stopped()
        self.advance(100)
        self.assertEqual(self.pulses(), 1)
        self.assertEqual(self.manager.state, "BOOTING")
        self.inputs(0, 0)
        self.advance(300)
        self.assertEqual(self.manager.state, "BOOTING")
        self.inputs(0, 1)
        self.advance(2400)
        self.assertEqual(self.manager.state, "STOPPING")
        self.assertEqual(self.pulses(), 3)

    def test_boot_timeout_does_not_rearm_on_acc(self):
        self.inputs(1, 0)
        self.advance(300)
        self.manager.confirm_stopped()
        self.advance(91_000)
        self.inputs(0, 0)
        self.advance(300)
        self.inputs(1, 0)
        self.advance(300)
        self.assertEqual(self.manager.state, "FAULT")
        self.assertEqual(self.pulses(), 1)

    def test_confirmation_rejected_while_running_or_input_unsettled(self):
        with self.assertRaises(RuntimeError):
            self.manager.confirm_stopped()
        self.running()
        with self.assertRaises(RuntimeError):
            self.manager.confirm_stopped()

    def test_external_boot_revokes_confirmation_before_debounce(self):
        self.inputs(0, 0)
        self.advance(300)
        self.manager.confirm_stopped()
        self.inputs(1, 1)
        self.manager.step()
        self.assertEqual(self.manager.state, "UNKNOWN")
        self.advance(2300)
        self.assertEqual(self.pulses(), 0)

    def test_noise_does_not_cause_shutdown(self):
        self.running()
        self.inputs(0, 1)
        self.advance(50)
        self.inputs(1, 1)
        self.advance(300)
        self.assertEqual(self.pulses(), 0)

    def test_pulse_releases_even_on_keyboard_interrupt(self):
        self.module.time.sleep_ms = Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.manager.j2_button_line.pulse()
        self.assertEqual(FakePin.levels[14], 0)

    def test_run_failure_releases_and_sets_fault(self):
        self.manager.step = Mock(side_effect=RuntimeError("test"))
        with self.assertRaises(RuntimeError):
            self.manager.run()
        self.assertEqual(self.manager.state, "FAULT")
        self.assertEqual(FakePin.levels[14], 0)
        self.assertEqual(FakePin.levels[25], 0)

    def test_open_drain_polarity(self):
        pulse = self.module.J2ButtonPulse(14, "open_drain_low")
        self.assertEqual(pulse.pin.mode, FakePin.OPEN_DRAIN)
        self.assertEqual(FakePin.levels[14], 1)
        pulse.pulse()
        self.assertEqual(FakePin.writes[-2:], [(14, 0), (14, 1)])

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.module.J2ButtonPulse(14, "wrong")

    def test_time_wrap_does_not_end_boot_early(self):
        self.now = (1 << 30) - 1000
        self.inputs(1, 0)
        self.manager = self.module.PicoPowerManager()
        self.advance(300)
        self.manager.confirm_stopped()
        self.advance(2000)
        self.assertEqual(self.manager.state, "BOOTING")
        self.assertEqual(self.pulses(), 1)


class PiDaemonTests(unittest.TestCase):
    def setUp(self):
        self.pin = Mock()
        self.factory = Mock(return_value=self.pin)
        self.daemon = Pi5PowerDaemon(self.factory)

    def test_signal_request_does_not_close_pin(self):
        self.daemon.request_stop()
        self.pin.off.assert_not_called()
        self.pin.close.assert_not_called()
        self.daemon.run()
        self.pin.on.assert_not_called()
        self.daemon.stop()
        self.pin.close.assert_called_once()

    def test_cleanup_is_idempotent(self):
        self.daemon.stop()
        self.daemon.stop()
        self.pin.off.assert_called_once()
        self.pin.close.assert_called_once()

    def test_off_failure_still_closes_pin(self):
        self.pin.off.side_effect = OSError("test")
        with self.assertRaises(OSError):
            self.daemon.stop()
        self.pin.close.assert_called_once()

    def test_run_sets_high_then_waits_and_cleanup_clears(self):
        self.daemon._stop_requested.wait = Mock(return_value=True)
        self.daemon.run()
        self.pin.on.assert_called_once()
        self.daemon.stop()
        self.pin.off.assert_called_once()

    def test_invalid_gpio_rejected_before_access(self):
        with patch("pi5_power_daemon.STATUS_GPIO", 100):
            with self.assertRaises(ValueError):
                Pi5PowerDaemon(self.factory)
        self.assertEqual(self.factory.call_count, 1)

    def test_main_run_failure_still_cleans_up(self):
        daemon = Mock()
        daemon.run.side_effect = RuntimeError("test")
        with patch.object(pi5_power_daemon, "Pi5PowerDaemon", return_value=daemon):
            with patch.object(pi5_power_daemon.signal, "signal"):
                with self.assertRaises(RuntimeError):
                    pi5_power_daemon.main()
        daemon.stop.assert_called_once()

    def test_main_signal_registration_failure_still_cleans_up(self):
        daemon = Mock()
        with patch.object(pi5_power_daemon, "Pi5PowerDaemon", return_value=daemon):
            with patch.object(pi5_power_daemon.signal, "signal", side_effect=ValueError):
                with self.assertRaises(ValueError):
                    pi5_power_daemon.main()
        daemon.run.assert_not_called()
        daemon.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
