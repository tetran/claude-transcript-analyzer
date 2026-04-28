"""hooks/_launcher_common.py

launch_dashboard.py / launch_archive.py の **fork-and-detach 経路の共通化**
(Issue #30 Phase C / plan-reviewer Proposal 1)。

OS 別 detach の細かい知見 (Windows DETACHED_PROCESS のフラグ値、Win64 で
ctypes の HANDLE 幅問題、POSIX `start_new_session` の必要性) を 1 箇所に
集約することで、launcher を増やしても detach 経路の再実装を silent に
壊さないようにする (Issue #24 PR#31 codex P1 のような知見が自動的に効く)。

API:
- spawn_detached(args, *, stdin, stdout, stderr) -> Optional[subprocess.Popen]
"""
from __future__ import annotations

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
