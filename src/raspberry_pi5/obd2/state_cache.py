"""車両項目を個別の有効期限付きで保持する。"""

from __future__ import annotations

from time import monotonic

from .models import CachedValue, Validity, VehicleSample


class VehicleStateCache:
    def __init__(self) -> None:
        """車両項目をキーごとに保持する空のキャッシュを作る。"""
        self._values: dict[str, CachedValue] = {}

    def apply_sample(self, sample: VehicleSample) -> CachedValue:
        """新しい測定値を有効値としてキャッシュへ登録する。"""
        value = CachedValue(sample=sample, validity=Validity.VALID)
        self._values[sample.key] = value
        return value

    def expire_values(self, *, now: float | None = None, reason: str = "期限切れ") -> list[CachedValue]:
        """項目ごとのvalid_for_msを確認し、期限切れ値を無効化する。"""
        now = monotonic() if now is None else now
        expired: list[CachedValue] = []
        for key, current in list(self._values.items()):
            if current.validity != Validity.VALID:
                continue
            if now - current.sample.acquired_mono >= current.sample.valid_for_ms / 1000.0:
                invalid = CachedValue(current.sample, Validity.STALE, reason)
                self._values[key] = invalid
                expired.append(invalid)
        return expired

    def invalidate_all(self, reason: str = "通信切断") -> list[CachedValue]:
        """保持中の全項目を無効化し、無効化された値を返す。"""
        invalidated: list[CachedValue] = []
        for key, current in list(self._values.items()):
            invalid = CachedValue(current.sample, Validity.STALE, reason)
            self._values[key] = invalid
            invalidated.append(invalid)
        return invalidated

    def invalidate_keys(self, keys: set[str], reason: str) -> list[CachedValue]:
        """取得モード変更時に、対象項目だけを使用不可にする。"""
        invalidated: list[CachedValue] = []
        for key in keys:
            current = self._values.get(key)
            if current is None or current.validity != Validity.VALID:
                continue
            invalid = CachedValue(current.sample, Validity.STALE, reason)
            self._values[key] = invalid
            invalidated.append(invalid)
        return invalidated

    def latest(self) -> dict[str, CachedValue]:
        """現在保持している項目のスナップショットを返す。"""
        return dict(self._values)
