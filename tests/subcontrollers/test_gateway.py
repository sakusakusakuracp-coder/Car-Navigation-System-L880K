import unittest

from subcontroller_gateway_service import SubcontrollerGateway


class GatewayTest(unittest.TestCase):
    def test_rejects_old_sequence_and_new_boot_is_allowed(self) -> None:
        gateway = SubcontrollerGateway("127.0.0.1", 45050)
        base = {"schema_version": "1.0", "source": "15 サブコントローラ1後方系", "boot_id": "boot-a", "sequence": 2, "event": "sub1.status"}
        self.assertTrue(gateway.accept(base)["accepted"])
        old = dict(base, sequence=1)
        self.assertEqual(gateway.accept(old)["reason"], "STALE_EVENT")
        new_boot = dict(base, boot_id="boot-b", sequence=1)
        self.assertTrue(gateway.accept(new_boot)["accepted"])

    def test_rejects_unknown_source_and_schema(self) -> None:
        gateway = SubcontrollerGateway("127.0.0.1", 45050)
        message = {"schema_version": "1.0", "source": "unknown", "boot_id": "boot", "sequence": 1}
        self.assertEqual(gateway.accept(message)["reason"], "UNAUTHORIZED_SOURCE")
        message["source"] = "16 サブコントローラ2前方系"
        message["schema_version"] = "2.0"
        self.assertEqual(gateway.accept(message)["reason"], "SCHEMA_VERSION_UNSUPPORTED")
