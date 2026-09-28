import unittest

from obd2.capabilities import CapabilityProbe
from obd2.models import InitStep, RequestDefinition, SerialIdentity, VehicleProfile, SupportState
from obd2.scheduler import PollScheduler


class _Session:
    def send_query(self, _request_id):
        raise AssertionError("probeはこのテストでは呼ばない")


class SchedulerTest(unittest.TestCase):
    def test_priority_order(self) -> None:
        first = RequestDefinition("speed", "vehicle_speed", b"\x01", priority=1, poll_modes=("minimal", "full"))
        second = RequestDefinition("rpm", "engine_rpm", b"\x02", priority=10)
        profile = VehicleProfile(
            "test", "1", "confirmed", True, "/dev/null", SerialIdentity(port="/dev/null"),
            38400, 10400, (InitStep("wait", wait_ms=1),), (first, second)
        )
        capabilities = CapabilityProbe(_Session(), profile)
        capabilities.probe_capabilities()
        scheduler = PollScheduler(profile.requests, capabilities)
        self.assertEqual(scheduler.select_next_query().request_id, "speed")
        self.assertEqual(capabilities.results["speed"].state, SupportState.SUPPORTED)
