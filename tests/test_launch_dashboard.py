"""tests/test_launch_dashboard.py — Issue #21 Phase C: launch_dashboard.py のテスト

`hooks/launch_dashboard.py` は SessionStart / UserPromptSubmit / PostToolUse hook で発火し、
server.json を見て (1) 既起動なら何もせず exit 0、(2) 未起動 / pid 死亡 / healthz 失敗 なら
fork-and-detach で `dashboard/server.py` を起動して exit 0 する、べき等な薄い launcher。

テスト戦略:
- 関数単位ユニットテスト: `_read_server_json` / `_is_pid_alive` / `_healthz_ok` /
  `_server_is_alive` / `_remove_stale_server_json` / `main` を、subprocess.Popen と
  urllib.request を patch して検証
- 結合スモークテスト: 実スクリプトを subprocess.run で起動し、server.py が fix-and-detach
  で起動されて server.json が現れることを確認（ベタ）

Issue #14 AC のテスト要件「server.json 不在/alive/dead/healthz失敗 の 4 ケース」をカバー。

PR #25 レビュー対応 (Codex F1/F2 + claude[bot] #1〜#5) で以下が変更された:
- `_server_is_alive` は **ピュア化** (副作用 unlink を抜き、`_remove_stale_server_json`
  に分離。`main()` で明示呼び出し)
- `_server_is_alive` は **pid alive のとき healthz をリトライ** (50ms × 3)。
  リトライ後も fail でも **True 返却** (起動中 race window で多重起動を発生させないため)
- `_HealthzHandler.status_code` は autouse fixture で test 終了時に default に戻す
"""
# pylint: disable=line-too-long
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

_LAUNCH_PATH = Path(__file__).parent.parent / "hooks" / "launch_dashboard.py"
_SERVER_PATH = Path(__file__).parent.parent / "dashboard" / "server.py"


def load_launch_module(server_json: Path):
    """DASHBOARD_SERVER_JSON をパッチした状態で launch_dashboard モジュールを読み込む。"""
    os.environ["DASHBOARD_SERVER_JSON"] = str(server_json)
    try:
        spec = importlib.util.spec_from_file_location("launch_dashboard", _LAUNCH_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del os.environ["DASHBOARD_SERVER_JSON"]
    return mod


# ----------------------------------------------------------------------------
# テスト用 healthz handler — 全テストで共有するため state leak 対策が必要
# ----------------------------------------------------------------------------

class _HealthzHandler(BaseHTTPRequestHandler):
    """テスト用の最小 healthz サーバー。各テストで status を切り替える。

    `status_code` はクラス変数で共有される。test_*_500 のような mutate 後に
    他テストへ leak すると並列実行 (pytest-xdist) で flaky になるため、
    `_reset_healthz_handler_state` autouse fixture で各テスト終了時に 200 へ戻す
    (claude[bot] #3 review 対応)。
    """
    status_code = 200

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            self.send_response(self.status_code)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args, **_kwargs):
        pass


@pytest.fixture(autouse=True)
def _reset_healthz_handler_state():
    """各テスト後に `_HealthzHandler.status_code` を 200 に復元 (state leak 対策)。"""
    yield
    _HealthzHandler.status_code = 200


# ----------------------------------------------------------------------------
# _read_server_json
# ----------------------------------------------------------------------------

class TestReadServerJson:
    def test_missing_file_returns_none(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        assert mod._read_server_json(tmp_path / "nonexistent.json") is None

    def test_invalid_json_returns_none(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        path = tmp_path / "broken.json"
        path.write_text("not json {{{", encoding="utf-8")
        assert mod._read_server_json(path) is None

    def test_valid_json_returns_dict(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        path = tmp_path / "ok.json"
        path.write_text(json.dumps({"pid": 1234, "port": 8080, "url": "http://localhost:8080"}), encoding="utf-8")
        info = mod._read_server_json(path)
        assert info == {"pid": 1234, "port": 8080, "url": "http://localhost:8080"}

    def test_non_dict_json_returns_none(self, tmp_path):
        """list / string / number 等 dict でない JSON → None (claude[bot] #4a 対応)。"""
        mod = load_launch_module(tmp_path / "server.json")
        for content in ('[1,2,3]', '"string"', '42', 'null'):
            path = tmp_path / "non_dict.json"
            path.write_text(content, encoding="utf-8")
            assert mod._read_server_json(path) is None, (
                f"non-dict JSON ({content!r}) が None 返却していない"
            )


# ----------------------------------------------------------------------------
# _is_pid_alive
# ----------------------------------------------------------------------------

class TestIsPidAlive:
    def test_self_pid_is_alive(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        assert mod._is_pid_alive(os.getpid()) is True

    def test_dead_pid_returns_false(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        # 確実に存在しない pid を選ぶ: 子を spawn して回収後の pid を使う
        with subprocess.Popen([sys.executable, "-c", "pass"]) as proc:
            proc.wait()
        # 回収済み pid なので os.kill(pid, 0) は ESRCH
        assert mod._is_pid_alive(proc.pid) is False


# ----------------------------------------------------------------------------
# _healthz_ok
# ----------------------------------------------------------------------------

class TestHealthzOk:
    def test_returns_true_for_200(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        _HealthzHandler.status_code = 200
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthzHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            assert mod._healthz_ok(f"http://127.0.0.1:{port}") is True
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2)

    def test_returns_false_for_500(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        _HealthzHandler.status_code = 500
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthzHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            assert mod._healthz_ok(f"http://127.0.0.1:{port}") is False
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2)

    def test_returns_false_for_connection_refused(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        # 空きポートを選んで即解放、そこに繋いで refused
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        assert mod._healthz_ok(f"http://127.0.0.1:{free_port}") is False

    def test_timeout_under_300ms(self, tmp_path):
        """healthz timeout は 200ms が AC。実測で 300ms 以内に False を返すこと。"""
        mod = load_launch_module(tmp_path / "server.json")
        # accept はしないが listen だけする → connect は通るが応答が無い → read timeout
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            t0 = time.perf_counter()
            assert mod._healthz_ok(f"http://127.0.0.1:{port}") is False
            elapsed = time.perf_counter() - t0
            assert elapsed < 0.3, f"healthz が timeout に 300ms 以上 ({elapsed:.3f}s) かかっている"
        finally:
            listener.close()


# ----------------------------------------------------------------------------
# _server_is_alive — ピュア化版 (副作用なし、pid alive 時は healthz fail でも True)
# ----------------------------------------------------------------------------

class TestServerIsAlive:
    def test_no_server_json_returns_false(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        assert mod._server_is_alive() is False

    def test_broken_server_json_returns_false(self, tmp_path):
        path = tmp_path / "server.json"
        path.write_text("garbage{{{", encoding="utf-8")
        mod = load_launch_module(path)
        assert mod._server_is_alive() is False

    def test_dead_pid_returns_false_without_side_effects(self, tmp_path):
        """ピュア化 (claude[bot] #5): `_server_is_alive` は副作用を持たない。
        ゾンビ削除は `_remove_stale_server_json` / `main()` に移譲。"""
        path = tmp_path / "server.json"
        with subprocess.Popen([sys.executable, "-c", "pass"]) as proc:
            proc.wait()
        path.write_text(json.dumps({"pid": proc.pid, "port": 9999, "url": "http://127.0.0.1:9999"}), encoding="utf-8")
        mod = load_launch_module(path)
        assert mod._server_is_alive() is False
        assert path.exists(), "_server_is_alive が副作用 (unlink) を持っている — ピュア化原則に違反"

    def test_alive_pid_with_persistent_healthz_failure_returns_true(self, tmp_path):
        """Codex F2 / claude[bot] #1: pid alive + healthz 永久失敗 → **True** 返却 (spawn 抑止)。
        サーバー起動中の race window で `write_server_json()` 後 `serve_forever()` 開始前に hook が
        発火するケースで、pid alive だけ見て True を返すことで二重起動を防ぐ。デッドロック中サーバー
        は ops 介入 (kill) で復旧する想定。"""
        path = tmp_path / "server.json"
        # 自プロセス pid + 接続不可 url (healthz は絶対 fail)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        path.write_text(json.dumps({"pid": os.getpid(), "port": free_port, "url": f"http://127.0.0.1:{free_port}"}), encoding="utf-8")
        mod = load_launch_module(path)
        # 絶対 fail の healthz だがリトライ後も True 返却 (pid alive 経由)
        assert mod._server_is_alive() is True

    def test_alive_pid_with_eventual_healthz_success_returns_true(self, tmp_path):
        """healthz リトライ動作確認: 1 回目 fail + 2 回目以降 success → True 返却。"""
        path = tmp_path / "server.json"

        class _CountingHealthzHandler(BaseHTTPRequestHandler):
            attempts = 0

            def do_GET(self):  # noqa: N802
                _CountingHealthzHandler.attempts += 1
                if _CountingHealthzHandler.attempts < 2:
                    self.send_response(503)
                else:
                    self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')

            def log_message(self, *_args, **_kwargs):
                pass

        _CountingHealthzHandler.attempts = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), _CountingHealthzHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            path.write_text(json.dumps({"pid": os.getpid(), "port": port, "url": f"http://127.0.0.1:{port}"}), encoding="utf-8")
            mod = load_launch_module(path)
            assert mod._server_is_alive() is True
            assert _CountingHealthzHandler.attempts >= 2, "healthz リトライが効いていない"
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2)

    def test_alive_pid_and_healthz_ok_returns_true(self, tmp_path):
        """pid 生存 + healthz 200 → True (re-launch 不要)。リトライ無しで即時。"""
        path = tmp_path / "server.json"
        _HealthzHandler.status_code = 200
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthzHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            path.write_text(json.dumps({"pid": os.getpid(), "port": port, "url": f"http://127.0.0.1:{port}"}), encoding="utf-8")
            mod = load_launch_module(path)
            assert mod._server_is_alive() is True
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2)


# ----------------------------------------------------------------------------
# _remove_stale_server_json — 切り出された zombie cleanup (claude[bot] #5 対応)
# ----------------------------------------------------------------------------

class TestRemoveStaleServerJson:
    def test_dead_pid_unlinks_and_returns_true(self, tmp_path):
        path = tmp_path / "server.json"
        with subprocess.Popen([sys.executable, "-c", "pass"]) as proc:
            proc.wait()
        path.write_text(json.dumps({"pid": proc.pid, "port": 9999, "url": "http://x"}), encoding="utf-8")
        mod = load_launch_module(path)
        assert mod._remove_stale_server_json() is True
        assert not path.exists()

    def test_alive_pid_keeps_file(self, tmp_path):
        """alive pid は誤って削除しない (server.json は信頼できる状態)。"""
        path = tmp_path / "server.json"
        path.write_text(json.dumps({"pid": os.getpid(), "port": 9999, "url": "http://x"}), encoding="utf-8")
        mod = load_launch_module(path)
        assert mod._remove_stale_server_json() is False
        assert path.exists(), "alive pid のファイルを誤って削除してしまった"

    def test_missing_file_is_noop(self, tmp_path):
        """不在ファイル → 何もしない (False, 例外も投げない)。"""
        mod = load_launch_module(tmp_path / "server.json")
        assert mod._remove_stale_server_json() is False

    def test_broken_json_is_noop(self, tmp_path):
        """壊れた JSON は削除せず spawn 後の atomic replace に任せる (race 回避)。"""
        path = tmp_path / "server.json"
        path.write_text("{broken", encoding="utf-8")
        mod = load_launch_module(path)
        assert mod._remove_stale_server_json() is False
        assert path.exists()


# ----------------------------------------------------------------------------
# main() — 全体フロー (Popen mock)
# ----------------------------------------------------------------------------

class TestMainSpawnDecision:
    def test_alive_does_not_spawn(self, tmp_path):
        path = tmp_path / "server.json"
        _HealthzHandler.status_code = 200
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthzHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            path.write_text(json.dumps({"pid": os.getpid(), "port": port, "url": f"http://127.0.0.1:{port}"}), encoding="utf-8")
            mod = load_launch_module(path)
            with mock.patch.object(mod.subprocess, "Popen") as popen:
                rc = mod.main()
            assert rc == 0
            popen.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2)

    def test_no_server_json_spawns(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.subprocess, "Popen") as popen:
            rc = mod.main()
        assert rc == 0
        popen.assert_called_once()

    def test_dead_pid_spawns(self, tmp_path):
        path = tmp_path / "server.json"
        with subprocess.Popen([sys.executable, "-c", "pass"]) as proc:
            proc.wait()
        path.write_text(json.dumps({"pid": proc.pid, "port": 9999, "url": "http://127.0.0.1:9999"}), encoding="utf-8")
        mod = load_launch_module(path)
        with mock.patch.object(mod.subprocess, "Popen") as popen:
            rc = mod.main()
        assert rc == 0
        popen.assert_called_once()

    def test_healthz_failure_does_not_spawn_when_pid_alive(self, tmp_path):
        """Codex F2 / claude[bot] #1: pid alive + healthz fail → spawn しない (二重起動防止)。"""
        path = tmp_path / "server.json"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        path.write_text(json.dumps({"pid": os.getpid(), "port": free_port, "url": f"http://127.0.0.1:{free_port}"}), encoding="utf-8")
        mod = load_launch_module(path)
        with mock.patch.object(mod.subprocess, "Popen") as popen:
            rc = mod.main()
        assert rc == 0
        popen.assert_not_called()

    def test_spawn_oserror_silent_exit_zero(self, tmp_path):
        """Popen が OSError → exit 0 (Claude Code をブロックしない)。"""
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.subprocess, "Popen", side_effect=OSError("denied")):
            rc = mod.main()
        assert rc == 0

    def test_unexpected_exception_silent_exit_zero(self, tmp_path):
        """想定外の例外でも exit 0 (Claude Code をブロックしない)。"""
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod, "_server_is_alive", side_effect=RuntimeError("boom")):
            rc = mod.main()
        assert rc == 0


class TestMainSweepsZombieBeforeSpawn:
    """main() は dead pid 経路で `_remove_stale_server_json` を呼んでから spawn する
    (claude[bot] #5: 副作用を main() に集約)。"""

    def test_dead_pid_path_unlinks_before_spawn(self, tmp_path):
        path = tmp_path / "server.json"
        with subprocess.Popen([sys.executable, "-c", "pass"]) as proc:
            proc.wait()
        path.write_text(json.dumps({"pid": proc.pid, "port": 9999, "url": "http://127.0.0.1:9999"}), encoding="utf-8")
        mod = load_launch_module(path)
        with mock.patch.object(mod.subprocess, "Popen") as popen:
            rc = mod.main()
        assert rc == 0
        popen.assert_called_once()
        assert not path.exists(), "main() が dead pid 経路で server.json を削除していない"


# ----------------------------------------------------------------------------
# Popen 引数（fork-and-detach のための呼び出し形）
# ----------------------------------------------------------------------------

class TestSpawnArguments:
    def test_popen_uses_start_new_session(self, tmp_path):
        """fork-and-detach (POSIX): start_new_session=True で親 PG/SID から切り離す。
        親プロセス（hook 経由 = Claude Code）終了後も子サーバー生存のため必須。
        Windows では start_new_session が no-op なので別経路 (creationflags) を使う。"""
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "platform", "linux"), \
             mock.patch.object(mod.subprocess, "Popen") as popen:
            rc = mod.main()
        assert rc == 0
        kwargs = popen.call_args.kwargs
        assert kwargs.get("start_new_session") is True
        assert "creationflags" not in kwargs

    def test_popen_uses_creationflags_on_windows(self, tmp_path):
        """fork-and-detach (Win): DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP で
        親プロセス終了後も子サーバー生存。POSIX の start_new_session 相当 (Issue #24)。

        定数値は MSDN ドキュメント通りハードコード:
          DETACHED_PROCESS         = 0x00000008
          CREATE_NEW_PROCESS_GROUP = 0x00000200
        POSIX では subprocess モジュールに DETACHED_PROCESS が存在しないため、
        ハードコード値で比較する (実装側も getattr fallback を使う想定)。
        """
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "platform", "win32"), \
             mock.patch.object(mod.subprocess, "Popen") as popen:
            rc = mod.main()
        assert rc == 0
        kwargs = popen.call_args.kwargs
        # Windows では start_new_session を渡さない
        assert "start_new_session" not in kwargs
        cf = kwargs.get("creationflags", 0)
        assert cf & 0x00000008, f"DETACHED_PROCESS (0x8) が含まれていない: cf={cf:#x}"
        assert cf & 0x00000200, (
            f"CREATE_NEW_PROCESS_GROUP (0x200) が含まれていない: cf={cf:#x}"
        )

    def test_popen_redirects_stdio_to_devnull(self, tmp_path):
        """親 hook の pipe を引き継がない。Claude Code の stdout/stderr を汚さない。"""
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.subprocess, "Popen") as popen:
            mod.main()
        kwargs = popen.call_args.kwargs
        assert kwargs.get("stdin") == subprocess.DEVNULL
        assert kwargs.get("stdout") == subprocess.DEVNULL
        assert kwargs.get("stderr") == subprocess.DEVNULL

    def test_popen_target_is_dashboard_server_py(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.subprocess, "Popen") as popen:
            mod.main()
        args = popen.call_args.args[0]
        # [python, dashboard/server.py]
        assert len(args) == 2
        assert Path(args[1]).name == "server.py"
        assert Path(args[1]).parent.name == "dashboard"


# ----------------------------------------------------------------------------
# Performance — alive 判定経路で 100ms 以内
# ----------------------------------------------------------------------------

class TestPerformance:
    def test_alive_path_under_100ms(self, tmp_path):
        """既起動検出経路は毎 hook 走るため AC: < 100ms。"""
        path = tmp_path / "server.json"
        _HealthzHandler.status_code = 200
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthzHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            path.write_text(json.dumps({"pid": os.getpid(), "port": port, "url": f"http://127.0.0.1:{port}"}), encoding="utf-8")
            mod = load_launch_module(path)
            t0 = time.perf_counter()
            mod.main()
            elapsed = time.perf_counter() - t0
            assert elapsed < 0.1, f"alive 検出経路が 100ms 超過: {elapsed:.3f}s"
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2)


# ----------------------------------------------------------------------------
# 結合スモーク — 実スクリプトを subprocess で起動し fork-and-detach を検証
# ----------------------------------------------------------------------------

class TestHooksJsonRegistration:
    """Issue #26: `launch_dashboard.py` が plan v4 で指示された 4 hook すべてに登録されていること。

    SessionStart / UserPromptExpansion / UserPromptSubmit / PostToolUse の 4 経路で
    べき等 launcher が並列発火するのが Issue #14 plan v4 の AC。
    過去 Phase C PR (#25) で UserPromptExpansion が登録漏れしていたのを Issue #26 で修正。
    """

    @staticmethod
    def _load_hooks_json():
        path = Path(__file__).parent.parent / "hooks" / "hooks.json"
        return json.loads(path.read_text(encoding="utf-8"))["hooks"]

    @pytest.mark.parametrize(
        "event",
        ["SessionStart", "UserPromptExpansion", "UserPromptSubmit", "PostToolUse"],
    )
    def test_launch_dashboard_registered_on_event(self, event):
        """指定 event の hook entries に launch_dashboard.py を呼ぶ command が含まれる。"""
        hooks = self._load_hooks_json()
        assert event in hooks, f"{event} が hooks.json に存在しない"
        commands = [
            h["command"]
            for entry in hooks[event]
            for h in entry["hooks"]
            if h.get("type") == "command"
        ]
        assert any("launch_dashboard.py" in cmd for cmd in commands), (
            f"{event} に launch_dashboard.py が登録されていない (commands: {commands})"
        )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="os.kill semantics differ on Windows; spawn 経路は test_popen_uses_creationflags_on_windows で構造検証",
)
class TestEndToEndLaunch:
    def test_script_spawns_real_dashboard_server(self, tmp_path):
        """実スクリプト起動: server.json 不在 → fork-and-detach で dashboard/server.py が起動し、
        server.json が現れる。子サーバーは cleanup でちゃんと止める。"""
        server_json = tmp_path / "server.json"
        usage_jsonl = tmp_path / "usage.jsonl"
        env = os.environ.copy()
        env["DASHBOARD_SERVER_JSON"] = str(server_json)
        env["USAGE_JSONL"] = str(usage_jsonl)
        env["DASHBOARD_PORT"] = "0"  # 空きポート
        # CI runner (特に macOS arm64) では起動オーバーヘッドが大きく、5s idle だと
        # server.json 書き込み直後に shutdown が動いてテストが捕まえられないことがある。
        # 30s に伸ばして CI flaky を解消 (Issue #24)。
        env["DASHBOARD_IDLE_SECONDS"] = "30"
        # 子サーバーが起動準備に時間がかかる場合があるので poll_interval は長めでも可
        result = subprocess.run(
            [sys.executable, str(_LAUNCH_PATH)],
            input="",
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        assert result.returncode == 0, f"launcher exit code: {result.returncode}, stderr: {result.stderr}"

        # 子サーバーが server.json を書くまで待つ。CI macOS arm64 は Python の
        # cold import + socket bind + write がトータルで 10s を超えることがある
        # ため 30s に伸ばす (Issue #24 PR#31)。ローカルでは 1s 未満で完了する。
        deadline = time.time() + 30.0
        while time.time() < deadline:
            if server_json.exists():
                try:
                    info = json.loads(server_json.read_text(encoding="utf-8"))
                    if "pid" in info and "url" in info:
                        break
                except json.JSONDecodeError:
                    pass
            time.sleep(0.05)
        assert server_json.exists(), "子サーバーが server.json を書いていない"
        info = json.loads(server_json.read_text(encoding="utf-8"))
        spawned_pid = info["pid"]

        try:
            # healthz が応答することを確認（実 server.py が動いている証拠）
            req = urllib.request.Request(f"{info['url']}/healthz")
            with urllib.request.urlopen(req, timeout=2) as resp:
                assert resp.status == 200
        finally:
            # 子サーバーを cleanup
            try:
                os.kill(spawned_pid, 15)  # SIGTERM
            except (OSError, ProcessLookupError):
                pass
            # 終了待ち（DASHBOARD_IDLE_SECONDS=5 で勝手にも消えるが念のため）
            deadline = time.time() + 3.0
            while time.time() < deadline:
                try:
                    os.kill(spawned_pid, 0)
                except (OSError, ProcessLookupError):
                    break
                time.sleep(0.05)
            # SIGTERM を無視するプロセスへの fallback (claude[bot] #4b 対応)
            try:
                os.kill(spawned_pid, 0)
            except (OSError, ProcessLookupError):
                pass  # 既に死亡済み
            else:
                try:
                    os.kill(spawned_pid, 9)  # SIGKILL
                except (OSError, ProcessLookupError):
                    pass


# ============================================================================
# Issue #34: systemMessage 出力テスト
# ============================================================================
# `launch_dashboard.py` が hook output の `{"systemMessage": "📊 Dashboard: <url>"}` を
# stdout に 1 行出力する条件:
#   (a) サーバー新規 spawn 成功時 (4 hook いずれでも)
#   (b) 既起動 + hook_event_name=SessionStart のとき
# それ以外は silent (毎ターン発火する hook で会話を埋めない)。
# ============================================================================

_SYSTEM_MESSAGE_PREFIX = "📊 Dashboard: "
_SUPPORTED_EVENTS = ("SessionStart", "UserPromptExpansion", "UserPromptSubmit", "PostToolUse")


def _stdin_with_event(event_name):
    """`{"hook_event_name": <event_name>}` を流す stdin StringIO。None なら空 stdin。"""
    if event_name is None:
        return io.StringIO("")
    return io.StringIO(json.dumps({"hook_event_name": event_name}))


def _stdin_raw(raw):
    return io.StringIO(raw)


def _assert_dashboard_message(captured, expected_url):
    """capsys.readouterr() の (out, err) が systemMessage 1 行 + stderr 空であることを assert。"""
    out = captured.out
    assert captured.err == "", f"stderr に何か出ている: {captured.err!r}"
    line = out.strip()
    assert line, f"stdout が空: {out!r}"
    # 単一行 (末尾改行 1 つだけ許容)
    assert out.count("\n") <= 1, f"stdout が複数行: {out!r}"
    payload = json.loads(line)  # strict JSON
    assert isinstance(payload, dict), f"payload が dict でない: {payload!r}"
    msg = payload.get("systemMessage")
    assert isinstance(msg, str), f"systemMessage が str でない: {payload!r}"
    assert msg.startswith(_SYSTEM_MESSAGE_PREFIX), f"prefix 不一致: {msg!r}"
    assert msg == f"{_SYSTEM_MESSAGE_PREFIX}{expected_url}"


def _assert_silent(captured):
    assert captured.out == "", f"silent path で stdout に出力された: {captured.out!r}"
    assert captured.err == "", f"silent path で stderr に出力された: {captured.err!r}"


# ----------------------------------------------------------------------------
# _read_hook_event_name — stdin parser
# ----------------------------------------------------------------------------

class TestReadHookEventName:
    """Issue #34: stdin から hook_event_name を読み、未知値 / parse 失敗は None。"""

    def test_empty_stdin_returns_none(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "stdin", io.StringIO("")):
            assert mod._read_hook_event_name() is None

    def test_invalid_json_returns_none(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "stdin", io.StringIO("not json {{{")):
            assert mod._read_hook_event_name() is None

    def test_non_dict_returns_none(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "stdin", io.StringIO('[1,2,3]')):
            assert mod._read_hook_event_name() is None

    def test_missing_field_returns_none(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "stdin", io.StringIO('{"foo":"bar"}')):
            assert mod._read_hook_event_name() is None

    def test_unknown_event_returns_none(self, tmp_path):
        """未知の hook 名は silent path に倒れる (表記揺れ防御)。"""
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "stdin", io.StringIO('{"hook_event_name":"Bogus"}')):
            assert mod._read_hook_event_name() is None

    def test_non_string_value_returns_none(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "stdin", io.StringIO('{"hook_event_name": 42}')):
            assert mod._read_hook_event_name() is None

    @pytest.mark.parametrize("event", _SUPPORTED_EVENTS)
    def test_known_events_return_value(self, tmp_path, event):
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "stdin", io.StringIO(json.dumps({"hook_event_name": event}))):
            assert mod._read_hook_event_name() == event

    def test_whitespace_around_value_is_stripped(self, tmp_path):
        """前後 whitespace が混入しても strip して受け入れる (defensive)。"""
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "stdin", io.StringIO('{"hook_event_name": "  SessionStart  "}')):
            assert mod._read_hook_event_name() == "SessionStart"


# ----------------------------------------------------------------------------
# _wait_for_self_server_json_url — Proposal 1 (pid 一致確認)
# ----------------------------------------------------------------------------

class TestWaitForSelfServerJsonUrl:
    """Proposal 1: poll 中は `info.pid == self_pid` で stale json を拒否する。"""

    def test_returns_url_when_pid_matches(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        path = tmp_path / "server.json"
        path.write_text(json.dumps({"pid": 12345, "port": 9999, "url": "http://x:9999"}), encoding="utf-8")
        assert mod._wait_for_self_server_json_url(12345) == "http://x:9999"

    def test_returns_none_when_pid_mismatches(self, tmp_path):
        """別プロセスの server.json は誤って読まない (stale json 拒否)。"""
        mod = load_launch_module(tmp_path / "server.json")
        path = tmp_path / "server.json"
        path.write_text(json.dumps({"pid": 99999, "port": 9999, "url": "http://stale:9999"}), encoding="utf-8")
        # poll の上限を短く potatch して budget を超えないようにする
        with mock.patch.object(mod, "SPAWN_WAIT_TIMEOUT_SECONDS", 0.05), \
             mock.patch.object(mod, "SPAWN_WAIT_INTERVAL_SECONDS", 0.01):
            assert mod._wait_for_self_server_json_url(12345) is None

    def test_returns_none_when_no_json(self, tmp_path):
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod, "SPAWN_WAIT_TIMEOUT_SECONDS", 0.05), \
             mock.patch.object(mod, "SPAWN_WAIT_INTERVAL_SECONDS", 0.01):
            assert mod._wait_for_self_server_json_url(12345) is None

    def test_returns_url_after_late_arrival(self, tmp_path):
        """poll 開始後に server.json が現れるケース (race window 吸収)。"""
        mod = load_launch_module(tmp_path / "server.json")
        path = tmp_path / "server.json"
        target_pid = 23456

        def write_json_after_delay():
            time.sleep(0.05)
            path.write_text(json.dumps({"pid": target_pid, "port": 9999, "url": "http://late:9999"}), encoding="utf-8")

        t = threading.Thread(target=write_json_after_delay, daemon=True)
        t.start()
        try:
            with mock.patch.object(mod, "SPAWN_WAIT_TIMEOUT_SECONDS", 0.5), \
                 mock.patch.object(mod, "SPAWN_WAIT_INTERVAL_SECONDS", 0.01):
                result = mod._wait_for_self_server_json_url(target_pid)
            assert result == "http://late:9999"
        finally:
            t.join(timeout=2)


# ----------------------------------------------------------------------------
# main() レベル: systemMessage 出力経路 (Issue #34 AC 中心)
# ----------------------------------------------------------------------------

class TestSystemMessageOutput:
    """Issue #34 AC: spawn / alive と hook_event_name の組み合わせで出力判定。

    spawn 経路 (4 hook 全て) → 出力あり / 既起動 + SessionStart → 出力あり /
    既起動 + 他 hook → silent / stdin 不正 → silent。
    """

    @staticmethod
    def _fake_spawn_writing(path, pid, url):
        """`_spawn_server` の side_effect: 子が server.json を書く挙動を fake。"""
        def _spawn():
            path.write_text(json.dumps({"pid": pid, "port": 12345, "url": url}), encoding="utf-8")
            return mock.Mock(pid=pid)
        return _spawn

    @pytest.mark.parametrize("event", _SUPPORTED_EVENTS)
    def test_spawn_path_emits_system_message_for_all_hooks(self, tmp_path, capsys, event):
        """spawn 成功 → 4 hook いずれでも systemMessage 出力。"""
        path = tmp_path / "server.json"
        mod = load_launch_module(path)
        url = f"http://127.0.0.1:54321"
        with mock.patch.object(mod.sys, "stdin", _stdin_with_event(event)), \
             mock.patch.object(mod, "_spawn_server", side_effect=self._fake_spawn_writing(path, 12345, url)):
            rc = mod.main()
        assert rc == 0
        _assert_dashboard_message(capsys.readouterr(), url)

    def test_alive_session_start_emits_system_message(self, tmp_path, capsys):
        """既起動 + SessionStart → 再表示ポリシーで systemMessage 出力。"""
        path = tmp_path / "server.json"
        _HealthzHandler.status_code = 200
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthzHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{port}"
            path.write_text(json.dumps({"pid": os.getpid(), "port": port, "url": url}), encoding="utf-8")
            mod = load_launch_module(path)
            with mock.patch.object(mod.sys, "stdin", _stdin_with_event("SessionStart")):
                rc = mod.main()
            assert rc == 0
            _assert_dashboard_message(capsys.readouterr(), url)
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2)

    @pytest.mark.parametrize("event", ["UserPromptExpansion", "UserPromptSubmit", "PostToolUse"])
    def test_alive_non_session_start_is_silent(self, tmp_path, capsys, event):
        """既起動 + SessionStart 以外 → silent (会話画面を埋めない)。"""
        path = tmp_path / "server.json"
        _HealthzHandler.status_code = 200
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthzHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{port}"
            path.write_text(json.dumps({"pid": os.getpid(), "port": port, "url": url}), encoding="utf-8")
            mod = load_launch_module(path)
            with mock.patch.object(mod.sys, "stdin", _stdin_with_event(event)):
                rc = mod.main()
            assert rc == 0
            _assert_silent(capsys.readouterr())
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2)

    def test_empty_stdin_silent(self, tmp_path, capsys):
        """stdin 空 (hook 経由でない直叩き) → silent fallback。"""
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "stdin", io.StringIO("")), \
             mock.patch.object(mod, "_spawn_server", return_value=mock.Mock(pid=99999)):
            rc = mod.main()
        assert rc == 0
        _assert_silent(capsys.readouterr())

    def test_invalid_json_stdin_silent(self, tmp_path, capsys):
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "stdin", io.StringIO("not json {{{")), \
             mock.patch.object(mod, "_spawn_server", return_value=mock.Mock(pid=99999)):
            rc = mod.main()
        assert rc == 0
        _assert_silent(capsys.readouterr())

    def test_missing_hook_event_name_silent(self, tmp_path, capsys):
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "stdin", io.StringIO('{"foo":"bar"}')), \
             mock.patch.object(mod, "_spawn_server", return_value=mock.Mock(pid=99999)):
            rc = mod.main()
        assert rc == 0
        _assert_silent(capsys.readouterr())

    def test_unknown_hook_event_name_silent(self, tmp_path, capsys):
        """`hook_event_name=Bogus` のような未知値 → silent (set membership 防御)。"""
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "stdin", io.StringIO('{"hook_event_name":"Bogus"}')), \
             mock.patch.object(mod, "_spawn_server", return_value=mock.Mock(pid=99999)):
            rc = mod.main()
        assert rc == 0
        _assert_silent(capsys.readouterr())

    def test_spawn_race_server_json_absent_silent(self, tmp_path, capsys):
        """spawn 後 poll 上限まで server.json が出ない → silent fallback (次回 hook で復活)。"""
        path = tmp_path / "server.json"
        mod = load_launch_module(path)
        # _spawn_server は Popen を返すが server.json は書かれないまま
        with mock.patch.object(mod.sys, "stdin", _stdin_with_event("SessionStart")), \
             mock.patch.object(mod, "_spawn_server", return_value=mock.Mock(pid=99999)), \
             mock.patch.object(mod, "SPAWN_WAIT_TIMEOUT_SECONDS", 0.05), \
             mock.patch.object(mod, "SPAWN_WAIT_INTERVAL_SECONDS", 0.01):
            rc = mod.main()
        assert rc == 0
        _assert_silent(capsys.readouterr())

    def test_spawn_oserror_no_system_message_and_no_poll(self, tmp_path, capsys):
        """Popen が OSError → `_spawn_server` が None 返却 → poll を呼ばずに silent (Proposal 1)。"""
        mod = load_launch_module(tmp_path / "server.json")
        # _spawn_server が None を返す = Popen 失敗
        wait_mock = mock.Mock()
        with mock.patch.object(mod.sys, "stdin", _stdin_with_event("SessionStart")), \
             mock.patch.object(mod, "_spawn_server", return_value=None), \
             mock.patch.object(mod, "_wait_for_self_server_json_url", wait_mock):
            rc = mod.main()
        assert rc == 0
        _assert_silent(capsys.readouterr())
        wait_mock.assert_not_called()  # poll を呼ばないこと

    def test_spawn_rejects_stale_pid_server_json(self, tmp_path, capsys):
        """spawn 直前に他 pid の有効 server.json が残っているケースで、自分の子の json
        と pid 一致しないため URL を採用しない (Proposal 1)。"""
        path = tmp_path / "server.json"
        mod = load_launch_module(path)
        # 古い (alive 別 pid) server.json を残す
        path.write_text(json.dumps({"pid": 88888, "port": 80, "url": "http://stale:80"}), encoding="utf-8")
        # _spawn_server は別の pid を持つ Popen を返すが、子は server.json を書かないまま
        with mock.patch.object(mod.sys, "stdin", _stdin_with_event("SessionStart")), \
             mock.patch.object(mod, "_spawn_server", return_value=mock.Mock(pid=12345)), \
             mock.patch.object(mod, "SPAWN_WAIT_TIMEOUT_SECONDS", 0.05), \
             mock.patch.object(mod, "SPAWN_WAIT_INTERVAL_SECONDS", 0.01):
            rc = mod.main()
        assert rc == 0
        _assert_silent(capsys.readouterr())

    def test_unexpected_exception_no_system_message(self, tmp_path, capsys):
        """内部例外でも systemMessage 出力なし、exit 0 維持。"""
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.sys, "stdin", _stdin_with_event("SessionStart")), \
             mock.patch.object(mod, "_server_is_alive", side_effect=RuntimeError("boom")):
            rc = mod.main()
        assert rc == 0
        _assert_silent(capsys.readouterr())


# ----------------------------------------------------------------------------
# stdout 構造制約 (Proposal 5)
# ----------------------------------------------------------------------------

class TestSystemMessageStructure:
    """Proposal 5: 出力ありケースで stdout 1 行 + strict JSON + prefix + stderr 空を pin。

    hook output protocol は format-fragile (Claude Code parser は厳密)。
    デバッグ print 混入 / BOM / trailing whitespace への regression を構造的に防ぐ。
    """

    def test_emit_output_is_strict_single_line_json(self, tmp_path, capsys):
        path = tmp_path / "server.json"
        mod = load_launch_module(path)
        url = "http://127.0.0.1:11111"

        def fake_spawn():
            path.write_text(json.dumps({"pid": 12345, "port": 11111, "url": url}), encoding="utf-8")
            return mock.Mock(pid=12345)

        with mock.patch.object(mod.sys, "stdin", _stdin_with_event("SessionStart")), \
             mock.patch.object(mod, "_spawn_server", side_effect=fake_spawn):
            mod.main()

        captured = capsys.readouterr()
        # 構造制約
        assert captured.err == "", f"stderr に何か出ている: {captured.err!r}"
        # 1 行 (末尾改行の有無のみ違う)
        assert captured.out.count("\n") <= 1, f"複数行出力: {captured.out!r}"
        # strict JSON parse 可能
        parsed = json.loads(captured.out.strip())
        assert isinstance(parsed, dict)
        # prefix 検証
        assert parsed["systemMessage"].startswith("📊 Dashboard: http")


# ----------------------------------------------------------------------------
# spawn 経路 budget (Proposal 2)
# ----------------------------------------------------------------------------

class TestSpawnPathBudget:
    """Proposal 2: spawn 経路の budget を constants で固定し、テストで pin。

    PostToolUse 経由で偶発的に spawn が走るケース (`_server_is_alive`=False の異常時)
    でも `SPAWN_WAIT_TIMEOUT_SECONDS + 50ms slack` 以内に main() が返ることを保証。
    """

    def test_constants_exposed_as_module_attributes(self, tmp_path):
        """budget 定数が module attribute として export されていること (テストから参照可能)。"""
        mod = load_launch_module(tmp_path / "server.json")
        assert isinstance(mod.SPAWN_WAIT_TIMEOUT_SECONDS, float)
        assert isinstance(mod.SPAWN_WAIT_INTERVAL_SECONDS, float)
        assert mod.SPAWN_WAIT_TIMEOUT_SECONDS > 0
        assert mod.SPAWN_WAIT_INTERVAL_SECONDS > 0
        assert mod.SPAWN_WAIT_INTERVAL_SECONDS < mod.SPAWN_WAIT_TIMEOUT_SECONDS

    def test_spawn_wait_budget_under_300ms(self, tmp_path):
        """spawn 後 server.json が来ない場合でも main() 全体が 300ms 以内に返る。"""
        path = tmp_path / "server.json"
        mod = load_launch_module(path)
        # 子が server.json を書かないシナリオ (Popen は成功するが json は来ない)
        with mock.patch.object(mod.sys, "stdin", _stdin_with_event("SessionStart")), \
             mock.patch.object(mod, "_spawn_server", return_value=mock.Mock(pid=99999)):
            t0 = time.perf_counter()
            mod.main()
            elapsed = time.perf_counter() - t0
        # SPAWN_WAIT_TIMEOUT_SECONDS = 0.25 + slack 50ms = 300ms
        budget = mod.SPAWN_WAIT_TIMEOUT_SECONDS + 0.05
        assert elapsed < budget, f"spawn 経路が {budget*1000:.0f}ms 超過: {elapsed:.3f}s"


# ----------------------------------------------------------------------------
# alive 経路 budget — 4 hook 名でパラメトリック化 (Issue #34 拡張)
# ----------------------------------------------------------------------------

class TestAlivePathBudgetPerHook:
    """既起動経路 < 100ms を 4 hook 名すべてで pin。systemMessage 出力経路 (SessionStart) も含む。

    既存 `TestPerformance.test_alive_path_under_100ms` は stdin 流していなかったので、
    本クラスで `hook_event_name` ありの実機シナリオを 4 通り pin する。
    """

    @pytest.mark.parametrize("event", _SUPPORTED_EVENTS)
    def test_alive_path_under_100ms_per_event(self, tmp_path, event):
        path = tmp_path / "server.json"
        _HealthzHandler.status_code = 200
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthzHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            path.write_text(json.dumps({"pid": os.getpid(), "port": port, "url": f"http://127.0.0.1:{port}"}), encoding="utf-8")
            mod = load_launch_module(path)
            with mock.patch.object(mod.sys, "stdin", _stdin_with_event(event)):
                t0 = time.perf_counter()
                mod.main()
                elapsed = time.perf_counter() - t0
            assert elapsed < 0.1, f"alive 経路 ({event}) が 100ms 超過: {elapsed:.3f}s"
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2)


# ----------------------------------------------------------------------------
# opt-in debug hook (Proposal 3)
# ----------------------------------------------------------------------------

class TestDebugHookEventLogging:
    """Proposal 3: `DASHBOARD_DEBUG_HOOK_EVENT` env が truthy のとき hook_event_name の
    実値を `~/.claude/transcript-analyzer/hook_event_debug.jsonl` (or env で指定された path)
    に append。env 未設定時は完全 no-op (本番経路に副作用ゼロ)。
    """

    def test_no_debug_log_when_env_not_set(self, tmp_path, monkeypatch):
        mod = load_launch_module(tmp_path / "server.json")
        log_path = tmp_path / "debug.jsonl"
        monkeypatch.delenv("DASHBOARD_DEBUG_HOOK_EVENT", raising=False)
        monkeypatch.setenv("DASHBOARD_DEBUG_HOOK_EVENT_PATH", str(log_path))
        with mock.patch.object(mod.sys, "stdin", _stdin_with_event("SessionStart")):
            mod._read_hook_event_name()
        assert not log_path.exists()

    def test_debug_log_appended_when_env_truthy(self, tmp_path, monkeypatch):
        mod = load_launch_module(tmp_path / "server.json")
        log_path = tmp_path / "debug.jsonl"
        monkeypatch.setenv("DASHBOARD_DEBUG_HOOK_EVENT", "1")
        monkeypatch.setenv("DASHBOARD_DEBUG_HOOK_EVENT_PATH", str(log_path))
        with mock.patch.object(mod.sys, "stdin", io.StringIO('{"hook_event_name":"WeirdValue"}')):
            mod._read_hook_event_name()
        assert log_path.exists(), "DASHBOARD_DEBUG_HOOK_EVENT=1 で debug log が書かれない"
        line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
        rec = json.loads(line)
        assert rec.get("hook_event_name") == "WeirdValue"

    def test_debug_log_failure_does_not_raise(self, tmp_path, monkeypatch):
        """debug 経路の I/O 失敗で hook_event_name 経路を壊さない (本番への副作用なし)。"""
        mod = load_launch_module(tmp_path / "server.json")
        # 書けない path を指定 (ディレクトリ扱い)
        bad_path = tmp_path / "subdir"
        bad_path.mkdir()
        monkeypatch.setenv("DASHBOARD_DEBUG_HOOK_EVENT", "1")
        monkeypatch.setenv("DASHBOARD_DEBUG_HOOK_EVENT_PATH", str(bad_path))  # is a dir, not a file
        with mock.patch.object(mod.sys, "stdin", _stdin_with_event("SessionStart")):
            # 例外が漏れず、hook_event_name は通常通り取れる
            assert mod._read_hook_event_name() == "SessionStart"
