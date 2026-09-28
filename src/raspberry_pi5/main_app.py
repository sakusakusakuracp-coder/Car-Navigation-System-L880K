"""カーナビUIの起動・終了を管理するエントリポイント。

このファイルは画面の細かな判断を持たず、設定、状態、通信、画面切替の
各モジュールを組み立ててQMLへ渡す役割だけを担当する。
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from ui.config.settings_store import load_ui_config
from ui.controllers.screen_controller import ScreenController
from ui.controllers.service_bridge import ServiceBridge
from ui.state.app_state import AppState
from ui.frame_presenter import FramePresenter
from ui.controllers.feature_controller import FeatureController


def build_dependencies(config: dict) -> tuple[AppState, ServiceBridge, ScreenController]:
    """画面が依存するモジュールを生成する。

    生成順を固定することで、画面切替処理が状態管理や通信処理より先に
    動き始める状態を防ぐ。ここでは外部サービスへの接続完了までは待たない。
    """
    state = AppState()
    bridge = ServiceBridge(config)
    controller = ScreenController(state, bridge, config)
    return state, bridge, controller


def load_view(engine: QQmlApplicationEngine, state: AppState, controller: ScreenController, qml_path: Path) -> None:
    """QMLへ表示用状態と操作窓口を公開して画面を読み込む。"""
    engine.rootContext().setContextProperty("appState", state)
    engine.rootContext().setContextProperty("screenController", controller)
    # SwayではUIを通常ウィンドウとして配置し、その上にWaydroidを重ねる。
    # Raspberry Pi実機などの通常起動では従来どおりフルスクリーンを使用する。
    embedded_window = os.environ.get("L880K_NAV_EXTERNAL_WINDOW", "").lower() in {"1", "true", "yes"}
    engine.rootContext().setContextProperty("embeddedWindow", embedded_window)
    engine.load(str(qml_path))
    if not engine.rootObjects():
        raise RuntimeError("QML画面を作成できませんでした")


def begin_shutdown(bridge: ServiceBridge) -> None:
    """終了時に新規通信を止め、使用中のリソースを解放する。"""
    bridge.close()


def main() -> int:
    """アプリケーションを初期化し、Qtのイベントループを開始する。"""
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    app = QGuiApplication(sys.argv)
    app.setDesktopFileName("l880k-car-navigation")
    config = load_ui_config()
    state, bridge, controller = build_dependencies(config)
    features = FeatureController(state, bridge, config)
    if features.preferences.path.exists():
        config["startup_screen"] = features.saved["startup_screen"]
    engine = QQmlApplicationEngine()
    camera_view = FramePresenter(bridge)
    engine.addImageProvider("camera", camera_view.provider)
    engine.rootContext().setContextProperty("cameraPresenter", camera_view)
    engine.rootContext().setContextProperty("uiFeatures", features)
    qml_path = Path(__file__).parent / "ui" / "qml" / "Main.qml"
    try:
        load_view(engine, state, controller, qml_path)
        controller.start()
        features.start()
        exit_code = app.exec()
    finally:
        begin_shutdown(bridge)
        features.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
