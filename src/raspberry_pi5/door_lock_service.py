"""Raspberry Pi 5ドアロック制御の常駐エントリポイント。"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import time
from pathlib import Path

from door_lock.driver import DryRunOutputDriver, LgpioOutputDriver
from door_lock.service import DoorLockConfig, DoorLockService
from door_lock.store import CommandStore
from telemetry_ipc import JsonLinesEventServer, runtime_socket_path

LOGGER = logging.getLogger("l880k.door-lock")


def main(argv: list[str] | None = None) -> int:
    """引数を解釈して処理を開始する。"""
    parser = argparse.ArgumentParser(description="L880K door lock control service")
    parser.add_argument("--config", required=True)
    parser.add_argument("--socket", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true", help="起動確認だけ行って終了する")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    service: DoorLockService | None = None
    server: JsonLinesEventServer | None = None
    stop_requested = False

    def request_stop(_signum, _frame) -> None:
        """停止要求を主処理へ通知する。"""
        nonlocal stop_requested
        stop_requested = True
    try:
        data = json.loads(Path(args.config).read_text(encoding="utf-8"))
        config = DoorLockConfig.from_mapping(data)
        if args.dry_run:
            driver = DryRunOutputDriver()
        else:
            if not config.enabled:
                LOGGER.info("ドアロック制御は無効です。GPIOを取得しません")
                return 0
            driver = LgpioOutputDriver(int(config.lock_gpio), int(config.unlock_gpio), int(config.active_level), int(config.release_level))
        service = DoorLockService(config, driver, CommandStore(str(data.get("result_store", "/var/lib/l880k/door-lock-results.json"))))
        server = None if args.dry_run else JsonLinesEventServer(args.socket or runtime_socket_path("l880k-door-lock.sock"), service.handle_request)
        if server:
            server.start()
        LOGGER.info("ドアロックサービスを開始しました: state=%s", service.state)
        if args.once:
            return 0
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        while not stop_requested:
            time.sleep(1.0)
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        LOGGER.error("ドアロックサービスを開始できません: %s", exc)
        return 2
    finally:
        if service:
            service.stop()
        if server:
            server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
