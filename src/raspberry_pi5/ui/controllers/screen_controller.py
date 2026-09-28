"""画面切替と操作判断を担当するController。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from ui.state.app_state import AppState
from ui.controllers.service_bridge import ServiceBridge
from ui.controllers.event_ordering import OrderedEventGate
from ui.controllers.error_messages import command_failure


class ScreenController(QObject):
    """QMLからの意図を検証し、状態と外部サービスへ振り分ける。"""

    screen_changed = Signal()
    transition_changed = Signal()

    def __init__(self, state: AppState, bridge: ServiceBridge, config: dict) -> None:
        """QML状態、外部サービス橋渡し、画面切替制御を結び付ける。"""
        super().__init__()
        self._state = state
        self._bridge = bridge
        self._config = config
        self._navigation_viewport = dict(config.get("navigation_viewport", {}))
        self._previous_screen = "home"
        self._transition_state = "IDLE"
        self._pending_screen: str | None = None
        self._reverse_active = False
        self._external_focus_timer = QTimer(self)
        self._external_focus_timer.setSingleShot(True)
        self._external_focus_timer.setInterval(max(0, int(config.get("external_focus_delay_ms", 40))))
        self._external_focus_timer.timeout.connect(self._restore_external_app_focus_now)
        self._event_gate = OrderedEventGate()
        self._allowed_screens = {"home", "navigation", "android_apps", "camera", "audio", "vehicle", "settings", "rear_camera", "recordings", "warnings"}
        self._screen_priority = {"home": 10, "navigation": 20, "android_apps": 20, "camera": 30, "audio": 20, "vehicle": 20, "settings": 20, "rear_camera": 100}
        bridge.service_event.connect(self.on_service_event)
        bridge.command_result.connect(self.on_command_result)

    @Property(str, notify=screen_changed)
    def currentScreen(self) -> str:
        """QMLが現在画面を確認するための読み取り専用値。"""
        return self._state.screen

    @Property(str, notify=transition_changed)
    def transitionState(self) -> str:
        """画面切替の進行状態を返す。"""
        return self._transition_state

    @Property(bool, notify=transition_changed)
    def reverseActive(self) -> bool:
        """バックギア割込み中かどうかを返す。"""
        return self._reverse_active

    def start(self) -> None:
        """状態購読を開始し、初期画面をホームへ設定する。"""
        self._state.setScreen("home")
        self._transition_state = "IDLE"
        self._pending_screen = None
        self.transition_changed.emit()
        self._bridge.subscribe_state()
        self._set_obd_polling_mode("minimal")
        # UI起動時にOsmAndを先行起動し、ナビ選択時は表示だけを切り替える。
        self._bridge.send_command("02 Waydroidナビ管理", "prelaunch_navigation", self._navigation_arguments())
        startup = self._config.get("startup_screen", "home")
        if startup != "home":
            self.request_screen(startup)

    @Slot(str)
    def request_screen(self, screen: str) -> None:
        """画面切替要求を受け付ける。

        画面IDを許可リストで確認し、QMLから外部サービスへ直接操作が
        流れないようにする。
        """
        if screen not in self._allowed_screens:
            self._state.apply_update({"warning": "利用できない画面です"})
            return
        if self._reverse_active and screen != "rear_camera":
            self._state.apply_update({"warning": "バックカメラ表示中は画面を切り替えられません"})
            return
        # 同じ画面への再押下は、画面切替ではなく表示状態の維持として扱う。
        # 特にナビ画面で再度showを送ると、Swayのscratchpad showがトグル
        # 動作になり、表示中のOsmAndを隠して黒いQML背景だけが見えることがある。
        # 切替アニメーション中でもこの分岐を優先し、再表示処理へ流さない。
        if screen == self._state.screen:
            if screen == "navigation":
                # 画面IDが同じでも、OsmAndまたはLIVIの外部ウィンドウがUIの背面へ
                # 回っている場合があるため、表示状態だけを再確認する。
                self.restore_external_app_focus()
            elif screen == "android_apps":
                self.restore_external_app_focus()
            return
        if self._transition_state == "TRANSITIONING":
            if self._screen_priority.get(screen, 0) >= self._screen_priority.get(self._state.screen, 0):
                self._pending_screen = screen
            return
        previous_screen = self._state.screen
        previous_navigation_mode = self._state.navigationMode
        self._previous_screen = previous_screen
        self._transition_state = "TRANSITIONING"
        self.transition_changed.emit()
        if previous_screen == "navigation" and screen != "navigation":
            self._bridge.send_command(
                self._navigation_service(previous_navigation_mode),
                self._navigation_hide_action(previous_navigation_mode),
            )
        elif previous_screen == "android_apps" and screen != "android_apps":
            self._bridge.send_command("02 Waydroidナビ管理", "hide_waydroid_home")
        self._state.setScreen(screen)
        self.screen_changed.emit()
        self._set_obd_polling_mode("full" if screen == "vehicle" else "minimal")
        if screen == "navigation":
            self._bridge.send_command(self._navigation_service(), self._navigation_show_action(), self._navigation_arguments())
        elif screen == "android_apps":
            self._bridge.send_command("02 Waydroidナビ管理", "show_waydroid_home", self._waydroid_home_arguments())
        QTimer.singleShot(int(self._config.get("screen_transition_ms", 220)), self._finish_transition)

    @Slot()
    def open_osmand(self) -> None:
        """ホームのナビゲーションボタンからOsmAndを選択して表示する。"""
        self.set_navigation_mode("osmand")
        self.request_screen("navigation")

    @Slot()
    def open_livi(self) -> None:
        """ホームの直通ボタンからLIVIを選択し、外部画面を表示する。"""
        self.set_navigation_mode("livi")
        self.request_screen("navigation")

    @Slot()
    def open_waydroid_home(self) -> None:
        """Androidアプリを自由に選べるWaydroidホーム画面を表示する。"""
        self.request_screen("android_apps")

    @Slot(str)
    def handle_action(self, action: str) -> None:
        """ボタン操作を機能ごとの外部要求へ変換する。"""
        actions = {
            "play": ("12 オーディオ連携", "play"),
            "pause": ("12 オーディオ連携", "pause"),
        }
        if action == "home":
            self.request_screen("home")
        elif action == "navigation":
            self._bridge.send_command(self._navigation_service(), self._navigation_show_action(), self._navigation_arguments())
        elif action in actions:
            service, command = actions[action]
            payload = self._navigation_arguments() if action == "navigation" else None
            self._bridge.send_command(service, command, payload)
        else:
            self._state.apply_update({"warning": f"未対応の操作です: {action}"})

    @Slot(str)
    def set_navigation_mode(self, mode: str) -> None:
        """OsmAndまたはLIVIを選択し、表示中なら管理プログラムへ反映する。"""
        if mode not in {"osmand", "livi"}:
            self._state.apply_update({"warning": "利用できないナビモードです"})
            return
        previous_mode = self._state.navigationMode
        if previous_mode == mode:
            return
        self._state.setNavigationMode(mode)
        if self._state.screen == "navigation":
            self._bridge.send_command(self._navigation_service(previous_mode), self._navigation_hide_action(previous_mode))
            self._bridge.send_command(self._navigation_service(mode), self._navigation_show_action(mode), self._navigation_arguments())

    @Slot()
    def maintain_navigation_focus(self) -> None:
        """ナビ表示中にUIが前面へ出た場合、選択中の外部ナビへフォーカスを戻す。"""
        if self._state.screen == "navigation" and not self._reverse_active:
            self._bridge.send_command(self._navigation_service(), self._navigation_focus_action(), self._navigation_arguments())

    @Slot()
    def maintain_waydroid_home_focus(self) -> None:
        """WaydroidホームがUIの背面へ回った場合に、既存画面だけを前面へ戻す。"""
        if self._state.screen == "android_apps" and not self._reverse_active:
            self._bridge.send_command(
                "02 Waydroidナビ管理",
                "maintain_waydroid_home_focus",
                self._waydroid_home_arguments(),
            )

    @Slot()
    def restore_external_app_focus(self) -> None:
        """タッチ完了後に、表示中の外部アプリを改めて最前面へ戻す。

        Swayでは自作UIのバーを押した時点でUI全体が前面へ移動する。
        その処理が終わる前に外部アプリをfocusすると、直後にUIへ隠される
        ことがあるため、単発タイマーを再始動して処理順を安定させる。
        """
        if self._reverse_active or self._state.screen not in {"navigation", "android_apps"}:
            return
        self._external_focus_timer.start()

    def _restore_external_app_focus_now(self) -> None:
        """現在の画面種別に対応する外部ウィンドウへフォーカスを戻す。"""
        if self._reverse_active:
            return
        if self._state.screen == "navigation":
            self.maintain_navigation_focus()
        elif self._state.screen == "android_apps":
            self.maintain_waydroid_home_focus()

    def _navigation_arguments(self) -> dict:
        """ナビ表示方式と固定表示領域を管理ソフトへ渡す。"""
        return {"mode": self._state.navigationMode, "viewport": dict(self._navigation_viewport)}

    def _waydroid_home_arguments(self) -> dict:
        """Androidアプリ一覧の表示対象と固定表示領域を管理ソフトへ渡す。"""
        return {"mode": "home", "viewport": dict(self._navigation_viewport)}

    def _navigation_service(self, mode: str | None = None) -> str:
        """選択中のナビ方式に対応する常駐サービス名を返す。"""
        return "11 LIVI連携" if (mode or self._state.navigationMode) == "livi" else "02 Waydroidナビ管理"

    def _navigation_show_action(self, mode: str | None = None) -> str:
        """選択中のナビ方式に対応する表示操作名を返す。"""
        return "show_livi" if (mode or self._state.navigationMode) == "livi" else "show_navigation"

    def _navigation_hide_action(self, mode: str | None = None) -> str:
        """選択中のナビ方式に対応する非表示操作名を返す。"""
        return "hide_livi" if (mode or self._state.navigationMode) == "livi" else "hide_navigation"

    def _navigation_focus_action(self, mode: str | None = None) -> str:
        """選択中のナビ方式に対応するフォーカス操作名を返す。"""
        return "maintain_livi_focus" if (mode or self._state.navigationMode) == "livi" else "maintain_navigation_focus"

    def _set_obd_polling_mode(self, mode: str) -> None:
        """表示画面に応じてOBD2の取得項目数を切り替える。"""
        self._state.apply_update({"obd_polling_mode": mode})
        self._bridge.send_command("09 OBD2車両情報取得", "set_polling_mode", {"mode": mode})

    @Slot(dict)
    def on_service_event(self, event: dict) -> None:
        """外部サービスの通知を検証して表示状態へ反映する。"""
        if not self._event_gate.accept(event):
            return
        reverse = event.get("reverse", event.get("reverse_on"))
        if isinstance(reverse, bool):
            self._handle_reverse(reverse, bool(event.get("reverse_valid", True)))
        self._state.apply_update(event)

    @Slot(dict)
    def on_command_result(self, result: dict) -> None:
        """操作結果を画面へ反映し、失敗理由を隠さない。"""
        if not result.get("success", False):
            self._state.apply_update({"warning": command_failure(result)})
            return
        # 完了通知が遅延・欠落した場合でも、UIを実画面の状態へ戻す。
        # QUEUED/RUNNINGは受付通知なので、表示状態を確定させない。
        if result.get("state") in {"QUEUED", "RUNNING"}:
            return
        action = result.get("action")
        visible_target = (
            (action == "show_waydroid_home" and self._state.screen == "android_apps")
            or (action == "show_livi" and self._state.screen == "navigation" and self._state.navigationMode == "livi")
            or (action == "show_navigation" and self._state.screen == "navigation" and self._state.navigationMode == "osmand")
        )
        if visible_target:
            self._state.apply_update({"actual_visibility": "visible"})
        elif action in {"hide_navigation", "hide_livi", "hide_waydroid_home"} and self._state.screen not in {"navigation", "android_apps"}:
            # 外部画面同士を切り替えた後に古いhide結果が届いても、
            # 新しい画面の表示状態をhiddenへ戻さない。
            self._state.apply_update({"actual_visibility": "hidden"})

    def _finish_transition(self) -> None:
        """画面切替アニメーションの完了後に保留要求を処理する。"""
        self._transition_state = "IDLE"
        pending = self._pending_screen
        self._pending_screen = None
        self.transition_changed.emit()
        if pending and pending != self._state.screen:
            self.request_screen(pending)

    def _handle_reverse(self, active: bool, valid: bool) -> None:
        """有効なリバース信号で後方画面を割り込み表示する。"""
        if not valid:
            self._state.apply_update({"warning": "リバース信号が不明なため、後方画面を自動表示しません"})
            return
        if active and not self._reverse_active:
            self._reverse_active = True
            self._previous_screen = self._state.screen
            self._transition_state = "IDLE"
            self._pending_screen = None
            self.transition_changed.emit()
            self.request_screen("rear_camera")
            self._bridge.subscribe_video("rear")
        elif not active and self._reverse_active:
            self._reverse_active = False
            self.transition_changed.emit()
            restore = self._previous_screen if self._previous_screen in self._allowed_screens else "home"
            self._transition_state = "IDLE"
            self._pending_screen = None
            self.request_screen(restore)
