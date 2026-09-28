"""GPS採用と短時間の車速・IMU推定を直列に処理する。"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass

from .calibration import SensorCalibration
from .config import PositioningConfig
from .coordinates import LocalFrame, bearing_from_velocity, normalize_angle, normalize_bearing
from .models import FusionBatch, MotionState, Observation, PositionEstimate, Validity
from .quality import QualityEvaluator


@dataclass
class _State:
    frame: LocalFrame | None = None
    origin_id: str = ""
    east_m: float = 0.0
    north_m: float = 0.0
    heading_rad: float | None = None
    signed_speed_mps: float = 0.0
    last_process_monotonic: float | None = None
    last_gps: Observation | None = None
    last_gps_monotonic: float | None = None
    last_gps_lost_monotonic: float | None = None
    predicted_distance_m: float = 0.0
    predicted_elapsed_s: float = 0.0
    motion: MotionState = MotionState.UNKNOWN
    estimate_id: str = ""
    initialized: bool = False
    dr_active: bool = False
    recovery: list[Observation] | None = None
    discontinuity: bool = False


class PositionEstimator:
    """推定状態を1つの処理経路で更新する。"""

    def __init__(self, config: PositioningConfig, calibration: SensorCalibration | None = None) -> None:
        """GPS、速度、IMU観測を統合する推定器の状態を初期化する。"""
        self.config = config
        self.calibration = calibration or SensorCalibration()
        self.quality = QualityEvaluator(config)
        self.state = _State()
        self._latest: dict[str, Observation] = {}

    def initialize_estimate(self, gps: Observation, initial_heading_deg: float | None = None) -> bool:
        """GPS基準と初期方位を準備する。方位なしでもGPS出力は継続可能。"""
        values = gps.values
        self.state.frame = LocalFrame(values["latitude_deg"], values["longitude_deg"])
        self.state.origin_id = uuid.uuid4().hex
        self.state.east_m = 0.0
        self.state.north_m = 0.0
        heading = initial_heading_deg
        if heading is None and values.get("bearing_deg") is not None and (values.get("speed_mps") or 0.0) >= self.config.initial_heading_speed_mps:
            heading = float(values["bearing_deg"])
        self.state.heading_rad = math.radians(heading) if heading is not None else None
        self.state.last_gps = gps
        self.state.last_gps_monotonic = gps.observed_monotonic
        self.state.last_process_monotonic = gps.observed_monotonic
        self.state.estimate_id = uuid.uuid4().hex
        self.state.initialized = True
        self.state.dr_active = False
        self.state.recovery = []
        return self.state.heading_rad is not None

    def process_cycle(self, batch: FusionBatch, now: float | None = None) -> PositionEstimate | None:
        """新しい観測を取り込み、GPS優先または条件付き推測航法の結果を作る。"""
        now = time.monotonic() if now is None else now
        for observation in batch.observations:
            self._latest[observation.kind] = observation
        gps = self._latest.get("gps")
        if gps is not None and gps.validity in {Validity.VALID, Validity.DEGRADED} and self._is_new_gps(gps):
            return self.update_with_gps(gps, now)
        if not self.state.initialized or self.state.last_gps is None:
            return None
        if self.state.last_gps_lost_monotonic is None:
            self.state.last_gps_lost_monotonic = now
            self.state.recovery = []
        if not self.config.dead_reckoning_enabled or self.state.heading_rad is None:
            return None
        motion = self.determine_motion(now)
        if motion not in {MotionState.FORWARD, MotionState.REVERSE, MotionState.STOPPED}:
            return None
        current = self._latest.get("speed")
        imu = self._latest.get("imu")
        if current is None or imu is None or not self._fresh(current, self.config.speed_max_age_ms, now) or not self._fresh(imu, self.config.imu_max_age_ms, now):
            return None
        process_time = max(self.state.last_process_monotonic or now, now)
        dt = max(0.0, process_time - (self.state.last_process_monotonic or process_time))
        if dt > self.config.max_prediction_gap_ms / 1000.0:
            return None
        self.predict_motion(dt, imu)
        self.state.last_process_monotonic = process_time
        self.state.predicted_elapsed_s = now - (self.state.last_gps_lost_monotonic or now)
        self.state.dr_active = True
        accuracy = min(self.config.dr_max_error_guard_m * 2, self.state.last_gps.values["horizontal_accuracy_m"] + self.state.predicted_distance_m * 0.08 + self.state.predicted_elapsed_s * 0.5)
        age_ms = max(0, int((now - self.state.last_gps.observed_monotonic) * 1000) + self.state.last_gps.age_ms_at_send)
        estimate = self._build_estimate("DEAD_RECKONING", accuracy, age_ms, now, Validity.DEGRADED)
        return estimate

    def determine_motion(self, now: float | None = None) -> MotionState:
        """速度とリバース信号から停止・前進・後退の状態を判定する。"""
        now = time.monotonic() if now is None else now
        reverse = self._latest.get("reverse")
        speed = self._latest.get("speed")
        if reverse is None or not self._fresh(reverse, self.config.reverse_max_age_ms, now):
            self.state.motion = MotionState.UNKNOWN
            return self.state.motion
        if speed is None or not self._fresh(speed, self.config.speed_max_age_ms, now):
            self.state.motion = MotionState.UNKNOWN
            return self.state.motion
        speed_mps = float(speed.values["speed_mps"])
        if speed_mps < self.config.stationary_speed_mps:
            self.state.motion = MotionState.STOPPED
        elif reverse.values["state"] == "ON":
            self.state.motion = MotionState.REVERSE
        elif reverse.values["state"] == "OFF":
            self.state.motion = MotionState.FORWARD
        else:
            self.state.motion = MotionState.UNKNOWN
        if self.state.motion == MotionState.REVERSE:
            self.state.signed_speed_mps = -speed_mps
        elif self.state.motion == MotionState.FORWARD:
            self.state.signed_speed_mps = speed_mps
        else:
            self.state.signed_speed_mps = 0.0
        return self.state.motion

    def predict_motion(self, dt: float, imu: Observation) -> None:
        """速度と角速度を用いて、GPS断の短時間だけ位置と方位を予測する。"""
        if self.state.heading_rad is None:
            raise ValueError("初期方位がありません")
        speed = 0.0 if self.state.motion == MotionState.STOPPED else self.state.signed_speed_mps
        yaw_rate = self.calibration.apply_imu(imu).get("yaw_rate_rad_s", 0.0)
        next_heading = normalize_angle(self.state.heading_rad + yaw_rate * dt)
        midpoint = normalize_angle(self.state.heading_rad + yaw_rate * dt / 2.0)
        distance = speed * dt
        self.state.east_m += distance * math.sin(midpoint)
        self.state.north_m += distance * math.cos(midpoint)
        self.state.heading_rad = next_heading
        self.state.predicted_distance_m += abs(distance)

    def update_with_gps(self, gps: Observation, now: float | None = None) -> PositionEstimate | None:
        """GPS観測で推定位置を更新し、復帰時は複数観測の整合性を確認する。"""
        now = time.monotonic() if now is None else now
        previous_dr = self.state.dr_active
        if not self.state.initialized:
            self.initialize_estimate(gps)
        elif previous_dr:
            self.state.recovery = (self.state.recovery or []) + [gps]
            if len(self.state.recovery) < self.config.gps_recovery_count:
                return None
            if not self._recovery_is_consistent():
                self.state.recovery = self.state.recovery[-1:]
                return None
            self.state.discontinuity = self._distance_to_gps(gps) > self.config.gps_recovery_jump_m
            self._reanchor(gps)
        else:
            self._reanchor(gps, preserve_heading=True)
        accuracy = float(gps.values["horizontal_accuracy_m"])
        age_ms = max(0, int((now - gps.observed_monotonic) * 1000) + gps.age_ms_at_send)
        decision = self.quality.evaluate_quality("GPS", accuracy, age_ms)
        if not decision.publishable:
            return None
        self.state.last_gps = gps
        self.state.last_gps_monotonic = gps.observed_monotonic
        self.state.last_gps_lost_monotonic = None
        self.state.predicted_distance_m = 0.0
        self.state.predicted_elapsed_s = 0.0
        self.state.dr_active = False
        estimate = self._build_estimate("GPS", accuracy, age_ms, now, decision.validity)
        self.state.discontinuity = False
        return estimate

    def handle_gps_recovery(self, gps: Observation) -> str:
        """GPS復帰候補を蓄積し、復帰確定または確認継続を判定する。"""
        self.state.recovery = (self.state.recovery or []) + [gps]
        if len(self.state.recovery) < self.config.gps_recovery_count:
            return "CONFIRMING"
        return "RECOVERED" if self._recovery_is_consistent() else "CONFIRMING"

    def _build_estimate(self, method: str, accuracy: float, age_ms: int, now: float, validity: Validity) -> PositionEstimate:
        """内部座標を外部へ出す位置推定結果へ変換する。"""
        assert self.state.frame is not None
        latitude, longitude = self.state.frame.to_geodetic(self.state.east_m, self.state.north_m)
        speed = abs(self.state.signed_speed_mps) if method == "DEAD_RECKONING" else _gps_speed(self.state.last_gps)
        bearing = None
        if self.state.heading_rad is not None and speed is not None and speed >= self.config.stationary_speed_mps:
            bearing = normalize_bearing(math.degrees(self.state.heading_rad))
        return PositionEstimate(
            latitude_deg=latitude,
            longitude_deg=longitude,
            horizontal_accuracy_m=accuracy,
            speed_mps=speed,
            speed_valid=speed is not None,
            bearing_deg=bearing,
            bearing_valid=bearing is not None,
            method=method,
            publishable=True,
            validity=validity,
            observed_at_utc=self.state.last_gps.observed_at_utc if self.state.last_gps else None,
            age_ms_at_send=age_ms,
            valid_for_ms=max(1, self.config.gps_max_age_ms - age_ms),
            last_gps_age_ms=age_ms if method == "DEAD_RECKONING" else 0,
            estimate_id=self.state.estimate_id,
            origin_id=self.state.origin_id,
            discontinuity=self.state.discontinuity,
            input_refs=tuple(item.measurement_id for item in self._latest.values()),
        )

    def _reanchor(self, gps: Observation, preserve_heading: bool = False) -> None:
        """GPSを新しい基準点として座標系を張り直し、推定誤差の累積を抑える。"""
        old_heading = self.state.heading_rad
        self.state.frame = LocalFrame(gps.values["latitude_deg"], gps.values["longitude_deg"])
        self.state.origin_id = uuid.uuid4().hex
        self.state.east_m = 0.0
        self.state.north_m = 0.0
        if preserve_heading and old_heading is not None:
            self.state.heading_rad = old_heading
        elif gps.values.get("bearing_deg") is not None and (gps.values.get("speed_mps") or 0.0) >= self.config.initial_heading_speed_mps:
            self.state.heading_rad = math.radians(float(gps.values["bearing_deg"]))
        else:
            self.state.heading_rad = None
        self.state.estimate_id = uuid.uuid4().hex

    def _recovery_is_consistent(self) -> bool:
        """復帰候補のGPS点が時間幅と移動距離の条件を満たすか確認する。"""
        values = self.state.recovery or []
        if len(values) < self.config.gps_recovery_count:
            return False
        if values[-1].observed_monotonic - values[0].observed_monotonic > self.config.gps_recovery_window_s:
            return False
        first = values[0]
        frame = LocalFrame(first.values["latitude_deg"], first.values["longitude_deg"])
        return all(math.hypot(*frame.to_local(item.values["latitude_deg"], item.values["longitude_deg"])) <= self.config.gps_recovery_jump_m for item in values[1:])

    def _distance_to_gps(self, gps: Observation) -> float:
        """現在の基準座標からGPS観測までの平面距離を計算する。"""
        if self.state.frame is None:
            return float("inf")
        return math.hypot(*self.state.frame.to_local(gps.values["latitude_deg"], gps.values["longitude_deg"]))

    def _is_new_gps(self, gps: Observation) -> bool:
        """測定IDを比較し、未処理のGPS観測かを判定する。"""
        return self.state.last_gps is None or gps.measurement_id != self.state.last_gps.measurement_id

    @staticmethod
    def _fresh(observation: Observation, max_age_ms: int, now: float) -> bool:
        """観測時刻とvalidityから、指定期限内で利用可能か判定する。"""
        age = (now - observation.observed_monotonic) * 1000 + observation.age_ms_at_send
        return 0 <= age <= max_age_ms and observation.validity in {Validity.VALID, Validity.DEGRADED}


def _gps_speed(observation: Observation | None) -> float | None:
    """GPS観測から速度を安全に取り出し、未提供ならNoneを返す。"""
    if observation is None or observation.values.get("speed_mps") is None:
        return None
    return float(observation.values["speed_mps"])
