#!/usr/bin/env python3
"""hooks/launch_dashboard.py — Issue #21 Phase C: ダッシュボードの自動起動 launcher

Claude Code Hook (SessionStart / UserPromptExpansion / UserPromptSubmit / PostToolUse) から
呼ばれ、`dashboard/server.py` が既に動いていれば何もせず、未起動なら fork-and-detach で
起動する **べき等な薄い launcher**。

判定フロー:
  1. server.json を読む
  2. pid 生存 → **多重起動を発生させないため True 返却** で何もせず exit 0
     (起動中 race window: `write_server_json()` 直後 `serve_forever()` 開始前に hook が
     来ても healthz のリトライで吸収。リトライ後も healthz fail でも pid alive なら
     True を返す)
  3. server.json 不在 / 壊れた JSON / pid 死亡 → ゾンビ pid file を削除し、
     `subprocess.Popen([python, server.py], start_new_session=True)` で fork-and-detach 起動

Issue #34: 起動 URL を **systemMessage で即時通知** する経路を追加。
  - **spawn 成功時** (4 hook いずれでも) → stdout に
    `{"systemMessage": "📊 Dashboard: <url>"}` を 1 行出力
  - **既起動 + hook_event_name=SessionStart** → 同じ 1 行を出力 (再表示ポリシー)
  - それ以外 (UserPromptExpansion / Submit / PostToolUse の alive 経路) は **silent**
    (毎ターン発火するため出すと会話を埋める)

設計上の不変条件:
- どんな例外が起きても **silent に exit 0**（Claude Code をブロックしない）
  - Issue #34 で「成功経路で hook output JSON を 1 行 stdout に書ける」と緩和。
    例外時 / 該当条件外は依然 silent
- 既起動検出経路 (healthy server fast path) は **< 100ms**（毎 hook 走るため）
  - alive + healthz 即時 200 のケースのみ。pid alive + healthz が不応答 (3 回
    リトライしてもタイムアウト) の稀ケースでは最大 ~700ms (= 200ms × 3 + 50ms × 2)
    に伸びる。多重起動回避優先のトレードオフで意図的に許容
- spawn 経路は budget < 300ms (= SPAWN_WAIT_TIMEOUT_SECONDS + slack)。spawn 自体が稀
  (新セッション or idle 復活時のみ) なので budget 違反 risk は小さい
- healthz timeout は **200ms**、リトライ回数 3 / 間隔 50ms（起動中 race window 吸収）
- start_new_session=True で親 PG/SID から切り離し、Claude Code 終了後も子は生存
- 子サーバーの stdin/stdout/stderr は DEVNULL でリダイレクトし親 hook の pipe を引き継がない
  (親 launcher の stdout は systemMessage 経路でのみ使用 / 例外時は silent)
- `_server_is_alive()` は **ピュア** (副作用なし)。zombie cleanup は
  `_remove_stale_server_json()` に分離し `main()` で明示呼び出し
- `_spawn_server()` は `Optional[Popen]` を返す。Popen 失敗時は None。main() は
  None のとき poll を呼ばずに silent return (Issue #34 Proposal 1: stale json 誤読防止)
- spawn 後の URL 取得は `_wait_for_self_server_json_url(self_pid)` で
  `info.pid == self_pid` 一致確認 (自分が spawn した子の json か検証)

stdin から hook input JSON が流れてくる (Claude Code Hook 標準)。
launcher は `hook_event_name` のみ参照し systemMessage の出力可否を判断する。
"""
# pylint: disable=broad-except
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# Issue #24 PR#31 codex P2: server.json の lock + compare-and-delete を server.py と
# 共有するため、`server_registry` 経由で `remove_server_json` を呼ぶ。launch_dashboard が
# プロジェクトルートを sys.path に持っていない経路でも動くよう明示的に追加 (Hook から
# 直接 `python <abs path>/launch_dashboard.py` で起動されるため)。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server_registry import remove_server_json as _registry_remove_server_json

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

# Issue #34: spawn 後 server.json 出現を待つ poll の budget。
# spawn 経路は alive 経路 (< 100ms) と違い毎 hook ではないので 250ms を許容。
# server.py は write_server_json() を serve_forever() 開始前に呼ぶため、通常は
# 100ms 以内に取得できる。タイムアウト時は silent fallback で次回 hook に委ねる。
SPAWN_WAIT_TIMEOUT_SECONDS = 0.25
SPAWN_WAIT_INTERVAL_SECONDS = 0.05

# Issue #34: 公式 docs (https://code.claude.com/docs/en/hooks.md) の hook 名 PascalCase。
# 未知値は silent path に倒れる (表記揺れ防御)。
_EXPECTED_HOOK_EVENTS = frozenset({
    "SessionStart",
    "UserPromptExpansion",
    "UserPromptSubmit",
    "PostToolUse",
})

# Issue #34 codex P1 対応: payload size 非依存の hook_event_name 抽出。
# `_read_hook_event_name` は alive 判定の hot path で呼ばれ、UserPromptSubmit /
# PostToolUse の payload は長いプロンプトや tool I/O を含むため、`json.loads` 全体
# parse は payload-size に比例した work となり <100ms budget が崩れる risk があった。
# 先頭 _HOOK_PEEK_BYTES だけ読んで regex で値だけ抽出することで O(payload-size 非依存)
# に変える。typical な hook payload で `hook_event_name` field は先頭近くにある。
# codex Finding A 対応: 4KB peek で見つからない場合は **fallback** で残り stdin を
# 読んで full `json.loads` する。これにより PostToolUse の `tool_response` が大きい
# 等で field が後ろに来るケースでも通知が出る (rare path のため payload-size 依存を許容)。
_HOOK_PEEK_BYTES = 4096
# JSON で `"hook_event_name": "<value>"` の形を非欲張りでマッチ。
# `[^"\\\n\r]` の文字クラスにより、JSON-escape された文字列値 (例 `\"...`)
# は誤マッチしない。
# 注 (codex Finding B): top-level の field 位置に anchor していないため、
# ネストオブジェクト内に literal `hook_event_name` キーがあると先のものに誤マッチ
# しうる。Claude Code 公式 hook schema にこのネストは無い想定だが、もし regex 経路
# で誤マッチしても fallback parse が起動するのは「regex match なし」のときのみで
# あり Finding B の完全防御にはならない点は許容 (攻撃面 narrow / 公式 schema にない)。
_HOOK_EVENT_RE = re.compile(
    r'"hook_event_name"\s*:\s*"([^"\\\n\r]{1,256})"'
)

# Issue #34: systemMessage の本文 prefix。視認性は実機で観察し必要なら調整。
_SYSTEM_MESSAGE_PREFIX = "📊 Dashboard: "

# Issue #34: opt-in debug — `DASHBOARD_DEBUG_HOOK_EVENT` truthy のとき hook_event_name の
# 実値を `DASHBOARD_DEBUG_HOOK_EVENT_PATH` (デフォルト
# ~/.claude/transcript-analyzer/hook_event_debug.jsonl) に append。
# env 未設定時は完全 no-op (本番経路に副作用ゼロ)。
_DEFAULT_DEBUG_LOG_PATH = Path.home() / ".claude" / "transcript-analyzer" / "hook_event_debug.jsonl"

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

# kernel32 の bound symbol キャッシュ。初回 `_win_kernel32()` 呼び出しで signature 設定 +
# キャッシュ、以降の launch_dashboard 経路はキャッシュ参照のみで < 100ms budget を維持。
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

    Issue #24 PR#31 codex P2: 削除は `server_registry.remove_server_json` 経由で
    `_file_lock` + compare-and-delete を経由する。事前 `_is_pid_alive` 判定後に
    他プロセスが新 server.json を上書きしても、pid 不一致で削除を skip するため
    安全。dashboard/server.py の write/remove と同じ lock を共有することで
    multi-hook race window で TOCTOU race が再発しない。
    """
    info = _read_server_json(SERVER_JSON_PATH)
    if info is None:
        return False
    pid = info.get("pid")
    if not isinstance(pid, int):
        return False
    if _is_pid_alive(pid):
        return False
    return _registry_remove_server_json(SERVER_JSON_PATH, expected_pid=pid)


# Windows fork-and-detach 用の CreateProcess flags (subprocess.* は Win 限定属性)。
# POSIX で AttributeError を避けるため getattr fallback でハードコード値を採用。
# DETACHED_PROCESS=0x8 / CREATE_NEW_PROCESS_GROUP=0x200 (MSDN: process creation flags)
_WIN_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_WIN_CREATE_NEW_PROCESS_GROUP = getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
)


def _spawn_server() -> Optional[subprocess.Popen]:
    """`dashboard/server.py` を fork-and-detach で起動。Popen を返す。失敗時 None。

    Issue #34 Proposal 1: 戻り値で `Popen` を返すことで、main() 側で `proc is None`
    のとき poll を呼ばずに silent return できる (古い server.json の誤読防止)。

    OS 別 detach 経路 (Issue #24):
    - POSIX: `start_new_session=True` で親 PG/SID から切り離し、Claude Code 終了後も生存
    - Windows: `creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP` で同等の切り離し
      (`start_new_session` は Win では no-op で親終了時に子も死ぬため不可)

    共通:
    - `stdin/stdout/stderr=DEVNULL`: 親 hook の pipe を引き継がない
    - `close_fds=True`: 余計な fd を継承しない
    """
    if not _SERVER_SCRIPT.exists():
        return None
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
        return subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, str(_SERVER_SCRIPT)],
            **kwargs,
        )
    except OSError:
        # PermissionError / FileNotFoundError / fork limit 等。silent fail。
        return None


# ----------------------------------------------------------------------------
# Issue #34: stdin parser / systemMessage emitter / spawn 後 URL 取得 poll
# ----------------------------------------------------------------------------

def _maybe_log_debug_event(name) -> None:
    """`DASHBOARD_DEBUG_HOOK_EVENT` truthy のとき実値を debug log に append。

    env 未設定時は完全 no-op (本番経路に副作用ゼロ)。I/O 失敗は silent (本処理を壊さない)。
    """
    if not os.environ.get("DASHBOARD_DEBUG_HOOK_EVENT"):
        return
    try:
        log_path = Path(os.environ.get("DASHBOARD_DEBUG_HOOK_EVENT_PATH", str(_DEFAULT_DEBUG_LOG_PATH)))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"hook_event_name": name, "ts": time.time()}
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:  # pylint: disable=broad-except
        # debug 経路の失敗で本処理を壊さない
        pass


def _read_hook_event_name() -> Optional[str]:
    """sys.stdin の先頭 `_HOOK_PEEK_BYTES` を peek + regex で `hook_event_name` 抽出。

    Issue #34 codex P1 対応: payload size に依存しない peek 実装。`json.loads` で
    payload 全体を parse すると、UserPromptSubmit / PostToolUse の長い payload で
    < 100ms budget が崩れる risk があったため、先頭固定 bytes だけを read + regex で
    値だけ取り出す形に変更。

    Issue #34 codex Finding A 対応: peek で見つからない場合は **fallback** で残り
    stdin を読んで full `json.loads`。PostToolUse の `tool_response` が大きい等で
    field が 4KB 以降に来るケースでも通知が出るようにする (rare path のため
    payload-size 依存を許容)。

    - stdin 空 / 値が抽出できない / 未知値 → None
    - `_EXPECTED_HOOK_EVENTS` に含まれる値のみ返す (set membership 防御 / 表記揺れに強い)
    - `name.strip()` で前後 whitespace を吸収
    - `DASHBOARD_DEBUG_HOOK_EVENT` 経路で実値ログを残す (env 未設定時 no-op)
    """
    try:
        head = sys.stdin.read(_HOOK_PEEK_BYTES)
    except Exception:  # pylint: disable=broad-except
        # pytest の stdin capture 等 OSError も silent fallback
        return None
    if not head:
        return None
    match = _HOOK_EVENT_RE.search(head)
    if match:
        name = match.group(1).strip()
    else:
        # codex Finding A: 4KB に収まらなかった → 残りを読んで full parse (rare path)
        try:
            rest = sys.stdin.read()
        except Exception:  # pylint: disable=broad-except
            rest = ""
        try:
            data = json.loads(head + (rest or ""))
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        raw_name = data.get("hook_event_name")
        if not isinstance(raw_name, str):
            return None
        name = raw_name.strip()
    _maybe_log_debug_event(name)
    return name if name in _EXPECTED_HOOK_EVENTS else None


def _emit_dashboard_message(url: str) -> None:
    """`{"systemMessage": "📊 Dashboard: <url>"}` を stdout に 1 行出力。

    Issue #34: 「silent exit 0」原則の緩和点 — 成功経路で hook output JSON を 1 行書ける。
    write 失敗は silent (Claude Code をブロックしない)。
    """
    try:
        sys.stdout.write(json.dumps({"systemMessage": f"{_SYSTEM_MESSAGE_PREFIX}{url}"}) + "\n")
        sys.stdout.flush()
    except Exception:  # pylint: disable=broad-except
        pass


def _wait_for_self_server_json_url(self_pid: int) -> Optional[str]:
    """spawn した子の server.json が現れるまで poll し URL を返す。

    Issue #34 Proposal 1: `info.pid == self_pid` で「自分が spawn した子の json か」を
    確認することで、`_remove_stale_server_json` をすり抜けた古い server.json
    (broken JSON / non-dict は残す設計) を誤って読み「他の URL」を通知することを防ぐ。

    上限 `SPAWN_WAIT_TIMEOUT_SECONDS` を超えたら None (silent fallback)。
    """
    deadline = time.monotonic() + SPAWN_WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        info = _read_server_json(SERVER_JSON_PATH)
        if info is not None:
            pid = info.get("pid")
            url = info.get("url")
            if pid == self_pid and isinstance(url, str) and url:
                return url
        time.sleep(SPAWN_WAIT_INTERVAL_SECONDS)
    return None


def main() -> int:
    """launcher のエントリポイント。常に 0 を返す（silent fail）。

    フロー (Issue #34 拡張):
        event = _read_hook_event_name()  # 未知値 / 不在は None (silent path に倒す)
        if _server_is_alive():
            # 既起動: SessionStart のみ URL 再表示
            if event == "SessionStart":
                emit (info["url"])
            return 0
        _remove_stale_server_json()
        proc = _spawn_server()
        if proc is None: return 0  # Popen 失敗 → silent (poll を呼ばない)
        if event is None: return 0  # 直叩き or 未知 event → silent
        url = _wait_for_self_server_json_url(proc.pid)
        if url: emit (url)
    """
    try:
        event = _read_hook_event_name()
        if _server_is_alive():
            if event == "SessionStart":
                # Issue #34 codex P2 対応: `_server_is_alive()` は pid alive + healthz fail
                # でも True を返す設計 (race window 吸収のため)。stale-PID-reuse / hung-server
                # のケースで「応答しないエンドポイントの URL」を通知してしまうのを避けるため、
                # 通知直前に healthz を **追加で 1 回打って 200 のときだけ** emit する。
                info = _read_server_json(SERVER_JSON_PATH)
                if info is not None:
                    url = info.get("url")
                    if isinstance(url, str) and url and _healthz_ok(url):
                        _emit_dashboard_message(url)
            return 0
        _remove_stale_server_json()
        proc = _spawn_server()
        if proc is None:
            # Popen 失敗 → silent (古い server.json の誤読を構造的に避ける)
            return 0
        if event is None:
            # hook 経由でない直叩き or 未知 event → silent
            return 0
        url = _wait_for_self_server_json_url(proc.pid)
        if url:
            _emit_dashboard_message(url)
    except Exception:  # pylint: disable=broad-except
        # どんな想定外例外でも Claude Code をブロックしない (Issue #14 AC)
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
