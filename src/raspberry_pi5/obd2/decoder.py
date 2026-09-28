"""検査済み応答を内部単位へ変換するデコーダ。"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from .models import RequestDefinition, ResponseFrame, VehicleProfile, VehicleSample


class DecodeError(ValueError):
    """応答を値へ変換できない。"""


def decode_response(
    frame: ResponseFrame,
    request: RequestDefinition,
    profile: VehicleProfile,
    *,
    ecu_identity: str | None = None,
) -> VehicleSample:
    """要求定義の抽出位置と換算式だけを用いて値を作る。"""

    raw = frame.payload
    if raw.startswith(b"\x7f"):
        raise DecodeError("ECUから否定応答を受信しました")
    definition = dict(request.decode)
    prefix = definition.get("response_prefix")
    if prefix:
        expected = bytes.fromhex(str(prefix).replace(" ", ""))
        if not raw.startswith(expected):
            raise DecodeError("応答識別子が要求定義と一致しません")
    offset = int(definition.get("offset", 0))
    length = int(definition.get("length", 1))
    if offset < 0 or length <= 0 or offset + length > len(raw):
        raise DecodeError("応答からのデータ抽出範囲が不正です")
    chunk = raw[offset : offset + length]
    kind = str(definition.get("type", "uint")).lower()
    if kind == "hex":
        value = chunk.hex().upper()
    else:
        byteorder = str(definition.get("byteorder", "big"))
        signed = bool(definition.get("signed", False))
        if byteorder not in {"big", "little"}:
            raise DecodeError("byteorderはbigまたはlittleです")
        numeric = int.from_bytes(chunk, byteorder=byteorder, signed=signed)
        value = numeric * float(definition.get("scale", 1.0)) + float(definition.get("add", 0.0))
        if not math.isfinite(value):
            raise DecodeError("数値が有限ではありません")
        if "min" in definition and value < float(definition["min"]):
            raise DecodeError("値が下限を外れています")
        if "max" in definition and value > float(definition["max"]):
            raise DecodeError("値が上限を外れています")
        if float(value).is_integer():
            value = int(value)
    return VehicleSample(
        key=request.key,
        value=value,
        unit=str(definition.get("unit")) if definition.get("unit") is not None else None,
        acquired_at_utc=datetime.now(timezone.utc).isoformat(),
        acquired_mono=frame.received_mono,
        valid_for_ms=request.ttl_ms,
        source_service="obd2_service",
        source_identifier=request.request_id,
        source_pid=str(request.data_id) if request.data_id is not None else None,
        ecu_identity=ecu_identity,
        profile_id=profile.profile_id,
        profile_version=profile.version,
        session_epoch=frame.session_epoch,
        connection_epoch=frame.connection_epoch,
        query_id=frame.query_id,
    )

