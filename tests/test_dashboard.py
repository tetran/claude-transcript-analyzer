"""tests/test_dashboard.py — dashboard/server.py のテスト"""
# pylint: disable=line-too-long
import importlib.util
import json
import os
import socketserver
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
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

    def test_html_template_has_xss_escape_for_user_strings(self, tmp_path):
        """ユーザー入力文字列（skill 名 / プロジェクト名）は必ず esc() を通すこと。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        template = mod._HTML_TEMPLATE
        assert "function esc(s)" in template
        # ranking 行 + project legend で esc() 通過
        assert "esc(it.name)" in template
        assert "esc(p.project)" in template

    def test_ranking_uses_inline_gauge_layout(self, tmp_path):
        """ランキングは行内に gauge-bar を持つレイアウト（旧 v0.2 の固定幅 bar-track ではない）。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        template = mod._HTML_TEMPLATE
        assert "gauge-bar" in template
        assert "rank-row" in template
        assert "width: 130px" not in template

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


class TestGraphDataTooltip:
    """Issue #17: sparkline と project stack のデータポイントに floating tooltip を追加。

    - sparkline の各 dot に hover で {date} / {events} を表示
    - stack の各 seg / legend に hover で {project} / {count} / {percent} を表示
    - native title= 属性は廃止（OS tooltip との重複表示を避ける）
    - 既存の `?` ヘルプポップアップとは視覚的に区別される
    """

    def test_template_has_data_tooltip_element(self, tmp_path):
        """共有 floating tooltip 要素 (#dataTooltip) が body に存在する。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        template = mod._HTML_TEMPLATE
        assert 'id="dataTooltip"' in template

    def test_template_has_data_tooltip_css(self, tmp_path):
        """data-tip 用の CSS クラス（.data-tip）が定義されている。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        template = mod._HTML_TEMPLATE
        assert ".data-tip" in template
        # fixed positioning でマウス追従できる
        assert "position: fixed" in template

    def test_data_tooltip_visually_distinct_from_help_pop(self, tmp_path):
        """データ tooltip は help-pop と別クラスで定義され、視覚的に区別できる。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        template = mod._HTML_TEMPLATE
        # help-pop と data-tip は別の class 名
        assert ".data-tip" in template
        assert ".help-pop" in template
        # data-tip は値中心なので mono フォントを使う
        assert "data-tip" in template

    def test_sparkline_dots_have_data_attributes(self, tmp_path):
        """sparkline の dot に data-tip="daily" + data-d / data-c が付与される。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        template = mod._HTML_TEMPLATE
        # JS の dots 生成箇所で data-tip="daily" を埋め込んでいる
        assert 'data-tip="daily"' in template
        assert "data-d=" in template
        assert "data-c=" in template

    def test_project_stack_has_data_attributes(self, tmp_path):
        """stack seg / legend の各行に data-tip="proj" + data-p / data-c / data-pct が付与される。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        template = mod._HTML_TEMPLATE
        assert 'data-tip="proj"' in template
        assert "data-p=" in template
        assert "data-pct=" in template

    def test_no_native_title_on_project_stack_segments(self, tmp_path):
        """seg と legend pn の native title= は削除済み（floating tooltip に置き換え）。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        template = mod._HTML_TEMPLATE
        # 削除前は `<div class="seg" title=` / `<div class="pn" title=` を含んでいた
        assert '<div class="seg" title=' not in template
        assert "class=\"seg\" title=" not in template
        assert "class=\"pn\" title=" not in template

    def test_data_tooltip_handler_attached(self, tmp_path):
        """delegated mouseover ハンドラで data-tip 要素を捕捉している。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        template = mod._HTML_TEMPLATE
        # mouseover/mousemove/mouseout のいずれか + data-tip セレクタ
        assert "mouseover" in template or "mouseenter" in template
        assert "mousemove" in template
        assert "[data-tip]" in template

    def test_data_tooltip_has_aria_label_for_accessibility(self, tmp_path):
        """hover 対象要素に aria-label を付与（最低限のキーボード/SR 対応）。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        template = mod._HTML_TEMPLATE
        # ranking 系（既存の `title=` 付き）はスコープ外。daily / proj に aria-label を付ける
        assert "aria-label" in template


# ======================================================================
# Issue #19 Phase A — ライブダッシュボード基盤
# ======================================================================


def _start_server_in_thread(server) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


class TestThreadingServer:
    """Phase A: ThreadingHTTPServer 化 + 空きポート取得。"""

    def test_create_server_returns_threading_http_server(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        try:
            assert isinstance(server, ThreadingHTTPServer)
        finally:
            server.server_close()

    def test_create_server_with_port_zero_picks_free_port(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        try:
            actual = server.server_address[1]
            assert actual != 0
            assert 1024 <= actual <= 65535
        finally:
            server.server_close()

    def test_create_server_with_specific_port(self, tmp_path):
        """DASHBOARD_PORT 具体ポート指定時の互換: bind に成功する。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        # 一時 bind して空きポートを得てから即解放、そのポートで create_server
        with socketserver.TCPServer(("127.0.0.1", 0), socketserver.BaseRequestHandler) as probe:
            free_port = probe.server_address[1]
        server = mod.create_server(port=free_port, idle_seconds=0)
        try:
            assert server.server_address[1] == free_port
        finally:
            server.server_close()

    def test_init_failure_does_not_mask_original_error_with_attribute_error(self, tmp_path):
        """codex P1 回帰: bind 失敗時、親 TCPServer の `try/except: self.server_close()` 経路で
        override した server_close() が走る。`_stop_event` が super().__init__() より後に初期化
        されていると AttributeError で本来の OSError をマスクする。
        `_stop_event` は super().__init__() より前に作る必要がある。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        # 占有プロセスを建ててから同じポートで生成 → bind 失敗を誘発
        occupier = mod.create_server(port=0, idle_seconds=0)
        busy_port = occupier.server_address[1]
        try:
            try:
                mod.create_server(port=busy_port, idle_seconds=0)
            except BaseException as exc:  # pylint: disable=broad-except
                # AttributeError でマスクされていないことを保証
                assert not isinstance(exc, AttributeError), (
                    f"bind 失敗が AttributeError でマスクされている: {exc!r}"
                )
                # 本来は OSError (EADDRINUSE) が出る
                assert isinstance(exc, OSError), f"想定外の例外型: {type(exc).__name__}: {exc}"
            else:
                # bind が成功してしまった環境（OS によっては SO_REUSEADDR で許される）
                # 本テストの目的は「マスクしない」検証なので skip 相当の no-op
                pass
        finally:
            occupier.server_close()

    def test_concurrent_requests_processed(self, tmp_path):
        """ThreadingHTTPServer なので、ハンドラが少しブロックしても同時に複数リクエストを返せる。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        port = server.server_address[1]
        _start_server_in_thread(server)
        try:
            results: list[int] = []
            errors: list[BaseException] = []

            def hit():
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as resp:
                        results.append(resp.status)
                except BaseException as exc:  # pylint: disable=broad-except
                    errors.append(exc)

            threads = [threading.Thread(target=hit) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)
            assert errors == []
            assert results == [200, 200, 200, 200, 200]
        finally:
            server.shutdown()
            server.server_close()


class TestHealthzEndpoint:
    """Phase A: /healthz が `200 OK` + `{"status":"ok","started_at":...}` を返す。"""

    def test_healthz_returns_200_and_json(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        port = server.server_address[1]
        _start_server_in_thread(server)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz") as resp:
                assert resp.status == 200
                assert "application/json" in resp.headers["Content-Type"]
                payload = json.loads(resp.read())
                assert payload["status"] == "ok"
                # started_at は ISO8601 文字列
                assert isinstance(payload["started_at"], str)
                assert payload["started_at"] == server.started_at
        finally:
            server.shutdown()
            server.server_close()


class TestServerJsonLifecycle:
    """Phase A: server.json を atomic write し、停止時に削除する。"""

    def test_write_server_json_creates_file_with_required_fields(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        info = {
            "pid": 12345,
            "port": 53412,
            "url": "http://localhost:53412",
            "started_at": "2026-04-27T10:00:00+00:00",
        }
        mod.write_server_json(target, info)
        assert target.exists()
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == info

    def test_write_server_json_uses_atomic_replace(self, tmp_path, monkeypatch):
        """tmp に書いて os.replace で原子性を確保する実装になっていること。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"

        replace_calls: list[tuple[str, str]] = []
        original_replace = os.replace

        def spy_replace(src, dst):
            replace_calls.append((str(src), str(dst)))
            return original_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy_replace)
        info = {"pid": 1, "port": 1, "url": "u", "started_at": "t"}
        mod.write_server_json(target, info)
        assert len(replace_calls) == 1
        src, dst = replace_calls[0]
        assert dst == str(target)
        # tmp ファイルは別パスで、replace 後に target だけ残る
        assert src != dst
        assert not Path(src).exists()

    def test_write_server_json_creates_parent_directories(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "nested" / "dir" / "server.json"
        mod.write_server_json(target, {"pid": 1, "port": 1, "url": "u", "started_at": "t"})
        assert target.exists()

    def test_remove_server_json_idempotent(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        # ファイル不在時もエラーにならない
        mod.remove_server_json(target)
        target.write_text("{}", encoding="utf-8")
        mod.remove_server_json(target)
        assert not target.exists()
        # 削除後の二重呼び出しもエラーにならない
        mod.remove_server_json(target)

    def test_remove_server_json_compare_and_delete_matches_pid(self, tmp_path):
        """expected_pid が一致するとき削除する。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        target.write_text(json.dumps({"pid": 4242, "port": 1, "url": "u", "started_at": "t"}))
        removed = mod.remove_server_json(target, expected_pid=4242)
        assert removed is True
        assert not target.exists()

    def test_remove_server_json_compare_and_delete_preserves_other_pid(self, tmp_path):
        """別プロセスが上書きした server.json を消さない（多重インスタンス保護）。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        target.write_text(json.dumps({"pid": 9999, "port": 1, "url": "u", "started_at": "t"}))
        removed = mod.remove_server_json(target, expected_pid=4242)
        assert removed is False
        assert target.exists()
        # 中身は元のまま
        assert json.loads(target.read_text())["pid"] == 9999

    def test_remove_server_json_compare_and_delete_handles_invalid_json(self, tmp_path):
        """壊れた JSON のときは消さない（誰かが書き換え中の可能性）。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        target.write_text("not valid json")
        removed = mod.remove_server_json(target, expected_pid=4242)
        assert removed is False
        assert target.exists()

    def test_remove_server_json_compare_and_delete_handles_missing_file(self, tmp_path):
        """ファイル不在でもエラーにならず False を返す。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        target = tmp_path / "server.json"
        removed = mod.remove_server_json(target, expected_pid=4242)
        assert removed is False


class TestIdleWatchdog:
    """Phase A: idle 経過で graceful shutdown / 0 で無効化 / リクエストで idle カウンタ reset。"""

    def test_idle_for_returns_elapsed_seconds(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        try:
            time.sleep(0.05)
            elapsed = server.idle_for()
            assert elapsed >= 0.04
            assert elapsed < 1.0
        finally:
            server.server_close()

    def test_request_resets_idle_counter(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        port = server.server_address[1]
        _start_server_in_thread(server)
        try:
            time.sleep(0.1)
            assert server.idle_for() >= 0.09
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz") as resp:
                resp.read()
            # リクエスト直後は idle はほぼ 0
            assert server.idle_for() < 0.05
        finally:
            server.shutdown()
            server.server_close()

    def test_watchdog_shuts_down_after_idle(self, tmp_path):
        """idle_seconds 経過で server がシャットダウンする。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0.2)
        t = _start_server_in_thread(server)
        try:
            t.join(timeout=3.0)
            assert not t.is_alive(), "watchdog で serve_forever が exit していない"
        finally:
            server.server_close()

    def test_watchdog_disabled_when_idle_seconds_zero(self, tmp_path):
        """idle_seconds=0 で watchdog は起動せず、外部 shutdown まで生き続ける。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        t = _start_server_in_thread(server)
        try:
            time.sleep(0.5)
            assert t.is_alive(), "idle_seconds=0 なのに自動停止した"
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=2.0)


class TestRunIntegration:
    """Phase A: run() が server.json の write/remove を結線する。"""

    def test_run_writes_server_json_with_pid_port_url(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        target = tmp_path / "server.json"

        ready = threading.Event()

        def runner():
            mod.run(server, target, install_signals=False, on_ready=ready.set)

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        try:
            assert ready.wait(timeout=2.0), "run() が ready シグナルを発火しなかった"
            # server.json に required fields が揃っている
            info = json.loads(target.read_text(encoding="utf-8"))
            assert info["pid"] == os.getpid()
            assert info["port"] == server.server_address[1]
            assert info["url"] == f"http://localhost:{server.server_address[1]}"
            assert info["started_at"] == server.started_at
        finally:
            server.shutdown()
            t.join(timeout=2.0)

    def test_run_does_not_remove_server_json_overwritten_by_another_instance(self, tmp_path):
        """codex P2 回帰: A が起動 → B が同じ path に server.json を上書き →
        A が exit しても B のレジストリは残る (compare-and-delete)。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        target = tmp_path / "server.json"

        ready = threading.Event()

        def runner():
            mod.run(server, target, install_signals=False, on_ready=ready.set)

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        try:
            assert ready.wait(timeout=2.0)
            # 別インスタンス B が同じ path に自分の server.json を被せる
            other_pid = os.getpid() + 1
            target.write_text(
                json.dumps({"pid": other_pid, "port": 99999, "url": "http://x", "started_at": "t"}),
                encoding="utf-8",
            )
        finally:
            server.shutdown()
            t.join(timeout=2.0)
        # A exit 後でも server.json は B のものとして残る
        assert target.exists(), "別インスタンスのレジストリを誤って削除した"
        info = json.loads(target.read_text(encoding="utf-8"))
        assert info["pid"] == other_pid

    def test_run_removes_server_json_on_shutdown(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        target = tmp_path / "server.json"

        ready = threading.Event()

        def runner():
            mod.run(server, target, install_signals=False, on_ready=ready.set)

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        try:
            assert ready.wait(timeout=2.0)
            assert target.exists()
        finally:
            server.shutdown()
            t.join(timeout=2.0)
        # serve_forever が exit した後、server.json は削除されている
        assert not target.exists()
