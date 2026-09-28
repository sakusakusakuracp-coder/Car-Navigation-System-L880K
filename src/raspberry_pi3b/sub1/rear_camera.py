"""後方CSIカメラの取得とPi 5への送信データ作成。"""

from __future__ import annotations

import base64
from dataclasses import asdict
from typing import Any

from subcontroller_common import CameraDriver


class RearCameraService:
    """カメラをバックギア監視と独立して動かす。"""

    def __init__(self, driver: CameraDriver, width: int, height: int, fps: int, max_frame_bytes: int = 400_000) -> None:
        """設定と内部状態を初期化する。"""
        self.driver = driver
        self.width = width
        self.height = height
        self.fps = fps
        self.max_frame_bytes = max_frame_bytes
        self.sequence = 0
        self.stream_session_id = "rear-start"

    def start(self) -> None:
        """サービスまたはデバイスを起動する。"""
        self.driver.start()

    def capture_and_send(self) -> dict[str, Any]:
        """RearCameraServiceのcapture_and_sendの内部処理を実行する。"""
        self.sequence += 1
        frame = self.driver.capture("rear", self.sequence)
        if frame is None:
            return {"camera_id": "rear", "camera_state": "FAULT", "reason": "FRAME_UNAVAILABLE"}
        event: dict[str, Any] = {
            "camera_id": "rear",
            "camera_state": "STREAMING",
            "stream_session_id": self.stream_session_id,
            "frame_sequence": frame.sequence,
            "captured_at": frame.captured_at_utc,
            "clock_quality": frame.clock_quality,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
        }
        if frame.jpeg_bytes is not None:
            if len(frame.jpeg_bytes) > self.max_frame_bytes:
                event.update(camera_state="DEGRADED", dropped_frames=1, reason="FRAME_TOO_LARGE")
            else:
                event["jpeg_base64"] = base64.b64encode(frame.jpeg_bytes).decode("ascii")
        return event

    def reset_stream(self, stream_session_id: str) -> None:
        """RearCameraServiceのreset_streamの内部処理を実行する。"""
        self.stream_session_id = stream_session_id

    def stop(self) -> None:
        """サービスまたはデバイスを停止して資源を解放する。"""
        self.driver.close()
