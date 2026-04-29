#!/usr/bin/env python3
"""scripts/restart_dashboard.py — Issue #52: ライブダッシュボードを明示的に再起動する

`hooks/launch_dashboard.py` は **idempotent な spawn 経路** で「既起動なら何もしない」
設計のため、`/plugin update` で `dashboard/template.html` が更新されても既存プロセスが
古い HTML をメモリに保持し続け、UI 変更が反映されない問題があった (Issue #52)。

このスクリプトは **明示的な再起動経路** として:

1. server.json から pid を読む
2. alive なら SIGTERM で graceful shutdown を依頼し、shutdown 完了を待つ
3. POSIX で graceful timeout を超えても生きていたら SIGKILL で強制終了
4. server.json を **compare-and-delete** で idempotent にクリーンアップ
5. `hooks/launch_dashboard.py` を直叩きして新 spawn を発生させる
   (launcher 経由なので server.json の atomic write / lock は再利用される)

`launch_dashboard.py` (silent fail) と違い、こちらは **明示的な手動操作**なので
状態を stderr に 1 行ずつ短く出力する。

Exit code:
- 0: 正常完了 (kill → spawn or no-server → spawn)
- 1: 既存プロセスを止められなかった (PermissionError / timeout 後も生存) — 安全側で
     新 spawn を行わずに終了 (二重起動防止)

stdlib only。テスト隔離は `DASHBOARD_SERVER_JSON` env で `SERVER_JSON_PATH` を上書き。
"""
# pylint: disable=broad-except
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# `_is_pid_alive` (POSIX/Windows 両対応) と server_registry を再利用する。
# launch_dashboard.py は import 時に stdin を読まないので import しても副作用ゼロ。
from hooks import launch_dashboard as _launcher  # noqa: E402  pylint: disable=wrong-import-position
from server_registry import remove_server_json as _remove_server_json  # noqa: E402  pylint: disable=wrong-import-position

# server.json のパス (テスト用に env で差し替え可能 / launch_dashboard と同じ規約)
_DEFAULT_SERVER_JSON_PATH = Path.home() / ".claude" / "transcript-analyzer" / "server.json"
SERVER_JSON_PATH = Path(
    os.environ.get("DASHBOARD_SERVER_JSON", str(_DEFAULT_SERVER_JSON_PATH))
)

# graceful shutdown を待つ最大秒数。`server.py` の SIGTERM ハンドラは即座に
# `serve_forever` を抜けるので通常 < 1s で死ぬが、SSE 接続が切れるのを待つ余裕で 5s。
GRACEFUL_TIMEOUT_SECONDS = 5.0
# SIGKILL は OS レベルで即時終了するので graceful timeout を使い回す必要なし。
# 0.5s で十分 (codex / claude[bot] review #53)。
KILL_TIMEOUT_SECONDS = 0.5
_POLL_INTERVAL_SECONDS = 0.05

# spawn 後に launcher の子プロセスが server.json を書くまでの待ち時間。
# launcher の `_wait_for_self_server_json_url` の budget (0.25s) よりも余裕を持つ。
# launcher の systemMessage 経路は restart 時には機能しない (input=b"{}" で
# hook_event_name 不明 → silent return / capture_output で読み捨て) ため、
# restart_dashboard 側で server.json から URL を読んで stderr に出力する
# (PR #53 review 対応)。
SPAWN_URL_WAIT_TIMEOUT_SECONDS = 2.0
_SPAWN_URL_POLL_INTERVAL_SECONDS = 0.05

_LAUNCHER_PATH = _PROJECT_ROOT / "hooks" / "launch_dashboard.py"


def _read_pid_from_server_json() -> Optional[int]:
    """server.json から pid (int) を取り出す。不在 / 壊れた JSON / 型不正 → None。"""
    info = _launcher._read_server_json(SERVER_JSON_PATH)
    if info is None:
        return None
    pid = info.get("pid")
    if not isinstance(pid, int):
        return None
    return pid


def _is_pid_alive(pid: int) -> bool:
    """POSIX/Windows 両対応の pid 生存確認。launch_dashboard と同じ実装を再利用。"""
    return _launcher._is_pid_alive(pid)


def _wait_for_pid_exit(pid: int, timeout: float) -> bool:
    """pid が timeout 秒以内に死ねば True、間に合わなければ False。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_pid_alive(pid):
            return True
        time.sleep(_POLL_INTERVAL_SECONDS)
    # 最後に 1 度確認 (sleep 直後に死んだケースを拾う)
    return not _is_pid_alive(pid)


def _send_signal(pid: int, sig) -> bool:
    """`os.kill(pid, sig)` を best-effort で呼ぶ。返り値: kill request が通ったか。

    - ProcessLookupError → 既に死んでる → True (kill 不要だった)
    - PermissionError → 他ユーザー pid 等 → False
    - その他 OSError → False (silent best-effort)
    """
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return False


def _terminate_existing_server() -> bool:
    """既存 dashboard プロセスを止める。返り値: 「止まった or もともと無い」かどうか。

    - server.json 不在 → True (kill 不要)
    - pid 死亡 → server.json をクリーンアップして True
    - alive → SIGTERM → graceful 待ち
        - 期限内に死亡 → True
        - 死なない → POSIX なら SIGKILL fallback、その後再度待つ
    - kill PermissionError → False (二重起動防止のため呼び出し側で諦める)
    """
    pid = _read_pid_from_server_json()
    if pid is None:
        return True
    if not _is_pid_alive(pid):
        # zombie server.json をクリーンアップ (compare-and-delete)
        _remove_server_json(SERVER_JSON_PATH, expected_pid=pid)
        return True

    sys.stderr.write(f"[restart] sending SIGTERM to dashboard pid={pid}\n")
    if not _send_signal(pid, signal.SIGTERM):
        sys.stderr.write(f"[restart] cannot signal pid={pid} (permission denied?)\n")
        return False

    if _wait_for_pid_exit(pid, GRACEFUL_TIMEOUT_SECONDS):
        # SIGTERM ハンドラが server.json を消すはずだが、念のため compare-and-delete で
        # 残骸を片付ける (idempotent: 不在なら何もしない)
        _remove_server_json(SERVER_JSON_PATH, expected_pid=pid)
        return True

    # SIGTERM で死ななかった → POSIX なら SIGKILL fallback
    if sys.platform != "win32":
        sys.stderr.write(
            f"[restart] SIGTERM timeout, escalating to SIGKILL pid={pid}\n"
        )
        if not _send_signal(pid, signal.SIGKILL):
            return False
        # SIGKILL は即時終了なので短い timeout で十分 (review #53)
        if _wait_for_pid_exit(pid, KILL_TIMEOUT_SECONDS):
            _remove_server_json(SERVER_JSON_PATH, expected_pid=pid)
            return True
        return False

    # Windows で SIGTERM 相当 (TerminateProcess) が効かなかった → 諦め
    sys.stderr.write(f"[restart] could not terminate pid={pid} on Windows\n")
    return False


def _wait_for_url(timeout: float = SPAWN_URL_WAIT_TIMEOUT_SECONDS) -> Optional[str]:
    """spawn 後に server.json から URL を読む。timeout 内に出現しなければ None。

    launcher の `_wait_for_self_server_json_url` と違い、ここでは self_pid の
    一致確認はしない (restart 経路では launcher が spawn した子の pid は
    別プロセス越しで把握できないため)。代わりに server.json が出現した時点で
    最新の URL を採用する (terminate 後は古い json を消してから launcher を
    呼んでいるので、出現する json は新 spawn 由来と見做せる)。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = _launcher._read_server_json(SERVER_JSON_PATH)
        if info is not None:
            url = info.get("url")
            if isinstance(url, str) and url:
                return url
        time.sleep(_SPAWN_URL_POLL_INTERVAL_SECONDS)
    return None


def _run_launcher() -> int:
    """`hooks/launch_dashboard.py` を subprocess で叩いて新 spawn を発生させる。

    launcher は idempotent で既起動チェックをするため、`_terminate_existing_server`
    が成功した後に呼ぶと **必ず spawn が走る** (server.json は既に消えているため)。

    launcher は stdin から hook payload を読もうとするので、空の stdin (`{}`) を
    渡す。launcher は `hook_event_name` を取れず silent path に入るため
    systemMessage URL emit はされない。restart は明示的な手動操作なので、
    launcher 戻り後に `server.json` を直接読んで URL を **stderr に出力する**
    (PR #53 review 対応 / docs と挙動を整合)。

    `RESTART_DASHBOARD_DRYRUN=1` のときは spawn を抑止してテスト用にだけ exit 0 する。
    """
    if os.environ.get("RESTART_DASHBOARD_DRYRUN"):
        return 0
    try:
        proc = subprocess.run(
            [sys.executable, str(_LAUNCHER_PATH)],
            input=b"{}",
            capture_output=True,
            timeout=10,
            check=False,
        )
        # launcher の stdout は systemMessage JSON か空。stderr は基本空。
        # 失敗時に診断できるよう exit code != 0 のみ stderr へ流す。
        if proc.returncode != 0:
            sys.stderr.write(
                f"[restart] launcher exit={proc.returncode} stderr={proc.stderr!r}\n"
            )
            return proc.returncode
        # launcher 成功 → 新 spawn 子が server.json を書くのを待って URL を出力。
        # 子の起動 race window で server.json が遅れて出てくることがあるので poll。
        url = _wait_for_url()
        if url:
            sys.stderr.write(f"[restart] dashboard available at {url}\n")
        # URL 不明でも exit 0 (spawn 自体は launcher 経由で発生済み / docs にも
        # `cat ~/.claude/transcript-analyzer/server.json` のフォールバック手順あり)
        return proc.returncode
    except subprocess.TimeoutExpired:
        sys.stderr.write("[restart] launcher timeout\n")
        return 1
    except OSError as exc:
        sys.stderr.write(f"[restart] launcher OSError: {exc}\n")
        return 1


def main() -> int:
    """エントリポイント。0=成功 / 1=失敗。"""
    if not _terminate_existing_server():
        sys.stderr.write(
            "[restart] failed to stop existing dashboard; aborting respawn "
            "to avoid double-instance\n"
        )
        return 1
    return _run_launcher()


if __name__ == "__main__":
    sys.exit(main())
