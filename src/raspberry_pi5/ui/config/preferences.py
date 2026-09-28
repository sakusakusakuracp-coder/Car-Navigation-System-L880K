"""利用者設定を検証し、確定済みの内容だけを原子的に保存する。"""

import json
import os
from pathlib import Path
import tempfile
from ui.config.file_lock import FileLock

DEFAULTS = {"schema_version": 1, "animations": True, "night_mode": True,
            "startup_screen": "home", "coolant_warning": 105, "voltage_warning": 11.8}


def validate(data):
    """単位ごとの範囲と型を確認し、未対応設定を取り込まない。"""
    if not isinstance(data, dict) or set(data) - DEFAULTS.keys():
        raise ValueError("設定項目が不正です")
    result = {**DEFAULTS, **data}
    if type(result["schema_version"]) is not int or result["schema_version"] != 1:
        raise ValueError("設定のバージョンが未対応です")
    for key in ("animations", "night_mode"):
        if type(result[key]) is not bool:
            raise ValueError("表示設定はON/OFFで指定してください")
    if not isinstance(result["startup_screen"], str) or result["startup_screen"] not in {"home", "navigation", "camera", "audio", "vehicle", "settings"}:
        raise ValueError("起動画面が不正です")
    for key, low, high in (("coolant_warning", 80, 130), ("voltage_warning", 10, 15)):
        if type(result[key]) not in (int, float) or not low <= result[key] <= high:
            raise ValueError("警告値が許容範囲外です")
    return result


def atomic_json(path, data):
    """同じディレクトリの一時ファイルを同期して置換し、途中書込みを残さない。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Preferences:
    def __init__(self, path=None):
        """運用ファイルとバックアップ先を利用者単位で決める。"""
        self.path = Path(path) if path else Path.home() / ".config/l880k-car-navigation/preferences.json"
        self.backup_path = self.path.with_suffix(".backup.json")
        self.expected = self.path.read_bytes() if self.path.exists() else None

    def load(self, backup=False):
        """破損は呼出元へ通知し、復元時も通常と同じ検証を行う。"""
        path = self.backup_path if backup else self.path
        raw = path.read_bytes() if path.exists() else None
        if not backup:
            self.expected = raw
        if raw is None and not backup:
            return dict(DEFAULTS)
        if raw is None:
            raise ValueError("バックアップがありません")
        return validate(json.loads(raw.decode("utf-8")))

    def save(self, data, backup=False):
        """検証した設定だけを保存して、実際に保存した値を返す。"""
        value = validate(data)
        with FileLock(str(self.path) + ".write.lock"):
            current = self.path.read_bytes() if self.path.exists() else None
            if not backup and current != self.expected:
                raise ValueError("設定が別の処理で変更されました。「再読込」で確認してから編集してください")
            atomic_json(self.backup_path if backup else self.path, value)
            if not backup:
                self.expected = self.path.read_bytes()
        return value
