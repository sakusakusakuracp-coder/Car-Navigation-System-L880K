"""Raspberry Pi 3B サブコントローラ2（前方カメラ・左右ウィンカー）サービス。"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from sub2.front_camera import FrontCameraService
from sub2.state_api import Sub2Api
from sub2.turn_signal import TurnSignalMonitor
from subcontroller_common import DryRunCameraDriver, DryRunInputDriver, JsonLinePublisher, LgpioInputDriver, Picamera2JpegDriver

LOGGER = logging.getLogger("l880k.sub2")


def load_config(path: str) -> dict:
    """load_configの内部処理を実行する。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run(config: dict, *, dry_run: bool, once: bool) -> int:
    """主処理を実行して終了まで監視する。"""
    if not dry_run and config.get("configuration_state") != "confirmed":
        raise RuntimeError("サブコン2のGPIO・極性・通信設定が未確定です。実機起動にはconfiguration_state=confirmedが必要です")
    peer = config["peer"]
    left_cfg = config["turn_signals"]["left"]
    right_cfg = config["turn_signals"]["right"]
    camera_cfg = config["camera"]
    gpios = [int(left_cfg["gpio"]), int(right_cfg["gpio"])]
    input_driver = DryRunInputDriver() if dry_run else LgpioInputDriver(gpios)
    camera_driver = DryRunCameraDriver() if dry_run else Picamera2JpegDriver(int(camera_cfg["width"]), int(camera_cfg["height"]), int(camera_cfg["fps"]))
    publisher = JsonLinePublisher(str(peer["host"]), int(peer["port"]), "sub2")
    kwargs = {"poll_ms": int(config["timing"]["poll_ms"]), "debounce_ms": int(config["timing"]["debounce_ms"]), "max_read_gap_ms": int(config["timing"]["max_read_gap_ms"]), "activity_hold_ms": int(config["timing"]["activity_hold_ms"]), "max_on_ms": int(config["timing"]["max_on_ms"])}
    left = TurnSignalMonitor(input_driver, gpios[0], "left", int(left_cfg["active_level"]), **kwargs)
    right = TurnSignalMonitor(input_driver, gpios[1], "right", int(right_cfg["active_level"]), **kwargs)
    camera = FrontCameraService(camera_driver, int(camera_cfg["width"]), int(camera_cfg["height"]), int(camera_cfg["fps"]), int(camera_cfg["max_frame_bytes"]))
    api = Sub2Api(publisher)
    try:
        camera.start()
        interval = 1.0 / max(1, int(camera_cfg["fps"]))
        while True:
            left_state = left.read()
            right_state = right.read()
            frame = camera.capture_and_send()
            api.publish_state(left_state, right_state, {"camera_state": frame.get("camera_state", "UNKNOWN")}, "RUNNING")
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
    parser = argparse.ArgumentParser(description="L880K sub-controller 2")
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    try:
        return run(load_config(args.config), dry_run=args.dry_run, once=args.once)
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        LOGGER.error("サブコン2を開始できません: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
