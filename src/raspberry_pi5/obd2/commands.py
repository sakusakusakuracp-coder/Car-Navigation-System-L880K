"""ECUへ送信できる要求の許可リスト。"""

from __future__ import annotations

from .models import RequestDefinition


class CommandRejected(PermissionError):
    """要求がプロファイルの読み取り許可リストにない。"""


class RequestAllowlist:
    def __init__(self, requests: tuple[RequestDefinition, ...]) -> None:
        """設定済み要求をIDから検索できる許可リストへ変換する。"""
        self._requests = {request.request_id: request for request in requests}

    def get(self, request_id: str) -> RequestDefinition:
        """許可された要求を取得し、未登録なら送信を拒否する。"""
        try:
            request = self._requests[request_id]
        except KeyError as exc:
            raise CommandRejected(f"未登録のOBD要求です: {request_id}") from exc
        if request.operation != "read":
            raise CommandRejected(f"読み取り以外のOBD操作です: {request_id}")
        return request

    def all(self) -> tuple[RequestDefinition, ...]:
        """登録済み要求を設定順に返す。"""
        return tuple(self._requests.values())
