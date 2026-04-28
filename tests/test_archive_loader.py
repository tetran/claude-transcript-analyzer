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


@pytest.fixture
def loader_module(monkeypatch):
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
# archive lock fallback — codex P3
# ---------------------------------------------------------------------------


class TestArchiveLockFallback:
    def test_loader_skips_archive_when_archive_job_holds_lock(
        self, loader_module, monkeypatch, tmp_path
    ):
        """codex P3: archive job が LOCK_EX を保持中に reader が走ると
        archive .gz と hot tier の両方に同 event が見える transient window で
        二重カウントが起きる。reader は LOCK_SH | LOCK_NB を試み、取得できない
        ときは archive を読まずに空 iterator を返してフォールバックする。
        """
        try:
            import fcntl  # noqa: F401
        except ImportError:
            pytest.skip("requires POSIX fcntl")

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

        # archive lock を別プロセスで保持しているのを模擬: 同プロセスで
        # LOCK_EX を取った fd を生かしたまま load_archive_events を呼ぶ。
        lock_path = tmp_path / "usage.jsonl.lock"
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
        monkeypatch.setenv("ARCHIVE_DIR", str(archive_dir))
        monkeypatch.setenv("USAGE_JSONL_LOCK", str(lock_path))

        import fcntl

        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            events = list(loader_module.load_archive_events())
            assert events == [], "reader must not read archive while LOCK_EX is held"
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)

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
