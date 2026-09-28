"""外部画面の表示・非表示を閉じ込めるAdapter。"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
import shlex
import json
from pathlib import Path
from typing import Any

from .command_runner import CommandRunner


class WindowAdapter:
    def __init__(self, config: dict[str, Any], runner: CommandRunner) -> None:
        """外部ウィンドウの表示状態、固定表示領域、Sway識別子を初期化する。"""
        self.config = config
        self.runner = runner
        self.actual_visibility = "unknown"
        self._window_process: subprocess.Popen[bytes] | None = None
        self._window_log_handle = None
        self._viewport: dict[str, int] = {}
        self._sway_container_id: int | None = None
        self._target = "navigation"

    def set_target(self, target: str) -> None:
        """配置対象をOsmAndまたはWaydroidホームへ切り替える。"""
        normalized = "home" if target == "home" else "navigation"
        if normalized != self._target:
            self._target = normalized
            self._sway_container_id = None

    def set_viewport(self, viewport: dict[str, Any] | None) -> None:
        """外部画面または埋込み面に渡す固定表示領域を保持する。"""
        if isinstance(viewport, dict):
            self._viewport = {key: int(viewport[key]) for key in ("x", "y", "width", "height") if key in viewport}

    def apply_visibility(self, visible: bool, timeout: float) -> dict[str, Any]:
        """Waydroid画面を表示領域へ出す、または隠して状態を更新する。"""
        key = "show_window" if visible else "hide_window"
        command = self.config.get("commands", {}).get(key)
        if not command:
            return {"success": False, "state": "UNKNOWN", "reason": "画面管理方法未確認"}
        if visible and command != ["true"]:
            if self.config.get("display_backend") == "sway":
                # OsmAndはapp launchで作成済みのアプリウィンドウだけを使う。
                # show-full-uiはAndroid全体の表示面と直前タスクを復元し得るため、
                # OsmAndの再表示処理では実行しない。
                placement = self._place_sway_window(timeout)
                if not placement["success"]:
                    return placement
                self.actual_visibility = "visible"
                return placement
            result = self._start_window(command, timeout)
            if result["success"] and self.config.get("display_backend") == "sway":
                placement = self._place_sway_window(timeout)
                if not placement["success"]:
                    return placement
            return result
        if not visible:
            if self.config.get("display_backend") == "sway":
                self._hide_sway_window(timeout)
            self._stop_window_process()
        result = self.runner.run(list(command), timeout)
        ok = result.returncode == 0 and not result.timed_out
        if ok:
            self.actual_visibility = "visible" if visible else "hidden"
        return {"success": ok, "state": self.actual_visibility, "reason": result.stderr if not ok else ""}

    def show_home(self, timeout: float) -> dict[str, Any]:
        """WaydroidのAndroidホームを起動し、固定表示領域へ配置する。"""
        command = self.config.get("commands", {}).get("show_home_window") or self.config.get("commands", {}).get("show_window")
        if not command:
            return {"success": False, "state": "UNKNOWN", "reason": "Androidホーム画面の表示方法が未設定です"}
        self.set_target("home")
        # show-full-uiはAndroidで最後に表示していたタスクを一度復元することがある。
        # 先にホームを選んでも後続のshow-full-uiでOsmAndへ戻るため、表示面を
        # 作った後にランチャーを起動し、最終的な前面アプリをホームに確定する。
        result = self._start_window(list(command), timeout)
        if not result["success"]:
            return result
        activated = self._activate_android_home(timeout)
        if not activated["success"]:
            # HOMEキー送信に失敗した状態でshow-full-uiの表示面を残すと、
            # 直前のOsmAndがWaydroidホームのように見えてしまうため退避する。
            self.hide_current_sway_window(timeout)
            return activated
        if self.config.get("display_backend") == "sway":
            placement = self._place_sway_window(timeout)
            if not placement["success"]:
                return placement
            self.actual_visibility = "visible"
            return placement
        return result

    def focus_existing(self, timeout: float) -> dict[str, Any]:
        """起動済みのWaydroidウィンドウだけを前面化する。アプリは起動しない。"""
        if self.config.get("display_backend") != "sway":
            return {"success": False, "state": "UNKNOWN", "reason": "既存画面のフォーカス制御はSway方式だけ対応しています"}
        if self._sway_container_id is None:
            return self._place_sway_window(timeout)
        # この操作は「現在表示中の画面」がUIの背面へ回った場合だけに使う。
        # scratchpad showを実行すると、表示中のウィンドウを一度隠す動作に
        # なるため、ステータスバーの一回目のタッチで画面が消えてしまう。
        focused = self.runner.run(
            ["swaymsg", f"[con_id=\"{self._sway_container_id}\"]", "focus"],
            timeout,
        )
        if focused.returncode == 0:
            self.actual_visibility = "visible"
            return {"success": True, "state": "visible", "reason": "Waydroid画面を前面へ戻しました"}
        # 保存IDが無効になった場合だけ、Swayツリーから対象を探し直す。
        self._sway_container_id = None
        return self._place_sway_window(timeout)

    def _activate_android_home(self, timeout: float) -> dict[str, Any]:
        """現在のAndroidアプリに関係なく、ホームアプリを明示的に前面化する。"""
        command = self.config.get("commands", {}).get("activate_home")
        if not command:
            return {"success": True, "state": "READY", "reason": "ホーム明示起動は未設定です"}
        retries = max(1, int(self.config.get("app_launch_retries", 3)))
        retry_interval = max(0.0, float(self.config.get("app_launch_retry_interval", 1.0)))
        result = None
        reason = ""
        for attempt in range(retries):
            result = self.runner.run(list(command), timeout)
            reason = result.stderr.strip() or result.stdout.strip()
            platform_starting = "Failed to get service waydroidplatform" in reason
            if result.returncode == 0 and not result.timed_out and not platform_starting:
                return {"success": True, "state": "READY", "reason": "Androidホームを選択しました"}
            if attempt + 1 < retries:
                time.sleep(retry_interval)
        assert result is not None
        return {
            "success": False,
            "state": "UNKNOWN",
            "reason": reason or "Androidホームアプリを起動できませんでした",
        }

    def _place_sway_window(self, timeout: float) -> dict[str, Any]:
        """Sway上のWaydroidウィンドウを固定ナビ領域へ配置する。"""
        if not self._viewport:
            return {"success": False, "state": "UNKNOWN", "reason": "ナビ表示領域が未設定です"}
        identifiers = self._target_identifiers()
        exact = self._target == "home"
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            tree = self.runner.run(["swaymsg", "-t", "get_tree", "-r"], min(2.0, max(0.1, deadline - time.monotonic())))
            if tree.returncode == 0:
                try:
                    node = self._find_sway_window(json.loads(tree.stdout), identifiers, exact=exact)
                except (json.JSONDecodeError, TypeError):
                    node = None
                if node:
                    container_id = int(node["id"])
                    commands = []
                    if node.get("scratchpad_state") not in {None, "none"}:
                        commands.append(["swaymsg", f"[con_id=\"{container_id}\"]", "scratchpad", "show"])
                    commands.extend([
                        ["swaymsg", f"[con_id=\"{container_id}\"]", "floating", "enable"],
                        ["swaymsg", f"[con_id=\"{container_id}\"]", "move", "position", str(self._viewport["x"]), "px", str(self._viewport["y"]), "px"],
                        ["swaymsg", f"[con_id=\"{container_id}\"]", "resize", "set", str(self._viewport["width"]), "px", str(self._viewport["height"]), "px"],
                        ["swaymsg", f"[con_id=\"{container_id}\"]", "focus"],
                    ])
                    for command in commands:
                        result = self.runner.run(command, 2.0)
                        if result.returncode != 0:
                            return {"success": False, "state": "UNKNOWN", "reason": result.stderr or result.stdout or "Sway配置に失敗しました"}
                    self._sway_container_id = container_id
                    self._write_log(f"sway placement: {self._viewport}")
                    return {"success": True, "state": "visible", "reason": "Waydroidを固定ナビ領域へ配置しました"}
            time.sleep(0.25)
        target_name = "Androidホーム" if self._target == "home" else "OsmAnd"
        return {"success": False, "state": "UNKNOWN", "reason": f"{target_name}ウィンドウをSwayから確認できませんでした"}

    @classmethod
    def _find_sway_window(cls, node: dict[str, Any], identifiers: list[str], *, exact: bool = False) -> dict[str, Any] | None:
        """対象アプリを示す識別子に一致するSwayウィンドウを探す。"""
        values = [str(node.get("app_id", "")), str(node.get("name", ""))]
        properties = node.get("window_properties") or {}
        values.extend([str(properties.get("class", "")), str(properties.get("instance", ""))])
        normalized_values = [value.lower() for value in values if value]
        matched = (
            any(identifier == value for identifier in identifiers for value in normalized_values)
            if exact
            else any(identifier in value for identifier in identifiers for value in normalized_values)
        )
        if matched:
            if node.get("type") in {"con", "floating_con"} and node.get("id") is not None:
                return node
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            found = cls._find_sway_window(child, identifiers, exact=exact)
            if found:
                return found
        return None

    def _target_identifiers(self) -> list[str]:
        """現在の表示対象にだけ一致するSway識別子を返す。"""
        if self._target == "home":
            values = self.config.get("waydroid_home_window_identifiers", ["Waydroid"])
        else:
            values = self.config.get(
                "navigation_window_identifiers",
                self.config.get("waydroid_window_identifiers", ["net.osmand.plus", "OsmAnd"]),
            )
        return [str(value).lower() for value in values]

    def _hide_sway_window(self, timeout: float) -> None:
        """WindowAdapterの_hide_sway_windowの内部処理を実行する。"""
        if self._sway_container_id is not None:
            self.runner.run(["swaymsg", f"[con_id=\"{self._sway_container_id}\"]", "move", "scratchpad"], timeout)

    def hide_current_sway_window(self, timeout: float) -> dict[str, Any]:
        """現在のWaydroidウィンドウを検出し、表示せずに待機させる。"""
        if self.config.get("display_backend") != "sway":
            self.actual_visibility = "hidden"
            return {"success": True, "state": "hidden", "reason": "Sway以外のため外部ウィンドウ操作を省略しました"}
        identifiers = self._target_identifiers()
        exact = self._target == "home"
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            tree = self.runner.run(["swaymsg", "-t", "get_tree", "-r"], min(2.0, max(0.1, deadline - time.monotonic())))
            if tree.returncode == 0:
                try:
                    node = self._find_sway_window(json.loads(tree.stdout), identifiers, exact=exact)
                except (json.JSONDecodeError, TypeError):
                    node = None
                if node:
                    container_id = int(node["id"])
                    result = self.runner.run(["swaymsg", f"[con_id=\"{container_id}\"]", "move", "scratchpad"], 2.0)
                    if result.returncode != 0:
                        return {"success": False, "state": "UNKNOWN", "reason": result.stderr or result.stdout or "Waydroid画面を退避できませんでした"}
                    self._sway_container_id = container_id
                    self.actual_visibility = "hidden"
                    return {"success": True, "state": "hidden", "reason": "先行起動したWaydroid画面を退避しました"}
            time.sleep(0.25)
        self.actual_visibility = "hidden"
        return {"success": True, "state": "hidden", "reason": "Waydroid画面は表示されていない状態で待機します"}

    def _start_window(self, command: list[str], timeout: float) -> dict[str, Any]:
        """画面を保持するコマンドをUI要求の処理から切り離して起動する。"""
        self._stop_window_process()
        try:
            log_path = self.config.get("command_log")
            self._window_log_handle = Path(log_path).open("a", encoding="utf-8") if log_path else None
            self._write_log(f"window command: {shlex.join(command)} viewport: {self._viewport}")
            process = subprocess.Popen(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=self._window_log_handle or subprocess.DEVNULL,
                stderr=self._window_log_handle or subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            self._close_window_log()
            return {"success": False, "state": "UNKNOWN", "reason": str(exc)}
        ready_delay = max(0.05, float(self.config.get("window_ready_delay", 0.3)))
        time.sleep(min(ready_delay, max(0.05, timeout)))
        return_code = process.poll()
        self._write_log(f"window process returncode: {return_code}")
        if return_code is not None and return_code != 0:
            self._close_window_log()
            return {"success": False, "state": "UNKNOWN", "reason": f"画面表示コマンドが終了しました: {return_code}"}
        self._window_process = process if return_code is None else None
        if return_code is not None:
            self._close_window_log()
        self.actual_visibility = "visible"
        return {"success": True, "state": self.actual_visibility, "reason": ""}

    def _stop_window_process(self) -> None:
        """WindowAdapterの_stop_window_processの内部処理を実行する。"""
        if self._window_process is None or self._window_process.poll() is not None:
            self._window_process = None
            return
        self._window_process.terminate()
        try:
            self._window_process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            self._window_process.kill()
        finally:
            self._window_process = None
            self._close_window_log()

    def _close_window_log(self) -> None:
        """WindowAdapterの_close_window_logの内部処理を実行する。"""
        if self._window_log_handle is not None:
            self._window_log_handle.close()
            self._window_log_handle = None

    def _write_log(self, message: str) -> None:
        """WindowAdapterの_write_logの内部処理を実行する。"""
        log_path = self.config.get("command_log")
        if not log_path:
            return
        try:
            timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            path = Path(log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(f"[{timestamp}] {message}\n")
        except OSError:
            pass
