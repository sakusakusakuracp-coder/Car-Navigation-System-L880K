"""ホーム画面のOsmAnd・LIVI・Waydroid直通操作を検証する。"""

import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from navigation.command_runner import CommandResult
from navigation.window_adapter import WindowAdapter
from ui.controllers.screen_controller import ScreenController
from ui.state.app_state import AppState


class RecordingBridge(QObject):
    service_event = Signal(dict)
    command_result = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.commands: list[tuple[str, str, dict | None]] = []

    def subscribe_state(self) -> None:
        pass

    def send_command(self, service: str, action: str, payload: dict | None = None) -> None:
        self.commands.append((service, action, payload))


class SwayRunner:
    def __init__(self, tree: dict) -> None:
        self.tree = tree
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], _timeout: float) -> CommandResult:
        self.calls.append(argv)
        if argv[:4] == ["swaymsg", "-t", "get_tree", "-r"]:
            return CommandResult(0, json.dumps(self.tree), "")
        return CommandResult(0, "", "")


class HomeExternalButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self) -> None:
        self.state = AppState()
        self.bridge = RecordingBridge()
        self.controller = ScreenController(
            self.state,
            self.bridge,
            {
                "navigation_viewport": {"x": 0, "y": 62, "width": 1280, "height": 580},
                "external_focus_delay_ms": 1,
            },
        )

    def test_livi_button_selects_livi_and_opens_navigation_viewport(self) -> None:
        self.controller.open_livi()

        self.assertEqual(self.state.navigationMode, "livi")
        self.assertEqual(self.state.screen, "navigation")
        self.assertEqual(self.bridge.commands[-1][0:2], ("11 LIVI連携", "show_livi"))

    def test_home_navigation_button_always_selects_osmand(self) -> None:
        self.controller.open_livi()
        self.controller._finish_transition()
        self.controller.request_screen("home")
        self.controller._finish_transition()
        self.bridge.commands.clear()

        self.controller.open_osmand()

        self.assertEqual(self.state.navigationMode, "osmand")
        self.assertEqual(self.state.screen, "navigation")
        self.assertEqual(self.bridge.commands[-1][0:2], ("02 Waydroidナビ管理", "show_navigation"))

    def test_bottom_navigation_reopens_last_selected_navigation(self) -> None:
        self.controller.open_livi()
        self.controller._finish_transition()
        self.controller.request_screen("home")
        self.controller._finish_transition()
        self.controller.open_waydroid_home()
        self.controller._finish_transition()
        self.controller.request_screen("home")
        self.controller._finish_transition()
        self.bridge.commands.clear()

        # NavigationBarのナビボタンと同じ要求を行う。途中でWaydroidを
        # 表示しても、最後に選択したOsmAnd/LIVIは変更しない。
        self.controller.request_screen("navigation")

        self.assertEqual(self.state.navigationMode, "livi")
        self.assertEqual(self.bridge.commands[-1][0:2], ("11 LIVI連携", "show_livi"))

    def test_waydroid_button_hides_livi_and_shows_waydroid_home(self) -> None:
        self.controller.open_livi()
        self.controller._finish_transition()
        self.bridge.commands.clear()

        self.controller.open_waydroid_home()

        self.assertEqual(self.state.screen, "android_apps")
        external_commands = [
            (service, action)
            for service, action, _payload in self.bridge.commands
            if service in {"02 Waydroidナビ管理", "11 LIVI連携"}
        ]
        self.assertEqual(
            external_commands,
            [
                ("11 LIVI連携", "hide_livi"),
                ("02 Waydroidナビ管理", "show_waydroid_home"),
            ],
        )
        self.assertEqual(self.bridge.commands[-1][2]["mode"], "home")

    def test_leaving_waydroid_hides_waydroid_home(self) -> None:
        self.controller.open_waydroid_home()
        self.controller._finish_transition()
        self.bridge.commands.clear()

        self.controller.request_screen("home")

        self.assertEqual(self.state.screen, "home")
        self.assertIn(("02 Waydroidナビ管理", "hide_waydroid_home"), [command[0:2] for command in self.bridge.commands])

    def test_reselecting_waydroid_only_restores_focus(self) -> None:
        self.controller.open_waydroid_home()
        self.controller._finish_transition()
        self.bridge.commands.clear()

        self.controller.open_waydroid_home()
        QTest.qWait(30)

        self.assertEqual(self.bridge.commands[-1][0:2], ("02 Waydroidナビ管理", "maintain_waydroid_home_focus"))

    def test_reselecting_osmand_restores_focus_after_click_finishes(self) -> None:
        self.controller.open_osmand()
        self.controller._finish_transition()
        self.bridge.commands.clear()

        self.controller.request_screen("navigation")

        self.assertEqual(self.bridge.commands, [])
        QTest.qWait(30)
        self.assertEqual(self.bridge.commands[-1][0:2], ("02 Waydroidナビ管理", "maintain_navigation_focus"))

    def test_reselecting_livi_restores_livi_focus_after_click_finishes(self) -> None:
        self.controller.open_livi()
        self.controller._finish_transition()
        self.bridge.commands.clear()

        self.controller.request_screen("navigation")
        QTest.qWait(30)

        self.assertEqual(self.bridge.commands[-1][0:2], ("11 LIVI連携", "maintain_livi_focus"))

    def test_status_bar_restores_waydroid_as_well_as_navigation_apps(self) -> None:
        status_bar = Path(__file__).resolve().parents[2] / "src/raspberry_pi5/ui/qml/StatusBar.qml"
        source = status_bar.read_text(encoding="utf-8")

        self.assertIn('appState.screen === "android_apps"', source)
        self.assertIn("screenController.restore_external_app_focus()", source)
        self.assertIn("onClicked:", source)

    def test_late_livi_hide_does_not_clear_android_home_visibility(self) -> None:
        self.controller.open_waydroid_home()
        self.state.apply_update({"actual_visibility": "visible"})

        self.controller.on_command_result({"success": True, "state": "SUCCEEDED", "action": "hide_livi"})

        self.assertTrue(self.state.navigationWindowVisible)

    def test_home_window_identifier_does_not_match_osmand(self) -> None:
        tree = {
            "nodes": [
                {"type": "con", "id": 1, "app_id": "waydroid.net.osmand.plus", "name": "OsmAnd", "nodes": [], "floating_nodes": []},
                {"type": "con", "id": 2, "app_id": "Waydroid", "name": "Waydroid", "nodes": [], "floating_nodes": []},
            ],
            "floating_nodes": [],
        }

        found = WindowAdapter._find_sway_window(tree, ["waydroid"], exact=True)

        self.assertEqual(found["id"], 2)

    def test_waydroid_home_starts_full_ui_and_then_sends_home_key(self) -> None:
        runner = SwayRunner({})
        adapter = WindowAdapter(
            {
                "commands": {
                    "activate_home": ["sudo", "-n", "waydroid", "shell", "input", "keyevent", "KEYCODE_HOME"],
                    "show_home_window": ["waydroid", "show-full-ui"],
                }
            },
            runner,
        )
        events: list[list[str]] = []

        def start_window(command: list[str], _timeout: float) -> dict:
            events.append(command)
            return {"success": True, "state": "visible", "reason": ""}

        adapter._start_window = start_window

        result = adapter.show_home(1.0)

        self.assertTrue(result["success"])
        self.assertEqual(events, [["waydroid", "show-full-ui"]])
        self.assertEqual(runner.calls[0], ["sudo", "-n", "waydroid", "shell", "input", "keyevent", "KEYCODE_HOME"])

    def test_waydroid_home_sends_home_key_after_full_ui_is_started(self) -> None:
        events: list[tuple[str, list[str]]] = []

        class OrderedRunner(SwayRunner):
            def run(self, argv: list[str], timeout: float) -> CommandResult:
                events.append(("run", argv))
                return super().run(argv, timeout)

        runner = OrderedRunner({})
        adapter = WindowAdapter(
            {
                "commands": {
                    "activate_home": ["sudo", "-n", "waydroid", "shell", "input", "keyevent", "KEYCODE_HOME"],
                    "show_home_window": ["waydroid", "show-full-ui"],
                }
            },
            runner,
        )

        def start_window(command: list[str], _timeout: float) -> dict:
            events.append(("window", command))
            return {"success": True, "state": "visible", "reason": ""}

        adapter._start_window = start_window

        result = adapter.show_home(1.0)

        self.assertTrue(result["success"])
        self.assertEqual(
            events[:2],
            [
                ("window", ["waydroid", "show-full-ui"]),
                ("run", ["sudo", "-n", "waydroid", "shell", "input", "keyevent", "KEYCODE_HOME"]),
            ],
        )

    def test_waydroid_focus_uses_saved_container_without_relayout(self) -> None:
        runner = SwayRunner({})
        adapter = WindowAdapter({"display_backend": "sway"}, runner)
        adapter._sway_container_id = 7

        result = adapter.focus_existing(1.0)

        self.assertTrue(result["success"])
        self.assertEqual(runner.calls, [["swaymsg", '[con_id="7"]', "focus"]])

    def test_osmand_restore_does_not_run_show_full_ui(self) -> None:
        tree = {
            "nodes": [
                {
                    "type": "con",
                    "id": 7,
                    "app_id": "waydroid.net.osmand.plus",
                    "name": "OsmAnd",
                    "scratchpad_state": "fresh",
                    "nodes": [],
                    "floating_nodes": [],
                }
            ],
            "floating_nodes": [],
        }
        runner = SwayRunner(tree)
        adapter = WindowAdapter(
            {
                "display_backend": "sway",
                "navigation_window_identifiers": ["net.osmand.plus"],
                "commands": {"show_window": ["/usr/bin/waydroid", "show-full-ui"]},
            },
            runner,
        )
        adapter.set_viewport({"x": 0, "y": 62, "width": 1280, "height": 580})

        result = adapter.apply_visibility(True, 1.0)

        self.assertTrue(result["success"])
        self.assertNotIn(["/usr/bin/waydroid", "show-full-ui"], runner.calls)
        self.assertIn(["swaymsg", '[con_id="7"]', "scratchpad", "show"], runner.calls)

    def test_home_qml_exposes_livi_and_android_app_buttons(self) -> None:
        engine = QQmlApplicationEngine()
        context = engine.rootContext()
        context.setContextProperty("appState", self.state)
        context.setContextProperty("screenController", self.controller)
        qml = Path(__file__).resolve().parents[2] / "src/raspberry_pi5/ui/qml/HomeScreen.qml"

        engine.load(str(qml))

        self.assertTrue(engine.rootObjects())
        source = qml.read_text(encoding="utf-8")
        self.assertIn('text: "LIVI"', source)
        self.assertIn('text: "Waydroid"', source)
        self.assertIn('onTriggered: screenController.open_osmand()', source)
        engine.deleteLater()


if __name__ == "__main__":
    unittest.main()
