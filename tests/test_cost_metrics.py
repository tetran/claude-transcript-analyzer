"""tests/test_cost_metrics.py — cost_metrics.py の TDD テスト (Issue #99)

価格表は `cost_metrics.MODEL_PRICING` の docstring で pin した
公式値 (https://platform.claude.com/docs/en/about-claude/pricing) を信頼する。
ここでの数値 pin は **公式値の参照値** として固定し、価格改定が来たら
本テストも合わせて update する (= drift guard)。
"""
import sys
import unittest
from pathlib import Path

# repo root を sys.path に追加 (他テストと同じ慣習)
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cost_metrics import (
    DEFAULT_PRICING,
    MODEL_PRICING,
    TOP_N_SESSIONS,
    aggregate_session_breakdown,
    calculate_message_cost,
    calculate_session_cost,
)


class TestCalculateMessageCost(unittest.TestCase):
    """4 dimension × per-1M-token rate × 4 桁丸め の純関数テスト。"""

    def test_sonnet_46_input_only(self):
        # claude-sonnet-4-6: input $3 / MTok
        self.assertEqual(
            calculate_message_cost("claude-sonnet-4-6", 1_000_000, 0, 0, 0),
            3.0,
        )

    def test_sonnet_46_all_dimensions(self):
        # 1M each × {input 3, output 15, cache_read 0.30, cache_creation 3.75} = 22.05
        self.assertEqual(
            calculate_message_cost("claude-sonnet-4-6", 1_000_000, 1_000_000, 1_000_000, 1_000_000),
            22.05,
        )

    def test_opus_47_pricing(self):
        # claude-opus-4-7: input $5, output $25
        # 100k input + 100k output = $0.5 + $2.5 = $3.0
        self.assertEqual(
            calculate_message_cost("claude-opus-4-7", 100_000, 100_000, 0, 0),
            3.0,
        )

    def test_haiku_45_pricing(self):
        # claude-haiku-4-5: input $1 / MTok
        self.assertEqual(
            calculate_message_cost("claude-haiku-4-5", 1_000_000, 0, 0, 0),
            1.0,
        )

    def test_haiku_45_with_date_suffix_prefix_match(self):
        # 公式 model ID は `claude-haiku-4-5-20251001` のような date-suffix を
        # 持つ。prefix match で base price を当てる規律を pin。
        self.assertEqual(
            calculate_message_cost("claude-haiku-4-5-20251001", 1_000_000, 0, 0, 0),
            1.0,
        )

    def test_opus_4_legacy_pricing_distinct_from_4_5(self):
        # claude-opus-4 は $15 / MTok (deprecated 系)。
        # prefix collision で claude-opus-4-5 ($5) に誤マッチしないこと。
        self.assertEqual(
            calculate_message_cost("claude-opus-4", 1_000_000, 0, 0, 0),
            15.0,
        )
        self.assertEqual(
            calculate_message_cost("claude-opus-4-5", 1_000_000, 0, 0, 0),
            5.0,
        )

    # ───── Claude 3.x official IDs (codex Round 2 / P2 修正) ─────
    # 3.x naming は `claude-{version}-{model}-{date}` で 4.x と逆順。
    # date-suffix 付きで Anthropic API から実際に返ってくる ID を pin。

    def test_claude_3_5_haiku_official_id(self):
        # claude-3-5-haiku: input $0.80 / MTok
        self.assertEqual(
            calculate_message_cost("claude-3-5-haiku-20241022", 1_000_000, 0, 0, 0),
            0.80,
        )

    def test_claude_3_haiku_official_id(self):
        # claude-3-haiku: input $0.25 / MTok (deprecated)
        self.assertEqual(
            calculate_message_cost("claude-3-haiku-20240307", 1_000_000, 0, 0, 0),
            0.25,
        )

    def test_claude_3_5_sonnet_official_id(self):
        # claude-3-5-sonnet: input $3 / MTok (Sonnet 3.5 retired)
        self.assertEqual(
            calculate_message_cost("claude-3-5-sonnet-20241022", 1_000_000, 0, 0, 0),
            3.0,
        )

    def test_claude_3_7_sonnet_official_id(self):
        # claude-3-7-sonnet: input $3 / MTok (deprecated)
        self.assertEqual(
            calculate_message_cost("claude-3-7-sonnet-20250219", 1_000_000, 0, 0, 0),
            3.0,
        )

    def test_claude_3_opus_official_id(self):
        # claude-3-opus: input $15 / MTok (deprecated)
        self.assertEqual(
            calculate_message_cost("claude-3-opus-20240229", 1_000_000, 0, 0, 0),
            15.0,
        )

    def test_3x_haiku_does_not_fallback_to_sonnet(self):
        """codex Round 2 P2 regression guard: claude-3-5-haiku-* が
        Sonnet fallback ($3) ではなく Haiku rate ($0.80) で計算される。
        """
        haiku_cost = calculate_message_cost(
            "claude-3-5-haiku-20241022", 1_000_000, 0, 0, 0,
        )
        sonnet_fallback = calculate_message_cost(
            "claude-future-99-x", 1_000_000, 0, 0, 0,
        )
        self.assertEqual(haiku_cost, 0.80)
        self.assertEqual(sonnet_fallback, 3.0)
        self.assertNotEqual(haiku_cost, sonnet_fallback)

    def test_3x_5_haiku_distinct_from_3_haiku(self):
        # 3-5 vs 3 で取り違えないこと (longest-prefix collision 防御)
        self.assertEqual(
            calculate_message_cost("claude-3-5-haiku-20241022", 1_000_000, 0, 0, 0),
            0.80,
        )
        self.assertEqual(
            calculate_message_cost("claude-3-haiku-20240307", 1_000_000, 0, 0, 0),
            0.25,
        )

    def test_four_decimal_rounding(self):
        # 1 token × $3 / 1M = $0.000003 → 4 桁丸めで $0.0
        self.assertEqual(
            calculate_message_cost("claude-sonnet-4-6", 1, 0, 0, 0),
            0.0,
        )
        # 1000 token × $3 / 1M = $0.003 (= $0.0030 4 桁)
        self.assertEqual(
            calculate_message_cost("claude-sonnet-4-6", 1000, 0, 0, 0),
            0.003,
        )

    def test_returns_float(self):
        result = calculate_message_cost("claude-sonnet-4-6", 100, 0, 0, 0)
        self.assertIsInstance(result, float)


class TestUnknownModelFallback(unittest.TestCase):
    """plan §1 / cost-calculation-design.md §2: 未知 model は Sonnet 4.6 にフォールバック。"""

    def test_unknown_uses_sonnet_46_rate(self):
        sonnet_cost = calculate_message_cost("claude-sonnet-4-6", 1_000_000, 0, 0, 0)
        unknown_cost = calculate_message_cost("claude-future-99-x", 1_000_000, 0, 0, 0)
        self.assertEqual(unknown_cost, sonnet_cost)

    def test_empty_model_uses_sonnet_46_rate(self):
        sonnet_cost = calculate_message_cost("claude-sonnet-4-6", 1_000_000, 0, 0, 0)
        self.assertEqual(
            calculate_message_cost("", 1_000_000, 0, 0, 0),
            sonnet_cost,
        )

    def test_default_pricing_is_sonnet_46(self):
        # Sonnet 4.6 fallback の docstring が drift しないこと
        self.assertEqual(DEFAULT_PRICING, MODEL_PRICING["claude-sonnet-4-6"])


class TestEmptyTokensReturnsZero(unittest.TestCase):
    def test_all_zero_tokens(self):
        self.assertEqual(
            calculate_message_cost("claude-opus-4-7", 0, 0, 0, 0),
            0.0,
        )

    def test_zero_tokens_unknown_model(self):
        # fallback 経路でも error なく 0.0
        self.assertEqual(
            calculate_message_cost("foo-model", 0, 0, 0, 0),
            0.0,
        )


class TestCalculateSessionCost(unittest.TestCase):
    def test_single_assistant_usage_event(self):
        events = [{
            "event_type": "assistant_usage",
            "session_id": "s1",
            "model": "claude-sonnet-4-6",
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }]
        self.assertEqual(calculate_session_cost(events), 3.0)

    def test_multiple_events_summed(self):
        events = [
            {"event_type": "assistant_usage", "session_id": "s1", "model": "claude-sonnet-4-6",
             "input_tokens": 1_000_000, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0},
            {"event_type": "assistant_usage", "session_id": "s1", "model": "claude-sonnet-4-6",
             "input_tokens": 0, "output_tokens": 1_000_000, "cache_read_tokens": 0, "cache_creation_tokens": 0},
        ]
        # input 3 + output 15 = 18
        self.assertEqual(calculate_session_cost(events), 18.0)

    def test_non_assistant_usage_events_ignored(self):
        events = [
            {"event_type": "skill_tool", "session_id": "s1"},
            {"event_type": "session_start", "session_id": "s1"},
            {"event_type": "assistant_usage", "session_id": "s1", "model": "claude-haiku-4-5",
             "input_tokens": 1_000_000, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0},
        ]
        self.assertEqual(calculate_session_cost(events), 1.0)

    def test_empty_events_returns_zero(self):
        self.assertEqual(calculate_session_cost([]), 0.0)


class TestModelMixSessionCostInvariant(unittest.TestCase):
    """混在 sum の罠 (cost-calculation-design.md §5) を踏まないことの drift guard。"""

    def test_mixed_models_session_equals_per_model_sum(self):
        events = [
            {"event_type": "assistant_usage", "session_id": "s1", "model": "claude-opus-4-7",
             "input_tokens": 1_000_000, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0},
            {"event_type": "assistant_usage", "session_id": "s1", "model": "claude-haiku-4-5",
             "input_tokens": 1_000_000, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0},
        ]
        # opus 1M = $5; haiku 1M = $1; total = $6 (混在 sum で誤って haiku rate を opus 分にも適用すると壊れる)
        opus_cost = calculate_message_cost("claude-opus-4-7", 1_000_000, 0, 0, 0)
        haiku_cost = calculate_message_cost("claude-haiku-4-5", 1_000_000, 0, 0, 0)
        self.assertEqual(calculate_session_cost(events), 6.0)
        self.assertEqual(calculate_session_cost(events), opus_cost + haiku_cost)


def _au(session_id, project, ts, model, in_t, out_t, cr=0, cc=0,
        msg_id="m", tier=None, source="main"):
    """assistant_usage event factory (テスト fixture 短縮)。"""
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
        "message_id": msg_id,
        "source": source,
    }
    if tier is not None:
        ev["service_tier"] = tier
    return ev


class TestAggregateSessionBreakdown(unittest.TestCase):
    def test_per_session_shape(self):
        events = [
            {"event_type": "session_start", "session_id": "s1", "project": "proj-a",
             "timestamp": "2026-05-01T10:00:00+00:00"},
            {"event_type": "session_end", "session_id": "s1", "project": "proj-a",
             "timestamp": "2026-05-01T11:00:00+00:00", "reason": "logout"},
            _au("s1", "proj-a", "2026-05-01T10:05:00+00:00", "claude-sonnet-4-6",
                1000, 500, cr=200, cc=100, tier="standard"),
            {"event_type": "skill_tool", "session_id": "s1", "project": "proj-a",
             "timestamp": "2026-05-01T10:10:00+00:00", "skill": "x"},
        ]
        result = aggregate_session_breakdown(events)
        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row["session_id"], "s1")
        self.assertEqual(row["project"], "proj-a")
        self.assertEqual(row["started_at"], "2026-05-01T10:00:00+00:00")
        self.assertEqual(row["ended_at"], "2026-05-01T11:00:00+00:00")
        self.assertEqual(row["duration_seconds"], 3600.0)
        self.assertEqual(row["models"], {"claude-sonnet-4-6": 1})
        self.assertEqual(row["tokens"], {
            "input": 1000, "output": 500, "cache_read": 200, "cache_creation": 100,
        })
        self.assertEqual(row["service_tier_breakdown"], {"standard": 1})
        self.assertEqual(row["skill_count"], 1)
        self.assertEqual(row["subagent_count"], 0)
        self.assertIsInstance(row["estimated_cost_usd"], float)

    def test_active_session_has_null_end(self):
        events = [
            {"event_type": "session_start", "session_id": "s1", "project": "p",
             "timestamp": "2026-05-01T10:00:00+00:00"},
            _au("s1", "p", "2026-05-01T10:05:00+00:00", "claude-sonnet-4-6", 100, 50),
        ]
        result = aggregate_session_breakdown(events)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["ended_at"])
        self.assertIsNone(result[0]["duration_seconds"])

    def test_sort_by_started_at_desc(self):
        # Issue #109: session_breakdown は assistant_usage 1 件以上を持つ session のみ
        events = [
            {"event_type": "session_start", "session_id": "s_old", "project": "p",
             "timestamp": "2026-05-01T10:00:00+00:00"},
            _au("s_old", "p", "2026-05-01T10:05:00+00:00", "claude-sonnet-4-6", 100, 50),
            {"event_type": "session_start", "session_id": "s_new", "project": "p",
             "timestamp": "2026-05-02T10:00:00+00:00"},
            _au("s_new", "p", "2026-05-02T10:05:00+00:00", "claude-sonnet-4-6", 100, 50),
            {"event_type": "session_start", "session_id": "s_mid", "project": "p",
             "timestamp": "2026-05-01T15:00:00+00:00"},
            _au("s_mid", "p", "2026-05-01T15:05:00+00:00", "claude-sonnet-4-6", 100, 50),
        ]
        result = aggregate_session_breakdown(events)
        self.assertEqual(
            [r["session_id"] for r in result],
            ["s_new", "s_mid", "s_old"],
        )

    def test_top_n_cap(self):
        # Issue #109: 各 session に assistant_usage を 1 件付けて render 対象にする
        events = []
        for i in range(25):
            events.append({
                "event_type": "session_start",
                "session_id": f"s{i:02d}",
                "project": "p",
                "timestamp": f"2026-05-{i+1:02d}T10:00:00+00:00",
            })
            events.append(_au(f"s{i:02d}", "p",
                              f"2026-05-{i+1:02d}T10:05:00+00:00",
                              "claude-sonnet-4-6", 100, 50))
        result = aggregate_session_breakdown(events, top_n=20)
        self.assertEqual(len(result), 20)

    def test_default_top_n_is_20(self):
        self.assertEqual(TOP_N_SESSIONS, 20)

    def test_empty_events_returns_empty_list(self):
        self.assertEqual(aggregate_session_breakdown([]), [])

    def test_unknown_model_attributed_to_sonnet_fallback(self):
        events = [
            {"event_type": "session_start", "session_id": "s1", "project": "p",
             "timestamp": "2026-05-01T10:00:00+00:00"},
            _au("s1", "p", "2026-05-01T10:05:00+00:00", "claude-future-99", 1_000_000, 0),
        ]
        result = aggregate_session_breakdown(events)
        self.assertEqual(len(result), 1)
        # Sonnet fallback → 1M input × $3 = $3.0
        self.assertEqual(result[0]["estimated_cost_usd"], 3.0)
        # models 集計には raw model 名を保持 (fallback の事実は cost にだけ反映、UI を毒さない)
        self.assertEqual(result[0]["models"], {"claude-future-99": 1})

    def test_session_subagent_count_matches_metrics(self):
        # subagent 1 件 (PostToolUse 単独 invocation) の session を集計し、
        # session_subagent_counts と aggregate_subagent_metrics の合計が一致する drift guard。
        # Issue #109: session_breakdown render 対象にするため assistant_usage を 1 件追加
        events = [
            {"event_type": "session_start", "session_id": "s1", "project": "p",
             "timestamp": "2026-05-01T10:00:00+00:00"},
            _au("s1", "p", "2026-05-01T10:04:00+00:00", "claude-sonnet-4-6", 100, 50),
            {"event_type": "subagent_start", "session_id": "s1", "subagent_type": "Explore",
             "timestamp": "2026-05-01T10:05:00+00:00", "tool_use_id": "toolu_1",
             "duration_ms": 1200, "permission_mode": "default"},
        ]
        result = aggregate_session_breakdown(events)
        self.assertEqual(result[0]["subagent_count"], 1)

    def test_models_chip_format_multi_model(self):
        # 1 session 内で model 切替がある場合、models dict が両方 count を持つ
        events = [
            {"event_type": "session_start", "session_id": "s1", "project": "p",
             "timestamp": "2026-05-01T10:00:00+00:00"},
            _au("s1", "p", "2026-05-01T10:05:00+00:00", "claude-opus-4-7", 1000, 500, msg_id="m1"),
            _au("s1", "p", "2026-05-01T10:10:00+00:00", "claude-haiku-4-5", 1000, 500, msg_id="m2"),
            _au("s1", "p", "2026-05-01T10:15:00+00:00", "claude-haiku-4-5", 1000, 500, msg_id="m3"),
        ]
        result = aggregate_session_breakdown(events)
        self.assertEqual(result[0]["models"], {
            "claude-opus-4-7": 1,
            "claude-haiku-4-5": 2,
        })

    def test_service_tier_breakdown(self):
        # priority / standard 混在 + 欠損ケース。欠損は breakdown に出さない
        events = [
            {"event_type": "session_start", "session_id": "s1", "project": "p",
             "timestamp": "2026-05-01T10:00:00+00:00"},
            _au("s1", "p", "2026-05-01T10:05:00+00:00", "claude-sonnet-4-6", 100, 0,
                tier="priority", msg_id="m1"),
            _au("s1", "p", "2026-05-01T10:06:00+00:00", "claude-sonnet-4-6", 100, 0,
                tier="standard", msg_id="m2"),
            _au("s1", "p", "2026-05-01T10:07:00+00:00", "claude-sonnet-4-6", 100, 0,
                tier="standard", msg_id="m3"),
            _au("s1", "p", "2026-05-01T10:08:00+00:00", "claude-sonnet-4-6", 100, 0,
                tier=None, msg_id="m4"),
        ]
        result = aggregate_session_breakdown(events)
        self.assertEqual(
            result[0]["service_tier_breakdown"],
            {"priority": 1, "standard": 2},  # null は記録しない
        )

    def test_session_without_session_start_dropped(self):
        # session_start を持たない session は orphan として breakdown に出さない
        events = [
            _au("s_orphan", "p", "2026-05-01T10:05:00+00:00", "claude-sonnet-4-6", 100, 0),
        ]
        self.assertEqual(aggregate_session_breakdown(events), [])


if __name__ == "__main__":
    unittest.main()
