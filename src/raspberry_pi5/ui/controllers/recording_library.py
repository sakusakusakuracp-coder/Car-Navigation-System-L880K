"""確定動画の一覧と、クラウド側の削除と競合しない再生用コピー。"""

from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from uuid import uuid4

from camera.config import CameraConfig
from drive_upload.local_files import open_recording
from navigation.resident_runtime import ProcessLock


class RecordingLibrary:
    def __init__(self, config_path):
        """録画サービスと同じ設定から台帳を読み、独自の台帳は作らない。"""
        self.config_path = config_path
        self.temporary = tempfile.TemporaryDirectory(prefix="l880k-playback-")

    def list(self, camera="", date="", page=0):
        """日付・方向で確定録画を50件ずつ取得し、秘密情報を画面に渡さない。"""
        config = CameraConfig.load(self.config_path)
        filters, values = [], []
        if camera:
            if camera not in {"front", "rear", "left", "right"}:
                raise ValueError("カメラ方向が不正です")
            filters.append("camera_id=?")
            values.append(camera)
        if date:
            start = datetime.strptime(date, "%Y-%m-%d")
            filters.append("ready_at>=? AND ready_at<?")
            values.extend((start.timestamp(), (start + timedelta(days=1)).timestamp()))
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with closing(sqlite3.connect(config.catalog_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT file_id,camera_id,ready_at,state,size_bytes,deleted_at FROM recordings" + where +
                              " ORDER BY ready_at DESC,file_id LIMIT 51 OFFSET ?", (*values, max(0, page) * 50)).fetchall()
        return {"items": [{**dict(row), "date": datetime.fromtimestamp(row["ready_at"]).strftime("%Y-%m-%d %H:%M:%S"),
                           "playable": row["deleted_at"] is None and row["state"] not in {"DELETING", "DELETED"}}
                          for row in rows[:50]], "more": len(rows) > 50, "page": max(0, page)}

    def prepare(self, file_id):
        """共通ロック中に確定動画をコピーし、再生中に元録画が消えても再生を維持する。"""
        config = CameraConfig.load(self.config_path)
        lock = ProcessLock(str(config.catalog_path) + ".files.lock")
        lock.acquire()
        try:
            with closing(sqlite3.connect(config.catalog_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT * FROM recordings WHERE file_id=?", (file_id,)).fetchone()
            if row is None or row["deleted_at"] is not None or row["state"] in {"DELETED", "DELETING"}:
                raise ValueError("動画はローカルにありません。一覧を更新してください")
            if row["size_bytes"] > 512 * 1024 * 1024:
                raise ValueError("再生用コピーの上限512MiBを超えています")
            if shutil.disk_usage(self.temporary.name).free < row["size_bytes"] + 64 * 1024 * 1024:
                raise ValueError("再生用の空き容量が足りません")
            # 切替直後にデコーダが古いコピーを保持していても上書きしない。
            for old in Path(self.temporary.name).glob("*.mkv"):
                try:
                    old.unlink()
                except PermissionError:
                    raise OSError("前の動画の終了を待って再度再生してください")
            target = Path(self.temporary.name) / (uuid4().hex + ".mkv")
            with open_recording(config.recording_root, row["relative_path"], json.loads(row["local_identity"])) as source:
                with target.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
            return str(target)
        finally:
            lock.release()

    def close(self):
        """このUIが作った再生用コピーだけを終了時に除去する。"""
        self.temporary.cleanup()
