"""Google Drive送信サービスと、認証・確定録画登録の管理コマンド。"""

from __future__ import annotations

import argparse
import json
import logging
import math
import signal
import sqlite3
import stat
import subprocess
import threading
import time
from pathlib import Path

from drive_upload.catalog_reader import CatalogReader
from drive_upload.config import UploadConfig, UploadError
from drive_upload.google_drive import GoogleDriveClient, SCOPES, authorized_session, save_credentials
from drive_upload.local_files import relative_parts
from drive_upload.network_policy import NetworkPolicy, check_google_url
from drive_upload.recovery import RecoveryWorker, retry_blocked
from navigation.resident_runtime import ProcessLock
from telemetry_ipc import JsonLinesEventServer, runtime_socket_path

LOGGER = logging.getLogger("l880k.drive-upload")


def inspect_closed_video(root: Path, relative_path: str) -> None:
    """手動登録時にffprobeで動画ストリームと長さを検査する。書込完了の保証は録画側が持つ。"""
    relative_parts(relative_path)
    target = root / relative_path
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise UploadError("INVALID_RECORDING_PATH")
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
                                 "-of", "json", str(target.resolve())], capture_output=True, text=True, timeout=30, check=True)
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0 or not any(s.get("codec_type") == "video" for s in data.get("streams", [])):
            raise ValueError()
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        raise UploadError("CLOSED_VIDEO_VALIDATION_FAILED") from exc


def run_service(config: UploadConfig, catalog: CatalogReader, args) -> int:
    """単一起動を確認して常駐し、SIGTERMで次の送信単位から終了する。"""
    lock = ProcessLock(str(catalog.path) + ".uploader.lock")
    lock.acquire()
    stop = threading.Event()
    client = server = socket_lock = None
    try:
        if args.dry_run:
            row = catalog.next_item(config.auto_delete_enabled)
            print(json.dumps({"dry_run": True, "next_file_id": row["file_id"] if row else None,
                              "network_access": False, "deletion": False, **catalog.status()}, ensure_ascii=False))
            return 0
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda _signum, _frame: stop.set())
        if config.enabled:
            catalog.bind_destination(config.account_ref, config.folder_id)
        catalog.recover_pending()
        policy = NetworkPolicy(config, catalog, stop)
        if config.enabled:
            client = GoogleDriveClient(authorized_session(config.credential_path, policy.before_request, config.io_timeout_s), config.io_timeout_s)
        snapshot = {}
        snapshot_time = 0.0
        snapshot_lock = threading.Lock()

        def publish(event):
            """状態またはイベントを購読者へ通知する。"""
            nonlocal snapshot_time
            with snapshot_lock:
                snapshot.clear()
                snapshot.update(event)
                snapshot_time = time.monotonic()

        def on_request(request):
            """状態照会と一時停止だけを受け付け、送信・削除の許可設定は変更しない。"""
            if isinstance(request, dict) and request.get("operation") in {"pause", "resume"}:
                catalog.set_paused(request["operation"] == "pause")
                return {"accepted": True, "paused": catalog.is_paused(), "enabled": config.enabled}
            if not isinstance(request, dict) or request.get("operation") not in {"status", "GET_STATE", "GET_HEALTH"}:
                return {"accepted": False, "reason": "UNSUPPORTED_OPERATION"}
            with snapshot_lock:
                return {**snapshot, "age_ms": int((time.monotonic() - snapshot_time) * 1000)} if snapshot else None

        worker = RecoveryWorker(config, catalog, client, policy, publish)
        if not args.once:
            socket_path = args.socket or runtime_socket_path("l880k-drive-upload.sock")
            socket_lock = ProcessLock(socket_path + ".lock")
            socket_lock.acquire()
            path = Path(socket_path)
            if path.is_symlink() or (path.exists() and not stat.S_ISSOCK(path.lstat().st_mode)):
                raise UploadError("INVALID_STATUS_SOCKET_PATH")
            server = JsonLinesEventServer(socket_path, on_request)
            server.start()
        LOGGER.info("アップロードサービス開始: enabled=%s auto_delete=%s", config.enabled, config.auto_delete_enabled)
        while not stop.is_set():
            event = worker.tick()
            LOGGER.info("state=%s reason=%s counts=%s", event["state"], event["reason"], event["counts"])
            if args.once:
                print(json.dumps(event, ensure_ascii=False))
                return 1 if event["state"] == "BLOCKED" else 0
            stop.wait(config.poll_interval_s)
        return 0
    finally:
        if server is not None:
            server.stop()
        if client is not None:
            client.close()
        if socket_lock is not None:
            socket_lock.release()
        lock.release()


def main(argv: list[str] | None = None) -> int:
    """引数を解釈して処理を開始する。"""
    parser = argparse.ArgumentParser(description="L880K Google Drive録画アップロード")
    parser.add_argument("--config", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="常駐サービスを起動")
    run.add_argument("--once", action="store_true")
    run.add_argument("--dry-run", action="store_true", help="ネットワーク送信・削除・状態更新なしの台帳確認")
    run.add_argument("--socket")
    register = commands.add_parser("register", help="close済みの動画1件を台帳へ登録")
    register.add_argument("--closed-file", required=True, help="録画ルートからの相対パス")
    register.add_argument("--camera", required=True, choices=["front", "rear", "left", "right", "test"])
    register.add_argument("--file-id")
    for command in ("status", "pause", "resume"):
        commands.add_parser(command)
    retry = commands.add_parser("retry", help="原因解消後に1件のBLOCKEDを再試行")
    retry.add_argument("--file-id", required=True)
    auth = commands.add_parser("authorize", help="ブラウザでGoogle OAuth初回認証")
    auth.add_argument("--client-secrets", required=True)
    auth.add_argument("--port", type=int, default=8765)
    auth.add_argument("--no-browser", action="store_true")
    folder = commands.add_parser("create-folder", help="アプリ専用Driveフォルダを作成して設定値を出力")
    folder.add_argument("--name", default="L880K Recordings")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = UploadConfig.load(args.config)
        if args.command == "authorize":
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, SCOPES)
            credentials = flow.run_local_server(host="localhost", port=args.port, open_browser=not args.no_browser,
                timeout_seconds=300, authorization_prompt_message="Google認証URL: {url}", success_message="認証が完了しました。この画面を閉じてください。")
            save_credentials(config.credential_path, credentials)
            print("Google認証情報を設定先へ保存しました。")
            return 0
        if args.command == "create-folder":
            client = GoogleDriveClient(authorized_session(config.credential_path, check_google_url, config.io_timeout_s), config.io_timeout_s)
            try:
                account = client.account_id()
                folder_id = client.create_folder(args.name)
                print(json.dumps({"account_ref": account, "folder_id": folder_id}, ensure_ascii=False, indent=2))
            finally:
                client.close()
            return 0
        catalog = CatalogReader(config.catalog_path, config.recording_root)
        if args.command == "run":
            return run_service(config, catalog, args)
        if args.command == "register":
            inspect_closed_video(config.recording_root, args.closed_file)
            file_id = catalog.register_ready(args.closed_file, args.camera, args.file_id)
            print(json.dumps({"file_id": file_id, "state": "READY"}))
        elif args.command == "status":
            print(json.dumps(catalog.status(), ensure_ascii=False, indent=2))
        elif args.command in {"pause", "resume"}:
            catalog.set_paused(args.command == "pause")
            print("アップロードを一時停止しました。" if args.command == "pause" else "アップロードの一時停止を解除しました。")
        elif args.command == "retry":
            lock = ProcessLock(str(catalog.path) + ".uploader.lock")
            lock.acquire()
            try:
                retry_blocked(catalog, args.file_id)
            finally:
                lock.release()
            print("再試行待ちに変更しました。")
        return 0
    except UploadError as exc:
        LOGGER.error("処理を停止しました: %s", exc.reason)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error, ImportError) as exc:
        # HTTP例外や資格情報を含む詳細はそのまま表示しない。
        LOGGER.error("設定・依存関係・台帳を確認してください: %s", type(exc).__name__)
    except Exception as exc:
        # OAuthライブラリの想定外例外にも認証URL等が含まれ得るため、詳細は出力しない。
        LOGGER.error("処理を停止しました。認証と設定を確認してください: %s", type(exc).__name__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
