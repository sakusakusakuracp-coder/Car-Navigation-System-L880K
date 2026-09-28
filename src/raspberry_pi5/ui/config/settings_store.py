"""UI設定の読込と検証。"""

from __future__ import annotations

import json
from pathlib import Path


DEFAULT_CONFIG = {
    "window_width": 1280,
    "window_height": 720,
    "startup_screen": "home",
    "navigation_viewport": {"x": 0, "y": 62, "width": 1280, "height": 580},
    "external_focus_delay_ms": 40,
}


def load_ui_config() -> dict:
    """設定ファイルを読み込み、許可範囲を検証した設定を返す。

    設定ファイルがない開発環境でも画面を確認できるよう既定値を使う。
    車両操作や秘密情報を既定値へ入れない。
    """
    path = Path.home() / ".config" / "l880k-car-navigation" / "ui.json"
    if not path.exists():
        return DEFAULT_CONFIG.copy()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("UI設定はオブジェクトで指定してください")
        width = int(data.get("window_width", DEFAULT_CONFIG["window_width"]))
        height = int(data.get("window_height", DEFAULT_CONFIG["window_height"]))
        screen = data.get("startup_screen", "home")
        if not 800 <= width <= 3840 or not 480 <= height <= 2160:
            raise ValueError("画面サイズが許可範囲外です")
        if screen not in {"home", "navigation", "camera", "audio", "vehicle", "settings"}:
            raise ValueError("起動画面が不正です")
        viewport = data.get("navigation_viewport", DEFAULT_CONFIG["navigation_viewport"])
        if not isinstance(viewport, dict):
            raise ValueError("ナビ表示領域が不正です")
        viewport = {key: int(viewport.get(key, DEFAULT_CONFIG["navigation_viewport"][key])) for key in ("x", "y", "width", "height")}
        if viewport["width"] <= 0 or viewport["height"] <= 0:
            raise ValueError("ナビ表示領域のサイズが不正です")
        focus_delay = int(data.get("external_focus_delay_ms", DEFAULT_CONFIG["external_focus_delay_ms"]))
        if not 0 <= focus_delay <= 1000:
            raise ValueError("外部アプリのフォーカス復帰待ち時間が許可範囲外です")
        result = {
            "window_width": width,
            "window_height": height,
            "startup_screen": screen,
            "navigation_viewport": viewport,
            "external_focus_delay_ms": focus_delay,
        }
        for key in ("audio_sink_name", "mopidy_url", "camera_config_path", "preferences_path", "fan_socket_path", "camera_socket_path", "drive_upload_socket_path", "navigation_socket_path", "livi_socket_path", "obd2_socket_path", "position_socket_path"):
            if key in data:
                if not isinstance(data[key], str) or not data[key]:
                    raise ValueError("接続先設定が不正です")
                result[key] = data[key]
        return result
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return DEFAULT_CONFIG.copy()
