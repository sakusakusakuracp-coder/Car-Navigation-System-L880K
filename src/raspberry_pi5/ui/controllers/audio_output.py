"""PulseAudio互換APIからUSB音声出力の接続だけを観測する。"""

import json
import os
import subprocess


class AudioOutputMonitor:
    def __init__(self, sink_name="", run=subprocess.run):
        """OS上の固定出力名を指定する。音量や既定出力は変更しない。"""
        self.sink_name, self.run = sink_name, run

    def status(self):
        """未対応・照会失敗・未指定・切断を正常な接続状態と区別する。"""
        if os.name != "posix":
            return {"status": "unsupported", "label": "USB音声出力: Linuxで確認", "devices": []}
        try:
            result = self.run(["pactl", "--format=json", "list", "sinks"], capture_output=True, text=True,
                              encoding="utf-8", timeout=3, check=True)
            if len(result.stdout) > 4_000_000:
                raise ValueError("音声出力一覧が大きすぎます")
            sinks = json.loads(result.stdout)
            if not isinstance(sinks, list):
                raise ValueError("音声出力一覧の形式が不正です")
            devices = [{"name": s["name"], "description": s.get("description", s["name"])}
                       for s in sinks if isinstance(s, dict) and isinstance(s.get("name"), str)
                       and isinstance(s.get("properties"), dict) and s["properties"].get("device.bus") == "usb"]
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return {"status": "unknown", "label": "USB音声出力: 確認できません", "reason": str(exc), "devices": []}
        if not self.sink_name:
            return {"status": "unconfigured", "label": f"USB音声出力: 監視対象未指定 ({len(devices)}出力)", "devices": devices}
        present = any(d["name"] == self.sink_name for d in devices)
        return {"status": "connected" if present else "disconnected",
                "label": "USB音声出力: 接続" if present else "USB音声出力: 未接続", "devices": devices}
