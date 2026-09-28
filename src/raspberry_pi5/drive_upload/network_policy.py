"""許可済みテザリングの接続UUIDと送信先への経路を確認する。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from urllib.parse import urlsplit

from drive_upload.config import Paused, UploadConfig, UploadError


def check_google_url(url: str) -> str:
    """台帳中のセッションURIを任意の外部ホストへ転送することを防ぐ。"""
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"www.googleapis.com", "oauth2.googleapis.com"}
            or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment):
        raise UploadError("UNTRUSTED_GOOGLE_ENDPOINT")
    return parsed.hostname


class NetworkPolicy:
    """各HTTP要求前に接続を再検査し、送信量を台帳へ予約する。"""

    def __init__(self, config: UploadConfig, catalog, stop) -> None:
        """設定と内部状態を初期化する。"""
        self.config, self.catalog, self.stop = config, catalog, stop
        self.next_send_at = 0.0

    def check_control(self) -> None:
        """停止・手動休止をチャンクとハッシュ計算の境界で反映する。"""
        if self.stop.is_set():
            raise Paused("STOPPING")
        if self.catalog.is_paused():
            raise Paused("PAUSED")

    @staticmethod
    def _run(arguments: list[str], *, allow_unreachable: bool = False) -> str:
        """バックグラウンド処理を実行する。"""
        try:
            result = subprocess.run(arguments, capture_output=True, text=True, timeout=5,
                                    env={**os.environ, "LC_ALL": "C"})
            if (allow_unreachable and result.returncode == 2
                    and result.stderr.strip() in {"RTNETLINK answers: Network is unreachable",
                                                   "RTNETLINK answers: No route to host"}):
                return "[]"
            result.check_returncode()
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise Paused("NETWORK_INSPECTION_UNAVAILABLE") from exc

    def before_request(self, url: str) -> None:
        """到達可能な全宛先の経路を検査する。IPv6経路なしだけならIPv4を使用できる。"""
        self.check_control()
        host = check_google_url(url)
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
            if not addresses:
                raise Paused("NETWORK_OFFLINE")
            reachable = False
            for address in addresses:
                routes = json.loads(self._run(["ip", "-j", "route", "get", address], allow_unreachable=True))
                if routes == []:
                    continue
                if not isinstance(routes, list) or not isinstance(routes[0], dict) or not routes[0].get("dev"):
                    raise Paused("NETWORK_ROUTE_UNKNOWN")
                reachable = True
                device = routes[0]["dev"]
                profile = self._run(["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", device])
                if profile not in self.config.allowed_profile_ids:
                    raise Paused("NETWORK_NOT_ALLOWED")
            if not reachable:
                raise Paused("NETWORK_OFFLINE")
        except (socket.gaierror, ValueError, KeyError, TypeError) as exc:
            raise Paused("NETWORK_ROUTE_UNKNOWN") from exc

    def reserve_chunk(self, size: int) -> None:
        """平均送信帯域と再送を含む日次ペイロード量を制限する。"""
        while time.monotonic() < self.next_send_at:
            self.check_control()
            self.stop.wait(min(0.25, self.next_send_at - time.monotonic()))
        self.before_request("https://www.googleapis.com/")
        if not self.catalog.reserve_bytes(size, self.config.daily_byte_budget):
            raise Paused("DAILY_BUDGET_EXHAUSTED")
        self.next_send_at = time.monotonic() + size / self.config.bandwidth_bytes_per_second
