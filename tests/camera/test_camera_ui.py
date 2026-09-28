"""QMLを実際に読み込み、実画像・方向切替・失効・画面寸法を検証する。"""

import base64
from io import BytesIO
import os
from pathlib import Path
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PIL import Image
from PySide6.QtCore import QObject, Signal, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication, QFont, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from ui.controllers.screen_controller import ScreenController
from ui.frame_presenter import FramePresenter
from ui.state.app_state import AppState


class Bridge(QObject):
    service_event = Signal(dict)
    command_result = Signal(dict)
    camera_frame = Signal(dict)

    def subscribe_video(self, camera):
        self.selected = camera

    def send_command(self, *args):
        pass


class CameraUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        font_path = Path("C:/Windows/Fonts/meiryo.ttc")
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
            cls.app.setFont(QFont("Meiryo"))

    def setUp(self):
        self.errors = []
        self.old_handler = qInstallMessageHandler(lambda kind, context, text: self.errors.append(text))
        self.bridge = Bridge()
        self.state = AppState()
        self.controller = ScreenController(self.state, self.bridge, {})
        self.presenter = FramePresenter(self.bridge)
        self.engine = QQmlApplicationEngine()
        self.engine.addImageProvider("camera", self.presenter.provider)
        context = self.engine.rootContext()
        for key, value in (("appState", self.state), ("screenController", self.controller),
                           ("cameraPresenter", self.presenter), ("embeddedWindow", True)):
            context.setContextProperty(key, value)
        path = Path(__file__).resolve().parents[2] / "src/raspberry_pi5/ui/qml/Main.qml"
        self.engine.load(str(path))
        self.assertTrue(self.engine.rootObjects(), self.errors)
        self.window = self.engine.rootObjects()[0]

    def tearDown(self):
        self.window.close()
        self.engine.deleteLater()
        self.app.processEvents()
        qInstallMessageHandler(self.old_handler)

    def settle(self):
        until = time.monotonic() + .3
        while time.monotonic() < until:
            self.app.processEvents()
            time.sleep(.01)

    def test_render_camera_at_two_sizes_and_expire_image(self):
        self.state.setScreen("camera")
        self.settle()
        self.assertEqual(self.bridge.selected, "front")
        data = BytesIO()
        Image.new("RGB", (640, 480), "#25925c").save(data, "JPEG")
        event = {"camera_id": "front", "validity": "VALID", "valid_for_ms": 5000,
                 "sequence": 1, "stream_session_id": "test", "width": 640, "height": 480,
                 "jpeg_base64": base64.b64encode(data.getvalue()).decode()}
        self.bridge.camera_frame.emit(event)
        self.state.apply_update({"event": "camera.status", "valid_for_ms": 3000, "recording": True,
                                 "storage_state": "OK", "free_bytes": 10000000000,
                                 "cameras": {"front": {"input_state": "STREAMING", "record_state": "RECORDING"}}})
        for width, height in ((1280, 720), (800, 480)):
            self.window.setWidth(width)
            self.window.setHeight(height)
            self.settle()
            screenshot = self.window.grabWindow()
            self.assertFalse(screenshot.isNull())
            # 中央の緑の実画像が描画され、プレースホルダーでないことを検査する。
            pixel = screenshot.pixelColor(width // 2 - 60, height // 2)
            self.assertGreater(pixel.green(), pixel.red() + 40)
            if os.environ.get("CAMERA_TEST_SCREENSHOTS"):
                output = Path(os.environ["CAMERA_TEST_SCREENSHOTS"])
                output.mkdir(parents=True, exist_ok=True)
                screenshot.save(str(output / f"camera-{width}.png"))
        self.presenter._expires = time.monotonic() - 1
        self.presenter.expire()
        self.assertEqual(self.presenter.source, "")
        self.assertFalse(any("Error" in line or "is not" in line for line in self.errors), self.errors)

    def test_rear_direction_and_leaving_camera_unsubscribe(self):
        self.state.setScreen("rear_camera")
        self.settle()
        self.assertEqual(self.bridge.selected, "rear")
        self.state.setScreen("home")
        self.settle()
        self.assertEqual(self.bridge.selected, "")

    def test_recording_status_expires_without_stop_command(self):
        self.state.apply_update({"event": "camera.status", "valid_for_ms": 1000, "recording": True,
                                 "cameras": {"front": {"input_state": "STREAMING"}}})
        self.assertTrue(self.state.recording)
        self.state._camera_deadline = time.monotonic() - 1
        self.state._expire_camera_status()
        self.assertFalse(self.state.recording)
        self.assertEqual(self.state.cameraStates, {})
