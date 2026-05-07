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
    with path.open("w", encoding="utf-8") as f:
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

    def test_session_stats_permission_prompt_accepts_short_form(self, tmp_path):
        """公式 hooks 仕様の短縮形 'permission' も permission_prompt と同じくカウント対象"""
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "notification", "notification_type": "permission", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "notification", "notification_type": "permission_prompt", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "notification", "notification_type": "idle", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T00:02:00+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        s = mod.aggregate_session_stats(events)
        assert s["permission_prompt_count"] == 2


class TestAggregateSubagentStats:
    """Issue #8: 失敗率と平均 duration を含む拡張集計"""

    def test_subagent_stats_includes_avg_duration_from_stop(self, tmp_path):
        """2 invocation 分の subagent_stop.duration_ms から avg を取る (paired stops)"""
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "duration_ms": 1000, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:10+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "duration_ms": 3000, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:20+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        stats = mod.aggregate_subagent_stats(events)
        assert stats["Explore"]["count"] == 2
        assert stats["Explore"]["avg_duration_ms"] == 2000.0

    def test_failure_count_capped_by_count_when_both_events_fail(self, tmp_path):
        """1 invocation の起動失敗と実行失敗が重複しても failure_rate は 100% を超えない"""
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "success": False, "tool_use_id": "toolu_x", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "success": False, "subagent_id": "agent_x", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:10+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        stats = mod.aggregate_subagent_stats(events)
        assert stats["Explore"]["count"] == 1
        assert stats["Explore"]["failure_count"] == 1
        assert stats["Explore"]["failure_rate"] == 1.0

    def test_failure_count_invocation_pairing_mixed_mode(self, tmp_path):
        """Codex round 4 P1: mixed mode で invocation 単位 dedup が効く"""
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "subagent_start", "subagent_type": "Plan", "success": False, "tool_use_id": "toolu_a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Plan", "success": False, "subagent_id": "agent_a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:10+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Plan", "tool_use_id": "toolu_b", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Plan", "success": False, "subagent_id": "agent_b", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:30+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Plan", "tool_use_id": "toolu_c", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:02:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Plan", "success": True, "subagent_id": "agent_c", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:02:30+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        stats = mod.aggregate_subagent_stats(events)
        assert stats["Plan"]["count"] == 3
        assert stats["Plan"]["failure_count"] == 2
        assert abs(stats["Plan"]["failure_rate"] - (2 / 3)) < 1e-9

    def test_failures_from_distinct_invocations_summed(self, tmp_path):
        """別 invocation の起動失敗と実行失敗を別個にカウントできる"""
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "success": False, "tool_use_id": "toolu_a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Explore", "tool_use_id": "toolu_b", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "success": False, "subagent_id": "agent_b", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:30+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        stats = mod.aggregate_subagent_stats(events)
        assert stats["Explore"]["count"] == 2
        assert stats["Explore"]["failure_count"] == 2
        assert stats["Explore"]["failure_rate"] == 1.0

    def test_subagent_lifecycle_start_does_not_inflate_count(self, tmp_path):
        """SubagentStart 経由の subagent_lifecycle_start は count に入らない"""
        usage_file = tmp_path / "usage.jsonl"
        sample_events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "tool_use_id": "toolu_x", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:01+00:00"},
        ]
        write_events(usage_file, sample_events)
        mod = load_summary_module(usage_file)
        events = mod.load_events()
        stats = mod.aggregate_subagent_stats(events)
        assert stats["Explore"]["count"] == 1

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


def _session_start(session_id: str, project: str, ts: str) -> dict:
    return {"event_type": "session_start", "session_id": session_id,
            "project": project, "timestamp": ts}


def _au_event(session_id: str, project: str, ts: str, *,
               model: str = "claude-sonnet-4-6",
               in_t: int = 1000, out_t: int = 100,
               msg_id: str = "m") -> dict:
    return {
        "event_type": "assistant_usage",
        "session_id": session_id,
        "project": project,
        "timestamp": ts,
        "model": model,
        "input_tokens": in_t,
        "output_tokens": out_t,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "message_id": msg_id,
        "source": "main",
    }


def _session_with_usage(session_id: str, project: str, ts_start: str, ts_usage: str, *,
                         in_t: int = 1000, out_t: int = 100, msg_id: str = "m") -> list[dict]:
    """session_start + assistant_usage のペアを返す。"""
    return [
        _session_start(session_id, project, ts_start),
        _au_event(session_id, project, ts_usage, in_t=in_t, out_t=out_t, msg_id=msg_id),
    ]


class TestPrintReportIncludeCost:
    def _run(self, events, include_cost=True, tmp_path=None):
        import io
        import contextlib
        usage_file = (tmp_path or Path("/tmp")) / "usage.jsonl"
        write_events(usage_file, events)
        mod = load_summary_module(usage_file)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.print_report(events, include_cost=include_cost)
        return buf.getvalue(), mod

    def test_summary_include_cost_prints_total_line(self, tmp_path):
        events = _session_with_usage("s1", "proj", "2026-05-01T09:59:00+00:00",
                                     "2026-05-01T10:00:00+00:00", msg_id="m1")
        stdout, _ = self._run(events, tmp_path=tmp_path)
        assert "Total estimated cost: $" in stdout

    def test_summary_include_cost_prints_top10_header(self, tmp_path):
        events = _session_with_usage("s1", "proj", "2026-05-01T09:59:00+00:00",
                                     "2026-05-01T10:00:00+00:00", msg_id="m1")
        stdout, _ = self._run(events, tmp_path=tmp_path)
        assert "Top 10 sessions by estimated cost" in stdout

    def test_summary_include_cost_sorts_descending(self, tmp_path):
        events = (
            _session_with_usage("s1", "proj", "2026-05-01T09:59:00+00:00",
                                 "2026-05-01T10:00:00+00:00", in_t=10000, out_t=1000, msg_id="m1")
            + _session_with_usage("s2", "proj", "2026-05-01T10:00:00+00:00",
                                   "2026-05-01T10:01:00+00:00", in_t=100, out_t=10, msg_id="m2")
            + _session_with_usage("s3", "proj", "2026-05-01T10:01:00+00:00",
                                   "2026-05-01T10:02:00+00:00", in_t=5000, out_t=500, msg_id="m3")
        )
        stdout, _ = self._run(events, tmp_path=tmp_path)
        # Cost section のみ抽出して順序確認
        cost_section = stdout[stdout.find("=== Cost"):]
        idx_s1 = cost_section.find("s1")
        idx_s2 = cost_section.find("s2")
        idx_s3 = cost_section.find("s3")
        assert idx_s1 < idx_s3 < idx_s2

    def test_summary_include_cost_caps_at_10(self, tmp_path):
        events = []
        for i in range(11):
            events += _session_with_usage(
                f"session_{i:02d}", "proj",
                f"2026-05-01T09:{i:02d}:00+00:00",
                f"2026-05-01T10:{i:02d}:00+00:00",
                in_t=1000 - i * 10, out_t=100, msg_id=f"m{i}",
            )
        stdout, _ = self._run(events, tmp_path=tmp_path)
        # 11 sessions 中 top 10 のみ表示: "session_10" は最安なので除外される
        cost_section = stdout[stdout.find("Top 10 sessions"):]
        assert cost_section.count("session_") == 10

    def test_summary_include_cost_prints_disclaimer(self, tmp_path):
        events = _session_with_usage("s1", "proj", "2026-05-01T09:59:00+00:00",
                                     "2026-05-01T10:00:00+00:00", msg_id="m1")
        stdout, mod = self._run(events, tmp_path=tmp_path)
        assert mod._COST_DISCLAIMER in stdout

    def test_summary_without_include_cost_omits_cost_section(self, tmp_path):
        events = _session_with_usage("s1", "proj", "2026-05-01T09:59:00+00:00",
                                     "2026-05-01T10:00:00+00:00", msg_id="m1")
        stdout, mod = self._run(events, include_cost=False, tmp_path=tmp_path)
        assert "Total estimated cost" not in stdout
        assert "Top 10 sessions by estimated cost" not in stdout
        assert mod._COST_DISCLAIMER not in stdout

    def test_summary_include_cost_handles_empty_events(self, tmp_path):
        stdout, _ = self._run([], tmp_path=tmp_path)
        assert "Total estimated cost: $" in stdout
        assert "Top 10 sessions by estimated cost" in stdout
