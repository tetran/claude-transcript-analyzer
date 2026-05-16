"""analyzer/platform/process.py — プロセス起動・生存確認の OS 別 seam (Issue #121)。

launch_dashboard.py / launch_archive.py / restart_dashboard.py が共有する
**fork-and-detach 経路と pid 生存確認の OS 分岐を 1 箇所に集約** する。

OS 別の細かい知見 (Windows DETACHED_PROCESS のフラグ値、Win64 で ctypes の
HANDLE 幅問題、POSIX `start_new_session` の必要性、`os.kill(pid, 0)` の errno
解釈) をこのモジュールに閉じ込めることで、launcher を増やしても detach /
pid-alive 経路の再実装を silent に壊さないようにする
(Issue #30 Phase C / Issue #24 PR#31 codex P1)。

API:
- spawn_detached(args, *, stdin, stdout, stderr) -> Optional[subprocess.Popen]
- is_pid_alive(pid) -> bool
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import IO, Optional, Union

# Windows fork-and-detach 用の CreateProcess flags (subprocess.* は Win 限定属性)。
# POSIX で AttributeError を避けるため getattr fallback でハードコード値を採用。
# DETACHED_PROCESS=0x8 / CREATE_NEW_PROCESS_GROUP=0x200 (MSDN: process creation flags)
_WIN_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_WIN_CREATE_NEW_PROCESS_GROUP = getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
)

_StreamSpec = Union[int, IO]


def spawn_detached(
    args: list[str],
    *,
    stdin: _StreamSpec = subprocess.DEVNULL,
    stdout: _StreamSpec = subprocess.DEVNULL,
    stderr: _StreamSpec = subprocess.DEVNULL,
) -> Optional[subprocess.Popen]:
    """fork-and-detach で subprocess を起動。

    OS 別 detach 経路:
    - POSIX: ``start_new_session=True`` で親 PG/SID から切り離し、Claude Code
      終了後も子プロセスが生存する (`os.setsid` 相当)。
    - Windows: ``creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP`` で
      同等の切り離し (``start_new_session`` は Win では no-op で親終了時に
      子も死ぬため不可)。

    共通:
    - stdin/stdout/stderr は DEVNULL (親 hook の pipe を引き継がない)
    - ``close_fds=True`` で余計な fd を継承しない

    呼び出し失敗 (Popen が ``OSError``) は **silent fail** として ``None`` を返す。
    呼び出し側は ``proc is None`` で最終分岐 (URL 通知・log 出力など) を skip する。
    """
    kwargs: dict = {
        "stdin": stdin,
        "stdout": stdout,
        "stderr": stderr,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            _WIN_DETACHED_PROCESS | _WIN_CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(args, **kwargs)  # pylint: disable=consider-using-with
    except OSError:
        return None


# ----------------------------------------------------------------------------
# pid 生存確認 (POSIX: os.kill(pid, 0) / Windows: kernel32 OpenProcess)
# ----------------------------------------------------------------------------

_WIN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WIN_STILL_ACTIVE = 259

# kernel32 の bound symbol キャッシュ。初回 `_win_kernel32()` 呼び出しで signature 設定 +
# キャッシュ、以降の pid-alive 経路はキャッシュ参照のみで < 100ms budget を維持。
_WIN_KERNEL32 = None


def _win_kernel32():
    """Windows kernel32 を取得し ctypes signatures を明示する (Issue #24 PR#31 codex P1)。

    Win64 で `OpenProcess` の戻り値 HANDLE はポインタ幅 (64bit) だが、ctypes default の
    `restype=c_int` (32bit signed) のままだと高位ビットが立った handle で truncate +
    sign-extend が起き、`GetExitCodeProcess` / `CloseHandle` に誤った handle を渡す
    可能性がある。`wintypes.HANDLE` で明示すれば正しく扱える。bound symbol を一度だけ
    設定してキャッシュするので毎回 lookup が走らず launcher の起動 budget も安全。
    """
    global _WIN_KERNEL32  # pylint: disable=global-statement
    if _WIN_KERNEL32 is not None:
        return _WIN_KERNEL32
    import ctypes  # pylint: disable=import-outside-toplevel
    from ctypes import wintypes  # pylint: disable=import-outside-toplevel
    k = ctypes.windll.kernel32  # type: ignore[attr-defined]
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.GetExitCodeProcess.restype = wintypes.BOOL
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    _WIN_KERNEL32 = k
    return _WIN_KERNEL32


def _is_pid_alive_posix(pid: int) -> bool:
    """POSIX: `os.kill(pid, 0)` で存在確認。ESRCH → False、EPERM → True。"""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # 他ユーザの pid が偶然 collision したケース。「別プロセスが存在する」の意味で True
        return True
    except OSError:
        return False


def _is_pid_alive_windows(pid: int) -> bool:
    """Windows: kernel32!OpenProcess + GetExitCodeProcess で alive 判定。

    OpenProcess が NULL → 不在 or アクセス不可 → False。
    GetExitCodeProcess の exit_code が STILL_ACTIVE (259) → 生存中 → True。
    """
    import ctypes  # pylint: disable=import-outside-toplevel
    from ctypes import wintypes  # pylint: disable=import-outside-toplevel
    kernel32 = _win_kernel32()
    handle = kernel32.OpenProcess(
        _WIN_PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        return bool(ok) and exit_code.value == _WIN_STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def is_pid_alive(pid: int) -> bool:
    """OS 別 dispatch。テストは sys.platform mock で各分岐を検証可能。"""
    if sys.platform == "win32":
        try:
            return _is_pid_alive_windows(pid)
        except OSError:
            # ctypes 呼び出しが落ちた場合の保険。多重起動回避を優先して False
            return False
    return _is_pid_alive_posix(pid)
