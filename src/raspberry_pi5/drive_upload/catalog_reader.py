"""確定録画と送信進捗をSQLiteへ永続化する共通台帳。"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from drive_upload.config import Paused, UploadError
from drive_upload.local_files import checksum, identity, open_recording
from navigation.resident_runtime import ProcessLock


class CatalogReader:
    """短いトランザクションと更新バージョンで、通知重複と状態の巻戻しを防ぐ。"""

    def __init__(self, path: Path, root: Path) -> None:
        """設定と内部状態を初期化する。"""
        self.path = Path(path)
        self.root = root.resolve(strict=True)
        if root.is_symlink():
            raise UploadError("INVALID_RECORDING_ROOT")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version != 1 and (version != 0 or tables):
                raise UploadError("CATALOG_SCHEMA_UNSUPPORTED")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS recordings (
                    file_id TEXT PRIMARY KEY, relative_path TEXT NOT NULL UNIQUE,
                    camera_id TEXT NOT NULL, size_bytes INTEGER NOT NULL, checksum TEXT NOT NULL,
                    local_identity TEXT NOT NULL, ready_at REAL NOT NULL,
                    state TEXT NOT NULL DEFAULT 'READY', state_version INTEGER NOT NULL DEFAULT 0,
                    owner_boot_id TEXT, planned_remote_id TEXT, session_uri TEXT,
                    acknowledged_bytes INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0, retry_after REAL NOT NULL DEFAULT 0,
                    blocked_reason TEXT NOT NULL DEFAULT '', verification_evidence TEXT,
                    delete_intent TEXT, deleted_at REAL
                );
                CREATE INDEX IF NOT EXISTS recording_queue ON recordings(state, retry_after, ready_at);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS network_budget (day TEXT PRIMARY KEY, bytes INTEGER NOT NULL);
                PRAGMA user_version=1;
            """)
            value = str(self.root)
            existing = db.execute("SELECT value FROM settings WHERE key='recording_root'").fetchone()
            if existing and existing[0] != value:
                raise UploadError("CATALOG_ROOT_CHANGED")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('recording_root', ?)", (value,))
        if os.name == "posix":
            self.path.chmod(0o600)

    def connect(self):
        """通信・ハッシュ計算中はトランザクションを保持しない。"""
        @contextmanager
        def connection():
            """接続状態または接続情報を返す。"""
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            try:
                with db:
                    yield db
            finally:
                db.close()
        return connection()

    @contextmanager
    def file_guard(self):
        """録画確定・差替え・削除の全処理者が取得する共通ロック。"""
        lock = ProcessLock(str(self.path) + ".files.lock")
        try:
            lock.acquire()
        except RuntimeError as exc:
            raise Paused("RECORDING_CATALOG_BUSY") from exc
        try:
            yield
        finally:
            lock.release()

    def register_ready(self, relative_path: str, camera_id: str, file_id: str | None = None) -> str:
        """録画側がclose・fsync・再生検査後に呼ぶ。不変ファイルを1回だけ登録する。"""
        if camera_id not in {"front", "rear", "left", "right", "test"}:
            raise UploadError("INVALID_CAMERA_ID")
        with self.file_guard(), open_recording(self.root, relative_path) as handle:
            digest = checksum(handle)
            info = identity(os.fstat(handle.fileno()))
            with self.connect() as db:
                existing = db.execute("SELECT * FROM recordings WHERE relative_path=?", (relative_path,)).fetchone()
                if existing:
                    if (existing["checksum"] != digest or json.loads(existing["local_identity"]) != info
                            or existing["camera_id"] != camera_id or (file_id and existing["file_id"] != file_id)):
                        raise UploadError("RECORDING_ALREADY_REGISTERED_WITH_DIFFERENT_CONTENT")
                    return existing["file_id"]
                file_id = file_id or str(uuid4())
                db.execute("""INSERT INTO recordings
                    (file_id,relative_path,camera_id,size_bytes,checksum,local_identity,ready_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (file_id, relative_path, camera_id, info["size"], digest, json.dumps(info), time.time()))
        return file_id

    def bind_destination(self, account: str, folder: str) -> None:
        """再起動時に別アカウントや別フォルダの送信情報を混ぜない。"""
        binding = json.dumps([account, folder])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT value FROM settings WHERE key='destination'").fetchone()
            if old and old[0] != binding:
                raise UploadError("DESTINATION_CHANGED_USE_NEW_CATALOG")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('destination', ?)", (binding,))

    def get(self, file_id: str) -> dict:
        """最新の行を取得する。セッションURIをそのままログへ渡さない。"""
        with self.connect() as db:
            row = db.execute("SELECT * FROM recordings WHERE file_id=?", (file_id,)).fetchone()
        if row is None:
            raise UploadError("UNKNOWN_FILE_ID")
        return dict(row)

    def update(self, row: dict, **values) -> dict:
        """期待する更新バージョンが一致した行だけを原子的に更新する。"""
        allowed = {"state", "owner_boot_id", "planned_remote_id", "session_uri", "acknowledged_bytes",
                   "retry_count", "retry_after", "blocked_reason", "verification_evidence", "delete_intent", "deleted_at"}
        if not values or set(values) - allowed:
            raise ValueError("台帳更新項目が不正です")
        assignments = ",".join(f"{key}=?" for key in values)
        with self.connect() as db:
            changed = db.execute(f"UPDATE recordings SET {assignments},state_version=state_version+1 WHERE file_id=? AND state_version=?",
                                 (*values.values(), row["file_id"], row["state_version"])).rowcount
            if changed != 1:
                raise UploadError("CATALOG_CONFLICT", retryable=True)
        return self.get(row["file_id"])

    def next_item(self, auto_delete: bool) -> dict | None:
        """期限が来た作業を1件だけ取得する。書込中ファイルは選ばない。"""
        states = ["READY", "UPLOADING", "RETRY_WAIT"]
        if auto_delete:
            states += ["VERIFIED", "DELETING"]
        with self.connect() as db:
            row = db.execute(f"SELECT * FROM recordings WHERE state IN ({','.join('?' for _ in states)}) AND retry_after<=? ORDER BY ready_at,file_id LIMIT 1",
                             (*states, time.time())).fetchone()
        return dict(row) if row else None

    def set_paused(self, paused: bool) -> None:
        """再起動しても利用者による一時停止を維持する。"""
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('paused', ?)", (str(int(paused)),))

    def is_paused(self) -> bool:
        """一時停止条件を判定する。"""
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        return bool(row and row[0] == "1")

    def reserve_bytes(self, size: int, limit: int) -> bool:
        """送信前にUTC日次予算を消費する。応答喪失・再送分も戻さない。"""
        day = datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO network_budget VALUES (?,0)", (day,))
            return db.execute("UPDATE network_budget SET bytes=bytes+? WHERE day=? AND bytes+?<=?", (size, day, size, limit)).rowcount == 1

    def recover_pending(self) -> None:
        """単一起動ロック取得後にだけ旧所有者を解放し、長すぎる再試行期限を丸める。"""
        with self.connect() as db:
            db.execute("UPDATE recordings SET owner_boot_id=NULL,state_version=state_version+1 WHERE owner_boot_id IS NOT NULL")
            db.execute("UPDATE recordings SET retry_after=? WHERE retry_after>?", (time.time() + 600, time.time() + 600))

    def status(self) -> dict:
        """秘密情報や録画パスを除き、状態別の件数と保持容量を返す。"""
        with self.connect() as db:
            groups = db.execute("SELECT state,COUNT(*) AS count,SUM(size_bytes) AS bytes FROM recordings GROUP BY state").fetchall()
            problems = db.execute("SELECT file_id,state,blocked_reason,retry_after FROM recordings WHERE blocked_reason!='' ORDER BY ready_at LIMIT 20").fetchall()
        return {"counts": {r["state"]: r["count"] for r in groups},
                "bytes_by_state": {r["state"]: r["bytes"] for r in groups},
                "problems": [dict(r) for r in problems], "paused": self.is_paused()}
