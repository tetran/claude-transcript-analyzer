"""tests/test_hooks_append_lock.py

hooks/_append.py の lock 付き append (Issue #30 Phase A1 + codex 5th review P1) のテスト。

責務:
- 通常 (非競合) 時の append 成功
- archive job (LOCK_EX) と並行時の blocking LOCK_SH (release 待ち)
- 500ms 超の archive hold でも silent drop せず最終的に append (codex 5th P1)
- fcntl 不在環境 (Windows) での lock なし degrade
- USAGE_JSONL_LOCK env での lock path override
- 非競合時の hot path 性能予算 (p99 < 20ms)
"""
import importlib
import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
HOOKS_DIR = PROJECT_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))


@pytest.fixture(name="fresh_append_module")
def _fresh_append_module_fixture(monkeypatch, tmp_path):
    """_append モジュールをクリーンに reload。"""
    monkeypatch.setenv("USAGE_JSONL_LOCK", str(tmp_path / "custom.lock"))
    monkeypatch.setenv("HEALTH_ALERTS_JSONL", str(tmp_path / "health_alerts.jsonl"))
    sys.modules.pop("_append", None)
    import _append  # noqa: E402
    importlib.reload(_append)
    return _append


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# TestSharedLockAcquire: 通常 (非競合) 時 — LOCK_SH 取得 → append 成功
# ---------------------------------------------------------------------------


class TestSharedLockAcquire:
    def test_basic_append_succeeds(self, tmp_path, fresh_append_module):
        data_file = tmp_path / "usage.jsonl"
        event = {"event_type": "skill_tool", "skill": "/foo", "session_id": "s1"}

        fresh_append_module.append_event(data_file, event)

        events = _read_lines(data_file)
        assert events == [event]

    def test_multiple_appends_preserve_order(self, tmp_path, fresh_append_module):
        data_file = tmp_path / "usage.jsonl"
        events = [{"event_type": f"e{i}", "session_id": "s"} for i in range(5)]

        for ev in events:
            fresh_append_module.append_event(data_file, ev)

        assert _read_lines(data_file) == events

    def test_creates_parent_directory(self, tmp_path, fresh_append_module):
        data_file = tmp_path / "nested" / "dir" / "usage.jsonl"
        fresh_append_module.append_event(data_file, {"event_type": "x"})

        assert data_file.exists()


# ---------------------------------------------------------------------------
# TestArchiveContention: archive job (LOCK_EX) と並行 — 5 retry × 100ms で待機後成功
# ---------------------------------------------------------------------------


def _archive_holds_ex_lock(lock_path: str, hold_seconds: float, ready_event, done_event):
    """archive job 役: LOCK_EX を hold_seconds 保持してから release。"""
    import fcntl
    with open(lock_path, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        ready_event.set()
        time.sleep(hold_seconds)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    done_event.set()


def _hook_appends_with_lock(data_file: str, lock_path: str, alerts_path: str, event: dict, ready_event, result_queue):
    """hook 役: archive ready 後に append_event を呼ぶ。"""
    os.environ["USAGE_JSONL_LOCK"] = lock_path
    os.environ["HEALTH_ALERTS_JSONL"] = alerts_path
    sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
    sys.modules.pop("_append", None)
    import _append
    importlib.reload(_append)

    ready_event.wait(timeout=5)
    start = time.time()
    _append.append_event(Path(data_file), event)
    elapsed = time.time() - start
    result_queue.put(elapsed)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX flock 限定 — Windows は lock なし degrade パス (TestFcntlMissing 参照)",
)
class TestArchiveContention:
    def test_hook_waits_then_succeeds_when_archive_releases(self, tmp_path):
        """archive が短時間 EX 保持 → hook は blocking で待ち release 後に append する。"""
        data_file = tmp_path / "usage.jsonl"
        lock_path = tmp_path / "usage.jsonl.lock"
        alerts_path = tmp_path / "health_alerts.jsonl"
        event = {"event_type": "skill_tool", "session_id": "s1"}

        ctx = multiprocessing.get_context("spawn")
        ready_event = ctx.Event()
        done_event = ctx.Event()
        result_queue = ctx.Queue()

        archive = ctx.Process(
            target=_archive_holds_ex_lock,
            args=(str(lock_path), 0.25, ready_event, done_event),
        )
        hook = ctx.Process(
            target=_hook_appends_with_lock,
            args=(str(data_file), str(lock_path), str(alerts_path), event, ready_event, result_queue),
        )

        archive.start()
        hook.start()
        archive.join(timeout=5)
        hook.join(timeout=5)

        assert archive.exitcode == 0
        assert hook.exitcode == 0

        elapsed = result_queue.get(timeout=1)
        # blocking 経路: hook は archive release を待ってから append する。
        # 0.25s 保持なので最低 0.1s は contention で待つ。
        assert elapsed >= 0.1, (
            f"hook returned too quickly ({elapsed:.3f}s) — lock contention not exercised"
        )

        events = _read_lines(data_file)
        assert events == [event]
        # alert は記録されていない (drop していない)
        assert not alerts_path.exists() or _read_lines(alerts_path) == []


# ---------------------------------------------------------------------------
# TestBlockingThroughLongHold: codex 5th review P1 — 500ms 超 EX hold でも drop しない
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX flock 限定",
)
class TestBlockingThroughLongHold:
    def test_hook_blocks_through_long_archive_hold_no_drop(self, tmp_path):
        """codex 5th review P1: archive が長時間 (500ms 超) EX を保持しても、
        blocking LOCK_SH に切り替えたので hook は silent drop せず最終的に append する。

        旧実装は LOCK_SH | LOCK_NB × 5 retry × 100ms = 500ms upper-bound で
        500ms 超えると `_record_drop_alert` 経由で event を silent drop していた。
        これは launch_archive auto-launcher (Phase C) が SessionStart で
        archive_usage を起動する前提下では、長期運用環境で大きな usage.jsonl の
        gzip rewrite に > 500ms かかる現実的なケースで append-only 不変条件を
        破っていた。blocking に切り替えることで data loss を eliminate する。
        """
        data_file = tmp_path / "usage.jsonl"
        lock_path = tmp_path / "usage.jsonl.lock"
        alerts_path = tmp_path / "health_alerts.jsonl"
        event = {"event_type": "skill_tool", "skill": "/foo", "session_id": "s_long"}

        ctx = multiprocessing.get_context("spawn")
        ready_event = ctx.Event()
        done_event = ctx.Event()
        result_queue = ctx.Queue()

        # 旧 retry budget 500ms を確実に超える 0.7s 保持
        HOLD_SECONDS = 0.7

        archive = ctx.Process(
            target=_archive_holds_ex_lock,
            args=(str(lock_path), HOLD_SECONDS, ready_event, done_event),
        )
        hook = ctx.Process(
            target=_hook_appends_with_lock,
            args=(str(data_file), str(lock_path), str(alerts_path), event, ready_event, result_queue),
        )

        archive.start()
        hook.start()
        hook.join(timeout=5)
        archive.join(timeout=5)

        assert hook.exitcode == 0

        elapsed = result_queue.get(timeout=1)
        # blocking なので hook は archive 完了 (~0.7s) まで待つ
        assert elapsed >= 0.5, (
            f"hook returned in {elapsed:.3f}s — should have blocked through "
            f"the full {HOLD_SECONDS}s archive hold"
        )

        # event は drop されず append されている (data loss 無し = append-only 守られた)
        events = _read_lines(data_file)
        assert events == [event], (
            "blocking 契約: 長時間 EX hold でも event は最終的に append される"
        )
        # drop alert は記録されない (drop が起きていない)
        assert not alerts_path.exists() or _read_lines(alerts_path) == [], (
            "blocking 契約下では drop alert は出ないはず"
        )


# ---------------------------------------------------------------------------
# TestFcntlMissing: fcntl ImportError → lock なし append
# ---------------------------------------------------------------------------


class TestFcntlMissing:
    def test_no_fcntl_falls_back_to_lockless_append(self, tmp_path, monkeypatch):
        # fcntl import を ImportError にする
        monkeypatch.setitem(sys.modules, "fcntl", None)

        # _append を reload して _HAS_FCNTL = False を効かせる
        sys.modules.pop("_append", None)
        import _append
        importlib.reload(_append)

        try:
            assert _append._HAS_FCNTL is False, (
                "fcntl ImportError simulate しても _HAS_FCNTL=True のまま"
            )

            data_file = tmp_path / "usage.jsonl"
            _append.append_event(data_file, {"event_type": "x", "session_id": "s"})

            events = _read_lines(data_file)
            assert events == [{"event_type": "x", "session_id": "s"}]
        finally:
            # 後続テストのため _append を本来の状態に戻す
            sys.modules.pop("_append", None)
            monkeypatch.delitem(sys.modules, "fcntl", raising=False)


# ---------------------------------------------------------------------------
# TestEnvOverride: USAGE_JSONL_LOCK env で lock path 上書き
# ---------------------------------------------------------------------------


class TestEnvOverride:
    def test_usage_jsonl_lock_env_overrides_default(self, tmp_path, monkeypatch):
        custom_lock = tmp_path / "custom_subdir" / "my_lock_file"
        monkeypatch.setenv("USAGE_JSONL_LOCK", str(custom_lock))

        sys.modules.pop("_append", None)
        import _append
        importlib.reload(_append)

        data_file = tmp_path / "usage.jsonl"
        resolved = _append._resolve_lock_path(data_file)
        assert resolved == custom_lock

    def test_default_lock_path_is_data_file_dot_lock(self, tmp_path, monkeypatch):
        monkeypatch.delenv("USAGE_JSONL_LOCK", raising=False)

        sys.modules.pop("_append", None)
        import _append
        importlib.reload(_append)

        data_file = tmp_path / "usage.jsonl"
        resolved = _append._resolve_lock_path(data_file)
        assert resolved == Path(str(data_file) + ".lock")

    def test_explicit_lock_path_overrides_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USAGE_JSONL_LOCK", str(tmp_path / "from_env.lock"))

        sys.modules.pop("_append", None)
        import _append
        importlib.reload(_append)

        explicit = tmp_path / "explicit.lock"
        data_file = tmp_path / "usage.jsonl"
        resolved = _append._resolve_lock_path(data_file, lock_path=explicit)
        assert resolved == explicit


# ---------------------------------------------------------------------------
# TestNonContentionPerformance: 非競合 hot path の budget 確認
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX flock 限定 (Windows degrade path は別経路)",
)
class TestNonContentionPerformance:
    def test_p99_under_10ms_no_contention(self, tmp_path, fresh_append_module):
        """1 hook の append が < 10ms (非競合)。lock acquire + write + release を含む。"""
        data_file = tmp_path / "usage.jsonl"
        event = {"event_type": "skill_tool", "session_id": "s_perf"}

        # warmup
        for _ in range(5):
            fresh_append_module.append_event(data_file, event)

        durations = []
        for _ in range(100):
            start = time.perf_counter()
            fresh_append_module.append_event(data_file, event)
            durations.append(time.perf_counter() - start)

        durations.sort()
        p99 = durations[int(len(durations) * 0.99)]
        assert p99 < 0.020, f"p99 {p99 * 1000:.2f}ms exceeds 20ms budget"
