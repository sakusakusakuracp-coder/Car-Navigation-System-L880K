"""Waydroidコンテナ操作を閉じ込めるAdapter。"""

from __future__ import annotations

from typing import Any

from .command_runner import CommandRunner


class ContainerAdapter:
    def __init__(self, config: dict[str, Any], runner: CommandRunner) -> None:
        """Waydroidコンテナの設定と起動所有権を初期化する。"""
        self.config = config
        self.runner = runner
        self.started_by_us = False

    def ensure_container(self, timeout: float) -> dict[str, Any]:
        """Waydroidコンテナを準備し、このプロセスが起動したかを記録する。"""
        command = self.config.get("commands", {}).get("container_start")
        if not command:
            return {"success": False, "state": "UNKNOWN", "reason": "コンテナ操作コマンド未設定"}
        result = self.runner.run(list(command), timeout)
        ok = result.returncode == 0 and not result.timed_out
        self.started_by_us = ok
        return {"success": ok, "state": "RUNNING" if ok else "UNKNOWN", "reason": result.stderr if not ok else ""}

    def stop_container(self, timeout: float) -> dict[str, Any]:
        """自分が起動したコンテナだけを停止し、共有資源は保持する。"""
        if not self.started_by_us:
            return {"success": True, "state": "SHARED_OR_UNOWNED", "reason": "自分で起動していないため停止しない"}
        command = self.config.get("commands", {}).get("container_stop")
        if not command:
            return {"success": False, "state": "UNKNOWN", "reason": "コンテナ停止コマンド未設定"}
        result = self.runner.run(list(command), timeout)
        ok = result.returncode == 0 and not result.timed_out
        self.started_by_us = False if ok else self.started_by_us
        return {"success": ok, "state": "STOPPED" if ok else "UNKNOWN", "reason": result.stderr if not ok else ""}
