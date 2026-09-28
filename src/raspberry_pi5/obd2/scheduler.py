"""要求を1件ずつ、期限と優先順位に従って選ぶ。"""

from __future__ import annotations

from time import monotonic

from .capabilities import CapabilityProbe
from .models import RequestDefinition


class PollScheduler:
    def __init__(self, requests: tuple[RequestDefinition, ...], capabilities: CapabilityProbe) -> None:
        """要求定義と対応可否を保持し、最小取得モードで開始する。"""
        self.requests = requests
        self.capabilities = capabilities
        self.next_due: dict[str, float] = {request.request_id: monotonic() for request in requests}
        self.last_query: dict[str, float] = {}
        self.active_mode = "minimal"

    def set_mode(self, mode: str) -> None:
        """minimal/fullの取得モードを切り替え、対象要求を早期実行対象にする。"""
        if mode not in {"minimal", "full"}:
            raise ValueError(f"未対応のOBD2取得モードです: {mode}")
        self.active_mode = mode
        now = monotonic()
        for request in self.requests:
            if mode in request.poll_modes:
                self.next_due[request.request_id] = min(self.next_due[request.request_id], now)

    def select_next_query(self, now: float | None = None) -> RequestDefinition | None:
        """対応が確認でき、期限を迎えた要求を1件だけ返す。"""

        now = monotonic() if now is None else now
        due = [
            request
            for request in self.requests
            if self.active_mode in request.poll_modes
            and self.capabilities.is_supported(request)
            and self.next_due[request.request_id] <= now
        ]
        if not due:
            return None
        return min(due, key=lambda item: (item.priority, self.next_due[item.request_id]))

    def mark_result(self, request: RequestDefinition, *, now: float | None = None, success: bool = True) -> None:
        """要求結果を記録し、成功・失敗に応じた次回取得時刻を設定する。"""
        now = monotonic() if now is None else now
        interval = request.interval_ms / 1000.0
        if not success:
            interval = max(interval, request.interval_ms / 1000.0 * 2.0)
        self.last_query[request.request_id] = now
        self.next_due[request.request_id] = now + interval
