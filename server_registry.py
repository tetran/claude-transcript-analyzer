"""server_registry.py — server.json の atomic IO + 排他ロック。

`dashboard/server.py` の本体ランタイムと `hooks/launch_dashboard.py` の cleanup
パスの両方から共有される。Issue #24 の TOCTOU race 解消は **両者が同じ
lock + compare-and-delete を経由する** ことで初めて成立するため、共有
モジュールに切り出している (Issue #24 / PR #31 codex P2 対応)。

依存は stdlib のみ。`launch_dashboard.py` の < 100ms 起動 budget を壊さない
よう、`http.server` / `socket` 等の重い import を持ち込まないこと。
"""
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


# ---- cross-platform 排他ロック ---------------------------------------
# `server.json` 自体を lock 対象にすると Windows の `msvcrt.locking` が unlink と
# 相性が悪い (sharing violation) ため、別ファイル `<server.json>.lock` を経由する。

if sys.platform == "win32":
    import msvcrt  # pylint: disable=import-error

    def _lock_fd(fd: int) -> None:
        # LK_NBLCK: 取得失敗時は ~1秒×10回リトライ (LK_LOCK) せず即 OSError。
        # `launch_dashboard.py` の < 100ms exit budget を Win 競合時にも維持するため
        # (Issue #24 PR#31 claude[bot] review #1)。`_file_lock` の yield False 経路で
        # 呼び出し側が安全側に倒す責務を持つ設計と整合する。
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock_fd(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _file_lock(lock_path: Path) -> Iterator[bool]:
    """別ファイル lock を経由する cross-platform 排他ロック。

    `yield acquired:bool` パターンで取得成否を呼び出し側に伝える。lock 取得に
    失敗 (OSError) したときは silent best-effort で続行せず False を yield し、
    呼び出し側が安全側 (削除/書き込みを諦める) に倒す責務を持つ。
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT)
    try:
        try:
            _lock_fd(fd)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            try:
                _unlock_fd(fd)
            except OSError:
                pass
    finally:
        os.close(fd)


def _lock_path_for(target: Path) -> Path:
    """`<target>.lock` を返す。target の親 dir に lock ファイルを置く。"""
    return target.with_name(target.name + ".lock")


def _pid_matches(path: Path, expected_pid: int) -> bool:
    """`server.json` の `pid` が `expected_pid` と一致するか。
    不在 / OSError / JSON 不正 / pid 不一致はすべて False。
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return False
    try:
        info = json.loads(content)
    except json.JSONDecodeError:
        return False
    return info.get("pid") == expected_pid


def write_server_json(path: Path, info: dict) -> None:
    """`{pid, port, url, started_at}` を atomic に書く（tmp + os.replace）。

    Issue #24: `_file_lock` で remove と逐次化し TOCTOU race を解消する。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(_lock_path_for(path)):
        # lock 取得成否に関わらず write は実行する: write は atomic (os.replace) で
        # 単独で正しく動くため lock 失敗時の安全側退避は不要。lock があれば
        # remove との逐次化が成立し、無くても自プロセス内では write 同士の race も無い
        # (run() は単一プロセスから 1 度だけ呼ばれる)。
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)


def remove_server_json(path: Path, expected_pid: Optional[int] = None) -> bool:
    """server.json をべき等に削除する。返り値は実際に削除したか。

    `expected_pid` を渡したときは compare-and-delete: ファイル中の `pid` が一致する場合
    のみ削除する。多重インスタンスで他プロセスが上書きしたレジストリを誤って消さないため。
    壊れた JSON / 不在ファイル / pid 不一致はいずれも `False` を返して安全側に倒す。

    Issue #24: read → pid 比較 → unlink を `_file_lock` で 1 critical section に
    閉じ込めて TOCTOU race を解消。lock 取得失敗時は削除を諦めて False を返す
    (silent best-effort で削除に踏み切ると lock の意味が無くなる)。
    """
    path = Path(path)
    with _file_lock(_lock_path_for(path)) as acquired:
        if expected_pid is not None:
            # compare-and-delete モードで lock が取れない = 他プロセスが
            # 書き込み/削除中の可能性。安全側に倒して何もしない。
            if not acquired or not _pid_matches(path, expected_pid):
                return False
        try:
            path.unlink()
            return True
        except OSError:
            # FileNotFoundError / PermissionError / read-only fs 等。run() の finally から
            # 例外が漏れるとプロセス終了時のエラーログを汚すため、cleanup は best-effort 扱い。
            return False
