"""tests/test_dashboard_sessions_api.py — `/api/data` の `session_breakdown`
field の API 統合テスト (Issue #99 / v0.8.0)。

per-aggregator unit test は `tests/test_cost_metrics.py::TestAggregateSessionBreakdown`
側で network。本ファイルは `build_dashboard_data` 経由で:
- field が response に出る
- period toggle (Issue #85) との整合
- subagent_count drift guard (cross-aggregator invariant)
を pin する。
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DASH_DIR = ROOT / "dashboard"
if str(DASH_DIR) not in sys.path:
    sys.path.insert(0, str(DASH_DIR))


def _au(session_id, project, ts, model="claude-sonnet-4-6", in_t=100, out_t=50,
        cr=0, cc=0, msg_id=None, tier=None, source="main"):
    ev = {
        "event_type": "assistant_usage",
        "session_id": session_id,
        "project": project,
        "timestamp": ts,
        "model": model,
        "input_tokens": in_t,
        "output_tokens": out_t,
        "cache_read_tokens": cr,
        "cache_creation_tokens": cc,
        "message_id": msg_id or f"m_{session_id}_{ts}",
        "source": source,
    }
    if tier is not None:
        ev["service_tier"] = tier
    return ev


def _session_start(sid, project, ts):
    return {"event_type": "session_start", "session_id": sid, "project": project,
            "timestamp": ts, "source": "startup", "model": "claude-opus-4-7"}


def _session_end(sid, project, ts, reason="logout"):
    return {"event_type": "session_end", "session_id": sid, "project": project,
            "timestamp": ts, "reason": reason}


class TestSessionBreakdown(unittest.TestCase):
    def test_session_breakdown_field_present(self):
        from server import build_dashboard_data
        events = [
            _session_start("s1", "p", "2026-05-01T10:00:00+00:00"),
            _au("s1", "p", "2026-05-01T10:05:00+00:00"),
        ]
        result = build_dashboard_data(events)
        self.assertIn("session_breakdown", result)
        self.assertIsInstance(result["session_breakdown"], list)

    def test_per_session_shape(self):
        from server import build_dashboard_data
        events = [
            _session_start("s1", "proj-a", "2026-05-01T10:00:00+00:00"),
            _session_end("s1", "proj-a", "2026-05-01T11:00:00+00:00"),
            _au("s1", "proj-a", "2026-05-01T10:05:00+00:00",
                in_t=1000, out_t=500, cr=200, cc=100, tier="standard"),
            {"event_type": "skill_tool", "session_id": "s1", "project": "proj-a",
             "timestamp": "2026-05-01T10:10:00+00:00", "skill": "x"},
        ]
        result = build_dashboard_data(events)
        sb = result["session_breakdown"]
        self.assertEqual(len(sb), 1)
        row = sb[0]
        for key in ("session_id", "project", "started_at", "ended_at",
                    "duration_seconds", "models", "tokens",
                    "estimated_cost_usd", "service_tier_breakdown",
                    "skill_count", "subagent_count"):
            self.assertIn(key, row, f"missing key: {key}")
        self.assertEqual(row["session_id"], "s1")
        self.assertEqual(row["project"], "proj-a")
        self.assertEqual(row["models"], {"claude-sonnet-4-6": 1})
        self.assertEqual(row["tokens"], {
            "input": 1000, "output": 500, "cache_read": 200, "cache_creation": 100,
        })
        self.assertEqual(row["service_tier_breakdown"], {"standard": 1})
        self.assertEqual(row["skill_count"], 1)
        self.assertEqual(row["subagent_count"], 0)

    def test_sort_by_started_at_desc(self):
        from server import build_dashboard_data
        # Issue #109: session_breakdown は assistant_usage を持つ session のみ
        events = [
            _session_start("s_old", "p", "2026-05-01T10:00:00+00:00"),
            _au("s_old", "p", "2026-05-01T10:05:00+00:00"),
            _session_start("s_new", "p", "2026-05-02T10:00:00+00:00"),
            _au("s_new", "p", "2026-05-02T10:05:00+00:00"),
        ]
        result = build_dashboard_data(events)
        ids = [r["session_id"] for r in result["session_breakdown"]]
        self.assertEqual(ids, ["s_new", "s_old"])

    def test_top_n_cap(self):
        from server import build_dashboard_data
        from cost_metrics import TOP_N_SESSIONS
        # Issue #109: 各 session に assistant_usage を 1 件付けて render 対象にする
        events = []
        for i in range(TOP_N_SESSIONS + 5):
            sid = f"s{i:02d}"
            events.append(_session_start(sid, "p", f"2026-05-{i+1:02d}T10:00:00+00:00"))
            events.append(_au(sid, "p", f"2026-05-{i+1:02d}T10:05:00+00:00"))
        result = build_dashboard_data(events)
        self.assertEqual(len(result["session_breakdown"]), TOP_N_SESSIONS)

    def test_active_session_has_null_end(self):
        from server import build_dashboard_data
        events = [
            _session_start("s1", "p", "2026-05-01T10:00:00+00:00"),
            _au("s1", "p", "2026-05-01T10:05:00+00:00"),
            # session_end なし
        ]
        result = build_dashboard_data(events)
        row = result["session_breakdown"][0]
        self.assertIsNone(row["ended_at"])
        self.assertIsNone(row["duration_seconds"])

    def test_unknown_model_attributed_to_sonnet_fallback(self):
        from server import build_dashboard_data
        events = [
            _session_start("s1", "p", "2026-05-01T10:00:00+00:00"),
            _au("s1", "p", "2026-05-01T10:05:00+00:00",
                model="claude-future-99", in_t=1_000_000, out_t=0),
        ]
        result = build_dashboard_data(events)
        # Sonnet fallback → 1M × $3 = $3.0
        self.assertEqual(result["session_breakdown"][0]["estimated_cost_usd"], 3.0)

    def test_empty_events_returns_empty_list(self):
        from server import build_dashboard_data
        result = build_dashboard_data([])
        self.assertEqual(result["session_breakdown"], [])

    def test_session_subagent_count_matches_metrics(self):
        """drift guard: session 単位 subagent_count の合計 ==
        aggregate_subagent_metrics の type 軸合計 (cross-aggregator invariant)。
        Issue #109: session_breakdown render 対象に出すため各 session に
        assistant_usage を 1 件付ける。
        """
        from server import build_dashboard_data
        from subagent_metrics import aggregate_subagent_metrics
        events = [
            _session_start("s1", "p", "2026-05-01T10:00:00+00:00"),
            _au("s1", "p", "2026-05-01T10:03:00+00:00"),
            _session_start("s2", "p", "2026-05-01T11:00:00+00:00"),
            _au("s2", "p", "2026-05-01T11:03:00+00:00"),
            {"event_type": "subagent_start", "session_id": "s1",
             "subagent_type": "Explore", "tool_use_id": "t1",
             "timestamp": "2026-05-01T10:05:00+00:00", "duration_ms": 1000},
            {"event_type": "subagent_start", "session_id": "s1",
             "subagent_type": "general-purpose", "tool_use_id": "t2",
             "timestamp": "2026-05-01T10:10:00+00:00", "duration_ms": 2000},
            {"event_type": "subagent_start", "session_id": "s2",
             "subagent_type": "Explore", "tool_use_id": "t3",
             "timestamp": "2026-05-01T11:05:00+00:00", "duration_ms": 1500},
        ]
        result = build_dashboard_data(events)
        sb_total = sum(r["subagent_count"] for r in result["session_breakdown"])
        metrics_total = sum(m["count"] for m in aggregate_subagent_metrics(events).values())
        self.assertEqual(sb_total, metrics_total)
        self.assertEqual(sb_total, 3)

    def test_session_breakdown_period_split(self):
        """drift guard: period="7d" だと cutoff 外の session は session_breakdown
        からも除外される (Issue #85 連動)。
        """
        from server import build_dashboard_data
        now = datetime(2026, 5, 10, tzinfo=timezone.utc)
        events = [
            # 30 日前 (7d cutoff の外)
            _session_start("s_old", "p", "2026-04-10T10:00:00+00:00"),
            _au("s_old", "p", "2026-04-10T10:05:00+00:00"),
            # 直近
            _session_start("s_new", "p", "2026-05-09T10:00:00+00:00"),
            _au("s_new", "p", "2026-05-09T10:05:00+00:00"),
        ]
        result_all = build_dashboard_data(events, period="all", now=now)
        result_7d = build_dashboard_data(events, period="7d", now=now)
        all_ids = {r["session_id"] for r in result_all["session_breakdown"]}
        recent_ids = {r["session_id"] for r in result_7d["session_breakdown"]}
        self.assertIn("s_old", all_ids)
        self.assertIn("s_new", all_ids)
        self.assertNotIn("s_old", recent_ids)
        self.assertIn("s_new", recent_ids)

    def test_cross_cutoff_session_keeps_in_period_costs(self):
        """codex review Round 1 P2 regression guard: long-running session が
        period 跨ぎで session_start が pre-cutoff のとき、in-period assistant_usage
        の cost / token が消えてはいけない。boundary lookup (session_start /
        session_end) は全期間 events から、content (token / cost / skill) は
        period_events_raw から、で session 自体は render される。
        """
        from server import build_dashboard_data
        now = datetime(2026, 5, 10, tzinfo=timezone.utc)
        events = [
            # 10 日前に session 開始 (7d cutoff の外)
            _session_start("s_long", "p", "2026-04-30T10:00:00+00:00"),
            # 3 日前に assistant_usage (= in-period)
            _au("s_long", "p", "2026-05-07T10:00:00+00:00",
                in_t=1_000_000, out_t=0),
        ]
        result = build_dashboard_data(events, period="7d", now=now)
        sb = result["session_breakdown"]
        self.assertEqual(len(sb), 1)
        row = sb[0]
        self.assertEqual(row["session_id"], "s_long")
        # boundary lookup は全期間 → started_at は pre-cutoff 値が出る
        self.assertEqual(row["started_at"], "2026-04-30T10:00:00+00:00")
        # content (cost) は in-period の 1M input × Sonnet $3/M = $3.0
        self.assertEqual(row["estimated_cost_usd"], 3.0)
        self.assertEqual(row["tokens"]["input"], 1_000_000)


class TestSessionBreakdownExcludesEmpty(unittest.TestCase):
    """Issue #109 / v0.8.0: assistant_usage event を 1 件も持たない session
    (= 起動だけ / builtin command のみ / abort) は session_breakdown 配列から
    除外される (aggregate_session_breakdown aggregator 単独レベルで pin)。
    """

    def test_session_with_only_session_start_excluded(self):
        from cost_metrics import aggregate_session_breakdown
        events = [
            _session_start("s_empty", "p", "2026-05-01T10:00:00+00:00"),
        ]
        sb = aggregate_session_breakdown(events)
        self.assertEqual(len(sb), 0)

    def test_session_with_only_session_start_and_session_end_excluded(self):
        from cost_metrics import aggregate_session_breakdown
        events = [
            _session_start("s_empty", "p", "2026-05-01T10:00:00+00:00"),
            _session_end("s_empty", "p", "2026-05-01T10:00:30+00:00"),
        ]
        sb = aggregate_session_breakdown(events)
        self.assertEqual(len(sb), 0)

    def test_session_with_only_skill_tool_excluded(self):
        """/help / /skills 等 skill_tool だけで終わった session
        (assistant_usage 未発火) は除外。
        """
        from cost_metrics import aggregate_session_breakdown
        events = [
            _session_start("s_empty", "p", "2026-05-01T10:00:00+00:00"),
            {"event_type": "skill_tool", "session_id": "s_empty", "project": "p",
             "timestamp": "2026-05-01T10:00:10+00:00", "skill": "help"},
        ]
        sb = aggregate_session_breakdown(events)
        self.assertEqual(len(sb), 0)

    def test_session_with_only_user_slash_command_excluded(self):
        """builtin command (/exit / /clear 等) のみで終わった session は除外。"""
        from cost_metrics import aggregate_session_breakdown
        events = [
            _session_start("s_empty", "p", "2026-05-01T10:00:00+00:00"),
            {"event_type": "user_slash_command", "session_id": "s_empty",
             "project": "p", "timestamp": "2026-05-01T10:00:10+00:00",
             "skill": "exit"},
        ]
        sb = aggregate_session_breakdown(events)
        self.assertEqual(len(sb), 0)

    def test_session_with_one_assistant_usage_included(self):
        """boundary: assistant_usage 1 件あれば残す (Q1=A の verbatim)。"""
        from cost_metrics import aggregate_session_breakdown
        events = [
            _session_start("s_keep", "p", "2026-05-01T10:00:00+00:00"),
            _au("s_keep", "p", "2026-05-01T10:05:00+00:00"),
        ]
        sb = aggregate_session_breakdown(events)
        self.assertEqual(len(sb), 1)
        self.assertEqual(sb[0]["session_id"], "s_keep")

    def test_session_with_zero_token_assistant_usage_included(self):
        """input=output=cr=cc=0 でも assistant_usage event 自体は 1 件
        → 残す (Q1=A の verbatim 解釈、event 数で判定)。
        """
        from cost_metrics import aggregate_session_breakdown
        events = [
            _session_start("s_keep", "p", "2026-05-01T10:00:00+00:00"),
            _au("s_keep", "p", "2026-05-01T10:05:00+00:00",
                in_t=0, out_t=0, cr=0, cc=0),
        ]
        sb = aggregate_session_breakdown(events)
        self.assertEqual(len(sb), 1)
        self.assertEqual(sb[0]["session_id"], "s_keep")
        self.assertEqual(sb[0]["estimated_cost_usd"], 0.0)

    def test_session_with_subagent_lifecycle_but_no_assistant_usage_excluded(self):
        """subagent_start は記録されたが main session の assistant_usage hook が
        1 件も発火しなかった session (= Task 起動直後 abort 等) は除外。
        Q1=A の verbatim ("assistant_usage event 0 件") に従う。
        """
        from cost_metrics import aggregate_session_breakdown
        events = [
            _session_start("s_empty", "p", "2026-05-01T10:00:00+00:00"),
            {"event_type": "subagent_start", "session_id": "s_empty",
             "subagent_type": "Explore", "tool_use_id": "t1",
             "timestamp": "2026-05-01T10:00:30+00:00", "duration_ms": 500},
        ]
        sb = aggregate_session_breakdown(events)
        self.assertEqual(len(sb), 0)


class TestSessionBreakdownEmptyExcludeIntegration(unittest.TestCase):
    """Issue #109: empty session 除外が build_dashboard_data 経由で
    `/api/data` / export_html / live SSE / demo fixture すべての消費者に
    透過に効くこと、および `session_stats.total_sessions` (footer 経路)
    は無変更であることを cross-aggregator invariant として pin。
    """

    def test_build_dashboard_data_excludes_empty_session(self):
        from server import build_dashboard_data
        events = [
            _session_start("s_valid", "p", "2026-05-01T10:00:00+00:00"),
            _au("s_valid", "p", "2026-05-01T10:05:00+00:00"),
            _session_start("s_empty", "p", "2026-05-01T11:00:00+00:00"),
        ]
        result = build_dashboard_data(events)
        sb = result["session_breakdown"]
        self.assertEqual(len(sb), 1)
        self.assertEqual(sb[0]["session_id"], "s_valid")

    def test_session_stats_total_sessions_includes_empty(self):
        """footer / header `${total_sessions} sessions` は unfilter 観測総数。
        empty session も含めて count される (= aggregate_session_stats は
        raw events から session_start を直接 count する別経路)。
        """
        from server import build_dashboard_data
        events = [
            _session_start("s_valid", "p", "2026-05-01T10:00:00+00:00"),
            _au("s_valid", "p", "2026-05-01T10:05:00+00:00"),
            _session_start("s_empty", "p", "2026-05-01T11:00:00+00:00"),
        ]
        result = build_dashboard_data(events)
        self.assertEqual(result["session_stats"]["total_sessions"], 2)
        # 同時に session_breakdown 側は 1 件 (= 二段表示の根拠)
        self.assertEqual(len(result["session_breakdown"]), 1)

    def test_period_filter_and_empty_exclude_compose(self):
        """period="7d" + empty session 混在で
        「period 内 valid + period 内 empty + period 外 valid」 → len==1。
        period filter と empty exclude が独立に compose する。
        """
        from server import build_dashboard_data
        now = datetime(2026, 5, 10, tzinfo=timezone.utc)
        events = [
            # period 外 (>7d 前) valid
            _session_start("s_old_valid", "p", "2026-04-30T10:00:00+00:00"),
            _au("s_old_valid", "p", "2026-04-30T10:05:00+00:00"),
            # period 内 valid
            _session_start("s_recent_valid", "p", "2026-05-09T10:00:00+00:00"),
            _au("s_recent_valid", "p", "2026-05-09T10:05:00+00:00"),
            # period 内 empty
            _session_start("s_recent_empty", "p", "2026-05-09T11:00:00+00:00"),
        ]
        result = build_dashboard_data(events, period="7d", now=now)
        sb = result["session_breakdown"]
        self.assertEqual(len(sb), 1)
        self.assertEqual(sb[0]["session_id"], "s_recent_valid")

    def test_cross_cutoff_session_with_in_period_assistant_usage_kept(self):
        """drift guard: session_start が pre-cutoff、in-period に
        assistant_usage 1 件 → 残る (`test_cross_cutoff_session_keeps_in_period_costs`
        の振る舞いを Issue #109 の filter で破壊しない)。
        """
        from server import build_dashboard_data
        now = datetime(2026, 5, 10, tzinfo=timezone.utc)
        events = [
            _session_start("s_long", "p", "2026-04-30T10:00:00+00:00"),
            _au("s_long", "p", "2026-05-07T10:00:00+00:00"),
        ]
        result = build_dashboard_data(events, period="7d", now=now)
        sb = result["session_breakdown"]
        self.assertEqual(len(sb), 1)
        self.assertEqual(sb[0]["session_id"], "s_long")

    def test_subagent_only_session_excluded_at_build_dashboard_data_level(self):
        """subagent_start のみ session (= main の assistant_usage 無し) は
        build_dashboard_data 経由でも除外される。
        """
        from server import build_dashboard_data
        events = [
            _session_start("s_subagent_only", "p", "2026-05-01T10:00:00+00:00"),
            {"event_type": "subagent_start", "session_id": "s_subagent_only",
             "subagent_type": "Explore", "tool_use_id": "t1",
             "timestamp": "2026-05-01T10:05:00+00:00", "duration_ms": 1000},
        ]
        result = build_dashboard_data(events)
        self.assertEqual(len(result["session_breakdown"]), 0)
        # 同 input で session_stats は session_start を count する
        self.assertEqual(result["session_stats"]["total_sessions"], 1)

    def test_session_with_only_pre_cutoff_assistant_usage_excluded_under_period(self):
        """session_start in-period (= 7d window 内)、assistant_usage は全て
        period 外 (>7d 前)。period 適用後の content_evs に assistant_usage が
        0 件 → 除外。「period 内の意味あるアクティビティ」が exclusion の
        単位であることを pin (`test_cross_cutoff_session_with_in_period_assistant_usage_kept`
        の対称形)。
        """
        from server import build_dashboard_data
        now = datetime(2026, 5, 10, tzinfo=timezone.utc)
        events = [
            # session_start 直近 (in-period)
            _session_start("s_pre_only", "p", "2026-05-09T10:00:00+00:00"),
            # assistant_usage は全て pre-cutoff (> 7d 前)
            _au("s_pre_only", "p", "2026-04-20T10:05:00+00:00"),
        ]
        result = build_dashboard_data(events, period="7d", now=now)
        # period filter 適用後は session に in-period assistant_usage 0 件 → 除外
        self.assertEqual(len(result["session_breakdown"]), 0)


if __name__ == "__main__":
    unittest.main()
