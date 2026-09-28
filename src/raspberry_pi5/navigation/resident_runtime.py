"""常駐プロセスの二重起動を防ぎ、PIDを記録する実行時管理。"""

from __future__ import annotations

import os
from pathlib import Path

if os.name == "posix":
    import fcntl
else:  # pragma: no cover - Raspberry Pi/Linuxではposix側を使用する
    import msvcrt
    fcntl = None


class ProcessLock:
    """ファイルロックで同一ユーザーの二重起動を防止する。"""

    def __init__(self, path: str) -> None:
        """設定と内部状態を初期化する。"""
        self.path = Path(path)
        self._file = None

    def acquire(self) -> None:
        """ロックを取得し、取得できなければ起動を拒否する。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        handle = self.path.open("r+b")
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError) as exc:
            handle.close()
            raise RuntimeError(f"管理プログラムは既に起動しています: {self.path}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n".encode("ascii"))
        handle.flush()
        self._file = handle

    def release(self) -> None:
        """ロックを解放する。ロックファイル自体は診断用に残す。"""
        if self._file is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            else:
                self._file.seek(0)
                try:
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    # Windowsではハンドル終了時にOSがロックを解放する場合がある。
                    pass
        finally:
            self._file.close()
            self._file = None
