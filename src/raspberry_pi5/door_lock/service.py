"""ドアロックの条件確認・排他パルス・結果記録。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any

from door_lock.driver import OutputDriver
from door_lock.store import CommandStore


@dataclass(frozen=True)
class DoorLockConfig:
    enabled: bool = False
    lock_gpio: int | None = None
    unlock_gpio: int | None = None
    active_level: int | None = None
    release_level: int | None = None
    pulse_ms: int = 300
    max_pulse_ms: int = 500
    cooldown_ms: int = 2_000
    speed_max_age_ms: int = 1_000
    stopped_threshold_mps: float = 0.3
    allowed_callers: tuple[str, ...] = ()
    command_ttl_max_ms: int = 10_000

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "DoorLockConfig":
        """DoorLockConfigのfrom_mappingの内部処理を実行する。"""
        allowed = tuple(str(item) for item in data.get("allowed_callers", []))
        result = cls(allowed_callers=allowed, **{field: data[field] for field in cls.__dataclass_fields__ if field not in {"allowed_callers"} and field in data})
        if result.enabled:
            if None in (result.lock_gpio, result.unlock_gpio, result.active_level, result.release_level):
                raise ValueError("有効化にはGPIOと出力レベルの実測設定が必要です")
            if result.lock_gpio == result.unlock_gpio:
                raise ValueError("LOCKとUNLOCKのGPIOは別にしてください")
        if not 0 < result.pulse_ms <= result.max_pulse_ms:
            raise ValueError("パルス時間の設定が不正です")
        if result.cooldown_ms < 0 or result.command_ttl_max_ms <= 0:
            raise ValueError("待機時間・要求寿命が不正です")
        return result


class DoorLockService:
    """1回の要求だけを排他的に実行し、結果不明時は再実行しない。"""

    def __init__(self, config: DoorLockConfig, driver: OutputDriver, store: CommandStore) -> None:
        """設定と内部状態を初期化する。"""
        self.config = config
        self.driver = driver
        self.store = store
        self.store.load()
        self._lock = Lock()
        self.state = "DISABLED" if not config.enabled else "IDLE"
        self.boot_id = f"door-lock-{time.time_ns()}"
        self.session_id = f"session-{time.time_ns()}"
        self.last_completed_at = 0.0

    def health(self) -> dict[str, Any]:
        """稼働状態と異常情報を返す。"""
        return {"event": "door_lock.status", "service_state": self.state, "boot_id": self.boot_id, "session_id": self.session_id, "lock_state": "UNKNOWN", "enabled": self.config.enabled}

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """DoorLockServiceのhandle_requestの内部処理を実行する。"""
        operation = str(request.get("operation", ""))
        if operation in {"GET_STATE", "GET_HEALTH"}:
            return {"accepted": True, **self.health()}
        if operation not in {"LOCK", "UNLOCK"}:
            return self._reject(request, "UNSUPPORTED_OPERATION")
        if not self.config.enabled:
            return self._reject(request, "DISABLED")
        caller = str(request.get("caller", ""))
        if self.config.allowed_callers and caller not in self.config.allowed_callers:
            return self._reject(request, "UNAUTHORIZED")
        command_id = str(request.get("command_id", ""))
        session_id = str(request.get("session_id", ""))
        if not command_id or session_id != self.session_id:
            return self._reject(request, "INVALID_COMMAND_CONTEXT")
        expires_at = request.get("expires_at_monotonic_ms")
        now_ms = int(time.monotonic() * 1000)
        if not isinstance(expires_at, (int, float)) or expires_at < now_ms or expires_at - now_ms > self.config.command_ttl_max_ms:
            return self._reject(request, "EXPIRED")
        speed = request.get("speed_mps")
        speed_validity = str(request.get("speed_validity", "UNKNOWN"))
        speed_age_ms = request.get("speed_age_ms")
        if speed_validity != "VALID" or not isinstance(speed, (int, float)) or float(speed) < 0.0 or float(speed) > self.config.stopped_threshold_mps or not isinstance(speed_age_ms, (int, float)) or float(speed_age_ms) > self.config.speed_max_age_ms:
            return self._reject(request, "NOT_STOPPED")
        key = f"{caller}:{command_id}"
        fingerprint = {"operation": operation, "session_id": session_id}
        with self._lock:
            if self.state == "COOLDOWN" and (time.monotonic() - self.last_completed_at) * 1000 >= self.config.cooldown_ms:
                self.state = "IDLE"
            if self.state in {"PULSING_LOCK", "PULSING_UNLOCK", "FAULT", "STOPPING"}:
                return self._reject(request, "BUSY")
            if (time.monotonic() - self.last_completed_at) * 1000 < self.config.cooldown_ms:
                return self._reject(request, "COOLDOWN")
            reservation, previous = self.store.reserve(key, fingerprint)
            if reservation == "EXISTING":
                return {"accepted": True, "state": previous.get("state", "UNKNOWN"), "command_id": command_id, "replayed": True}
            if reservation == "ID_CONFLICT":
                return self._reject(request, "ID_CONFLICT")
            self.state = "PULSING_LOCK" if operation == "LOCK" else "PULSING_UNLOCK"
            self.store.update(key, state="RUNNING", started_at_monotonic_ms=now_ms)
            return self._pulse_locked(key, command_id, operation)

    def _pulse_locked(self, key: str, command_id: str, operation: str) -> dict[str, Any]:
        """排他制御中に駆動パルスを実行する。"""
        started = time.monotonic()
        try:
            self.driver.release_all()
            self.driver.activate(operation)
            time.sleep(self.config.pulse_ms / 1000.0)
        except Exception as exc:  # noqa: BLE001
            self.state = "FAULT"
            try:
                self.driver.release_all()
            except Exception:  # noqa: BLE001
                pass
            self.store.update(key, state="UNKNOWN", reason=str(exc))
            return {"accepted": True, "success": False, "state": "UNKNOWN", "command_id": command_id, "reason": "DRIVER_FAULT"}
        finally:
            try:
                self.driver.release_all()
            except Exception as exc:  # noqa: BLE001
                self.state = "FAULT"
                self.store.update(key, state="UNKNOWN", reason=f"release: {exc}")
        if self.state == "FAULT":
            return {"accepted": True, "success": False, "state": "UNKNOWN", "command_id": command_id, "reason": "RELEASE_UNKNOWN"}
        self.last_completed_at = time.monotonic()
        self.state = "COOLDOWN"
        self.store.update(key, state="PULSE_COMPLETED", finished_at_monotonic_ms=int(time.monotonic() * 1000), elapsed_ms=int((time.monotonic() - started) * 1000))
        return {"accepted": True, "success": True, "state": "PULSE_COMPLETED", "operation_result": "PULSE_COMPLETED", "lock_state": "UNKNOWN", "command_id": command_id}

    def stop(self) -> None:
        """サービスまたはデバイスを停止して資源を解放する。"""
        self.state = "STOPPING"
        try:
            self.driver.release_all()
        finally:
            self.driver.close()

    def _reject(self, request: dict[str, Any], reason: str) -> dict[str, Any]:
        """共通形式の拒否応答を作る。"""
        return {"accepted": False, "success": False, "state": "REJECTED", "command_id": request.get("command_id"), "reason": reason}
