"""カメラの物理対応と、通信・保存量の上限を起動前に検査する。"""

from dataclasses import dataclass, field
import json
import math
from pathlib import Path

CAMERAS = ("front", "rear", "left", "right")


@dataclass(frozen=True)
class CameraConfig:
    recording_root: Path
    catalog_path: Path
    local_cameras: dict = field(default_factory=lambda: {"left": 0, "right": 1})
    remote_cameras: tuple = ("front", "rear")
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 45050
    width: int = 1280
    height: int = 720
    fps: int = 10
    segment_duration_s: float = 60
    max_frame_bytes: int = 400000
    queue_frames: int = 16
    frame_valid_for_ms: int = 1500
    low_space_bytes: int = 512 * 1024 * 1024
    resume_space_bytes: int = 1024 * 1024 * 1024
    segment_max_bytes: int = 256 * 1024 * 1024
    shutdown_timeout_s: float = 20
    reconnect_delay_s: float = 2
    recording_enabled: bool = True

    @classmethod
    def load(cls, path: str):
        """相対パスは設定ファイル基準で解決し、誤記を起動時に止める。"""
        config_path = Path(path).expanduser().resolve()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        for key in ("recording_root", "catalog_path"):
            value = Path(data[key]).expanduser()
            data[key] = value if value.is_absolute() else config_path.parent / value
        result = cls(**data)
        result.validate()
        return result

    def validate(self):
        """入力の重複、無制限キュー、無効な容量・周期設定を拒否する。"""
        if not isinstance(self.local_cameras, dict) or set(self.local_cameras) - {"left", "right"}:
            raise ValueError("local_camerasはleft/rightのCSI番号を指定してください")
        indexes = list(self.local_cameras.values())
        if any(type(x) is not int or x < 0 for x in indexes) or len(set(indexes)) != len(indexes):
            raise ValueError("CSI番号の重複または不正な番号です")
        if set(self.remote_cameras) - {"front", "rear"} or len(set(self.remote_cameras)) != len(self.remote_cameras):
            raise ValueError("remote_camerasはfront/rearを重複せず指定してください")
        for key in ("width", "height", "fps", "gateway_port", "max_frame_bytes", "queue_frames", "frame_valid_for_ms", "low_space_bytes", "resume_space_bytes", "segment_max_bytes"):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError(f"{key}には正の整数が必要です")
        for key in ("segment_duration_s", "shutdown_timeout_s", "reconnect_delay_s"):
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key}には有限の正数が必要です")
        if (self.width > 3840 or self.height > 2160 or self.fps > 60 or self.queue_frames > 64
                or self.max_frame_bytes > 1000000 or self.gateway_port > 65535
                or self.segment_duration_s > 180 or self.shutdown_timeout_s > 60
                or self.frame_valid_for_ms > 10000 or self.resume_space_bytes < self.low_space_bytes
                or self.segment_max_bytes < self.max_frame_bytes * 2):
            raise ValueError("画質・キュー・保存容量・期限の設定が許容範囲外です")
        if type(self.recording_enabled) is not bool:
            raise ValueError("recording_enabledはtrue/falseです")

    @property
    def camera_ids(self):
        """有効化した入力だけを方向別サービスの対象にする。"""
        return tuple(self.local_cameras) + tuple(self.remote_cameras)
