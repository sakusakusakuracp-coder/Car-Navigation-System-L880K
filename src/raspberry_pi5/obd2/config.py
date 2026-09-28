"""OBD2通信プロファイルの読み込みと安全性検査。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Raspberry PiのPython 3.11未満向け
    tomllib = None

from .models import InitStep, RequestDefinition, SerialIdentity, VehicleProfile


class ProfileError(ValueError):
    """通信プロファイルが不正、または安全条件を満たさない。"""


def parse_hex(value: str | bytes | None, *, field_name: str) -> bytes:
    """設定値の16進文字列を通信バイト列へ変換する。"""
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    compact = "".join(value.replace("0x", "").split())
    if len(compact) % 2 or any(char not in "0123456789abcdefABCDEF" for char in compact):
        raise ProfileError(f"{field_name}は偶数桁の16進数で指定してください")
    try:
        return bytes.fromhex(compact)
    except ValueError as exc:
        raise ProfileError(f"{field_name}の16進数を解釈できません") from exc


def _int_or_none(value: Any) -> int | None:
    """未設定値をNone、それ以外を整数へ変換する。"""
    if value is None or value == "":
        return None
    return int(value)


def _parse_init_steps(raw_steps: list[dict[str, Any]]) -> tuple[InitStep, ...]:
    """K-Line初期化手順を検証し、実行可能な定義列へ変換する。"""
    steps: list[InitStep] = []
    for index, raw in enumerate(raw_steps):
        action = str(raw.get("action", "")).lower()
        if action not in {"write", "wait"}:
            raise ProfileError(f"init.steps[{index}].actionはwriteまたはwaitです")
        data = parse_hex(raw.get("data"), field_name=f"init.steps[{index}].data")
        expect = raw.get("expect_prefix")
        steps.append(
            InitStep(
                action=action,
                data=data,
                wait_ms=max(0, int(raw.get("wait_ms", 0))),
                expect_prefix=parse_hex(expect, field_name=f"init.steps[{index}].expect_prefix")
                if expect
                else None,
            )
        )
    return tuple(steps)


def _parse_requests(raw_requests: list[dict[str, Any]]) -> tuple[RequestDefinition, ...]:
    """車両要求定義を重複・モード・送信内容の検証付きで読み込む。"""
    requests: list[RequestDefinition] = []
    identifiers: set[str] = set()
    for index, raw in enumerate(raw_requests):
        request_id = str(raw.get("id", "")).strip()
        if not request_id or request_id in identifiers:
            raise ProfileError(f"request[{index}].idが空、または重複しています")
        identifiers.add(request_id)
        operation = str(raw.get("operation", "read")).lower()
        if operation != "read":
            raise ProfileError(f"{request_id}はread以外の操作を登録できません")
        request = parse_hex(raw.get("request"), field_name=f"request[{index}].request")
        if not request:
            raise ProfileError(f"{request_id}の要求バイト列が空です")
        default_modes = ["minimal", "full"] if str(raw.get("key", request_id)) in {"vehicle_speed", "coolant_temperature"} else ["full"]
        raw_modes = raw.get("poll_modes", default_modes)
        if isinstance(raw_modes, str):
            raw_modes = [raw_modes]
        poll_modes = tuple(str(mode).lower() for mode in raw_modes)
        if not poll_modes or any(mode not in {"minimal", "full"} for mode in poll_modes):
            raise ProfileError(f"{request_id}.poll_modesはminimalまたはfullです")
        requests.append(
            RequestDefinition(
                request_id=request_id,
                key=str(raw.get("key", request_id)),
                request=request,
                service=_int_or_none(raw.get("service")),
                data_id=_int_or_none(raw.get("data_id")),
                priority=int(raw.get("priority", 100)),
                interval_ms=max(1, int(raw.get("interval_ms", 1000))),
                ttl_ms=max(1, int(raw.get("ttl_ms", 3000))),
                timeout_ms=max(1, int(raw.get("timeout_ms", 800))),
                probe=bool(raw.get("probe", False)),
                operation=operation,
                decode=dict(raw.get("decode", {})),
                response_length=_int_or_none(raw.get("response_length")),
                negative_is_unsupported=bool(raw.get("negative_is_unsupported", False)),
                poll_modes=poll_modes,
            )
        )
    return tuple(requests)


def load_profile(path: str | Path) -> VehicleProfile:
    """TOMLからプロファイルを読み、未知の送信操作を拒否する。"""

    if tomllib is None:
        raise ProfileError("Python 3.11以降、またはtomliが必要です")
    profile_path = Path(path)
    with profile_path.open("rb") as stream:
        data = tomllib.load(stream)
    raw_profile = dict(data.get("profile", {}))
    raw_serial = dict(data.get("serial", {}))
    raw_frame = dict(data.get("frame", {}))
    raw_init = dict(data.get("init", {}))
    raw_timing = dict(data.get("timing", {}))
    identity = SerialIdentity(
        port=str(raw_serial.get("port")) if raw_serial.get("port") else None,
        vendor_id=_int_or_none(raw_serial.get("vendor_id")),
        product_id=_int_or_none(raw_serial.get("product_id")),
        serial_number=str(raw_serial.get("serial_number")) if raw_serial.get("serial_number") else None,
    )
    profile = VehicleProfile(
        profile_id=str(raw_profile.get("id", "unknown")),
        version=str(raw_profile.get("version", "0")),
        status=str(raw_profile.get("status", "TBD")),
        confirmed=bool(raw_profile.get("confirmed", False)),
        port=identity.port,
        serial_identity=identity,
        host_baudrate=int(raw_serial.get("host_baudrate", 38400)),
        kline_baudrate=int(raw_serial.get("kline_baudrate", 10400)),
        init_steps=_parse_init_steps(list(raw_init.get("steps", []))),
        requests=_parse_requests(list(data.get("request", []))),
        max_frame_length=max(8, int(raw_frame.get("max_length", 256))),
        checksum=str(raw_frame.get("checksum", "none")).lower(),
        echo_mode=str(raw_frame.get("echo_mode", "unknown")).lower(),
        response_length=_int_or_none(raw_frame.get("response_length")),
        init_timeout_ms=max(1, int(raw_init.get("timeout_ms", 1000))),
        init_retry_limit=max(0, int(raw_init.get("retry_limit", 2))),
        min_query_gap_ms=max(0, int(raw_timing.get("min_query_gap_ms", 30))),
        inter_byte_timeout_ms=max(1, int(raw_timing.get("inter_byte_timeout_ms", 80))),
        retry_limit=max(0, int(raw_timing.get("retry_limit", 3))),
        shutdown_timeout_ms=max(1, int(raw_timing.get("shutdown_timeout_ms", 1000))),
    )
    if profile.checksum not in {"none", "sum8"}:
        raise ProfileError("frame.checksumはnoneまたはsum8にしてください")
    if profile.echo_mode not in {"none", "full", "unknown"}:
        raise ProfileError("frame.echo_modeはnone、full、unknownのいずれかです")
    if profile.confirmed and profile.status.lower() != "confirmed":
        raise ProfileError("confirmed=trueの場合、profile.statusもconfirmedにしてください")
    return profile
