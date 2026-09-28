"""WGS84と小範囲のローカル東北座標の変換。"""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6_378_137.0


def normalize_angle(angle_rad: float) -> float:
    """角度を-pi以上、pi未満へ折り返す。"""
    return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi


def normalize_bearing(angle_deg: float) -> float:
    """真北0度・時計回りの方位角へ正規化する。"""
    return angle_deg % 360.0


class LocalFrame:
    """車両周辺の短距離計算用ENUフレーム。"""

    def __init__(self, latitude_deg: float, longitude_deg: float) -> None:
        """基準緯度経度を保持し、短距離変換用の緯度補正値を準備する。"""
        self.latitude_deg = latitude_deg
        self.longitude_deg = longitude_deg
        self._cos_lat = math.cos(math.radians(latitude_deg))

    def to_local(self, latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
        """緯度経度を基準点からの東・北メートルへ変換する。"""
        east = math.radians(longitude_deg - self.longitude_deg) * EARTH_RADIUS_M * self._cos_lat
        north = math.radians(latitude_deg - self.latitude_deg) * EARTH_RADIUS_M
        return east, north

    def to_geodetic(self, east_m: float, north_m: float) -> tuple[float, float]:
        """東・北メートルを基準点からの緯度経度へ戻す。"""
        latitude = self.latitude_deg + math.degrees(north_m / EARTH_RADIUS_M)
        longitude = self.longitude_deg + math.degrees(east_m / (EARTH_RADIUS_M * self._cos_lat))
        return latitude, longitude


def bearing_from_velocity(east_mps: float, north_mps: float) -> float | None:
    """東北速度から移動方向を求める。停止中はNoneを返す。"""
    if math.hypot(east_mps, north_mps) < 1e-6:
        return None
    return normalize_bearing(math.degrees(math.atan2(east_mps, north_mps)))
