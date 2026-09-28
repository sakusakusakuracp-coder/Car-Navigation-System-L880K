"""QMLへ公開する画面状態。

通信処理や画面切替の判断をこのクラスへ集めず、確定した表示データだけを
保持する。QMLはこのクラスを読み取り、操作はControllerへ通知する。
"""

from __future__ import annotations

from time import monotonic
import math

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot
from ui.state.upload_status import UploadStatus
from ui.state.gps_status import GpsStatus


class AppState(QObject):
    """画面に表示する最小限の状態を保持する。"""

    changed = Signal()

    def __init__(self) -> None:
        """QMLへ公開する画面、通信、車両、GPS、録画状態を初期化する。"""
        super().__init__()
        self._screen = "home"
        self._navigation_mode = "osmand"
        self._service_status = "準備中"
        self._vehicle_speed: float | None = None
        self._stationary_deadline = 0.0
        self._engine_rpm: float | None = None
        self._coolant_temperature: float | None = None
        self._ecu_voltage: float | None = None
        self._intake_temperature: float | None = None
        self._throttle_position: float | None = None
        self._engine_load: float | None = None
        self._fuel_level: float | None = None
        self._obd_status = "未接続"
        self._obd_reason = ""
        self._obd_polling_mode = "minimal"
        self._gps_status = GpsStatus()
        self._gps_expiry_timer = QTimer(self)
        self._gps_expiry_timer.setInterval(100)
        self._gps_expiry_timer.timeout.connect(self._expire_gps_status)
        self._gps_expiry_timer.start()
        self._obd_expiry_deadlines: dict[str, float] = {}
        self._obd_value_attributes = {
            "vehicle_speed": "_vehicle_speed",
            "engine_rpm": "_engine_rpm",
            "coolant_temperature": "_coolant_temperature",
            "ecu_voltage": "_ecu_voltage",
            "intake_temperature": "_intake_temperature",
            "throttle_position": "_throttle_position",
            "engine_load": "_engine_load",
            "fuel_level": "_fuel_level",
        }
        self._obd_expiry_timer = QTimer(self)
        self._obd_expiry_timer.setInterval(100)
        self._obd_expiry_timer.timeout.connect(self._expire_obd_values)
        self._obd_expiry_timer.start()
        self._recording = False
        self._camera_states = {}
        self._camera_deadline = 0
        self._camera_storage = "未接続"
        self._camera_expiry_timer = QTimer(self)
        self._camera_expiry_timer.setInterval(100)
        self._camera_expiry_timer.timeout.connect(self._expire_camera_status)
        self._camera_expiry_timer.start()
        self._warning = ""
        self._navigation_window_visible = False
        self._upload_status = UploadStatus()
        self._upload_expiry_timer = QTimer(self)
        self._upload_expiry_timer.setInterval(500)
        self._upload_expiry_timer.timeout.connect(self._expire_upload_status)
        self._upload_expiry_timer.start()

    @Property(str, notify=changed)
    def screen(self) -> str:
        """現在表示する画面IDを返す。"""
        return self._screen

    @Property(str, notify=changed)
    def serviceStatus(self) -> str:
        """外部サービスの接続状態を返す。"""
        return self._service_status

    @Property(str, notify=changed)
    def serviceConnectionLabel(self) -> str:
        """管理プログラムの接続状態を短い表示名で返す。"""
        if self._service_status == "管理プログラム接続済み":
            return "接続済み"
        if self._service_status in {"管理プログラム未接続", "待機中（管理プログラム未接続）"}:
            return "未接続"
        return "確認中"

    @Property(bool, notify=changed)
    def serviceConnected(self) -> bool:
        """管理プログラムと接続中かどうかを返す。"""
        return self._service_status == "管理プログラム接続済み"

    @Property(str, notify=changed)
    def navigationMode(self) -> str:
        """現在選択しているナビ表示方式を返す。"""
        return self._navigation_mode

    @Property(str, notify=changed)
    def vehicleSpeed(self) -> str:
        """車速を表示用文字列で返す。不明値を0へ変換しない。"""
        return "不明" if self._vehicle_speed is None else f"{self._vehicle_speed:.0f} km/h"

    @Property(bool, notify=changed)
    def stationaryConfirmed(self) -> bool:
        """有効なOBD2車速が厳密に0で、最大5秒の操作許可期限内かを返す。"""
        return monotonic() < self._stationary_deadline

    @Property(bool, notify=changed)
    def obdUnavailable(self) -> bool:
        """有効な車速を取得できず、走行中とは判定できない状態かを返す。"""
        return self._vehicle_speed is None

    @Property(str, notify=changed)
    def obdStatus(self) -> str:
        """OBD2通信状態を画面表示用に返す。"""
        labels = {"POLLING": "取得中", "PROBING": "対応確認中", "DEGRADED": "一部取得不可", "DISCONNECTED": "未接続", "RETRY_WAIT": "再接続待ち", "INITIALIZING": "初期化中", "CONNECTING": "接続中"}
        return labels.get(self._obd_status, self._obd_status)

    @Property(str, notify=changed)
    def obdConnectionLabel(self) -> str:
        """OBD2ソケットの接続状態を短い表示名で返す。"""
        if self._obd_status == "OBD2サービス接続済み":
            return "接続済み"
        if self._obd_status in {"OBD2サービス切断", "OBD2サービス未接続", "DISCONNECTED", "RETRY_WAIT"}:
            return "未接続"
        return self.obdStatus

    @Property(bool, notify=changed)
    def obdConnected(self) -> bool:
        """OBD2サービスと通信可能な状態かどうかを返す。"""
        return self._obd_status in {"OBD2サービス接続済み", "POLLING", "PROBING", "INITIALIZING", "CONNECTING"}

    @Property(str, notify=changed)
    def gpsStatus(self) -> str:
        """GPSと現在地補正の状態を画面表示用に返す。"""
        return self._gps_status.label

    @Property(str, notify=changed)
    def gpsDetail(self) -> str:
        """車両情報画面に、測位状態の理由を表示する。"""
        return self._gps_status.detail

    @Property(bool, notify=changed)
    def gpsConnected(self) -> bool:
        """GPSサービスへ接続できているかどうかを返す。"""
        return self._gps_status.connected

    @Property(bool, notify=changed)
    def gpsSignalAvailable(self) -> bool:
        """有効なGPS測位を受信しているかどうかを返す。"""
        return self._gps_status.connected and self._gps_status.state == "GPS_ACTIVE"

    @Property(bool, notify=changed)
    def gpsCorrectionActive(self) -> bool:
        """GPS喪失後の補正位置を利用しているかどうかを返す。"""
        return self._gps_status.connected and self._gps_status.state == "DR_ACTIVE"

    @Property(str, notify=changed)
    def obdPollingMode(self) -> str:
        """OBD2の現在の取得モードを表示用名称で返す。"""
        return "詳細" if self._obd_polling_mode == "full" else "最小"

    @Property(str, notify=changed)
    def engineRpm(self) -> str:
        """エンジン回転数を表示用文字列へ変換する。"""
        return self._format_value(self._engine_rpm, "{:.0f}")

    @Property(str, notify=changed)
    def coolantTemperature(self) -> str:
        """冷却水温を表示用文字列へ変換する。"""
        return self._format_value(self._coolant_temperature, "{:.1f}")

    @Property(str, notify=changed)
    def ecuVoltage(self) -> str:
        """ECU電圧を表示用文字列へ変換する。"""
        return self._format_value(self._ecu_voltage, "{:.2f}")

    @Property(str, notify=changed)
    def intakeTemperature(self) -> str:
        """吸気温度を表示用文字列へ変換する。"""
        return self._format_value(self._intake_temperature, "{:.1f}")

    @Property(str, notify=changed)
    def throttlePosition(self) -> str:
        """スロットル開度を表示用文字列へ変換する。"""
        return self._format_value(self._throttle_position, "{:.1f}")

    @Property(str, notify=changed)
    def engineLoad(self) -> str:
        """エンジン負荷を表示用文字列へ変換する。"""
        return self._format_value(self._engine_load, "{:.1f}")

    @Property(str, notify=changed)
    def fuelLevel(self) -> str:
        """燃料残量を表示用文字列へ変換する。"""
        return self._format_value(self._fuel_level, "{:.1f}")

    @Property(bool, notify=changed)
    def recording(self) -> bool:
        """録画中かどうかを返す。"""
        return self._recording

    @Property("QVariantMap", notify=changed)
    def cameraStates(self):
        """方向別の入力・録画状態を返す。画像本文は保持しない。"""
        return self._camera_states

    @Property(str, notify=changed)
    def cameraStorage(self):
        """カメラサービスが確認した保存領域の空きと異常を表示する。"""
        return self._camera_storage

    def _expire_camera_status(self):
        """録画サービス停止時に録画中表示や古い接続状態を残さない。"""
        if self._camera_deadline and monotonic() >= self._camera_deadline:
            self._camera_deadline = 0
            self._recording = False
            self._camera_states = {}
            self._camera_storage = "未接続"
            self.changed.emit()

    @Property(str, notify=changed)
    def uploadStatus(self) -> str:
        """クラウド送信状態を期限付きの実データから表示する。"""
        return self._upload_status.label

    @Property(str, notify=changed)
    def uploadPending(self) -> str:
        """期限内のアップロード未完了件数を短い表示文字列で返す。"""
        if self._upload_status.state in {"STALE", "DISCONNECTED"}:
            return ""
        return f"未完了 {self._upload_status.pending}件"

    @Slot()
    def _expire_upload_status(self) -> None:
        """クラウド送信状態の期限を監視し、期限切れなら画面を更新する。"""
        if self._upload_status.expire():
            self.changed.emit()

    @Property(str, notify=changed)
    def warning(self) -> str:
        """現在表示する警告文を返す。"""
        return self._warning

    @Property(bool, notify=changed)
    def navigationWindowVisible(self) -> bool:
        """選択中の外部ナビ画面が表示状態として通知されているかを返す。"""
        return self._navigation_window_visible

    @Slot(str)
    def setScreen(self, screen: str) -> None:
        """Controllerが決めた画面IDを反映する。"""
        if screen in {"home", "navigation", "android_apps", "camera", "audio", "vehicle", "settings", "rear_camera", "recordings", "warnings"}:
            self._screen = screen
            self.changed.emit()

    @Slot(str)
    def setNavigationMode(self, mode: str) -> None:
        """OsmAndまたはLIVIの表示方式を選択する。"""
        if mode in {"osmand", "livi"} and self._navigation_mode != mode:
            self._navigation_mode = mode
            self.changed.emit()

    def apply_update(self, update: dict) -> None:
        """通信イベントを表示状態へ変換する。

        値の意味を確認する処理はControllerが済ませるため、ここでは
        表示データの更新だけを行う。映像本体は保持しない。
        """
        if "service_status" in update:
            self._service_status = str(update["service_status"])
        if "obd2_status" in update:
            self._obd_status = str(update["obd2_status"])
        if "obd_polling_mode" in update:
            self._obd_polling_mode = str(update["obd_polling_mode"])
        self._gps_status.apply(update)
        event = update.get("event")
        self._upload_status.apply(update)
        payload = update.get("payload") if isinstance(update.get("payload"), dict) else None
        if event == "vehicle.status" and payload is not None:
            if "communication_state" in payload:
                self._obd_status = str(payload["communication_state"])
            if payload.get("reason"):
                self._obd_reason = str(payload["reason"])
        elif event == "vehicle.update" and payload is not None:
            self._apply_obd_value(payload)
        elif event == "vehicle.invalidate" and payload is not None:
            self._apply_obd_value({"key": payload.get("key"), "value": None, "validity": payload.get("validity")})
        if "navigation_mode" in update and update["navigation_mode"] in {"osmand", "livi"}:
            self._navigation_mode = update["navigation_mode"]
        if "vehicle_speed_kmh" in update:
            self._vehicle_speed = update["vehicle_speed_kmh"]
            self._stationary_deadline = 0.0
        if self._obd_status in {"DISCONNECTED", "RETRY_WAIT", "CONNECTING", "INITIALIZING",
                                "OBD2サービス切断", "OBD2サービス未接続"}:
            self._clear_obd_values()
        if update.get("camera_disconnected"):
            self._camera_deadline = 0
            self._camera_states = {}
            self._camera_storage = "未接続"
            self._recording = False
        if update.get("event") == "camera.status":
            self._camera_states = update.get("cameras", {})
            self._camera_deadline = monotonic() + min(3000, max(0, update.get("valid_for_ms", 0))) / 1000
            free = update.get("free_bytes")
            storage = update.get("storage_state")
            self._camera_storage = f"空き {free / 1024 ** 3:.1f} GiB" if storage == "OK" and free is not None else {"LOW": "容量不足", "ERROR": "保存先異常"}.get(storage, "確認中")
        if "recording" in update:
            self._recording = bool(update["recording"])
        if "warning" in update:
            self._warning = str(update["warning"])
        if update.get("actual_visibility") == "visible":
            self._navigation_window_visible = True
        elif update.get("actual_visibility") == "hidden":
            self._navigation_window_visible = False
        self.changed.emit()

    def _apply_obd_value(self, payload: dict) -> None:
        """OBD2の内部単位をUI表示用の値へ変換する。"""
        key = payload.get("key")
        value = payload.get("value") if payload.get("validity", "VALID") == "VALID" else None
        attribute = self._obd_value_attributes.get(key)
        if attribute is None:
            return
        valid_for_ms = payload.get("valid_for_ms")
        valid_lifetime = type(valid_for_ms) in (int, float) and math.isfinite(valid_for_ms) and valid_for_ms > 0
        if key == "vehicle_speed":
            self._stationary_deadline = 0.0
            acquired = payload.get("acquired_mono")
            now = monotonic()
            valid_acquisition = type(acquired) in (int, float) and math.isfinite(acquired) and 0 <= acquired <= now
            valid_window = valid_lifetime and valid_acquisition and acquired + valid_for_ms / 1000 > now
            valid_speed = (type(value) in (int, float) and math.isfinite(value) and value >= 0
                    and payload.get("validity") == "VALID" and payload.get("unit") in {"m/s", "km/h"}
                    and valid_window and payload.get("time_quality") == "monotonic")
            if valid_speed and value == 0:
                deadline = acquired + min(valid_for_ms, 5000) / 1000
                if deadline > now:
                    self._stationary_deadline = deadline
            if not valid_speed:
                # 不正な単位・時刻・期限の通知で、直前の車速を残さない。
                value = None
        if value is None:
            setattr(self, attribute, None)
            self._obd_expiry_deadlines.pop(key, None)
            return
        if value is not None and key == "vehicle_speed" and payload.get("unit") == "m/s":
            value = float(value) * 3.6
        setattr(self, attribute, value)
        self._obd_expiry_deadlines.pop(key, None)
        if valid_lifetime:
            self._obd_expiry_deadlines[key] = monotonic() + float(valid_for_ms) / 1000.0

    def _clear_obd_values(self) -> None:
        """通信断や再接続中に、古いOBD2値を操作判断へ使わないよう消去する。"""
        for attribute in self._obd_value_attributes.values():
            setattr(self, attribute, None)
        self._obd_expiry_deadlines.clear()
        self._stationary_deadline = 0.0

    @Slot()
    def _expire_gps_status(self) -> None:
        """サービスが無言になっても測位中・補正中表示を保持しない。"""
        if self._gps_status.expire():
            self.changed.emit()

    @Slot()
    def _expire_obd_values(self) -> None:
        """UI側でもOBD2値のvalid_for_ms期限を監視し、古い値を消去する。"""
        now = monotonic()
        if self._stationary_deadline and now >= self._stationary_deadline:
            self._stationary_deadline = 0.0
            self.changed.emit()
        expired_keys = [key for key, deadline in self._obd_expiry_deadlines.items() if now >= deadline]
        if not expired_keys:
            return
        for key in expired_keys:
            attribute = self._obd_value_attributes.get(key)
            if attribute is not None:
                setattr(self, attribute, None)
            self._obd_expiry_deadlines.pop(key, None)
        self.changed.emit()

    @staticmethod
    def _format_value(value: float | None, pattern: str) -> str:
        """数値を指定書式で表示し、未取得値はダッシュで表す。"""
        return "—" if value is None else pattern.format(float(value))
