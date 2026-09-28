"""クラウド送信の通知期限と、画面用の短い状態名を保持する。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from time import monotonic


@dataclass
class UploadStatus:
    state: str = "DISCONNECTED"
    reason: str = ""
    pending: int = 0
    deadline: float = 0

    def apply(self, event: dict, now: float | None = None) -> None:
        """受信した時点の年齢を有効期間から引き、古いスナップショットを失効させる。"""
        now = monotonic() if now is None else now
        if event.get("upload_service_status") == "DISCONNECTED":
            self.state, self.deadline = "DISCONNECTED", 0
            return
        if event.get("event") != "upload.status":
            return
        age, ttl = event.get("age_ms"), event.get("valid_for_ms")
        if (type(age) not in (int, float) or type(ttl) not in (int, float)
                or not math.isfinite(age) or not math.isfinite(ttl) or age < 0 or not 0 < ttl <= 60000 or age >= ttl):
            self.state, self.deadline = "STALE", 0
            return
        counts = event.get("counts", {})
        if not isinstance(counts, dict) or any(type(v) is not int or v < 0 for v in counts.values()):
            self.state, self.deadline = "STALE", 0
            return
        self.state = str(event.get("state", "STALE"))
        self.reason = str(event.get("reason", ""))
        self.pending = sum(counts.get(k, 0) for k in ("READY", "UPLOADING", "RETRY_WAIT", "BLOCKED"))
        self.deadline = now + (ttl - age) / 1000

    def expire(self, now: float | None = None) -> bool:
        """アップロード状態の期限を確認し、期限切れなら表示を無効化する。"""
        now = monotonic() if now is None else now
        if self.deadline and now >= self.deadline:
            self.state, self.deadline = "STALE", 0
            return True
        return False

    @property
    def label(self) -> str:
        """内部の送信状態を画面用の日本語ラベルへ変換する。"""
        labels = {"DISCONNECTED": "サービス未接続", "DISABLED": "無効", "STALE": "状態確認待ち",
                  "IDLE": "待機中", "UPLOADING": "送信中", "RETRY_WAIT": "再試行待ち", "BLOCKED": "要確認",
                  "VERIFIED": "保存確認済み", "DELETING": "ローカル整理中", "DELETED": "保存・整理完了"}
        if self.state == "WAITING":
            return {"PAUSED": "一時停止", "DAILY_BUDGET_EXHAUSTED": "通信量上限", "STOPPING": "停止中",
                    "RECORDING_CATALOG_BUSY": "録画確定待ち"}.get(self.reason, "接続待ち")
        return labels.get(self.state, "状態確認待ち")
