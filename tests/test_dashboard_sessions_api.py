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
        events = [
            _session_start("s_old", "p", "2026-05-01T10:00:00+00:00"),
            _session_start("s_new", "p", "2026-05-02T10:00:00+00:00"),
        ]
        result = build_dashboard_data(events)
        ids = [r["session_id"] for r in result["session_breakdown"]]
        self.assertEqual(ids, ["s_new", "s_old"])

    def test_top_n_cap(self):
        from server import build_dashboard_data
        from cost_metrics import TOP_N_SESSIONS
        events = [
            _session_start(f"s{i:02d}", "p", f"2026-05-{i+1:02d}T10:00:00+00:00")
            for i in range(TOP_N_SESSIONS + 5)
        ]
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
        """
        from server import build_dashboard_data
        from subagent_metrics import aggregate_subagent_metrics
        events = [
            _session_start("s1", "p", "2026-05-01T10:00:00+00:00"),
            _session_start("s2", "p", "2026-05-01T11:00:00+00:00"),
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


if __name__ == "__main__":
    unittest.main()
