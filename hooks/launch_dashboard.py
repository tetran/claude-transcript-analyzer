"""hooks/launch_dashboard.py — Issue #21 Phase C: ダッシュボードの自動起動 launcher

Claude Code Hook (SessionStart / UserPromptSubmit / PostToolUse) から呼ばれ、
`dashboard/server.py` が既に動いていれば何もせず、未起動なら fork-and-detach で
起動する **べき等な薄い launcher**。

判定フロー:
  1. server.json を読む
  2. pid 生存 → **多重起動を発生させないため True 返却** で何もせず exit 0
     (起動中 race window: `write_server_json()` 直後 `serve_forever()` 開始前に hook が
     来ても healthz のリトライで吸収。リトライ後も healthz fail でも pid alive なら
     True を返す)
  3. server.json 不在 / 壊れた JSON / pid 死亡 → ゾンビ pid file を削除し、
     `subprocess.Popen([python, server.py], start_new_session=True)` で fork-and-detach 起動

設計上の不変条件:
- どんな例外が起きても **silent に exit 0**（Claude Code をブロックしない）
- 既起動検出経路 (healthy server fast path) は **< 100ms**（毎 hook 走るため）
  - alive + healthz 即時 200 のケースのみ。pid alive + healthz が不応答 (3 回
    リトライしてもタイムアウト) の稀ケースでは最大 ~700ms (= 200ms × 3 + 50ms × 2)
    に伸びる。多重起動回避優先のトレードオフで意図的に許容
- healthz timeout は **200ms**、リトライ回数 3 / 間隔 50ms（起動中 race window 吸収）
- start_new_session=True で親 PG/SID から切り離し、Claude Code 終了後も子は生存
- stdin/stdout/stderr は DEVNULL でリダイレクトし親 hook の pipe を引き継がない
- `_server_is_alive()` は **ピュア** (副作用なし)。zombie cleanup は
  `_remove_stale_server_json()` に分離し `main()` で明示呼び出し

stdin から JSON が流れてくる (Claude Code Hook 標準) が、launcher は中身を見ない。
"""
# pylint: disable=broad-except
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

_DEFAULT_SERVER_JSON_PATH = Path.home() / ".claude" / "transcript-analyzer" / "server.json"
SERVER_JSON_PATH = Path(os.environ.get("DASHBOARD_SERVER_JSON", str(_DEFAULT_SERVER_JSON_PATH)))

# Issue #14 AC: healthz チェックのタイムアウト 200ms
HEALTHZ_TIMEOUT_SECONDS = 0.2

# Codex F2 / claude[bot] #1 対応:
# pid alive のとき healthz が一時的に応答しない race window
# (write_server_json 後 serve_forever 開始前) を吸収するためのリトライ。
# fast path (健全サーバーで healthz 即時 200) は 1 回目で True 返却で < 100ms。
# 全リトライ枯渇する稀ケースは最大 ~700ms (timeout 200ms × 3 + sleep 50ms × 2)
# に伸びるが、多重起動回避を優先する設計上のトレードオフで許容
# (claude[bot] PR#27 review #2 対応: コメント正確化)。
HEALTHZ_RETRY_COUNT = 3
HEALTHZ_RETRY_INTERVAL_SECONDS = 0.05

# 起動対象スクリプト: hooks/ の隣の dashboard/server.py
_SERVER_SCRIPT = Path(__file__).resolve().parent.parent / "dashboard" / "server.py"


def _read_server_json(path: Path) -> Optional[dict]:
    """server.json を best-effort に読む。不在 / 壊れた JSON / 非 dict / OSError → None。"""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        info = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(info, dict):
        return None
    return info


# Issue #24: Windows では `os.kill(pid, 0)` が SystemError を上げる (Python issue 14480)。
# OpenProcess + GetExitCodeProcess で alive 判定する。STILL_ACTIVE=259 は MSDN
# `GetExitCodeProcess` ドキュメントで定数定義されている値 (確認済み)。
_WIN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WIN_STILL_ACTIVE = 259


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
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(
        _WIN_PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        return bool(ok) and exit_code.value == _WIN_STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _is_pid_alive(pid: int) -> bool:
    """OS 別 dispatch。テストは sys.platform mock で各分岐を検証可能。"""
    if sys.platform == "win32":
        try:
            return _is_pid_alive_windows(pid)
        except OSError:
            # ctypes 呼び出しが落ちた場合の保険。多重起動回避を優先して False
            return False
    return _is_pid_alive_posix(pid)


def _healthz_ok(url: str, timeout: float = HEALTHZ_TIMEOUT_SECONDS) -> bool:
    """`{url}/healthz` が 200 を返すか。接続失敗 / timeout / 非 200 → False。"""
    try:
        with urllib.request.urlopen(f"{url}/healthz", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _healthz_ok_with_retry(url: str) -> bool:
    """healthz を `HEALTHZ_RETRY_COUNT` 回までリトライ。1 回でも 200 なら True。"""
    for attempt in range(HEALTHZ_RETRY_COUNT):
        if _healthz_ok(url):
            return True
        if attempt < HEALTHZ_RETRY_COUNT - 1:
            time.sleep(HEALTHZ_RETRY_INTERVAL_SECONDS)
    return False


def _server_is_alive() -> bool:
    """server.json + pid + healthz の総合判定。**ピュア (副作用なし)**。

    Codex F2 / claude[bot] #1 対応: pid alive + healthz fail (リトライ後も) でも
    **True 返却** で多重起動を防ぐ。サーバー起動中 race window や一時的なデッドロックで
    `_spawn_server()` が呼ばれて 2 つ目のサーバーが立つことを避けるため。
    pid が完全停止 (デッドロック) している場合は ops 介入 (kill) で復旧する想定。
    """
    info = _read_server_json(SERVER_JSON_PATH)
    if info is None:
        return False
    pid = info.get("pid")
    url = info.get("url")
    if not isinstance(pid, int) or not isinstance(url, str) or not url:
        return False
    if not _is_pid_alive(pid):
        return False  # 副作用なし: 削除は _remove_stale_server_json() / main() で行う
    # pid alive 確定。healthz をリトライしてみるが、結果に関わらず True 返却
    # (race window 吸収 + 二重起動防止)。
    _healthz_ok_with_retry(url)
    return True


def _remove_stale_server_json() -> bool:
    """**dead pid** のときだけ server.json を削除する。返り値は実際に削除したか。

    `_server_is_alive` から副作用を分離した zombie cleanup (claude[bot] #5 対応)。
    `main()` から `_server_is_alive()` が False を返した直後に明示的に呼ばれる。

    - alive pid → 何もしない (False)
    - dead pid → 削除 (True)
    - 不在 / 壊れた JSON / 非 dict → 何もしない (False)。spawn 後の atomic replace に任せる
    """
    info = _read_server_json(SERVER_JSON_PATH)
    if info is None:
        return False
    pid = info.get("pid")
    if not isinstance(pid, int):
        return False
    if _is_pid_alive(pid):
        return False
    try:
        SERVER_JSON_PATH.unlink()
        return True
    except OSError:
        return False


# Windows fork-and-detach 用の CreateProcess flags (subprocess.* は Win 限定属性)。
# POSIX で AttributeError を避けるため getattr fallback でハードコード値を採用。
# DETACHED_PROCESS=0x8 / CREATE_NEW_PROCESS_GROUP=0x200 (MSDN: process creation flags)
_WIN_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_WIN_CREATE_NEW_PROCESS_GROUP = getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
)


def _spawn_server() -> None:
    """`dashboard/server.py` を fork-and-detach で起動。silent。

    OS 別 detach 経路 (Issue #24):
    - POSIX: `start_new_session=True` で親 PG/SID から切り離し、Claude Code 終了後も生存
    - Windows: `creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP` で同等の切り離し
      (`start_new_session` は Win では no-op で親終了時に子も死ぬため不可)

    共通:
    - `stdin/stdout/stderr=DEVNULL`: 親 hook の pipe を引き継がない
    - `close_fds=True`: 余計な fd を継承しない
    """
    if not _SERVER_SCRIPT.exists():
        return
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            _WIN_DETACHED_PROCESS | _WIN_CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, str(_SERVER_SCRIPT)],
            **kwargs,
        )
    except OSError:
        # PermissionError / FileNotFoundError / fork limit 等。silent fail。
        return


def main() -> int:
    """launcher のエントリポイント。常に 0 を返す（silent fail）。

    フロー:
        if _server_is_alive():     # ピュア判定 (副作用なし)
            return 0
        _remove_stale_server_json()  # 明示的な zombie cleanup
        _spawn_server()              # fork-and-detach
    """
    try:
        if _server_is_alive():
            return 0
        _remove_stale_server_json()
        _spawn_server()
    except Exception:
        # どんな想定外例外でも Claude Code をブロックしない (Issue #14 AC)
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
