"""車載サービス共通のJSON Lines通知ソケット。

ファン制御やドアロック制御のようなPi 5上の常駐サービスは、画面UIと
直接結合せず、同じ形式の状態通知と要求受付を使う。実機でない環境では
サーバーを起動せず、各サービスのdry-runだけを実行できる。
"""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Any, Callable


def runtime_socket_path(name: str) -> str:
    """XDG_RUNTIME_DIRを優先したサービスソケットの既定値を返す。"""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return str(Path(runtime_dir) / name)
    uid = getattr(os, "getuid", lambda: "user")()
    return f"/tmp/{name.removesuffix('.sock')}-{uid}.sock"


class JsonLinesEventServer:
    """状態通知の配信と、読み取り専用要求の受付を行うUnixソケット。"""

    def __init__(
        self,
        path: str,
        on_request: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    ) -> None:
        """設定と内部状態を初期化する。"""
        self.path = path
        self.on_request = on_request
        self._listener: socket.socket | None = None
        self._clients: set[socket.socket] = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """サービスまたはデバイスを起動する。"""
        if os.name == "nt":
            raise OSError("UnixソケットはLinux実機で使用してください")
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.path)
        os.chmod(self.path, 0o660)
        listener.listen(8)
        listener.settimeout(0.2)
        self._listener = listener
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, name="telemetry-ipc", daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        """JsonLinesEventServerの_accept_loopの内部処理を実行する。"""
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept() if self._listener else (None, None)
            except socket.timeout:
                continue
            except OSError:
                return
            if client is None:
                continue
            client.settimeout(0.5)
            with self._lock:
                self._clients.add(client)
            threading.Thread(target=self._client_loop, args=(client,), daemon=True).start()

    def _client_loop(self, client: socket.socket) -> None:
        """JsonLinesEventServerの_client_loopの内部処理を実行する。"""
        buffer = bytearray()
        try:
            while not self._stop.is_set():
                try:
                    chunk = client.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    return
                buffer.extend(chunk)
                if len(buffer) > 65536:
                    self._send(client, {"accepted": False, "reason": "要求が大きすぎます"})
                    return
                while b"\n" in buffer:
                    raw, rest = buffer.split(b"\n", 1)
                    buffer = bytearray(rest)
                    if not raw.strip():
                        continue
                    try:
                        request = json.loads(raw.decode("utf-8"))
                        response = self.on_request(request) if self.on_request else None
                    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                        response = {"accepted": False, "reason": f"JSON要求が不正です: {exc}"}
                    if response is not None:
                        self._send(client, response)
        finally:
            self._discard(client)

    def publish(self, event: dict[str, Any]) -> None:
        """状態またはイベントを購読者へ通知する。"""
        payload = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            clients = tuple(self._clients)
        for client in clients:
            try:
                client.sendall(payload)
            except OSError:
                self._discard(client)

    @staticmethod
    def _send(client: socket.socket, event: dict[str, Any]) -> None:
        """メッセージを接続先へ送信する。"""
        try:
            client.sendall((json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        except OSError:
            pass

    def _discard(self, client: socket.socket) -> None:
        """不要な接続または値を破棄する。"""
        with self._lock:
            self._clients.discard(client)
        try:
            client.close()
        except OSError:
            pass

    def stop(self) -> None:
        """サービスまたはデバイスを停止して資源を解放する。"""
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        with self._lock:
            clients = tuple(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        try:
            Path(self.path).unlink()
        except (FileNotFoundError, OSError):
            pass
