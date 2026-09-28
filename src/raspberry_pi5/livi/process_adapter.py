"""LIVIネイティブプロセスの起動・停止を担当する。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class NativeProcessAdapter:
    """設定されたLIVI実行ファイルを所有し、起動状態を返す。"""

    def __init__(self, config: dict[str, Any]) -> None:
        """実行ファイル、引数、ログ先を検証せずに保持する。検証は起動時に行う。"""
        self.config = config
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle = None

    @property
    def pid(self) -> int | None:
        """起動中のLIVIプロセスIDを返す。"""
        if self._process is None or self._process.poll() is not None:
            return None
        return self._process.pid

    def start(self, timeout: float = 1.0) -> dict[str, Any]:
        """LIVIを起動し、直後に終了していないことを確認する。"""
        if self.pid is not None:
            return {"success": True, "state": "RUNNING", "pid": self.pid, "reason": "LIVIは起動済みです"}
        executable = self.config.get("executable")
        if not isinstance(executable, str) or not executable:
            return {"success": False, "state": "FAILED", "reason": "LIVI実行ファイルが未設定です"}
        if (os.path.isabs(executable) and not Path(executable).exists()) or (not os.path.isabs(executable) and shutil.which(executable) is None):
            return {"success": False, "state": "FAILED", "reason": f"LIVI実行ファイルが見つかりません: {executable}"}
        args = self.config.get("args", [])
        if not isinstance(args, list) or any(not isinstance(item, str) or not item for item in args):
            return {"success": False, "state": "FAILED", "reason": "LIVI起動引数は文字列配列で指定してください"}
        working_directory = self.config.get("working_directory")
        if working_directory is not None and not isinstance(working_directory, str):
            return {"success": False, "state": "FAILED", "reason": "LIVI作業ディレクトリが不正です"}
        log_path = self.config.get("command_log")
        try:
            if log_path:
                path = Path(str(log_path))
                path.parent.mkdir(parents=True, exist_ok=True)
                self._log_handle = path.open("ab")
            self._process = subprocess.Popen(
                [executable, *args],
                cwd=working_directory or None,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle or subprocess.DEVNULL,
                stderr=self._log_handle or subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            self._close_log()
            return {"success": False, "state": "FAILED", "reason": f"LIVIを起動できません: {exc}"}
        if timeout > 0:
            import time

            time.sleep(min(timeout, 0.2))
        return_code = self._process.poll()
        if return_code is not None:
            self._close_log()
            self._process = None
            return {"success": False, "state": "FAILED", "reason": f"LIVIが起動直後に終了しました: {return_code}"}
        return {"success": True, "state": "RUNNING", "pid": self._process.pid, "reason": "LIVIを起動しました"}

    def stop(self, timeout: float = 2.0) -> dict[str, Any]:
        """所有しているLIVIだけを終了させ、終了状態を確認する。"""
        process = self._process
        if process is None or process.poll() is not None:
            self._process = None
            self._close_log()
            return {"success": True, "state": "STOPPED", "reason": "LIVIは停止済みです"}
        process.terminate()
        try:
            process.wait(timeout=max(0.1, timeout))
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._close_log()
                return {"success": False, "state": "UNKNOWN", "reason": "LIVIの停止状態を確認できません"}
        finally:
            self._process = None
            self._close_log()
        return {"success": True, "state": "STOPPED", "reason": "LIVIを停止しました"}

    def is_running(self) -> bool:
        """LIVIプロセスが終了していないかを返す。"""
        return self.pid is not None

    def _close_log(self) -> None:
        """LIVI標準出力ログのファイルを閉じる。"""
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
