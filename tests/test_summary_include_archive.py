"""tests/test_summary_include_archive.py

reports/summary.py の `--include-archive` flag (Issue #30 Phase B) のテスト。

カバー範囲:
- flag なしで hot tier のみ
- flag ありで archive 込みで集計件数が増える
- archive_dir/*.jsonl.gz.tmp を glob から除外
- archive_dir 不在でも crash しない
- archive 内の壊れた行は silent skip
"""
import gzip
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def summary_module(monkeypatch, tmp_path):
    """summary モジュールを env 隔離した状態で再 import + DATA_FILE 直接上書き。

    module-level DATA_FILE は import 時に env evaluate されるため、テスト間で
    sys.modules.pop + 再 import しても package-level cache などで古い値が
    残ることがある。setattr で確実に上書きすることで env race を排除する。
    """
    monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
    sys.modules.pop("reports.summary", None)
    sys.modules.pop("summary", None)
    from reports import summary
    monkeypatch.setattr(summary, "DATA_FILE", tmp_path / "usage.jsonl")
    return summary


def _write_hot(tmp_path: Path, events: list[dict]) -> None:
    p = tmp_path / "usage.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _write_archive(tmp_path: Path, month: str, events: list[dict]) -> None:
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    p = archive_dir / f"{month}.jsonl.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# TestIncludeArchiveFlag
# ---------------------------------------------------------------------------


class TestIncludeArchiveFlag:
    def test_load_events_default_returns_hot_only(self, summary_module, tmp_path):
        _write_hot(
            tmp_path,
            [{"event_type": "skill_tool", "skill": "/foo", "timestamp": "2026-04-20T00:00:00+00:00", "session_id": "s"}],
        )
        _write_archive(
            tmp_path,
            "2025-08",
            [{"event_type": "skill_tool", "skill": "/old", "timestamp": "2025-08-01T00:00:00+00:00", "session_id": "s"}],
        )

        events = summary_module.load_events()
        assert len(events) == 1
        assert events[0]["skill"] == "/foo"

    def test_load_events_include_archive_merges_both(self, summary_module, tmp_path):
        _write_hot(
            tmp_path,
            [{"event_type": "skill_tool", "skill": "/recent", "timestamp": "2026-04-20T00:00:00+00:00", "session_id": "s"}],
        )
        _write_archive(
            tmp_path,
            "2025-08",
            [{"event_type": "skill_tool", "skill": "/old1", "timestamp": "2025-08-01T00:00:00+00:00", "session_id": "s"}],
        )
        _write_archive(
            tmp_path,
            "2025-09",
            [{"event_type": "skill_tool", "skill": "/old2", "timestamp": "2025-09-15T00:00:00+00:00", "session_id": "s"}],
        )

        events = summary_module.load_events(include_archive=True)
        skills = sorted(ev["skill"] for ev in events)
        assert skills == ["/old1", "/old2", "/recent"]


# ---------------------------------------------------------------------------
# TestArchiveTmpExclusion
# ---------------------------------------------------------------------------


class TestArchiveTmpExclusion:
    def test_tmp_files_not_loaded(self, summary_module, tmp_path):
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        # 正常 .gz
        with gzip.open(archive_dir / "2025-08.jsonl.gz", "wt", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"event_type": "skill_tool", "skill": "/proper", "timestamp": "2025-08-01T00:00:00+00:00", "session_id": "s"}
                )
                + "\n"
            )
        # .tmp 残骸 — load されないはず
        with gzip.open(archive_dir / "2025-08.jsonl.gz.99999.tmp", "wt", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"event_type": "skill_tool", "skill": "/leftover", "timestamp": "2025-08-01T00:00:00+00:00", "session_id": "s"}
                )
                + "\n"
            )

        events = summary_module.load_events(include_archive=True)
        skills = [ev["skill"] for ev in events]
        assert "/proper" in skills
        assert "/leftover" not in skills


# ---------------------------------------------------------------------------
# TestArchiveDirNotExist
# ---------------------------------------------------------------------------


class TestArchiveDirNotExist:
    def test_no_archive_dir_with_flag_does_not_crash(self, summary_module, tmp_path):
        _write_hot(
            tmp_path,
            [{"event_type": "skill_tool", "skill": "/x", "timestamp": "2026-04-20T00:00:00+00:00", "session_id": "s"}],
        )
        # archive_dir は作らない
        events = summary_module.load_events(include_archive=True)
        assert len(events) == 1


# ---------------------------------------------------------------------------
# TestArchiveBrokenLineSkip
# ---------------------------------------------------------------------------


class TestArchiveBrokenLineSkip:
    def test_broken_lines_inside_gz_are_skipped(self, summary_module, tmp_path):
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        with gzip.open(archive_dir / "2025-08.jsonl.gz", "wt", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"event_type": "skill_tool", "skill": "/ok", "timestamp": "2025-08-01T00:00:00+00:00", "session_id": "s"}
                )
                + "\n"
            )
            f.write("not valid json {{{ \n")
            f.write(
                json.dumps(
                    {"event_type": "skill_tool", "skill": "/also_ok", "timestamp": "2025-08-02T00:00:00+00:00", "session_id": "s"}
                )
                + "\n"
            )

        events = summary_module.load_events(include_archive=True)
        skills = sorted(ev["skill"] for ev in events)
        assert skills == ["/also_ok", "/ok"]


# ---------------------------------------------------------------------------
# CLI 経路: argparse で --include-archive を受け付ける
# ---------------------------------------------------------------------------


class TestCliFlag:
    def test_main_with_include_archive_flag_uses_archive_events(self, summary_module, tmp_path, capsys):
        _write_hot(
            tmp_path,
            [{"event_type": "skill_tool", "skill": "/recent", "timestamp": "2026-04-20T00:00:00+00:00", "session_id": "s"}],
        )
        _write_archive(
            tmp_path,
            "2025-08",
            [{"event_type": "skill_tool", "skill": "/old", "timestamp": "2025-08-01T00:00:00+00:00", "session_id": "s"}],
        )

        summary_module.main(["--include-archive"])
        out = capsys.readouterr().out
        assert "Total events: 2" in out


# ---------------------------------------------------------------------------
# TestAtomicSnapshot — codex 5th review P2: hot+archive を同じ SH lock 下で読む
# ---------------------------------------------------------------------------


class TestAtomicSnapshot:
    def test_no_double_count_when_archive_runs_concurrently(
        self, summary_module, tmp_path, monkeypatch
    ):
        """codex 5th review P2: archive job が EX を取って event を hot から archive に
        移動している最中に load_events(include_archive=True) が走っても、event を
        二重カウントしない (atomic snapshot 契約)。

        旧実装は hot tier を lock 外で先に読んでから _archive_loader が SH を取って
        archive を読んだため、その間に archive job が走ると event A が hot 経由 +
        archive 経由で 2 回数えられる race window があった (codex 4th P2 #1 fix で
        archive 読み出しを blocking にしたが、read 順序を atomic 化していなかった
        ことで誘発)。修正後は load_events 全体が同じ SH lock 下で実行されるので、
        archive job 完了後の consistent snapshot で A はちょうど 1 回だけ見える。
        """
        try:
            import fcntl
        except ImportError:
            pytest.skip("requires POSIX fcntl")

        import threading
        import time as _time
        import os as _os

        a_old = {
            "event_type": "skill_tool",
            "skill": "/A_old",
            "timestamp": "2025-08-01T00:00:00+00:00",
            "session_id": "s",
        }
        b_recent = {
            "event_type": "skill_tool",
            "skill": "/B_recent",
            "timestamp": "2026-04-20T00:00:00+00:00",
            "session_id": "s",
        }
        _write_hot(tmp_path, [a_old, b_recent])
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        # USAGE_JSONL fixture が tmp_path/usage.jsonl を設定済 → lock は <data>.lock
        lock_path = tmp_path / "usage.jsonl.lock"
        # 念のため明示 (env 経由で _archive_loader / _append が同じ lock を見るように)
        monkeypatch.setenv("USAGE_JSONL_LOCK", str(lock_path))

        ex_acquired = threading.Event()
        HOLD_SECONDS = 0.3

        def archive_job_simulation():
            """archive_usage.run_archive 相当の動作を直列で実行する thread。

            手順 (本物の archive job と同じ): EX 取得 → archive .gz 書き込み →
            hot tier rewrite → EX release。途中の sleep で main 側が「lock 外で
            hot を読む旧バグ経路」を踏める時間を確保する。
            """
            fd = _os.open(str(lock_path), _os.O_CREAT | _os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                ex_acquired.set()
                _time.sleep(HOLD_SECONDS)
                # archive .gz に A_old を書き、hot を [B_recent] に rewrite
                with gzip.open(archive_dir / "2025-08.jsonl.gz", "wt", encoding="utf-8") as f:
                    f.write(json.dumps(a_old, ensure_ascii=False) + "\n")
                hot_path = tmp_path / "usage.jsonl"
                tmp_hot = hot_path.with_name(hot_path.name + ".tmp")
                with tmp_hot.open("w", encoding="utf-8") as f:
                    f.write(json.dumps(b_recent, ensure_ascii=False) + "\n")
                _os.replace(tmp_hot, hot_path)
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                _os.close(fd)

        t = threading.Thread(target=archive_job_simulation, daemon=True)
        t.start()
        assert ex_acquired.wait(timeout=2.0), "archive thread failed to acquire LOCK_EX"

        events = summary_module.load_events(include_archive=True)
        t.join(timeout=2.0)

        skills = [ev.get("skill") for ev in events]
        # 修正後: A_old と B_recent がちょうど 1 回ずつ (archive 完了後の snapshot)
        assert skills.count("/A_old") == 1, (
            f"A_old appears {skills.count('/A_old')} times — race window で double count!"
        )
        assert skills.count("/B_recent") == 1
        assert len(events) == 2
