"""方向別の映像監視と、全録画に共通する保存容量の予約。"""

import shutil
import threading
import time


class HealthMonitor:
    def __init__(self, config):
        """容量予約と方向別状態を、複数入力から更新できるようにする。"""
        self.config = config
        self._lock = threading.RLock()
        self._reserved = {}
        self._low = False
        self._storage_error = False
        self._states = {c: {"input_state": "STARTING", "record_state": "WAIT_INPUT", "drop_count": 0,
                           "reason": "", "last_frame": None, "clock_quality": "UNKNOWN"} for c in config.camera_ids}

    def update(self, camera_id, **values):
        """取得・保存処理が確認した事実だけを方向別状態へ反映する。"""
        with self._lock:
            self._states[camera_id].update(values)

    def dropped(self, camera_id, count=1):
        """処理能力や通信の不足で失ったフレーム数を保持する。"""
        with self._lock:
            self._states[camera_id]["drop_count"] += count

    def reserve_storage(self, file_id):
        """全方向の予約残量を差し引き、セグメントの最大容量を確保する。"""
        with self._lock:
            if self._storage_error:
                return False
            free = shutil.disk_usage(self.config.recording_root).free - sum(self._reserved.values())
            threshold = self.config.resume_space_bytes if self._low else self.config.low_space_bytes
            if free - self.config.segment_max_bytes < threshold:
                self._low = True
                return False
            self._low = False
            self._reserved[file_id] = self.config.segment_max_bytes
            return True

    def storage_error(self):
        """保存I/O異常では全方向の新規録画を停止し、確認後の再起動を待つ。"""
        with self._lock:
            self._storage_error = True

    def consume(self, file_id, used_bytes):
        """既にディスクへ書いた分を予約残量から引き、二重計上を避ける。"""
        with self._lock:
            self._reserved[file_id] = max(0, self.config.segment_max_bytes - used_bytes)

    def release_storage(self, file_id):
        """確定または未完了記録の後に、残り予約を解放する。"""
        with self._lock:
            self._reserved.pop(file_id, None)

    def check_health(self):
        """更新が途絶えた映像を失効させ、実際の空き容量を返す。"""
        now = time.monotonic()
        with self._lock:
            states = {key: dict(value) for key, value in self._states.items()}
        for state in states.values():
            last = state.pop("last_frame")
            state["frame_age_ms"] = None if last is None else int((now - last) * 1000)
            if last is not None and (now - last) * 1000 >= self.config.frame_valid_for_ms:
                state["input_state"] = "STALE"
        try:
            free = shutil.disk_usage(self.config.recording_root).free
            storage = "ERROR" if self._storage_error else ("LOW" if self._low or free < self.config.low_space_bytes else "OK")
        except OSError:
            free, storage = None, "ERROR"
        return {"cameras": states, "storage_state": storage, "free_bytes": free,
                "recording": any(s["record_state"] == "RECORDING" and s["input_state"] == "STREAMING" for s in states.values())}
