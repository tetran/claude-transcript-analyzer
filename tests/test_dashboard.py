"""tests/test_dashboard.py — dashboard/server.py のテスト"""
# pylint: disable=line-too-long
import importlib.util
import json
import os
import socketserver
import threading
import urllib.request
from pathlib import Path

_DASHBOARD_PATH = Path(__file__).parent.parent / "dashboard" / "server.py"


def load_dashboard_module(usage_jsonl: Path, alerts_jsonl: Path | None = None):
    """USAGE_JSONL をパッチした状態で dashboard モジュールを読み込む。"""
    os.environ["USAGE_JSONL"] = str(usage_jsonl)
    if alerts_jsonl is not None:
        os.environ["HEALTH_ALERTS_JSONL"] = str(alerts_jsonl)
    try:
        spec = importlib.util.spec_from_file_location("dashboard_server", _DASHBOARD_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del os.environ["USAGE_JSONL"]
        if alerts_jsonl is not None:
            del os.environ["HEALTH_ALERTS_JSONL"]
    return mod


def write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


class TestBuildDashboardData:
    def test_empty_events_returns_valid_structure(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        data = mod.build_dashboard_data([])
        assert data["total_events"] == 0
        assert data["skill_ranking"] == []
        assert data["subagent_ranking"] == []
        assert data["daily_trend"] == []
        assert data["project_breakdown"] == []
        assert "last_updated" in data

    def test_total_events_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert data["total_events"] == 2

    def test_total_events_excludes_session_housekeeping_events(self, tmp_path):
        """Codex round 8 P2: total_events / daily_trend / project_breakdown は
        usage 系 (skill_tool / user_slash_command / subagent_start) のみで集計し、
        session_start / notification / instructions_loaded / compact_* / subagent_stop は除外する。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            # 以下はハウスキーピング系で集計対象外
            {"event_type": "session_start", "source": "startup", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:01+00:00"},
            {"event_type": "notification", "notification_type": "idle", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:02+00:00"},
            {"event_type": "instructions_loaded", "file_path": "/x", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:03+00:00"},
            {"event_type": "compact_start", "trigger": "auto", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:04+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:05+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert data["total_events"] == 1
        assert data["daily_trend"] == [{"date": "2026-01-01", "count": 1}]
        assert data["project_breakdown"] == [{"project": "p", "count": 1}]

    def test_total_events_excludes_lifecycle_start_to_avoid_double_count(self, tmp_path):
        """Codex round 9 P2: subagent_lifecycle_start は subagent_start (PostToolUse 由来) と
        ペアで発火しうるため、total_events / daily_trend / project_breakdown では除外する。
        集計の正規ソースは subagent_start のみ。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            # 1 invocation で両方の hook が発火（実機の標準動作）
            {"event_type": "subagent_start", "subagent_type": "Explore", "tool_use_id": "toolu_a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00.500000+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        # subagent_ranking は invocation 単位 dedup で 1
        item = next(i for i in data["subagent_ranking"] if i["name"] == "Explore")
        assert item["count"] == 1
        # total_events / daily / projects も 1 で揃える（lifecycle_start を二重カウントしない）
        assert data["total_events"] == 1
        assert data["daily_trend"] == [{"date": "2026-01-01", "count": 1}]
        assert data["project_breakdown"] == [{"project": "p", "count": 1}]

    def test_total_events_includes_lifecycle_only_invocations(self, tmp_path):
        """Codex round 10 P2: PostToolUse(Task|Agent) が発火しない環境では subagent_lifecycle_start が
        invocation を表すため、total_events / daily_trend / project_breakdown にも 1 件として
        反映する（subagent_ranking と headline メトリクスを一致させる）。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = next(i for i in data["subagent_ranking"] if i["name"] == "Explore")
        assert item["count"] == 2
        # ranking と総数・日別・プロジェクト別が一致すること
        assert data["total_events"] == 2
        assert data["daily_trend"] == [{"date": "2026-01-01", "count": 2}]
        assert data["project_breakdown"] == [{"project": "p", "count": 2}]

    def test_total_events_disjoint_start_fail_plus_lifecycle_only(self, tmp_path):
        """Codex round 10 P2: invocation A (start fail のみ) と invocation B (lifecycle のみ) が
        独立に存在するケースで、headline メトリクスにも 2 invocation として反映する。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            # invocation A: PostToolUse 起動失敗、SubagentStop なし
            {"event_type": "subagent_start", "subagent_type": "Explore", "success": False, "tool_use_id": "toolu_a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            # invocation B: PostToolUse 発火せず lifecycle のみ → SubagentStop で成功
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:05+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "success": True, "duration_ms": 30000, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:35+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = next(i for i in data["subagent_ranking"] if i["name"] == "Explore")
        assert item["count"] == 2
        assert data["total_events"] == 2
        assert data["daily_trend"] == [{"date": "2026-01-01", "count": 2}]
        assert data["project_breakdown"] == [{"project": "p", "count": 2}]

    def test_skill_ranking_sorted_by_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "commit", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "commit", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "skill_tool", "skill": "review", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:02:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert data["skill_ranking"][0]["name"] == "commit"
        assert data["skill_ranking"][0]["count"] == 2
        assert data["skill_ranking"][1]["name"] == "review"
        assert data["skill_ranking"][1]["count"] == 1

    def test_skill_ranking_includes_user_slash_command(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "user_slash_command", "skill": "/insights", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "commit", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        names = [r["name"] for r in data["skill_ranking"]]
        assert "/insights" in names
        assert "commit" in names

    def test_subagent_ranking_sorted_by_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Plan", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:02:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert data["subagent_ranking"][0]["name"] == "Explore"
        assert data["subagent_ranking"][0]["count"] == 2
        assert data["subagent_ranking"][1]["name"] == "Plan"
        assert data["subagent_ranking"][1]["count"] == 1

    def test_daily_trend_grouped_by_date_sorted_desc(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s", "timestamp": "2026-01-02T10:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "b", "project": "p", "session_id": "s", "timestamp": "2026-01-01T15:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "c", "project": "p", "session_id": "s", "timestamp": "2026-01-01T10:00:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert data["daily_trend"][0] == {"date": "2026-01-02", "count": 1}
        assert data["daily_trend"][1] == {"date": "2026-01-01", "count": 2}

    def test_daily_trend_excludes_events_without_timestamp(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "b", "project": "p", "session_id": "s"},
        ]
        data = mod.build_dashboard_data(events)
        assert len(data["daily_trend"]) == 1
        assert data["daily_trend"][0]["count"] == 1

    def test_health_alerts_empty_when_no_file(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl",
                                    alerts_jsonl=tmp_path / "nonexistent_alerts.jsonl")
        data = mod.build_dashboard_data([])
        assert "health_alerts" in data
        assert data["health_alerts"] == []

    def test_health_alerts_returned_in_data(self, tmp_path):
        alerts_file = tmp_path / "health_alerts.jsonl"
        alert = {
            "timestamp": "2026-03-01T10:00:00+00:00",
            "session_id": "sess-abc",
            "missing_count": 3,
            "missing_types": ["subagent_start"],
        }
        alerts_file.write_text(json.dumps(alert) + "\n")

        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl",
                                    alerts_jsonl=alerts_file)
        data = mod.build_dashboard_data([])
        assert len(data["health_alerts"]) == 1
        assert data["health_alerts"][0]["session_id"] == "sess-abc"
        assert data["health_alerts"][0]["missing_count"] == 3

    def test_health_alerts_capped_at_max(self, tmp_path):
        """アラートが MAX_ALERTS(50) 件を超えたとき最新50件のみ返す"""
        alerts_file = tmp_path / "health_alerts.jsonl"
        alerts = [
            {"timestamp": f"2026-03-{i:02d}T10:00:00+00:00",
             "session_id": f"sess-{i:03d}",
             "missing_count": 1,
             "missing_types": ["subagent_start"]}
            for i in range(1, 61)  # 60件書く
        ]
        alerts_file.write_text("\n".join(json.dumps(a) for a in alerts) + "\n")

        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl",
                                    alerts_jsonl=alerts_file)
        data = mod.build_dashboard_data([])
        # 最新50件のみ（末尾50件 = sess-011 〜 sess-060）
        assert len(data["health_alerts"]) == 50
        assert data["health_alerts"][0]["session_id"] == "sess-011"
        assert data["health_alerts"][-1]["session_id"] == "sess-060"

    def test_html_template_has_xss_escape_for_bar_labels(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        template = mod._HTML_TEMPLATE
        assert "function esc(s)" in template
        assert "esc(item[nameKey])" in template

    def test_bar_chart_uses_stacked_layout(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        assert "bar-track-row" in mod._HTML_TEMPLATE
        assert "width: 130px" not in mod._HTML_TEMPLATE

    def test_project_breakdown_sorted_by_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "a", "project": "proj-a", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "b", "project": "proj-a", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "proj-b", "session_id": "s", "timestamp": "2026-01-01T00:02:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert data["project_breakdown"][0] == {"project": "proj-a", "count": 2}
        assert data["project_breakdown"][1] == {"project": "proj-b", "count": 1}


class TestSkillFailureStats:
    """Issue #8: skill_ranking に failure_count / failure_rate を含める"""

    def test_skill_ranking_includes_failure_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "commit", "success": True, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "commit", "success": False, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "skill_tool", "skill": "commit", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:02:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["skill_ranking"][0]
        assert item["name"] == "commit"
        assert item["count"] == 3
        assert item["failure_count"] == 1
        assert abs(item["failure_rate"] - (1 / 3)) < 1e-9

    def test_skill_ranking_failure_zero_when_no_failures(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "review", "success": True, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["skill_ranking"][0]
        assert item["failure_count"] == 0
        assert item["failure_rate"] == 0.0

    def test_user_slash_command_excluded_from_failure_stats(self, tmp_path):
        """user_slash_command は呼出元（ユーザー入力）なので failure 集計対象外"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "user_slash_command", "skill": "/insights", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["skill_ranking"][0]
        assert item["count"] == 1
        assert item["failure_count"] == 0


class TestSubagentFailureAndDurationStats:
    """Issue #8: subagent_ranking に failure_count / failure_rate / avg_duration_ms を含める"""

    def test_subagent_ranking_includes_failure_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "success": True, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Explore", "success": False, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        assert item["name"] == "Explore"
        assert item["count"] == 2
        assert item["failure_count"] == 1
        assert abs(item["failure_rate"] - 0.5) < 1e-9

    def test_subagent_ranking_avg_duration_from_subagent_stop(self, tmp_path):
        """subagent_stop の duration_ms を優先して平均を取る"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "duration_ms": 1000, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:10+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "duration_ms": 3000, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:20+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        assert item["avg_duration_ms"] == 2000.0

    def test_subagent_ranking_avg_duration_falls_back_to_start(self, tmp_path):
        """subagent_stop に duration_ms が無い場合は subagent_start の duration_ms を使う"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_start", "subagent_type": "Plan", "duration_ms": 500, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Plan", "duration_ms": 1500, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        assert item["avg_duration_ms"] == 1000.0

    def test_subagent_ranking_avg_duration_none_when_unavailable(self, tmp_path):
        """duration_ms が一切無いときは None"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        assert item["avg_duration_ms"] is None

    def test_subagent_lifecycle_start_does_not_inflate_count(self, tmp_path):
        """SubagentStart 経由の subagent_lifecycle_start は count に入らない（観測点一本化）"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "tool_use_id": "toolu_x", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:01+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        assert item["count"] == 1

    def test_subagent_stop_does_not_inflate_count(self, tmp_path):
        """subagent_stop は count（起動回数）を増やさない"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "duration_ms": 1000, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:10+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        assert item["count"] == 1

    def test_failure_count_capped_by_count_when_both_events_fail(self, tmp_path):
        """1 invocation の起動失敗 (PostToolUseFailure) と実行失敗 (SubagentStop) が
        重複しても failure_rate は 100% を超えない（count cap）"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "success": False, "tool_use_id": "toolu_x", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "success": False, "subagent_id": "agent_x", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:10+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        assert item["count"] == 1
        assert item["failure_count"] == 1
        assert item["failure_rate"] == 1.0

    def test_failures_from_distinct_invocations_summed(self, tmp_path):
        """別 invocation の失敗を区別: 起動失敗 1 件 + 別 invocation の実行失敗 1 件 = 計 2 件"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            # invocation A: 起動失敗
            {"event_type": "subagent_start", "subagent_type": "Explore", "success": False, "tool_use_id": "toolu_a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            # invocation B: 起動成功 → 実行失敗
            {"event_type": "subagent_start", "subagent_type": "Explore", "tool_use_id": "toolu_b", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "success": False, "subagent_id": "agent_b", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:30+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        assert item["count"] == 2
        assert item["failure_count"] == 2
        assert item["failure_rate"] == 1.0

    def test_duration_fallback_is_per_invocation_not_per_type(self, tmp_path):
        """Codex round 5 P2: 同じ subagent_type で stop ありと stop 無しが混在しても
        stop 無し invocation の start.duration_ms を捨てない。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            # invocation A: stop 有り（end-to-end 1000ms）
            {"event_type": "subagent_start", "subagent_type": "Explore", "duration_ms": 50, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "duration_ms": 1000, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:01+00:00"},
            # invocation B: stop 無し（start.duration_ms 3000ms にフォールバック）
            {"event_type": "subagent_start", "subagent_type": "Explore", "duration_ms": 3000, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:02+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        # per-invocation fallback: A=1000 (stop), B=3000 (start) → avg 2000
        # 旧実装の type 単位 or fallback だと A の stop だけが残り B の start を捨てる → 1000 になる
        assert item["avg_duration_ms"] == 2000.0

    def test_count_falls_back_to_lifecycle_start_when_post_tool_use_missing(self, tmp_path):
        """Codex round 5 P1 (Finding 1): PostToolUse(Task|Agent) が発火しない環境では
        SubagentStart 由来の subagent_lifecycle_start を count にフォールバックさせる。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert any(item["name"] == "Explore" for item in data["subagent_ranking"])
        item = next(i for i in data["subagent_ranking"] if i["name"] == "Explore")
        assert item["count"] == 2

    def test_count_does_not_double_count_when_both_subagent_start_and_lifecycle_start_present(self, tmp_path):
        """両方発火する環境では PostToolUse 経由の subagent_start を優先（count = max）"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "tool_use_id": "toolu_a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:01+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        assert item["count"] == 1

    def test_count_uses_invocation_merge_when_post_tool_use_partial_in_bucket(self, tmp_path):
        """Codex round 6/7 P1: PostToolUse が flaky な場合、timestamp 近接の start+lifecycle は
        同一 invocation、離れた lifecycle は別 invocation として数える。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            # invocation A: PostToolUse + SubagentStart 両方発火（1 秒以内 → 同一 invocation 扱い）
            {"event_type": "subagent_start", "subagent_type": "Explore", "tool_use_id": "toolu_a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00.500000+00:00"},
            # invocation B: PostToolUse 発火せず lifecycle のみ
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        # invocation A (start + lifecycle 同一) + invocation B (lifecycle 単独) = 2
        assert item["count"] == 2

    def test_disjoint_start_and_lifecycle_are_separate_invocations(self, tmp_path):
        """Codex round 7 P1: 同 bucket で start のみ invocation と lifecycle のみ invocation が
        時系列で独立に存在する場合、max(1,1)=1 で潰さず 2 invocation として数える"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            # invocation A: PostToolUse 発火、起動失敗 → SubagentStop なし
            {"event_type": "subagent_start", "subagent_type": "Explore", "success": False, "tool_use_id": "toolu_a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            # invocation B: PostToolUse 発火せず、SubagentStart 経由のみ → SubagentStop で成功 30 秒
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:05+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "success": True, "duration_ms": 30000, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:35+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        assert item["count"] == 2
        assert item["failure_count"] == 1
        assert abs(item["failure_rate"] - 0.5) < 1e-9
        assert item["avg_duration_ms"] == 30000.0

    def test_lifecycle_only_bucket_picks_up_stop_failure_and_duration(self, tmp_path):
        """Codex round 6 P1-B: lifecycle-only バケットでも stop の success/duration を集計する"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "success": False, "duration_ms": 1000, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:10+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        assert item["count"] == 1
        assert item["failure_count"] == 1
        assert item["avg_duration_ms"] == 1000.0

    def test_failure_count_invocation_pairing_mixed_mode(self, tmp_path):
        """Codex round 4 P1: mixed mode で invocation 単位 dedup が効く。
        3 invocations:
        - A: start fail + stop fail（同 invocation の重複発火）
        - B: stop fail のみ
        - C: 成功
        真の failed invocations = 2 (A と B)"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            # A: 起動失敗 + その後 stop も failure（仕様外だが防御）
            {"event_type": "subagent_start", "subagent_type": "Plan", "success": False, "tool_use_id": "toolu_a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Plan", "success": False, "subagent_id": "agent_a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:10+00:00"},
            # B: 起動成功 → 実行失敗
            {"event_type": "subagent_start", "subagent_type": "Plan", "tool_use_id": "toolu_b", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Plan", "success": False, "subagent_id": "agent_b", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:30+00:00"},
            # C: 起動成功 → 実行成功
            {"event_type": "subagent_start", "subagent_type": "Plan", "tool_use_id": "toolu_c", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:02:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Plan", "success": True, "subagent_id": "agent_c", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:02:30+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        assert item["count"] == 3
        assert item["failure_count"] == 2
        assert abs(item["failure_rate"] - (2 / 3)) < 1e-9

    def test_subagent_stop_failure_counted(self, tmp_path):
        """subagent_stop の success: false も failure_count に加算する"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "success": False, "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:10+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        item = data["subagent_ranking"][0]
        assert item["count"] == 1
        assert item["failure_count"] == 1


class TestSessionStats:
    """Issue #9: セッション・コンテキスト・摩擦サマリ"""

    def test_session_stats_counts_sessions(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "session_start", "source": "startup", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "session_start", "source": "resume", "session_id": "s2", "project": "p", "timestamp": "2026-01-02T00:00:00+00:00"},
            {"event_type": "session_start", "source": "resume", "session_id": "s3", "project": "p", "timestamp": "2026-01-03T00:00:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        s = data["session_stats"]
        assert s["total_sessions"] == 3
        assert s["resume_count"] == 2
        assert abs(s["resume_rate"] - (2 / 3)) < 1e-9

    def test_session_stats_zero_when_no_sessions(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        data = mod.build_dashboard_data([])
        s = data["session_stats"]
        assert s["total_sessions"] == 0
        assert s["resume_count"] == 0
        assert s["resume_rate"] == 0.0
        assert s["compact_count"] == 0
        assert s["permission_prompt_count"] == 0

    def test_session_stats_compact_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "compact_start", "trigger": "auto", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "compact_end", "trigger": "auto", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T00:00:10+00:00"},
            {"event_type": "compact_start", "trigger": "manual", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T01:00:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        # compact_start のみカウント（pair で重複しない）
        assert data["session_stats"]["compact_count"] == 2

    def test_session_stats_permission_prompt_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "notification", "notification_type": "permission_prompt", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "notification", "notification_type": "idle_prompt", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "notification", "notification_type": "permission_prompt", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T00:02:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        # permission_prompt のみカウント
        assert data["session_stats"]["permission_prompt_count"] == 2

    def test_session_stats_permission_prompt_accepts_short_form(self, tmp_path):
        """公式 hooks 仕様の短縮形 'permission' も permission_prompt と同じくカウント対象"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "notification", "notification_type": "permission", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "notification", "notification_type": "permission_prompt", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "notification", "notification_type": "idle", "session_id": "s1", "project": "p", "timestamp": "2026-01-01T00:02:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert data["session_stats"]["permission_prompt_count"] == 2


class TestHTTPEndpoints:
    def _start_server(self, mod):
        server = socketserver.TCPServer(("127.0.0.1", 0), mod.DashboardHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever)
        t.daemon = True
        t.start()
        return server, port

    def test_api_data_returns_json_with_correct_structure(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        write_events(usage_file, [
            {"event_type": "skill_tool", "skill": "commit", "project": "proj", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
        ])
        mod = load_dashboard_module(usage_file)
        server, port = self._start_server(mod)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/data") as resp:
                assert resp.status == 200
                assert "application/json" in resp.headers["Content-Type"]
                data = json.loads(resp.read())
                assert data["total_events"] == 1
                assert data["skill_ranking"][0]["name"] == "commit"
        finally:
            server.shutdown()
            server.server_close()

    def test_api_data_empty_when_no_file(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server, port = self._start_server(mod)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/data") as resp:
                assert resp.status == 200
                data = json.loads(resp.read())
                assert data["total_events"] == 0
        finally:
            server.shutdown()
            server.server_close()

    def test_api_data_skips_invalid_json_lines(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text(
            '{"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"}\n'
            "not valid json\n"
            '{"event_type": "skill_tool", "skill": "b", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"}\n'
        )
        mod = load_dashboard_module(usage_file)
        server, port = self._start_server(mod)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/data") as resp:
                assert resp.status == 200
                data = json.loads(resp.read())
                assert data["total_events"] == 2
                names = [r["name"] for r in data["skill_ranking"]]
                assert "a" in names
                assert "b" in names
        finally:
            server.shutdown()
            server.server_close()

    def test_html_endpoint_returns_200(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server, port = self._start_server(mod)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
                assert resp.status == 200
                assert "text/html" in resp.headers["Content-Type"]
                body = resp.read().decode("utf-8")
                assert "<!DOCTYPE html>" in body
        finally:
            server.shutdown()
            server.server_close()

    def test_html_endpoint_handles_any_path(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server, port = self._start_server(mod)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/unknown") as resp:
                assert resp.status == 200
                assert "text/html" in resp.headers["Content-Type"]
        finally:
            server.shutdown()
            server.server_close()
