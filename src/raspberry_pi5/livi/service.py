"""LIVIのライフサイクルとSway配置をまとめたサービス本体。"""

from __future__ import annotations

import time
import threading
import uuid
from typing import Any, Callable

from navigation.command_runner import CommandRunner

from .process_adapter import NativeProcessAdapter
from .window_adapter import LiviWindowAdapter


class LiviIntegration:
    """LIVIをWaydroidから切り離して管理する同期サービス。"""

    def __init__(self, config: dict[str, Any], publish: Callable[[dict[str, Any]], None] | None = None) -> None:
        """LIVIプロセス、Swayウィンドウ、状態通知の担当を組み立てる。"""
        self.config = config
        self.publish = publish or (lambda _event: None)
        self.boot_id = str(uuid.uuid4())
        self.sequence = 0
        runner = CommandRunner(int(config.get("max_output", 32_768)), config.get("command_log"))
        self.process = NativeProcessAdapter(config)
        self.window = LiviWindowAdapter(config, runner, lambda: self.process.pid)
        self.requested_visibility = "hidden"
        self.failure_reason = ""
        self.phone_connection = "UNKNOWN"
        self._last_publish = 0.0
        self._request_lock = threading.RLock()

    def handle_request(self, command_id: str, operation: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """JSON Lines要求を検証し、LIVIの操作結果を返す。"""
        # 再接続したUIからの要求も直列化し、復帰操作の重複で再び隠れることを防ぐ。
        with self._request_lock:
            return self._handle_request(command_id, operation, arguments)

    def _handle_request(self, command_id: str, operation: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        """同時に届いた要求を一件ずつ処理し、表示状態と結果を通知する。"""
        arguments = arguments or {}
        operations = {"start", "show", "hide", "focus", "stop", "status"}
        if operation not in operations:
            return self._result(command_id, False, "未対応のLIVI操作です")
        if operation in {"start", "show", "focus"}:
            self.window.set_viewport(arguments.get("viewport"))
        if operation == "start":
            result = self._start()
        elif operation == "show":
            self.requested_visibility = "visible"
            result = self._show(arguments)
        elif operation == "hide":
            self.requested_visibility = "hidden"
            result = self.window.hide(self._timeout("visibility"))
        elif operation == "focus":
            self.requested_visibility = "visible"
            result = self.window.focus(self._timeout("visibility"))
        elif operation == "stop":
            result = self._stop()
        else:
            result = {"success": True, "state": "STATUS", "reason": "現在状態を返しました", **self.status()}
        self.failure_reason = "" if result.get("success") else str(result.get("reason", ""))
        self._publish()
        return self._result(command_id, bool(result.get("success")), str(result.get("reason", "")), result)

    def status(self) -> dict[str, Any]:
        """電話接続状態を推測せず、LIVIプロセスとウィンドウの状態を返す。"""
        process_state = "RUNNING" if self.process.is_running() else "STOPPED"
        return {
            "service": "11 LIVI連携",
            "service_name": "livi_integration_service",
            "schema_version": "1.0",
            "boot_id": self.boot_id,
            "sequence": self.sequence,
            "process_state": process_state,
            "window_state": self.window.actual_visibility,
            "actual_visibility": self.window.actual_visibility,
            "requested_visibility": self.requested_visibility,
            "phone_connection": self.phone_connection,
            "failure_reason": self.failure_reason,
            "pid": self.process.pid,
        }

    def close(self) -> None:
        """終了時にLIVIプロセスを所有範囲内で停止する。"""
        with self._request_lock:
            self._stop()

    def _start(self) -> dict[str, Any]:
        """ネイティブLIVIを起動し、電話未接続でも待機状態を許可する。"""
        result = self.process.start(self._timeout("startup"))
        if result["success"]:
            result["state"] = "WAITING_FOR_PHONE"
            result["reason"] = "LIVIを起動しました。電話接続を待機しています"
        return result

    def _show(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """LIVIを起動後、Sway上の固定表示領域へ配置する。"""
        started = self._start()
        if not started["success"]:
            return started
        return self.window.show(self._timeout("window"))

    def _stop(self) -> dict[str, Any]:
        """LIVIを停止し、ウィンドウ表示も非表示へ戻す。"""
        hidden = self.window.hide(self._timeout("visibility"))
        stopped = self.process.stop(self._timeout("stop"))
        if not hidden["success"] or not stopped["success"]:
            return {"success": False, "state": "NOT_CONFIRMED", "reason": hidden.get("reason") or stopped.get("reason")}
        self.requested_visibility = "hidden"
        return {"success": True, "state": "STOPPED", "reason": "LIVIを停止しました"}

    def _publish(self) -> None:
        """状態通知にboot_idと連番を付け、古い通知を判別できるようにする。"""
        self.sequence += 1
        status = self.status()
        status["sequence"] = self.sequence
        self.publish({"event": "status", **status})
        self._last_publish = time.monotonic()

    def _result(self, command_id: str, success: bool, reason: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
        """UIへ返す結果にサービス名と要求IDを付加する。"""
        payload = result or {}
        return {"command_id": command_id, "service": "11 LIVI連携", "success": success, "reason": reason, **payload}

    def _timeout(self, stage: str) -> float:
        """LIVI処理段階のタイムアウトを読み、短すぎる値を補正する。"""
        value = self.config.get("timeouts", {}).get(stage, 5.0)
        return max(0.1, float(value))
