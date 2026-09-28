"""公式URL・専用ブラウザの所有範囲・停車制限と非同期取消を確認する。"""

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtGui import QGuiApplication
from ui.controllers.official_music import HIDDEN_WORKSPACE, OfficialMusicBrowser, OfficialMusicController, SERVICES
from ui.state.app_state import AppState


def tree(workspace="1", focused=False):
    """個人ブラウザ・自作UI・専用ブラウザが混在するSway応答を用意する。"""
    ui = {"id": 10, "type": "con", "pid": os.getpid(), "app_id": "l880k-car-navigation",
          "visible": True, "fullscreen_mode": 0, "focused": focused,
          "rect": {"x": 100, "y": 200, "width": 1280, "height": 720}}
    owned = {"id": 20, "type": "con", "pid": 12345, "name": "YouTube Music", "app_id": "chromium"}
    other = {"id": 99, "type": "con", "pid": 54321, "name": "YouTube Music", "app_id": "chromium"}
    return {"nodes": [{"type": "workspace", "name": "1", "nodes": [ui, other]},
                      {"type": "workspace", "name": workspace, "floating_nodes": [owned]}]}


class BrowserTest(unittest.TestCase):
    def setUp(self):
        """実際のブラウザやSwayへ接続しない所有プロセスを用意する。"""
        self.browser = OfficialMusicBrowser()
        self.process = Mock(pid=12345)
        self.process.poll.return_value = None
        self.browser.process = self.process
        self.browser.provider = "youtube_music"
        self.browser.started = time.monotonic()
        self.browser.sway = Mock(return_value=tree())

    def test_only_owned_pid_placed_and_visibility_toggle_never_used(self):
        """同名の個人ブラウザには触らず、表示要求を繰り返しても隠さない。"""
        request = ("youtube_music", True, (12, 140, 1256, 470))
        for workspace in ("1", "__i3_scratch", "1"):
            self.browser.sway.side_effect = lambda *args: tree(workspace, True) if args[0] == "-t" else [{"success": True}]
            self.browser.reconcile(request, lambda: True)
        commands = [call.args[0] for call in self.browser.sway.call_args_list if call.args[0] != "-t"]
        self.assertTrue(all("[con_id=20]" in cmd for cmd in commands))
        self.assertFalse(any("scratchpad show" in cmd for cmd in commands))
        self.assertTrue(any("move absolute position 112 px 340 px" in cmd for cmd in commands))
        self.assertTrue(any('move container to workspace "1"' in cmd for cmd in commands))

    def test_hide_on_stale_request_and_screen_exit(self):
        """画面切替時は専用ワークスペースへ退避し、再生用プロセスを維持する。"""
        self.browser.reconcile(("youtube_music", True, (12, 100, 700, 300)), lambda: False)
        self.browser.sway.assert_called_with(f'[con_id=20] move container to workspace "{HIDDEN_WORKSPACE}"')
        self.process.terminate.assert_not_called()
        self.browser.sway.reset_mock()
        self.browser.reconcile(("youtube_music", False, (12, 100, 700, 300)), lambda: True)
        self.browser.sway.assert_called_with(f'[con_id=20] move container to workspace "{HIDDEN_WORKSPACE}"')

    def test_return_to_music_restores_existing_window_without_restart(self):
        """音楽画面へ戻っても既存ブラウザを再起動せず同じウィンドウを戻す。"""
        hidden = tree(HIDDEN_WORKSPACE, False)
        self.browser.sway.side_effect = lambda *args: hidden if args[0] == "-t" else [{"success": True}]
        self.browser.reconcile(("youtube_music", False, (12, 100, 700, 300)), lambda: True)
        self.browser.sway.reset_mock()
        self.browser.reconcile(("youtube_music", True, (12, 100, 700, 300)), lambda: True)
        self.assertFalse(any("move scratchpad" in call.args[0] for call in self.browser.sway.call_args_list))
        self.assertTrue(any('move container to workspace "1"' in call.args[0] for call in self.browser.sway.call_args_list))
        self.process.terminate.assert_not_called()

    def test_exit_and_missing_window_do_not_count_as_playback(self):
        """プロセス終了やウィンドウ未確認を再生成功として扱わない。"""
        self.process.poll.return_value = 0
        with self.assertRaisesRegex(RuntimeError, "終了"):
            self.browser.reconcile(("youtube_music", True, (0, 100, 800, 300)), lambda: True)
        self.assertIsNone(self.browser.process)
        self.browser.process, self.browser.provider = self.process, "youtube_music"
        self.process.poll.return_value = None
        self.browser.sway.return_value = {"nodes": []}
        self.browser.started = time.monotonic() - 21
        with self.assertRaisesRegex(RuntimeError, "確認できません"):
            self.browser.reconcile(("youtube_music", True, (0, 100, 800, 300)), lambda: True)

    def test_nonzero_and_json_failure_are_errors(self):
        """終了コード0でもSwayのsuccess=falseなら失敗にする。"""
        for code, response in ((1, ""), (0, '[{"success":false}]'), (0, "[]")):
            with patch("ui.controllers.official_music.subprocess.run", return_value=Mock(returncode=code, stdout=response)):
                with self.assertRaises(RuntimeError):
                    OfficialMusicBrowser.sway("[con_id=20] focus")

    def test_launch_allowlist_profile_and_close_scope(self):
        """公式URLと分離プロファイルを使用し、安全機能を無効化しない。"""
        with tempfile.TemporaryDirectory() as directory:
            browser = OfficialMusicBrowser(Path(directory) / "web")
            process = Mock(pid=12345)
            process.poll.return_value = None
            with patch.object(browser, "availability", return_value=""), \
                 patch("ui.controllers.official_music.shutil.which", return_value="/usr/bin/chromium"), \
                 patch("ui.controllers.official_music.subprocess.Popen", return_value=process) as spawn:
                with self.assertRaises(ValueError):
                    browser.start("https://malicious.invalid")
                browser.start("youtube_music")
                args = spawn.call_args.args[0]
                self.assertIn("--app=" + SERVICES["youtube_music"], args)
                self.assertIn("--ozone-platform=wayland", args)
                self.assertTrue(any(arg.startswith("--user-data-dir=" + directory) for arg in args))
                self.assertNotIn("--no-sandbox", args)
                self.assertNotIn("--disable-web-security", args)
                browser.close()
                browser.close()
                process.terminate.assert_called_once()
                self.assertTrue((Path(directory) / "web/youtube_music").is_dir())

    def test_stop_prevents_launch(self):
        """起動前の取消ではブラウザ自体を生成しない。"""
        browser = OfficialMusicBrowser()
        with patch.object(browser, "start") as start:
            browser.reconcile(("youtube_music", True, (0, 100, 800, 300)), lambda: False)
            start.assert_not_called()


class ControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Qtの通知を受け取るアプリを共用する。"""
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        """外部サイトへ接続しないブラウザ代替を用意する。"""
        self.state = AppState()
        self.backend = Mock()
        self.backend.availability.return_value = ""
        self.backend.reconcile.return_value = "公式画面を表示"
        self.controller = OfficialMusicController(self.state, browser=self.backend)

    def tearDown(self):
        """各試験後にワーカーとタイマーを停止する。"""
        self.controller.close()

    def settle(self):
        """非同期処理の完了通知を短時間待つ。"""
        end = time.monotonic() + .2
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.005)

    def parked(self):
        """本番と同じ期限付き車速通知で停車許可を与える。"""
        self.state.apply_update({"event": "vehicle.update", "payload": {"key": "vehicle_speed", "value": 0,
            "validity": "VALID", "unit": "m/s", "valid_for_ms": 5000,
            "acquired_mono": time.monotonic(), "time_quality": "monotonic"}})

    def test_parking_fallback_and_hide_on_navigation_and_expiry(self):
        """OBD2未取得時は警告付きで起動し、画面離脱・失効では表示要求だけを解除する。"""
        c = self.controller
        c.select("youtube_music")
        self.settle()
        self.assertTrue(c.running)
        self.assertIn("公式画面を表示", c.status)
        self.parked()
        self.state.setScreen("audio")
        c.set_viewport(12, 100, 776, 250)
        c.open()
        self.settle()
        self.assertTrue(c.request[1])
        self.state.setScreen("navigation")
        self.settle()
        self.assertFalse(c.request[1])
        self.assertTrue(c.running)
        self.state.setScreen("audio")
        self.state.apply_update({"vehicle_speed_kmh": 10})
        self.settle()
        self.assertFalse(c.request[1])

    def test_stale_start_cannot_overwrite_stop(self):
        """起動中の停止要求を優先し、古い完了で表示中へ戻さない。"""
        c = self.controller
        self.parked()
        self.state.setScreen("audio")
        c.select("youtube_music")
        c.set_viewport(0, 100, 800, 300)
        self.settle()
        started, release = threading.Event(), threading.Event()

        def reconcile(request, current):
            """実行中の要求が途中で無効になる状況を再現する。"""
            if request[0]:
                started.set()
                release.wait(2)
                self.assertFalse(current())
                return "古い表示結果"
            return "停止中"

        self.backend.reconcile.side_effect = reconcile
        c.open()
        self.assertTrue(started.wait(1))
        c.stop()
        release.set()
        self.settle()
        self.assertFalse(c.running)
        self.assertEqual(c.status, "停止中")

    def test_failure_closes_browser_and_requires_explicit_retry(self):
        """通信失敗時は所有ブラウザを閉じ、自動再起動を繰り返さない。"""
        self.parked()
        self.state.setScreen("audio")
        c = self.controller
        c.select("spotify")
        c.set_viewport(0, 100, 800, 300)
        self.settle()
        self.backend.reconcile.side_effect = RuntimeError("通信失敗")
        c.open()
        self.settle()
        self.assertFalse(c.running)
        self.assertEqual(c.status, "通信失敗")
        self.assertFalse(c.timer.isActive())
        self.backend.close.assert_called()
