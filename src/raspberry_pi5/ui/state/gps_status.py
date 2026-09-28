"""位置通知の有効期限と、GPS表示用の状態を保持する。"""

from __future__ import annotations

import math
from time import monotonic


class GpsStatus:
    """ソケット接続と有効な測位を区別し、通知停止時に正常表示を消す。"""

    def __init__(self) -> None:
        """GPSサービス接続、測位状態、通知期限を初期化する。"""
        self.connected = False
        self.state = "DISCONNECTED"
        self.reason = ""
        self._boot_id: str | None = None
        self._had_fix = False
        self._deadline = 0.0
        self._position_deadline = 0.0

    def apply(self, event: dict, now: float | None = None) -> None:
        """検証済みの通知を反映する。受信待ち時間も有効期限から差し引く。"""
        now = monotonic() if now is None else now
        if "gps_service_status" in event:
            self.connected = event["gps_service_status"] == "現在地補正サービス接続済み"
            self.state = "WAITING" if self.connected else "DISCONNECTED"
            self.reason = "状態通知を待っています" if self.connected else "現在地補正サービスに接続できません"
            self._deadline = now + 5.0 if self.connected else 0.0
            self._position_deadline = 0.0
            return
        name = event.get("event")
        if name not in {"position.status", "position.update", "position.invalidate"} or not self.connected:
            return
        if event.get("boot_id") != self._boot_id:
            self._boot_id = event.get("boot_id")
            self._had_fix = False
        self._position_deadline = 0.0
        self.reason = str(event.get("reason", ""))
        issued = event.get("issued_monotonic")
        if not _number(issued) or issued > now:
            self._stale("通知時刻を確認できません")
            return
        delay_ms = (now - issued) * 1000
        self._deadline = now + max(0.0, 5.0 - delay_ms / 1000)
        if delay_ms >= 5000:
            self._stale("状態通知が期限切れです")
            return
        if name == "position.invalidate":
            self._had_fix = event.get("has_fix_history", self._had_fix) is True
            self.state = "SIGNAL_LOST" if self._had_fix else "NO_FIX"
            return
        if name == "position.update":
            method = event.get("method")
            state = {"GPS": "GPS_ACTIVE", "DEAD_RECKONING": "DR_ACTIVE"}.get(method) if isinstance(method, str) else None
            if state is None or event.get("publishable") is not True or event.get("validity") not in ("VALID", "DEGRADED"):
                self._stale("有効な位置通知ではありません")
                return
            ttl = event.get("display_valid_for_ms", event.get("valid_for_ms"))
        else:
            state = event.get("state")
            self._had_fix = event.get("has_fix_history", self._had_fix) is True
            ttl = event.get("position_valid_for_ms")
            if state == "NO_FIX":
                self.state = "SIGNAL_LOST" if self._had_fix else "NO_FIX"
                return
        if state not in ("GPS_ACTIVE", "DR_ACTIVE") or not _number(ttl) or ttl <= delay_ms:
            self._stale("位置の有効期限を確認できません")
            return
        self.state = state
        self._had_fix = True
        self._position_deadline = now + (ttl - delay_ms) / 1000

    def expire(self, now: float | None = None) -> bool:
        """100ms周期で呼び、位置期限または5秒の通知期限を監視する。"""
        now = monotonic() if now is None else now
        if ((self._deadline and now >= self._deadline)
                or (self._position_deadline and now >= self._position_deadline)):
            self._stale("位置または状態通知の更新が停止しました")
            return True
        return False

    def _stale(self, reason: str) -> None:
        """期限切れ状態へ遷移し、古い正常表示の期限を消去する。"""
        self.state, self.reason = "STALE", reason
        self._deadline = self._position_deadline = 0.0

    @property
    def label(self) -> str:
        """内部状態を短い日本語の画面表示名へ変換する。"""
        return {"DISCONNECTED": "サービス未接続", "WAITING": "確認中", "STALE": "更新停止",
                "NO_FIX": "測位待ち", "SIGNAL_LOST": "信号喪失", "GPS_ACTIVE": "測位中",
                "DR_ACTIVE": "補正中"}.get(self.state, "確認中")

    @property
    def detail(self) -> str:
        """補正中を含むGPS状態の理由を画面表示用に返す。"""
        if self.state == "DR_ACTIVE":
            return "GPS信号喪失中・センサー情報で位置を推定"
        return self.reason or {"GPS_ACTIVE": "有効なGPS位置を受信", "NO_FIX": "最初の有効なGPS測位を待っています",
                               "SIGNAL_LOST": "有効な現在地を取得できません"}.get(self.state, self.label)


def _number(value: object) -> bool:
    """非負の有限数として扱える通知値か検証する。"""
    return type(value) in (int, float) and math.isfinite(value) and value >= 0
