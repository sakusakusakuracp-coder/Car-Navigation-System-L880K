import unittest

from ui.controllers.event_ordering import OrderedEventGate


class OrderedEventGateTest(unittest.TestCase):
    def test_rejects_duplicate_and_old_sequence(self) -> None:
        gate = OrderedEventGate()
        self.assertTrue(gate.accept({"event": "vehicle.status", "schema_version": 1, "boot_id": "boot-a", "sequence": 1}))
        self.assertFalse(gate.accept({"event": "vehicle.update", "schema_version": 1, "boot_id": "boot-a", "sequence": 1}))
        self.assertFalse(gate.accept({"event": "vehicle.update", "schema_version": 1, "boot_id": "boot-a", "sequence": 0}))
        self.assertTrue(gate.accept({"event": "vehicle.update", "schema_version": 1, "boot_id": "boot-a", "sequence": 2}))

    def test_accepts_new_boot_and_rejects_retired_boot(self) -> None:
        gate = OrderedEventGate()
        self.assertTrue(gate.accept({"event": "vehicle.status", "schema_version": 1, "boot_id": "boot-a", "sequence": 10}))
        self.assertTrue(gate.accept({"event": "vehicle.status", "schema_version": 1, "boot_id": "boot-b", "sequence": 1}))
        self.assertFalse(gate.accept({"event": "vehicle.status", "schema_version": 1, "boot_id": "boot-a", "sequence": 11}))

    def test_legacy_event_without_metadata_is_allowed(self) -> None:
        gate = OrderedEventGate()
        self.assertTrue(gate.accept({"obd2_status": "未接続"}))
        self.assertFalse(gate.accept({"boot_id": "boot-a"}))

    def test_rejects_missing_or_unsupported_schema(self) -> None:
        gate = OrderedEventGate()
        self.assertFalse(gate.accept({"event": "vehicle.status", "boot_id": "boot-a", "sequence": 1}))
        self.assertFalse(gate.accept({"event": "vehicle.status", "schema_version": 2, "boot_id": "boot-a", "sequence": 1}))
        self.assertTrue(gate.accept({"event": "vehicle.status", "schema_version": 1, "boot_id": "boot-a", "sequence": 1}))
