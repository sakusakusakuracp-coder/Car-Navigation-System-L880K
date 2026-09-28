"""映像取得・表示配信・録画を独立して動かすサービス本体。"""

import logging
import json
from dataclasses import replace
import queue
import sqlite3
import threading
import time
from uuid import uuid4

from camera.catalog import RecordingCatalog
from camera.frames import FrameDistributor, validate_jpeg
from camera.health import HealthMonitor
from camera.inputs import CaptureAdapter, StreamReceiver
from camera.segment_writer import SegmentWriter

LOGGER = logging.getLogger("l880k.camera")


class CameraService:
    def __init__(self, config):
        """方向ごとの有限録画キューと共通台帳を組み立てる。"""
        config.validate()
        self.config = config
        self.catalog = RecordingCatalog(config)
        self._settings_lock = threading.RLock()
        with self.catalog.shared.connect() as db:
            saved = db.execute("SELECT value FROM settings WHERE key='camera_ui_settings'").fetchone()
        if saved:
            values = json.loads(saved[0])
            if (not isinstance(values, dict) or set(values) != {"recording_enabled", "segment_duration_s"}
                    or values["segment_duration_s"] not in (60, 120, 180)):
                raise ValueError("保存された録画設定が不正です")
            config = replace(config, **values)
            config.validate()
            self.config = config
        self.health = HealthMonitor(config)
        self.distributor = FrameDistributor(config.frame_valid_for_ms)
        self.stop = threading.Event()
        self._record_stop = threading.Event()
        self.boot_id = uuid4().hex
        self.sequence = 0
        self._status_lock = threading.Lock()
        self.queues = {c: queue.Queue(config.queue_frames) for c in config.camera_ids}
        self._capture_threads = []
        self._writers = []

    def accept_frame(self, frame):
        """画像検査後、表示と録画へ分岐する。録画混雑を表示の待ちにしない。"""
        if self.stop.is_set():
            return
        validate_jpeg(frame.jpeg, self.config.max_frame_bytes, frame.width, frame.height)
        self.health.update(frame.camera_id, input_state="STREAMING", last_frame=frame.received_mono,
                           clock_quality=frame.clock_quality, width=frame.width, height=frame.height)
        self.distributor.publish_frame(frame)
        if self.config.recording_enabled:
            try:
                self.queues[frame.camera_id].put_nowait(frame)
            except queue.Full:
                self.health.dropped(frame.camera_id)

    def start(self):
        """未完了録画を回復してから、方向別録画と入力を開始する。"""
        self.catalog.recover_catalog()
        for camera in self.config.camera_ids:
            thread = threading.Thread(target=self._record, args=(camera,), name=f"record-{camera}", daemon=True)
            self._writers.append(thread)
            thread.start()
        for camera, index in self.config.local_cameras.items():
            thread = threading.Thread(target=self._capture, args=(camera, index), name=f"capture-{camera}", daemon=True)
            self._capture_threads.append(thread)
            thread.start()
        if self.config.remote_cameras:
            thread = threading.Thread(target=StreamReceiver(self.config).connect_remote,
                                      args=(self.stop, self.accept_frame, self.health), name="camera-remote", daemon=True)
            self._capture_threads.append(thread)
            thread.start()

    def _capture(self, camera, index):
        """個別CSI障害を監視し、待機後にその方向だけ開き直す。"""
        while not self.stop.is_set():
            adapter = CaptureAdapter(camera, index, self.config)
            try:
                adapter.open_local_camera()
                while not self.stop.is_set():
                    self.accept_frame(adapter.accept_frame())
            except Exception:
                LOGGER.exception("%sの映像取得に失敗", camera)
                self.health.update(camera, input_state="FAULT", reason="CAPTURE_FAILED")
            finally:
                try:
                    adapter.close()
                except Exception:
                    LOGGER.exception("%sの入力解放に失敗", camera)
            self.stop.wait(self.config.reconnect_delay_s)

    def _record(self, camera):
        """方向別に書き込み、入力途絶時には末尾を閉じて再入力に備える。"""
        writer = SegmentWriter(camera, self.config, self.catalog, self.health)
        waiting = self.queues[camera]
        try:
            while not self._record_stop.is_set() or not waiting.empty():
                writer.config = self.config
                if not self.config.recording_enabled and writer.container:
                    writer.finalize_segment()
                try:
                    frame = waiting.get(timeout=0.1)
                except queue.Empty:
                    if writer.container and time.monotonic() - writer.last_frame.received_mono >= self.config.frame_valid_for_ms / 1000:
                        try:
                            writer.finalize_segment()
                        except Exception:
                            LOGGER.exception("%sの入力停止後の確定に失敗", camera)
                    continue
                try:
                    if not self.config.recording_enabled:
                        continue
                    writer.allow_new_segments = not self._record_stop.is_set()
                    if self.health.check_health()["storage_state"] in {"LOW", "ERROR"}:
                        writer.finalize_segment()
                        # start_segmentの予約判定が再開閾値も検査する。
                    writer.write_packet(frame)
                except Exception as exc:
                    LOGGER.exception("%sの録画処理に失敗", camera)
                    if isinstance(exc, (OSError, sqlite3.Error)):
                        self.health.storage_error()
                    writer.abort("RECORDING_FAILED")
                    self._record_stop.wait(self.config.reconnect_delay_s)
        except Exception:
            LOGGER.exception("%sの録画監視に失敗", camera)
            writer.abort("RECORDING_WORKER_FAILED")
        finally:
            try:
                writer.stop_recording()
            except Exception:
                LOGGER.exception("%sの最後の録画を確定できません", camera)

    def status(self):
        """UIが期限監視できる方向別状態と保存領域の状態を返す。"""
        with self._status_lock:
            self.sequence += 1
            return {"schema_version": 1, "source_service": "06 カメラ表示・録画", "event": "camera.status",
                    "boot_id": self.boot_id, "sequence": self.sequence, "valid_for_ms": 3000,
                    **self.health.check_health()}

    def request(self, request):
        """状態照会とコピー画像の取得を受け付け、録画の寿命と切り離す。"""
        if not isinstance(request, dict):
            raise ValueError("REQUEST_INVALID")
        operation = request.get("operation")
        if operation == "settings":
            return {"accepted": True, "settings": self.recording_settings()}
        if operation == "apply_settings":
            return self.apply_settings(request.get("settings"))
        if operation == "status":
            return self.status()
        if operation == "subscribe_video":
            key = self.distributor.subscribe_frames(request.get("camera_id"), request.get("display_generation"), request.get("consumer_boot_id"))
            return {"accepted": True, "subscription_id": key}
        if operation == "get_frame":
            return self.distributor.get_frame(request.get("subscription_id"))
        if operation == "unsubscribe_video":
            self.distributor.unsubscribe_frames(request.get("subscription_id"))
            return {"accepted": True}
        return {"accepted": False, "reason": "OPERATION_UNSUPPORTED"}

    def recording_settings(self):
        """UIで変更可能な録画設定だけを公開する。"""
        with self._settings_lock:
            return {"recording_enabled": self.config.recording_enabled, "segment_duration_s": self.config.segment_duration_s}

    def apply_settings(self, values):
        """検証・永続化後に設定を交換し、停止要求は録画側で終端処理する。"""
        if not isinstance(values, dict) or set(values) != {"recording_enabled", "segment_duration_s"}:
            raise ValueError("録画設定が不正です")
        if values["segment_duration_s"] not in (60, 120, 180):
            raise ValueError("分割時間は60・120・180秒です")
        with self._settings_lock:
            updated = replace(self.config, **values)
            updated.validate()
            with self.catalog.shared.connect() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES ('camera_ui_settings',?)", (json.dumps(values),))
            self.config = updated
        return {"accepted": True, "settings": self.recording_settings(), "state": "APPLIED"}

    def close(self):
        """入力停止後に録画キューを閉じる。待機上限を超えたら失敗として返す。"""
        deadline = time.monotonic() + self.config.shutdown_timeout_s
        self.stop.set()
        self._record_stop.set()
        for thread in self._capture_threads:
            thread.join(max(0, deadline - time.monotonic()))
        for thread in self._writers:
            thread.join(max(0, deadline - time.monotonic()))
        complete = not any(t.is_alive() for t in self._capture_threads + self._writers)
        failed = any(s["record_state"] == "FAULT" for s in self.health.check_health()["cameras"].values())
        return complete and not failed
