"""台帳のIDを保持しながら、閉じた録画を分割して送信する。"""

from __future__ import annotations

import json
import os

from drive_upload.config import UploadError
from drive_upload.local_files import checksum, identity, open_recording


class UploadWorker:
    def __init__(self, catalog, client, verifier, config, policy, progress) -> None:
        """設定と内部状態を初期化する。"""
        self.catalog, self.client, self.verifier = catalog, client, verifier
        self.config, self.policy, self.progress = config, policy, progress

    def upload(self, row: dict) -> dict:
        """完了応答の喪失時も予定IDで照合し、別IDの重複動画を作らない。"""
        self.policy.check_control()
        self.client.check_destination(self.config.account_ref, self.config.folder_id)
        expected = json.loads(row["local_identity"])
        with open_recording(self.catalog.root, row["relative_path"], expected) as handle:
            if checksum(handle, self.policy.check_control) != row["checksum"]:
                raise UploadError("LOCAL_CONTENT_CHANGED")
            if not row["planned_remote_id"]:
                row = self.catalog.update(row, planned_remote_id=self.client.generate_id())
            remote = self.client.metadata(row["planned_remote_id"])
            if remote is not None:
                self.verifier.validate_metadata(row, remote)
                return self._verified(row)
            if row["session_uri"]:
                complete, offset = self.client.resume_upload(row["session_uri"], row["size_bytes"])
                row = self.catalog.update(row, acknowledged_bytes=offset)
                if complete:
                    return self._verified(row)
            else:
                session_uri = self.client.start_upload(row, self.config.folder_id)
                row = self.catalog.update(row, session_uri=session_uri, acknowledged_bytes=0)
                offset = 0
            while offset < row["size_bytes"]:
                self.policy.check_control()
                if identity(os.fstat(handle.fileno())) != expected:
                    raise UploadError("LOCAL_IDENTITY_CHANGED")
                handle.seek(offset)
                data = handle.read(min(self.config.chunk_bytes, row["size_bytes"] - offset))
                if not data:
                    raise UploadError("LOCAL_READ_INCOMPLETE")
                self.policy.reserve_chunk(len(data))
                complete, received = self.client.send_chunk(row["session_uri"], data, offset, row["size_bytes"])
                if received > offset + len(data) or received <= offset:
                    raise UploadError("UPLOAD_PROGRESS_INVALID", retryable=True)
                row = self.catalog.update(row, acknowledged_bytes=received)
                self.progress(row)
                offset = received
                if complete:
                    if offset != row["size_bytes"]:
                        raise UploadError("UPLOAD_COMPLETION_INVALID")
                    return self._verified(row)
            # 全バイトを308で受領しただけでは完成と見なさず、次回サーバーへ照会する。
            raise UploadError("UPLOAD_COMPLETION_UNCONFIRMED", retryable=True)

    def _verified(self, row: dict) -> dict:
        """UploadWorkerの_verifiedの内部処理を実行する。"""
        evidence = self.verifier.verify_remote(row)
        return self.catalog.update(row, state="VERIFIED", verification_evidence=json.dumps(evidence),
                                   acknowledged_bytes=row["size_bytes"], session_uri=None, blocked_reason="", retry_after=0)
