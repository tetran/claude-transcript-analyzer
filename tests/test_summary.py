"""tests/test_summary.py — reports/summary.py のテスト"""
# pylint: disable=line-too-long
import json
from pathlib import Path

# reports/summary.py をモジュールとして読み込む
_SUMMARY_PATH = Path(__file__).parent.parent / "reports" / "summary.py"


def load_summary_module(usage_jsonl: Path):
    """USAGE_JSONL をパッチした状態で summary モジュールを読み込む。"""
    import importlib.util
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

    def test_skips_invalid_json_lines(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text(
            '{"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"}\n'
            "not valid json\n"
            '{"event_type": "skill_tool", "skill": "b", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"}\n'
        )
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        assert len(events) == 2
        assert events[0]["skill"] == "a"
        assert events[1]["skill"] == "b"


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


class TestAggregateSkillStats:
    """Issue #8: 失敗率を含む拡張集計"""

    def test_skill_stats_includes_failure_count(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "skill_tool", "skill": "commit", "success": True, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "commit", "success": False, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        stats = mod.aggregate_skill_stats(events)
        assert stats["commit"]["count"] == 2
        assert stats["commit"]["failure_count"] == 1
        assert abs(stats["commit"]["failure_rate"] - 0.5) < 1e-9


class TestAggregateSessionStats:
    """Issue #9: セッション・コンテキスト・摩擦サマリ"""

    def test_session_stats_counts_and_resume_rate(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "session_start", "source": "startup", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "session_start", "source": "resume", "session_id": "s2", "project": "p", "timestamp": "2026-01-02T00:00:00+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        s = mod.aggregate_session_stats(events)
        assert s["total_sessions"] == 2
        assert s["resume_count"] == 1
        assert s["resume_rate"] == 0.5

    def test_session_stats_zero_when_empty(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        write_events(usage_file, [])
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        s = mod.aggregate_session_stats(events)
        assert s["total_sessions"] == 0
        assert s["resume_rate"] == 0.0
        assert s["compact_count"] == 0
        assert s["permission_prompt_count"] == 0


class TestAggregateSubagentStats:
    """Issue #8: 失敗率と平均 duration を含む拡張集計"""

    def test_subagent_stats_includes_avg_duration_from_stop(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "duration_ms": 1000, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:10+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "duration_ms": 3000, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:20+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        stats = mod.aggregate_subagent_stats(events)
        assert stats["Explore"]["count"] == 1
        assert stats["Explore"]["avg_duration_ms"] == 2000.0

    def test_subagent_stats_failure_count(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "subagent_start", "subagent_type": "Plan", "success": False, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Plan", "success": True, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        stats = mod.aggregate_subagent_stats(events)
        assert stats["Plan"]["count"] == 2
        assert stats["Plan"]["failure_count"] == 1
