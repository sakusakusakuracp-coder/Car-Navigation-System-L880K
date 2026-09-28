"""録画領域外や差し替えられたファイルを読み出し・削除しないための検査。"""

from __future__ import annotations

import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from drive_upload.config import UploadError


def identity(info: os.stat_result) -> dict:
    """登録時と同じファイルであるかを比較する識別値を返す。"""
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}


def relative_parts(relative: str) -> tuple[str, ...]:
    """台帳の相対パスを検査する。異なるOSの区切りや親参照も拒否する。"""
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or any(p in ("", ".", "..") for p in relative.split("/")):
        raise UploadError("INVALID_RECORDING_PATH")
    if "\\" in relative or ":" in relative or "\0" in relative:
        raise UploadError("INVALID_RECORDING_PATH")
    if path.suffix.lower() not in {".mp4", ".mkv"}:
        raise UploadError("NOT_FINAL_RECORDING")
    return path.parts


@contextmanager
def parent_directory(root: Path, relative: str, *, deleting: bool = False):
    """LinuxではディレクトリFDをたどり、リンクを経由せず親を保持する。"""
    parts = relative_parts(relative)
    if root.is_symlink() or not root.is_dir():
        raise UploadError("INVALID_RECORDING_ROOT")
    if deleting and os.name != "posix":
        raise UploadError("DELETE_REQUIRES_LINUX")
    handles = []
    try:
        current = root
        if os.name == "posix":
            handles.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            for part in parts[:-1]:
                handles.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=handles[-1]))
            if deleting:
                for fd in handles:
                    info = os.fstat(fd)
                    if info.st_uid != os.getuid() or info.st_mode & 0o022:
                        raise UploadError("UNTRUSTED_RECORDING_DIRECTORY")
            yield handles[-1], parts[-1]
        else:
            for part in parts[:-1]:
                current = current / part
                if current.is_symlink() or current.is_junction() or not current.is_dir():
                    raise UploadError("INVALID_RECORDING_DIRECTORY")
            yield None, str(current / parts[-1])
    except OSError as exc:
        raise UploadError("LOCAL_FILE_UNAVAILABLE") from exc
    finally:
        for fd in reversed(handles):
            os.close(fd)


@contextmanager
def open_recording(root: Path, relative: str, expected: dict | None = None):
    """通常ファイルのみ開く。ファイルを開いた後にも識別値を検査する。"""
    with parent_directory(root, relative) as (parent_fd, name):
        if parent_fd is None and Path(name).is_symlink():
            raise UploadError("LINK_NOT_ALLOWED")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(name, flags, dir_fd=parent_fd)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size <= 0:
                raise UploadError("NOT_SINGLE_REGULAR_FILE")
            if expected is not None and identity(info) != expected:
                raise UploadError("LOCAL_IDENTITY_CHANGED")
            yield handle


def checksum(handle, stop=None) -> str:
    """一定量ずつMD5を計算し、計算中に変わったファイルは確定扱いしない。"""
    before = identity(os.fstat(handle.fileno()))
    digest = hashlib.md5(usedforsecurity=False)
    handle.seek(0)
    while True:
        if stop is not None:
            stop()
        block = handle.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
    if before != identity(os.fstat(handle.fileno())):
        raise UploadError("LOCAL_FILE_CHANGED_DURING_READ")
    handle.seek(0)
    return digest.hexdigest()
