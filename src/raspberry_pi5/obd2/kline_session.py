"""確認済みプロファイルに従うK-Lineセッション。"""

from __future__ import annotations

import time
from dataclasses import replace

from .commands import RequestAllowlist
from .connection import AdapterConnection, AdapterError
from .models import InitStep, RequestDefinition, ResponseFrame, VehicleProfile


class KLineError(RuntimeError):
    """K-Lineフレーム、初期化、同期状態のエラー。"""


class KLineSession:
    def __init__(self, adapter: AdapterConnection, profile: VehicleProfile) -> None:
        """K-Lineアダプタと車種別初期化・要求プロファイルを結び付ける。"""
        self.adapter = adapter
        self.profile = profile
        self.allowlist = RequestAllowlist(profile.requests)
        self.session_epoch = 0
        self.ready = False
        self._last_query_mono = 0.0

    def initialize_kline(self) -> bool:
        """確認済み初期化手順だけを実行する。未確認なら送信しない。"""

        if not self.profile.is_usable:
            self.ready = False
            return False
        if not self.adapter.is_open:
            raise KLineError("USBアダプタが接続されていません")
        for _attempt in range(self.profile.init_retry_limit + 1):
            try:
                for step in self.profile.init_steps:
                    self._run_init_step(step)
                self.session_epoch += 1
                self.ready = True
                return True
            except (AdapterError, KLineError):
                self.ready = False
                if _attempt >= self.profile.init_retry_limit:
                    return False
                time.sleep(self.profile.init_timeout_ms / 1000.0)
        return False

    def _run_init_step(self, step: InitStep) -> None:
        """初期化手順の待機、送信、応答確認を1ステップ実行する。"""
        if step.action == "wait":
            time.sleep(step.wait_ms / 1000.0)
            return
        self.adapter.write_bytes(step.data)
        if step.expect_prefix:
            response = self.adapter.read_bytes(
                max_bytes=self.profile.max_frame_length,
                timeout=self.profile.init_timeout_ms / 1000.0,
            )
            if not response.startswith(step.expect_prefix):
                raise KLineError("初期化応答が期待値と一致しません")
        if step.wait_ms:
            time.sleep(step.wait_ms / 1000.0)

    def send_query(self, request_id: str) -> ResponseFrame:
        """許可リストにある要求を周期制限付きで送信し、応答を受信する。"""
        if not self.ready:
            raise KLineError("K-Lineセッションが確立していません")
        request = self.allowlist.get(request_id)
        elapsed_ms = (time.monotonic() - self._last_query_mono) * 1000.0
        if elapsed_ms < self.profile.min_query_gap_ms:
            time.sleep((self.profile.min_query_gap_ms - elapsed_ms) / 1000.0)
        started = time.monotonic()
        self.adapter.write_bytes(request.request)
        self._last_query_mono = started
        return self.receive_frame(request, started_mono=started)

    def receive_frame(self, request: RequestDefinition, *, started_mono: float) -> ResponseFrame:
        """K-Line応答の長さ、エコー、チェックサムを検証してフレーム化する。"""
        payload = bytearray(
            self.adapter.read_bytes(
                max_bytes=request.response_length or self.profile.response_length or self.profile.max_frame_length,
                timeout=request.timeout_ms / 1000.0,
            )
        )
        if not payload:
            raise KLineError(f"応答がありません: {request.request_id}")
        if self.profile.echo_mode == "full" and payload.startswith(request.request):
            del payload[: len(request.request)]
        if not payload:
            raise KLineError("エコー後に応答がありません")
        if len(payload) > self.profile.max_frame_length:
            raise KLineError("応答フレームが長すぎます")
        if request.response_length and len(payload) != request.response_length:
            raise KLineError("応答フレーム長が要求定義と一致しません")
        if self.profile.checksum == "sum8":
            if len(payload) < 2 or (sum(payload[:-1]) & 0xFF) != payload[-1]:
                raise KLineError("応答チェックサムが不正です")
            del payload[-1]
        return ResponseFrame(
            payload=bytes(payload),
            query_id=request.request_id,
            connection_epoch=self.adapter.connection_epoch,
            session_epoch=self.session_epoch,
            started_mono=started_mono,
            received_mono=time.monotonic(),
        )

    def invalidate(self) -> None:
        """通信セッションを無効化し、次回初期化が必要な世代へ進める。"""
        self.ready = False
        self.session_epoch += 1
