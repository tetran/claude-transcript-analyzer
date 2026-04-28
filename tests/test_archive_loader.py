"""tests/test_archive_loader.py

reports/_archive_loader.py 単体のテスト。

カバー範囲:
- resolve_archive_dir() の env 解決規約 (codex P2 #1)
  - ARCHIVE_DIR 明示 → そのまま
  - ARCHIVE_DIR 未設定 + USAGE_JSONL 指定 → <parent>/archive (archive_usage.py と一致)
  - 両方未設定 → ~/.claude/transcript-analyzer/archive
- archive lock 中の reader fallback (codex P3)
"""
from __future__ import annotations

import gzip
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(name="loader_module")
def _loader_module_fixture(monkeypatch):
    """env 隔離した状態で _archive_loader を再 import。"""
    monkeypatch.delenv("ARCHIVE_DIR", raising=False)
    monkeypatch.delenv("USAGE_JSONL", raising=False)
    monkeypatch.delenv("USAGE_JSONL_LOCK", raising=False)
    sys.modules.pop("reports._archive_loader", None)
    return importlib.import_module("reports._archive_loader")


def _write_archive(archive_dir: Path, month: str, events: list[dict]) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    p = archive_dir / f"{month}.jsonl.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# resolve_archive_dir() — codex P2 #1
# ---------------------------------------------------------------------------


class TestResolveArchiveDir:
    def test_archive_dir_env_wins(self, loader_module, monkeypatch, tmp_path):
        custom = tmp_path / "custom_archive"
        monkeypatch.setenv("ARCHIVE_DIR", str(custom))
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
        assert loader_module.resolve_archive_dir() == custom

    def test_falls_back_to_usage_jsonl_parent(self, loader_module, monkeypatch, tmp_path):
        """codex P2 #1: ARCHIVE_DIR 未設定でも USAGE_JSONL から導出する。

        archive_usage.py:_resolve_paths と同じ規約。これが揃ってないと
        テスト隔離 (USAGE_JSONL のみ override) で reader が誤った default
        パスを見て archive を取りこぼす。
        """
        custom_data = tmp_path / "alt" / "usage.jsonl"
        monkeypatch.setenv("USAGE_JSONL", str(custom_data))
        monkeypatch.delenv("ARCHIVE_DIR", raising=False)
        assert loader_module.resolve_archive_dir() == custom_data.parent / "archive"

    def test_falls_back_to_home_default_when_neither_set(self, loader_module, monkeypatch):
        monkeypatch.delenv("ARCHIVE_DIR", raising=False)
        monkeypatch.delenv("USAGE_JSONL", raising=False)
        expected = Path.home() / ".claude" / "transcript-analyzer" / "archive"
        assert loader_module.resolve_archive_dir() == expected

    def test_archive_usage_and_loader_agree_on_default(
        self, loader_module, monkeypatch, tmp_path
    ):
        """archive_usage.py と _archive_loader.py の規約が揃っていることを直接照合。

        書き手 (archive_usage) と読み手 (loader) の解決パスが食い違うと
        archive 取りこぼしが起きるため、同じ env 状態で同じ Path を返すことを
        プロパティとして固定する。
        """
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
        monkeypatch.delenv("ARCHIVE_DIR", raising=False)

        sys.modules.pop("scripts.archive_usage", None)
        archive_usage = importlib.import_module("scripts.archive_usage")
        writer_paths = archive_usage._resolve_paths()
        reader_dir = loader_module.resolve_archive_dir()
        assert writer_paths.archive_dir == reader_dir


# ---------------------------------------------------------------------------
# archive lock — codex P2 #1 (blocking 契約) / 旧 codex P3 (silent skip → 廃止)
# ---------------------------------------------------------------------------


class TestArchiveLockBlocking:
    def test_loader_blocks_until_lock_released(
        self, loader_module, monkeypatch, tmp_path
    ):
        """codex P2 #1: archive job が LOCK_EX を保持中に loader を呼ぶと、
        archive を silent skip せず blocking で待ち、release 後に events を読む。

        旧 codex P3 fix は LOCK_SH | LOCK_NB で「取れなければ archive を読まない」
        だったが、`--include-archive` を明示したユーザーに「archive 0 件」を返すと
        全期間集計の silent な嘘になる。CLI 起動の reports は < 100ms 制約が無く、
        archive job の EX 保持はサブ秒で終わるため blocking が正解。
        """
        try:
            import fcntl
        except ImportError:
            pytest.skip("requires POSIX fcntl")

        import threading
        import time as _time

        archive_dir = tmp_path / "archive"
        _write_archive(
            archive_dir,
            "2025-08",
            [
                {
                    "event_type": "skill_tool",
                    "skill": "/old",
                    "timestamp": "2025-08-01T00:00:00+00:00",
                    "session_id": "s",
                }
            ],
        )
        lock_path = tmp_path / "usage.jsonl.lock"
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
        monkeypatch.setenv("ARCHIVE_DIR", str(archive_dir))
        monkeypatch.setenv("USAGE_JSONL_LOCK", str(lock_path))

        HOLD_SECONDS = 0.4
        lock_acquired = threading.Event()

        def hold_lock_briefly():
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                lock_acquired.set()
                _time.sleep(HOLD_SECONDS)
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

        holder = threading.Thread(target=hold_lock_briefly, daemon=True)
        holder.start()
        assert lock_acquired.wait(timeout=2.0), "holder thread failed to acquire lock"

        start = _time.monotonic()
        events = list(loader_module.load_archive_events())
        elapsed = _time.monotonic() - start

        holder.join(timeout=2.0)

        # blocking していれば holder の保持時間付近まで待つ。
        # silent skip 実装だと < 50ms で 0 件を返してしまうので、
        # `events 取得 + 待機時間 >= ホールド時間の大半` で blocking 契約を固定。
        assert len(events) == 1, "loader must read archive after lock release"
        assert events[0]["skill"] == "/old"
        assert elapsed >= HOLD_SECONDS * 0.7, (
            f"loader returned in {elapsed:.3f}s — should have blocked "
            f"until lock release (holder held for {HOLD_SECONDS}s)"
        )

    def test_loader_creates_lock_file_when_missing(
        self, loader_module, monkeypatch, tmp_path
    ):
        """codex 6th P3: lock_path 不在 (clean install / 手動削除直後) でも
        loader は lock を取得する。

        旧実装は `lock_path.exists()` で early return していたため、`exists()` 後に
        archive_usage が file 作成 + LOCK_EX 取得した瞬間に reader が unlocked で
        archive を読む TOCTOU window があった。`os.O_RDWR | os.O_CREAT` で
        create-on-open に変えることで window を構造的に閉じる。
        """
        pytest.importorskip("fcntl")

        archive_dir = tmp_path / "archive"
        _write_archive(
            archive_dir,
            "2025-08",
            [
                {
                    "event_type": "skill_tool",
                    "skill": "/old",
                    "timestamp": "2025-08-01T00:00:00+00:00",
                    "session_id": "s",
                }
            ],
        )

        lock_path = tmp_path / "usage.jsonl.lock"
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
        monkeypatch.setenv("ARCHIVE_DIR", str(archive_dir))
        monkeypatch.setenv("USAGE_JSONL_LOCK", str(lock_path))

        # 前提: lock file は存在しない (clean install / 手動削除後)
        assert not lock_path.exists()

        events = list(loader_module.load_archive_events())

        # archive を正常に読めた + lock file が作成された (= O_CREAT が効いた)
        assert len(events) == 1
        assert events[0]["skill"] == "/old"
        assert lock_path.exists(), "loader must create lock file via O_CREAT (TOCTOU 閉鎖)"

    def test_loader_reads_archive_when_lock_is_free(
        self, loader_module, monkeypatch, tmp_path
    ):
        archive_dir = tmp_path / "archive"
        _write_archive(
            archive_dir,
            "2025-08",
            [
                {
                    "event_type": "skill_tool",
                    "skill": "/old",
                    "timestamp": "2025-08-01T00:00:00+00:00",
                    "session_id": "s",
                }
            ],
        )

        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
        monkeypatch.setenv("ARCHIVE_DIR", str(archive_dir))
        monkeypatch.setenv("USAGE_JSONL_LOCK", str(tmp_path / "usage.jsonl.lock"))

        events = list(loader_module.load_archive_events())
        assert len(events) == 1
        assert events[0]["skill"] == "/old"
