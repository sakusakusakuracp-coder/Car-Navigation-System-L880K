"""運用設定を読み、送信先・処理量・保存領域の条件を検査する。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class UploadConfig:
    recording_root: Path
    catalog_path: Path
    credential_path: Path
    enabled: bool = False
    auto_delete_enabled: bool = False
    exclusive_recording_root: bool = False
    account_ref: str = ""
    folder_id: str = ""
    allowed_profile_ids: tuple[str, ...] = ()
    chunk_bytes: int = 1024 * 1024
    daily_byte_budget: int = 1024 * 1024 * 1024
    bandwidth_bytes_per_second: int = 1024 * 1024
    io_timeout_s: float = 15.0
    poll_interval_s: float = 2.0
    retry_base_s: float = 10.0
    retry_max_s: float = 600.0
    max_retries: int = 8

    @classmethod
    def load(cls, path: str) -> "UploadConfig":
        """JSON設定の誤記や型違いを起動時に検出する。相対パスは設定ファイル基準。"""
        config_path = Path(path).expanduser().resolve()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) - cls.__dataclass_fields__.keys():
            raise ValueError("アップロード設定の項目が不正です")
        for name in ("recording_root", "catalog_path", "credential_path"):
            raw = Path(data[name]).expanduser()
            data[name] = raw if raw.is_absolute() else config_path.parent / raw
        profiles = data.get("allowed_profile_ids", [])
        if not isinstance(profiles, list) or not all(isinstance(p, str) for p in profiles):
            raise ValueError("allowed_profile_idsには接続UUIDの配列が必要です")
        data["allowed_profile_ids"] = tuple(str(UUID(p)) for p in profiles)
        result = cls(**data)
        result.validate()
        return result

    def validate(self) -> None:
        """無制限送信や設定文字列による誤有効化を防止する。"""
        for name in ("enabled", "auto_delete_enabled", "exclusive_recording_root"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name}はtrue/falseで指定してください")
        for name in ("chunk_bytes", "daily_byte_budget", "bandwidth_bytes_per_second", "max_retries"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name}は正の整数で指定してください")
        if not 262144 <= self.chunk_bytes <= 8 * 1024 * 1024 or self.chunk_bytes % 262144:
            raise ValueError("chunk_bytesは256KiBの倍数、最大8MiBです")
        for name in ("io_timeout_s", "poll_interval_s", "retry_base_s", "retry_max_s"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name}は有限の正数で指定してください")
        if self.io_timeout_s > 30 or self.retry_base_s > self.retry_max_s:
            raise ValueError("通信タイムアウトは30秒以下、再試行初期待機は上限以下にしてください")
        if self.enabled and not (self.account_ref and self.folder_id and self.allowed_profile_ids):
            raise ValueError("送信有効化にはGoogleアカウントID・フォルダID・許可接続UUIDが必要です")
        if self.auto_delete_enabled and not self.exclusive_recording_root:
            raise ValueError("自動削除には録画専用領域と共通ロックの運用確認が必要です")


class UploadError(Exception):
    """秘密値を含まない理由コードだけを外部通知する処理エラー。"""

    def __init__(self, reason: str, *, retryable: bool = False, retry_after: float = 0) -> None:
        """設定と内部状態を初期化する。"""
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.retry_after = retry_after


class Paused(UploadError):
    """ネットワーク・利用者・停止要求による待機。失敗回数に含めない。"""
