"""現在地補正の設定読み込みと範囲検証。"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """設定の型または範囲が不正。"""


@dataclass(frozen=True)
class PositioningConfig:
    source: str = "03 現在地補正"
    socket_path: str = "/run/user/1000/l880k-position-correction.sock"
    lock_path: str = "/run/user/1000/l880k-position-correction.lock"
    calibration_path: str | None = None
    imu_device_id: str | None = None
    dead_reckoning_enabled: bool = False
    fusion_rate_hz: float = 50.0
    output_max_rate_hz: float = 5.0
    reorder_delay_ms: int = 100
    imu_max_age_ms: int = 200
    speed_max_age_ms: int = 500
    reverse_max_age_ms: int = 500
    gps_max_age_ms: int = 2000
    max_prediction_gap_ms: int = 100
    max_clock_uncertainty_ms: int = 50
    dr_max_elapsed_s: float = 10.0
    dr_max_distance_m: float = 200.0
    dr_max_error_guard_m: float = 25.0
    gps_max_accuracy_m: float = 50.0
    gps_recovery_count: int = 3
    gps_recovery_window_s: float = 5.0
    gps_recovery_jump_m: float = 80.0
    stationary_speed_mps: float = 0.3
    initial_heading_speed_mps: float = 2.0
    gpsd_enabled: bool = True
    gpsd_host: str = "127.0.0.1"
    gpsd_port: int = 2947


def load_config(path: str | Path | None = None) -> PositioningConfig:
    """TOML設定を読み込み、GPS単独設定と補正設定を分けて検証する。"""
    data: dict[str, Any] = {}
    if path is not None:
        config_path = Path(path)
        with config_path.open("rb") as stream:
            data = tomllib.load(stream)
    service = data.get("service", {})
    correction = data.get("correction", {})
    gpsd = data.get("gpsd", {})
    values = {
        "source": service.get("source", PositioningConfig.source),
        "socket_path": service.get("socket_path", PositioningConfig.socket_path),
        "lock_path": service.get("lock_path", PositioningConfig.lock_path),
        "calibration_path": correction.get("calibration_path"),
        "imu_device_id": correction.get("imu_device_id"),
        "dead_reckoning_enabled": correction.get("dead_reckoning_enabled", False),
        **{field: correction.get(field, getattr(PositioningConfig, field)) for field in (
            "fusion_rate_hz", "output_max_rate_hz", "reorder_delay_ms", "imu_max_age_ms",
            "speed_max_age_ms", "reverse_max_age_ms", "gps_max_age_ms",
            "max_prediction_gap_ms", "max_clock_uncertainty_ms", "dr_max_elapsed_s",
            "dr_max_distance_m", "dr_max_error_guard_m", "gps_max_accuracy_m",
            "gps_recovery_count", "gps_recovery_window_s", "gps_recovery_jump_m",
            "stationary_speed_mps", "initial_heading_speed_mps",
        )},
        "gpsd_enabled": gpsd.get("enabled", True),
        "gpsd_host": gpsd.get("host", PositioningConfig.gpsd_host),
        "gpsd_port": gpsd.get("port", PositioningConfig.gpsd_port),
    }
    config = PositioningConfig(**values)
    _validate(config)
    return config


def _validate(config: PositioningConfig) -> None:
    """_validateの内部処理を実行する。"""
    if not config.socket_path or not config.lock_path:
        raise ConfigError("ソケットとロックのパスは必須です")
    positive = (
        "fusion_rate_hz", "output_max_rate_hz", "dr_max_elapsed_s", "dr_max_distance_m",
        "dr_max_error_guard_m", "gps_max_accuracy_m", "gps_recovery_window_s",
        "gps_recovery_jump_m", "stationary_speed_mps", "initial_heading_speed_mps",
    )
    for name in positive:
        value = float(getattr(config, name))
        if not math.isfinite(value) or value <= 0:
            raise ConfigError(f"{name}は正の有限値が必要です")
    for name in (
        "reorder_delay_ms", "imu_max_age_ms", "speed_max_age_ms", "reverse_max_age_ms",
        "gps_max_age_ms", "max_prediction_gap_ms", "max_clock_uncertainty_ms",
    ):
        if int(getattr(config, name)) <= 0:
            raise ConfigError(f"{name}は正の整数が必要です")
    if config.gps_recovery_count < 2:
        raise ConfigError("gps_recovery_countは2以上が必要です")
    if not (1 <= int(config.gpsd_port) <= 65535):
        raise ConfigError("gpsd_portが範囲外です")
