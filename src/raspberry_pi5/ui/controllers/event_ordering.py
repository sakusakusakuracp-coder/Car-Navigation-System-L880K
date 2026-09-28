"""外部サービス通知の起動世代・連番を検査する。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

@dataclass
class _StreamCursor:
    boot_id: str | None = None
    sequence: int = 0
    retired_boot_ids: set[str] = field(default_factory=set)


class OrderedEventGate:
    """サービスごとに古い通知を受け入れないための検査器。"""

    def __init__(self) -> None:
        """サービスごとの起動世代と連番を保持する。"""
        self._streams: dict[str, _StreamCursor] = {}

    def accept(self, event: dict[str, Any], *, stream: str | None = None) -> bool:
        """通知が新しい場合だけ受け入れる。

        旧形式の通知には起動世代・連番がないため、後方互換として通す。
        一方、片方だけ存在する不完全なメタデータは受け入れない。
        """
        has_schema = "schema_version" in event
        has_boot = "boot_id" in event
        has_sequence = "sequence" in event
        position_event = str(event.get("event", "")).startswith("position.")
        if position_event and not (has_schema and has_boot and has_sequence):
            return False
        structured_event = has_schema or has_boot or has_sequence or str(event.get("event", "")).startswith("vehicle.") or position_event
        if structured_event:
            schema_version = event.get("schema_version")
            if isinstance(schema_version, bool) or not isinstance(schema_version, int):
                return False
            if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
                return False
        if not has_boot and not has_sequence:
            return True
        if not has_boot or not has_sequence:
            return False
        boot_id = event.get("boot_id")
        sequence = event.get("sequence")
        if not isinstance(boot_id, str) or not boot_id:
            return False
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            return False
        stream_key = stream or self._stream_key(event)
        cursor = self._streams.setdefault(stream_key, _StreamCursor())
        if boot_id in cursor.retired_boot_ids:
            return False
        if cursor.boot_id is None:
            cursor.boot_id = boot_id
            cursor.sequence = sequence
            return True
        if boot_id == cursor.boot_id:
            if sequence <= cursor.sequence:
                return False
            cursor.sequence = sequence
            return True
        # 新しい起動世代へ切り替え、以前の世代を遅延通知として無効化する。
        cursor.retired_boot_ids.add(cursor.boot_id)
        cursor.boot_id = boot_id
        cursor.sequence = sequence
        return True

    @staticmethod
    def _stream_key(event: dict[str, Any]) -> str:
        """イベント種別から連番を比較する論理ストリーム名を決める。"""
        if str(event.get("event", "")).startswith("position."):
            return "position"
        if str(event.get("event", "")).startswith("vehicle."):
            return "obd2"
        return str(event.get("source_service") or event.get("service") or "default")
