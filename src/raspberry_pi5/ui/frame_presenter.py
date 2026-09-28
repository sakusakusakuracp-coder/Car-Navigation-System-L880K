"""JPEGコピーをQML画像へ渡す。映像本文はAppStateへ格納しない。"""

import base64
import threading
import time

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider


class CameraImageProvider(QQuickImageProvider):
    def __init__(self):
        """描画側へ渡す最新画像と排他制御を用意する。"""
        super().__init__(QQuickImageProvider.Image)
        self.lock = threading.Lock()
        self.image = QImage()

    def set_image(self, image):
        """画像を置き換え、既に描画中のQImageの寿命はQtのコピー管理に任せる。"""
        with self.lock:
            self.image = image

    def requestImage(self, identifier, size, requestedSize):
        """Qt描画スレッドに独立した画像参照を返す。"""
        with self.lock:
            result = self.image.copy()
        if size is not None:
            size.setWidth(result.width())
            size.setHeight(result.height())
        return result


class FramePresenter(QObject):
    changed = Signal()

    def __init__(self, bridge):
        """選択方向の画像だけを公開し、更新が止まれば表示を消す。"""
        super().__init__()
        self.provider = CameraImageProvider()
        self.bridge = bridge
        self._camera = ""
        self._source = ""
        self._expires = 0
        self._serial = 0
        self._last_identity = None
        bridge.camera_frame.connect(self.accept_frame)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.expire)
        self.timer.start()

    @Property(str, notify=changed)
    def source(self):
        """QMLのキャッシュに旧画像が残らない一意のURLを返す。"""
        return self._source

    @Slot(str)
    def select_camera(self, camera):
        """画面からの方向指定を通信へ渡し、切替前の映像を即座に消す。"""
        if camera not in {"front", "rear", "left", "right", ""}:
            return
        self._camera = camera
        self.clear()
        self.bridge.subscribe_video(camera)

    @Slot(dict)
    def accept_frame(self, frame):
        """現在の方向・有効期間・JPEG復号を確認できた画像だけ表示する。"""
        if frame.get("camera_id") != self._camera:
            return
        if frame.get("validity") != "VALID" or frame.get("valid_for_ms", 0) <= 0:
            self.clear()
            return
        identity = (frame.get("stream_session_id"), frame.get("sequence"))
        self._expires = time.monotonic() + frame["valid_for_ms"] / 1000
        if identity == self._last_identity:
            return
        try:
            raw = base64.b64decode(frame["jpeg_base64"], validate=True)
            image = QImage.fromData(raw, "JPEG")
            if image.isNull() or image.width() != frame["width"] or image.height() != frame["height"]:
                raise ValueError("JPEG_INVALID")
        except (ValueError, KeyError):
            self.clear()
            return
        self.provider.set_image(image)
        self._last_identity = identity
        self._serial += 1
        self._source = f"image://camera/{self._serial}"
        self.changed.emit()

    def expire(self):
        """通知が途絶えても最後の画像を現在の映像として表示し続けない。"""
        if self._source and time.monotonic() >= self._expires:
            self.clear()

    def clear(self):
        """古い画像の参照と表示URLを解放する。"""
        self._source = ""
        self._last_identity = None
        self.provider.set_image(QImage())
        self.changed.emit()
