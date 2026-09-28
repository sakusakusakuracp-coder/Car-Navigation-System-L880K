"""OBD2専用UnixドメインソケットのJSON Linesサーバー。"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable


def default_socket_path() -> str:
    """OBD2イベント用Unixソケットの標準位置を利用者単位で決める。"""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return str(Path(runtime_dir) / "l880k-obd2.sock")
    uid = getattr(os, "getuid", lambda: "user")()
    return f"/tmp/l880k-obd2-{uid}.sock"


class Obd2EventServer:
    """車両イベントの配信と取得モード変更要求を扱う。"""

    def __init__(self, path: str | None = None, on_request: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None) -> None:
        """イベント配信先とUIからのモード変更処理を初期化する。"""
        self.path = path or default_socket_path()
        self.on_request = on_request
        self._listener: socket.socket | None = None
        self._clients: set[socket.socket] = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._accept_thread: threading.Thread | None = None

    def start(self) -> None:
        """Unixソケットを作成し、クライアント受付スレッドを開始する。"""
        if os.name == "nt":
            raise OSError("OBD2 UnixソケットはLinux実機で使用してください")
        socket_path = Path(self.path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(self.path)
        os.chmod(self.path, 0o660)
        self._listener.listen(4)
        self._listener.settimeout(0.2)
        self._stop.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop, name="obd2-ipc", daemon=True)
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        """接続クライアントを受け付け、各クライアント処理を分離する。"""
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept() if self._listener else (None, None)
            except socket.timeout:
                continue
            except OSError:
                return
            if client is None:
                continue
            with self._lock:
                self._clients.add(client)
            threading.Thread(target=self._client_loop, args=(client,), name="obd2-ipc-client", daemon=True).start()

    def _client_loop(self, client: socket.socket) -> None:
        """JSON Lines要求を読み取り、要求処理結果を同じ接続へ返す。"""
        buffer = bytearray()
        client.settimeout(0.5)
        try:
            while not self._stop.is_set():
                try:
                    chunk = client.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    return
                buffer.extend(chunk)
                while b"\n" in buffer:
                    raw, remainder = buffer.split(b"\n", 1)
                    buffer = bytearray(remainder)
                    if not raw.strip():
                        continue
                    try:
                        request = json.loads(raw.decode("utf-8"))
                        response = self.on_request(request) if self.on_request else None
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        response = {"event": "command_result", "success": False, "reason": str(exc)}
                    if response is not None:
                        self._send(client, response)
        finally:
            with self._lock:
                self._clients.discard(client)
            try:
                client.close()
            except OSError:
                pass

    def _send(self, client: socket.socket, message: dict[str, Any]) -> bool:
        """1件のJSONイベントをクライアントへ送信する。"""
        data = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            with self._lock:
                client.sendall(data)
            return True
        except OSError:
            with self._lock:
                self._clients.discard(client)
            return False

    def publish(self, message: dict[str, Any]) -> None:
        """接続中の全UIクライアントへ車両イベントを配信する。"""
        with self._lock:
            clients = tuple(self._clients)
        for client in clients:
            self._send(client, message)

    def stop(self) -> None:
        """受付停止、接続切断、ソケット削除を行って終了する。"""
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
        for _ in range(5):
            if self._accept_thread is None or not self._accept_thread.is_alive():
                break
            time.sleep(0.02)
        try:
            Path(self.path).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
