"""03から05へ現在地を期限付きで配送するLinuxブリッジ。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from navigation.resident_runtime import ProcessLock

from .protocol import recv_frame, send_frame
from .validator import PayloadValidator, ValidatedPosition


LOGGER = logging.getLogger("l880k.location_bridge")


class LocationBridgeService:
    """最新位置だけを保持し、通信世代と期限を分けて管理する。"""

    def __init__(
        self,
        source_socket: str,
        android_host: str,
        android_port: int,
        max_age_ms: int = 2_000,
        max_accuracy_m: float = 100.0,
        bridge_delay_bound_ms: int = 100,
        allow_dead_reckoning: bool = False,
        shared_token: str = "",
        reconnect_initial_s: float = 0.5,
        reconnect_max_s: float = 5.0,
    ) -> None:
        """設定と内部状態を初期化する。"""
        self.source_socket = source_socket
        self.android_host = android_host
        self.android_port = android_port
        self.bridge_delay_bound_ms = bridge_delay_bound_ms
        self.shared_token = shared_token
        self.reconnect_initial_s = reconnect_initial_s
        self.reconnect_max_s = reconnect_max_s
        self.validator = PayloadValidator(max_age_ms, max_accuracy_m, allow_dead_reckoning)
        self.bridge_boot_id = uuid.uuid4().hex
        self.connection_id = ""
        self.delivery_epoch = 0
        self.tx_sequence = 0
        self.source_boot_id = ""
        self.source_sequence = -1
        self.latest: ValidatedPosition | None = None
        self.last_ack: dict[str, Any] | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self._android_socket: socket.socket | None = None
        self._android_send_lock = threading.Lock()
        self._connection_ready = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_sent_key: tuple[str, int, int] | None = None

    def run(self) -> None:
        """主処理を実行して終了まで監視する。"""
        LOGGER.info("04 Linux位置情報連携を起動しました boot_id=%s", self.bridge_boot_id)
        self._threads = [
            threading.Thread(target=self._source_loop, name="location-source", daemon=True),
            threading.Thread(target=self._android_loop, name="location-android", daemon=True),
            threading.Thread(target=self._expiry_loop, name="location-expiry", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        try:
            while not self._stop.wait(0.5):
                pass
        finally:
            self.stop()

    def stop(self) -> None:
        """サービスまたはデバイスを停止して資源を解放する。"""
        if self._stop.is_set():
            return
        self._stop.set()
        self._wake.set()
        self._connection_ready.clear()
        with self._lock:
            sock = self._android_socket
            self._android_socket = None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=2.0)
        LOGGER.info("04 Linux位置情報連携を停止しました")

    def status(self) -> dict[str, Any]:
        """現在の処理状態を外部形式へまとめる。"""
        with self._lock:
            position = self.latest.payload if self.latest else None
            return {
                "event": "bridge.status",
                "source": "04 Linux位置情報連携",
                "bridge_boot_id": self.bridge_boot_id,
                "connection_id": self.connection_id or None,
                "connection_state": "READY" if self._connection_ready.is_set() else "DISCONNECTED",
                "delivery_state": "ACTIVE" if position else "WAIT_POSITION",
                "position_ref": position.get("estimate_id") if position else None,
                "last_ack": self.last_ack,
            }

    def _source_loop(self) -> None:
        """LocationBridgeServiceの_source_loopの内部処理を実行する。"""
        while not self._stop.is_set():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(5.0)
                    sock.connect(self.source_socket)
                    sock.sendall((json.dumps({"operation": "hello"}, separators=(",", ":")) + "\n").encode("utf-8"))
                    buffer = b""
                    sock.settimeout(1.0)
                    while not self._stop.is_set():
                        try:
                            chunk = sock.recv(4096)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        buffer += chunk
                        while b"\n" in buffer:
                            raw, buffer = buffer.split(b"\n", 1)
                            if raw.strip():
                                self._handle_source_event(json.loads(raw.decode("utf-8")))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                LOGGER.warning("03 現在地補正との接続を待機します: %s", exc)
            self._stop.wait(self.reconnect_initial_s)

    def _handle_source_event(self, event: dict[str, Any]) -> None:
        """LocationBridgeServiceの_handle_source_eventの内部処理を実行する。"""
        name = event.get("event")
        if name == "position.update":
            try:
                position = self.validator.validate_position(event)
            except (TypeError, ValueError) as exc:
                LOGGER.warning("位置を配送候補から除外しました: %s", exc)
                return
            with self._lock:
                if self.source_boot_id == position.payload["boot_id"] and int(position.payload["sequence"]) <= self.source_sequence:
                    return
                self.source_boot_id = str(position.payload["boot_id"])
                self.source_sequence = int(position.payload["sequence"])
                self.latest = position
            self._wake.set()
        elif name == "position.invalidate":
            reason = str(event.get("reason", "03から無効通知を受信しました"))
            self._invalidate(reason)
        elif name == "position.status":
            LOGGER.info("03状態: %s %s", event.get("state"), event.get("reason", ""))

    def _android_loop(self) -> None:
        """LocationBridgeServiceの_android_loopの内部処理を実行する。"""
        delay = self.reconnect_initial_s
        while not self._stop.is_set():
            try:
                with socket.create_connection((self.android_host, self.android_port), timeout=3.0) as sock:
                    sock.settimeout(1.0)
                    with self._lock:
                        self._android_socket = sock
                        self.connection_id = uuid.uuid4().hex
                        self._last_sent_key = None
                    self._send({
                        "event": "bridge.hello",
                        "schema_version": 1,
                        "bridge_boot_id": self.bridge_boot_id,
                        "connection_id": self.connection_id,
                        "token": self.shared_token,
                        "capabilities": {"dead_reckoning": self.validator.allow_dead_reckoning},
                    })
                    hello = recv_frame(sock)
                    if not isinstance(hello, dict) or hello.get("event") != "bridge.ack" or hello.get("state") not in {"READY", "WAIT_POSITION"}:
                        raise ConnectionError("05のhello応答を受け付けられません")
                    self._connection_ready.set()
                    delay = self.reconnect_initial_s
                    self._send_latest_if_available()
                    while not self._stop.is_set():
                        try:
                            response = recv_frame(sock)
                        except socket.timeout:
                            self._send_latest_if_available()
                            continue
                        if response is None:
                            raise ConnectionError("05との接続が閉じられました")
                        self._handle_ack(response)
                        self._send_latest_if_available()
            except (OSError, ValueError, ConnectionError) as exc:
                self._connection_ready.clear()
                LOGGER.warning("05 Android位置情報連携との接続を待機します: %s", exc)
            finally:
                with self._lock:
                    self._android_socket = None
                    self.connection_id = ""
            self._stop.wait(delay)
            delay = min(self.reconnect_max_s, delay * 2)

    def _send_latest_if_available(self) -> None:
        """LocationBridgeServiceの_send_latest_if_availableの内部処理を実行する。"""
        with self._lock:
            position = self.latest
            connection_id = self.connection_id
            epoch = self.delivery_epoch
            if position is not None:
                key = (str(position.payload["estimate_id"]), int(position.payload["sequence"]), epoch)
                if key == self._last_sent_key:
                    return
        if position is None or not self._connection_ready.is_set() or not connection_id:
            return
        try:
            payload = self.validator.prepare_delivery(position, bridge_delay_bound_ms=self.bridge_delay_bound_ms)
        except (TimeoutError, ValueError) as exc:
            self._invalidate(str(exc))
            return
        with self._lock:
            self.tx_sequence += 1
            tx_sequence = self.tx_sequence
            epoch = self.delivery_epoch
        message = {
            "event": "position.update",
            "schema_version": 1,
            "bridge_boot_id": self.bridge_boot_id,
            "connection_id": connection_id,
            "delivery_epoch": epoch,
            "tx_sequence": tx_sequence,
            "position": payload,
        }
        try:
            self._send(message)
            with self._lock:
                self._last_sent_key = (str(payload["estimate_id"]), int(payload["sequence"]), epoch)
        except OSError as exc:
            LOGGER.warning("位置送信に失敗しました: %s", exc)

    def _send(self, message: dict[str, Any]) -> None:
        """メッセージを接続先へ送信する。"""
        with self._lock:
            sock = self._android_socket
        if sock is None:
            raise ConnectionError("05との接続がありません")
        with self._android_send_lock:
            send_frame(sock, message)

    def _handle_ack(self, response: dict[str, Any]) -> None:
        """LocationBridgeServiceの_handle_ackの内部処理を実行する。"""
        if response.get("event") != "bridge.ack":
            return
        with self._lock:
            if response.get("connection_id") != self.connection_id or int(response.get("delivery_epoch", -1)) != self.delivery_epoch:
                return
            self.last_ack = dict(response)
        LOGGER.info("05応答: %s %s", response.get("state"), response.get("reason", ""))

    def _invalidate(self, reason: str) -> None:
        """LocationBridgeServiceの_invalidateの内部処理を実行する。"""
        with self._lock:
            self.delivery_epoch += 1
            self.latest = None
            self._last_sent_key = None
            epoch = self.delivery_epoch
            connection_id = self.connection_id
            self.tx_sequence += 1
            tx_sequence = self.tx_sequence
        if self._connection_ready.is_set() and connection_id:
            try:
                self._send({
                    "event": "position.invalidate",
                    "schema_version": 1,
                    "bridge_boot_id": self.bridge_boot_id,
                    "connection_id": connection_id,
                    "delivery_epoch": epoch,
                    "tx_sequence": tx_sequence,
                    "reason": reason,
                })
            except OSError:
                pass
        LOGGER.info("位置配送を無効化しました: %s", reason)

    def _expiry_loop(self) -> None:
        """LocationBridgeServiceの_expiry_loopの内部処理を実行する。"""
        while not self._stop.wait(0.2):
            with self._lock:
                position = self.latest
            if position is None:
                continue
            try:
                self.validator.prepare_delivery(position, bridge_delay_bound_ms=0)
            except (TimeoutError, ValueError) as exc:
                self._invalidate(str(exc))


def main() -> int:
    """引数を解釈して処理を開始する。"""
    parser = argparse.ArgumentParser(description="04 Linux位置情報連携")
    parser.add_argument("--source-socket", required=True, help="03 現在地補正のUnixソケット")
    parser.add_argument("--android-host", required=True, help="05 Android位置情報連携の接続先")
    parser.add_argument("--android-port", type=int, default=8765)
    parser.add_argument("--max-age-ms", type=int, default=2_000)
    parser.add_argument("--max-accuracy-m", type=float, default=100.0)
    parser.add_argument("--bridge-delay-bound-ms", type=int, default=100)
    parser.add_argument("--allow-dead-reckoning", action="store_true")
    parser.add_argument("--shared-token", default="")
    parser.add_argument("--lock-path", default="/run/user/1000/l880k-location-bridge.lock")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    lock = ProcessLock(args.lock_path)
    try:
        lock.acquire()
    except RuntimeError as exc:
        parser.error(str(exc))
    service = LocationBridgeService(
        args.source_socket, args.android_host, args.android_port, args.max_age_ms, args.max_accuracy_m,
        args.bridge_delay_bound_ms, args.allow_dead_reckoning, args.shared_token,
    )
    signal.signal(signal.SIGINT, lambda *_: service.stop())
    signal.signal(signal.SIGTERM, lambda *_: service.stop())
    try:
        service.run()
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
