"""共通データ型。

車種固有のバイト列をこのモジュールへ埋め込まず、設定された通信プロファイルと
観測結果を明示的に持ち回るための型だけを定義する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class ServiceState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    INITIALIZING = "INITIALIZING"
    PROBING = "PROBING"
    POLLING = "POLLING"
    DEGRADED = "DEGRADED"
    RETRY_WAIT = "RETRY_WAIT"
    STOPPING = "STOPPING"


class SupportState(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class Validity(str, Enum):
    VALID = "VALID"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    FAULT = "FAULT"


@dataclass(frozen=True)
class SerialIdentity:
    port: str | None = None
    vendor_id: int | None = None
    product_id: int | None = None
    serial_number: str | None = None


@dataclass(frozen=True)
class InitStep:
    action: str
    data: bytes = b""
    wait_ms: int = 0
    expect_prefix: bytes | None = None


@dataclass(frozen=True)
class RequestDefinition:
    request_id: str
    key: str
    request: bytes
    service: int | None = None
    data_id: int | None = None
    priority: int = 100
    interval_ms: int = 1000
    ttl_ms: int = 3000
    timeout_ms: int = 800
    probe: bool = False
    operation: str = "read"
    decode: Mapping[str, Any] = field(default_factory=dict)
    response_length: int | None = None
    negative_is_unsupported: bool = False
    poll_modes: tuple[str, ...] = ("full",)


@dataclass(frozen=True)
class VehicleProfile:
    profile_id: str
    version: str
    status: str
    confirmed: bool
    port: str | None
    serial_identity: SerialIdentity
    host_baudrate: int
    kline_baudrate: int
    init_steps: tuple[InitStep, ...]
    requests: tuple[RequestDefinition, ...]
    max_frame_length: int = 256
    checksum: str = "none"
    echo_mode: str = "unknown"
    response_length: int | None = None
    init_timeout_ms: int = 1000
    init_retry_limit: int = 2
    min_query_gap_ms: int = 30
    inter_byte_timeout_ms: int = 80
    retry_limit: int = 3
    shutdown_timeout_ms: int = 1000

    @property
    def is_usable(self) -> bool:
        """実車送信を許可できる設定かを判定する。"""

        return self.confirmed and self.status.lower() == "confirmed" and bool(self.init_steps)


@dataclass(frozen=True)
class ResponseFrame:
    payload: bytes
    query_id: str
    connection_epoch: int
    session_epoch: int
    started_mono: float
    received_mono: float


@dataclass(frozen=True)
class VehicleSample:
    key: str
    value: Any
    unit: str | None
    acquired_at_utc: str
    acquired_mono: float
    valid_for_ms: int
    source_service: str
    source_identifier: str
    source_pid: str | None
    ecu_identity: str | None
    profile_id: str
    profile_version: str
    session_epoch: int
    connection_epoch: int
    query_id: str
    time_quality: str = "monotonic"


@dataclass(frozen=True)
class CachedValue:
    sample: VehicleSample
    validity: Validity = Validity.VALID
    invalid_reason: str | None = None
