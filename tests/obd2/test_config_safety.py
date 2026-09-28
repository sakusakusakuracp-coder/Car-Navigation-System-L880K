import unittest
from pathlib import Path

from obd2.config import ProfileError, _parse_requests, load_profile


class ConfigSafetyTest(unittest.TestCase):
    def test_unconfirmed_profile_is_not_usable(self) -> None:
        path = Path("src/raspberry_pi5/config/obd2.example.toml")
        profile = load_profile(path)
        self.assertFalse(profile.is_usable)

    def test_non_read_request_is_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            _parse_requests(
                [
                    {
                        "id": "clear_dtc",
                        "key": "diagnostic_codes",
                        "operation": "clear",
                        "request": "01 02",
                    }
                ]
            )

    def test_speed_and_coolant_are_minimal_by_default(self) -> None:
        requests = _parse_requests(
            [
                {"id": "speed", "key": "vehicle_speed", "request": "01"},
                {"id": "coolant", "key": "coolant_temperature", "request": "02"},
                {"id": "rpm", "key": "engine_rpm", "request": "03"},
            ]
        )
        self.assertEqual(requests[0].poll_modes, ("minimal", "full"))
        self.assertEqual(requests[1].poll_modes, ("minimal", "full"))
        self.assertEqual(requests[2].poll_modes, ("full",))
