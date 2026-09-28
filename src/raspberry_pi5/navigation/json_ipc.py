"""Unixドメインソケット上でJSON Linesを交換するIPCサーバー。"""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Any, Callable


JsonHandler = Callable[[dict[str, Any]], dict[str, Any]]


class JsonLineServer:
    """1行1JSONオブジェクトの要求を受け付ける常駐サーバー。"""

    def __init__(self, path: str, request_handler: JsonHandler, max_line_bytes: int = 65_536) -> None:
        """設定と内部状態を初期化する。"""
        self.path = Path(path)
        self.request_handler = request_handler
        self.max_line_bytes = max_line_bytes
        self._server: socket.socket | None = None
        self._clients: set[socket.socket] = set()
        self._clients_lock = threading.Lock()
        self._stopping = threading.Event()

    def serve_forever(self) -> None:
        """ソケットを作成し、停止指示まで接続を処理する。"""
        if not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("UnixドメインソケットはLinux実行環境で利用してください")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_socket()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server = server
        try:
            server.bind(str(self.path))
            os.chmod(self.path, 0o660)
            server.listen(8)
            server.settimeout(1.0)
            while not self._stopping.is_set():
                try:
                    client, _ = server.accept()
                except socket.timeout:
                    continue
                with self._clients_lock:
                    self._clients.add(client)
                threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()
        finally:
            self.stop()

    def stop(self) -> None:
        """新規接続を止め、接続とソケットを閉じる。"""
        self._stopping.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass

    def publish(self, event: dict[str, Any]) -> None:
        """接続中のUIへ状態通知を配信する。"""
        payload = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.sendall(payload)
            except OSError:
                self._discard_client(client)

    def _handle_client(self, client: socket.socket) -> None:
        """接続中クライアントからの要求を処理する。"""
        buffer = b""
        try:
            while not self._stopping.is_set():
                chunk = client.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > self.max_line_bytes:
                    self._send(client, {"accepted": False, "reason": "JSON要求が大きすぎます"})
                    break
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    if not raw.strip():
                        continue
                    self._send(client, self._dispatch(raw))
        finally:
            self._discard_client(client)

    def _dispatch(self, raw: bytes) -> dict[str, Any]:
        """受信データを検証して処理へ振り分ける。"""
        try:
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("JSON要求の最上位はオブジェクトにしてください")
            return self.request_handler(request)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
            return {"accepted": False, "reason": f"JSON要求が不正です: {exc}"}

    @staticmethod
    def _send(client: socket.socket, response: dict[str, Any]) -> None:
        """メッセージを接続先へ送信する。"""
        payload = (json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            client.sendall(payload)
        except OSError:
            pass

    def _discard_client(self, client: socket.socket) -> None:
        """切断クライアントを管理対象から外す。"""
        with self._clients_lock:
            self._clients.discard(client)
        try:
            client.close()
        except OSError:
            pass

    def _remove_stale_socket(self) -> None:
        """残留ソケットを確認し、不要なら削除する。"""
        if not self.path.exists():
            return
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(self.path))
        except OSError:
            self.path.unlink()
        else:
            raise RuntimeError(f"既にIPCソケットを使用中です: {self.path}")
        finally:
            probe.close()
