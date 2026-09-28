"""左右ウィンカーGPIOの安定判定と点滅活動推定。"""

from __future__ import annotations

import time

from subcontroller_common import DebouncedSignal, InputDriver


class TurnSignalMonitor:
    def __init__(self, driver: InputDriver, gpio: int, side: str, active_level: int, poll_ms: int, debounce_ms: int, max_read_gap_ms: int, activity_hold_ms: int, max_on_ms: int) -> None:
        """設定と内部状態を初期化する。"""
        self.driver = driver
        self.gpio = gpio
        self.side = side
        self.poll_ms = poll_ms
        self.max_read_gap_ms = max_read_gap_ms
        self.activity_hold_ms = activity_hold_ms
        self.max_on_ms = max_on_ms
        self.signal = DebouncedSignal(active_level, debounce_ms)
        self.last_raw: int | None = None
        self.last_lamp: bool | None = None
        self.lamp_on_started_ms: int | None = None
        self.hold_until_ms: int | None = None
        self.sample_sequence = 0

    def read(self) -> dict:
        """入力値を読み取り、利用可能な形式で返す。"""
        now = int(time.monotonic() * 1000)
        try:
            raw = int(self.driver.read(self.gpio))
        except Exception as exc:  # noqa: BLE001
            self.invalidate()
            return self._snapshot(None, None, now, "GPIO_READ_ERROR", str(exc))
        self.last_raw = raw
        lamp = self.signal.update(raw, now)
        if lamp is not None and lamp != self.last_lamp:
            if lamp:
                self.lamp_on_started_ms = now
            elif self.lamp_on_started_ms is not None:
                self.hold_until_ms = now + self.activity_hold_ms
            self.last_lamp = lamp
        self.sample_sequence += 1
        return self._snapshot(lamp, self._activity(lamp, now), now, "STABLE" if lamp is not None else "DEBOUNCING")

    def invalidate(self) -> None:
        """保持中の値を無効化する。"""
        self.signal.invalidate()
        self.last_lamp = None
        self.lamp_on_started_ms = None
        self.hold_until_ms = None

    def _activity(self, lamp: bool | None, now: int) -> str | None:
        """TurnSignalMonitorの_activityの内部処理を実行する。"""
        if lamp is None:
            return None
        if lamp:
            if self.lamp_on_started_ms is not None and now - self.lamp_on_started_ms > self.max_on_ms:
                return None
            return "ACTIVE"
        if self.hold_until_ms is not None and now < self.hold_until_ms:
            return "ACTIVE"
        return "INACTIVE"

    def _snapshot(self, lamp: bool | None, activity: str | None, now: int, reason: str, error: str | None = None) -> dict:
        """TurnSignalMonitorの_snapshotの内部処理を実行する。"""
        return {
            "side": self.side,
            "lamp": lamp,
            "activity": activity,
            "raw_level": self.last_raw,
            "sample_sequence": self.sample_sequence,
            "observed_at_monotonic_ms": now,
            "valid_for_ms": self.max_read_gap_ms,
            "reason": error or reason,
        }
