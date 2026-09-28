"""配送前の位置値・品質・期限の検査。"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ValidatedPosition:
    payload: dict[str, Any]
    received_monotonic: float


class PayloadValidator:
    def __init__(self, max_age_ms: int = 2_000, max_accuracy_m: float = 100.0, allow_dead_reckoning: bool = False) -> None:
        """設定と内部状態を初期化する。"""
        self.max_age_ms = max_age_ms
        self.max_accuracy_m = max_accuracy_m
        self.allow_dead_reckoning = allow_dead_reckoning

    def validate_position(self, payload: dict[str, Any], now: float | None = None) -> ValidatedPosition:
        """入力の値を変更せず、配送可否だけを判定する。"""
        now = time.monotonic() if now is None else now
        required = ("source", "boot_id", "sequence", "estimate_id", "origin_id", "latitude_deg", "longitude_deg")
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError(f"必須項目がありません: {','.join(missing)}")
        lat = float(payload["latitude_deg"])
        lon = float(payload["longitude_deg"])
        accuracy = float(payload.get("horizontal_accuracy_m"))
        if not all(math.isfinite(value) for value in (lat, lon, accuracy)):
            raise ValueError("位置または精度が有限値ではありません")
        if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
            raise ValueError("緯度経度が範囲外です")
        if accuracy <= 0 or accuracy > self.max_accuracy_m:
            raise ValueError("水平精度が許容範囲外です")
        if not bool(payload.get("publishable", False)):
            raise ValueError("publishable=falseの位置は配送できません")
        if payload.get("method") == "DEAD_RECKONING" and not self.allow_dead_reckoning:
            raise ValueError("推定位置の配送は無効です")
        valid_for_ms = int(payload.get("valid_for_ms", 0))
        age_ms = int(payload.get("age_ms_at_send", 0))
        if valid_for_ms <= 0 or age_ms < 0 or age_ms > min(valid_for_ms, self.max_age_ms):
            raise ValueError("位置の有効期間または測定年齢が不正です")
        sequence = int(payload["sequence"])
        if sequence < 0:
            raise ValueError("元通知のsequenceが不正です")
        return ValidatedPosition(dict(payload), now)

    def prepare_delivery(self, position: ValidatedPosition, now: float | None = None, bridge_delay_bound_ms: int = 100) -> dict[str, Any]:
        """送信直前の年齢を計算し、期限切れなら例外にする。"""
        now = time.monotonic() if now is None else now
        payload = dict(position.payload)
        age_ms = int(payload.get("age_ms_at_send", 0)) + max(0, int((now - position.received_monotonic) * 1000)) + bridge_delay_bound_ms
        limit = min(int(payload["valid_for_ms"]), self.max_age_ms)
        if age_ms >= limit:
            raise TimeoutError("送信前に位置の有効期間が切れました")
        payload["age_ms_at_send"] = age_ms
        return payload
