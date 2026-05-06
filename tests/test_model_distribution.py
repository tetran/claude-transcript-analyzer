"""tests/test_model_distribution.py — Issue #106 / Overview モデル分布パネル

server-side aggregation テスト:
- TestInferModelFamily — raw model ID → family rollup helper
- TestAggregateModelDistribution — events list → 3-row distribution dict
- TestPricingHelperSemanticsContrast — `_get_pricing` (prefix) vs `infer_model_family`
  (substring) の semantics 違いを test レベルで明示 (R7)
- TestBuildDashboardDataModelDistribution — /api/data 統合 (Phase 2)
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cost_metrics import (  # noqa: E402
    _get_pricing,
    aggregate_model_distribution,
    infer_model_family,
)


class TestInferModelFamily(unittest.TestCase):
    """raw model ID → 'opus' / 'sonnet' / 'haiku' 文字列 (substring match, 未知 → sonnet)."""

    def test_opus_4_7_rolls_up_to_opus(self):
        self.assertEqual(infer_model_family("claude-opus-4-7-20260101"), "opus")

    def test_sonnet_4_6_rolls_up_to_sonnet(self):
        self.assertEqual(infer_model_family("claude-sonnet-4-6-20260101"), "sonnet")

    def test_haiku_4_5_rolls_up_to_haiku(self):
        self.assertEqual(infer_model_family("claude-haiku-4-5-20251001"), "haiku")

    def test_legacy_3_5_haiku_rolls_up_to_haiku(self):
        # 3.x convention: `claude-{version}-{model}-{date}` (4.x と逆順)
        self.assertEqual(infer_model_family("claude-3-5-haiku-20241022"), "haiku")

    def test_unknown_model_falls_back_to_sonnet(self):
        # DEFAULT_PRICING (sonnet-4-6) と意味論を合わせる
        self.assertEqual(infer_model_family("made-up-model"), "sonnet")

    def test_empty_string_falls_back_to_sonnet(self):
        self.assertEqual(infer_model_family(""), "sonnet")
        self.assertEqual(infer_model_family(None), "sonnet")


class TestAggregateModelDistribution(unittest.TestCase):
    """events list → 3-row dict (canonical order, NaN guard, total invariants)."""

    def _au(self, model, in_t=0, out_t=0, cr_t=0, cc_t=0):
        return {
            "event_type": "assistant_usage",
            "model": model,
            "input_tokens": in_t,
            "output_tokens": out_t,
            "cache_read_tokens": cr_t,
            "cache_creation_tokens": cc_t,
        }

    def test_returns_three_rows_with_canonical_order(self):
        result = aggregate_model_distribution([self._au("claude-opus-4-7")])
        families = [row["family"] for row in result["families"]]
        self.assertEqual(families, ["opus", "sonnet", "haiku"])

    def test_messages_pct_sums_to_one_within_tolerance(self):
        # opus 3 / sonnet 5 / haiku 2 → Σ pct = 1.0 ± 0.005 (Issue AC)
        events = (
            [self._au("claude-opus-4-7")] * 3
            + [self._au("claude-sonnet-4-6")] * 5
            + [self._au("claude-haiku-4-5")] * 2
        )
        result = aggregate_model_distribution(events)
        total_pct = sum(row["messages_pct"] for row in result["families"])
        self.assertAlmostEqual(total_pct, 1.0, places=2)

    def test_cost_pct_sums_to_one_within_tolerance(self):
        events = (
            [self._au("claude-opus-4-7", 1_000_000, 0)] * 3
            + [self._au("claude-sonnet-4-6", 1_000_000, 0)] * 5
            + [self._au("claude-haiku-4-5", 1_000_000, 0)] * 2
        )
        result = aggregate_model_distribution(events)
        total_pct = sum(row["cost_pct"] for row in result["families"])
        self.assertAlmostEqual(total_pct, 1.0, places=2)

    def test_messages_total_matches_array_sum(self):
        events = [self._au("claude-opus-4-7"), self._au("claude-sonnet-4-6")]
        result = aggregate_model_distribution(events)
        self.assertEqual(
            result["messages_total"],
            sum(row["messages"] for row in result["families"]),
        )

    def test_cost_total_matches_array_sum_to_4_decimals(self):
        # 1M opus output = $25, 1M sonnet output = $15 → total $40
        events = [
            self._au("claude-opus-4-7", 0, 1_000_000),
            self._au("claude-sonnet-4-6", 0, 1_000_000),
        ]
        result = aggregate_model_distribution(events)
        self.assertEqual(result["cost_total"], 40.0)
        # invariant: cost_total == sum(row.cost_usd) (4 桁精度内)
        self.assertAlmostEqual(
            result["cost_total"],
            sum(row["cost_usd"] for row in result["families"]),
            places=4,
        )

    def test_empty_events_returns_three_zero_rows(self):
        result = aggregate_model_distribution([])
        self.assertEqual(len(result["families"]), 3)
        for row in result["families"]:
            self.assertEqual(row["messages"], 0)
            self.assertEqual(row["cost_usd"], 0)
            self.assertEqual(row["messages_pct"], 0.0)  # NaN guard
            self.assertEqual(row["cost_pct"], 0.0)
        self.assertEqual(result["messages_total"], 0)
        self.assertEqual(result["cost_total"], 0)

    def test_only_assistant_usage_events_counted(self):
        events = [
            {"event_type": "session_start", "session_id": "s1"},
            {"event_type": "skill_tool", "skill": "foo"},
            {"event_type": "subagent_start", "agent": "bar"},
            self._au("claude-opus-4-7"),
        ]
        result = aggregate_model_distribution(events)
        opus_row = next(r for r in result["families"] if r["family"] == "opus")
        self.assertEqual(opus_row["messages"], 1)
        self.assertEqual(result["messages_total"], 1)

    def test_unknown_model_rolls_up_to_sonnet_in_distribution(self):
        # 未知 model → sonnet 行に集計、cost は DEFAULT_PRICING (sonnet-4-6) で計算
        # 1M output × sonnet $15 = $15
        events = [self._au("future-model-xyz", 0, 1_000_000)]
        result = aggregate_model_distribution(events)
        sonnet_row = next(r for r in result["families"] if r["family"] == "sonnet")
        self.assertEqual(sonnet_row["messages"], 1)
        self.assertEqual(sonnet_row["cost_usd"], 15.0)

    def test_cost_uses_calculate_message_cost_per_event(self):
        # opus 1 件 (1M input + 1M output) で cost = $5 + $25 = $30
        events = [self._au("claude-opus-4-7", 1_000_000, 1_000_000)]
        result = aggregate_model_distribution(events)
        opus_row = next(r for r in result["families"] if r["family"] == "opus")
        self.assertEqual(opus_row["cost_usd"], 30.0)
        self.assertEqual(result["cost_total"], 30.0)

    def test_zero_cost_event_does_not_zero_div(self):
        # token=0 / cost=0 → cost_total=0 で NaN なし
        events = [self._au("claude-opus-4-7", 0, 0, 0, 0)]
        result = aggregate_model_distribution(events)
        self.assertEqual(result["cost_total"], 0)
        for row in result["families"]:
            self.assertEqual(row["cost_pct"], 0.0)  # 0/0 を 0.0 に塞ぐ


class TestPricingHelperSemanticsContrast(unittest.TestCase):
    """`_get_pricing` (prefix) と `infer_model_family` (substring) の semantics 違い対比 (R7)。

    R7 の load-bearing テスト: 両 helper を**同じ test class に並べる**ことで
    「rate 解決 (prefix, longest-match) と family rollup (substring) は意図的に
    違う abstraction」であることを将来の reviewer / maintainer に test 形式で文書化する。
    """

    def test_get_pricing_uses_prefix_match(self):
        # `claude-opus-4-5-20260101` は longest-prefix で `claude-opus-4-5` ($5) を
        # 当てる。`claude-opus-4` ($15) ではない (取り違えると 3x 致命的)
        pricing = _get_pricing("claude-opus-4-5-20260101")
        self.assertEqual(pricing.input, 5.00)
        self.assertEqual(pricing.output, 25.00)

    def test_infer_model_family_uses_substring_match(self):
        # `opus-foo-bar-haiku` は substring match の前後関係 (opus check が先) で
        # `"opus"` を返す。両 helper の semantics が違う = rate と family は別軸
        self.assertEqual(infer_model_family("opus-foo-bar-haiku"), "opus")


# =============================================================================
# Phase 2 — build_dashboard_data 統合テスト
# =============================================================================


class TestBuildDashboardDataModelDistribution(unittest.TestCase):
    """`/api/data` の return dict に `model_distribution` が additive で入ることを pin。"""

    def setUp(self):
        # late-import: Phase 1 GREEN 前は cost_metrics import で fail するため
        # build_dashboard_data import を method 内に遅延させる
        sys.path.insert(0, str(ROOT))
        from dashboard.server import build_dashboard_data
        self.build_dashboard_data = build_dashboard_data

    def _au(self, model, ts, sid="s1", in_t=0, out_t=0, cr_t=0, cc_t=0, source="user"):
        return {
            "event_type": "assistant_usage",
            "model": model,
            "timestamp": ts,
            "session_id": sid,
            "source": source,
            "input_tokens": in_t,
            "output_tokens": out_t,
            "cache_read_tokens": cr_t,
            "cache_creation_tokens": cc_t,
        }

    def _ss(self, ts, sid="s1", project="proj-a"):
        return {
            "event_type": "session_start",
            "timestamp": ts,
            "session_id": sid,
            "project": project,
        }

    def test_field_present_in_response(self):
        result = self.build_dashboard_data([])
        self.assertIn("model_distribution", result)

    def test_shape_has_families_and_totals(self):
        result = self.build_dashboard_data([])
        md = result["model_distribution"]
        self.assertIsInstance(md, dict)
        self.assertIn("families", md)
        self.assertIn("messages_total", md)
        self.assertIn("cost_total", md)
        self.assertEqual(len(md["families"]), 3)

    def test_period_filter_applied(self):
        # 8 日前 (period=7d で除外) と今日 (含む) の 2 件
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
        old_ts = (now - timedelta(days=8)).isoformat()
        new_ts = now.isoformat()
        events = [
            self._ss(old_ts, sid="s_old"),
            self._au("claude-opus-4-7", old_ts, sid="s_old"),
            self._ss(new_ts, sid="s_new"),
            self._au("claude-sonnet-4-6", new_ts, sid="s_new"),
        ]
        result = self.build_dashboard_data(events, period="7d", now=now)
        md = result["model_distribution"]
        # opus は 8 日前のみ → 0、sonnet は今日 → 1
        opus_row = next(r for r in md["families"] if r["family"] == "opus")
        sonnet_row = next(r for r in md["families"] if r["family"] == "sonnet")
        self.assertEqual(opus_row["messages"], 0)
        self.assertEqual(sonnet_row["messages"], 1)

    def test_period_all_includes_all_events(self):
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
        old_ts = (now - timedelta(days=200)).isoformat()
        new_ts = now.isoformat()
        events = [
            self._ss(old_ts, sid="s_old"),
            self._au("claude-opus-4-7", old_ts, sid="s_old"),
            self._ss(new_ts, sid="s_new"),
            self._au("claude-sonnet-4-6", new_ts, sid="s_new"),
        ]
        result = self.build_dashboard_data(events, period="all", now=now)
        md = result["model_distribution"]
        opus_row = next(r for r in md["families"] if r["family"] == "opus")
        sonnet_row = next(r for r in md["families"] if r["family"] == "sonnet")
        self.assertEqual(opus_row["messages"], 1)
        self.assertEqual(sonnet_row["messages"], 1)

    def test_subagent_assistant_usage_included(self):
        # source="subagent" の assistant_usage も model field を持つので集計対象
        # (Issue body の "subagent token 別 model 扱い → 別 issue" は per-message
        # cost に subagent invocation の入れ子を作らないという意味、event 自体は count される)
        from datetime import datetime, timezone
        now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
        ts = now.isoformat()
        events = [
            self._ss(ts),
            self._au("claude-haiku-4-5", ts, source="subagent"),
        ]
        result = self.build_dashboard_data(events, period="all", now=now)
        haiku_row = next(r for r in result["model_distribution"]["families"] if r["family"] == "haiku")
        self.assertEqual(haiku_row["messages"], 1)

    def test_session_breakdown_total_matches_model_distribution_total(self):
        # cap 内 (20 session 未満) では一致する drift guard
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
        events = []
        for i in range(5):
            sid = f"s{i}"
            ts = (now - timedelta(hours=i)).isoformat()
            events.append(self._ss(ts, sid=sid))
            events.append(self._au("claude-opus-4-7", ts, sid=sid, in_t=100_000, out_t=50_000))
        result = self.build_dashboard_data(events, period="all", now=now)
        sb_total = sum(row["estimated_cost_usd"] for row in result["session_breakdown"])
        md_total = result["model_distribution"]["cost_total"]
        self.assertAlmostEqual(sb_total, md_total, places=4)

    def test_session_breakdown_total_diverges_from_model_distribution_above_cap(self):
        # 21 session で session_breakdown は 20 cap → 1 session 分小さい (R8 対偶 drift guard)
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
        events = []
        for i in range(21):
            sid = f"s{i}"
            # 全 session 同じ時刻ではなく 1 hour stride で並べて sort 安定化
            ts = (now - timedelta(hours=i)).isoformat()
            events.append(self._ss(ts, sid=sid))
            events.append(self._au("claude-opus-4-7", ts, sid=sid, in_t=100_000, out_t=50_000))
        result = self.build_dashboard_data(events, period="all", now=now)
        sb_total = sum(row["estimated_cost_usd"] for row in result["session_breakdown"])
        md_total = result["model_distribution"]["cost_total"]
        # cap (top_n=20) で 1 session 分 session_breakdown 側が小さい
        self.assertLess(sb_total, md_total)

    def test_empty_events_yields_three_zero_rows(self):
        result = self.build_dashboard_data([])
        md = result["model_distribution"]
        self.assertEqual(len(md["families"]), 3)
        for row in md["families"]:
            self.assertEqual(row["messages"], 0)


if __name__ == "__main__":
    unittest.main()
