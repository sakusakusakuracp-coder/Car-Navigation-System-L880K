import time
import unittest
from pathlib import Path

from door_lock.driver import DryRunOutputDriver
from door_lock.service import DoorLockConfig, DoorLockService
from door_lock.store import CommandStore


class DoorLockTest(unittest.TestCase):
    def _service(self) -> DoorLockService:
        self.result_path = Path("tests/.door-lock-test-results.json")
        self.result_path.unlink(missing_ok=True)
        self.addCleanup(self.result_path.unlink, missing_ok=True)
        config = DoorLockConfig(enabled=True, lock_gpio=5, unlock_gpio=6, active_level=1, release_level=0, pulse_ms=1, max_pulse_ms=10, cooldown_ms=0, allowed_callers=("test",))
        store = CommandStore(str(self.result_path))
        return DoorLockService(config, DryRunOutputDriver(), store)

    def _request(self, service: DoorLockService, command_id: str = "1") -> dict:
        return service.handle_request({"operation": "LOCK", "caller": "test", "command_id": command_id, "session_id": service.session_id, "expires_at_monotonic_ms": int(time.monotonic() * 1000) + 1000, "speed_mps": 0.0, "speed_validity": "VALID", "speed_age_ms": 0})

    def test_pulse_is_exclusive_and_completes(self) -> None:
        service = self._service()
        result = self._request(service)
        self.assertEqual(result["state"], "PULSE_COMPLETED")
        self.assertEqual(service.driver.active_direction, None)

    def test_duplicate_command_is_not_replayed(self) -> None:
        service = self._service()
        self._request(service)
        result = self._request(service)
        self.assertTrue(result["replayed"])

    def test_unknown_speed_is_rejected(self) -> None:
        service = self._service()
        request = {"operation": "LOCK", "caller": "test", "command_id": "2", "session_id": service.session_id, "expires_at_monotonic_ms": int(time.monotonic() * 1000) + 1000, "speed_mps": 0.0, "speed_validity": "UNKNOWN", "speed_age_ms": 0}
        result = service.handle_request(request)
        self.assertEqual(result["reason"], "NOT_STOPPED")
