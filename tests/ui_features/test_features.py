"""設定の永続化・実通信・警告状態・録画取得とUIの回帰試験。"""

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QObject, Signal, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication, QFont, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from camera.service import CameraService
from camera.config import CameraConfig
from camera.catalog import RecordingCatalog
from camera.health import HealthMonitor
from camera.frames import Frame
from camera.segment_writer import SegmentWriter
from tests.camera.test_camera import jpeg
from ui.config.preferences import Preferences, DEFAULTS
from ui.controllers.feature_controller import FeatureController
from ui.controllers.mopidy_client import MopidyClient
from ui.controllers.recording_library import RecordingLibrary
from ui.controllers.screen_controller import ScreenController
from ui.frame_presenter import FramePresenter
from ui.state.app_state import AppState
from ui.state.warnings import WarningHistory
from ui.controllers.error_messages import command_failure


def monotonic_ms():
    """同一ホスト上の通知時刻をミリ秒で作る。"""
    return int(time.monotonic() * 1000)


class PreferencesTest(unittest.TestCase):
    def test_atomic_save_backup_restore_and_bad_value(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Preferences(Path(temp) / "prefs.json")
            values = {**DEFAULTS, "coolant_warning": 115, "animations": False}
            store.save(values)
            store.save(values, backup=True)
            store.save(DEFAULTS)
            store.save(store.load(backup=True))
            self.assertEqual(store.load(), values)
            for invalid in ({"coolant_warning": 300}, {"voltage_warning": float("nan")}, {"night_mode": "false"}, {"startup_screen": []}):
                with self.assertRaises(ValueError):
                    store.save({**values, **invalid})
            with patch("ui.config.preferences.os.replace", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    store.save(DEFAULTS)
            self.assertEqual(store.load(), values)
            self.assertEqual(list(Path(temp).glob(".settings-*")), [])

    def test_warning_confirm_is_not_recovery_and_recurrence_unacknowledged(self):
        history = WarningHistory()
        history.update("fan", "ファン異常", severity="critical")
        history.acknowledge(history.items[0]["id"])
        self.assertTrue(history.items[0]["active"])
        self.assertFalse(history.banner)
        history.update("fan", "", False)
        history.update("fan", "ファン異常", severity="critical")
        self.assertFalse(history.banner["acknowledged"])
        self.assertEqual(len(history.items), 2)


class MopidyTest(unittest.TestCase):
    def test_http_status_commands_and_rejection(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(message)
                results = {"core.playback.get_state": "playing", "core.playback.get_current_track": {"name": "実曲", "length": 60000},
                           "core.playback.get_time_position": 1234, "core.mixer.get_volume": 37,
                           "core.tracklist.get_tl_tracks": [], "core.mixer.set_volume": True,
                           "core.playback.seek": False, "core.library.browse": [{"name": "音楽", "type": "directory", "uri": "file:"}]}
                response = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": results.get(message["method"])}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = MopidyClient(f"http://127.0.0.1:{server.server_port}/mopidy/rpc")
            self.assertEqual(client.status()["title"], "実曲")
            self.assertEqual(client.command("volume", 37)["volume"], 37)
            self.assertTrue(any(x["params"] == {"volume": 37} for x in calls))
            self.assertEqual(client.browse()[0]["uri"], "file:")
            client.command("select", 17.0)
            self.assertTrue(any(x["params"] == {"tlid": 17} for x in calls))
            with self.assertRaises(ValueError):
                client.command("select", 17.5)
            with self.assertRaises(ValueError):
                client.command("seek", 300)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


class RecordingSettingsTest(unittest.TestCase):
    def test_drive_control_changes_pause_only(self):
        from types import SimpleNamespace
        import signal
        from unittest.mock import Mock
        from drive_upload.catalog_reader import CatalogReader
        from drive_upload.config import UploadConfig
        from drive_upload_service import run_service

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = UploadConfig(root, root / "catalog.sqlite", root / "credentials.json")
            catalog = CatalogReader(config.catalog_path, root)
            handlers = {}
            server = Mock()

            def create_server(path, callback):
                """通信受付だけを置き換え、実台帳への一時停止保存を検証する。"""
                def start():
                    paused = callback({"operation": "pause"})
                    self.assertEqual(paused, {"accepted": True, "paused": True, "enabled": False})
                    self.assertTrue(CatalogReader(config.catalog_path, root).is_paused())
                    resumed = callback({"operation": "resume"})
                    self.assertFalse(resumed["paused"])
                    self.assertFalse(catalog.is_paused())
                    self.assertFalse(callback({"operation": "enable"})["accepted"])
                    handlers[signal.SIGTERM](None, None)
                server.start.side_effect = start
                return server

            args = SimpleNamespace(dry_run=False, once=False, socket=str(root / "upload.sock"))
            with patch("drive_upload_service.signal.signal", side_effect=lambda key, value: handlers.update({key: value})), \
                 patch("drive_upload_service.JsonLinesEventServer", side_effect=create_server), \
                 patch("drive_upload_service.authorized_session") as authorize:
                self.assertEqual(run_service(config, catalog, args), 0)
                authorize.assert_not_called()
            self.assertFalse(config.enabled)
            self.assertFalse(config.auto_delete_enabled)
            server.stop.assert_called_once()

    def test_settings_persist_and_pausing_closes_segment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = CameraConfig(root, root / "catalog.sqlite", local_cameras={"left": 0}, remote_cameras=(),
                                  low_space_bytes=1024, resume_space_bytes=2048, segment_max_bytes=1048576)
            service = CameraService(config)
            thread = threading.Thread(target=service._record, args=("left",))
            service._writers.append(thread)
            thread.start()
            try:
                service.accept_frame(Frame("left", "test", 1, time.monotonic(), jpeg(), 64, 48))
                end = time.monotonic() + 3
                while not service.status()["recording"] and time.monotonic() < end:
                    time.sleep(.01)
                self.assertTrue(service.status()["recording"])
                service.apply_settings({"recording_enabled": False, "segment_duration_s": 120})
                end = time.monotonic() + 3
                while service.status()["recording"] and time.monotonic() < end:
                    time.sleep(.01)
                self.assertFalse(service.status()["recording"])
                self.assertTrue(thread.is_alive())
                self.assertFalse(CameraService(config).config.recording_enabled)
                with self.assertRaises(ValueError):
                    service.apply_settings({"recording_enabled": True, "segment_duration_s": 600})
                self.assertFalse(service.config.recording_enabled)
            finally:
                self.assertTrue(service.close())

    def test_recording_copy_and_list_without_mutating_upload_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "camera.json"
            config_path.write_text(json.dumps({"recording_root": str(root), "catalog_path": str(root / "catalog.sqlite")}), encoding="utf-8")
            config = CameraConfig.load(config_path)
            catalog = RecordingCatalog(config)
            writer = SegmentWriter("front", config, catalog, HealthMonitor(config))
            writer.write_packet(Frame("front", "test", 1, time.monotonic(), jpeg(), 64, 48))
            writer.stop_recording()
            library = RecordingLibrary(config_path)
            try:
                page = library.list("front")
                self.assertEqual(len(page["items"]), 1)
                identifier = page["items"][0]["file_id"]
                copy = library.prepare(identifier)
                self.assertTrue(Path(copy).is_file())
                self.assertEqual(library.list("rear")["items"], [])
                with catalog.shared.connect() as db:
                    self.assertEqual(db.execute("SELECT state FROM recordings").fetchone()[0], "READY")
                    db.execute("UPDATE recordings SET state='DELETING'")
                with self.assertRaises(ValueError):
                    library.prepare(identifier)
            finally:
                library.close()


class Bridge(QObject):
    service_event = Signal(dict)
    command_result = Signal(dict)
    camera_frame = Signal(dict)
    def send_command(self, *args):
        pass
    def subscribe_video(self, camera):
        pass


class FeaturesUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        font = Path("C:/Windows/Fonts/meiryo.ttc")
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
            cls.app.setFont(QFont("Meiryo"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state, self.bridge = AppState(), Bridge()
        self.features = FeatureController(self.state, self.bridge, {"preferences_path": str(Path(self.temp.name) / "prefs.json")})
        self.state.apply_update({"event": "vehicle.update", "payload": {"key": "vehicle_speed", "value": 0,
                                "validity": "VALID", "unit": "m/s", "valid_for_ms": 5000,
                                "acquired_mono": time.monotonic(), "time_quality": "monotonic"}})

    def tearDown(self):
        self.features.close()
        self.temp.cleanup()

    def settle(self, seconds=.3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.01)

    def test_async_preferences_failure_and_fan_expiry(self):
        self.features.edit("coolant_warning", 115)
        self.features.apply()
        self.settle()
        self.assertEqual(self.features.settings["coolant_warning"], 115)
        self.features.edit("coolant_warning", 500)
        self.features.apply()
        self.settle()
        self.assertEqual(self.features.settings["coolant_warning"], 115)
        event = {"schema_version": "1.0", "event": "fan.status", "source": "10 冷却ファン制御", "boot_id": "a", "sequence": 1,
                 "pwm_duty": .5, "temperature_c": 55, "fan_rpm": 1200, "status": "RUNNING", "observed_at_monotonic_ms": monotonic_ms()}
        self.features.on_event(event)
        self.assertEqual(self.features.fan["duty"], "50 %")
        self.features.on_event({**event, "pwm_duty": 1})
        self.assertEqual(self.features.fan["duty"], "50 %")
        self.features.fan_deadline = time.monotonic() - 1
        with patch.object(self.features, "submit"):
            self.features.poll()
        self.assertEqual(self.features.fan["status"], "更新停止")
        self.features.on_event({**event, "sequence": 2, "observed_at_monotonic_ms": monotonic_ms() - 6000})
        self.assertEqual(self.features.fan["status"], "更新停止")

    def test_old_poll_does_not_overwrite_music_command_result(self):
        self.features.audio_value = {"connected": True, "title": "新しい曲"}
        self.features.music_generation = 2
        self.features.poll_generation = 1
        self.features.jobs.add("audio_poll")
        self.features._complete("audio_poll", {"connected": True, "title": "古い曲"}, "")
        self.assertEqual(self.features.audio["title"], "新しい曲")
        self.assertNotIn("audio_poll", self.features.jobs)

    def test_official_music_waits_for_confirmed_pause_and_cancels_on_source_change(self):
        """停止状態不明や音源を選び直した後の古い完了では公式画面を開かない。"""
        self.state.setScreen("audio")
        self.features.web_music.select("youtube_music")
        with patch.object(self.features, "submit"), patch.object(self.features.web_music, "open") as launch:
            self.features.audio_value = {"connected": True, "state": "playing"}
            self.features.open_official_music()
            self.features._complete("audio_command", {"connected": True, "state": "unknown"}, "")
            launch.assert_not_called()
            self.features.audio_value = {"connected": True, "state": "playing"}
            self.features.open_official_music()
            self.features.web_music.select("spotify")
            self.features.web_music.select("youtube_music")
            self.features._complete("audio_command", {"connected": True, "state": "paused"}, "")
            launch.assert_not_called()
            self.features.audio_value = {"connected": True, "state": "playing"}
            self.features.open_official_music()
            self.features._complete("audio_command", {"connected": True, "state": "paused"}, "")
            launch.assert_called_once()

    def test_all_screens_render_at_two_sizes(self):
        errors = []
        old = qInstallMessageHandler(lambda kind, context, text: errors.append(text))
        controller = ScreenController(self.state, self.bridge, {})
        presenter = FramePresenter(self.bridge)
        engine = QQmlApplicationEngine()
        engine.addImageProvider("camera", presenter.provider)
        for key, value in (("appState", self.state), ("screenController", controller), ("cameraPresenter", presenter),
                           ("uiFeatures", self.features), ("embeddedWindow", True)):
            engine.rootContext().setContextProperty(key, value)
        engine.load(str(Path(__file__).resolve().parents[2] / "src/raspberry_pi5/ui/qml/Main.qml"))
        try:
            self.assertTrue(engine.rootObjects(), errors)
            window = engine.rootObjects()[0]
            with patch.object(self.features, "submit"):
                self.features.history.update("test", command_failure({
                    "service": "11 LIVI連携", "action": "show_livi",
                    "reason": "LIVIのウィンドウが見つかりません。" + "原因の長文確認。" * 30 + "\n診断情報の末尾。",
                }))
                for width, height in ((800, 480), (1280, 720)):
                    window.setWidth(width)
                    window.setHeight(height)
                    for screen in ("home", "settings", "audio", "vehicle", "warnings", "recordings", "camera"):
                        self.state.setScreen(screen)
                        self.settle()
                        if screen == "audio":
                            self.features.audio_value = {"connected": True, "title": "長い曲名のテスト " * 5,
                                "artist": "アーティスト名 " * 5, "state": "playing", "position": 10000,
                                "length": 200000, "volume": 40, "tracks": []}
                            self.features.library_items = [{"type": "track", "name": f"{i} 長い曲名 " * 5, "uri": f"local:{i}"} for i in range(601)]
                            self.features.changed.emit()
                            window.findChild(QObject, "musicScreen").setProperty("libraryMode", True)
                            self.settle()
                        image = window.grabWindow()
                        self.assertFalse(image.isNull(), screen)
                        if os.environ.get("UI_TEST_SCREENSHOTS"):
                            output = Path(os.environ["UI_TEST_SCREENSHOTS"])
                            output.mkdir(parents=True, exist_ok=True)
                            image.save(str(output / f"{screen}-{width}.png"))
                        if screen == "audio":
                            music = window.findChild(QObject, "musicScreen")
                            for provider in ("youtube_music", "spotify"):
                                self.features.web_music.select(provider)
                                self.settle()
                                viewport = window.findChild(QObject, "officialMusicViewport")
                                self.assertGreater(viewport.property("width"), 100)
                                self.assertGreater(viewport.property("height"), 100)
                                self.assertLessEqual(viewport.property("y") + viewport.property("height"), music.property("height"))
                                if os.environ.get("UI_TEST_SCREENSHOTS"):
                                    window.grabWindow().save(str(output / f"{provider}-{width}.png"))
                            self.features.web_music.select("local")
                self.features.saved["night_mode"] = False
                self.features.changed.emit()
                self.features.history.acknowledge(self.features.history.items[0]["id"])
                self.features.changed.emit()
                window.setWidth(800)
                window.setHeight(480)
                for screen in ("home", "settings", "audio", "vehicle", "warnings", "recordings", "camera"):
                    self.state.setScreen(screen)
                    self.settle()
                    if os.environ.get("UI_TEST_SCREENSHOTS"):
                        window.grabWindow().save(str(output / f"{screen}-light-800.png"))
            self.assertFalse(any("Error" in text or "not defined" in text or "Unable to assign" in text or "Binding loop" in text for text in errors), errors)
        finally:
            if engine.rootObjects():
                engine.rootObjects()[0].close()
            engine.deleteLater()
            self.app.processEvents()
            qInstallMessageHandler(old)

    def test_duplicate_requests_and_abandoned_playback(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        def prepare(identifier):
            """コピー準備中の画面離脱を再現する。"""
            calls.append(identifier)
            started.set()
            release.wait(2)
            return str(Path(self.temp.name) / "movie.mkv")

        with patch.object(self.features.recordings, "prepare", side_effect=prepare):
            self.features.play_recording("one")
            self.assertTrue(started.wait(1))
            self.features.play_recording("two")
            self.features.stop_playback()
            release.set()
            self.settle()
        self.assertEqual(calls, ["one"])
        self.assertEqual(self.features.playbackSource, "")
        self.features.jobs.add("preferences")
        with patch.object(self.features.preferences, "load") as read:
            self.features.restore()
            self.assertNotIn("restore", self.features.jobs)
            read.assert_not_called()
        self.features.jobs.clear()

    def test_qt_multimedia_decodes_recording_copy(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        root = Path(self.temp.name)
        config = CameraConfig(root, root / "catalog.sqlite", low_space_bytes=1024,
                              resume_space_bytes=2048, segment_max_bytes=1048576)
        catalog = RecordingCatalog(config)
        writer = SegmentWriter("front", config, catalog, HealthMonitor(config))
        begin = time.monotonic()
        for n in range(20):
            writer.write_packet(Frame("front", "test", n, begin + n / 10, jpeg(), 64, 48))
        writer.stop_recording()
        config_path = root / "camera.json"
        config_path.write_text(json.dumps({"recording_root": str(root), "catalog_path": str(config.catalog_path)}), encoding="utf-8")
        library = RecordingLibrary(config_path)
        player, sink = QMediaPlayer(), QVideoSink()
        frames, errors = [], []
        sink.videoFrameChanged.connect(lambda frame: frames.append(frame.isValid()))
        player.errorOccurred.connect(lambda *args: errors.append(args))
        player.setVideoSink(sink)
        try:
            copy = library.prepare(library.list()["items"][0]["file_id"])
            player.setSource(QUrl.fromLocalFile(copy))
            player.play()
            deadline = time.monotonic() + 5
            while not any(frames) and not errors and time.monotonic() < deadline:
                self.settle(.05)
            self.assertFalse(errors, errors)
            self.assertTrue(any(frames), "録画ファイルをQtで復号できませんでした")
        finally:
            player.stop()
            player.setSource(QUrl())
            self.settle(.1)
            library.close()
