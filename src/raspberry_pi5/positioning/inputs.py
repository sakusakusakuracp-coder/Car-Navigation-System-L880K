"""観測入力の検証・単位変換・gpsd取り込み。"""

from __future__ import annotations

import json
import math
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .models import Observation, Validity


class InputError(ValueError):
    """観測を採用できない。"""


class InputAdapter:
    """生JSONを共通観測へ変換し、重複・逆順を除外する。"""

    def __init__(self) -> None:
        """設定と内部状態を初期化する。"""
        self._last_sequence: dict[tuple[str, str], int] = {}
        self._last_measurement: dict[tuple[str, str], str] = {}
        self._boot_ids: dict[str, str] = {}
        self.last_rejection = ""

    def normalize_observation(self, payload: dict[str, Any], now: float | None = None) -> Observation:
        """InputAdapterのnormalize_observationの内部処理を実行する。"""
        now = time.monotonic() if now is None else now
        kind = str(payload.get("kind", payload.get("type", ""))).lower()
        if kind in {"observation", "position.update"}:
            kind = str(payload.get("observation_kind", "" if kind == "observation" else "gps")).lower()
        if kind not in {"gps", "speed", "imu", "reverse"}:
            raise InputError("kindはgps、speed、imu、reverseのいずれかが必要です")
        source = str(payload.get("source", ""))
        source_id = str(payload.get("source_id", payload.get("device", source)))
        boot_id = str(payload.get("boot_id", ""))
        if not source or not source_id or not boot_id:
            raise InputError("source、source_id、boot_idは必須です")
        sequence = _integer(payload.get("sequence", 0), "sequence", minimum=0)
        values = dict(payload.get("values", payload.get("value", {})))
        if not values:
            values = {key: value for key, value in payload.items() if key not in {
                "type", "kind", "observation_kind", "source", "source_id", "device", "boot_id",
                "sequence", "measurement_id", "measurement_epoch", "observed_monotonic",
                "observed_at_utc", "received_monotonic", "valid_for_ms", "validity", "age_ms_at_send",
            }}
        measurement_id = str(payload.get("measurement_id", payload.get("measurement_epoch", "")))
        if not measurement_id:
            measurement_id = f"{source_id}:{sequence}"
        key = (source, source_id)
        previous_boot = self._boot_ids.get(source_id)
        if previous_boot is not None and previous_boot != boot_id:
            self._last_sequence.pop(key, None)
            self._last_measurement.pop(key, None)
        self._boot_ids[source_id] = boot_id
        if key in self._last_sequence and sequence < self._last_sequence[key]:
            raise InputError("古いsequenceの観測です")
        if self._last_measurement.get(key) == measurement_id:
            raise InputError("同じ測定の再送です")
        normalized = _normalize_values(kind, values)
        observed_monotonic = float(payload.get("observed_monotonic", now))
        if not math.isfinite(observed_monotonic):
            raise InputError("observed_monotonicが不正です")
        validity = Validity(str(payload.get("validity", "VALID")).upper())
        valid_for_ms = _integer(payload.get("valid_for_ms", _default_validity_ms(kind)), "valid_for_ms", minimum=1)
        age_ms = _integer(payload.get("age_ms_at_send", 0), "age_ms_at_send", minimum=0)
        observation = Observation(
            kind=kind,
            source=source,
            source_id=source_id,
            boot_id=boot_id,
            sequence=sequence,
            measurement_id=measurement_id,
            received_monotonic=now,
            observed_monotonic=observed_monotonic,
            observed_at_utc=_optional_string(payload.get("observed_at_utc")),
            valid_for_ms=valid_for_ms,
            validity=validity,
            values=normalized,
            age_ms_at_send=age_ms,
        )
        self._last_sequence[key] = sequence
        self._last_measurement[key] = measurement_id
        return observation

    def handle_source_reset(self, source_id: str, boot_id: str) -> None:
        """送信元の世代変更時に、その送信元の重複判定を初期化する。"""
        self._boot_ids[source_id] = boot_id


def _normalize_values(kind: str, values: dict[str, Any]) -> dict[str, Any]:
    """_normalize_valuesの内部処理を実行する。"""
    result = dict(values)
    if kind == "gps":
        result["latitude_deg"] = _finite(result.get("latitude_deg"), "latitude_deg", -90.0, 90.0)
        result["longitude_deg"] = _finite(result.get("longitude_deg"), "longitude_deg", -180.0, 180.0)
        accuracy = result.get("horizontal_accuracy_m", result.get("accuracy_m"))
        result["horizontal_accuracy_m"] = _finite(accuracy, "horizontal_accuracy_m", 0.01, 10_000.0)
        if result.get("speed_mps") is None and result.get("speed_kmh") is not None:
            result["speed_mps"] = float(result["speed_kmh"]) / 3.6
        if result.get("speed_mps") is not None:
            result["speed_mps"] = _finite(result["speed_mps"], "speed_mps", 0.0, 150.0)
        if result.get("bearing_deg") is not None:
            result["bearing_deg"] = _finite(result["bearing_deg"], "bearing_deg", 0.0, 360.0)
        return result
    if kind == "speed":
        if values.get("speed_mps") is None and values.get("speed_kmh") is not None:
            result["speed_mps"] = float(values["speed_kmh"]) / 3.6
        result["speed_mps"] = _finite(result.get("speed_mps"), "speed_mps", 0.0, 150.0)
        return result
    if kind == "imu":
        yaw = values.get("yaw_rate_rad_s", values.get("gyro_z_rad_s"))
        result["yaw_rate_rad_s"] = _finite(yaw, "yaw_rate_rad_s", -20.0, 20.0)
        return result
    state = str(values.get("state", "UNKNOWN")).upper()
    if state not in {"ON", "OFF", "UNKNOWN"}:
        raise InputError("reverse.stateが不正です")
    result["state"] = state
    return result


class GpsdInput:
    """gpsdのTPV JSONを読み、InputAdapterへ渡す小さな読取スレッド。"""

    def __init__(self, host: str, port: int, on_observation: Callable[[dict[str, Any]], None]) -> None:
        """設定と内部状態を初期化する。"""
        self.host = host
        self.port = port
        self.on_observation = on_observation
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._boot_id = f"gpsd-{time.time_ns()}"
        self._sequence = 0

    def start(self) -> None:
        """サービスまたはデバイスを起動する。"""
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="gpsd-input", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """サービスまたはデバイスを停止して資源を解放する。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        """バックグラウンド処理を実行する。"""
        while not self._stop.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=3.0) as connection:
                    connection.sendall(b'?WATCH={"enable":true,"json":true}\n')
                    connection.settimeout(2.0)
                    buffer = b""
                    while not self._stop.is_set():
                        try:
                            chunk = connection.recv(4096)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        buffer += chunk
                        while b"\n" in buffer:
                            raw, buffer = buffer.split(b"\n", 1)
                            if raw.strip():
                                self._handle_line(raw)
            except OSError:
                self._stop.wait(2.0)

    def _handle_line(self, raw: bytes) -> None:
        """受信した1行を解析して状態へ反映する。"""
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if message.get("class") != "TPV":
            return
        mode = int(message.get("mode", 0) or 0)
        if mode < 2 or message.get("lat") is None or message.get("lon") is None:
            return
        self._sequence += 1
        accuracy_m = message.get("eph")
        if accuracy_m is None:
            accuracy_m = message.get("epx")
        if accuracy_m is None:
            accuracy_m = message.get("epy")
        if accuracy_m is None:
            accuracy_m = 50.0
        payload: dict[str, Any] = {
            "kind": "gps",
            "observation_kind": "gps",
            "source": "gpsd",
            "source_id": str(message.get("device", "gpsd")),
            "boot_id": self._boot_id,
            "sequence": self._sequence,
            "measurement_id": f"{message.get('device', 'gpsd')}:{message.get('time', self._sequence)}",
            "observed_at_utc": _gpsd_time(message.get("time")),
            "valid_for_ms": 2000,
            "values": {
                "latitude_deg": message["lat"],
                "longitude_deg": message["lon"],
                "horizontal_accuracy_m": accuracy_m,
                "speed_mps": message.get("speed"),
                "bearing_deg": message.get("track"),
            },
        }
        self.on_observation(payload)


def _gpsd_time(value: Any) -> str | None:
    """_gpsd_timeの内部処理を実行する。"""
    if not value:
        return None
    return str(value)


def _default_validity_ms(kind: str) -> int:
    """_default_validity_msの内部処理を実行する。"""
    return {"gps": 2_000, "speed": 500, "imu": 200, "reverse": 500}[kind]


def _optional_string(value: Any) -> str | None:
    """_optional_stringの内部処理を実行する。"""
    return None if value in (None, "") else str(value)


def _integer(value: Any, name: str, minimum: int) -> int:
    """_integerの内部処理を実行する。"""
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise InputError(f"{name}が整数ではありません") from exc
    if number < minimum:
        raise InputError(f"{name}が範囲外です")
    return number


def _finite(value: Any, name: str, minimum: float, maximum: float) -> float:
    """_finiteの内部処理を実行する。"""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InputError(f"{name}が数値ではありません") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise InputError(f"{name}が範囲外です")
    return number
