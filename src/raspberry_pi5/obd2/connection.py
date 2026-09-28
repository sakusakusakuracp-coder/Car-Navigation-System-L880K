"""USBシリアルアダプタへの排他的な接続。"""

from __future__ import annotations

import time
from typing import Any

from .models import SerialIdentity

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - 実機依存
    serial = None
    list_ports = None


class AdapterError(RuntimeError):
    """USB–OBDアダプタの接続・入出力エラー。"""


class AdapterConnection:
    """pySerialを使う生バイト転送アダプタ。

    DTR/RTS/BREAKによる車体側波形生成は、採用ケーブルの仕様確認前には実行しない。
    """

    def __init__(
        self,
        *,
        port: str | None,
        baudrate: int,
        identity: SerialIdentity,
        read_timeout: float = 0.02,
        write_timeout: float = 1.0,
    ) -> None:
        """USBシリアルアダプタの接続条件と通信世代を初期化する。"""
        self.port = port
        self.baudrate = baudrate
        self.identity = identity
        self.read_timeout = read_timeout
        self.write_timeout = write_timeout
        self._serial: Any = None
        self.connection_epoch = 0

    @property
    def is_open(self) -> bool:
        """シリアルポートが開いているか返す。"""
        return bool(self._serial is not None and self._serial.is_open)

    def _resolve_port(self) -> str:
        """設定ポートまたはUSB識別情報から接続先を決める。"""
        if self.port:
            return self.port
        if list_ports is None:
            raise AdapterError("pySerialがインストールされていません")
        candidates = []
        for item in list_ports.comports():
            if self.identity.vendor_id is not None and item.vid != self.identity.vendor_id:
                continue
            if self.identity.product_id is not None and item.pid != self.identity.product_id:
                continue
            if self.identity.serial_number and item.serial_number != self.identity.serial_number:
                continue
            candidates.append(item.device)
        if len(candidates) != 1:
            raise AdapterError(f"対象アダプタを一意に特定できません: {candidates}")
        return candidates[0]

    def connect_adapter(self) -> int:
        """対象USB機器だけを開き、新しい接続世代を返す。"""

        if serial is None:
            raise AdapterError("pySerialがインストールされていません")
        if self.is_open:
            return self.connection_epoch
        selected_port = self._resolve_port()
        try:
            self._serial = serial.Serial(
                port=selected_port,
                baudrate=self.baudrate,
                timeout=self.read_timeout,
                write_timeout=self.write_timeout,
                exclusive=True,
            )
        except TypeError:  # Windowsや古いpySerialのexclusive非対応
            self._serial = serial.Serial(
                port=selected_port,
                baudrate=self.baudrate,
                timeout=self.read_timeout,
                write_timeout=self.write_timeout,
            )
        except Exception as exc:  # pragma: no cover - 実機依存
            self._serial = None
            raise AdapterError(f"USBシリアルを開けません: {selected_port}") from exc
        self.port = selected_port
        self.connection_epoch += 1
        return self.connection_epoch

    def write_bytes(self, data: bytes, *, deadline: float | None = None) -> int:
        """接続済みアダプタへK-Line要求バイト列を送信する。"""
        if not self.is_open:
            raise AdapterError("USBシリアルが接続されていません")
        if not data:
            raise AdapterError("空の送信は許可されていません")
        deadline = deadline if deadline is not None else time.monotonic() + self.write_timeout
        written = 0
        while written < len(data):
            if time.monotonic() >= deadline:
                raise AdapterError("USB書き込みがタイムアウトしました")
            count = int(self._serial.write(data[written:]))
            if count <= 0:
                raise AdapterError("USB書き込みが進みません")
            written += count
        return written

    def read_bytes(self, *, max_bytes: int = 256, timeout: float = 0.8) -> bytes:
        """期限までシリアル応答を読み、上限長までのバイト列を返す。"""
        if not self.is_open:
            raise AdapterError("USBシリアルが接続されていません")
        end = time.monotonic() + timeout
        received = bytearray()
        while time.monotonic() < end and len(received) < max_bytes:
            waiting = min(max_bytes - len(received), int(getattr(self._serial, "in_waiting", 0)))
            if waiting:
                received.extend(self._serial.read(waiting))
            else:
                time.sleep(0.002)
        return bytes(received)

    def reconnect_adapter(self) -> int:
        """ポートを再接続し、通信世代を更新する。"""
        self.stop_obd()
        return self.connect_adapter()

    def stop_obd(self) -> None:
        """シリアルポートを閉じて車両通信を停止する。"""
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None
