"""UIや他サービス向けのJSONイベントを作る。"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any, Callable

from .models import CachedValue, ServiceState, SupportState, VehicleSample


class Publisher:
    def __init__(self, emit: Callable[[dict[str, Any]], None]) -> None:
        """イベント出力先と再起動後も識別できる通知情報を初期化する。"""
        self.emit = emit
        self.boot_id = str(uuid.uuid4())
        self.sequence = 0

    def _publish(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        """スキーマ、boot_id、連番を付けたJSONイベントを発行する。"""
        self.sequence += 1
        message = {
            "schema_version": 1,
            "event": event,
            "boot_id": self.boot_id,
            "sequence": self.sequence,
            "payload": payload,
        }
        self.emit(message)
        return message

    def publish_state(self, state: ServiceState, *, reason: str | None = None) -> dict[str, Any]:
        """OBD2通信サービスの状態変化をイベントとして発行する。"""
        payload: dict[str, Any] = {"communication_state": state.value}
        if reason:
            payload["reason"] = reason
        return self._publish("vehicle.status", payload)

    def publish_sample(self, sample: VehicleSample) -> dict[str, Any]:
        """有効な車両測定値をUI向け更新イベントへ変換する。"""
        payload = asdict(sample)
        payload["validity"] = "VALID"
        return self._publish("vehicle.update", payload)

    def publish_invalid(self, cached: CachedValue) -> dict[str, Any]:
        """期限切れまたは通信断で使えなくなった値を通知する。"""
        return self._publish(
            "vehicle.invalidate",
            {"key": cached.sample.key, "validity": cached.validity.value, "reason": cached.invalid_reason},
        )

    def publish_capabilities(self, capabilities: dict[str, Any]) -> dict[str, Any]:
        """要求ごとの対応可否を車両機能イベントとして通知する。"""
        payload = {
            request_id: {
                "key": item.key,
                "support_state": item.state.value if isinstance(item.state, SupportState) else str(item.state),
                "reason": item.reason,
            }
            for request_id, item in capabilities.items()
        }
        return self._publish("vehicle.status", {"capabilities": payload})
