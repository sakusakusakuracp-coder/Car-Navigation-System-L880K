"""送信・検証・削除の中断位置を保持し、常駐サービスの1巡を実行する。"""

from __future__ import annotations

import random
import time
from uuid import uuid4

from drive_upload.cleaner import LocalCleaner
from drive_upload.config import Paused, UploadError
from drive_upload.uploader import UploadWorker
from drive_upload.verifier import RemoteVerifier


class RecoveryWorker:
    def __init__(self, config, catalog, client, policy, publish=lambda event: None) -> None:
        """設定と内部状態を初期化する。"""
        self.config, self.catalog, self.client, self.policy = config, catalog, client, policy
        self.publish = publish
        self.boot_id = str(uuid4())
        self.sequence = 0
        self.state = "DISABLED" if not config.enabled else "IDLE"
        self.reason = ""
        self.verifier = RemoteVerifier(client, config)
        self.uploader = UploadWorker(catalog, client, self.verifier, config, policy, self.progress)
        self.cleaner = LocalCleaner(catalog, self.verifier, config, policy)

    def publish_status(self) -> dict:
        """パス・認証値・再開URIを含めず、UIが期限監視できる状態を配信する。"""
        self.sequence += 1
        event = {"schema_version": 1, "source_service": "07 Google Driveアップロード・削除",
                 "event": "upload.status", "boot_id": self.boot_id, "sequence": self.sequence,
                 "age_ms": 0, "valid_for_ms": 5000, "state": self.state, "reason": self.reason,
                 **self.catalog.status()}
        self.publish(event)
        return event

    def progress(self, row: dict) -> None:
        """処理の進捗を返す。"""
        self.state = "UPLOADING"
        self.reason = ""
        self.publish_status()

    def tick(self) -> dict:
        """確定順に1件処理する。停止やネットワーク待ちはエラー回数に含めない。"""
        row = None
        try:
            if not self.config.enabled:
                self.state = "DISABLED"
                return self.publish_status()
            self.policy.check_control()
            row = self.catalog.next_item(self.config.auto_delete_enabled)
            if row is None:
                summary = self.catalog.status()
                counts = summary["counts"]
                self.state = "BLOCKED" if counts.get("BLOCKED") else "RETRY_WAIT" if summary["problems"] else "IDLE"
                self.reason = summary["problems"][0]["blocked_reason"] if summary["problems"] else ""
                return self.publish_status()
            target = row["state"] if row["state"] in {"VERIFIED", "DELETING"} else "UPLOADING"
            row = self.catalog.update(row, owner_boot_id=self.boot_id, state=target)
            self.state = target
            self.publish_status()
            if target == "UPLOADING":
                row = self.uploader.upload(row)
            if self.config.auto_delete_enabled:
                row = self.cleaner.delete_verified(row)
            self.state, self.reason = row["state"], ""
        except Paused as exc:
            self.state, self.reason = "WAITING", exc.reason
            if row:
                row = self.catalog.get(row["file_id"])
                self.catalog.update(row, retry_after=time.time() + self.config.poll_interval_s, blocked_reason=exc.reason)
        except UploadError as exc:
            self.reason = exc.reason
            if row:
                row = self.catalog.get(row["file_id"])
                if exc.reason == "UPLOAD_SESSION_EXPIRED":
                    row = self.catalog.update(row, session_uri=None, acknowledged_bytes=0)
                row = self.schedule_retry(row, exc)
                self.state = row["state"]
            else:
                self.state = "BLOCKED"
        except OSError as exc:
            # 絶対パスやHTTP要求情報を外部イベントへ出さない。
            self.reason, self.state = "LOCAL_IO_ERROR", "BLOCKED"
            if row:
                self.catalog.update(self.catalog.get(row["file_id"]), state="BLOCKED", blocked_reason=self.reason)
        return self.publish_status()

    def schedule_retry(self, row: dict, error: UploadError) -> dict:
        """一時障害だけを上限付き指数待機へ進め、認証・内容不一致は保持する。"""
        count = row["retry_count"] + 1
        if not error.retryable or count > self.config.max_retries:
            return self.catalog.update(row, state="BLOCKED", blocked_reason=error.reason, retry_count=count)
        delay = min(self.config.retry_max_s, self.config.retry_base_s * 2 ** min(count - 1, 16))
        delay = max(delay * random.uniform(0.8, 1.0), error.retry_after)
        state = row["state"] if row["state"] in {"VERIFIED", "DELETING"} else "RETRY_WAIT"
        return self.catalog.update(row, state=state, retry_count=count, retry_after=time.time() + delay, blocked_reason=error.reason)


def retry_blocked(catalog, file_id: str) -> None:
    """認証・容量等を解消した後の明示再試行。内容不一致や削除証拠不良は解除しない。"""
    row = catalog.get(file_id)
    allowed = {"AUTHENTICATION_REQUIRED", "DRIVE_STORAGE_FULL", "DRIVE_FOLDER_UNAVAILABLE", "DRIVE_HTTP_403",
               "DRIVE_RATE_LIMIT", "DRIVE_TEMPORARY_ERROR", "NETWORK_ERROR", "REMOTE_FILE_MISSING",
               "UPLOAD_SESSION_EXPIRED", "UPLOAD_COMPLETION_UNCONFIRMED", "REMOTE_ID_CONFLICT"}
    if row["state"] != "BLOCKED" or row["blocked_reason"] not in allowed:
        raise UploadError("BLOCKED_REASON_REQUIRES_INSPECTION")
    state = "DELETING" if row["delete_intent"] else "VERIFIED" if row["verification_evidence"] else "UPLOADING"
    catalog.update(row, state=state, retry_count=0, retry_after=0, blocked_reason="")
