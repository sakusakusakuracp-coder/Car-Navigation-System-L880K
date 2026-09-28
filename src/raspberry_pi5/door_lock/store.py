"""ドアロック操作の重複実行を防ぐ小さな永続記録。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class CommandStore:
    def __init__(self, path: str) -> None:
        """設定と内部状態を初期化する。"""
        self.path = Path(path)
        self.records: dict[str, dict[str, Any]] = {}

    def load(self) -> None:
        """設定または保存状態を読み込む。"""
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("records", {}), dict):
            raise ValueError("ドアロック操作記録の形式が不正です")
        self.records = {str(key): dict(value) for key, value in data["records"].items()}

    def reserve(self, key: str, fingerprint: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        """CommandStoreのreserveの内部処理を実行する。"""
        existing = self.records.get(key)
        if existing is not None:
            if existing.get("fingerprint") == fingerprint:
                return "EXISTING", existing
            return "ID_CONFLICT", existing
        record = {"fingerprint": fingerprint, "state": "RESERVED"}
        self.records[key] = record
        self._save()
        return "RESERVED", record

    def update(self, key: str, **values: Any) -> None:
        """受信値を内部状態へ反映する。"""
        if key not in self.records:
            raise KeyError(key)
        self.records[key].update(values)
        self._save()

    def _save(self) -> None:
        """現在状態を永続化する。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"records": self.records}, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
