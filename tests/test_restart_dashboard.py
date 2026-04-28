"""tests/test_restart_dashboard.py — Issue #52: ライブダッシュボード再起動 (TDD)

`scripts/restart_dashboard.py` は **明示的な手動 restart** 経路。
ユーザーが UI を更新したとき (例: `/plugin update` で `dashboard/template.html` が
変わったが launcher は idempotent で何もしない) にダッシュボードを再起動するための
スクリプト。

責務:
1. server.json を読む (compare-and-delete のため pid を控える)
2. alive なら SIGTERM で graceful shutdown を依頼し、shutdown 完了を待つ
3. タイムアウトしたら SIGKILL で強制終了 (POSIX 経路)
4. server.json を **idempotent に** クリーンアップ (compare-and-delete: 自分が
   控えた pid と一致するときのみ unlink — 他プロセスが既に新規 spawn した json を
   誤って消さない)
5. `hooks/launch_dashboard.py` を直叩きして新 spawn を発生させる

設計上の不変条件:
- server.json 不在 / 壊れた JSON / pid 死亡 でも **冪等** に動く (= spawn だけ実行)
- exit code は 0 (正常完了) / 1 (回復不能なエラー)
- stdlib only

`launch_dashboard.py` (idempotent spawn / silent fail) と違い、`restart_dashboard.py`
は **明示的に起こす** 操作なので少しはエラー出力 (stderr) を出す方針。
"""
# pylint: disable=line-too-long
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

_RESTART_PATH = Path(__file__).parent.parent / "scripts" / "restart_dashboard.py"


def load_restart_module(server_json: Path):
    """DASHBOARD_SERVER_JSON をパッチした状態で restart_dashboard モジュールを読み込む。"""
    os.environ["DASHBOARD_SERVER_JSON"] = str(server_json)
    try:
        spec = importlib.util.spec_from_file_location("restart_dashboard", _RESTART_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del os.environ["DASHBOARD_SERVER_JSON"]
    return mod


# ----------------------------------------------------------------------------
# unit: _read_pid_from_server_json
# ----------------------------------------------------------------------------

def test_read_pid_returns_pid_when_valid(tmp_path):
    """server.json が valid なら pid を返す。"""
    sj = tmp_path / "server.json"
    sj.write_text(json.dumps({"pid": 12345, "port": 8080, "url": "http://x"}))
    mod = load_restart_module(sj)
    assert mod._read_pid_from_server_json() == 12345


def test_read_pid_returns_none_when_missing(tmp_path):
    """server.json が不在なら None。"""
    sj = tmp_path / "server.json"  # 作らない
    mod = load_restart_module(sj)
    assert mod._read_pid_from_server_json() is None


def test_read_pid_returns_none_when_corrupt(tmp_path):
    """server.json が壊れた JSON なら None。"""
    sj = tmp_path / "server.json"
    sj.write_text("{not json")
    mod = load_restart_module(sj)
    assert mod._read_pid_from_server_json() is None


def test_read_pid_returns_none_when_pid_missing(tmp_path):
    """server.json に pid が無ければ None。"""
    sj = tmp_path / "server.json"
    sj.write_text(json.dumps({"port": 8080}))
    mod = load_restart_module(sj)
    assert mod._read_pid_from_server_json() is None


def test_read_pid_returns_none_when_pid_not_int(tmp_path):
    """pid が int でなければ None (型ガード)。"""
    sj = tmp_path / "server.json"
    sj.write_text(json.dumps({"pid": "not_int", "port": 8080}))
    mod = load_restart_module(sj)
    assert mod._read_pid_from_server_json() is None


# ----------------------------------------------------------------------------
# unit: _wait_for_pid_exit
# ----------------------------------------------------------------------------

def test_wait_for_pid_exit_returns_true_when_pid_dies_quickly(tmp_path):
    """pid が timeout 前に死んだら True を返す。"""
    sj = tmp_path / "server.json"
    sj.write_text(json.dumps({"pid": 99999}))  # 中身は使わない
    mod = load_restart_module(sj)
    # pid 死亡を即座に通知する mock
    with mock.patch.object(mod, "_is_pid_alive", return_value=False):
        assert mod._wait_for_pid_exit(99999, timeout=1.0) is True


def test_wait_for_pid_exit_returns_false_on_timeout(tmp_path):
    """timeout までに pid が死ななければ False。"""
    sj = tmp_path / "server.json"
    sj.write_text(json.dumps({"pid": 99999}))
    mod = load_restart_module(sj)
    with mock.patch.object(mod, "_is_pid_alive", return_value=True):
        start = time.monotonic()
        result = mod._wait_for_pid_exit(99999, timeout=0.1)
        elapsed = time.monotonic() - start
    assert result is False
    assert elapsed >= 0.1  # ちゃんと待った
    assert elapsed < 0.5  # 暴走してない


# ----------------------------------------------------------------------------
# unit: _terminate_existing_server
# ----------------------------------------------------------------------------

def test_terminate_returns_true_when_no_server(tmp_path):
    """server.json 不在 → 何もせず True (kill 不要)。"""
    sj = tmp_path / "server.json"  # 作らない
    mod = load_restart_module(sj)
    assert mod._terminate_existing_server() is True


def test_terminate_returns_true_when_pid_already_dead(tmp_path):
    """server.json はあるが pid 死亡 → server.json をクリーンアップして True。"""
    sj = tmp_path / "server.json"
    sj.write_text(json.dumps({"pid": 99999, "url": "http://x"}))
    mod = load_restart_module(sj)
    with mock.patch.object(mod, "_is_pid_alive", return_value=False):
        result = mod._terminate_existing_server()
    assert result is True
    # zombie server.json はクリーンアップされている
    assert not sj.exists()


def test_terminate_sends_sigterm_and_waits(tmp_path):
    """alive なら SIGTERM 送信 → graceful shutdown 待ち → server.json 消えて True。"""
    sj = tmp_path / "server.json"
    sj.write_text(json.dumps({"pid": 12345, "url": "http://x"}))
    mod = load_restart_module(sj)
    kill_calls = []

    def fake_kill(pid, sig):
        kill_calls.append((pid, sig))
        # SIGTERM を受けた server.py は server.json を削除して死ぬ動きを模倣
        if sig == signal.SIGTERM:
            sj.unlink(missing_ok=True)

    # 1 回目 alive → kill 後 dead に切り替え
    alive_states = iter([True, False, False])
    with mock.patch.object(mod, "_is_pid_alive", side_effect=lambda _pid: next(alive_states)):
        with mock.patch("os.kill", side_effect=fake_kill):
            result = mod._terminate_existing_server()
    assert result is True
    assert (12345, signal.SIGTERM) in kill_calls


def test_terminate_falls_back_to_sigkill_on_timeout(tmp_path):
    """SIGTERM で死ななければ SIGKILL で強制終了する (POSIX)。"""
    if sys.platform == "win32":
        pytest.skip("SIGKILL fallback は POSIX 経路のみ")
    sj = tmp_path / "server.json"
    sj.write_text(json.dumps({"pid": 12345, "url": "http://x"}))
    mod = load_restart_module(sj)
    kill_calls = []

    def fake_kill(pid, sig):
        kill_calls.append((pid, sig))
        if sig == signal.SIGKILL:
            # SIGKILL なら確実に死ぬ
            return

    # ずっと alive (SIGTERM では死なない) → SIGKILL 後は dead
    alive_after_kill = {"sigkilled": False}

    def fake_alive(_pid):
        return not alive_after_kill["sigkilled"]

    def fake_kill_with_state(pid, sig):
        kill_calls.append((pid, sig))
        if sig == signal.SIGKILL:
            alive_after_kill["sigkilled"] = True

    with mock.patch.object(mod, "_is_pid_alive", side_effect=fake_alive):
        with mock.patch("os.kill", side_effect=fake_kill_with_state):
            # graceful timeout を短く設定するため env で上書き
            with mock.patch.object(mod, "GRACEFUL_TIMEOUT_SECONDS", 0.1):
                result = mod._terminate_existing_server()
    assert result is True
    sigterm_seen = any(sig == signal.SIGTERM for _pid, sig in kill_calls)
    sigkill_seen = any(sig == signal.SIGKILL for _pid, sig in kill_calls)
    assert sigterm_seen, "SIGTERM should be sent first"
    assert sigkill_seen, "SIGKILL should fall back when SIGTERM does not work"


def test_terminate_handles_kill_permission_error(tmp_path):
    """os.kill が PermissionError → False (他ユーザーの pid なので諦める)。"""
    sj = tmp_path / "server.json"
    sj.write_text(json.dumps({"pid": 1, "url": "http://x"}))  # init pid
    mod = load_restart_module(sj)
    with mock.patch.object(mod, "_is_pid_alive", return_value=True):
        with mock.patch("os.kill", side_effect=PermissionError):
            result = mod._terminate_existing_server()
    assert result is False


# ----------------------------------------------------------------------------
# unit: main()
# ----------------------------------------------------------------------------

def test_main_invokes_launcher_after_terminate(tmp_path):
    """main() は terminate 後に launch_dashboard.py を subprocess 起動する。"""
    sj = tmp_path / "server.json"
    sj.write_text(json.dumps({"pid": 99999, "url": "http://x"}))
    mod = load_restart_module(sj)
    spawn_calls = []

    def fake_run_launcher():
        spawn_calls.append("called")
        return 0

    with mock.patch.object(mod, "_is_pid_alive", return_value=False):
        with mock.patch.object(mod, "_run_launcher", side_effect=fake_run_launcher):
            rc = mod.main()
    assert rc == 0
    assert spawn_calls == ["called"], "launcher must be invoked exactly once"


def test_main_returns_1_when_terminate_fails(tmp_path):
    """terminate が False (kill 不能) なら exit 1。"""
    sj = tmp_path / "server.json"
    sj.write_text(json.dumps({"pid": 1, "url": "http://x"}))
    mod = load_restart_module(sj)
    with mock.patch.object(mod, "_terminate_existing_server", return_value=False):
        with mock.patch.object(mod, "_run_launcher") as launcher:
            rc = mod.main()
    assert rc == 1
    launcher.assert_not_called(), "terminate 失敗時は launcher を呼ばない"


def test_main_works_when_no_server_running(tmp_path):
    """server.json 不在でも main() は launcher を呼んで成功 (起動経路兼用)。"""
    sj = tmp_path / "server.json"  # 作らない
    mod = load_restart_module(sj)
    with mock.patch.object(mod, "_run_launcher", return_value=0) as launcher:
        rc = mod.main()
    assert rc == 0
    launcher.assert_called_once()


# ----------------------------------------------------------------------------
# integration: 実際にスクリプトを subprocess で動かす
# ----------------------------------------------------------------------------

def test_script_runs_and_exits_0_with_no_server(tmp_path, monkeypatch):
    """server.json が無い状態で実スクリプトを叩いて exit 0 を確認する smoke test。

    実際の launch_dashboard.py が呼ばれるが、テスト中は spawn を抑止するため
    `_run_launcher` を no-op で stub する経路として `RESTART_DASHBOARD_DRYRUN` env を見る。
    """
    sj = tmp_path / "server.json"  # 不在
    env = os.environ.copy()
    env["DASHBOARD_SERVER_JSON"] = str(sj)
    env["RESTART_DASHBOARD_DRYRUN"] = "1"  # spawn を抑止
    proc = subprocess.run(
        [sys.executable, str(_RESTART_PATH)],
        env=env, capture_output=True, timeout=10, check=False,
    )
    assert proc.returncode == 0, (
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
