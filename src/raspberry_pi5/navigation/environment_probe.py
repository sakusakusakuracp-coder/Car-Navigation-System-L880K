"""Waydroid実行環境とオフライン資源を読み取り確認する。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


class EnvironmentProbe:
    _BINDER_DEVICE_CANDIDATES = (
        "/dev/binder",
        "/dev/binderfs/binder",
        "/dev/anbox-binder",
    )

    def __init__(self, config: dict[str, Any]) -> None:
        """設定と内部状態を初期化する。"""
        self.config = config

    def check_environment(self) -> dict[str, Any]:
        """EnvironmentProbeのcheck_environmentの内部処理を実行する。"""
        binder_device = next((path for path in self._BINDER_DEVICE_CANDIDATES if Path(path).exists()), "")
        checks = {
            "waydroid_command": shutil.which("waydroid") is not None,
            "wayland_display": bool(os.environ.get("WAYLAND_DISPLAY")),
            "runtime_dir": bool(os.environ.get("XDG_RUNTIME_DIR")),
            "binder_device": bool(binder_device),
        }
        return {"ready": all(checks.values()), "checks": checks, "binder_device_path": binder_device}

    def check_resources(self) -> dict[str, Any]:
        """EnvironmentProbeのcheck_resourcesの内部処理を実行する。"""
        records = self.config.get("resource_record", {})
        map_path = records.get("map_path")
        voice_path = records.get("voice_path")
        return {
            "map_confirmed": bool(map_path and Path(map_path).exists()),
            "voice_confirmed": bool(voice_path and Path(voice_path).exists()),
            "map_path": map_path or "",
            "voice_path": voice_path or "",
        }
