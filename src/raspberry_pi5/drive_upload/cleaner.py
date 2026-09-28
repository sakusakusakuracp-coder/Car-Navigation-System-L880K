"""検証済み録画1件の削除と、削除直後の電源断からの回復を担当する。"""

from __future__ import annotations

import json
import os
import stat
import time

from drive_upload.config import UploadError
from drive_upload.local_files import checksum, identity, open_recording, parent_directory


class LocalCleaner:
    def __init__(self, catalog, verifier, config, policy) -> None:
        """設定と内部状態を初期化する。"""
        self.catalog, self.verifier, self.config, self.policy = catalog, verifier, config, policy

    def delete_verified(self, row: dict) -> dict:
        """直前の遠隔照合・ローカル再検査・削除意図の保存後にだけunlinkする。"""
        if not self.config.auto_delete_enabled:
            return row
        if not self.config.exclusive_recording_root or os.name != "posix":
            raise UploadError("DELETE_REQUIRES_EXCLUSIVE_LINUX_ROOT")
        if row["state"] not in {"VERIFIED", "DELETING"}:
            raise UploadError("NOT_VERIFIED")
        evidence = json.loads(row["verification_evidence"] or "{}")
        self.verifier.validate_evidence(row, evidence)
        expected = json.loads(row["local_identity"])
        intent = {"file_id": row["file_id"], "local_identity": expected}
        if row["state"] == "DELETING" and json.loads(row["delete_intent"] or "{}") != intent:
            raise UploadError("DELETE_INTENT_INVALID")
        # 長い通信待ちで、録画側のファイル管理ロックを占有しない。
        self.policy.check_control()
        evidence = self.verifier.verify_remote(row)
        with self.catalog.file_guard(), parent_directory(self.catalog.root, row["relative_path"], deleting=True) as (parent_fd, name):
            try:
                before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                if row["state"] != "DELETING":
                    raise UploadError("LOCAL_FILE_MISSING_BEFORE_DELETE")
                os.fsync(parent_fd)
                return self.catalog.update(row, state="DELETED", deleted_at=time.time(), blocked_reason="")
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.getuid()
                    or before.st_mode & 0o022 or identity(before) != expected):
                raise UploadError("LOCAL_IDENTITY_CHANGED")
            with open_recording(self.catalog.root, row["relative_path"], expected) as handle:
                if checksum(handle, self.policy.check_control) != row["checksum"]:
                    raise UploadError("LOCAL_CONTENT_CHANGED")
                # 大きい録画のハッシュ計算時間を、直前照合の鮮度に含めない。
                evidence = self.verifier.verify_remote(row)
                self.policy.check_control()
                row = self.catalog.update(row, state="DELETING", delete_intent=json.dumps(intent),
                                          verification_evidence=json.dumps(evidence))
                if identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) != expected:
                    raise UploadError("LOCAL_IDENTITY_CHANGED")
                os.unlink(name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            return self.catalog.update(row, state="DELETED", deleted_at=time.time(), blocked_reason="")
