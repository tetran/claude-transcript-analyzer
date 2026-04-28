"""hooks/_lock.py

POSIX/Windows cross-platform advisory file lock 抽象化 (Issue #44)。

retention/archive 機構 (Issue #30) は元々 POSIX `fcntl.flock` 限定で、Windows では
archive 機能が無効化されていた。このモジュールで lock layer を抽象化することで
全 OS で archive が動くようにする。

差異の吸収:
- POSIX (`fcntl.flock`): SH/EX が区別される。`LOCK_NB` flag で blocking/non-blocking。
- Windows (`msvcrt.locking`): SH 概念無し → SH も EX として実装 (concurrency 落ちる
  が acceptable)。`LK_LOCK` は 1 秒×10 回 retry の擬似 blocking で 10 秒粘ってから
  OSError、`LK_NBLCK` は即 OSError、`LK_UNLCK` で解除。

両 module が import 不能な特殊環境では **no-op degrade** (lock なしで進む)。

設計上の制約:
- Lock target は **先頭 1 byte** の慣用法。`msvcrt.locking` は byte range lock
  なので nbytes 引数が必須、POSIX `flock` はファイル全体を対象にするので無視されるが
  API は統一する。
- caller は `os.open(path, O_RDWR | O_CREAT)` で fd を取得すること。`open(..., "a")`
  の TextIOWrapper は Windows の `msvcrt.locking` と相性が悪い。本モジュールの
  `open_lock_file` を経由すれば自動で正しい fd が取れる。

API:
- 低レベル: `open_lock_file` / `acquire_shared` / `acquire_exclusive` / `release`
- 高レベル: `shared_lock` / `exclusive_lock` (context manager)
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Iterator

try:
    import fcntl  # type: ignore[import]
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover (Windows のみ)
    fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False

try:
    import msvcrt  # type: ignore[import]
    _HAS_MSVCRT = True
except ImportError:  # pragma: no cover (POSIX のみ)
    msvcrt = None  # type: ignore[assignment]
    _HAS_MSVCRT = False


# 先頭 1 byte をロック対象にする慣用法 (msvcrt.locking が byte range lock のため)
_LOCK_NBYTES = 1


def open_lock_file(path: Path) -> int:
    """lock 用 fd を確保 (`O_RDWR | O_CREAT`)。

    parent dir は `mkdir(parents=True, exist_ok=True)` で作成する。
    fd の close 責務は **caller** が持つ (`os.close(fd)` を finally で)。

    Windows の `msvcrt.locking` は binary mode の fd を要求するため `os.open`
    経由で取得する (`open(path, "a")` の TextIOWrapper では不可)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)


def acquire_shared(fd: int, *, blocking: bool = True) -> None:
    """SH lock を取得する。

    Windows では SH 概念が無いため EX 相当に degrade する (concurrency は落ちるが
    correctness は保たれる)。

    blocking=True で取得不能な場合:
    - POSIX: indefinite に待つ
    - Windows: `LK_LOCK` の 1 秒 × 10 回 retry 後に OSError
    blocking=False で取得不能な場合: 即 `OSError` (POSIX は `BlockingIOError`)。
    """
    if _HAS_FCNTL:
        flag = fcntl.LOCK_SH | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(fd, flag)
    elif _HAS_MSVCRT:
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        msvcrt.locking(fd, mode, _LOCK_NBYTES)
    # else: no-op degrade (両 module 不在環境)


def acquire_exclusive(fd: int, *, blocking: bool = True) -> None:
    """EX lock を取得する。`acquire_shared` と同じ blocking semantics。"""
    if _HAS_FCNTL:
        flag = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(fd, flag)
    elif _HAS_MSVCRT:
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        msvcrt.locking(fd, mode, _LOCK_NBYTES)
    # else: no-op degrade (両 module 不在環境)


def release(fd: int) -> None:
    """lock を release する。

    既に解除済み / 二重 release 等の `OSError` は silent swallow する。
    caller の finally 内で例外を上げないようにするため。
    """
    if _HAS_FCNTL:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
    elif _HAS_MSVCRT:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, _LOCK_NBYTES)
        except OSError:
            pass


@contextlib.contextmanager
def shared_lock(path: Path, *, blocking: bool = True) -> Iterator[None]:
    """SH lock を取得して yield する context manager。

    `with shared_lock(path):` で critical section を囲む典型用途向け。
    fd の open/close と acquire/release の対称性を一括管理する。
    """
    fd = open_lock_file(path)
    try:
        acquire_shared(fd, blocking=blocking)
        try:
            yield
        finally:
            release(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def exclusive_lock(path: Path, *, blocking: bool = True) -> Iterator[None]:
    """EX lock を取得して yield する context manager。"""
    fd = open_lock_file(path)
    try:
        acquire_exclusive(fd, blocking=blocking)
        try:
            yield
        finally:
            release(fd)
    finally:
        os.close(fd)
