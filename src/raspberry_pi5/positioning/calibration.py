"""IMU取付方向とジャイロゼロ点の校正。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Observation


@dataclass(frozen=True)
class CalibrationProfile:
    device_id: str
    version: str
    axis_matrix: tuple[tuple[float, float, float], ...]
    gyro_bias_z: float
    valid: bool
    reason: str = ""


class SensorCalibration:
    def __init__(self) -> None:
        """未校正状態とIMUの初期単位行列を設定する。"""
        self.profile = CalibrationProfile("", "", _identity(), 0.0, False, "未校正")

    def load_calibration(self, path: str | Path, expected_device: str | None = None) -> CalibrationProfile:
        """校正JSONを読み込み、機器ID・行列・バイアスを検証する。"""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            matrix = tuple(tuple(float(value) for value in row) for row in data["axis_matrix"])
            profile = CalibrationProfile(
                device_id=str(data["device_id"]),
                version=str(data["version"]),
                axis_matrix=matrix,
                gyro_bias_z=float(data.get("gyro_bias_z", 0.0)),
                valid=True,
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.profile = CalibrationProfile("", "", _identity(), 0.0, False, f"校正値を読めません: {exc}")
            return self.profile
        if expected_device and profile.device_id != expected_device:
            self.profile = CalibrationProfile(profile.device_id, profile.version, profile.axis_matrix, profile.gyro_bias_z, False, "機器IDが一致しません")
        elif not _valid_matrix(profile.axis_matrix) or not math.isfinite(profile.gyro_bias_z):
            self.profile = CalibrationProfile(profile.device_id, profile.version, profile.axis_matrix, profile.gyro_bias_z, False, "取付行列またはバイアスが不正です")
        else:
            self.profile = profile
        return self.profile

    def calibrate_stationary(self, observations: list[Observation], stopped: bool) -> dict[str, Any]:
        """停止中の角速度サンプルからゼロ点候補を計算する。"""
        if not stopped or len(observations) < 10:
            return {"valid": False, "reason": "静止条件または観測件数が不足しています"}
        values = [float(item.values["yaw_rate_rad_s"]) for item in observations]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        if variance > 0.05**2:
            return {"valid": False, "reason": "静止中の角速度ばらつきが大きすぎます", "variance": variance}
        return {"valid": True, "gyro_bias_z": mean, "variance": variance, "requires_review": True}

    def apply_imu(self, observation: Observation) -> dict[str, float]:
        """校正済みのIMUバイアスを観測値へ適用する。"""
        if not self.profile.valid:
            return dict(observation.values)
        values = observation.values
        return {"yaw_rate_rad_s": float(values["yaw_rate_rad_s"]) - self.profile.gyro_bias_z}


def _identity() -> tuple[tuple[float, float, float], ...]:
    """3軸に補正を加えない単位行列を返す。"""
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _valid_matrix(matrix: tuple[tuple[float, float, float], ...]) -> bool:
    """IMU取付行列が3行3列の有限値で、軸長条件を満たすか検証する。"""
    if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
        return False
    values = [value for row in matrix for value in row]
    if not all(math.isfinite(value) for value in values):
        return False
    for row in matrix:
        if abs(sum(value * value for value in row) - 1.0) > 0.05:
            return False
    return True
