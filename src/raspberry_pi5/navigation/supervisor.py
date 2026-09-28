"""Waydroid/OsmAndの要求・状態・起動順序を管理する。"""

from __future__ import annotations

import time
from typing import Any, Callable

from .app_launcher import AppLauncher
from .command_runner import CommandRunner
from .container_adapter import ContainerAdapter
from .environment_probe import EnvironmentProbe
from .recovery_policy import RecoveryPolicy
from .request_registry import RequestRegistry
from .session_adapter import SessionAdapter
from .state import NavigationState, OperationResult, Phase
from .window_adapter import WindowAdapter


class NavigationSupervisor:
    """外部操作の所有範囲を確認しながら、同期的な一段階処理を提供する。"""

    def __init__(self, config: dict[str, Any], status_publisher: Callable[[dict[str, Any]], None] | None = None) -> None:
        """ナビの状態管理と各OS境界Adapterを組み立てる。"""
        self.config = config
        self.state = NavigationState()
        self.registry = RequestRegistry(int(config.get("request_limit", 128)))
        self.recovery = RecoveryPolicy(**config.get("recovery", {}))
        runner = CommandRunner(int(config.get("max_output", 32_768)), config.get("command_log"))
        self.probe = EnvironmentProbe(config)
        self.container = ContainerAdapter(config, runner)
        self.session = SessionAdapter(config, runner)
        self.apps = AppLauncher(config, runner)
        self.window = WindowAdapter(config, runner)
        self.publish = status_publisher or (lambda _status: None)

    def handle_request(self, command_id: str, operation: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """UIからの操作要求を検証し、対応するナビ処理を1回だけ実行する。"""
        arguments = arguments or {}
        if operation not in {"prelaunch", "start", "show", "show_home", "hide", "focus", "focus_home", "stop"}:
            return {"accepted": False, "reason": "未対応の操作です"}
        try:
            record = self.registry.register(command_id, operation, arguments)
        except ValueError as exc:
            return {"accepted": False, "reason": str(exc)}
        if record.status == "COMPLETED" and record.result is not None:
            return {"accepted": True, "duplicate": True, **record.result}
        if operation == "prelaunch":
            self.state.requested_visibility = "hidden"
            result = self.prelaunch_navigation(arguments)
        elif operation == "start":
            result = self.ensure_running(arguments)
        elif operation == "show":
            self.state.requested_visibility = "visible"
            # 再表示時も必ず画面配置と対象アプリ起動を確認する。
            # Waydroidホーム画面だけが残った状態からOsmAndへ復帰させるため、
            # READY状態でもensure_runningを通す。
            result = self.ensure_running(arguments, prepare_window=True)
        elif operation == "show_home":
            self.state.requested_visibility = "visible"
            result = self.show_android_home(arguments)
        elif operation == "hide":
            self.state.requested_visibility = "hidden"
            result = self._visibility(False)
        elif operation == "focus":
            result = self.focus_navigation(arguments)
        elif operation == "focus_home":
            result = self.focus_android_home(arguments)
        else:
            result = self.stop_navigation()
        payload = {"accepted": True, **result}
        self.registry.complete(command_id, payload)
        self.publish(self.build_status())
        return payload

    def ensure_running(self, arguments: dict[str, Any] | None = None, prepare_window: bool = False) -> dict[str, Any]:
        """環境、コンテナ、セッション、対象アプリを順に準備して表示可能にする。"""
        arguments = arguments or {}
        mode = str(arguments.get("mode", self.config.get("default_navigation_mode", "osmand")))
        role = "livi" if mode == "livi" else "navigation"
        self.window.set_target("navigation")
        self.state.app_state["navigation_mode"] = mode
        self.state.operation_epoch += 1
        self.state.phase = Phase.CHECKING
        self.window.set_viewport(arguments.get("viewport"))
        environment = self.probe.check_environment()
        if not environment["ready"]:
            self.state.phase = Phase.BLOCKED
            self.state.failure_reason = "実行環境を確認できません: " + ", ".join(k for k, v in environment["checks"].items() if not v)
            return {"success": False, "state": self.state.phase.value, "reason": self.state.failure_reason}
        resources = self.probe.check_resources()
        self.state.phase = Phase.STARTING_CONTAINER
        container = self.container.ensure_container(self._timeout("container"))
        if not container["success"]:
            return self._fail("コンテナ準備", container["reason"])
        self.state.container_state = container["state"]
        self.state.phase = Phase.STARTING_SESSION
        session = self.session.ensure_session(self._timeout("session"))
        if not session["success"]:
            return self._fail("セッション準備", session["reason"])
        self.state.session_state = session["state"]
        self.state.android_state = "READY"
        self.state.phase = Phase.STARTING_APP
        location = self.state.location_state.get("state")
        navigation = self.apps.ensure_app(role, self._timeout("app"))
        if not navigation["success"]:
            return self._fail(f"{role}準備", navigation["reason"])
        self.state.app_state[role] = navigation["state"]
        if prepare_window:
            visibility = self._visibility(True)
            if not visibility["success"]:
                return self._fail("Waydroid画面準備", visibility["reason"])
        self.state.phase = Phase.READY if location == "VALID" else Phase.READY_WITHOUT_FIX
        self.state.failure_reason = "" if resources["map_confirmed"] else "地図資源の確認は未完了"
        return {"success": True, "state": self.state.phase.value, "reason": self.state.failure_reason}

    def prelaunch_navigation(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """UI起動時にOsmAndを先行起動し、表示だけを隠して待機させる。"""
        arguments = arguments or {}
        result = self.ensure_running(arguments, prepare_window=False)
        if not result["success"]:
            return result
        hidden = self.window.hide_current_sway_window(self._timeout("visibility"))
        self.state.actual_visibility = hidden["state"]
        self.state.requested_visibility = "hidden"
        self.publish(self.build_status())
        return {"success": True, "state": "PRELAUNCHED", "reason": "OsmAndを先行起動して待機させました"}

    def show_android_home(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Waydroidを準備し、Androidアプリを選べるホーム画面を表示する。"""
        arguments = arguments or {}
        self.state.operation_epoch += 1
        self.state.phase = Phase.CHECKING
        self.window.set_target("home")
        self.window.set_viewport(arguments.get("viewport"))
        environment = self.probe.check_environment()
        if not environment["ready"]:
            self.state.phase = Phase.BLOCKED
            self.state.failure_reason = "実行環境を確認できません: " + ", ".join(k for k, v in environment["checks"].items() if not v)
            return {"success": False, "state": self.state.phase.value, "reason": self.state.failure_reason}
        self.state.phase = Phase.STARTING_CONTAINER
        container = self.container.ensure_container(self._timeout("container"))
        if not container["success"]:
            return self._fail("コンテナ準備", container["reason"])
        self.state.container_state = container["state"]
        self.state.phase = Phase.STARTING_SESSION
        session = self.session.ensure_session(self._timeout("session"))
        if not session["success"]:
            return self._fail("セッション準備", session["reason"])
        self.state.session_state = session["state"]
        self.state.android_state = "READY"
        home = self.window.show_home(self._timeout("visibility"))
        if not home["success"]:
            return self._fail("Androidホーム画面準備", home["reason"])
        self.state.actual_visibility = home["state"]
        self.state.app_state["home"] = "RUNNING"
        self.state.phase = Phase.READY
        self.state.failure_reason = ""
        return {"success": True, "state": self.state.phase.value, "reason": "Androidアプリ画面を表示しました"}

    def focus_navigation(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """起動済みWaydroid画面のフォーカスだけを戻し、アプリを再起動しない。"""
        arguments = arguments or {}
        self.window.set_target("navigation")
        self.window.set_viewport(arguments.get("viewport"))
        result = self.window.focus_existing(timeout=self._timeout("visibility"))
        self.state.actual_visibility = result["state"]
        return {"success": result["success"], "state": result["state"], "reason": result["reason"]}

    def focus_android_home(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """起動済みWaydroidホームのフォーカスだけを戻し、再起動しない。"""
        arguments = arguments or {}
        self.window.set_target("home")
        self.window.set_viewport(arguments.get("viewport"))
        result = self.window.focus_existing(timeout=self._timeout("visibility"))
        self.state.actual_visibility = result["state"]
        return {"success": result["success"], "state": result["state"], "reason": result["reason"]}

    def update_location_state(self, update: dict[str, Any]) -> None:
        """位置の値そのものではなく、期限と連携状態を管理する。"""
        state = str(update.get("state", "UNKNOWN"))
        updated = dict(update)
        updated["received_monotonic"] = time.monotonic()
        self.state.location_state = {"state": state, **updated}
        if self.state.phase in {Phase.READY, Phase.READY_WITHOUT_FIX}:
            self.state.phase = Phase.READY if state == "VALID" else Phase.READY_WITHOUT_FIX
        self.publish(self.build_status())

    def build_status(self) -> dict[str, Any]:
        """UIへ通知する現在のナビ管理状態を辞書として作る。"""
        return self.state.as_dict()

    def schedule_recovery(self, reason: str) -> bool:
        """失敗理由を回復ポリシーへ渡し、再試行可能なら回復中へ遷移する。"""
        allowed = self.recovery.schedule(reason)
        if allowed:
            self.state.phase = Phase.RECOVERING
        return allowed

    def stop_navigation(self) -> dict[str, Any]:
        """画面、アプリ、セッション、コンテナを所有権に従って停止確認する。"""
        self.state.operation_epoch += 1
        self.state.requested_visibility = "hidden"
        stages = {
            "visibility": self._visibility(False),
            "osmand": self.apps.stop_app("navigation", self._timeout("stop_app")),
            "livi": self.apps.stop_app("livi", self._timeout("stop_app")),
            "session": self.session.stop_session(self._timeout("stop_session")),
            "container": self.container.stop_container(self._timeout("stop_container")),
        }
        self.state.stop_result = stages
        failures = [name for name, result in stages.items() if not result.get("success", False)]
        unknown = [name for name, result in stages.items() if result.get("state") == "UNKNOWN"]
        shared = [name for name, result in stages.items() if result.get("state") in {"SHARED_OR_UNOWNED", "NOT_OWNED"}]
        if failures:
            result_state = "NOT_CONFIRMED" if unknown else "PARTIALLY_STOPPED"
            reason = "停止状態を確認できない段階: " + ", ".join(failures)
            self.state.phase = Phase.FAILED
            success = False
        else:
            result_state = "STOPPED_WITH_SHARED_RESOURCE" if shared else "STOPPED"
            reason = "停止状態を確認しました"
            self.state.phase = Phase.STOPPED
            success = True
        self.state.actual_visibility = stages["visibility"].get("state", "unknown")
        self.state.last_result = result_state
        return {"success": success, "state": result_state, "reason": reason, "stop_result": stages}

    def _visibility(self, visible: bool) -> dict[str, Any]:
        """表示・非表示の実処理結果を状態へ反映する。"""
        result = self.window.apply_visibility(visible, self._timeout("visibility"))
        self.state.actual_visibility = result["state"]
        return {"success": result["success"], "state": result["state"], "reason": result["reason"]}

    def _fail(self, stage: str, reason: str) -> dict[str, Any]:
        """失敗した処理段階を記録し、共通の失敗応答を作る。"""
        self.state.phase = Phase.FAILED
        self.state.failure_reason = f"{stage}: {reason}"
        return {"success": False, "state": self.state.phase.value, "reason": self.state.failure_reason}

    def _timeout(self, stage: str) -> float:
        """処理段階ごとの設定値を読み、最小実行時間を保証する。"""
        value = self.config.get("timeouts", {}).get(stage, 5.0)
        return max(0.1, float(value))
