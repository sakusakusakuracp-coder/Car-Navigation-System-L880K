"""ナビ操作を順番に実行する非同期要求ディスパッチャー。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Any, Callable

from .supervisor import NavigationSupervisor


class AsyncCommandDispatcher:
    """UIからの要求を受け付け、実処理をUIと別スレッドで実行する。"""

    def __init__(self, supervisor: NavigationSupervisor, event_publisher: Callable[[dict[str, Any]], None]) -> None:
        """設定と内部状態を初期化する。"""
        self.supervisor = supervisor
        self.publish = event_publisher
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="navigation-command")
        self._active: set[str] = set()
        self._lock = Lock()
        self._closed = False

    def submit(self, command_id: str, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """要求を受け付け、実処理を待たずに受付結果を返す。"""
        with self._lock:
            if self._closed:
                return {"accepted": False, "command_id": command_id, "reason": "管理プログラムは終了処理中です"}
            if command_id in self._active:
                return {"accepted": True, "command_id": command_id, "duplicate": True, "state": "RUNNING"}
            self._active.add(command_id)
        future = self.executor.submit(self._run, command_id, operation, arguments)
        future.add_done_callback(lambda completed: self._finish(command_id, completed))
        return {"accepted": True, "command_id": command_id, "state": "QUEUED"}

    def close(self) -> None:
        """新規要求を止め、実行中の要求を完了させてから終了する。"""
        with self._lock:
            self._closed = True
        self.executor.shutdown(wait=True, cancel_futures=False)

    def _run(self, command_id: str, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """要求を専用ワーカーで実行し、UIへ返す結果を整形する。"""
        result = self.supervisor.handle_request(command_id, operation, arguments)
        return {"event": "command_result", "command_id": command_id, **result}

    def _finish(self, command_id: str, future: Future[dict[str, Any]]) -> None:
        """完了した要求を追跡集合から外し、結果または例外を通知する。"""
        with self._lock:
            self._active.discard(command_id)
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 - 常駐プロセスからUIへ失敗を通知する
            result = {"event": "command_result", "command_id": command_id, "accepted": False, "success": False, "reason": str(exc)}
        self.publish(result)
