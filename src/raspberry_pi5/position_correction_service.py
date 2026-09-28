"""03 現在地補正サービスの起動・入力受付・位置配信。"""

from __future__ import annotations

import argparse
import signal
import threading
import time
from pathlib import Path
from typing import Any

from navigation.json_ipc import JsonLineServer
from navigation.resident_runtime import ProcessLock
from positioning.config import ConfigError, PositioningConfig, load_config
from positioning.estimator import PositionEstimator
from positioning.inputs import GpsdInput, InputAdapter, InputError
from positioning.publisher import PositionPublisher
from positioning.calibration import SensorCalibration
from positioning.time_aligner import TimeAligner


class PositionCorrectionService:
    """観測受付と推定状態の更新を1本のロックで直列化する。"""

    def __init__(self, config: PositioningConfig, socket_path: str | None = None) -> None:
        """観測入力、時間整列、位置推定、通知、停止制御を初期化する。"""
        self.config = config
        self.socket_path = socket_path or config.socket_path
        self.adapter = InputAdapter()
        self.aligner = TimeAligner(config.reorder_delay_ms)
        self.calibration = SensorCalibration()
        self.estimator = PositionEstimator(config, self.calibration)
        self.server: JsonLineServer | None = None
        self.publisher: PositionPublisher | None = None
        self.gpsd: GpsdInput | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._ticker: threading.Thread | None = None
        self._last_invalid_reason = ""
        self._display_position: dict[str, Any] | None = None
        self._display_deadline = 0.0
        self._has_fix_history = False
        self._last_status_monotonic = float("-inf")

    def start(self) -> None:
        """位置IPC、推定周期、GPS入力を起動して要求受付を開始する。"""
        self.server = JsonLineServer(self.socket_path, self.handle_request)
        self.publisher = PositionPublisher(self.config.source, self._publish, self.config.output_max_rate_hz)
        if self.config.calibration_path:
            profile = self.calibration.load_calibration(self.config.calibration_path, self.config.imu_device_id)
            if not profile.valid:
                self._publish_status("GPS_ONLY", profile.reason)
        self._ticker = threading.Thread(target=self._run_tick, name="position-correction-tick", daemon=True)
        self._ticker.start()
        if self.config.gpsd_enabled:
            self.gpsd = GpsdInput(self.config.gpsd_host, self.config.gpsd_port, self.submit_observation)
            self.gpsd.start()
        self._publish_status("NO_FIX", "有効なGPSを待っています")
        try:
            self.server.serve_forever()
        finally:
            self.stop()

    def stop(self) -> None:
        """GPS入力、IPC、周期処理を停止し、再起動可能な状態へ戻す。"""
        if self._stop.is_set():
            return
        self._stop.set()
        if self.gpsd is not None:
            self.gpsd.stop()
        if self.server is not None:
            self.server.stop()
        if self._ticker is not None:
            self._ticker.join(timeout=2.0)
            self._ticker = None

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """04 Linux位置情報連携と開発用入力からのJSON要求を処理する。"""
        operation = str(request.get("operation", request.get("type", "")))
        if operation in {"hello", "status", "get_status"}:
            return {"accepted": True, "success": True, **self.status()}
        if operation in {"observation", "submit_observation"}:
            try:
                events = self.submit_observation(dict(request.get("observation", request)))
            except (InputError, TypeError, ValueError) as exc:
                return {"accepted": False, "success": False, "reason": str(exc)}
            return {"accepted": True, "success": True, "state": "PROCESSED", "events": events}
        if operation == "invalidate":
            reason = str(request.get("reason", "外部要求で無効化しました"))
            with self._lock:
                event = self._publish_invalidate(reason)
            return {"accepted": True, "success": True, "state": "INVALID", "event": event}
        if operation == "stop":
            self.stop()
            return {"accepted": True, "success": True, "state": "STOPPING"}
        return {"accepted": False, "success": False, "reason": "未対応の操作です"}

    def submit_observation(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """観測を正規化し、時間整列後に推定・配信する。"""
        with self._lock:
            observation = self.adapter.normalize_observation(payload)
            self.aligner.add(observation)
            return self._process_ready(flush=False)

    def status(self) -> dict[str, Any]:
        """品質判定済み位置の残り寿命から状態を作り、新しい連番を付ける。"""
        with self._lock:
            state = self.estimator.state
            publisher = self.publisher
            last_gps_age_ms = self._last_gps_age_ms()
            now = time.monotonic()
            remaining_ms = max(0, int((self._display_deadline - now) * 1000))
            position_state = "NO_FIX"
            if self._display_position is not None and remaining_ms > 0:
                position_state = "DR_ACTIVE" if self._display_position["method"] == "DEAD_RECKONING" else "GPS_ACTIVE"
            if publisher:
                publisher.sequence += 1
            return {
                "event": "position.status",
                "schema_version": 1,
                "source": self.config.source,
                "boot_id": publisher.boot_id if publisher else "",
                "sequence": publisher.sequence if publisher else 0,
                "issued_monotonic": now,
                "position_valid_for_ms": remaining_ms if position_state != "NO_FIX" else 0,
                "has_fix_history": self._has_fix_history,
                "state": position_state,
                "dead_reckoning_enabled": self.config.dead_reckoning_enabled,
                "initialized": state.initialized,
                "heading_valid": state.heading_rad is not None,
                "motion": state.motion.value,
                "last_gps_age_ms": last_gps_age_ms,
                "reason": self._last_invalid_reason,
            }

    def _run_tick(self) -> None:
        """一定周期で整列済み観測を処理し、状態通知を定期発行する。"""
        interval = 1.0 / self.config.fusion_rate_hz
        while not self._stop.wait(interval):
            with self._lock:
                self._process_ready(flush=False)
                now = time.monotonic()
                if now - self._last_status_monotonic >= 1.0:
                    self._publish(self.status())
                    self._last_status_monotonic = now

    def _process_ready(self, flush: bool) -> list[dict[str, Any]]:
        """時刻整列済み観測を推定器へ渡し、位置または無効化イベントを作る。"""
        batch = self.aligner.align_observations(flush=flush)
        if not batch.observations and self.estimator.state.last_gps is None:
            return []
        estimate = self.estimator.process_cycle(batch)
        if estimate is None:
            age = self._last_gps_age_ms()
            if age is None:
                self._maybe_invalidate("有効なGPS基準がありません")
            elif age > self.config.gps_max_age_ms:
                self._maybe_invalidate("GPS測定が期限切れです")
            return []
        decision = self.estimator.quality.evaluate_quality(
            estimate.method,
            estimate.horizontal_accuracy_m,
            estimate.age_ms_at_send,
            self.estimator.state.predicted_elapsed_s,
            self.estimator.state.predicted_distance_m,
            inputs_valid=estimate.method == "GPS" or self.estimator.state.motion.value in {"FORWARD", "REVERSE", "STOPPED"},
            calibration_valid=self.calibration.profile.valid or estimate.method == "GPS",
        )
        if not decision.publishable:
            self._maybe_invalidate(decision.reason)
            return []
        self._last_invalid_reason = ""
        if not self.publisher:
            return []
        published = self.publisher.publish_position(estimate, display_valid_for_ms=decision.valid_for_ms)
        if published is not None:
            self._has_fix_history = True
            self._display_position = published
            self._display_deadline = published["issued_monotonic"] + published["display_valid_for_ms"] / 1000
        return [published] if published else []

    def _maybe_invalidate(self, reason: str = "位置を利用できません") -> None:
        """同じ理由の重複通知を避けながら、位置無効化を必要時だけ発行する。"""
        if not reason:
            reason = "位置を利用できません"
        if reason == self._last_invalid_reason:
            return
        self._publish_invalidate(reason)

    def _publish_invalidate(self, reason: str) -> dict[str, Any]:
        """表示用位置を消去し、最後の有効履歴を含む無効化イベントを作る。"""
        self._last_invalid_reason = reason
        self._display_position = None
        self._display_deadline = 0.0
        return self.publisher.invalidate_position(reason, has_fix_history=self._has_fix_history) if self.publisher else {"event": "position.invalidate", "reason": reason}

    def _publish_status(self, state: str, reason: str = "") -> None:
        """GPS補正の状態と現在のGPS鮮度を通知する。"""
        with self._lock:
            if self.publisher:
                self.publisher.publish_status(state, reason, last_gps_age_ms=self._last_gps_age_ms(),
                                              has_fix_history=self._has_fix_history,
                                              position_valid_for_ms=0)

    def _publish(self, event: dict[str, Any]) -> None:
        """PositionCorrectionServiceの_publishの内部処理を実行する。"""
        if self.server is not None:
            self.server.publish(event)

    def _last_gps_age_ms(self) -> int | None:
        """PositionCorrectionServiceの_last_gps_age_msの内部処理を実行する。"""
        last = self.estimator.state.last_gps
        if last is None:
            return None
        return max(0, int((time.monotonic() - last.observed_monotonic) * 1000) + last.age_ms_at_send)


def main() -> int:
    """引数を解釈して処理を開始する。"""
    parser = argparse.ArgumentParser(description="03 現在地補正サービス")
    parser.add_argument("--config", type=Path, required=True, help="positioning.toml")
    parser.add_argument("--socket", help="設定を上書きするUnixソケット")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except (OSError, ConfigError, ValueError) as exc:
        parser.error(str(exc))
    lock = ProcessLock(config.lock_path)
    try:
        lock.acquire()
    except RuntimeError as exc:
        parser.error(str(exc))
    service = PositionCorrectionService(config, args.socket)
    signal.signal(signal.SIGTERM, lambda *_: service.stop())
    signal.signal(signal.SIGINT, lambda *_: service.stop())
    try:
        service.start()
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
