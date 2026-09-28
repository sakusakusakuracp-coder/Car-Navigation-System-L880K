"""Pi 5の左右CSIと、既存サブコンゲートウェイからの前後JPEGを取得する。"""

import base64
from io import BytesIO
import json
import logging
import socket
import time
from uuid import uuid4

from camera.frames import Frame

LOGGER = logging.getLogger("l880k.camera.inputs")
SOURCES = {"rear": "15 サブコントローラ1後方系", "front": "16 サブコントローラ2前方系"}


class CaptureAdapter:
    def __init__(self, camera_id, index, config):
        """CSIの番号と方向を固定し、他処理から二重オープンしない。"""
        self.camera_id, self.index, self.config = camera_id, index, config
        self.camera = None

    def open_local_camera(self):
        """Picamera2で設定したCSIカメラを1回だけ開く。"""
        from picamera2 import Picamera2

        self.camera = Picamera2(self.index)
        settings = self.camera.create_video_configuration(main={"size": (self.config.width, self.config.height)},
                                                          controls={"FrameRate": self.config.fps})
        self.camera.configure(settings)
        self.camera.start()
        self.stream = uuid4().hex
        self.sequence = 0

    def accept_frame(self):
        """公式JPEG保存APIで1画像を得る。UTCを撮影時刻として捏造しない。"""
        buffer = BytesIO()
        self.camera.capture_file(buffer, format="jpeg")
        self.sequence += 1
        return Frame(self.camera_id, self.stream, self.sequence, time.monotonic(), buffer.getvalue(),
                     self.config.width, self.config.height)

    def close(self):
        """取得を止め、次回接続のためにCSIの所有を解放する。"""
        if self.camera is not None:
            try:
                self.camera.stop()
            finally:
                self.camera.close()
                self.camera = None


class StreamReceiver:
    def __init__(self, config):
        """前後入力の連番と廃止した起動世代を別々に保持する。"""
        self.config = config
        self._cursors = {}
        self._retired = {c: set() for c in SOURCES}

    def accept_frame(self, message):
        """送信元・方向・鮮度・連番を照合し、旧接続や重複映像を拒否する。"""
        camera = message.get("camera_id")
        if camera not in self.config.remote_cameras or message.get("source") != SOURCES[camera]:
            raise ValueError("CAMERA_SOURCE_MISMATCH")
        if message.get("schema_version") != "1.0" or message.get("event") != "camera.frame":
            raise ValueError("CAMERA_SCHEMA_INVALID")
        boot, stream, seq = message.get("boot_id"), message.get("stream_session_id"), message.get("frame_sequence")
        if not isinstance(boot, str) or not boot or not isinstance(stream, str) or not stream or type(seq) is not int or seq < 1:
            raise ValueError("FRAME_SEQUENCE_INVALID")
        generation = (boot, stream)
        previous = self._cursors.get(camera)
        if generation in self._retired[camera] or (previous and previous[0] == generation and seq <= previous[1]):
            raise ValueError("FRAME_STALE")
        age = message.get("gateway_age_ms")
        if type(age) not in (int, float) or not 0 <= age < self.config.frame_valid_for_ms:
            raise ValueError("FRAME_EXPIRED")
        raw = message.get("jpeg_base64", "")
        if not isinstance(raw, str) or len(raw) > ((self.config.max_frame_bytes + 2) // 3) * 4:
            raise ValueError("FRAME_TOO_LARGE")
        jpeg = base64.b64decode(raw, validate=True)
        frame = Frame(camera, boot + "/" + stream, seq, time.monotonic() - age / 1000,
                      jpeg, message.get("width", 0), message.get("height", 0), message.get("captured_at"), "UNSYNCHRONIZED")
        if previous and previous[0] != generation:
            self._retired[camera].add(previous[0])
        self._cursors[camera] = (generation, seq)
        return frame

    def connect_remote(self, stop, on_frame, health):
        """ゲートウェイの有限キューを取り出し、切断時もローカル録画を止めない。"""
        while not stop.is_set():
            try:
                with socket.create_connection((self.config.gateway_host, self.config.gateway_port), timeout=2) as client:
                    with client.makefile("rb") as reader:
                        while not stop.is_set():
                            client.sendall(b'{"operation":"GET_FRAMES"}\n')
                            line = reader.readline(24000001)
                            if not line or len(line) > 24000000 or not line.endswith(b"\n"):
                                raise ValueError("GATEWAY_RESPONSE_INVALID")
                            response = json.loads(line)
                            if response.get("event") != "camera.batch":
                                raise ValueError("GATEWAY_UPDATE_REQUIRED")
                            for camera, count in response.get("dropped", {}).items():
                                if camera in self.config.remote_cameras:
                                    health.dropped(camera, count)
                            for message in response.get("frames", []):
                                try:
                                    frame = self.accept_frame(message)
                                    on_frame(frame)
                                except (ValueError, TypeError, KeyError) as exc:
                                    LOGGER.debug("前後フレームを除外: %s", exc)
                            stop.wait(0.03)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                for camera in self.config.remote_cameras:
                    health.update(camera, input_state="DISCONNECTED", reason=type(exc).__name__)
                stop.wait(self.config.reconnect_delay_s)
