"""登録済みAndroidアプリの起動・終了を閉じ込めるAdapter。"""

from __future__ import annotations

import time
from typing import Any

from .command_runner import CommandRunner


class AppLauncher:
    def __init__(self, config: dict[str, Any], runner: CommandRunner) -> None:
        """起動設定と外部コマンド実行器を保持する。"""
        self.config = config
        self.runner = runner
        self.running: dict[str, bool] = {}

    def ensure_app(self, role: str, timeout: float) -> dict[str, Any]:
        """指定された役割のAndroidアプリを起動し、起動結果を返す。"""
        command = self.config.get("commands", {}).get(role)
        package = self.config.get("packages", {}).get(role)
        if not command or not package:
            return {"success": False, "state": "UNKNOWN", "reason": f"{role}の登録設定未確認"}
        retries = max(1, int(self.config.get("app_launch_retries", 3)))
        retry_interval = max(0.0, float(self.config.get("app_launch_retry_interval", 1.0)))
        result = None
        transient_reason = ""
        for attempt in range(retries):
            result = self.runner.run(list(command), timeout)
            transient_reason = result.stderr.strip() or result.stdout.strip()
            platform_starting = "Failed to get service waydroidplatform" in transient_reason
            if result.returncode == 0 and not result.timed_out and not platform_starting:
                break
            if attempt + 1 < retries:
                time.sleep(retry_interval)
        assert result is not None
        ok = result.returncode == 0 and not result.timed_out and not ("Failed to get service waydroidplatform" in transient_reason)
        self.running[role] = ok
        reason = "" if ok else transient_reason or "アプリ起動を確認できませんでした"
        return {"success": ok, "state": "RUNNING" if ok else "UNKNOWN", "reason": reason, "package": package}

    def stop_app(self, role: str, timeout: float) -> dict[str, Any]:
        """この管理プログラムが起動したAndroidアプリだけを停止する。"""
        command = self.config.get("commands", {}).get(f"stop_{role}")
        if not self.running.get(role):
            return {"success": True, "state": "NOT_OWNED", "reason": "管理対象として起動していない"}
        if not command:
            return {"success": False, "state": "UNKNOWN", "reason": f"{role}の停止方法未確認"}
        result = self.runner.run(list(command), timeout)
        ok = result.returncode == 0 and not result.timed_out
        if ok:
            self.running[role] = False
        return {"success": ok, "state": "STOPPED" if ok else "UNKNOWN", "reason": result.stderr if not ok else ""}
