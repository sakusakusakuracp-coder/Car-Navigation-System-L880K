"""LIVI連携がWaydroidを経由せず、固定契約で動くことを確認する。"""

from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import Mock

from livi.process_adapter import NativeProcessAdapter
from livi.service import LiviIntegration
from livi.window_adapter import LiviWindowAdapter
from ui.config.settings_store import DEFAULT_CONFIG


class LiviWindowAdapterTests(unittest.TestCase):
    """Sway対象の選択とコマンド失敗の扱いを検証する。"""

    def test_home_navigation_round_trip_restores_without_toggling(self):
        """ナビ→ホーム→ナビで復帰し、再押下・再フォーカスでも隠れない。"""
        hidden = False
        restores = 0

        def run(argv, timeout):
            """退避領域内では移動・リサイズだけで表示されないSwayを再現する。"""
            nonlocal hidden, restores
            if argv == ["swaymsg", "-t", "get_tree", "-r"]:
                tree = {"type": "root", "nodes": [{
                    "type": "workspace", "name": "__i3_scratch" if hidden else "1",
                    "floating_nodes": [{"type": "con", "id": 8, "pid": 100,
                                        "app_id": "dev.f-io.livi", "visible": not hidden}],
                }]}
                return Mock(returncode=0, stdout=json.dumps(tree), stderr="")
            if argv[2:] == ["move", "scratchpad"]:
                hidden = True
            elif argv[2:] == ["scratchpad", "show"]:
                hidden = not hidden
                restores += 1
            return Mock(returncode=0, stdout='[{"success": true}]', stderr="")

        runner = Mock()
        runner.run.side_effect = run
        adapter = LiviWindowAdapter({}, runner, lambda: 100)
        adapter.set_viewport(DEFAULT_CONFIG["navigation_viewport"])
        self.assertTrue(adapter.show(0.1)["success"])
        for _ in range(3):
            self.assertTrue(adapter.hide(0.1)["success"])
            self.assertTrue(hidden)
            self.assertTrue(adapter.show(0.1)["success"])
            self.assertFalse(hidden)
            self.assertTrue(adapter.focus(0.1)["success"])
            self.assertTrue(adapter.show(0.1)["success"])
            self.assertFalse(hidden)
        self.assertEqual(restores, 3)

    def test_occluded_window_is_not_toggled_into_scratchpad(self):
        """UI背面でvisible=falseでも退避領域外ならscratchpad showを送らない。"""
        tree = {"type": "workspace", "name": "1", "floating_nodes": [
            {"type": "con", "id": 8, "pid": 100, "visible": False, "scratchpad_state": "changed"}
        ]}
        runner = Mock()
        runner.run.return_value = Mock(returncode=0, stdout=json.dumps(tree), stderr="")
        adapter = LiviWindowAdapter({}, runner, lambda: 100)
        adapter.set_viewport(DEFAULT_CONFIG["navigation_viewport"])
        self.assertTrue(adapter.focus(0.1)["success"])
        self.assertFalse(any(call.args[0][2:] == ["scratchpad", "show"] for call in runner.run.call_args_list))

    def test_restore_failure_is_not_reported_as_visible(self):
        """退避からの復帰に失敗した場合は配置を進めず非表示のまま返す。"""
        tree = {"type": "workspace", "name": "__i3_scratch", "floating_nodes": [
            {"type": "con", "id": 8, "pid": 100}
        ]}
        runner = Mock()
        runner.run.side_effect = [Mock(returncode=0, stdout=json.dumps(tree), stderr=""),
                                  Mock(returncode=1, stdout="", stderr="No matching node")]
        adapter = LiviWindowAdapter({}, runner, lambda: 100)
        adapter.set_viewport(DEFAULT_CONFIG["navigation_viewport"])
        self.assertFalse(adapter.show(0.1)["success"])
        self.assertEqual(adapter.actual_visibility, "hidden")
        self.assertEqual(runner.run.call_count, 2)

    def test_centered_resize_does_not_cover_navigation_bar(self):
        """中心基準のサイズ変更後も上下のUIバーを覆わず、再配置でずれない。"""
        for initial_size in [(1280, 720), (1920, 1080), (800, 480)]:
            with self.subTest(initial_size=initial_size):
                rect = {"x": 0, "y": 0, "width": initial_size[0], "height": initial_size[1]}
                viewport = dict(DEFAULT_CONFIG["navigation_viewport"])
                fullscreen = True

                def run(argv, timeout):
                    """Swayの中心基準リサイズと最終位置指定を再現する。"""
                    nonlocal fullscreen
                    if argv[2:] == ["fullscreen", "disable"]:
                        fullscreen = False
                    elif argv[2:4] == ["resize", "set"]:
                        width, height = int(argv[4]), int(argv[6])
                        rect["x"] += (rect["width"] - width) / 2
                        rect["y"] += (rect["height"] - height) / 2
                        rect.update(width=width, height=height)
                    elif argv[2:4] == ["move", "position"]:
                        rect.update(x=int(argv[4]), y=int(argv[6]))
                    return Mock(returncode=0, stdout='[{"success": true}]', stderr="")

                runner = Mock()
                runner.run.side_effect = run
                adapter = LiviWindowAdapter({}, runner, lambda: 100)
                adapter.set_viewport(viewport)
                for _ in range(2):
                    self.assertTrue(adapter._place(8)["success"])
                    self.assertFalse(fullscreen)
                    self.assertEqual(rect, viewport)
                    self.assertEqual(rect["y"], 62)
                    self.assertEqual(rect["y"] + rect["height"], 720 - 78)

    def test_pid_is_prioritized_over_window_title(self):
        """同名の別ウィンドウがあってもLIVI PIDを優先する。"""
        tree = {
            "type": "root",
            "nodes": [
                {"type": "con", "id": 1, "pid": 200, "name": "LIVI"},
                {"type": "con", "id": 2, "pid": 100, "name": "LIVI"},
            ],
            "floating_nodes": [],
        }
        runner = Mock()
        runner.run.return_value = Mock(returncode=0, stdout=json.dumps(tree), stderr="")
        adapter = LiviWindowAdapter({"window_identifiers": ["livi"]}, runner, lambda: 100)
        adapter.set_viewport({"x": 0, "y": 56, "width": 1280, "height": 578})

        result = adapter.show(0.1)

        self.assertTrue(result["success"])
        self.assertIn('[con_id="2"]', runner.run.call_args_list[1].args[0])

    def test_sway_failure_is_not_reported_as_visible(self):
        """Swayの失敗応答を成功扱いにしない。"""
        runner = Mock()
        runner.run.return_value = Mock(returncode=1, stdout="", stderr="invalid command")
        adapter = LiviWindowAdapter({}, runner, lambda: 100)
        adapter.container_id = 12
        adapter.set_viewport(DEFAULT_CONFIG["navigation_viewport"])

        result = adapter.focus(0.01)

        self.assertFalse(result["success"])
        self.assertEqual(result["state"], "UNKNOWN")

    def test_focus_uses_saved_container_without_relayout(self):
        """表示中のLIVIはSway再検索や再配置をせず直ちに前面へ戻す。"""
        runner = Mock()
        runner.run.return_value = Mock(returncode=0, stdout="", stderr="")
        adapter = LiviWindowAdapter({}, runner, lambda: 100)
        adapter.container_id = 12

        result = adapter.focus(0.1)

        self.assertTrue(result["success"])
        runner.run.assert_called_once_with(["swaymsg", '[con_id="12"]', "focus"], 0.1)


class LiviIntegrationTests(unittest.TestCase):
    """LIVI状態の公開契約とネイティブ起動を検証する。"""

    def test_phone_connection_is_unknown_until_verified(self):
        """未確認のLIVI APIを推測せず、電話接続状態をUNKNOWNで返す。"""
        integration = LiviIntegration({"executable": "/missing/livi"})
        status = integration.status()

        self.assertEqual(status["phone_connection"], "UNKNOWN")
        self.assertEqual(status["service"], "11 LIVI連携")

    def test_missing_executable_returns_explicit_failure(self):
        """実行ファイル未配置時に、Waydroid起動へフォールバックしない。"""
        integration = LiviIntegration({"executable": "/missing/livi"})

        result = integration.handle_request("test-1", "start")

        self.assertFalse(result["success"])
        self.assertIn("実行ファイルが見つかりません", result["reason"])

    def test_native_process_uses_argument_list(self):
        """LIVI起動がshell文字列ではなく引数配列で行われる。"""
        adapter = NativeProcessAdapter({"executable": sys.executable, "args": ["-c", "import time; time.sleep(2)"]})
        try:
            result = adapter.start(0.01)
            self.assertTrue(result["success"])
            self.assertIsNotNone(adapter.pid)
        finally:
            adapter.stop(1.0)


if __name__ == "__main__":
    unittest.main()
