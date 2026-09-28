"""観測を測定時刻順に並べる有限バッファ。"""

from __future__ import annotations

from dataclasses import dataclass
import time

from .models import FusionBatch, Observation


class TimeAligner:
    def __init__(self, reorder_delay_ms: int, max_buffer_size: int = 512) -> None:
        """到着順の揺らぎを吸収する観測バッファを初期化する。"""
        self.reorder_delay_s = reorder_delay_ms / 1000.0
        self.max_buffer_size = max_buffer_size
        self._buffer: list[Observation] = []
        self._last_processed = float("-inf")

    def add(self, observation: Observation) -> None:
        """未処理の観測を測定時刻順のバッファへ追加する。"""
        if observation.observed_monotonic <= self._last_processed:
            return
        self._buffer.append(observation)
        self._buffer.sort(key=lambda item: item.observed_monotonic)
        if len(self._buffer) > self.max_buffer_size:
            del self._buffer[: len(self._buffer) - self.max_buffer_size]

    def align_observations(self, now: float | None = None, flush: bool = False) -> FusionBatch:
        """処理可能時刻に達した観測を取り出し、不足する入力種別も記録する。"""
        now = time.monotonic() if now is None else now
        cutoff = now if flush else now - self.reorder_delay_s
        ready = [item for item in self._buffer if item.observed_monotonic <= cutoff]
        self._buffer = [item for item in self._buffer if item.observed_monotonic > cutoff]
        ready = [item for item in ready if item.observed_monotonic > self._last_processed]
        ready.sort(key=lambda item: (item.observed_monotonic, item.kind, item.measurement_id))
        if ready:
            self._last_processed = ready[-1].observed_monotonic
        present = {item.kind for item in ready}
        missing = tuple(kind for kind in ("gps", "speed", "imu", "reverse") if kind not in present)
        return FusionBatch(tuple(ready), cutoff, missing)
