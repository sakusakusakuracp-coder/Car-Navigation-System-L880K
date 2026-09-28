"""Waydroidホーム表示時の外部コマンド順序を検証する。"""

import unittest

from navigation.command_runner import CommandResult
from navigation.window_adapter import WindowAdapter


class RecordingRunner:
    def __init__(self, events: list[tuple[str, list[str]]]) -> None:
        self.events = events

    def run(self, argv: list[str], _timeout: float) -> CommandResult:
        self.events.append(("run", argv))
        return CommandResult(0, "", "")


class WaydroidHomeTests(unittest.TestCase):
    def test_home_key_is_sent_after_full_ui_surface_is_started(self) -> None:
        events: list[tuple[str, list[str]]] = []
        adapter = WindowAdapter(
            {
                "commands": {
                    "activate_home": ["sudo", "-n", "waydroid", "shell", "input", "keyevent", "KEYCODE_HOME"],
                    "show_home_window": ["waydroid", "show-full-ui"],
                }
            },
            RecordingRunner(events),
        )

        def start_window(command: list[str], _timeout: float) -> dict:
            events.append(("window", command))
            return {"success": True, "state": "visible", "reason": ""}

        adapter._start_window = start_window

        result = adapter.show_home(1.0)

        self.assertTrue(result["success"])
        self.assertEqual(
            events,
            [
                ("window", ["waydroid", "show-full-ui"]),
                ("run", ["sudo", "-n", "waydroid", "shell", "input", "keyevent", "KEYCODE_HOME"]),
            ],
        )


if __name__ == "__main__":
    unittest.main()
