"""tests/test_dashboard_no_archive.py

dashboard/server.py が archive を読まないことの regression guard
(Issue #30 Phase B / Proposal 5)。

dashboard は仕様で「直近 180 日のみ表示」と約束しているため、archive_dir に
イベントを置いても dashboard の集計には影響してはならない。同時に
build_dashboard_data() は events 引数を全部使う pure 関数 (内部で再 load しない)
ことも pin する — これが崩れると「dashboard は archive を読まない」不変条件が
構造的に揺らぐ。
"""
import gzip
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _write_hot(tmp_path: Path, events: list[dict]) -> None:
    p = tmp_path / "usage.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _write_archive(tmp_path: Path, month: str, events: list[dict]) -> None:
    d = tmp_path / "archive"
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / f"{month}.jsonl.gz", "wt", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


@pytest.fixture(name="server_module")
def _server_module_fixture(monkeypatch, tmp_path):
    monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("HEALTH_ALERTS_JSONL", str(tmp_path / "health_alerts.jsonl"))
    sys.modules.pop("dashboard.server", None)
    sys.modules.pop("server", None)
    from dashboard import server
    return server


class TestDashboardIgnoresArchive:
    def test_load_events_returns_hot_only(self, server_module, tmp_path):
        """server.load_events() は archive_dir に何があっても hot tier のみ返す."""
        _write_hot(
            tmp_path,
            [
                {
                    "event_type": "skill_tool",
                    "skill": "/recent",
                    "timestamp": "2026-04-20T00:00:00+00:00",
                    "session_id": "s",
                    "tool_use_id": "t1",
                }
            ],
        )
        _write_archive(
            tmp_path,
            "2025-08",
            [
                {
                    "event_type": "skill_tool",
                    "skill": "/old",
                    "timestamp": "2025-08-01T00:00:00+00:00",
                    "session_id": "s",
                    "tool_use_id": "t_old",
                }
            ],
        )
        events = server_module.load_events()
        assert len(events) == 1
        assert events[0]["skill"] == "/recent"

    def test_dashboard_total_events_excludes_archive(self, server_module, tmp_path):
        """build_dashboard_data の total_events は hot tier のみ."""
        _write_hot(
            tmp_path,
            [
                {
                    "event_type": "skill_tool",
                    "skill": "/recent",
                    "timestamp": "2026-04-20T00:00:00+00:00",
                    "session_id": "s",
                    "tool_use_id": "t1",
                }
            ],
        )
        _write_archive(
            tmp_path,
            "2025-08",
            [
                {
                    "event_type": "skill_tool",
                    "skill": "/old1",
                    "timestamp": "2025-08-01T00:00:00+00:00",
                    "session_id": "s",
                    "tool_use_id": "t_a",
                },
                {
                    "event_type": "skill_tool",
                    "skill": "/old2",
                    "timestamp": "2025-08-02T00:00:00+00:00",
                    "session_id": "s",
                    "tool_use_id": "t_b",
                },
            ],
        )
        events = server_module.load_events()
        data = server_module.build_dashboard_data(events)
        assert data["total_events"] == 1


class TestBuildDashboardDataIsPure:
    def test_uses_only_argument_events(self, server_module, tmp_path):
        """build_dashboard_data(events) は与えた events 引数のみ集計する pure 関数。
        引数を空にしたら total_events == 0 (hot tier に何があっても無関係)。"""
        _write_hot(
            tmp_path,
            [
                {
                    "event_type": "skill_tool",
                    "skill": "/should_not_appear",
                    "timestamp": "2026-04-20T00:00:00+00:00",
                    "session_id": "s",
                    "tool_use_id": "t1",
                }
            ],
        )
        data = server_module.build_dashboard_data([])
        assert data["total_events"] == 0
        assert data["skill_ranking"] == []
