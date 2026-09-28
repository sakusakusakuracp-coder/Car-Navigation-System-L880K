"""カメラ表示・録画サービスの常駐起動と、終了要求の受付。"""

import argparse
import logging
from pathlib import Path
import signal
import threading

from camera.config import CameraConfig
from camera.service import CameraService
from navigation.resident_runtime import ProcessLock
from telemetry_ipc import JsonLinesEventServer, runtime_socket_path


def main(argv=None):
    """設定と依存ライブラリを検査し、単一起動で入力・録画・IPCを開始する。"""
    parser = argparse.ArgumentParser(description="L880K camera capture and recording")
    parser.add_argument("--config", required=True)
    parser.add_argument("--socket", default=runtime_socket_path("l880k-camera.sock"))
    parser.add_argument("--check", action="store_true", help="設定と依存のみ確認し、カメラは開かない")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    service, server = None, None
    lock = None
    exit_code = 0
    try:
        config = CameraConfig.load(args.config)
        import av
        from PIL import Image

        if args.check:
            print("カメラ設定・PyAV・JPEG処理を確認しました。CSI・通信・保存先は実起動時に確認します。")
            return 0
        if not config.recording_root.is_dir():
            raise ValueError("録画先ディレクトリを作成し、SSDのマウントを確認してください")
        lock = ProcessLock(str(config.catalog_path) + ".camera.lock")
        lock.acquire()
        service = CameraService(config)
        server = JsonLinesEventServer(args.socket, service.request)
        stopped = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        server.start()
        service.start()
        while not stopped.wait(0.5):
            pass
    except (ValueError, RuntimeError, OSError, ImportError, TypeError, KeyError):
        logging.exception("カメラサービスの起動または運用に失敗しました")
        exit_code = 2
    finally:
        if server:
            server.stop()
        if service and not service.close():
            logging.error("録画の確定失敗または終了期限超過です。未確定ファイルは次回起動で確認します。")
            exit_code = 3
        if lock:
            lock.release()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
