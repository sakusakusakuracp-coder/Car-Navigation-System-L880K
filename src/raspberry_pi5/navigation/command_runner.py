"""固定された外部コマンドを期限付きで実行する。"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
import shlex
from pathlib import Path
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


class CommandRunner:
    """shell=Trueを使わず、引数配列だけを実行する。"""

    def __init__(self, max_output: int = 32_768, log_path: str | None = None) -> None:
        """外部コマンドの出力上限と監査ログ先を設定する。"""
        self.max_output = max_output
        self.log_path = log_path

    def run(self, argv: list[str], timeout: float) -> CommandResult:
        """引数配列のコマンドを期限付きで実行し、結果を統一形式で返す。"""
        if not argv or any(not isinstance(item, str) or not item for item in argv):
            raise ValueError("外部コマンドは空でない文字列配列で指定してください")
        try:
            completed = subprocess.run(
                argv,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            result = CommandResult(
                completed.returncode,
                completed.stdout[: self.max_output],
                completed.stderr[: self.max_output],
            )
            self._log(argv, result)
            return result
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(None, str(exc.stdout or "")[: self.max_output], str(exc.stderr or "")[: self.max_output], True)
            self._log(argv, result)
            return result
        except OSError as exc:
            result = CommandResult(None, "", str(exc), False)
            self._log(argv, result)
            return result

    def _log(self, argv: list[str], result: CommandResult) -> None:
        """コマンド、終了状態、標準出力・標準エラーを診断ログへ追記する。"""
        if not self.log_path:
            return
        try:
            path = Path(self.log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            with path.open("a", encoding="utf-8") as stream:
                stream.write(f"[{timestamp}] command: {shlex.join(argv)}\n")
                stream.write(f"[{timestamp}] returncode: {result.returncode} timed_out: {result.timed_out}\n")
                if result.stdout:
                    stream.write(f"[{timestamp}] stdout: {result.stdout.rstrip()}\n")
                if result.stderr:
                    stream.write(f"[{timestamp}] stderr: {result.stderr.rstrip()}\n")
        except OSError:
            pass
