"""UI側のカメラ専用IPC。最新画像1件だけを保持して表示遅延を蓄積しない。"""

import json
import socket
import threading
import time
from uuid import uuid4


class CameraClient:
    def __init__(self, path, on_status):
        """画像と状態の通信を録画サービスの寿命から分離する。"""
        self.path, self.on_status = path, on_status
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.camera = None
        self.generation = 0
        self.pending = None
        self.consumer = uuid4().hex
        self.thread = None

    def start(self):
        """通信処理を一度だけ起動する。"""
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="camera-ui-ipc", daemon=True)
            self.thread.start()

    def select(self, camera):
        """表示方向変更時に世代を進め、以前の方向の未表示画像を破棄する。"""
        with self.lock:
            if camera != self.camera:
                self.camera = camera
                self.generation += 1
                self.pending = None

    def take_latest(self):
        """Qtの表示周期で最新1件だけ取り出し、キューの無制限増大を防ぐ。"""
        with self.lock:
            event, self.pending = self.pending, None
        if event is not None:
            event["valid_for_ms"] = max(0, int((event.pop("_expires") - time.monotonic()) * 1000))
        return event

    @staticmethod
    def _request(client, reader, request):
        """要求と応答を1件ずつ照合し、画像サイズ以上の行は受け付けない。"""
        client.sendall((json.dumps(request) + "\n").encode())
        raw = reader.readline(1500001)
        if not raw or len(raw) > 1500000 or not raw.endswith(b"\n"):
            raise ValueError("CAMERA_RESPONSE_INVALID")
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise ValueError("CAMERA_RESPONSE_INVALID")
        return response

    def _run(self):
        """再接続しながら状態と選択方向の映像を照会し、世代の違う画像を捨てる。"""
        if not hasattr(socket, "AF_UNIX"):
            self.on_status({"camera_disconnected": True, "reason": "UNIX_SOCKET_UNSUPPORTED"})
            return
        while not self.stop.is_set():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(1)
                    client.connect(self.path)
                    with client.makefile("rb") as reader:
                        subscription, active_generation, status_at = None, -1, 0
                        while not self.stop.is_set():
                            now = time.monotonic()
                            if now - status_at >= 1:
                                self.on_status(self._request(client, reader, {"operation": "status"}))
                                status_at = now
                            with self.lock:
                                camera, generation = self.camera, self.generation
                            if active_generation != generation:
                                if subscription:
                                    self._request(client, reader, {"operation": "unsubscribe_video", "subscription_id": subscription})
                                subscription = None
                                if camera:
                                    result = self._request(client, reader, {"operation": "subscribe_video", "camera_id": camera,
                                                                          "display_generation": generation, "consumer_boot_id": self.consumer})
                                    subscription = result["subscription_id"]
                                active_generation = generation
                            if subscription:
                                requested = time.monotonic()
                                frame = self._request(client, reader, {"operation": "get_frame", "subscription_id": subscription})
                                if frame.get("subscription_id") != subscription or frame.get("display_generation") != generation:
                                    raise ValueError("FRAME_REQUEST_MISMATCH")
                                frame["_expires"] = requested + frame.get("valid_for_ms", 0) / 1000
                                with self.lock:
                                    if generation == self.generation:
                                        self.pending = frame
                            self.stop.wait(0.1)
            except (OSError, ValueError, TypeError, KeyError):
                self.on_status({"camera_disconnected": True})
                self.stop.wait(1)

    def close(self):
        """通信だけを終了し、録画サービスには停止要求を送らない。"""
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
