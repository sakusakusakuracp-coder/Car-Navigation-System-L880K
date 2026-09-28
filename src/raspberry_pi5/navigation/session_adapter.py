"""Wayland利用者セッションの操作を閉じ込めるAdapter。"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from .command_runner import CommandRunner


class SessionAdapter:
    def __init__(self, config: dict[str, Any], runner: CommandRunner) -> None:
        """WaylandとWaydroidセッションの確認に必要な状態を初期化する。"""
        self.config = config
        self.runner = runner
        self.started_by_us = False

    def ensure_session(self, timeout: float) -> dict[str, Any]:
        """利用者のWayland上でWaydroidセッションとAndroid起動完了を確認する。"""
        if not os.environ.get("WAYLAND_DISPLAY") or not os.environ.get("XDG_RUNTIME_DIR"):
            return {"success": False, "state": "UNKNOWN", "reason": "Wayland利用者セッション未確認"}
        command = self.config.get("commands", {}).get("session_status")
        if command:
            deadline = time.monotonic() + max(0.1, timeout)
            last_reason = "Waydroidセッション準備中"
            while time.monotonic() < deadline:
                result = self.runner.run(list(command), min(2.0, max(0.1, deadline - time.monotonic())))
                output = f"{result.stdout}\n{result.stderr}"
                session_running = re.search(r"(?m)^Session:\s*RUNNING\s*$", output) is not None
                if result.returncode == 0 and session_running:
                    boot_command = self.config.get("commands", {}).get("android_boot_status")
                    if not boot_command:
                        self.started_by_us = False
                        return {"success": True, "state": "READY", "reason": "Waydroidセッション稼働を確認"}
                    boot = self.runner.run(list(boot_command), min(2.0, max(0.1, deadline - time.monotonic())))
                    boot_output = f"{boot.stdout}\n{boot.stderr}"
                    if boot.returncode == 0 and re.search(r"(?m)^\s*1\s*$", boot_output):
                        self.started_by_us = False
                        return {"success": True, "state": "READY", "reason": "WaydroidセッションとAndroid起動完了を確認"}
                    last_reason = boot.stderr.strip() or boot.stdout.strip() or "Android起動完了待ち"
                elif result.returncode != 0 or not session_running:
                    last_reason = result.stderr.strip() or result.stdout.strip() or last_reason
                time.sleep(0.5)
            return {"success": False, "state": "UNKNOWN", "reason": last_reason}
        self.started_by_us = False
        return {"success": True, "state": "READY", "reason": "既存の利用者セッションを確認"}

    def stop_session(self, timeout: float) -> dict[str, Any]:
        """共有利用されるWaylandセッションを停止せず、管理対象外として返す。"""
        return {"success": True, "state": "SHARED_OR_UNOWNED", "reason": "共有セッションは停止しない"}
