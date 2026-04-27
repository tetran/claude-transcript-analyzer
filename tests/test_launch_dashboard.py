"""tests/test_launch_dashboard.py — Issue #21 Phase C: launch_dashboard.py のテスト

`hooks/launch_dashboard.py` は SessionStart / UserPromptSubmit / PostToolUse hook で発火し、
server.json を見て (1) 既起動なら何もせず exit 0、(2) 未起動 / pid 死亡 / healthz 失敗 なら
fork-and-detach で `dashboard/server.py` を起動して exit 0 する、べき等な薄い launcher。

テスト戦略:
- 関数単位ユニットテスト: `_read_server_json` / `_is_pid_alive` / `_healthz_ok` /
  `_server_is_alive` / `main` を、subprocess.Popen と urllib.request を patch して検証
- 結合スモークテスト: 実スクリプトを subprocess.run で起動し、server.py が fork-and-detach
  で起動されて server.json が現れることを確認（ベタ）

Issue #14 AC のテスト要件「server.json 不在/alive/dead/healthz失敗 の 4 ケース」をカバー。
"""
# pylint: disable=line-too-long
import importlib.util
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

class _HealthzHandler(BaseHTTPRequestHandler):
    """テスト用の最小 healthz サーバー。各テストで status を切り替える。"""
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
# _server_is_alive (server.json + pid + healthz の総合判定)
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

    def test_dead_pid_removes_stale_server_json_and_returns_false(self, tmp_path):
        """ゾンビ pid file は launch_dashboard が削除する（AC: ゾンビ pid file を削除）。"""
        path = tmp_path / "server.json"
        # 死んだ pid を埋め込む
        with subprocess.Popen([sys.executable, "-c", "pass"]) as proc:
            proc.wait()
        path.write_text(json.dumps({"pid": proc.pid, "port": 9999, "url": "http://127.0.0.1:9999"}), encoding="utf-8")
        mod = load_launch_module(path)
        assert mod._server_is_alive() is False
        assert not path.exists(), "ゾンビ server.json が削除されていない"

    def test_alive_pid_but_healthz_fails_returns_false(self, tmp_path):
        """pid は生きてるが healthz が無応答 → 復帰のために False を返す。"""
        path = tmp_path / "server.json"
        # 自プロセス pid + 接続不可 url
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        path.write_text(json.dumps({"pid": os.getpid(), "port": free_port, "url": f"http://127.0.0.1:{free_port}"}), encoding="utf-8")
        mod = load_launch_module(path)
        assert mod._server_is_alive() is False

    def test_alive_pid_and_healthz_ok_returns_true(self, tmp_path):
        """pid 生存 + healthz 200 → True (re-launch 不要)。"""
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

    def test_healthz_failure_spawns(self, tmp_path):
        path = tmp_path / "server.json"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        path.write_text(json.dumps({"pid": os.getpid(), "port": free_port, "url": f"http://127.0.0.1:{free_port}"}), encoding="utf-8")
        mod = load_launch_module(path)
        with mock.patch.object(mod.subprocess, "Popen") as popen:
            rc = mod.main()
        assert rc == 0
        popen.assert_called_once()

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


# ----------------------------------------------------------------------------
# Popen 引数（fork-and-detach のための呼び出し形）
# ----------------------------------------------------------------------------

class TestSpawnArguments:
    def test_popen_uses_start_new_session(self, tmp_path):
        """fork-and-detach: start_new_session=True で親 PG/SID から切り離す。
        親プロセス（hook 経由 = Claude Code）終了後も子サーバー生存のため必須。"""
        mod = load_launch_module(tmp_path / "server.json")
        with mock.patch.object(mod.subprocess, "Popen") as popen:
            rc = mod.main()
        assert rc == 0
        kwargs = popen.call_args.kwargs
        assert kwargs.get("start_new_session") is True

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
        env["DASHBOARD_IDLE_SECONDS"] = "5"  # テスト後に自動消滅
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

        # 子サーバーが server.json を書くまで待つ
        deadline = time.time() + 5.0
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
            deadline = time.time() + 5.0
            while time.time() < deadline:
                try:
                    os.kill(spawned_pid, 0)
                except (OSError, ProcessLookupError):
                    break
                time.sleep(0.05)
