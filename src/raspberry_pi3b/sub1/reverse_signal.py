"""バックギア信号の読取と安定判定。"""

from __future__ import annotations

import time

from subcontroller_common import DebouncedSignal, InputDriver


class ReverseSignalMonitor:
    """保護済み3.3V入力をON/OFF/UNKNOWNへ変換する。"""

    def __init__(self, driver: InputDriver, gpio: int, active_level: int = 1, poll_ms: int = 50, debounce_ms: int = 100, max_read_gap_ms: int = 500) -> None:
        """設定と内部状態を初期化する。"""
        self.driver = driver
        self.gpio = gpio
        self.poll_ms = poll_ms
        self.max_read_gap_ms = max_read_gap_ms
        self.signal = DebouncedSignal(active_level, debounce_ms)
        self.last_raw: int | None = None
        self.sample_sequence = 0

    def read(self) -> dict:
        """入力値を読み取り、利用可能な形式で返す。"""
        now = int(time.monotonic() * 1000)
        try:
            raw = int(self.driver.read(self.gpio))
        except Exception as exc:  # noqa: BLE001
            self.signal.invalidate()
            return self._snapshot(None, "GPIO_READ_ERROR", now, str(exc))
        self.last_raw = raw
        stable = self.signal.update(raw, now)
        self.sample_sequence += 1
        return self._snapshot(stable, "STABLE" if stable is not None else "DEBOUNCING", now)

    def check_freshness(self) -> dict | None:
        """ReverseSignalMonitorのcheck_freshnessの内部処理を実行する。"""
        now = int(time.monotonic() * 1000)
        if self.signal.last_read_ms is None or now - self.signal.last_read_ms <= self.max_read_gap_ms:
            return None
        self.signal.invalidate()
        return self._snapshot(None, "READ_GAP_EXCEEDED", now)

    def _snapshot(self, stable: bool | None, reason: str, now: int, error: str | None = None) -> dict:
        """ReverseSignalMonitorの_snapshotの内部処理を実行する。"""
        return {
            "reverse": stable,
            "reverse_valid": stable is not None,
            "raw_level": self.last_raw,
            "sample_sequence": self.sample_sequence,
            "observed_at_monotonic_ms": now,
            "valid_for_ms": self.max_read_gap_ms,
            "reason": error or reason,
        }
