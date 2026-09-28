"""Raspberry Pi 3B サブコントローラ1（後方カメラ・バックギア）サービス。"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from sub1.rear_camera import RearCameraService
from sub1.reverse_signal import ReverseSignalMonitor
from sub1.state_api import Sub1Api
from subcontroller_common import DryRunCameraDriver, DryRunInputDriver, JsonLinePublisher, LgpioInputDriver, Picamera2JpegDriver

LOGGER = logging.getLogger("l880k.sub1")


def load_config(path: str) -> dict:
    """load_configの内部処理を実行する。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run(config: dict, *, dry_run: bool, once: bool) -> int:
    """主処理を実行して終了まで監視する。"""
    if not dry_run and config.get("configuration_state") != "confirmed":
        raise RuntimeError("サブコン1のGPIO・極性・通信設定が未確定です。実機起動にはconfiguration_state=confirmedが必要です")
    peer = config["peer"]
    reverse_cfg = config["reverse_signal"]
    camera_cfg = config["camera"]
    input_driver = DryRunInputDriver() if dry_run else LgpioInputDriver([int(reverse_cfg["gpio"])])
    camera_driver = DryRunCameraDriver() if dry_run else Picamera2JpegDriver(int(camera_cfg["width"]), int(camera_cfg["height"]), int(camera_cfg["fps"]))
    publisher = JsonLinePublisher(str(peer["host"]), int(peer["port"]), "sub1")
    monitor = ReverseSignalMonitor(input_driver, int(reverse_cfg["gpio"]), int(reverse_cfg["active_level"]), int(reverse_cfg["poll_ms"]), int(reverse_cfg["debounce_ms"]), int(reverse_cfg["max_read_gap_ms"]))
    camera = RearCameraService(camera_driver, int(camera_cfg["width"]), int(camera_cfg["height"]), int(camera_cfg["fps"]), int(camera_cfg["max_frame_bytes"]))
    api = Sub1Api(publisher)
    try:
        camera.start()
        interval = 1.0 / max(1, int(camera_cfg["fps"]))
        while True:
            reverse = monitor.read()
            freshness = monitor.check_freshness()
            if freshness:
                reverse = freshness
            frame = camera.capture_and_send()
            api.publish_state(reverse, {"camera_state": frame.get("camera_state", "UNKNOWN")}, "RUNNING")
            api.publish_frame(frame)
            if once:
                return 0
            time.sleep(interval)
    finally:
        camera.stop()
        input_driver.close()
        publisher.close()


def main(argv: list[str] | None = None) -> int:
    """引数を解釈して処理を開始する。"""
    parser = argparse.ArgumentParser(description="L880K sub-controller 1")
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    try:
        return run(load_config(args.config), dry_run=args.dry_run, once=args.once)
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        LOGGER.error("サブコン1を開始できません: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
