"""UI追加機能の保存、実行許可、音楽編集、音声出力照会の回帰試験。"""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import QObject, Signal, qInstallMessageHandler
from PySide6.QtQml import QQmlApplicationEngine
from ui.config.preferences import Preferences, DEFAULTS
from ui.config.file_lock import FileLock
from ui.controllers.feature_controller import FeatureController
from ui.controllers.mopidy_client import MopidyClient
from ui.controllers.audio_output import AudioOutputMonitor
from ui.state.app_state import AppState
from ui.state.warnings import WarningHistory


class PersistenceTest(unittest.TestCase):
    def test_conflicting_preferences_are_not_overwritten(self):
        """読み込み後の他プロセス相当の保存を検出し、再読込後だけ保存を許す。"""
        with tempfile.TemporaryDirectory() as temp:
            a = Preferences(Path(temp) / "prefs.json")
            b = Preferences(a.path)
            a.load()
            b.load()
            a.save({**DEFAULTS, "coolant_warning": 110})
            with self.assertRaisesRegex(ValueError, "別の処理"):
                b.save(DEFAULTS)
            self.assertEqual(b.load()["coolant_warning"], 110)
            b.save(DEFAULTS)
            with FileLock(str(a.path) + ".write.lock"):
                with self.assertRaises(RuntimeError):
                    b.save(DEFAULTS)
            self.assertEqual(b.load(), DEFAULTS)

    def test_warning_history_survives_but_does_not_claim_current_fault(self):
        """過去の発生中警告は未再確認、確認済みは確認済みのまま復元する。"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "warnings.json"
            a = WarningHistory()
            a.update("fan", "ファン故障", severity="critical")
            revision = a.revision
            a.update("fan", "ファン故障", severity="critical")
            self.assertEqual(a.revision, revision)
            a.acknowledge(a.items[0]["id"])
            a.save(path, a.snapshot())
            b = WarningHistory()
            b.load(path)
            self.assertTrue(b.items[0]["previous_run"])
            self.assertTrue(b.items[0]["acknowledged"])
            self.assertFalse(b.items[0]["active"])
            self.assertFalse(b.banner)
            b.update("fan", "ファン故障", severity="critical")
            self.assertEqual(len(b.items), 2)
            self.assertFalse(b.banner["acknowledged"])
            path.write_text('{"schema_version":99}', encoding="utf-8")
            with self.assertRaises(ValueError):
                b.load(path)

    def test_lock_is_released_after_exit(self):
        """同一プロセス内でも二重取得を拒否し、解除後は再取得できる。"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ui.lock"
            with FileLock(path):
                with self.assertRaises(RuntimeError):
                    with FileLock(path):
                        self.fail("二重ロック")
            with FileLock(path):
                pass


class MusicExtensionsTest(unittest.TestCase):
    def test_browse_keeps_more_than_500_and_search_deduplicates(self):
        """ライブラリを切り捨てず、検索結果の同一曲だけをまとめる。"""
        client = MopidyClient()
        refs = [{"uri": f"local:track:{n}", "name": str(n)} for n in range(601)]
        with patch.object(client, "call", return_value=refs):
            self.assertEqual(len(client.browse()), 601)
        with patch.object(client, "call", return_value=[{"tracks": refs[:2]}, {"tracks": refs[:1]}]) as call:
            self.assertEqual(len(client.search("name")), 2)
            self.assertEqual(call.call_args.args[1]["query"], {"any": ["name"]})
        with self.assertRaises(ValueError):
            client.search(" ")

    def test_playlist_edit_refuses_conflict_and_rejection(self):
        """競合検出とバックエンド拒否を成功に変えない。"""
        client = MopidyClient()
        original = {"__model__": "Playlist", "uri": "m3u:list", "name": "old", "tracks": []}
        edited = {**original, "name": "new"}
        with patch.object(client, "call", side_effect=[original, edited]) as call:
            self.assertEqual(client.save_playlist(original, edited), edited)
            self.assertEqual(call.call_args.args, ("core.playlists.save", {"playlist": edited}))
        with patch.object(client, "call", return_value={**original, "last_modified": 5}) as call:
            with self.assertRaisesRegex(ValueError, "別の処理"):
                client.save_playlist(original, edited)
            self.assertEqual(call.call_count, 1)
        with patch.object(client, "call", side_effect=[original, None]):
            with self.assertRaises(ValueError):
                client.save_playlist(original, edited)
        with patch.object(client, "call", side_effect=[original, False]):
            with self.assertRaises(ValueError):
                client.delete_playlist(original)
        with patch.object(client, "call", return_value=None):
            with self.assertRaises(ValueError):
                client.create_playlist("name")

    def test_usb_output_states_and_no_mutating_commands(self):
        """照会失敗を切断と断定せず、監視では音量や経路を書き換えない。"""
        run = Mock(return_value=SimpleNamespace(stdout=json.dumps([
            {"name": "usb-dac", "description": "DAC", "properties": {"device.bus": "usb"}},
            {"name": "hdmi", "properties": {"device.bus": "pci"}}])))
        monitor = AudioOutputMonitor("usb-dac", run)
        with patch("ui.controllers.audio_output.os.name", "posix"):
            self.assertEqual(monitor.status()["status"], "connected")
            monitor.sink_name = "absent"
            self.assertEqual(monitor.status()["status"], "disconnected")
            monitor.sink_name = ""
            self.assertEqual(monitor.status()["status"], "unconfigured")
            run.side_effect = subprocess.TimeoutExpired("pactl", 3)
            self.assertEqual(monitor.status()["status"], "unknown")
        for call in run.call_args_list:
            self.assertEqual(call.args[0], ["pactl", "--format=json", "list", "sinks"])


class Bridge(QObject):
    service_event = Signal(dict)


class ControllerExtensionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"preferences_path": str(Path(self.temp.name) / "prefs.json")}
        self.state, self.bridge = AppState(), Bridge()
        self.features = FeatureController(self.state, self.bridge, self.config)

    def tearDown(self):
        self.features.close()
        self.temp.cleanup()

    def speed(self, value=0, **overrides):
        """運用通知と同じ形式で有効な車速または不正な車速を与える。"""
        payload = {"key": "vehicle_speed", "value": value, "validity": "VALID", "unit": "m/s", "valid_for_ms": 5000,
                   "acquired_mono": time.monotonic(), "time_quality": "monotonic"}
        self.state.apply_update({"event": "vehicle.update", "payload": {**payload, **overrides}})

    def settle(self):
        """非同期処理完了をQtイベントループへ届ける。"""
        end = time.monotonic() + 1
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.01)

    def test_unknown_speed_allows_with_warning_and_moving_stops_playback(self):
        """OBD2未取得時は警告付きで許可し、走行中は操作を禁止する。"""
        with patch.object(self.features, "submit") as submit:
            self.features.apply()
            self.assertFalse(self.features.parked)
            self.assertTrue(self.features.operationAllowed)
            self.assertIn("OBD2", self.features.drivingRestriction)
            submit.assert_called_once()
            submit.reset_mock()
            self.speed()
            self.assertTrue(self.features.parked)
            self.assertTrue(self.features.operationAllowed)
            self.features.apply()
            submit.assert_called_once()
            self.features.playback_url = "file:///tmp/movie.mkv"
            self.speed(.001)
            self.assertEqual(self.features.playbackSource, "")
            self.assertFalse(self.features.parked)
            self.assertFalse(self.features.operationAllowed)
            for data in ({"unit": ""}, {"validity": "STALE"}, {"valid_for_ms": float("inf")}, {"value": False},
                         {"acquired_mono": None}, {"acquired_mono": time.monotonic() - 6},
                         {"acquired_mono": time.monotonic() + 100}, {"time_quality": "unknown"}):
                self.speed(**data)
                self.assertFalse(self.features.parked)
                self.assertTrue(self.features.operationAllowed)
            self.speed()
            self.state._stationary_deadline = time.monotonic() - 1
            self.state._expire_obd_values()
            self.assertFalse(self.features.parked)
            self.assertFalse(self.features.operationAllowed)
            self.state._obd_expiry_deadlines["vehicle_speed"] = time.monotonic() - 1
            self.state._expire_obd_values()
            self.assertTrue(self.features.operationAllowed)
            self.speed()
            self.state.apply_update({"obd2_status": "DISCONNECTED"})
            self.assertFalse(self.features.parked)
            self.assertTrue(self.features.operationAllowed)
            for status in ("OBD2サービス切断", "OBD2サービス未接続"):
                self.state.apply_update({"obd2_status": "POLLING"})
                self.speed()
                self.assertTrue(self.features.parked)
                self.state.apply_update({"obd2_status": status})
                self.assertFalse(self.features.parked)

    def test_pending_playback_not_published_after_vehicle_moves(self):
        """コピーの完了が遅れても走行開始後に動画を表示しない。"""
        self.speed()
        generation = self.features.playback_generation
        self.speed(1)
        self.features._complete("playback", (generation, "/tmp/file.mkv"), "")
        self.assertEqual(self.features.playbackSource, "")

    def test_history_async_persistence_and_single_ui(self):
        """同じ保存先のUIを拒否し、正常終了後は履歴を復元して再起動できる。"""
        with self.assertRaises(RuntimeError):
            FeatureController(AppState(), Bridge(), self.config)
        self.features.history.update("fan", "failure")
        self.features._flush_history()
        self.settle()
        self.assertEqual(self.features.history_saved_revision, self.features.history.revision)
        self.features.close()
        self.features = FeatureController(self.state, self.bridge, self.config)
        self.assertEqual(self.features.warnings[0]["message"], "failure")
        self.assertTrue(self.features.warnings[0]["previous_run"])

    def test_corrupt_history_not_overwritten(self):
        """復元不能な履歴を初期状態のJSONで上書きしない。"""
        self.features.close()
        path = self.features.history_path
        path.write_text("broken", encoding="utf-8")
        self.features = FeatureController(self.state, self.bridge, self.config)
        self.assertFalse(self.features.history_writable)
        self.features.close()
        self.assertEqual(path.read_text(encoding="utf-8"), "broken")

    def test_music_page_and_playlist_draft_operations(self):
        """ページ末尾まで選択でき、編集が保存前の原本を変えない。"""
        self.features._complete("browse", ([{"name": str(n)} for n in range(601)], ["", "local:"], "Library"), "")
        self.features.music_page(12)
        self.assertEqual(self.features.musicLibrary, [{"name": "600"}])
        self.assertFalse(self.features.musicPage["more"])
        self.features.music_page(13)
        self.assertEqual(self.features.musicPage["page"], 12)
        self.speed()
        playlist = {"uri": "m3u:p", "name": "p", "tracks": [{"uri": "one"}, {"uri": "two"}]}
        self.features._complete("playlist_read", playlist, "")
        self.features.move_playlist_track(0, 1)
        self.features.rename_playlist("changed")
        self.assertEqual(self.features.playlistDraft["tracks"][0]["uri"], "two")
        self.assertEqual(self.features.playlist_original, playlist)
        self.features.move_playlist_track(1, -1)
        self.assertEqual(len(self.features.playlistDraft["tracks"]), 1)

    def test_playlist_dialog_renders_at_small_and_large_sizes(self):
        """編集ダイアログを実QMLで開き、長い曲名でもレイアウトエラーがない。"""
        errors = []
        old = qInstallMessageHandler(lambda kind, context, text: errors.append(text))
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("uiFeatures", self.features)
        qml_dir = Path(__file__).resolve().parents[2] / "src/raspberry_pi5/ui/qml"
        source = ('import QtQuick\nimport QtQuick.Controls\nimport "' + qml_dir.as_uri() + '"\n'
                  'ApplicationWindow { visible: true; width: 800; height: 480;'
                  'PlaylistEditor { id: editor; objectName: "editor"; anchors.centerIn: parent; width: parent.width - 24; height: parent.height - 24; Component.onCompleted: open() } }')
        self.speed()
        self.features.playlists_value = [{"uri": "m3u:test", "name": "長いプレイリストのタイトル" * 5}]
        self.features.playlist_editing = {"uri": "m3u:test", "name": "音楽", "tracks": [{"uri": str(n), "name": "長い曲名" * 20} for n in range(50)]}
        self.features.playlist_original = deepcopy(self.features.playlist_editing)
        engine.loadData(source.encode())
        try:
            self.assertTrue(engine.rootObjects(), errors)
            window = engine.rootObjects()[0]
            for width, height in ((800, 480), (1280, 720)):
                window.setWidth(width)
                window.setHeight(height)
                self.settle()
                screenshot = window.grabWindow()
                self.assertFalse(screenshot.isNull())
                if os.environ.get("UI_TEST_SCREENSHOTS"):
                    target = Path(os.environ["UI_TEST_SCREENSHOTS"])
                    target.mkdir(parents=True, exist_ok=True)
                    screenshot.save(str(target / f"playlist-{width}.png"))
            self.assertFalse(any(x in text for text in errors for x in ("Error", "not defined", "Unable to assign", "Binding loop")), errors)
        finally:
            if engine.rootObjects():
                engine.rootObjects()[0].close()
            engine.deleteLater()
            self.app.processEvents()
            qInstallMessageHandler(old)
