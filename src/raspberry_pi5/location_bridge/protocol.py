"""04/05間の長さ付きJSON通信。"""

from __future__ import annotations

import json
import socket
import struct
from typing import Any


DEFAULT_MAX_FRAME_BYTES = 65_536


def encode_frame(message: dict[str, Any], max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> bytes:
    """JSONを4バイトのネットワーク順本文長付きフレームへ変換する。"""
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if not body or len(body) > max_frame_bytes:
        raise ValueError("JSONフレームのサイズが上限を超えています")
    return struct.pack("!I", len(body)) + body


def recv_frame(sock: socket.socket, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> dict[str, Any] | None:
    """1フレームを読み取り、切断時はNoneを返す。"""
    header = _recv_exact(sock, 4)
    if not header:
        return None
    size = struct.unpack("!I", header)[0]
    if size == 0 or size > max_frame_bytes:
        raise ValueError("JSONフレームの本文長が不正です")
    raw = _recv_exact(sock, size)
    if raw is None:
        raise ConnectionError("JSONフレームの途中で接続が閉じられました")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSONフレームの最上位はオブジェクトにしてください")
    return value


def send_frame(sock: socket.socket, message: dict[str, Any], max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> None:
    """send_frameの内部処理を実行する。"""
    sock.sendall(encode_frame(message, max_frame_bytes))


def _recv_exact(sock: socket.socket, size: int) -> bytes | None:
    """_recv_exactの内部処理を実行する。"""
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            return None if not chunks else b"".join(chunks)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
