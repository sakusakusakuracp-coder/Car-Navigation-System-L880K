"""Pi 3BサブコントローラからのEthernet JSON Lines受信サービス。"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger("l880k.subcontroller-gateway")


@dataclass
class SourceCursor:
    boot_id: str = ""
    sequence: int = -1


class SubcontrollerGateway:
    """許可したサブコンの最新状態を保持し、古い通知を破棄する。"""

    def __init__(self, host: str, port: int, allowed_sources: set[str] | None = None) -> None:
        """サブコントローラ接続先と受信状態の保存領域を初期化する。"""
        self.host = host
        self.port = port
        self.allowed_sources = allowed_sources or {"15 サブコントローラ1後方系", "16 サブコントローラ2前方系"}
        self._listener: socket.socket | None = None
        self._stop = threading.Event()
        self._latest: dict[str, dict[str, Any]] = {}
        self._cursors: dict[str, SourceCursor] = {}
        self._lock = threading.RLock()
        self._frames = {camera: deque(maxlen=16) for camera in ("front", "rear")}
        self._frame_drops = {camera: 0 for camera in self._frames}
        self._retired_boots: dict[str, set[str]] = {}

    def start(self) -> None:
        """TCP待受を開始し、接続ごとのJSON Lines処理を起動する。"""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(8)
        listener.settimeout(0.5)
        self._listener = listener
        self._stop.clear()
        LOGGER.info("サブコンゲートウェイを%s:%sで待ち受けます", self.host, self.port)
        try:
            while not self._stop.is_set():
                try:
                    client, address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                client.settimeout(0.5)
                threading.Thread(target=self._client_loop, args=(client, address), daemon=True).start()
        finally:
            listener.close()

    def _client_loop(self, client: socket.socket, address: Any) -> None:
        """サブコントローラからの通知を読み、許可された状態だけ受け付ける。"""
        buffer = bytearray()
        try:
            while not self._stop.is_set():
                try:
                    chunk = client.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    return
                buffer.extend(chunk)
                if len(buffer) > 2_000_000:
                    return
                while b"\n" in buffer:
                    raw, rest = buffer.split(b"\n", 1)
                    buffer = bytearray(rest)
                    if not raw.strip():
                        continue
                    operation = None
                    try:
                        message = json.loads(raw.decode("utf-8"))
                        if not isinstance(message, dict):
                            raise ValueError("MESSAGE_INVALID")
                        operation = message.get("operation")
                        response = self.accept(message)
                    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                        response = {"accepted": False, "reason": str(exc)}
                    if operation in {"GET_STATE", "GET_HEALTH", "GET_FRAMES"}:
                        client.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            LOGGER.info("サブコン接続が終了しました: %s", address)
        finally:
            client.close()

    def accept(self, message: dict[str, Any]) -> dict[str, Any]:
        """送信元、スキーマ、連番を検証し、古い通知を破棄する。"""
        source = message.get("source")
        if message.get("operation") == "GET_FRAMES":
            return self.take_frames()
        if message.get("operation") in {"GET_STATE", "GET_HEALTH"}:
            return self.snapshot()
        if source not in self.allowed_sources:
            return {"accepted": False, "reason": "UNAUTHORIZED_SOURCE"}
        if message.get("schema_version") != "1.0":
            return {"accepted": False, "reason": "SCHEMA_VERSION_UNSUPPORTED"}
        boot_id = str(message.get("boot_id", ""))
        try:
            sequence = int(message["sequence"])
        except (KeyError, TypeError, ValueError):
            return {"accepted": False, "reason": "SEQUENCE_INVALID"}
        is_frame = message.get("event") == "camera.frame"
        camera = message.get("camera_id")
        if is_frame:
            expected = {"front": "16 サブコントローラ2前方系", "rear": "15 サブコントローラ1後方系"}
            if not isinstance(camera, str) or camera not in self._frames or expected[camera] != source:
                return {"accepted": False, "reason": "CAMERA_SOURCE_MISMATCH"}
            raw = message.get("jpeg_base64")
            if not isinstance(raw, str) or len(raw) > 533336:
                return {"accepted": False, "reason": "FRAME_TOO_LARGE_OR_MISSING"}
        with self._lock:
            cursor = self._cursors.setdefault(source, SourceCursor())
            retired = self._retired_boots.setdefault(source, set())
            if not boot_id or boot_id in retired:
                return {"accepted": False, "reason": "STALE_BOOT"}
            if cursor.boot_id == boot_id and sequence <= cursor.sequence:
                return {"accepted": False, "reason": "STALE_EVENT"}
            if cursor.boot_id and cursor.boot_id != boot_id:
                retired.add(cursor.boot_id)
                self._latest.pop(source, None)
                for pending in self._frames.values():
                    retained = [item for item in pending if item[0].get("source") != source]
                    pending.clear()
                    pending.extend(retained)
            cursor.boot_id = boot_id
            cursor.sequence = sequence
            if is_frame:
                if len(self._frames[camera]) == self._frames[camera].maxlen:
                    self._frame_drops[camera] += 1
                self._frames[camera].append((dict(message), time.monotonic()))
            else:
                self._latest[source] = dict(message)
        return {"accepted": True, "source": source, "sequence": sequence}

    def take_frames(self) -> dict:
        """録画サービス専用の有限キューを引き渡し、滞留時間も通知する。"""
        with self._lock:
            now = time.monotonic()
            frames = []
            for pending in self._frames.values():
                while pending:
                    message, received = pending.popleft()
                    frames.append({**message, "gateway_age_ms": int((now - received) * 1000)})
            dropped = dict(self._frame_drops)
            self._frame_drops = {camera: 0 for camera in dropped}
        return {"event": "camera.batch", "frames": frames, "dropped": dropped}

    def snapshot(self) -> dict[str, Any]:
        """サブコントローラごとの最新状態をスナップショットで返す。"""
        with self._lock:
            return {"accepted": True, "event": "subcontroller.snapshot", "sources": {key: dict(value) for key, value in self._latest.items()}}

    def stop(self) -> None:
        """待受を停止し、接続受付を終了する。"""
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    """引数を解釈して処理を開始する。"""
    parser = argparse.ArgumentParser(description="L880K Pi 3B sub-controller gateway")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=45050)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        SubcontrollerGateway(args.host, args.port).start()
        return 0
    except OSError as exc:
        LOGGER.error("ゲートウェイを開始できません: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
