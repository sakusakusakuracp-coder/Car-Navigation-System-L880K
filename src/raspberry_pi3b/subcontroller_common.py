"""Pi 3Bサブコントローラで共用する、安全な入力・通信部品。"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

LOGGER = logging.getLogger("l880k.subcontroller")


def make_event(source: str, boot_id: str, notification_sequence: int, event: str, **payload: Any) -> dict[str, Any]:
    """共通仕様の起動世代・通知連番を付けたイベントを作る。"""
    return {
        "schema_version": "1.0",
        "event": event,
        "source": source,
        "boot_id": boot_id,
        "sequence": notification_sequence,
        "observed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "observed_at_monotonic_ms": int(time.monotonic() * 1000),
        **payload,
    }


class JsonLinePublisher:
    """Pi 5へ状態を送るTCP JSON Lines発行器。

    接続が落ちたときは古い状態やフレームを無制限に蓄積しない。再接続後は
    呼び出し側が現在状態をもう一度生成して送ることで、過去データを持ち越さない。
    """

    def __init__(self, host: str, port: int, source: str, connect_timeout_s: float = 2.0) -> None:
        """設定と内部状態を初期化する。"""
        self.host = host
        self.port = port
        self.source = source
        self.connect_timeout_s = connect_timeout_s
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self.connection_generation = 0

    def publish(self, event: dict[str, Any]) -> bool:
        """状態またはイベントを購読者へ通知する。"""
        data = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            if self._socket is None and not self._connect_locked():
                return False
            try:
                self._socket.sendall(data)
                return True
            except OSError:
                self._close_locked()
                return False

    def request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """GET_STATEなどの参照要求を送り、1行の応答を返す。"""
        data = (json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            if self._socket is None and not self._connect_locked():
                return None
            try:
                self._socket.sendall(data)
                self._socket.settimeout(self.connect_timeout_s)
                raw = b""
                while b"\n" not in raw:
                    chunk = self._socket.recv(4096)
                    if not chunk:
                        raise OSError("相手が接続を閉じました")
                    raw += chunk
                return json.loads(raw.split(b"\n", 1)[0].decode("utf-8"))
            except (OSError, json.JSONDecodeError):
                self._close_locked()
                return None

    def _connect_locked(self) -> bool:
        """JsonLinePublisherの_connect_lockedの内部処理を実行する。"""
        try:
            self._socket = socket.create_connection((self.host, self.port), timeout=self.connect_timeout_s)
            self.connection_generation += 1
            LOGGER.info("%sへ接続しました（世代=%s）", self.source, self.connection_generation)
            return True
        except OSError as exc:
            LOGGER.warning("%sへ接続できません: %s", self.source, exc)
            self._socket = None
            return False

    def _close_locked(self) -> None:
        """JsonLinePublisherの_close_lockedの内部処理を実行する。"""
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None

    def close(self) -> None:
        """保持中の資源を解放する。"""
        with self._lock:
            self._close_locked()


class InputDriver:
    """GPIO入力の実装境界。"""

    def read(self, gpio: int) -> int:
        """入力値を読み取り、利用可能な形式で返す。"""
        raise NotImplementedError

    def close(self) -> None:
        """保持中の資源を解放する。"""
        pass


class DryRunInputDriver(InputDriver):
    """開発・試験用の入力。set_valueで車体信号を模擬できる。"""

    def __init__(self, initial: int = 0) -> None:
        """設定と内部状態を初期化する。"""
        self.value = initial

    def read(self, gpio: int) -> int:
        """入力値を読み取り、利用可能な形式で返す。"""
        return self.value


class LgpioInputDriver(InputDriver):
    """lgpioを使ったPi 3Bの3.3V入力。車体12Vはここへ直接入れない。"""

    def __init__(self, gpios: list[int]) -> None:
        """設定と内部状態を初期化する。"""
        try:
            import lgpio
        except ImportError as exc:
            raise RuntimeError("実機GPIOにはlgpioが必要です") from exc
        self._lgpio = lgpio
        self._chip = lgpio.gpiochip_open(0)
        for gpio in gpios:
            lgpio.gpio_claim_input(self._chip, gpio)

    def read(self, gpio: int) -> int:
        """入力値を読み取り、利用可能な形式で返す。"""
        return int(self._lgpio.gpio_read(self._chip, gpio))

    def close(self) -> None:
        """保持中の資源を解放する。"""
        try:
            self._lgpio.gpiochip_close(self._chip)
        except Exception:  # noqa: BLE001
            LOGGER.exception("GPIO入力の終了に失敗しました")


@dataclass
class DebouncedSignal:
    """候補値が一定時間続いたときだけ安定値として採用する。"""

    active_level: int
    debounce_ms: int
    stable: bool | None = None
    candidate: int | None = None
    candidate_started_ms: int | None = None
    last_read_ms: int | None = None

    def update(self, raw: int, now_ms: int) -> bool | None:
        """受信値を内部状態へ反映する。"""
        self.last_read_ms = now_ms
        if raw != self.candidate:
            self.candidate = raw
            self.candidate_started_ms = now_ms
        if self.candidate_started_ms is not None and now_ms - self.candidate_started_ms >= self.debounce_ms:
            self.stable = self.candidate == self.active_level
        return self.stable

    def invalidate(self) -> None:
        """保持中の値を無効化する。"""
        self.stable = None
        self.candidate = None
        self.candidate_started_ms = None


@dataclass
class CameraFrame:
    camera_id: str
    sequence: int
    captured_at_utc: str
    clock_quality: str
    jpeg_bytes: bytes | None = None


class CameraDriver:
    """カメラAPIの実装境界。"""

    def start(self) -> None:
        """サービスまたはデバイスを起動する。"""
        pass

    def capture(self, camera_id: str, sequence: int) -> CameraFrame | None:
        """カメラ入力を取得して次の処理へ渡す。"""
        raise NotImplementedError

    def close(self) -> None:
        """保持中の資源を解放する。"""
        pass


class DryRunCameraDriver(CameraDriver):
    """フレーム本体を作らず、映像経路の状態だけを試験する。"""

    def capture(self, camera_id: str, sequence: int) -> CameraFrame:
        """カメラ入力を取得して次の処理へ渡す。"""
        return CameraFrame(
            camera_id=camera_id,
            sequence=sequence,
            captured_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            clock_quality="SYSTEM_UTC",
        )


class Picamera2JpegDriver(CameraDriver):
    """Picamera2でJPEGフレームを作る任意依存ドライバ。"""

    def __init__(self, width: int, height: int, fps: int) -> None:
        """設定と内部状態を初期化する。"""
        try:
            from picamera2 import Picamera2
        except ImportError as exc:
            raise RuntimeError("CSIカメラを使うにはpicamera2が必要です") from exc
        self._picamera2 = Picamera2()
        self._config = self._picamera2.create_video_configuration(main={"size": (width, height), "format": "RGB888"}, controls={"FrameRate": fps})

    def start(self) -> None:
        """サービスまたはデバイスを起動する。"""
        self._picamera2.configure(self._config)
        self._picamera2.start()

    def capture(self, camera_id: str, sequence: int) -> CameraFrame | None:
        """カメラ入力を取得して次の処理へ渡す。"""
        import io

        from PIL import Image

        image = self._picamera2.capture_array()
        buffer = io.BytesIO()
        Image.fromarray(image).save(buffer, format="JPEG", quality=82)
        return CameraFrame(camera_id, sequence, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "SYSTEM_UTC", buffer.getvalue())

    def close(self) -> None:
        """保持中の資源を解放する。"""
        self._picamera2.stop()
