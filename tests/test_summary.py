"""tests/test_summary.py — reports/summary.py のテスト"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

# reports/summary.py をモジュールとして読み込む
_SUMMARY_PATH = Path(__file__).parent.parent / "reports" / "summary.py"


def load_summary_module(usage_jsonl: Path):
    """USAGE_JSONL をパッチした状態で summary モジュールを読み込む。"""
    import importlib
    spec = importlib.util.spec_from_file_location("summary", _SUMMARY_PATH)
    mod = importlib.util.module_from_spec(spec)
    # モジュールレベル定数を差し替えるため、先に sys.modules に登録しない
    # DATA_FILE をパッチするために環境変数経由で渡す
    import os
    os.environ["USAGE_JSONL"] = str(usage_jsonl)
    try:
        spec.loader.exec_module(mod)
    finally:
        del os.environ["USAGE_JSONL"]
    return mod


def write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


class TestLoadEvents:
    def test_returns_empty_list_when_file_missing(self, tmp_path):
        mod = load_summary_module(tmp_path / "nonexistent.jsonl")
        events = mod.load_events()
        assert events == []

    def test_returns_all_events(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "skill_tool", "skill": "my-skill", "project": "proj", "session_id": "s1", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "proj", "session_id": "s1", "timestamp": "2026-01-01T00:01:00+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        assert len(events) == 2
        assert events[0]["event_type"] == "skill_tool"
        assert events[1]["event_type"] == "subagent_start"

    def test_skips_blank_lines(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text(
            '{"event_type": "skill_tool", "skill": "a"}\n\n{"event_type": "skill_tool", "skill": "b"}\n'
        )
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        assert len(events) == 2


class TestAggregateSkills:
    def test_counts_skill_tool_events(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "skill_tool", "skill": "skill-a", "project": "p", "session_id": "s1", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "skill-a", "project": "p", "session_id": "s1", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "skill_tool", "skill": "skill-b", "project": "p", "session_id": "s1", "timestamp": "2026-01-01T00:02:00+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        counts = mod.aggregate_skills(events)
        assert counts["skill-a"] == 2
        assert counts["skill-b"] == 1

    def test_ignores_non_skill_events(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s1", "timestamp": "2026-01-01T00:00:00+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        counts = mod.aggregate_skills(events)
        assert len(counts) == 0

    def test_counts_user_slash_command_events(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "user_slash_command", "skill": "/insights", "project": "p", "session_id": "s1", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "insights", "project": "p", "session_id": "s1", "timestamp": "2026-01-01T00:00:01+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        counts = mod.aggregate_skills(events)
        # user_slash_command と skill_tool は別々に集計される
        assert counts["/insights"] == 1
        assert counts["insights"] == 1


class TestAggregateSubagents:
    def test_counts_subagent_start_events(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s1", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s1", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Plan", "project": "p", "session_id": "s1", "timestamp": "2026-01-01T00:02:00+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        counts = mod.aggregate_subagents(events)
        assert counts["Explore"] == 2
        assert counts["Plan"] == 1

    def test_ignores_non_subagent_events(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "skill_tool", "skill": "my-skill", "project": "p", "session_id": "s1", "timestamp": "2026-01-01T00:00:00+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        counts = mod.aggregate_subagents(events)
        assert len(counts) == 0
