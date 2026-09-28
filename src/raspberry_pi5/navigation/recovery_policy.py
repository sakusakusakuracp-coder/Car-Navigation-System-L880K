"""原因別の再試行回数と待機時間を管理する。"""

from __future__ import annotations

import time
from collections import defaultdict


class RecoveryPolicy:
    def __init__(self, max_attempts: int = 3, base_delay: float = 2.0, max_delay: float = 30.0) -> None:
        """設定と内部状態を初期化する。"""
        if max_attempts < 0 or base_delay <= 0 or max_delay < base_delay:
            raise ValueError("再試行設定が不正です")
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._attempts: defaultdict[str, int] = defaultdict(int)
        self._next_allowed: dict[str, float] = {}

    def schedule(self, reason: str, now: float | None = None) -> bool:
        """再試行または次回処理を予定へ登録する。"""
        now = time.monotonic() if now is None else now
        if self._attempts[reason] >= self.max_attempts or now < self._next_allowed.get(reason, 0):
            return False
        self._attempts[reason] += 1
        delay = min(self.max_delay, self.base_delay * (2 ** (self._attempts[reason] - 1)))
        self._next_allowed[reason] = now + delay
        return True

    def reset(self, reason: str) -> None:
        """管理状態を初期値へ戻す。"""
        self._attempts.pop(reason, None)
        self._next_allowed.pop(reason, None)
