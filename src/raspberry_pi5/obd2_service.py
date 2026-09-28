"""L880K向けOBD2/K-Line車両情報サービス。

デフォルトの設定はL880K固有の通信方式が未確認のため停止します。実車試験で
初期化手順・要求・応答形式を確認した後、プロファイルのstatus/confirmedを更新して
初めて車体への送信を許可します。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from obd2.capabilities import CapabilityProbe
from obd2.config import ProfileError, load_profile
from obd2.connection import AdapterConnection, AdapterError
from obd2.decoder import DecodeError, decode_response
from obd2.kline_session import KLineError, KLineSession
from obd2.models import ServiceState
from obd2.ipc import Obd2EventServer, default_socket_path
from obd2.publisher import Publisher
from obd2.scheduler import PollScheduler
from obd2.state_cache import VehicleStateCache

LOGGER = logging.getLogger("obd2_service")


class Obd2Service:
    """OBD2のライフサイクルを管理する常駐可能なサービス。"""

    def __init__(
        self,
        config_path: str | Path,
        *,
        emit: Callable[[dict[str, Any]], None] | None = None,
        socket_path: str | None = None,
    ) -> None:
        """K-Lineプロファイル、通信アダプタ、取得周期、IPCを組み立てる。"""
        self.config_path = Path(config_path)
        self.profile = load_profile(self.config_path)
        self.stop_event = threading.Event()
        self._mode_lock = threading.Lock()
        self._requested_mode = "minimal"
        self.socket_path = socket_path or os.environ.get("L880K_OBD_SOCKET", default_socket_path())
        self._external_emit = emit
        self.ipc = Obd2EventServer(self.socket_path, self._handle_ipc_request)
        self.adapter = AdapterConnection(
            port=self.profile.port,
            baudrate=self.profile.host_baudrate,
            identity=self.profile.serial_identity,
        )
        self.session = KLineSession(self.adapter, self.profile)
        self.capabilities = CapabilityProbe(self.session, self.profile)
        self.scheduler = PollScheduler(self.profile.requests, self.capabilities)
        self.cache = VehicleStateCache()
        self.publisher = Publisher(self._emit_event)
        self._started = False

    @staticmethod
    def _emit_stdout(message: dict[str, Any]) -> None:
        """標準出力へ1件のJSONイベントを即時出力する。"""
        print(json.dumps(message, ensure_ascii=False, separators=(",", ":")), flush=True)

    def _emit_event(self, message: dict[str, Any]) -> None:
        """外部出力とOBD2 IPCへ同じイベントを配信する。"""
        if self._external_emit is not None:
            self._external_emit(message)
        else:
            self._emit_stdout(message)
        self.ipc.publish(message)

    def _handle_ipc_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """UIからのminimal/full取得モード変更要求を検証して受け付ける。"""
        operation = request.get("operation")
        command_id = request.get("command_id")
        if operation != "set_polling_mode":
            return {"event": "command_result", "command_id": command_id, "success": False, "reason": "未対応のOBD2操作です"}
        mode = str(request.get("arguments", {}).get("mode", "")).lower()
        if mode not in {"minimal", "full"}:
            return {"event": "command_result", "command_id": command_id, "success": False, "reason": "取得モードが不正です"}
        with self._mode_lock:
            self._requested_mode = mode
        return {"event": "command_result", "command_id": command_id, "success": True, "state": "QUEUED", "mode": mode}

    def request_stop(self) -> None:
        """ポーリングループへ安全な停止を通知する。"""
        self.stop_event.set()

    def _publish_invalid(self, reason: str) -> None:
        """キャッシュ中の車両値を無効化し、無効化イベントを発行する。"""
        for cached in self.cache.invalidate_all(reason):
            self.publisher.publish_invalid(cached)

    def run(self, *, once: bool = False) -> int:
        """サービスを実行する。戻り値はCLI終了コード。"""

        try:
            self.ipc.start()
            if not self.profile.is_usable:
                reason = "L880K通信プロファイルが未確認のため、車体への送信を停止しました"
                self.publisher.publish_state(ServiceState.DISCONNECTED, reason=reason)
                LOGGER.warning(reason)
                return 2
            self._started = True
            self.publisher.publish_state(ServiceState.CONNECTING)
            self.adapter.connect_adapter()
            self.publisher.publish_state(ServiceState.INITIALIZING)
            if not self.session.initialize_kline():
                self._publish_invalid("K-Line初期化失敗")
                self.publisher.publish_state(ServiceState.RETRY_WAIT, reason="K-Line初期化失敗")
                return 3
            self.publisher.publish_state(ServiceState.PROBING)
            self.capabilities.probe_capabilities()
            self.publisher.publish_capabilities(self.capabilities.results)
            self.publisher.publish_state(ServiceState.POLLING)
            return self._poll_loop(once=once)
        except AdapterError as exc:
            self._publish_invalid("USBアダプタ異常")
            self.publisher.publish_state(ServiceState.DEGRADED, reason=str(exc))
            LOGGER.exception("USBアダプタ異常")
            return 4
        finally:
            self.stop_obd()
            self.ipc.stop()

    def _poll_loop(self, *, once: bool) -> int:
        """期限を迎えた車両要求を順番に送り、測定値をキャッシュとUIへ配信する。"""
        polled = False
        while not self.stop_event.is_set():
            self._apply_requested_mode()
            expired = self.cache.expire_values()
            for cached in expired:
                self.publisher.publish_invalid(cached)
            request = self.scheduler.select_next_query()
            if request is None:
                if once:
                    return 0
                if once and polled:
                    return 0
                time.sleep(0.05)
                continue
            polled = True
            try:
                frame = self.session.send_query(request.request_id)
                sample = decode_response(frame, request, self.profile)
            except (AdapterError, KLineError, DecodeError) as exc:
                self.scheduler.mark_result(request, success=False)
                self.publisher.publish_state(ServiceState.DEGRADED, reason=f"{request.key}: {exc}")
                if once:
                    return 5
            else:
                self.scheduler.mark_result(request, success=True)
                self.cache.apply_sample(sample)
                self.publisher.publish_sample(sample)
            if once and polled:
                return 0
        return 0

    def _apply_requested_mode(self) -> None:
        """UIから要求された取得モードを適用し、詳細値の不要な保持を止める。"""
        with self._mode_lock:
            requested = self._requested_mode
        if requested == self.scheduler.active_mode:
            return
        self.scheduler.set_mode(requested)
        if requested == "full":
            self.publisher.publish_state(ServiceState.PROBING, reason="車両情報画面向けの詳細項目を確認中")
            self.capabilities.probe_capabilities("full")
            self.publisher.publish_capabilities(self.capabilities.results)
            self.publisher.publish_state(ServiceState.POLLING)
        else:
            detailed_keys = {request.key for request in self.profile.requests if request.poll_modes == ("full",)}
            for cached in self.cache.invalidate_keys(detailed_keys, "車両情報画面を閉じたため詳細値を停止"):
                self.publisher.publish_invalid(cached)
            self.publisher.publish_state(ServiceState.POLLING, reason="必要最小限の項目を取得中")

    def stop_obd(self) -> None:
        """K-Line、アダプタ、キャッシュを停止順序に従って解放する。"""
        if not self._started and not self.adapter.is_open:
            return
        self.publisher.publish_state(ServiceState.STOPPING)
        self._publish_invalid("サービス終了")
        self.session.invalidate()
        self.adapter.stop_obd()
        self.publisher.publish_state(ServiceState.DISCONNECTED)
        self._started = False


def _build_parser() -> argparse.ArgumentParser:
    """OBD2サービスのコマンドライン引数定義を作る。"""
    parser = argparse.ArgumentParser(description="L880K OBD2/K-Line車両情報サービス")
    parser.add_argument("--config", required=True, help="OBD2プロファイルTOML")
    parser.add_argument("--dry-run", action="store_true", help="設定検査だけ実行し、車体へ接続しない")
    parser.add_argument("--once", action="store_true", help="各実行で1回だけ読み取り、終了する")
    parser.add_argument("--socket", help="OBD2専用Unixソケット。未指定時は環境変数または既定値")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def main(argv: list[str] | None = None) -> int:
    """設定検査またはOBD2サービス実行をCLIから開始する。"""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s")
    try:
        profile = load_profile(args.config)
    except (OSError, ProfileError) as exc:
        print(f"設定エラー: {exc}")
        return 2
    print(
        json.dumps(
            {
                "profile_id": profile.profile_id,
                "profile_version": profile.version,
                "status": profile.status,
                "confirmed": profile.confirmed,
                "request_count": len(profile.requests),
                "init_step_count": len(profile.init_steps),
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )
    if args.dry_run:
        if profile.is_usable:
            print("設定検査: 実車送信可能なプロファイルです。実車接続は行っていません。")
        else:
            print("設定検査: 未確認プロファイルです。実車送信は行いません。")
        return 0
    service = Obd2Service(args.config, socket_path=args.socket)
    try:
        return service.run(once=args.once)
    except KeyboardInterrupt:
        service.request_stop()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
