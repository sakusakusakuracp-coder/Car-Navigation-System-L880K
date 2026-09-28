"""Sway上のLIVIウィンドウを固定領域へ配置する。"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from navigation.command_runner import CommandRunner


class LiviWindowAdapter:
    """LIVIプロセスに対応するSwayウィンドウだけを操作する。"""

    def __init__(self, config: dict[str, Any], runner: CommandRunner, pid_supplier: Callable[[], int | None]) -> None:
        """Sway設定、コマンド実行器、LIVI PID取得関数を保持する。"""
        self.config = config
        self.runner = runner
        self.pid_supplier = pid_supplier
        self.container_id: int | None = None
        self.actual_visibility = "hidden"
        self._viewport: dict[str, int] = {}

    def set_viewport(self, viewport: dict[str, Any] | None) -> None:
        """UIから受け取った固定表示領域を数値へ変換して保持する。"""
        if isinstance(viewport, dict):
            self._viewport = {
                key: int(viewport[key])
                for key in ("x", "y", "width", "height")
                if key in viewport
            }

    def show(self, timeout: float) -> dict[str, Any]:
        """LIVIウィンドウを検出し、固定領域へ配置して表示する。"""
        if self.config.get("display_backend", "sway") != "sway":
            return {"success": False, "state": "UNKNOWN", "reason": "LIVIの表示先はSwayに設定してください"}
        if not self._viewport:
            return {"success": False, "state": "UNKNOWN", "reason": "LIVI表示領域が未設定です"}
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            node = self._find_target()
            if node is not None:
                # scratchpad showは表示中に呼ぶと隠れるため、退避領域にある場合だけ戻す。
                if node.get("_scratchpad_hidden", False):
                    restored = self.runner.run(
                        ["swaymsg", f"[con_id=\"{node['id']}\"]", "scratchpad", "show"],
                        max(0.1, deadline - time.monotonic()),
                    )
                    if restored.returncode != 0:
                        self.actual_visibility = "hidden"
                        return {"success": False, "state": "UNKNOWN", "reason": restored.stderr or restored.stdout or "LIVIを退避領域から戻せませんでした"}
                result = self._place(int(node["id"]))
                if result["success"]:
                    self.container_id = int(node["id"])
                    self.actual_visibility = "visible"
                return result
            time.sleep(0.2)
        return {"success": False, "state": "UNKNOWN", "reason": "LIVIウィンドウをSwayから確認できませんでした"}

    def hide(self, timeout: float) -> dict[str, Any]:
        """LIVIウィンドウを終了せず、Swayのスクラッチパッドへ退避する。"""
        if self.container_id is None:
            self.actual_visibility = "hidden"
            return {"success": True, "state": "hidden", "reason": "LIVIウィンドウは表示されていません"}
        result = self.runner.run(["swaymsg", f"[con_id=\"{self.container_id}\"]", "move", "scratchpad"], timeout)
        if result.returncode != 0:
            return {"success": False, "state": "UNKNOWN", "reason": result.stderr or result.stdout or "LIVIを退避できませんでした"}
        self.actual_visibility = "hidden"
        return {"success": True, "state": "hidden", "reason": "LIVIを退避しました"}

    def focus(self, timeout: float) -> dict[str, Any]:
        """現在のウィンドウを再検出し、退避中だけ復帰して前面へ戻す。"""
        if self.container_id is not None:
            result = self.runner.run(
                ["swaymsg", f"[con_id=\"{self.container_id}\"]", "focus"],
                timeout,
            )
            if result.returncode == 0:
                self.actual_visibility = "visible"
                return {"success": True, "state": "visible", "reason": "LIVIを前面へ戻しました"}
            self.container_id = None
        # プロセス再生成などで保存IDが無効になった場合だけ再検出・再配置する。
        return self.show(timeout)

    def _find_target(self) -> dict[str, Any] | None:
        """SwayツリーからLIVI PIDまたは明示識別子に一致するウィンドウを探す。"""
        result = self.runner.run(["swaymsg", "-t", "get_tree", "-r"], 2.0)
        if result.returncode != 0:
            return None
        try:
            tree = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return None
        pid = self.pid_supplier()
        identifiers = [str(value).lower() for value in self.config.get("window_identifiers", [])]
        # PID検索を先に全ツリーへ適用し、同名アプリの先頭ノードを誤選択しない。
        return self._find_node(tree, pid, []) or self._find_node(tree, None, identifiers)

    @classmethod
    def _find_node(cls, node: dict[str, Any], pid: int | None, identifiers: list[str], scratchpad_hidden: bool = False) -> dict[str, Any] | None:
        """Swayツリーを走査し、対象が非表示の退避領域にあるかも返す。"""
        if node.get("type") == "workspace":
            scratchpad_hidden = node.get("name") == "__i3_scratch"
        if node.get("type") in {"con", "floating_con"} and node.get("id") is not None:
            if pid is not None and node.get("pid") == pid:
                return {**node, "_scratchpad_hidden": scratchpad_hidden}
            values = [str(node.get("app_id", "")), str(node.get("name", ""))]
            properties = node.get("window_properties") or {}
            values.extend([str(properties.get("class", "")), str(properties.get("instance", ""))])
            if identifiers and any(identifier in value.lower() for identifier in identifiers for value in values):
                return {**node, "_scratchpad_hidden": scratchpad_hidden}
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            found = cls._find_node(child, pid, identifiers, scratchpad_hidden)
            if found is not None:
                return found
        return None

    def _place(self, container_id: int) -> dict[str, Any]:
        """対象ウィンドウを枠なしの固定サイズへ移動して前面化する。"""
        commands = [
            ["swaymsg", f"[con_id=\"{container_id}\"]", "fullscreen", "disable"],
            ["swaymsg", f"[con_id=\"{container_id}\"]", "floating", "enable"],
            ["swaymsg", f"[con_id=\"{container_id}\"]", "border", "none"],
            # Swayは中心を保ってリサイズするため、最終座標はサイズ確定後に指定する。
            ["swaymsg", f"[con_id=\"{container_id}\"]", "resize", "set", str(self._viewport["width"]), "px", str(self._viewport["height"]), "px"],
            ["swaymsg", f"[con_id=\"{container_id}\"]", "move", "position", str(self._viewport["x"]), "px", str(self._viewport["y"]), "px"],
            ["swaymsg", f"[con_id=\"{container_id}\"]", "focus"],
        ]
        for command in commands:
            result = self.runner.run(command, 2.0)
            if result.returncode != 0:
                return {"success": False, "state": "UNKNOWN", "reason": result.stderr or result.stdout or "Swayへの配置に失敗しました"}
        return {"success": True, "state": "visible", "reason": "LIVIを固定ナビ領域へ配置しました"}
