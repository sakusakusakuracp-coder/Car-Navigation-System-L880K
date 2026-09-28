"""要求番号の重複と結果を管理する。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RequestRecord:
    command_id: str
    operation: str
    arguments: dict[str, Any]
    status: str = "ACCEPTED"
    result: dict[str, Any] | None = None


class RequestRegistry:
    def __init__(self, limit: int = 128) -> None:
        """設定と内部状態を初期化する。"""
        self._records: dict[str, RequestRecord] = {}
        self._limit = limit

    def register(self, command_id: str, operation: str, arguments: dict[str, Any]) -> RequestRecord:
        """要求または接続を管理対象へ登録する。"""
        existing = self._records.get(command_id)
        if existing:
            if existing.operation != operation or existing.arguments != arguments:
                raise ValueError("同じcommand_idに異なる要求は登録できません")
            return existing
        if len(self._records) >= self._limit:
            oldest = next(iter(self._records))
            del self._records[oldest]
        record = RequestRecord(command_id, operation, dict(arguments))
        self._records[command_id] = record
        return record

    def complete(self, command_id: str, result: dict[str, Any]) -> None:
        """処理完了結果を記録する。"""
        if command_id in self._records:
            self._records[command_id].status = "COMPLETED"
            self._records[command_id].result = dict(result)

    def get(self, command_id: str) -> RequestRecord | None:
        """登録済みの値または要求を取得する。"""
        return self._records.get(command_id)
