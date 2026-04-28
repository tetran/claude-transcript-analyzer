"""tests/archive_test_helpers.py

Archive 関連テスト (test_archive_usage.py / test_archive_state.py) で共有する
helper 関数。test_ プレフィックスを付けないので pytest 収集対象外。
fixture (`archive_module`) は ``tests/conftest.py`` で定義している。
"""
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path


def utc(year, month, day, hour=0, minute=0, second=0, microsecond=0):
    return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=timezone.utc)


def write_hot_tier(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def read_hot_tier(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_archive(archive_dir: Path, month: str) -> list[dict]:
    p = archive_dir / f"{month}.jsonl.gz"
    if not p.exists():
        return []
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def make_event(event_type: str, ts: datetime, **extra) -> dict:
    base = {
        "event_type": event_type,
        "session_id": extra.pop("session_id", "s1"),
        "timestamp": ts.isoformat(),
        "project": extra.pop("project", "p1"),
    }
    base.update(extra)
    return base


def fp_event(event_type: str, **fields) -> dict:
    """fingerprint テスト専用の compact event builder。

    timestamp / session_id を固定既定値にして dict literal の冗長度を抑える。
    """
    return {
        "event_type": event_type,
        "session_id": fields.pop("session_id", "s"),
        "timestamp": fields.pop("timestamp", "2026-01-01T00:00:00+00:00"),
        **fields,
    }
