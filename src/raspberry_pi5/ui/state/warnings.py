"""警告の発生・確認・回復を独立して保持する有限の履歴。"""

from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4
from ui.config.preferences import atomic_json


class WarningHistory:
    def __init__(self):
        """同一原因の連発を抑えるため、発生元付きのIDで管理する。"""
        self.items = []
        self.revision = 0

    def load(self, path):
        """過去の警告を検証し、終了前の発生中警告を現在の異常や回復と断定しない。"""
        path = Path(path)
        if not path.exists():
            return
        if path.stat().st_size > 1_000_000:
            raise ValueError("警告履歴が大きすぎます")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ValueError("警告履歴の形式が未対応です")
        items = data.get("items")
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError("警告履歴の件数が不正です")
        for item in items:
            if (not isinstance(item, dict)
                    or any(not isinstance(item.get(k), str) for k in ("id", "key", "message", "severity", "time", "recovered_at"))
                    or any(type(item.get(k)) is not bool for k in ("active", "acknowledged"))
                    or item["severity"] not in {"warning", "critical"}):
                raise ValueError("警告履歴の項目が不正です")
        self.items = [{**x, "previous_run": bool(x["active"] or x.get("previous_run")), "active": False} for x in items]
        self.revision += 1

    def snapshot(self):
        """非同期保存中に履歴が変わっても影響しないコピーを返す。"""
        return {"schema_version": 1, "items": [dict(x) for x in self.items]}

    @staticmethod
    def save(path, snapshot):
        """正常に書き終えた履歴だけを既存ファイルと置換する。"""
        atomic_json(path, snapshot)

    def update(self, key, message, active=True, severity="warning"):
        """原因が回復したときだけ解除し、再発は未確認の新しい履歴として残す。"""
        before = self.snapshot()
        item = next((x for x in self.items if x["key"] == key and x["active"]), None)
        if active:
            if item:
                item.update(message=message, severity=severity)
            else:
                self.items.insert(0, {"id": uuid4().hex, "key": key,
                    "message": message, "severity": severity, "active": True, "acknowledged": False,
                    "time": datetime.now().strftime("%Y/%m/%d %H:%M:%S"), "recovered_at": "", "previous_run": False})
        elif item:
            item.update(active=False, recovered_at=datetime.now().strftime("%m/%d %H:%M:%S"))
        # 有効な警告を優先して残し、履歴だけが増え続けることを防ぐ。
        active_items = [x for x in self.items if x["active"]]
        past = [x for x in self.items if not x["active"]]
        self.items = (active_items + past)[:100]
        if before != self.snapshot():
            self.revision += 1

    def acknowledge(self, identifier):
        """確認操作で原因のactive状態を変えない。"""
        for item in self.items:
            if item["id"] == identifier and not item["acknowledged"]:
                item["acknowledged"] = True
                self.revision += 1

    @property
    def banner(self):
        """未確認の重要警告を優先し、画面を覆う表示は一件に限定する。"""
        candidates = [x for x in self.items if x["active"] and not x["acknowledged"]]
        return next((x for x in candidates if x["severity"] == "critical"), candidates[0] if candidates else {})
