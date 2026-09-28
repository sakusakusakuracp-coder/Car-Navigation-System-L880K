"""JPEG内容の検査と、利用者ごとの最新フレーム配信。"""

import base64
from dataclasses import dataclass
from io import BytesIO
import threading
import time
from uuid import uuid4

from camera.config import CAMERAS


@dataclass(frozen=True)
class Frame:
    camera_id: str
    stream_session_id: str
    sequence: int
    received_mono: float
    jpeg: bytes
    width: int
    height: int
    captured_at: str | None = None
    clock_quality: str = "UNKNOWN"


def validate_jpeg(data: bytes, max_bytes: int, width: int, height: int):
    """宣言寸法と実画像を照合し、破損・過大な画像を録画前に拒否する。"""
    from PIL import Image

    if not data or len(data) > max_bytes or type(width) is not int or type(height) is not int or not (0 < width <= 3840 and 0 < height <= 2160):
        raise ValueError("FRAME_SIZE_INVALID")
    with Image.open(BytesIO(data)) as image:
        if image.format != "JPEG" or image.size != (width, height):
            raise ValueError("FRAME_FORMAT_INVALID")
        image.verify()
    with Image.open(BytesIO(data)) as image:
        image.load()


class FrameDistributor:
    """不変JPEGのコピーを返すため、遅い利用者が録画バッファを占有しない。"""

    def __init__(self, valid_for_ms: int):
        """全方向の最新1件と、上限付き利用登録を用意する。"""
        self.valid_for_ms = valid_for_ms
        self._latest = {}
        self._subscriptions = {}
        self._lock = threading.RLock()

    def publish_frame(self, frame: Frame):
        """最新フレームを置き換え、過去画像のキューを蓄積しない。"""
        with self._lock:
            self._latest[frame.camera_id] = frame

    def subscribe_frames(self, camera_id: str, generation: int, consumer: str):
        """方向と表示要求の世代を固定した利用IDを返す。"""
        if camera_id not in CAMERAS or type(generation) is not int or generation < 0 or not consumer or len(consumer) > 128:
            raise ValueError("SUBSCRIPTION_INVALID")
        with self._lock:
            now = time.monotonic()
            self._subscriptions = {k: v for k, v in self._subscriptions.items() if now - v[3] < 10}
            if len(self._subscriptions) >= 16:
                raise ValueError("SUBSCRIPTION_LIMIT")
            key = uuid4().hex
            self._subscriptions[key] = (camera_id, generation, consumer, now)
            return key

    def release_frame(self, subscription_id: str):
        """コピー配信では貸出領域がないため、利用IDの生存だけを確認する。"""
        with self._lock:
            return subscription_id in self._subscriptions

    def unsubscribe_frames(self, subscription_id: str):
        """映像の利用登録だけを解除する。録画は継続する。"""
        with self._lock:
            self._subscriptions.pop(subscription_id, None)

    def get_frame(self, subscription_id: str):
        """期限内の画像だけを返し、応答に表示世代と残り有効期間を付ける。"""
        with self._lock:
            now = time.monotonic()
            entry = self._subscriptions.get(subscription_id)
            if entry is None or now - entry[3] >= 10:
                self._subscriptions.pop(subscription_id, None)
                raise ValueError("SUBSCRIPTION_EXPIRED")
            camera_id, generation, consumer, _ = entry
            self._subscriptions[subscription_id] = (camera_id, generation, consumer, now)
            frame = self._latest.get(camera_id)
        age = int((now - frame.received_mono) * 1000) if frame else self.valid_for_ms
        result = {"event": "camera.frame", "camera_id": camera_id, "subscription_id": subscription_id,
                  "display_generation": generation, "valid_for_ms": max(0, self.valid_for_ms - age),
                  "validity": "STALE"}
        if frame and 0 <= age < self.valid_for_ms:
            result.update(validity="VALID", stream_session_id=frame.stream_session_id, sequence=frame.sequence,
                          width=frame.width, height=frame.height, captured_at=frame.captured_at,
                          clock_quality=frame.clock_quality, age_ms=age, capture_age_ms=None,
                          pixel_format="JPEG", jpeg_base64=base64.b64encode(frame.jpeg).decode("ascii"))
        return result
