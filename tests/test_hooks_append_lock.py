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

import _lock  # noqa: E402  — Issue #44 cross-platform lock helper


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
    """archive job 役: EX を hold_seconds 保持してから release。

    Issue #44: lock 取得は `_lock` 経由で POSIX/Windows 両対応。
    spawn context で別プロセスとして起動されるため module-level の `_lock` が
    新プロセスで fresh import される (再 import 不要)。
    """
    fd = _lock.open_lock_file(Path(lock_path))
    try:
        _lock.acquire_exclusive(fd, blocking=True)
        ready_event.set()
        time.sleep(hold_seconds)
        _lock.release(fd)
    finally:
        os.close(fd)
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


class TestNoLockingDegrade:
    """Issue #44: fcntl も msvcrt も両方 import 不能な特殊環境での degrade 経路。

    通常配布の Python では POSIX なら fcntl、Windows なら msvcrt のいずれかが必ず
    存在するため、in-house Python build や embedded 環境への保険として degrade
    path を維持する。両方とも ImportError のとき `_lock` は no-op (lockless) に
    fall back し、`_append.append_event` も正常に append する。
    """

    def test_lockless_degrade_when_neither_module_available(self, tmp_path, monkeypatch):
        # fcntl と msvcrt の両方を ImportError にする
        monkeypatch.setitem(sys.modules, "fcntl", None)
        monkeypatch.setitem(sys.modules, "msvcrt", None)

        # _lock を reload して _HAS_FCNTL=False, _HAS_MSVCRT=False を効かせる
        sys.modules.pop("_lock", None)
        sys.modules.pop("_append", None)
        import _lock as fresh_lock  # noqa: E402
        importlib.reload(fresh_lock)
        import _append as fresh_append  # noqa: E402
        importlib.reload(fresh_append)

        try:
            assert fresh_lock._HAS_FCNTL is False
            assert fresh_lock._HAS_MSVCRT is False

            data_file = tmp_path / "usage.jsonl"
            fresh_append.append_event(
                data_file, {"event_type": "x", "session_id": "s"}
            )

            events = _read_lines(data_file)
            assert events == [{"event_type": "x", "session_id": "s"}]
        finally:
            # 後続テストのため module を本来の状態に戻す
            sys.modules.pop("_append", None)
            sys.modules.pop("_lock", None)
            monkeypatch.delitem(sys.modules, "fcntl", raising=False)
            monkeypatch.delitem(sys.modules, "msvcrt", raising=False)


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
# TestDropAlertSchema: Issue #51 — drop alert に actionable な情報を載せる
# ---------------------------------------------------------------------------


class TestDropAlertSchema:
    """Issue #51: drop alert はもとから情報量が少なく、復旧が難しかった。

    新スキーマ:
    - `kind: "append_drop"` で種別を明示 (verify_session 由来と区別)
    - `project` で発生プロジェクトが分かる
    - `event_payload` で失われた event 全体を保持 (lost forever 回避 / 手動復旧可)
    - `hint` で recommended action を示す
    - 既存の `alert: "append_skipped_due_to_archive_lock"` は backwards compat で維持
    """

    def test_drop_alert_includes_kind_and_event_payload(self, tmp_path, fresh_append_module):
        alerts_path = tmp_path / "health_alerts.jsonl"
        # fresh_append_module fixture が HEALTH_ALERTS_JSONL を tmp_path に向けている
        event = {
            "event_type": "skill_tool",
            "skill": "user-story-creation",
            "args": "6",
            "project": "myapp",
            "session_id": "sess-drop-1",
            "timestamp": "2026-04-28T10:00:00+00:00",
        }
        fresh_append_module._record_drop_alert(event)
        records = _read_lines(alerts_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["kind"] == "append_drop"
        assert rec["alert"] == "append_skipped_due_to_archive_lock"  # backwards compat
        assert rec["session_id"] == "sess-drop-1"
        assert rec["event_type"] == "skill_tool"
        # event_payload で失われた event 全体を保持 (recovery 用)
        assert rec["event_payload"] == event

    def test_drop_alert_includes_project_when_present(self, tmp_path, fresh_append_module):
        alerts_path = tmp_path / "health_alerts.jsonl"
        event = {
            "event_type": "subagent_start",
            "subagent_type": "Explore",
            "project": "chirper",
            "session_id": "sess-drop-2",
        }
        fresh_append_module._record_drop_alert(event)
        records = _read_lines(alerts_path)
        assert len(records) == 1
        assert records[0]["project"] == "chirper"

    def test_drop_alert_handles_missing_project(self, tmp_path, fresh_append_module):
        """project field が event に無くても落ちない (空文字 fallback)。"""
        alerts_path = tmp_path / "health_alerts.jsonl"
        event = {"event_type": "skill_tool", "session_id": "sess-drop-3"}
        fresh_append_module._record_drop_alert(event)
        records = _read_lines(alerts_path)
        assert len(records) == 1
        assert records[0]["project"] == ""

    def test_drop_alert_includes_hint(self, tmp_path, fresh_append_module):
        """hint で recommended action を示す。"""
        alerts_path = tmp_path / "health_alerts.jsonl"
        event = {"event_type": "skill_tool", "session_id": "sess-drop-4"}
        fresh_append_module._record_drop_alert(event)
        records = _read_lines(alerts_path)
        assert len(records) == 1
        hint = records[0]["hint"]
        assert isinstance(hint, str) and hint  # non-empty
        # event_payload に言及して復旧経路を示す
        assert "event_payload" in hint or "archive" in hint.lower()


# ---------------------------------------------------------------------------
# TestNonContentionPerformance: 非競合 hot path の budget 確認
# ---------------------------------------------------------------------------


class TestNonContentionPerformance:
    def test_p99_under_budget_no_contention(self, tmp_path, fresh_append_module):
        """1 hook の append が budget 内 (非競合)。lock acquire + write + release を含む。

        Issue #44: Windows は `msvcrt.locking` + NTFS + Defender の I/O オーバーヘッド
        により POSIX より構造的に遅い。GitHub Actions `windows-latest` runner では
        p99 が ~30ms まで伸びることが観測されたため OS 別に budget を分ける:

        - POSIX (`fcntl.flock` + ext4/APFS): 20ms (元の budget 維持)
        - Windows (`msvcrt.locking` + NTFS + Defender): 60ms (~2x の余裕)
        """
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
        budget = 0.060 if sys.platform == "win32" else 0.020
        assert p99 < budget, (
            f"p99 {p99 * 1000:.2f}ms exceeds {budget * 1000:.0f}ms budget"
            f" ({sys.platform})"
        )
