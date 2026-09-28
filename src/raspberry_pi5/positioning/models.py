"""現在地補正で扱う値の型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import monotonic
from typing import Any


class Validity(str, Enum):
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    FAULT = "FAULT"


class MotionState(str, Enum):
    FORWARD = "FORWARD"
    REVERSE = "REVERSE"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Observation:
    """単位と識別情報を揃えた1件の観測値。"""

    kind: str
    source: str
    source_id: str
    boot_id: str
    sequence: int
    measurement_id: str
    received_monotonic: float
    observed_monotonic: float
    observed_at_utc: str | None
    valid_for_ms: int
    validity: Validity
    values: dict[str, Any]
    age_ms_at_send: int = 0


@dataclass(frozen=True)
class FusionBatch:
    """同じ処理時刻に対して取り出した観測のまとまり。"""

    observations: tuple[Observation, ...]
    process_monotonic: float
    missing: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityDecision:
    publishable: bool
    validity: Validity
    accuracy_m: float | None
    valid_for_ms: int
    reason: str = ""


@dataclass(frozen=True)
class PositionEstimate:
    """04 Linux位置情報連携へ渡す位置データ。"""

    latitude_deg: float
    longitude_deg: float
    horizontal_accuracy_m: float
    speed_mps: float | None
    speed_valid: bool
    bearing_deg: float | None
    bearing_valid: bool
    method: str
    publishable: bool
    validity: Validity
    observed_at_utc: str | None
    age_ms_at_send: int
    valid_for_ms: int
    last_gps_age_ms: int | None
    estimate_id: str
    origin_id: str
    discontinuity: bool = False
    reason: str = ""
    input_refs: tuple[str, ...] = ()

    def to_payload(self, source: str, boot_id: str, sequence: int) -> dict[str, Any]:
        """共通仕様のposition.update形式へ変換する。"""
        return {
            "event": "position.update",
            "schema_version": 1,
            "source": source,
            "boot_id": boot_id,
            "sequence": sequence,
            "issued_monotonic": monotonic(),
            "latitude_deg": self.latitude_deg,
            "longitude_deg": self.longitude_deg,
            "horizontal_accuracy_m": self.horizontal_accuracy_m,
            "speed_mps": self.speed_mps if self.speed_valid else None,
            "speed_valid": self.speed_valid,
            "bearing_deg": self.bearing_deg if self.bearing_valid else None,
            "bearing_valid": self.bearing_valid,
            "method": self.method,
            "publishable": self.publishable,
            "validity": self.validity.value,
            "observed_at_utc": self.observed_at_utc,
            "age_ms_at_send": self.age_ms_at_send,
            "valid_for_ms": self.valid_for_ms,
            "last_gps_age_ms": self.last_gps_age_ms,
            "estimate_id": self.estimate_id,
            "origin_id": self.origin_id,
            "discontinuity": self.discontinuity,
            "reason": self.reason,
            "input_refs": list(self.input_refs),
        }
