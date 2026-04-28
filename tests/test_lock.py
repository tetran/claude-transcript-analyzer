"""hooks/_lock.py — POSIX/Windows cross-platform advisory file lock のテスト
(Issue #44)。

責務:
- open_lock_file: parent mkdir + O_RDWR|O_CREAT で fd を返す
- acquire_shared / acquire_exclusive: blocking / non-blocking 経路
- release: 二重 release で例外を上げない (silent swallow)
- 別プロセスとの競合: blocking=True は release を待つ / blocking=False は即 OSError
- 高レベル API: shared_lock / exclusive_lock context manager
- fcntl も msvcrt も両方 import 不能な特殊環境では no-op degrade
"""
import importlib
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
HOOKS_DIR = PROJECT_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))


@pytest.fixture(name="fresh_lock_module")
def _fresh_lock_module_fixture():
    """_lock モジュールをクリーンに reload。"""
    sys.modules.pop("_lock", None)
    import _lock  # noqa: E402
    importlib.reload(_lock)
    return _lock


# ---------------------------------------------------------------------------
# TestOpenLockFile
# ---------------------------------------------------------------------------


class TestOpenLockFile:
    def test_creates_parent_directory(self, fresh_lock_module, tmp_path):
        lock_path = tmp_path / "nested" / "dir" / "test.lock"
        fd = fresh_lock_module.open_lock_file(lock_path)
        try:
            assert lock_path.exists()
            assert lock_path.parent.is_dir()
        finally:
            os.close(fd)

    def test_returns_valid_fd(self, fresh_lock_module, tmp_path):
        lock_path = tmp_path / "test.lock"
        fd = fresh_lock_module.open_lock_file(lock_path)
        try:
            assert isinstance(fd, int)
            assert fd >= 0
        finally:
            os.close(fd)

    def test_idempotent_on_existing_file(self, fresh_lock_module, tmp_path):
        lock_path = tmp_path / "test.lock"
        lock_path.touch()
        fd = fresh_lock_module.open_lock_file(lock_path)
        os.close(fd)


# ---------------------------------------------------------------------------
# TestAcquireRelease: 非競合時の基本動作
# ---------------------------------------------------------------------------


class TestAcquireRelease:
    def test_acquire_exclusive_blocking(self, fresh_lock_module, tmp_path):
        fd = fresh_lock_module.open_lock_file(tmp_path / "test.lock")
        try:
            fresh_lock_module.acquire_exclusive(fd, blocking=True)
            fresh_lock_module.release(fd)
        finally:
            os.close(fd)

    def test_acquire_exclusive_non_blocking(self, fresh_lock_module, tmp_path):
        fd = fresh_lock_module.open_lock_file(tmp_path / "test.lock")
        try:
            fresh_lock_module.acquire_exclusive(fd, blocking=False)
            fresh_lock_module.release(fd)
        finally:
            os.close(fd)

    def test_acquire_shared_blocking(self, fresh_lock_module, tmp_path):
        fd = fresh_lock_module.open_lock_file(tmp_path / "test.lock")
        try:
            fresh_lock_module.acquire_shared(fd, blocking=True)
            fresh_lock_module.release(fd)
        finally:
            os.close(fd)

    def test_acquire_shared_non_blocking(self, fresh_lock_module, tmp_path):
        fd = fresh_lock_module.open_lock_file(tmp_path / "test.lock")
        try:
            fresh_lock_module.acquire_shared(fd, blocking=False)
            fresh_lock_module.release(fd)
        finally:
            os.close(fd)

    def test_double_release_is_silent(self, fresh_lock_module, tmp_path):
        """二重 release は silent (caller の冗長な finally で例外を上げない)。"""
        fd = fresh_lock_module.open_lock_file(tmp_path / "test.lock")
        try:
            fresh_lock_module.acquire_exclusive(fd, blocking=True)
            fresh_lock_module.release(fd)
            fresh_lock_module.release(fd)  # 二度目は no-op (silent)
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------
# TestCrossProcessContention: 別プロセスでの lock 保持
# ---------------------------------------------------------------------------


def _hold_exclusive_lock(lock_path: str, hold_seconds: float, ready_event):
    """lock holder プロセスのエントリポイント。
    spawn context のため module-level 関数で picklable。"""
    sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
    sys.modules.pop("_lock", None)
    import _lock  # noqa: E402
    importlib.reload(_lock)
    fd = _lock.open_lock_file(Path(lock_path))
    try:
        _lock.acquire_exclusive(fd, blocking=True)
        ready_event.set()
        time.sleep(hold_seconds)
        _lock.release(fd)
    finally:
        os.close(fd)


class TestCrossProcessContention:
    def test_blocking_acquire_waits_until_holder_releases(self, tmp_path):
        """別プロセスが EX 保持中、blocking=True の acquire は release まで待つ。"""
        lock_path = tmp_path / "test.lock"
        ctx = multiprocessing.get_context("spawn")
        ready_event = ctx.Event()
        HOLD = 0.4

        holder = ctx.Process(
            target=_hold_exclusive_lock,
            args=(str(lock_path), HOLD, ready_event),
        )
        holder.start()
        try:
            assert ready_event.wait(timeout=5), "holder failed to acquire lock"

            sys.modules.pop("_lock", None)
            import _lock  # noqa: E402
            importlib.reload(_lock)

            fd = _lock.open_lock_file(lock_path)
            try:
                start = time.monotonic()
                _lock.acquire_exclusive(fd, blocking=True)
                elapsed = time.monotonic() - start
                _lock.release(fd)
                # holder の release を待ったので最低 HOLD * 0.5 秒は経過してる
                assert elapsed >= HOLD * 0.5, (
                    f"acquire returned in {elapsed:.3f}s — should have blocked"
                )
            finally:
                os.close(fd)
        finally:
            holder.join(timeout=5)
            assert holder.exitcode == 0

    def test_non_blocking_fails_immediately_when_contended(self, tmp_path):
        """別プロセスが EX 保持中、blocking=False は即 OSError。"""
        lock_path = tmp_path / "test.lock"
        ctx = multiprocessing.get_context("spawn")
        ready_event = ctx.Event()
        HOLD = 0.5

        holder = ctx.Process(
            target=_hold_exclusive_lock,
            args=(str(lock_path), HOLD, ready_event),
        )
        holder.start()
        try:
            assert ready_event.wait(timeout=5), "holder failed to acquire lock"

            sys.modules.pop("_lock", None)
            import _lock  # noqa: E402
            importlib.reload(_lock)

            fd = _lock.open_lock_file(lock_path)
            try:
                start = time.monotonic()
                with pytest.raises(OSError):
                    _lock.acquire_exclusive(fd, blocking=False)
                elapsed = time.monotonic() - start
                # non-blocking は即失敗 (< 200ms)
                assert elapsed < 0.2, (
                    f"non-blocking returned in {elapsed:.3f}s — should fail immediately"
                )
            finally:
                os.close(fd)
        finally:
            holder.join(timeout=5)
            assert holder.exitcode == 0


# ---------------------------------------------------------------------------
# TestContextManagers
# ---------------------------------------------------------------------------


class TestContextManagers:
    def test_exclusive_lock_acquire_and_release(self, fresh_lock_module, tmp_path):
        lock_path = tmp_path / "test.lock"
        with fresh_lock_module.exclusive_lock(lock_path):
            pass
        # 抜けた後に再取得できる
        with fresh_lock_module.exclusive_lock(lock_path):
            pass

    def test_shared_lock_acquire_and_release(self, fresh_lock_module, tmp_path):
        lock_path = tmp_path / "test.lock"
        with fresh_lock_module.shared_lock(lock_path):
            pass
        with fresh_lock_module.shared_lock(lock_path):
            pass

    def test_exclusive_lock_releases_on_exception(self, fresh_lock_module, tmp_path):
        """context manager の中で例外が起きても lock は release される。"""
        lock_path = tmp_path / "test.lock"
        with pytest.raises(RuntimeError):
            with fresh_lock_module.exclusive_lock(lock_path):
                raise RuntimeError("boom")
        # 例外後も再取得できる (release されているはず)
        with fresh_lock_module.exclusive_lock(lock_path):
            pass

    def test_shared_lock_releases_on_exception(self, fresh_lock_module, tmp_path):
        lock_path = tmp_path / "test.lock"
        with pytest.raises(RuntimeError):
            with fresh_lock_module.shared_lock(lock_path):
                raise RuntimeError("boom")
        with fresh_lock_module.shared_lock(lock_path):
            pass


# ---------------------------------------------------------------------------
# TestNoLockingAvailable: fcntl も msvcrt も無い特殊環境の degrade
# ---------------------------------------------------------------------------


class TestNoLockingAvailable:
    def test_no_op_when_neither_module_available(self, tmp_path, monkeypatch):
        """fcntl / msvcrt 両方 ImportError → 例外を上げず no-op で進む (degrade)。

        通常の Python 配布では片方は必ず存在するが、in-house Python build や
        embedded 環境への保険として degrade path を維持する。
        """
        monkeypatch.setitem(sys.modules, "fcntl", None)
        monkeypatch.setitem(sys.modules, "msvcrt", None)

        sys.modules.pop("_lock", None)
        import _lock  # noqa: E402
        importlib.reload(_lock)

        try:
            assert _lock._HAS_FCNTL is False
            assert _lock._HAS_MSVCRT is False

            fd = _lock.open_lock_file(tmp_path / "test.lock")
            try:
                # acquire/release が例外を上げない
                _lock.acquire_exclusive(fd, blocking=True)
                _lock.acquire_exclusive(fd, blocking=False)
                _lock.acquire_shared(fd, blocking=True)
                _lock.acquire_shared(fd, blocking=False)
                _lock.release(fd)
            finally:
                os.close(fd)

            # context manager も degrade で動く
            with _lock.exclusive_lock(tmp_path / "test2.lock"):
                pass
            with _lock.shared_lock(tmp_path / "test3.lock"):
                pass
        finally:
            sys.modules.pop("_lock", None)
            monkeypatch.delitem(sys.modules, "fcntl", raising=False)
            monkeypatch.delitem(sys.modules, "msvcrt", raising=False)
