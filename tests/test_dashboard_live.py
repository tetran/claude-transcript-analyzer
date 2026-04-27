"""tests/test_dashboard_live.py — Issue #19 Phase A: ライブダッシュボード基盤のテスト

`tests/test_dashboard.py` から Phase A 関連 (ThreadingHTTPServer 化 / 空きポート /
server.json lifecycle / idle watchdog / /healthz / run() 統合) を切り出した。
共通ヘルパ `load_dashboard_module` / `write_events` は test_dashboard.py から再利用する。
"""
# pylint: disable=line-too-long
import json
import os
import socketserver
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

# Issue #24 PR#31 codex P2: lock/compare-and-delete primitives は `server_registry` に
# 切り出された。`mod._lock_fd` を monkeypatch しても `server_registry._file_lock`
# 内の参照は変わらないため、内部実装テストは `server_registry` を直接 monkeypatch する。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_dashboard import load_dashboard_module  # noqa: F401, E402  (re-exported helper)
import server_registry  # noqa: E402


def _start_server_in_thread(server) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


class TestThreadingServer:
    """Phase A: ThreadingHTTPServer 化 + 空きポート取得。"""

    def test_create_server_returns_threading_http_server(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        try:
            assert isinstance(server, ThreadingHTTPServer)
        finally:
            server.server_close()

    def test_create_server_with_port_zero_picks_free_port(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        try:
            actual = server.server_address[1]
            assert actual != 0
            assert 1024 <= actual <= 65535
        finally:
            server.server_close()

    def test_create_server_with_specific_port(self, tmp_path):
        """DASHBOARD_PORT 具体ポート指定時の互換: bind に成功する。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        # 一時 bind して空きポートを得てから即解放、そのポートで create_server
        with socketserver.TCPServer(("127.0.0.1", 0), socketserver.BaseRequestHandler) as probe:
            free_port = probe.server_address[1]
        server = mod.create_server(port=free_port, idle_seconds=0)
        try:
            assert server.server_address[1] == free_port
        finally:
            server.server_close()

    def test_init_failure_does_not_mask_original_error_with_attribute_error(self, tmp_path):
        """codex P1 回帰: bind 失敗時、親 TCPServer の `try/except: self.server_close()` 経路で
        override した server_close() が走る。`_stop_event` が super().__init__() より後に初期化
        されていると AttributeError で本来の OSError をマスクする。
        `_stop_event` は super().__init__() より前に作る必要がある。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        # 占有プロセスを建ててから同じポートで生成 → bind 失敗を誘発
        occupier = mod.create_server(port=0, idle_seconds=0)
        busy_port = occupier.server_address[1]
        try:
            try:
                mod.create_server(port=busy_port, idle_seconds=0)
            except BaseException as exc:  # pylint: disable=broad-except
                # AttributeError でマスクされていないことを保証
                assert not isinstance(exc, AttributeError), (
                    f"bind 失敗が AttributeError でマスクされている: {exc!r}"
                )
                # 本来は OSError (EADDRINUSE) が出る
                assert isinstance(exc, OSError), f"想定外の例外型: {type(exc).__name__}: {exc}"
            else:
                # bind が成功してしまった環境（OS によっては SO_REUSEADDR で許される）
                # 本テストの目的は「マスクしない」検証なので skip 相当の no-op
                pass
        finally:
            occupier.server_close()

    def test_concurrent_requests_processed(self, tmp_path):
        """ThreadingHTTPServer なので、ハンドラが少しブロックしても同時に複数リクエストを返せる。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        port = server.server_address[1]
        _start_server_in_thread(server)
        try:
            results: list[int] = []
            errors: list[BaseException] = []

            def hit():
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as resp:
                        results.append(resp.status)
                except BaseException as exc:  # pylint: disable=broad-except
                    errors.append(exc)

            threads = [threading.Thread(target=hit) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)
            assert errors == []
            assert results == [200, 200, 200, 200, 200]
        finally:
            server.shutdown()
            server.server_close()


class TestHealthzEndpoint:
    """Phase A: /healthz が `200 OK` + `{"status":"ok","started_at":...}` を返す。"""

    def test_healthz_returns_200_and_json(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        port = server.server_address[1]
        _start_server_in_thread(server)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz") as resp:
                assert resp.status == 200
                assert "application/json" in resp.headers["Content-Type"]
                payload = json.loads(resp.read())
                assert payload["status"] == "ok"
                # started_at は ISO8601 文字列
                assert isinstance(payload["started_at"], str)
                assert payload["started_at"] == server.started_at
        finally:
            server.shutdown()
            server.server_close()


class TestServerJsonLifecycle:
    """Phase A: server.json を atomic write し、停止時に削除する。"""

    def test_write_server_json_creates_file_with_required_fields(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        info = {
            "pid": 12345,
            "port": 53412,
            "url": "http://localhost:53412",
            "started_at": "2026-04-27T10:00:00+00:00",
        }
        mod.write_server_json(target, info)
        assert target.exists()
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == info

    def test_write_server_json_uses_atomic_replace(self, tmp_path, monkeypatch):
        """tmp に書いて os.replace で原子性を確保する実装になっていること。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"

        replace_calls: list[tuple[str, str]] = []
        original_replace = os.replace

        def spy_replace(src, dst):
            replace_calls.append((str(src), str(dst)))
            return original_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy_replace)
        info = {"pid": 1, "port": 1, "url": "u", "started_at": "t"}
        mod.write_server_json(target, info)
        assert len(replace_calls) == 1
        src, dst = replace_calls[0]
        assert dst == str(target)
        # tmp ファイルは別パスで、replace 後に target だけ残る
        assert src != dst
        assert not Path(src).exists()

    def test_write_server_json_creates_parent_directories(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "nested" / "dir" / "server.json"
        mod.write_server_json(target, {"pid": 1, "port": 1, "url": "u", "started_at": "t"})
        assert target.exists()

    def test_remove_server_json_idempotent(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        # ファイル不在時もエラーにならない
        mod.remove_server_json(target)
        target.write_text("{}", encoding="utf-8")
        mod.remove_server_json(target)
        assert not target.exists()
        # 削除後の二重呼び出しもエラーにならない
        mod.remove_server_json(target)

    def test_remove_server_json_compare_and_delete_matches_pid(self, tmp_path):
        """expected_pid が一致するとき削除する。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        target.write_text(json.dumps({"pid": 4242, "port": 1, "url": "u", "started_at": "t"}), encoding="utf-8")
        removed = mod.remove_server_json(target, expected_pid=4242)
        assert removed is True
        assert not target.exists()

    def test_remove_server_json_compare_and_delete_preserves_other_pid(self, tmp_path):
        """別プロセスが上書きした server.json を消さない（多重インスタンス保護）。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        target.write_text(json.dumps({"pid": 9999, "port": 1, "url": "u", "started_at": "t"}), encoding="utf-8")
        removed = mod.remove_server_json(target, expected_pid=4242)
        assert removed is False
        assert target.exists()
        # 中身は元のまま
        assert json.loads(target.read_text(encoding="utf-8"))["pid"] == 9999

    def test_remove_server_json_compare_and_delete_handles_invalid_json(self, tmp_path):
        """壊れた JSON のときは消さない（誰かが書き換え中の可能性）。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        target.write_text("not valid json", encoding="utf-8")
        removed = mod.remove_server_json(target, expected_pid=4242)
        assert removed is False
        assert target.exists()

    def test_remove_server_json_compare_and_delete_handles_missing_file(self, tmp_path):
        """ファイル不在でもエラーにならず False を返す。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        removed = mod.remove_server_json(target, expected_pid=4242)
        assert removed is False

    def test_remove_server_json_swallows_unlink_oserror(self, tmp_path, monkeypatch):
        """claude[bot] #1 回帰: unlink() で PermissionError などの OSError が起きても
        例外を投げず False を返す。run() の finally で cleanup が壊れないことを保証。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        target.write_text(
            json.dumps({"pid": 4242, "port": 1, "url": "u", "started_at": "t"}),
            encoding="utf-8",
        )

        def boom(_self):
            raise PermissionError("read-only fs")

        monkeypatch.setattr(Path, "unlink", boom)
        # compare-and-delete でも no-args 削除でも、いずれも例外を投げず False を返す
        assert mod.remove_server_json(target, expected_pid=4242) is False
        assert mod.remove_server_json(target) is False


class TestIdleWatchdog:
    """Phase A: idle 経過で graceful shutdown / 0 で無効化 / リクエストで idle カウンタ reset。"""

    def test_idle_for_returns_elapsed_seconds(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        try:
            time.sleep(0.05)
            elapsed = server.idle_for()
            assert elapsed >= 0.04
            assert elapsed < 1.0
        finally:
            server.server_close()

    def test_request_resets_idle_counter(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        port = server.server_address[1]
        _start_server_in_thread(server)
        try:
            time.sleep(0.1)
            assert server.idle_for() >= 0.09
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz") as resp:
                resp.read()
            # リクエスト直後は idle はほぼ 0
            assert server.idle_for() < 0.05
        finally:
            server.shutdown()
            server.server_close()

    def test_watchdog_shuts_down_after_idle(self, tmp_path):
        """idle_seconds 経過で server がシャットダウンする。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0.2)
        t = _start_server_in_thread(server)
        try:
            t.join(timeout=3.0)
            assert not t.is_alive(), "watchdog で serve_forever が exit していない"
        finally:
            server.server_close()

    def test_watchdog_disabled_when_idle_seconds_zero(self, tmp_path):
        """idle_seconds=0 で watchdog は起動せず、外部 shutdown まで生き続ける。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        t = _start_server_in_thread(server)
        try:
            time.sleep(0.5)
            assert t.is_alive(), "idle_seconds=0 なのに自動停止した"
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2.0)


class TestRunIntegration:
    """Phase A: run() が server.json の write/remove を結線する。"""

    def test_run_writes_server_json_with_pid_port_url(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        target = tmp_path / "server.json"

        ready = threading.Event()

        def runner():
            mod.run(server, target, install_signals=False, on_ready=ready.set)

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        try:
            assert ready.wait(timeout=2.0), "run() が ready シグナルを発火しなかった"
            # server.json に required fields が揃っている
            info = json.loads(target.read_text(encoding="utf-8"))
            assert info["pid"] == os.getpid()
            assert info["port"] == server.server_address[1]
            assert info["url"] == f"http://localhost:{server.server_address[1]}"
            assert info["started_at"] == server.started_at
        finally:
            server.shutdown()
            t.join(timeout=2.0)

    def test_run_does_not_remove_server_json_overwritten_by_another_instance(self, tmp_path):
        """codex P2 回帰: A が起動 → B が同じ path に server.json を上書き →
        A が exit しても B のレジストリは残る (compare-and-delete)。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        target = tmp_path / "server.json"

        ready = threading.Event()

        def runner():
            mod.run(server, target, install_signals=False, on_ready=ready.set)

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        try:
            assert ready.wait(timeout=2.0)
            # 別インスタンス B が同じ path に自分の server.json を被せる
            # sentinel 値を使い「自プロセス以外の pid なら削除しない」ことを明示的に検証
            other_pid = 999_999
            target.write_text(
                json.dumps({"pid": other_pid, "port": 99999, "url": "http://x", "started_at": "t"}),
                encoding="utf-8",
            )
        finally:
            server.shutdown()
            t.join(timeout=2.0)
        # A exit 後でも server.json は B のものとして残る
        assert target.exists(), "別インスタンスのレジストリを誤って削除した"
        info = json.loads(target.read_text(encoding="utf-8"))
        assert info["pid"] == other_pid

    def test_run_removes_server_json_on_shutdown(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        target = tmp_path / "server.json"

        ready = threading.Event()

        def runner():
            mod.run(server, target, install_signals=False, on_ready=ready.set)

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        try:
            assert ready.wait(timeout=2.0)
            assert target.exists()
        finally:
            server.shutdown()
            t.join(timeout=2.0)
        # serve_forever が exit した後、server.json は削除されている
        assert not target.exists()


class TestPlatformSpecificServerConfig:
    """Issue #24 N1/N2: Windows 互換のためのプラットフォーム別設定。"""

    def test_allow_reuse_address_disabled_on_windows(self, tmp_path):
        """Windows で allow_reuse_address=True にすると別プロセスがポート横取りできる
        (SO_REUSEADDR の Win 仕様差)。POSIX のみ True、Win では False に倒す。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        if sys.platform == "win32":
            assert mod.DashboardServer.allow_reuse_address is False
        else:
            assert mod.DashboardServer.allow_reuse_address is True

    def test_file_watcher_signature_excludes_inode_on_windows(self, tmp_path):
        """Windows NTFS では st_ino が 0 / 不安定なので、signature から除外する。
        POSIX は (inode, size, mtime_ns)、Win は (size, mtime_ns) のみ。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "watched.jsonl"
        target.write_text("hello", encoding="utf-8")

        watcher = mod._FileWatcher(path=target, interval=0.0)
        sig = watcher._signature()
        assert sig is not None

        if sys.platform == "win32":
            assert len(sig) == 2  # (size, mtime_ns)
        else:
            assert len(sig) == 3  # (inode, size, mtime_ns)
            assert sig[0] == target.stat().st_ino


class TestServerJsonCrossProcessLock:
    """Issue #24: TOCTOU race 解消。write/remove を別ファイル lock 経由で逐次化。

    現状の compare-and-delete は read → pid 比較 → unlink の 4 ステップが
    非アトミックで、A が pid を読んだ後 B が atomic write で上書きすると、
    A の unlink が B のレジストリ (pid 不一致) を誤削除する race が残る。
    `_file_lock` で write/remove を 1 critical section に閉じ込めて解消する。
    """

    def test_file_lock_yields_acquired_true_on_success(self, tmp_path):
        """通常経路: lock 取得成功時は True を yield する。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        with mod._file_lock(tmp_path / "x.lock") as acquired:
            assert acquired is True

    def test_file_lock_yields_acquired_false_when_lock_call_fails(self, tmp_path, monkeypatch):
        """lock 取得に失敗したとき (msvcrt.locking が 10 秒待っても OSError 等)、
        yield False で呼び出し側に伝える。silent best-effort で続行はしない
        (race 解消保証を緩めないため、安全側に倒す責務は呼び出し側へ)。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")

        def boom(_fd):
            raise OSError("simulated lock failure")

        monkeypatch.setattr(server_registry, "_lock_fd", boom)
        with mod._file_lock(tmp_path / "x.lock") as acquired:
            assert acquired is False

    def test_lock_file_co_located_with_server_json(self, tmp_path):
        """`<server.json>.lock` ファイルが同じ親 dir に作られる (cleanup は best-effort)。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        mod.write_server_json(target, {"pid": 1, "port": 1, "url": "u", "started_at": "t"})
        # 命名規約: server.json + ".lock" suffix
        lock = tmp_path / "server.json.lock"
        assert lock.exists(), f"lock ファイルが存在しない (期待パス: {lock})"

    def test_remove_server_json_returns_false_when_lock_unavailable(self, tmp_path, monkeypatch):
        """lock 取得失敗時、remove は何もせず False を返してファイルを残す (安全側)。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        target.write_text(
            json.dumps({"pid": 4242, "port": 1, "url": "u", "started_at": "t"}),
            encoding="utf-8",
        )

        def boom(_fd):
            raise OSError("simulated lock failure")

        monkeypatch.setattr(server_registry, "_lock_fd", boom)
        removed = mod.remove_server_json(target, expected_pid=4242)
        assert removed is False
        # ファイルは残る (削除を諦めた)
        assert target.exists()

    def test_concurrent_write_and_remove_are_serialized(self, tmp_path):
        """write_server_json と remove_server_json を Barrier で同時起動。
        lock があれば必ず逐次化され、最終 state は決定論的:
          - write が後に走った → ファイル存在 + B の pid
          - remove が後に走った → ファイル不在
        race があると read 中の A が B の atomic write 直後の内容を「中途半端に」
        読み取って例外を投げる、または unlink で B のファイルを誤削除する。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        target.write_text(
            json.dumps({"pid": 1, "port": 1, "url": "u", "started_at": "t"}),
            encoding="utf-8",
        )

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def remove_fn():
            try:
                barrier.wait(timeout=2.0)
                mod.remove_server_json(target, expected_pid=1)
            except BaseException as exc:  # pylint: disable=broad-except
                errors.append(exc)

        def write_fn():
            try:
                barrier.wait(timeout=2.0)
                mod.write_server_json(
                    target,
                    {"pid": 2, "port": 2, "url": "u2", "started_at": "t2"},
                )
            except BaseException as exc:  # pylint: disable=broad-except
                errors.append(exc)

        t1 = threading.Thread(target=remove_fn)
        t2 = threading.Thread(target=write_fn)
        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        assert errors == [], f"並行実行中に例外: {errors!r}"
        # 最終 state: 存在するなら必ず B の pid (中間状態の混在ファイル不可)
        if target.exists():
            info = json.loads(target.read_text(encoding="utf-8"))
            assert info["pid"] == 2, (
                f"race 検出: 中間状態のファイルが残っている pid={info.get('pid')}"
            )

    def test_remove_holds_lock_during_compare_and_delete(self, tmp_path, monkeypatch):
        """compare-and-delete の read → pid 比較 → unlink が `_file_lock` 内で
        完結している (構造テスト)。lock を取らずに read+unlink すると
        TOCTOU race が再発するため、lock acquire 経路を必ず通ることを保証。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        target.write_text(
            json.dumps({"pid": 4242, "port": 1, "url": "u", "started_at": "t"}),
            encoding="utf-8",
        )

        lock_acquired_calls: list = []
        original_lock_fd = server_registry._lock_fd

        def spy_lock_fd(fd):
            lock_acquired_calls.append(fd)
            return original_lock_fd(fd)

        monkeypatch.setattr(server_registry, "_lock_fd", spy_lock_fd)
        removed = mod.remove_server_json(target, expected_pid=4242)
        assert removed is True
        assert len(lock_acquired_calls) >= 1, "remove_server_json が lock を取得していない"
