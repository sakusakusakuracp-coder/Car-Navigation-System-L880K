"""JPEGを再圧縮せずMatroskaへ格納し、独立再生できる分割録画を作る。"""

from fractions import Fraction
import os
import sqlite3
import time
from uuid import uuid4

from camera.catalog import digest_file, inspect_video, sync_directory


class SegmentWriter:
    def __init__(self, camera_id, config, catalog, health):
        """1方向の現在セグメントと保存上限を保持する。"""
        self.camera_id, self.config, self.catalog, self.health = camera_id, config, catalog, health
        self.container = None
        self.file_id = None
        self.count = 0
        self.byte_count = 0
        self.last_pts = -1
        self.pending_ready = None
        self.allow_new_segments = True

    def start_segment(self, frame):
        """容量予約・WRITING登録を終えてから一時動画を開く。"""
        import av

        if not self.allow_new_segments:
            return False
        if self.pending_ready:
            self.catalog.mark_ready(self.pending_ready)
            self.pending_ready = None
        file_id = uuid4().hex
        if not self.health.reserve_storage(file_id):
            reason = "STORAGE_ERROR" if self.health.check_health()["storage_state"] == "ERROR" else "STORAGE_LOW"
            self.health.update(self.camera_id, record_state="PAUSED", reason=reason)
            return False
        self.file_id = file_id
        try:
            self.partial, self.final = self.catalog.mark_writing(file_id, self.camera_id)
            self.handle = self.partial.open("xb")
            self.container = av.open(self.handle, mode="w", format="matroska")
            self.stream = self.container.add_stream("mjpeg", rate=self.config.fps)
            self.stream.width, self.stream.height = frame.width, frame.height
            self.stream.pix_fmt = "yuvj420p"
            self.stream.time_base = Fraction(1, 1000)
            self.started_mono = frame.received_mono
            self.session_id = frame.stream_session_id
            self.dimensions = (frame.width, frame.height)
            self.clock_quality = frame.clock_quality
            self.captured_start = frame.captured_at
            self.count, self.byte_count, self.last_pts = 0, 0, -1
            self.health.update(self.camera_id, record_state="RECORDING", reason="", file_id=file_id)
            return True
        except Exception as exc:
            if isinstance(exc, (OSError, sqlite3.Error)):
                self.health.storage_error()
            self.abort("WRITER_START_FAILED")
            raise

    def write_packet(self, frame):
        """取得時刻の間隔を保持してJPEGを多重化し、世代・画質変更時に分割する。"""
        import av

        if self.container is not None and (frame.received_mono - self.started_mono >= self.config.segment_duration_s
                or self.session_id != frame.stream_session_id or self.dimensions != (frame.width, frame.height)
                or self.byte_count + len(frame.jpeg) + 65536 >= self.config.segment_max_bytes):
            self.rotate_segment()
        if self.container is None and not self.start_segment(frame):
            return
        packet = av.Packet(frame.jpeg)
        packet.stream = self.stream
        packet.time_base = Fraction(1, 1000)
        pts = max(self.last_pts + 1, round((frame.received_mono - self.started_mono) * 1000))
        packet.pts = packet.dts = pts
        packet.duration = max(1, round(1000 / self.config.fps))
        packet.is_keyframe = True
        self.container.mux(packet)
        self.last_pts = pts
        self.count += 1
        self.byte_count += len(frame.jpeg) + 128
        self.health.consume(self.file_id, self.partial.stat().st_size)
        self.last_frame = frame

    def rotate_segment(self):
        """JPEG全フレームが独立画像であることを利用し、現在録画を確定する。"""
        return self.finalize_segment()

    def finalize_segment(self):
        """終端・全フレーム検査・同期・改名を済ませた動画だけをREADYへ渡す。"""
        if self.container is None:
            return None
        file_id = self.file_id
        self.health.update(self.camera_id, record_state="FINALIZING")
        try:
            self.container.close()
            self.container = None
            self.handle.flush()
            os.fsync(self.handle.fileno())
            self.handle.close()
            count = inspect_video(self.partial)
            if count != self.count:
                raise ValueError("FRAME_COUNT_MISMATCH")
            evidence = {"frame_count": count, "sha256": digest_file(self.partial),
                        "size_bytes": self.partial.stat().st_size,
                        "stream_session_id": self.session_id, "clock_quality": self.clock_quality,
                        "captured_start": self.captured_start, "captured_end": self.last_frame.captured_at,
                        "timeline": "PI5_RECEIVE_MONOTONIC", "ended_at": time.time(),
                        "drop_count_total": self.health.check_health()["cameras"][self.camera_id]["drop_count"]}
            self.catalog.set_evidence(file_id, evidence)
            self.partial.rename(self.final)
            sync_directory(self.config.recording_root)
            self.pending_ready = file_id
            self.catalog.mark_ready(file_id)
            self.pending_ready = None
            self.health.update(self.camera_id, record_state="WAIT_INPUT", last_ready=file_id, file_id=None)
            return file_id
        except Exception as exc:
            if isinstance(exc, (OSError, sqlite3.Error)):
                self.health.storage_error()
            self.abort("FINALIZE_FAILED")
            raise
        finally:
            self.health.release_storage(file_id)
            self.file_id = None

    def abort(self, reason):
        """書込失敗を未完了として残し、ハンドルと容量予約を解放する。"""
        try:
            if self.container is not None:
                self.container.close()
        finally:
            self.container = None
            if getattr(self, "handle", None) is not None:
                self.handle.close()
            if self.file_id:
                self.catalog.mark_incomplete(self.file_id, reason)
                self.health.release_storage(self.file_id)
                self.file_id = None
            self.health.update(self.camera_id, record_state="FAULT", reason=reason)

    def stop_recording(self):
        """最後のセグメントを確定する。アップロード完了は待たない。"""
        return self.finalize_segment()
