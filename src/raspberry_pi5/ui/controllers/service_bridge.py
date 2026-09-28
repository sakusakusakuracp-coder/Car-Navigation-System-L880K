"""外部ソフトとの通信境界。Pi 5ではJSON Lines over Unix socketを使う。"""

from __future__ import annotations

import json
import os
import queue
import socket
import threading
import uuid

from PySide6.QtCore import QObject, QTimer, Signal
from telemetry_ipc import runtime_socket_path
from camera.client import CameraClient


class ServiceBridge(QObject):
    """状態通知と操作要求を外部サービスへ中継する。"""

    service_event = Signal(dict)
    command_result = Signal(dict)
    camera_frame = Signal(dict)

    def __init__(self, config: dict) -> None:
        """各常駐サービスのソケット、送信キュー、受信ワーカーを初期化する。"""
        super().__init__()
        self._config = config
        self._closed = False
        self._socket_path = str(config.get("navigation_socket_path", os.environ.get("L880K_NAV_SOCKET", _default_socket_path())))
        self._livi_socket_path = str(config.get("livi_socket_path", os.environ.get("L880K_LIVI_SOCKET", _default_livi_socket_path())))
        self._obd_socket_path = str(config.get("obd2_socket_path", os.environ.get("L880K_OBD_SOCKET", _default_obd_socket_path())))
        self._position_socket_path = str(config.get("position_socket_path", os.environ.get("L880K_POSITION_SOCKET", _default_position_socket_path())))
        self._upload_socket_path = str(config.get("drive_upload_socket_path", os.environ.get("L880K_DRIVE_SOCKET", runtime_socket_path("l880k-drive-upload.sock"))))
        self._outgoing: queue.Queue[dict] = queue.Queue()
        self._livi_outgoing: queue.Queue[dict] = queue.Queue()
        self._obd_outgoing: queue.Queue[dict] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._livi_worker: threading.Thread | None = None
        self._obd_worker: threading.Thread | None = None
        self._position_worker: threading.Thread | None = None
        self._upload_worker: threading.Thread | None = None
        self._upload_stop = threading.Event()
        self._fan_worker = None
        self.audio_controller = None
        self._camera_client = CameraClient(str(config.get("camera_socket_path", os.environ.get("L880K_CAMERA_SOCKET", runtime_socket_path("l880k-camera.sock")))), self.service_event.emit)
        self._camera_timer = QTimer(self)
        self._camera_timer.setInterval(50)
        self._camera_timer.timeout.connect(self._drain_camera_frame)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._emit_stub_state)

    def subscribe_state(self) -> None:
        """車両・録画などの状態購読を開始する。"""
        self._timer.start()
        self._camera_client.start()
        self._camera_timer.start()
        self._start_worker()
        self._start_obd_worker()
        self._start_position_worker()
        if self._fan_worker is None:
            self._fan_worker = threading.Thread(target=self._run_fan_socket, name="fan-ui-ipc", daemon=True)
            self._fan_worker.start()
        if self._upload_worker is None or not self._upload_worker.is_alive():
            self._upload_worker = threading.Thread(target=self._run_upload_socket, name="drive-upload-ipc", daemon=True)
            self._upload_worker.start()

    def subscribe_video(self, camera_id: str) -> None:
        """選択方向のコピー画像を取得する。空文字では表示用の取得だけ停止する。"""
        self._camera_client.select(camera_id or None)

    def _drain_camera_frame(self) -> None:
        """Qt側で最新1枚だけ受け取り、画像本文をAppStateへ送らない。"""
        event = self._camera_client.take_latest()
        if event is not None:
            self.camera_frame.emit(event)

    def send_command(self, service: str, action: str, payload: dict | None = None) -> None:
        """外部サービスへの操作を非同期要求として送る。"""
        if self._closed:
            return
        if service == "12 オーディオ連携" and self.audio_controller is not None:
            self.audio_controller.music(action, (payload or {}).get("value"))
            return
        if service not in {"02 Waydroidナビ管理", "waydroid_navigation_manager", "09 OBD2車両情報取得", "obd2_service", "11 LIVI連携", "livi_integration_service"}:
            self.command_result.emit({"service": service, "action": action, "success": False, "reason": "対応する通信先が未実装"})
            return
        operation = {
            "prelaunch_navigation": "prelaunch",
            "start_navigation": "start",
            "show_navigation": "show",
            "hide_navigation": "hide",
            "maintain_navigation_focus": "focus",
            "show_waydroid_home": "show_home",
            "hide_waydroid_home": "hide",
            "maintain_waydroid_home_focus": "focus_home",
            "stop_navigation": "stop",
            "set_polling_mode": "set_polling_mode",
            "start_livi": "start",
            "show_livi": "show",
            "hide_livi": "hide",
            "maintain_livi_focus": "focus",
            "stop_livi": "stop",
            "status_livi": "status",
        }.get(action)
        if operation is None:
            self.command_result.emit({"service": service, "action": action, "success": False, "reason": "対応する操作が未実装"})
            return
        request = {"command_id": f"ui-{uuid.uuid4().hex}", "operation": operation, "arguments": payload or {}, "_service": service, "_action": action}
        if service in {"09 OBD2車両情報取得", "obd2_service"}:
            self._obd_outgoing.put(request)
        elif service in {"11 LIVI連携", "livi_integration_service"}:
            self._start_livi_worker()
            self._livi_outgoing.put(request)
        else:
            self._outgoing.put(request)

    def unsubscribe(self) -> None:
        """状態・映像の購読を停止する。"""
        self._timer.stop()

    def close(self) -> None:
        """新規要求を止め、通信資源を解放する。"""
        self._closed = True
        self._camera_timer.stop()
        self._camera_client.close()
        self._upload_stop.set()
        self.unsubscribe()
        self._outgoing.put({"_close": True})
        self._livi_outgoing.put({"_close": True})
        self._obd_outgoing.put({"_close": True})

    def _emit_stub_state(self) -> None:
        """ソケット未接続時だけ、開発用の待機状態を通知する。"""
        if self._worker is None or not self._worker.is_alive():
            self.service_event.emit({"service_status": "待機中（管理プログラム未接続）"})
            if not self._closed:
                self._start_worker()

    def _run_fan_socket(self):
        """冷却サービスの通知だけを読み、切断時には未接続へ戻す。"""
        path = self._config.get("fan_socket_path", runtime_socket_path("l880k-fan.sock"))
        if not hasattr(socket, "AF_UNIX"):
            return
        while not self._closed:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(1)
                    client.connect(path)
                    buffer = bytearray()
                    while not self._closed:
                        try:
                            chunk = client.recv(4096)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        buffer.extend(chunk)
                        if len(buffer) > 65536:
                            raise ValueError("ファン通知が大きすぎます")
                        while b"\n" in buffer:
                            raw, _, rest = buffer.partition(b"\n")
                            buffer = bytearray(rest)
                            event = json.loads(raw)
                            if isinstance(event, dict) and event.get("event") == "fan.status":
                                self.service_event.emit(event)
            except (OSError, ValueError):
                pass
            if not self._closed:
                self.service_event.emit({"fan_disconnected": True})
                self._upload_stop.wait(1)

    def _start_worker(self) -> None:
        """ナビ管理ソケットの受信ワーカーを必要なときだけ開始する。"""
        if self._closed or not hasattr(socket, "AF_UNIX"):
            return
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._run_socket, name="navigation-ipc", daemon=True)
        self._worker.start()

    def _start_obd_worker(self) -> None:
        """OBD2ソケットの受信ワーカーを重複起動せず開始する。"""
        if self._closed or not hasattr(socket, "AF_UNIX"):
            return
        if self._obd_worker is not None and self._obd_worker.is_alive():
            return
        self._obd_worker = threading.Thread(target=self._run_obd_socket, name="obd2-ipc", daemon=True)
        self._obd_worker.start()

    def _start_livi_worker(self) -> None:
        """LIVI専用ソケットの受信ワーカーを必要なときだけ開始する。"""
        if self._closed or not hasattr(socket, "AF_UNIX"):
            return
        if self._livi_worker is not None and self._livi_worker.is_alive():
            return
        self._livi_worker = threading.Thread(target=self._run_livi_socket, name="livi-ipc", daemon=True)
        self._livi_worker.start()

    def _start_position_worker(self) -> None:
        """現在地補正ソケットの受信ワーカーを重複起動せず開始する。"""
        if self._closed or not hasattr(socket, "AF_UNIX"):
            return
        if self._position_worker is not None and self._position_worker.is_alive():
            return
        self._position_worker = threading.Thread(target=self._run_position_socket, name="position-ipc", daemon=True)
        self._position_worker.start()

    def _run_upload_socket(self) -> None:
        """送信状態を1秒周期で照会する。サービスを起動・終了させる責務は持たない。"""
        if not hasattr(socket, "AF_UNIX"):
            return
        while not self._closed:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(2)
                    client.connect(self._upload_socket_path)
                    while not self._closed:
                        client.sendall(b'{"operation":"status"}\n')
                        buffer = bytearray()
                        while b"\n" not in buffer:
                            chunk = client.recv(4096)
                            if not chunk or len(buffer) + len(chunk) > 65536:
                                raise OSError("アップロード状態通知が途絶えました")
                            buffer.extend(chunk)
                        event = json.loads(buffer.split(b"\n", 1)[0])
                        if not isinstance(event, dict) or event.get("event") != "upload.status":
                            raise ValueError("アップロード状態の形式が不正です")
                        self.service_event.emit(event)
                        self._upload_stop.wait(1)
            except (OSError, ValueError):
                if not self._closed:
                    self.service_event.emit({"upload_service_status": "DISCONNECTED"})
                    self._upload_stop.wait(1)

    def _run_socket(self) -> None:
        """ナビ管理ソケットへ再接続しながら要求と状態通知を処理する。"""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(1.0)
                client.connect(self._socket_path)
                self.service_event.emit({"service_status": "管理プログラム接続済み"})
                while not self._closed:
                    try:
                        request = self._outgoing.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if request.get("_close"):
                        return
                    service = request.pop("_service", "waydroid_navigation_manager")
                    action = request.pop("_action", "")
                    client.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
                    result = _receive_response(client, self.service_event.emit)
                    if result is None:
                        if not self._closed:
                            self.service_event.emit({"service_status": "管理プログラム未接続", "reason": "ソケットが切断されました"})
                        break
                    self.command_result.emit({"service": service, "action": action, "success": bool(result.get("success", result.get("accepted", False))), **result})
        except (OSError, json.JSONDecodeError) as exc:
            if not self._closed:
                self.service_event.emit({"service_status": "管理プログラム未接続", "reason": str(exc)})

    def _run_livi_socket(self) -> None:
        """LIVI連携ソケットへ再接続しながら要求と状態通知を処理する。"""
        while not self._closed:
            request = None
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(1.0)
                    client.connect(self._livi_socket_path)
                    self.service_event.emit({"livi_service_status": "LIVI連携サービス接続済み"})
                    while not self._closed:
                        try:
                            request = self._livi_outgoing.get(timeout=0.5)
                        except queue.Empty:
                            continue
                        if request.get("_close"):
                            return
                        outbound = dict(request)
                        service = outbound.pop("_service", "11 LIVI連携")
                        action = outbound.pop("_action", "")
                        client.sendall((json.dumps(outbound, ensure_ascii=False) + "\n").encode("utf-8"))
                        result = _receive_response(client, self.service_event.emit)
                        if result is None:
                            raise OSError("LIVI連携ソケットが切断されました")
                        self.command_result.emit({"service": service, "action": action, "success": bool(result.get("success", result.get("accepted", False))), **result})
                        request = None
            except (OSError, json.JSONDecodeError) as exc:
                if request is not None and not request.get("_close"):
                    self._livi_outgoing.put(request)
                if not self._closed:
                    self.service_event.emit({"livi_service_status": "LIVI連携サービス未接続", "livi_reason": str(exc)})
                    threading.Event().wait(1.0)

    def _run_obd_socket(self) -> None:
        """OBD2専用ソケットを再接続しながら受信する。"""
        while not self._closed:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(0.2)
                    client.connect(self._obd_socket_path)
                    self.service_event.emit({"obd2_status": "OBD2サービス接続済み"})
                    buffer = bytearray()
                    while not self._closed:
                        while True:
                            try:
                                request = self._obd_outgoing.get_nowait()
                            except queue.Empty:
                                break
                            if request.get("_close"):
                                return
                            request.pop("_service", None)
                            request.pop("_action", None)
                            client.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
                        try:
                            chunk = client.recv(4096)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        buffer.extend(chunk)
                        while b"\n" in buffer:
                            raw, remainder = buffer.split(b"\n", 1)
                            buffer = bytearray(remainder)
                            if not raw.strip():
                                continue
                            event = json.loads(raw.decode("utf-8"))
                            if event.get("event") == "command_result":
                                self.command_result.emit({"service": "09 OBD2車両情報取得", **event})
                            else:
                                self.service_event.emit(event)
                    self.service_event.emit({"obd2_status": "OBD2サービス切断"})
            except (OSError, json.JSONDecodeError) as exc:
                if not self._closed:
                    self.service_event.emit({"obd2_status": "OBD2サービス未接続", "obd2_reason": str(exc)})
                    threading.Event().wait(1.0)

    def _run_position_socket(self) -> None:
        """現在地補正サービスを再接続しながらGPS状態を受信する。"""
        while not self._closed:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(0.2)
                    client.connect(self._position_socket_path)
                    self.service_event.emit({"gps_service_status": "現在地補正サービス接続済み"})
                    client.sendall(b'{"operation":"status"}\n')
                    buffer = bytearray()
                    while not self._closed:
                        try:
                            chunk = client.recv(4096)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        buffer.extend(chunk)
                        if len(buffer) > 65536:
                            raise ValueError("位置通知が大きすぎます")
                        while b"\n" in buffer:
                            raw, remainder = buffer.split(b"\n", 1)
                            buffer = bytearray(remainder)
                            if not raw.strip():
                                continue
                            event = json.loads(raw.decode("utf-8"))
                            if isinstance(event, dict) and event.get("event") in {"position.status", "position.update", "position.invalidate"}:
                                self.service_event.emit(event)
                    if not self._closed:
                        self.service_event.emit({"gps_service_status": "現在地補正サービス未接続"})
            except (OSError, ValueError) as exc:
                if not self._closed:
                    self.service_event.emit({"gps_service_status": "現在地補正サービス未接続", "gps_reason": str(exc)})
                    threading.Event().wait(1.0)


def _receive_response(client: socket.socket, on_event) -> dict | None:
    """ソケットから1行JSONを読み、受信イベントをコールバックへ渡す。"""
    buffer = bytearray()
    while len(buffer) <= 65_536:
        try:
            chunk = client.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            return None
        buffer.extend(chunk)
        while b"\n" in buffer:
            raw, remainder = buffer.split(b"\n", 1)
            buffer = bytearray(remainder)
            if not raw.strip():
                continue
            response = json.loads(raw.decode("utf-8"))
            if response.get("event") == "status":
                on_event(response)
                continue
            # 非同期サーバーの受付応答では読み取りを終えず、
            # 実処理完了後のcommand_resultまで状態通知を受け取る。
            if response.get("accepted") is True and response.get("state") in {"QUEUED", "RUNNING"}:
                continue
            return response
    return None


def _default_socket_path() -> str:
    """ナビ管理IPCの利用者単位の既定ソケットを返す。"""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return os.path.join(runtime_dir, "l880k-navigation.sock")
    uid = getattr(os, "getuid", lambda: "user")()
    return os.path.join("/tmp", f"l880k-navigation-{uid}.sock")


def _default_livi_socket_path() -> str:
    """LIVI連携IPCの利用者単位の既定ソケットを返す。"""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return os.path.join(runtime_dir, "l880k-livi.sock")
    uid = getattr(os, "getuid", lambda: "user")()
    return os.path.join("/tmp", f"l880k-livi-{uid}.sock")


def _default_obd_socket_path() -> str:
    """OBD2 IPCの利用者単位の既定ソケットを返す。"""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return os.path.join(runtime_dir, "l880k-obd2.sock")
    uid = getattr(os, "getuid", lambda: "user")()
    return os.path.join("/tmp", f"l880k-obd2-{uid}.sock")


def _default_position_socket_path() -> str:
    """現在地補正IPCの利用者単位の既定ソケットを返す。"""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return os.path.join(runtime_dir, "l880k-position-correction.sock")
    uid = getattr(os, "getuid", lambda: "user")()
    return os.path.join("/tmp", f"l880k-position-correction-{uid}.sock")
