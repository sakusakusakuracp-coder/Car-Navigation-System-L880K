"""位置と無効状態を最新1件として配信する。"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from .models import PositionEstimate, Validity


class PositionPublisher:
    def __init__(self, source: str, emit: Callable[[dict[str, Any]], None], max_rate_hz: float = 5.0) -> None:
        """位置イベントの出力元、連番、通知間隔、最新状態を初期化する。"""
        self.source = source
        self.emit = emit
        self.boot_id = uuid.uuid4().hex
        self.sequence = 0
        self._minimum_interval_s = 1.0 / max(0.1, max_rate_hz)
        self._last_position_monotonic = float("-inf")
        self.latest: dict[str, Any] = {"event": "position.status", "state": "NO_FIX", "validity": Validity.UNKNOWN.value}

    def publish_position(self, estimate: PositionEstimate, *, display_valid_for_ms: int | None = None) -> dict[str, Any] | None:
        """位置推定を契約形式へ変換し、通知頻度を制限して出力する。"""
        self.sequence += 1
        payload = estimate.to_payload(self.source, self.boot_id, self.sequence)
        # UIの表示期限は品質判定の残存時間。04/05の位置配送契約とは分ける。
        payload["display_valid_for_ms"] = estimate.valid_for_ms if display_valid_for_ms is None else display_valid_for_ms
        self.latest = payload
        now = time.monotonic()
        if now - self._last_position_monotonic < self._minimum_interval_s:
            return None
        self._last_position_monotonic = now
        self.emit(payload)
        return payload

    def invalidate_position(self, reason: str, state: str = "NO_FIX", **details: Any) -> dict[str, Any]:
        """位置が利用できなくなったことを無効化イベントとして通知する。"""
        self.sequence += 1
        payload = {
            "event": "position.invalidate",
            "schema_version": 1,
            "source": self.source,
            "boot_id": self.boot_id,
            "sequence": self.sequence,
            "state": state,
            "validity": Validity.UNKNOWN.value,
            "reason": reason,
            "issued_monotonic": time.monotonic(),
            **details,
        }
        self.latest = payload
        self.emit(payload)
        return payload

    def publish_status(self, state: str, reason: str = "", **details: Any) -> dict[str, Any]:
        """GPSまたは補正処理の状態を最新状態イベントとして通知する。"""
        self.sequence += 1
        payload = {
            "event": "position.status",
            "schema_version": 1,
            "source": self.source,
            "boot_id": self.boot_id,
            "sequence": self.sequence,
            "state": state,
            "reason": reason,
            "issued_monotonic": time.monotonic(),
            **details,
        }
        self.latest = payload
        self.emit(payload)
        return payload
