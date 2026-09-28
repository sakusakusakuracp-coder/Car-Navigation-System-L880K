"""設定と履歴の競合を防ぐ、プロセス終了時に自動解放されるロック。"""

import os
from pathlib import Path


class FileLock:
    def __init__(self, path):
        """保存先とは別の固定ファイルで置換保存中も排他を維持する。"""
        self.path, self.handle = Path(path), None

    def __enter__(self):
        """待ち続けず取得し、競合時は既存の処理とファイルを変更しない。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if self.path.stat().st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("同じ設定を使用するUIまたは保存処理が既に動作しています") from exc
        self.handle = handle
        return self

    def __exit__(self, *args):
        """ハンドルを閉じてOSのロックを解放する。ロックファイルは削除しない。"""
        if self.handle:
            self.handle.close()
            self.handle = None
