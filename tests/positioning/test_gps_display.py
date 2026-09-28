"""測位表示が古い通知や接続だけで正常へ戻らないことを検証する。"""

import unittest
from unittest.mock import patch

from position_correction_service import PositionCorrectionService
from positioning.config import PositioningConfig
from positioning.publisher import PositionPublisher
from ui.controllers.event_ordering import OrderedEventGate
from ui.state.gps_status import GpsStatus


def position(**changes):
    return {"event": "position.update", "schema_version": 1, "boot_id": "boot-a", "sequence": 1,
            "issued_monotonic": 100.0, "method": "GPS", "publishable": True,
            "validity": "VALID", "valid_for_ms": 2000, **changes}


class GpsDisplayTest(unittest.TestCase):
    def setUp(self):
        self.state = GpsStatus()
        self.connect()

    def connect(self, now=100):
        self.state.apply({"gps_service_status": "現在地補正サービス接続済み"}, now=now)

    def test_connection_is_not_a_fix_and_silent_service_expires(self):
        self.assertEqual(self.state.label, "確認中")
        self.assertFalse(self.state.expire(now=104.99))
        self.assertTrue(self.state.expire(now=105))
        self.assertEqual(self.state.label, "更新停止")

    def test_receive_delay_is_subtracted_without_double_subtracting_observation_age(self):
        self.state.apply(position(age_ms_at_send=700), now=100.5)
        self.assertEqual(self.state.label, "測位中")
        self.assertFalse(self.state.expire(now=101.99))
        self.assertTrue(self.state.expire(now=102))
        self.assertEqual(self.state.label, "更新停止")

    def test_disconnect_and_reconnect_never_restore_previous_fix(self):
        self.state.apply(position(), now=100)
        self.state.apply({"gps_service_status": "現在地補正サービス未接続"}, now=100.2)
        self.assertEqual(self.state.label, "サービス未接続")
        self.state.apply(position(sequence=2), now=100.3)
        self.assertEqual(self.state.label, "サービス未接続")
        self.connect(now=100.4)
        self.assertEqual(self.state.label, "確認中")

    def test_initial_wait_loss_correction_and_recovery(self):
        status = position(event="position.status", state="NO_FIX", has_fix_history=False)
        self.state.apply(status, now=100)
        self.assertEqual(self.state.label, "測位待ち")
        self.state.apply(position(), now=100)
        self.state.apply(position(event="position.invalidate", has_fix_history=True), now=100)
        self.assertEqual(self.state.label, "信号喪失")
        self.state.apply(position(method="DEAD_RECKONING", validity="DEGRADED", display_valid_for_ms=500), now=100)
        self.assertEqual(self.state.label, "補正中")
        self.assertIn("センサー", self.state.detail)
        self.state.expire(now=100.5)
        self.assertEqual(self.state.label, "更新停止")
        self.state.apply(position(issued_monotonic=101), now=101)
        self.assertEqual(self.state.label, "測位中")

    def test_service_restart_clears_fix_history(self):
        self.state.apply(position(), now=100)
        self.state.apply(position(event="position.status", boot_id="boot-b", state="NO_FIX"), now=100)
        self.assertEqual(self.state.label, "測位待ち")

    def test_invalid_position_never_displays_healthy(self):
        for changes in ({"method": "UNRECOGNIZED"}, {"publishable": False}, {"validity": "STALE"},
                        {"valid_for_ms": float("nan")}, {"valid_for_ms": True}, {"valid_for_ms": -1},
                        {"issued_monotonic": 101}, {"issued_monotonic": None}):
            with self.subTest(changes=changes):
                self.state.apply(position(**changes), now=100)
                self.assertEqual(self.state.label, "更新停止")

    def test_heartbeat_keeps_waiting_fresh_but_cannot_renew_position(self):
        self.state.apply(position(), now=100)
        self.state.apply(position(event="position.status", state="GPS_ACTIVE", issued_monotonic=101,
                                  position_valid_for_ms=1000, has_fix_history=True), now=101)
        self.assertTrue(self.state.expire(now=102))
        self.state.apply(position(event="position.status", state="NO_FIX", issued_monotonic=103,
                                  has_fix_history=True), now=103)
        self.assertEqual(self.state.label, "信号喪失")
        self.assertFalse(self.state.expire(now=107.9))
        self.assertTrue(self.state.expire(now=108))

    def test_schema_sequence_and_service_stream_are_independent(self):
        gate = OrderedEventGate()
        self.assertTrue(gate.accept({"event": "status", "schema_version": 1, "boot_id": "nav", "sequence": 100}))
        self.assertTrue(gate.accept(position()))
        self.assertFalse(gate.accept(position()))
        self.assertFalse(gate.accept(position(sequence=2, schema_version=2)))
        self.assertFalse(gate.accept({"event": "position.status", "state": "GPS_ACTIVE"}))
        self.assertTrue(gate.accept(position(boot_id="boot-b")))
        self.assertFalse(gate.accept(position(sequence=3)))


class PositionDisplaySourceTest(unittest.TestCase):
    def setUp(self):
        self.service = PositionCorrectionService(PositioningConfig(gpsd_enabled=False))
        self.events = []
        self.service.publisher = PositionPublisher("03 現在地補正", self.events.append)

    def test_internal_dr_flag_without_accepted_position_does_not_report_correction(self):
        self.service.estimator.state.dr_active = True
        self.assertEqual(self.service.status()["state"], "NO_FIX")

    def test_snapshot_sequence_is_new_and_lifetime_counts_down(self):
        self.service._display_position = {"method": "GPS"}
        self.service._display_deadline = 102
        with patch("position_correction_service.time.monotonic", return_value=100):
            first = self.service.status()
        with patch("position_correction_service.time.monotonic", return_value=101):
            second = self.service.status()
        self.assertGreater(second["sequence"], first["sequence"])
        self.assertEqual(second["position_valid_for_ms"], 1000)
        with patch("position_correction_service.time.monotonic", return_value=102):
            self.assertEqual(self.service.status()["state"], "NO_FIX")

    def test_invalidation_clears_cached_position_even_if_estimator_still_has_dr_flag(self):
        self.service._display_position = {"method": "DEAD_RECKONING"}
        self.service._display_deadline = float("inf")
        self.service.estimator.state.dr_active = True
        self.service._publish_invalidate("推定誤差の上限")
        self.assertEqual(self.service.status()["state"], "NO_FIX")
        self.assertEqual(self.events[-1]["event"], "position.invalidate")

    def test_real_gps_only_input_reaches_display_then_expires(self):
        # 実際の入力検証・整列・推定・品質確認を通し、IMUなしの表示を確認する。
        with patch("time.monotonic", return_value=100):
            self.service.submit_observation({"kind": "gps", "source": "gpsd", "source_id": "gps0",
                "boot_id": "gps-boot", "sequence": 1, "measurement_id": "fix-1",
                "observed_monotonic": 99.8, "valid_for_ms": 2000, "validity": "VALID",
                "values": {"latitude_deg": 35.0, "longitude_deg": 139.0,
                           "horizontal_accuracy_m": 5.0, "mode": 3}})
            self.service._process_ready(flush=True)
            status = self.service.status()
        self.assertEqual(status["state"], "GPS_ACTIVE")
        ui = GpsStatus()
        ui.apply({"gps_service_status": "現在地補正サービス接続済み"}, now=100)
        ui.apply(status, now=100)
        self.assertEqual(ui.label, "測位中")
        with patch("time.monotonic", return_value=103):
            self.service._process_ready(flush=True)
            status = self.service.status()
        ui.apply(status, now=103)
        self.assertEqual(ui.label, "信号喪失")


if __name__ == "__main__":
    unittest.main()
