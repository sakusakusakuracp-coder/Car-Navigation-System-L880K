#!/usr/bin/env python3
"""Waydroid/OsmAndナビ管理ソフトの起動入口。"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

from navigation.json_ipc import JsonLineServer
from navigation.async_dispatcher import AsyncCommandDispatcher
from navigation.supervisor import NavigationSupervisor
from navigation.resident_runtime import ProcessLock


def load_config(path: str | None) -> dict[str, Any]:
    """JSONとして読める設定ファイルを検証して読み込む。"""
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("設定の最上位はオブジェクトで指定してください")
    return data


def main(argv: list[str] | None = None) -> int:
    """引数を解釈して処理を開始する。"""
    parser = argparse.ArgumentParser(description="Waydroid/OsmAnd navigation supervisor")
    parser.add_argument("--config", help="JSON形式の設定ファイル")
    parser.add_argument("--socket", help="JSON Lines通信用Unixドメインソケット")
    parser.add_argument("--stdin", action="store_true", help="開発用に標準入力から1行JSONを受け付ける")
    args = parser.parse_args(argv)
    server: JsonLineServer | None = None
    dispatcher: AsyncCommandDispatcher | None = None
    process_lock: ProcessLock | None = None
    try:
        config = load_config(args.config)
        if args.stdin:
            supervisor = NavigationSupervisor(config, lambda status: print(json.dumps({"event": "status", **status}, ensure_ascii=False), flush=True))
            for line in sys.stdin:
                if not line.strip():
                    continue
                request = json.loads(line)
                result = supervisor.handle_request(str(request["command_id"]), str(request["operation"]), dict(request.get("arguments", {})))
                print(json.dumps(result, ensure_ascii=False), flush=True)
            return 0

        socket_path = args.socket or config.get("ipc", {}).get("socket_path") or _default_socket_path()
        lock_path = config.get("ipc", {}).get("lock_path") or _default_lock_path()
        process_lock = ProcessLock(str(lock_path))
        process_lock.acquire()
        holder: dict[str, JsonLineServer] = {}

        def publish(status: dict[str, Any]) -> None:
            """状態またはイベントを購読者へ通知する。"""
            current = holder.get("server")
            if current is not None:
                current.publish({"event": "status", **status})

        supervisor = NavigationSupervisor(config, publish)
        dispatcher = AsyncCommandDispatcher(supervisor, publish)

        def handle_request(request: dict[str, Any]) -> dict[str, Any]:
            """handle_requestの内部処理を実行する。"""
            command_id = request.get("command_id")
            operation = request.get("operation")
            if not isinstance(command_id, str) or not isinstance(operation, str):
                return {"accepted": False, "reason": "command_idとoperationは文字列で指定してください"}
            arguments = request.get("arguments", {})
            if not isinstance(arguments, dict):
                return {"accepted": False, "reason": "argumentsはJSONオブジェクトで指定してください"}
            return dispatcher.submit(command_id, operation, arguments)

        server = JsonLineServer(str(socket_path), handle_request, int(config.get("ipc", {}).get("max_line_bytes", 65_536)))
        holder["server"] = server
        signal.signal(signal.SIGINT, lambda _signum, _frame: server.stop())
        signal.signal(signal.SIGTERM, lambda _signum, _frame: server.stop())
        server.serve_forever()
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"accepted": False, "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if dispatcher is not None:
            dispatcher.close()
        if process_lock is not None:
            process_lock.release()
    return 0


def _default_socket_path() -> str:
    """利用者専用の実行時ディレクトリを優先してソケット位置を決める。"""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return str(Path(runtime_dir) / "l880k-navigation.sock")
    uid = getattr(os, "getuid", lambda: "user")()
    return str(Path("/tmp") / f"l880k-navigation-{uid}.sock")


def _default_lock_path() -> str:
    """ソケットと同じ利用者専用領域へ排他ロックを置く。"""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return str(Path(runtime_dir) / "l880k-navigation.lock")
    uid = getattr(os, "getuid", lambda: "user")()
    return str(Path("/tmp") / f"l880k-navigation-{uid}.lock")


if __name__ == "__main__":
    raise SystemExit(main())
