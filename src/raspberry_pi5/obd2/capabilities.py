"""取得項目の対応状態を管理する。"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from .decoder import DecodeError, decode_response
from .kline_session import KLineError, KLineSession
from .models import RequestDefinition, SupportState, VehicleProfile


@dataclass(frozen=True)
class Capability:
    request_id: str
    key: str
    state: SupportState
    reason: str
    checked_mono: float


class CapabilityProbe:
    def __init__(self, session: KLineSession, profile: VehicleProfile) -> None:
        """K-Line応答を用いた車両機能確認器を初期化する。"""
        self.session = session
        self.profile = profile
        self.results: dict[str, Capability] = {}

    def probe_capabilities(self, active_mode: str = "minimal") -> dict[str, Capability]:
        """表示モードに必要な要求だけを有限回実行する。"""

        for request in self.profile.requests:
            if active_mode not in request.poll_modes:
                continue
            if not request.probe:
                self.results.setdefault(
                    request.request_id,
                    Capability(request.request_id, request.key, SupportState.SUPPORTED, "確認済みプロファイルで明示", monotonic()),
                )
                continue
            try:
                frame = self.session.send_query(request.request_id)
                decode_response(frame, request, self.profile)
            except (KLineError, DecodeError) as exc:
                state = SupportState.UNSUPPORTED if request.negative_is_unsupported else SupportState.UNKNOWN
                reason = str(exc)
            else:
                state = SupportState.SUPPORTED
                reason = "確認済み応答あり"
            self.results[request.request_id] = Capability(
                request.request_id, request.key, state, reason, monotonic()
            )
        return dict(self.results)

    def is_supported(self, request: RequestDefinition) -> bool:
        """要求が確認済みで、現在の車両へ送信可能か判定する。"""
        result = self.results.get(request.request_id)
        return result is not None and result.state == SupportState.SUPPORTED
