"""書込中の記録と、アップロード可能な確定録画との境界を管理する。"""

import hashlib
import json
import os
from pathlib import Path
import time

from drive_upload.catalog_reader import CatalogReader


def sync_directory(path: Path):
    """Linuxでは名前変更を保存先ディレクトリへ同期する。"""
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def digest_file(path: Path):
    """確定時と回復時の内容一致をSHA-256で確認する。"""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def inspect_video(path: Path):
    """実際に全フレームを復号し、空動画・途中破損をREADYから除外する。"""
    import av

    count = 0
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            if frame.width <= 0 or frame.height <= 0:
                raise ValueError("VIDEO_DIMENSIONS_INVALID")
            count += 1
    if not count:
        raise ValueError("VIDEO_EMPTY")
    return count


class RecordingCatalog:
    def __init__(self, config):
        """共有台帳に録画側専用テーブルを追加し、送信状態を上書きしない。"""
        self.root = config.recording_root
        self.shared = CatalogReader(config.catalog_path, self.root)
        with self.shared.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS capture_segments (
                file_id TEXT PRIMARY KEY, camera_id TEXT NOT NULL,
                partial_path TEXT NOT NULL, final_path TEXT NOT NULL,
                state TEXT NOT NULL, started_at REAL NOT NULL,
                evidence TEXT, reason TEXT NOT NULL DEFAULT '')""")

    def mark_writing(self, file_id, camera_id):
        """ファイル作成より先にIDと両方の名前を記録する。"""
        partial, final = f"{camera_id}-{file_id}.mkv.part", f"{camera_id}-{file_id}.mkv"
        with self.shared.connect() as db:
            db.execute("INSERT INTO capture_segments VALUES (?,?,?,?,?,?,NULL,'')",
                       (file_id, camera_id, partial, final, "WRITING", time.time()))
        return self.root / partial, self.root / final

    def set_evidence(self, file_id, evidence):
        """終端・再生検査・同期を終えた内容を、名前変更前に記録する。"""
        with self.shared.connect() as db:
            changed = db.execute("UPDATE capture_segments SET evidence=?,state='FINALIZING' WHERE file_id=? AND state='WRITING'",
                                 (json.dumps(evidence), file_id)).rowcount
            if changed != 1:
                raise RuntimeError("CAPTURE_STATE_CONFLICT")

    def mark_ready(self, file_id):
        """確定ファイルを既存のGoogle Drive台帳へ登録し、再通知を冪等にする。"""
        with self.shared.connect() as db:
            row = dict(db.execute("SELECT * FROM capture_segments WHERE file_id=?", (file_id,)).fetchone())
        if row["state"] not in {"FINALIZING", "READY"}:
            raise ValueError("CAPTURE_NOT_FINALIZED")
        self.shared.register_ready(row["final_path"], row["camera_id"], file_id)
        with self.shared.connect() as db:
            db.execute("UPDATE capture_segments SET state='READY',reason='' WHERE file_id=?", (file_id,))

    def mark_incomplete(self, file_id, reason):
        """未完了ファイルを残し、通常アップロード対象に登録しない。"""
        with self.shared.connect() as db:
            db.execute("UPDATE capture_segments SET state='INCOMPLETE',reason=? WHERE file_id=? AND state='WRITING'",
                       (reason, file_id))

    def recover_catalog(self):
        """確定証拠のある未登録動画だけ回復し、根拠のない末尾は未完了にする。"""
        with self.shared.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM capture_segments WHERE state IN ('WRITING','FINALIZING')")]
        recovered = []
        for row in rows:
            if row["state"] == "WRITING":
                self.mark_incomplete(row["file_id"], "INTERRUPTED_BEFORE_VALIDATION")
                continue
            # READY以降はアップロード側が削除する場合があるため、再登録しない。
            with self.shared.connect() as db:
                registered = db.execute("SELECT 1 FROM recordings WHERE file_id=?", (row["file_id"],)).fetchone()
                if registered:
                    db.execute("UPDATE capture_segments SET state='READY' WHERE file_id=?", (row["file_id"],))
                    continue
            partial = self.root / row["partial_path"]
            final = self.root / row["final_path"]
            candidate = final if final.exists() else partial
            try:
                evidence = json.loads(row["evidence"])
                if (candidate.is_symlink() or candidate.stat().st_size != evidence["size_bytes"]
                        or digest_file(candidate) != evidence["sha256"]
                        or inspect_video(candidate) != evidence["frame_count"]):
                    raise ValueError("FINALIZED_CONTENT_CHANGED")
                if candidate == partial:
                    partial.rename(final)
                    sync_directory(self.root)
                self.mark_ready(row["file_id"])
                recovered.append(row["file_id"])
            except Exception as exc:
                with self.shared.connect() as db:
                    db.execute("UPDATE capture_segments SET reason=? WHERE file_id=?", (type(exc).__name__, row["file_id"]))
        return recovered
