#!/usr/bin/env python3
"""LIVIネイティブ連携サービスの起動入口。"""

from __future__ import annotations

import argparse
import json
import os
import signal
from pathlib import Path
from typing import Any

from livi.service import LiviIntegration
from navigation.json_ipc import JsonLineServer
from navigation.resident_runtime import ProcessLock


def load_config(path: str | None) -> dict[str, Any]:
    """LIVI設定ファイルをJSONオブジェクトとして読み込む。"""
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("LIVI設定の最上位はオブジェクトで指定してください")
    return data


def main(argv: list[str] | None = None) -> int:
    """LIVIサービスをJSON Lines Unixソケットで常駐させる。"""
    parser = argparse.ArgumentParser(description="LIVI native integration service")
    parser.add_argument("--config", help="JSON形式のLIVI設定")
    parser.add_argument("--socket", help="LIVI JSON Lines Unixソケット")
    args = parser.parse_args(argv)
    server: JsonLineServer | None = None
    lock: ProcessLock | None = None
    integration: LiviIntegration | None = None
    try:
        config = load_config(args.config)
        ipc = config.get("ipc", {})
        socket_path = args.socket or ipc.get("socket_path") or _default_socket_path()
        lock_path = ipc.get("lock_path") or _default_lock_path()
        lock = ProcessLock(str(lock_path))
        lock.acquire()
        holder: dict[str, JsonLineServer] = {}

        def publish(event: dict[str, Any]) -> None:
            """接続中のUIへLIVI状態を通知する。"""
            if holder.get("server") is not None:
                holder["server"].publish(event)

        integration = LiviIntegration(config, publish)

        def handle(request: dict[str, Any]) -> dict[str, Any]:
            """受信JSONからcommand_id、operation、argumentsを取り出して処理する。"""
            command_id = request.get("command_id")
            operation = request.get("operation")
            arguments = request.get("arguments", {})
            if not isinstance(command_id, str) or not isinstance(operation, str):
                return {"accepted": False, "reason": "command_idとoperationは文字列で指定してください"}
            if not isinstance(arguments, dict):
                return {"accepted": False, "reason": "argumentsはJSONオブジェクトで指定してください"}
            return integration.handle_request(command_id, operation, arguments)

        server = JsonLineServer(str(socket_path), handle, int(ipc.get("max_line_bytes", 65_536)))
        holder["server"] = server
        signal.signal(signal.SIGINT, lambda _signum, _frame: server.stop())
        signal.signal(signal.SIGTERM, lambda _signum, _frame: server.stop())
        server.serve_forever()
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"accepted": False, "reason": str(exc)}, ensure_ascii=False))
        return 2
    finally:
        if integration is not None:
            integration.close()
        if lock is not None:
            lock.release()
    return 0


def _default_socket_path() -> str:
    """利用者単位のLIVIソケット既定値を返す。"""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return str(Path(runtime_dir) / "l880k-livi.sock")
    uid = getattr(os, "getuid", lambda: "user")()
    return f"/tmp/l880k-livi-{uid}.sock"


def _default_lock_path() -> str:
    """利用者単位のLIVIロック既定値を返す。"""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return str(Path(runtime_dir) / "l880k-livi.lock")
    uid = getattr(os, "getuid", lambda: "user")()
    return f"/tmp/l880k-livi-{uid}.lock"


if __name__ == "__main__":
    raise SystemExit(main())
