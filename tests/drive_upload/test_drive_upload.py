"""送信中断・結果不明・削除条件を実Driveへ接続せず障害注入で検査する。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from drive_upload.catalog_reader import CatalogReader
from drive_upload.config import Paused, UploadConfig, UploadError
from drive_upload.google_drive import GoogleDriveClient
from drive_upload.local_files import relative_parts
from drive_upload.network_policy import NetworkPolicy, check_google_url
from drive_upload.recovery import RecoveryWorker, retry_blocked
from drive_upload_service import main


class FakeDrive:
    def __init__(self):
        self.generated = 0
        self.started = 0
        self.received = bytearray()
        self.remote = None
        self.row = None
        self.folder = "folder"
        self.expired = False
        self.lost_completion = False
        self.fail_chunk_once = False
        self.send_offsets = []
        self.metadata_fault = None

    def check_destination(self, account, folder):
        if (account, folder) != ("account", self.folder):
            raise UploadError("GOOGLE_ACCOUNT_CHANGED")

    def generate_id(self):
        self.generated += 1
        return "remote-1"

    def metadata(self, remote_id):
        if self.metadata_fault:
            raise self.metadata_fault
        return self.remote

    def start_upload(self, row, folder):
        self.started += 1
        self.row = row
        self.received.clear()
        return "https://www.googleapis.com/upload/drive/v3/files?upload_id=secret"

    def resume_upload(self, uri, total):
        if self.expired:
            self.expired = False
            raise UploadError("UPLOAD_SESSION_EXPIRED", retryable=True)
        return self.remote is not None, len(self.received)

    def send_chunk(self, uri, data, offset, total):
        self.send_offsets.append(offset)
        assert offset == len(self.received)
        self.received.extend(data)
        if self.fail_chunk_once:
            self.fail_chunk_once = False
            raise UploadError("NETWORK_ERROR", retryable=True)
        complete = len(self.received) == total
        if complete:
            self.remote = {"id": "remote-1", "parents": [self.folder], "trashed": False,
                           "appProperties": {"file_id": self.row["file_id"]}, "size": str(total),
                           "md5Checksum": hashlib.md5(self.received, usedforsecurity=False).hexdigest(), "version": "1"}
            if self.lost_completion:
                self.lost_completion = False
                raise UploadError("NETWORK_ERROR", retryable=True)
        return complete, len(self.received)


class TestUpload(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "recordings"
        self.root.mkdir(mode=0o700)
        self.video = self.root / "front.mp4"
        self.video.write_bytes(b"test-video-data" * 30000)
        self.catalog = CatalogReader(self.base / "state" / "catalog.sqlite3", self.root)
        self.file_id = self.catalog.register_ready("front.mp4", "test")
        self.config = UploadConfig(self.root, self.catalog.path, self.base / "token.json", enabled=True,
                                   account_ref="account", folder_id="folder", chunk_bytes=262144,
                                   allowed_profile_ids=("12345678-1234-1234-1234-123456789abc",))
        self.client = FakeDrive()
        self.policy = Mock()
        self.worker = RecoveryWorker(self.config, self.catalog, self.client, self.policy)

    def row(self):
        return self.catalog.get(self.file_id)

    def due_now(self):
        self.catalog.update(self.row(), retry_after=0)

    def test_verified_does_not_delete_by_default(self):
        self.worker.tick()
        self.assertEqual(self.row()["state"], "VERIFIED")
        self.assertEqual(self.row()["acknowledged_bytes"], self.video.stat().st_size)
        self.assertTrue(self.video.exists())
        self.worker.tick()
        self.assertEqual(self.client.generated, 1)

    def test_restart_resumes_server_offset_after_lost_chunk_response(self):
        self.client.fail_chunk_once = True
        self.worker.tick()
        self.assertEqual(self.row()["state"], "RETRY_WAIT")
        self.assertEqual(self.row()["acknowledged_bytes"], 0)
        self.due_now()
        restarted_catalog = CatalogReader(self.catalog.path, self.root)
        restarted_catalog.recover_pending()
        RecoveryWorker(self.config, restarted_catalog, self.client, self.policy).tick()
        self.assertEqual(self.client.send_offsets, [0, 262144])
        self.assertEqual(self.row()["state"], "VERIFIED")
        self.assertEqual(self.client.started, 1)

    def test_lost_completion_is_reconciled_without_duplicate(self):
        self.client.lost_completion = True
        self.worker.tick()
        self.assertEqual(self.row()["state"], "RETRY_WAIT")
        self.due_now()
        self.worker.tick()
        self.assertEqual(self.row()["state"], "VERIFIED")
        self.assertEqual(self.client.generated, 1)
        self.assertEqual(self.client.started, 1)

    def test_expired_session_keeps_planned_id(self):
        self.client.fail_chunk_once = True
        self.worker.tick()
        self.client.expired = True
        self.due_now()
        self.worker.tick()
        self.assertIsNone(self.row()["session_uri"])
        self.due_now()
        self.worker.tick()
        self.assertEqual(self.row()["state"], "VERIFIED")
        self.assertEqual(self.client.generated, 1)
        self.assertEqual(self.client.started, 2)

    def test_changed_local_file_is_blocked_and_never_sent(self):
        self.video.write_bytes(b"changed")
        self.worker.tick()
        self.assertEqual(self.row()["state"], "BLOCKED")
        self.assertEqual(self.client.started, 0)
        self.assertTrue(self.video.exists())
        with self.assertRaises(UploadError):
            retry_blocked(self.catalog, self.file_id)

    def test_wrong_remote_hash_never_verified(self):
        self.client.lost_completion = True
        self.worker.tick()
        self.client.remote["md5Checksum"] = "bad"
        self.due_now()
        self.worker.tick()
        self.assertEqual(self.row()["state"], "BLOCKED")
        self.assertIsNone(self.row()["verification_evidence"])
        self.assertTrue(self.video.exists())

    def test_wrong_parent_trashed_or_missing_checksum_blocks(self):
        self.worker.tick()
        for changes in ({"parents": ["other"]}, {"trashed": True}, {"md5Checksum": None}, {"size": "bad"}, {"appProperties": {}}):
            data = {**self.client.remote, **changes}
            with self.subTest(changes=changes), self.assertRaises(UploadError):
                self.worker.verifier.validate_metadata(self.row(), data)

    def test_budget_denial_preserves_local_file_and_session(self):
        self.policy.reserve_chunk.side_effect = Paused("DAILY_BUDGET_EXHAUSTED")
        result = self.worker.tick()
        self.assertEqual(result["state"], "WAITING")
        self.assertEqual(self.row()["retry_count"], 0)
        self.assertTrue(self.row()["session_uri"])
        self.assertTrue(self.video.exists())

    def test_quota_failure_blocks_until_explicit_retry(self):
        self.client.metadata_fault = UploadError("DRIVE_STORAGE_FULL")
        self.worker.tick()
        self.assertEqual(self.row()["state"], "BLOCKED")
        retry_blocked(self.catalog, self.file_id)
        self.client.metadata_fault = None
        self.worker.tick()
        self.assertEqual(self.row()["state"], "VERIFIED")

    def test_stop_prevents_new_work(self):
        self.policy.check_control.side_effect = Paused("STOPPING")
        self.worker.tick()
        self.assertEqual(self.row()["state"], "READY")
        self.assertEqual(self.client.started, 0)

    def test_registration_is_idempotent_and_rejects_path_reuse(self):
        self.assertEqual(self.catalog.register_ready("front.mp4", "test"), self.file_id)
        self.video.write_bytes(b"different")
        with self.assertRaises(UploadError):
            self.catalog.register_ready("front.mp4", "test")

    def test_busy_recorder_lock_waits_instead_of_crashing(self):
        with patch("drive_upload.catalog_reader.ProcessLock.acquire", side_effect=RuntimeError("busy")), self.assertRaises(Paused) as caught:
            self.catalog.register_ready("front.mp4", "test")
        self.assertEqual(caught.exception.reason, "RECORDING_CATALOG_BUSY")
        self.assertEqual(self.row()["state"], "READY")

    def test_stale_catalog_update_is_rejected(self):
        old = self.row()
        self.catalog.update(old, state="UPLOADING")
        with self.assertRaises(UploadError):
            self.catalog.update(old, state="VERIFIED")

    def test_destination_change_rejected(self):
        self.catalog.bind_destination("account", "folder")
        with self.assertRaises(UploadError):
            self.catalog.bind_destination("different-account", "folder")

    def test_daily_budget_reservation_counts_retries(self):
        self.assertTrue(self.catalog.reserve_bytes(200, 300))
        self.assertFalse(self.catalog.reserve_bytes(200, 300))
        self.assertTrue(self.catalog.reserve_bytes(100, 300))

    def test_dry_run_never_creates_remote_or_changes_state(self):
        path = self.base / "config.json"
        path.write_text(json.dumps({"recording_root": str(self.root), "catalog_path": str(self.catalog.path),
                                   "credential_path": str(self.base / "missing-token.json")}), encoding="utf-8")
        before = self.row()
        with patch("builtins.print"), patch("drive_upload_service.authorized_session") as auth:
            self.assertEqual(main(["--config", str(path), "run", "--dry-run"]), 0)
        auth.assert_not_called()
        self.assertEqual(self.row(), before)

    def test_retry_limit_blocks_without_deleting(self):
        self.client.metadata_fault = UploadError("NETWORK_ERROR", retryable=True)
        worker = RecoveryWorker(replace(self.config, max_retries=1), self.catalog, self.client, self.policy)
        worker.tick()
        self.due_now()
        worker.tick()
        self.assertEqual(self.row()["state"], "BLOCKED")
        self.assertTrue(self.video.exists())

    def test_status_has_version_order_and_no_secrets(self):
        self.client.fail_chunk_once = True
        first = self.worker.tick()
        second = self.worker.publish_status()
        self.assertEqual(first["schema_version"], 1)
        self.assertGreater(second["sequence"], first["sequence"])
        self.assertNotIn("upload_id", json.dumps(second))

    @unittest.skipUnless(os.name == "posix", "Linuxのdir_fd削除はLinuxで検証")
    def test_verified_delete_and_crash_after_unlink_recovery(self):
        config = replace(self.config, auto_delete_enabled=True, exclusive_recording_root=True)
        worker = RecoveryWorker(config, self.catalog, self.client, self.policy)
        original_update = self.catalog.update

        def update(row, **values):
            if values.get("state") == "DELETED":
                raise RuntimeError("simulated power loss")
            return original_update(row, **values)

        with patch.object(self.catalog, "update", side_effect=update), self.assertRaises(RuntimeError):
            worker.tick()
        self.assertFalse(self.video.exists())
        self.assertEqual(self.row()["state"], "DELETING")
        worker.tick()
        self.assertEqual(self.row()["state"], "DELETED")

    @unittest.skipUnless(os.name == "posix", "Linuxのリンク検査")
    def test_symlink_and_hardlink_rejected(self):
        (self.root / "link.mp4").symlink_to(self.video)
        os.link(self.video, self.root / "hard.mp4")
        for path in ("link.mp4", "hard.mp4"):
            with self.subTest(path=path), self.assertRaises(UploadError):
                self.catalog.register_ready(path, "test")

    @unittest.skipUnless(os.name == "posix", "Linuxの削除前再検査")
    def test_remote_changed_before_delete_preserves_local(self):
        self.worker.tick()
        self.client.remote["trashed"] = True
        config = replace(self.config, auto_delete_enabled=True, exclusive_recording_root=True)
        RecoveryWorker(config, self.catalog, self.client, self.policy).tick()
        self.assertTrue(self.video.exists())
        self.assertEqual(self.row()["state"], "BLOCKED")


class TestDriveProtocol(unittest.TestCase):
    def response(self, status, data=None, headers=None):
        response = Mock(status_code=status, headers=headers or {})
        response.json.return_value = data or {}
        return response

    def test_308_range_is_authoritative(self):
        session = Mock()
        session.request.return_value = self.response(308, headers={"Range": "bytes=0-42"})
        client = GoogleDriveClient(session)
        self.assertEqual(client.resume_upload("https://www.googleapis.com/upload/id", 100), (False, 43))
        self.assertEqual(session.request.call_args.kwargs["headers"]["Content-Range"], "bytes */100")
        self.assertFalse(session.request.call_args.kwargs["allow_redirects"])
        session.request.return_value = self.response(308)
        self.assertEqual(client.resume_upload("https://www.googleapis.com/upload/id", 100), (False, 0))

    def test_range_outside_file_and_nonzero_start_rejected(self):
        client = GoogleDriveClient(Mock())
        for raw in ("bytes=1-42", "bytes=0-200", "invalid"):
            with self.subTest(raw=raw), self.assertRaises(UploadError):
                client._progress(self.response(308, headers={"Range": raw}), 100)

    def test_errors_do_not_leak_response_or_session_uri(self):
        client = GoogleDriveClient(Mock())
        for status, reason, retryable in ((429, "DRIVE_RATE_LIMIT", True), (500, "DRIVE_TEMPORARY_ERROR", True), (401, "AUTHENTICATION_REQUIRED", False)):
            with self.subTest(status=status), self.assertRaises(UploadError) as caught:
                client._error(self.response(status, {"secret": "must-not-leak"}, {"Retry-After": "30"}))
            self.assertEqual(str(caught.exception), reason)
            self.assertEqual(caught.exception.retryable, retryable)

    def test_arbitrary_session_url_and_unsafe_paths_rejected(self):
        for uri in ("http://www.googleapis.com/", "https://example.com/", "https://www.googleapis.com.example.com/", "https://user@www.googleapis.com/"):
            with self.subTest(uri=uri), self.assertRaises(UploadError):
                check_google_url(uri)
        for path in ("../outside.mp4", "/absolute.mp4", "file.mp4.partial", "a\\b.mp4", "C:/a.mp4"):
            with self.subTest(path=path), self.assertRaises(UploadError):
                relative_parts(path)

    def test_network_checks_profile_of_actual_route(self):
        config = UploadConfig(Path("record"), Path("catalog"), Path("token"), allowed_profile_ids=("approved-uuid",))
        catalog = Mock()
        catalog.is_paused.return_value = False
        policy = NetworkPolicy(config, catalog, threading.Event())
        address = [(None, None, None, None, ("192.0.2.1", 443))]
        with patch("socket.getaddrinfo", return_value=address), patch.object(policy, "_run", side_effect=['[{"dev":"wlan0"}]', "other-uuid"]), self.assertRaises(Paused):
            policy.before_request("https://www.googleapis.com/")
        with patch("socket.getaddrinfo", return_value=address), patch.object(policy, "_run", side_effect=['[{"dev":"wlan0"}]', "approved-uuid"]):
            policy.before_request("https://www.googleapis.com/")

    def test_unreachable_route_is_distinguished_from_inspection_failure(self):
        unavailable = subprocess.CompletedProcess([], 2, "", "RTNETLINK answers: Network is unreachable\n")
        with patch("subprocess.run", return_value=unavailable):
            self.assertEqual(NetworkPolicy._run(["ip", "-j", "route", "get", "::1"], allow_unreachable=True), "[]")
        denied = subprocess.CompletedProcess([], 2, "", "permission denied")
        with patch("subprocess.run", return_value=denied), self.assertRaises(Paused):
            NetworkPolicy._run(["ip", "-j", "route", "get", "::1"], allow_unreachable=True)

    def test_all_unreachable_addresses_wait_offline(self):
        config = UploadConfig(Path("record"), Path("catalog"), Path("token"))
        catalog = Mock()
        catalog.is_paused.return_value = False
        policy = NetworkPolicy(config, catalog, threading.Event())
        addresses = [(None, None, None, None, ("2001:db8::1", 443))]
        with patch("socket.getaddrinfo", return_value=addresses), patch.object(policy, "_run", return_value="[]"), self.assertRaises(Paused) as caught:
            policy.before_request("https://www.googleapis.com/")
        self.assertEqual(caught.exception.reason, "NETWORK_OFFLINE")


if __name__ == "__main__":
    unittest.main()
