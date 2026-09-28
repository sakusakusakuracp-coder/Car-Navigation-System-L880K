"""Driveの保存内容を照合する。HTTP成功だけでは削除許可を出さない。"""

from __future__ import annotations

import time

from drive_upload.config import UploadError


class RemoteVerifier:
    def __init__(self, client, config) -> None:
        """設定と内部状態を初期化する。"""
        self.client, self.config = client, config

    def verify_remote(self, row: dict) -> dict:
        """指定したID・親フォルダ・元録画ID・サイズ・MD5を全て検証する。"""
        self.client.check_destination(self.config.account_ref, self.config.folder_id)
        data = self.client.metadata(row["planned_remote_id"])
        if data is None:
            raise UploadError("REMOTE_FILE_MISSING", retryable=True)
        self.validate_metadata(row, data)
        return {"account_ref": self.config.account_ref, "folder_id": self.config.folder_id,
                "remote_id": data["id"], "file_id": row["file_id"], "size_bytes": row["size_bytes"],
                "checksum": row["checksum"], "remote_version": data["version"], "verified_at": time.time()}

    def validate_metadata(self, row: dict, data: dict) -> None:
        """メタデータの形式と内容を検証する。"""
        try:
            matches = (data.get("id") == row["planned_remote_id"]
                and data.get("parents") == [self.config.folder_id]
                and data.get("trashed") is False
                and data.get("appProperties", {}).get("file_id") == row["file_id"]
                and int(data.get("size", -1)) == row["size_bytes"]
                and data.get("md5Checksum") == row["checksum"]
                and bool(data.get("version")))
        except (TypeError, ValueError, AttributeError):
            matches = False
        if not matches:
            raise UploadError("REMOTE_CONTENT_MISMATCH")

    def validate_evidence(self, row: dict, evidence: dict) -> None:
        """削除中に停止した行も、永続化済みの照合証拠なしでは回復しない。"""
        expected = {"account_ref": self.config.account_ref, "folder_id": self.config.folder_id,
                    "remote_id": row["planned_remote_id"], "file_id": row["file_id"],
                    "size_bytes": row["size_bytes"], "checksum": row["checksum"]}
        if not evidence or any(evidence.get(k) != v for k, v in expected.items()):
            raise UploadError("VERIFICATION_EVIDENCE_INVALID")
