"""位置の品質と推定継続条件を判定する。"""

from __future__ import annotations

import math

from .config import PositioningConfig
from .models import QualityDecision, Validity


class QualityEvaluator:
    def __init__(self, config: PositioningConfig) -> None:
        """GPSと推測航法の採用条件を設定として保持する。"""
        self.config = config

    def evaluate_quality(
        self,
        method: str,
        accuracy_m: float,
        age_ms: int,
        elapsed_s: float = 0.0,
        distance_m: float = 0.0,
        inputs_valid: bool = True,
        calibration_valid: bool = True,
    ) -> QualityDecision:
        """精度、鮮度、推定経過時間から公開可否と有効期限を判定する。"""
        if not math.isfinite(accuracy_m) or accuracy_m <= 0:
            return QualityDecision(False, Validity.FAULT, None, 0, "水平精度が不正です")
        if method == "GPS":
            if age_ms > self.config.gps_max_age_ms:
                return QualityDecision(False, Validity.STALE, accuracy_m, 0, "GPS測定が期限切れです")
            if accuracy_m > self.config.gps_max_accuracy_m:
                return QualityDecision(False, Validity.DEGRADED, accuracy_m, 0, "GPS精度が採用範囲外です")
            return QualityDecision(True, Validity.VALID, accuracy_m, max(1, self.config.gps_max_age_ms - age_ms), "")
        if not self.config.dead_reckoning_enabled:
            return QualityDecision(False, Validity.UNKNOWN, accuracy_m, 0, "推測航法が無効です")
        if not inputs_valid:
            return QualityDecision(False, Validity.UNKNOWN, accuracy_m, 0, "推定入力が不足しています")
        if not calibration_valid:
            return QualityDecision(False, Validity.UNKNOWN, accuracy_m, 0, "IMU校正が無効です")
        if elapsed_s > self.config.dr_max_elapsed_s:
            return QualityDecision(False, Validity.STALE, accuracy_m, 0, "GPS喪失時間の上限を超えました")
        if distance_m > self.config.dr_max_distance_m:
            return QualityDecision(False, Validity.STALE, accuracy_m, 0, "補正移動距離の上限を超えました")
        if accuracy_m > self.config.dr_max_error_guard_m:
            return QualityDecision(False, Validity.DEGRADED, accuracy_m, 0, "推定誤差の上限を超えました")
        return QualityDecision(True, Validity.DEGRADED, accuracy_m, max(1, self.config.gps_max_age_ms), "")
