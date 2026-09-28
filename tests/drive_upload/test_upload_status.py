"""UIへ届いた送信状態の鮮度と、他サービス通知との分離を検証する。"""

import unittest

from ui.controllers.event_ordering import OrderedEventGate
from ui.state.upload_status import UploadStatus


class UploadStatusTest(unittest.TestCase):
    def event(self, **values):
        return {"event": "upload.status", "source_service": "07 Google Driveアップロード・削除",
                "schema_version": 1, "boot_id": "one", "sequence": 1, "state": "UPLOADING",
                "age_ms": 1000, "valid_for_ms": 5000, "counts": {"READY": 2, "UPLOADING": 1, "DELETED": 20}, **values}

    def test_pending_counts_and_expiration(self):
        status = UploadStatus()
        status.apply(self.event(), now=100)
        self.assertEqual(status.label, "送信中")
        self.assertEqual(status.pending, 3)
        self.assertFalse(status.expire(now=103))
        self.assertTrue(status.expire(now=104))
        self.assertEqual(status.label, "状態確認待ち")

    def test_old_snapshot_and_disconnect_never_show_success(self):
        status = UploadStatus()
        status.apply(self.event(age_ms=6000, state="DELETED"))
        self.assertEqual(status.label, "状態確認待ち")
        status.apply({"upload_service_status": "DISCONNECTED"})
        self.assertEqual(status.label, "サービス未接続")

    def test_invalid_age_and_counts_rejected(self):
        for update in ({"age_ms": float("nan")}, {"counts": {"READY": "3"}}, {"valid_for_ms": True}):
            status = UploadStatus()
            status.apply(self.event(**update))
            self.assertEqual(status.label, "状態確認待ち")

    def test_sequence_validation_uses_separate_stream(self):
        gate = OrderedEventGate()
        self.assertTrue(gate.accept(self.event()))
        self.assertFalse(gate.accept(self.event()))
        self.assertFalse(gate.accept(self.event(sequence=2, schema_version=2)))
        self.assertTrue(gate.accept(self.event(sequence=2)))


if __name__ == "__main__":
    unittest.main()
