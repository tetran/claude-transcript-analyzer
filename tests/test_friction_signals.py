"""tests/test_friction_signals.py — Issue #61 friction signals (A2 + A3) のテスト。

A2: permission notification を直前の skill_tool / subagent invocation に
    session 内時系列リンクで帰属させ、skill / subagent ごとに
    permission_rate = 帰属 prompt 数 / 総起動数 を算出。

A3: compact_start を session 単位で集計し、回数の histogram (0/1/2/3+) と
    worst session top 10 を返す。

帰属 algorithm は execution-window (interval-cover) 優先 + 直前 backward
fallback の 2 段階。subagent invocation の interval 解釈は
`subagent_metrics.subagent_invocation_interval` helper に委譲する。

詳細は `docs/plans/archive/issue-61-friction-signals.md` を参照。
"""
# pylint: disable=line-too-long
import importlib.util
import os
from pathlib import Path

import subagent_metrics

_DASHBOARD_PATH = Path(__file__).parent.parent / "dashboard" / "server.py"


def load_dashboard_module(usage_jsonl: Path, alerts_jsonl: Path | None = None):
    """USAGE_JSONL をパッチした状態で dashboard モジュールを読み込む (test_dashboard.py 流)。"""
    os.environ["USAGE_JSONL"] = str(usage_jsonl)
    if alerts_jsonl is not None:
        os.environ["HEALTH_ALERTS_JSONL"] = str(alerts_jsonl)
    try:
        spec = importlib.util.spec_from_file_location("dashboard_server_friction", _DASHBOARD_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del os.environ["USAGE_JSONL"]
        if alerts_jsonl is not None:
            del os.environ["HEALTH_ALERTS_JSONL"]
    return mod


# ---- event factory helpers -------------------------------------------------

def _skill(name, session, ts, duration_ms=None, success=True, project="p"):
    ev = {
        "event_type": "skill_tool",
        "skill": name,
        "project": project,
        "session_id": session,
        "timestamp": ts,
        "success": success,
    }
    if duration_ms is not None:
        ev["duration_ms"] = duration_ms
    return ev


def _slash(name, session, ts, project="p"):
    return {
        "event_type": "user_slash_command",
        "skill": name,
        "args": "",
        "source": "expansion",
        "project": project,
        "session_id": session,
        "timestamp": ts,
    }


def _subagent_start(name, session, ts, duration_ms=None, success=True, project="p"):
    ev = {
        "event_type": "subagent_start",
        "subagent_type": name,
        "project": project,
        "session_id": session,
        "timestamp": ts,
        "success": success,
    }
    if duration_ms is not None:
        ev["duration_ms"] = duration_ms
    return ev


def _subagent_lifecycle(name, session, ts, duration_ms=None, project="p"):
    ev = {
        "event_type": "subagent_lifecycle_start",
        "subagent_type": name,
        "project": project,
        "session_id": session,
        "timestamp": ts,
    }
    if duration_ms is not None:
        ev["duration_ms"] = duration_ms
    return ev


def _subagent_stop(name, session, ts, duration_ms=None, success=True):
    ev = {
        "event_type": "subagent_stop",
        "subagent_type": name,
        "subagent_id": "agent_x",
        "session_id": session,
        "timestamp": ts,
        "success": success,
    }
    if duration_ms is not None:
        ev["duration_ms"] = duration_ms
    return ev


def _notification(session, ts, ntype="permission", project="p"):
    return {
        "event_type": "notification",
        "notification_type": ntype,
        "project": project,
        "session_id": session,
        "timestamp": ts,
    }


def _session_start(session, ts, project="p"):
    return {
        "event_type": "session_start",
        "source": "startup",
        "model": "claude-opus-4-7",
        "project": project,
        "session_id": session,
        "timestamp": ts,
    }


def _compact_start(session, ts, trigger="auto", project="p"):
    return {
        "event_type": "compact_start",
        "trigger": trigger,
        "project": project,
        "session_id": session,
        "timestamp": ts,
    }


# ============================================================
#  TestSubagentInvocationInterval — subagent_metrics 側 helper
# ============================================================

class TestSubagentInvocationInterval:
    def test_subagent_start_uses_end_timestamp_interval(self):
        # event_type="subagent_start", duration_ms=10_000, ts=T → (T - 10s, T)
        ev = _subagent_start("Explore", "s", "2026-01-01T00:00:10+00:00", duration_ms=10_000)
        start, end = subagent_metrics.subagent_invocation_interval(ev)
        assert end - start == 10.0
        # end timestamp = ts (subagent_start は終了時刻 timestamp 慣習)
        from datetime import datetime
        assert end == datetime.fromisoformat(ev["timestamp"]).timestamp()

    def test_subagent_lifecycle_only_uses_start_timestamp_interval(self):
        # event_type="subagent_lifecycle_start", duration_ms=10_000, ts=T → (T, T+10s)
        ev = _subagent_lifecycle("Explore", "s", "2026-01-01T00:00:00+00:00", duration_ms=10_000)
        start, end = subagent_metrics.subagent_invocation_interval(ev)
        assert end - start == 10.0
        from datetime import datetime
        assert start == datetime.fromisoformat(ev["timestamp"]).timestamp()

    def test_no_duration_returns_point_interval(self):
        ev = _subagent_start("Explore", "s", "2026-01-01T00:00:00+00:00")  # no duration_ms
        start, end = subagent_metrics.subagent_invocation_interval(ev)
        assert start == end

    def test_both_hooks_invocation_uses_subagent_start_representative(self):
        # usage_invocation_events() は両 hook 発火 invocation で subagent_start を代表に選ぶ。
        # 代表 ev の timestamp = 終了時刻 → interval = [end - duration, end] になることを pin。
        events = [
            _subagent_start("Explore", "s", "2026-01-01T00:01:00+00:00", duration_ms=60_000),
            _subagent_lifecycle("Explore", "s", "2026-01-01T00:00:59.500+00:00"),
        ]
        repr_evs = subagent_metrics.usage_invocation_events(events)
        assert len(repr_evs) == 1
        assert repr_evs[0]["event_type"] == "subagent_start"
        start, end = subagent_metrics.subagent_invocation_interval(repr_evs[0])
        assert end - start == 60.0  # 60 秒間の invocation

    def test_invalid_timestamp_returns_zero_or_skipped_interval(self):
        # 不正な timestamp でも例外を出さず (0.0, 0.0) など safe な値を返すこと
        ev = {"event_type": "subagent_start", "timestamp": "", "duration_ms": 1000}
        start, end = subagent_metrics.subagent_invocation_interval(ev)
        assert start == 0.0 and end == 0.0
        ev2 = {"event_type": "subagent_start", "timestamp": "not-an-iso", "duration_ms": 1000}
        start, end = subagent_metrics.subagent_invocation_interval(ev2)
        assert start == 0.0 and end == 0.0


# ============================================================
#  TestPermissionLinkAlgorithm — 帰属 algorithm
# ============================================================

class TestPermissionLinkAlgorithm:
    def test_no_notification_returns_empty_breakdowns(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_skill("a", "s", "2026-01-01T00:00:00+00:00")]
        result = mod.aggregate_permission_breakdowns(events)
        assert result == {"skill": [], "subagent": []}

    def test_notification_without_candidates_is_dropped(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _notification("s", "2026-01-01T00:05:00+00:00"),
            # no skill / subagent in this session
        ]
        result = mod.aggregate_permission_breakdowns(events)
        assert result == {"skill": [], "subagent": []}

    def test_skill_in_backward_window_attributed(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _skill("a", "s", "2026-01-01T00:00:00+00:00"),  # skill ts
            _notification("s", "2026-01-01T00:00:10+00:00"),  # +10s within 30s window
        ]
        result = mod.aggregate_permission_breakdowns(events)
        skills = result["skill"]
        assert len(skills) == 1 and skills[0]["skill"] == "a"
        assert skills[0]["prompt_count"] == 1

    def test_skill_outside_window_not_attributed(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _skill("a", "s", "2026-01-01T00:00:00+00:00"),
            _notification("s", "2026-01-01T00:00:31+00:00"),  # +31s, outside 30s
        ]
        result = mod.aggregate_permission_breakdowns(events)
        assert result == {"skill": [], "subagent": []}

    def test_skill_in_execution_interval_attributed(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # skill_tool: end_ts = T+5s, duration = 10s → interval = [T-5s, T+5s]
        # notification ts = T → covers
        events = [
            _skill("a", "s", "2026-01-01T00:00:05+00:00", duration_ms=10_000),
            _notification("s", "2026-01-01T00:00:00+00:00"),
        ]
        result = mod.aggregate_permission_breakdowns(events)
        skills = result["skill"]
        assert len(skills) == 1 and skills[0]["skill"] == "a"

    def test_subagent_lifecycle_start_in_interval_attributed(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # lifecycle-only invocation: ts=T-300s, duration_ms=600_000 (10 min)
        # interval = [T-300s, T+300s]. notification ts=T → covers
        events = [
            _subagent_lifecycle("Explore", "s", "2026-01-01T00:00:00+00:00", duration_ms=600_000),
            _notification("s", "2026-01-01T00:05:00+00:00"),
        ]
        result = mod.aggregate_permission_breakdowns(events)
        subs = result["subagent"]
        assert len(subs) == 1 and subs[0]["subagent_type"] == "Explore"
        assert subs[0]["prompt_count"] == 1

    def test_subagent_postooluse_end_after_notif_via_interval(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # subagent_start: end_ts=T+100s, duration=200s → interval=[T-100s, T+100s]
        # notification ts=T → covers via interval (backward window だけだと帰属しない)
        events = [
            _subagent_start("Explore", "s", "2026-01-01T00:01:40+00:00", duration_ms=200_000),
            _notification("s", "2026-01-01T00:00:00+00:00"),
        ]
        result = mod.aggregate_permission_breakdowns(events)
        subs = result["subagent"]
        assert len(subs) == 1 and subs[0]["subagent_type"] == "Explore"

    def test_multiple_candidates_attribute_to_most_recent(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # skill A ts=T-20s, skill B ts=T-5s (both in backward window)
        # → B に帰属 (より直近)
        events = [
            _skill("A", "s", "2026-01-01T00:00:00+00:00"),
            _skill("B", "s", "2026-01-01T00:00:15+00:00"),
            _notification("s", "2026-01-01T00:00:20+00:00"),
        ]
        result = mod.aggregate_permission_breakdowns(events)
        skills = result["skill"]
        names = {s["skill"]: s["prompt_count"] for s in skills}
        assert names.get("B", 0) == 1
        assert names.get("A", 0) == 0  # 0 のため出力に含まれない

    def test_skill_and_subagent_disjoint_attribution(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # skill_tool ts=T-10s + subagent_start ts=T+0s (duration=5s, interval=[T-5s,T+0s])
        # 両方候補だが「直近」= subagent (interval covers, end_ts=T 直近)
        events = [
            _skill("a", "s", "2026-01-01T00:00:00+00:00"),
            _subagent_start("Explore", "s", "2026-01-01T00:00:10+00:00", duration_ms=5_000),
            _notification("s", "2026-01-01T00:00:10+00:00"),
        ]
        result = mod.aggregate_permission_breakdowns(events)
        # subagent に帰属、skill には帰属しない
        assert any(s["subagent_type"] == "Explore" and s["prompt_count"] == 1 for s in result["subagent"])
        assert all(s["prompt_count"] == 0 for s in result["skill"]) or result["skill"] == []

    def test_permission_and_permission_prompt_unified(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _skill("a", "s", "2026-01-01T00:00:00+00:00"),
            _notification("s", "2026-01-01T00:00:05+00:00", ntype="permission"),
            _notification("s", "2026-01-01T00:00:10+00:00", ntype="permission_prompt"),
        ]
        result = mod.aggregate_permission_breakdowns(events)
        skills = result["skill"]
        assert len(skills) == 1 and skills[0]["prompt_count"] == 2

    def test_user_slash_command_not_a_candidate(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _slash("/insights", "s", "2026-01-01T00:00:00+00:00"),
            _notification("s", "2026-01-01T00:00:05+00:00"),
        ]
        result = mod.aggregate_permission_breakdowns(events)
        assert result == {"skill": [], "subagent": []}

    def test_different_session_not_linked(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _skill("a", "sA", "2026-01-01T00:00:00+00:00"),
            _notification("sB", "2026-01-01T00:00:05+00:00"),
        ]
        result = mod.aggregate_permission_breakdowns(events)
        assert result == {"skill": [], "subagent": []}

    def test_subagent_lifecycle_only_invocation_uses_start_timestamp_as_interval_start(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # lifecycle-only invocation で subagent_start 不在
        # interval = [lifecycle.ts, lifecycle.ts + duration] になることを pin
        events = [
            _subagent_lifecycle("Explore", "s", "2026-01-01T00:00:00+00:00", duration_ms=120_000),
            _notification("s", "2026-01-01T00:01:00+00:00"),  # +60s, interval covers (0..120)
        ]
        result = mod.aggregate_permission_breakdowns(events)
        subs = result["subagent"]
        assert len(subs) == 1 and subs[0]["subagent_type"] == "Explore"

    def test_subagent_both_hooks_invocation_uses_end_timestamp(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # 両 hook 発火 invocation (1 秒以内 merge) → 代表 ev = subagent_start
        # ev.timestamp=終了時刻 → interval=[end - duration, end]
        # notification ts=end-30s で covers
        events = [
            _subagent_start("Explore", "s", "2026-01-01T00:01:00+00:00", duration_ms=60_000),
            _subagent_lifecycle("Explore", "s", "2026-01-01T00:00:59.500+00:00"),
            _notification("s", "2026-01-01T00:00:30+00:00"),
        ]
        result = mod.aggregate_permission_breakdowns(events)
        subs = result["subagent"]
        assert len(subs) == 1
        assert subs[0]["subagent_type"] == "Explore"
        assert subs[0]["prompt_count"] == 1
        assert subs[0]["invocation_count"] == 1

    def test_subagent_lifecycle_only_uses_paired_stop_duration(self, tmp_path):
        """Codex Round 1 P2 回帰: production の lifecycle-only invocation は
        `subagent_lifecycle_start` に duration_ms を持たない (`record_subagent.py`
        の `_handle_subagent_start` は duration_ms を書かない)。代わりに
        `subagent_stop` の duration_ms を fallback に使って interval を組み立てる。
        この経路が無いと長時間 invocation 中の permission を取りこぼす。
        """
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # lifecycle event は duration_ms 不在 (production reality)
        # 同 (session, type) bucket に subagent_stop が duration_ms=600_000 (10 分) で来る
        # interval = [lifecycle.ts, lifecycle.ts + 600s] になり、+5min の notification を cover
        events = [
            _subagent_lifecycle("Explore", "s", "2026-01-01T00:00:00+00:00"),  # no duration_ms
            _subagent_stop("Explore", "s", "2026-01-01T00:10:00+00:00", duration_ms=600_000),
            _notification("s", "2026-01-01T00:05:00+00:00"),
        ]
        result = mod.aggregate_permission_breakdowns(events)
        subs = result["subagent"]
        assert len(subs) == 1 and subs[0]["subagent_type"] == "Explore"
        assert subs[0]["prompt_count"] == 1, (
            "lifecycle-only invocation の interval が paired stop の duration_ms で "
            "復元されないと point interval に縮退して +5min notification を取りこぼす"
        )

    def test_usage_invocation_intervals_lifecycle_only_uses_stop_duration(self):
        """単体 helper レベルでも paired stop fallback を pin。
        `usage_invocation_intervals` 経由で lifecycle-only invocation の interval
        が `[lifecycle.ts, lifecycle.ts + stop.duration_ms/1000]` になること。
        """
        from datetime import datetime
        events = [
            _subagent_lifecycle("Explore", "s", "2026-01-01T00:00:00+00:00"),  # no duration_ms
            _subagent_stop("Explore", "s", "2026-01-01T00:02:00+00:00", duration_ms=120_000),
        ]
        intervals = subagent_metrics.usage_invocation_intervals(events)
        assert len(intervals) == 1
        start_ts, end_ts, rep = intervals[0]
        assert rep["event_type"] == "subagent_lifecycle_start"
        # interval 長さは paired stop の duration (120 秒) を反映
        assert end_ts - start_ts == 120.0
        assert start_ts == datetime.fromisoformat("2026-01-01T00:00:00+00:00").timestamp()


# ============================================================
#  TestPermissionBreakdownsAggregate — sort / rate / cap
# ============================================================

class TestPermissionBreakdownsAggregate:
    def test_invocation_count_matches_total_skill_tool(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # skill X が 5 回呼ばれて 2 回 permission → invocation_count=5, prompt_count=2
        events = []
        for i in range(5):
            events.append(_skill("X", "s", f"2026-01-01T00:00:{i:02d}+00:00"))
        events.append(_notification("s", "2026-01-01T00:00:01+00:00"))
        events.append(_notification("s", "2026-01-01T00:00:03+00:00"))
        result = mod.aggregate_permission_breakdowns(events)
        skills = result["skill"]
        assert len(skills) == 1
        assert skills[0]["invocation_count"] == 5
        assert skills[0]["prompt_count"] == 2

    def test_invocation_count_matches_aggregate_subagent_metrics(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # subagent Y が 3 invocation あり、aggregate_subagent_metrics(events)["Y"].count == 3 と一致
        events = [
            _subagent_start("Y", "sA", "2026-01-01T00:00:00+00:00"),
            _subagent_start("Y", "sB", "2026-01-01T00:01:00+00:00"),
            _subagent_start("Y", "sC", "2026-01-01T00:02:00+00:00"),
            _notification("sA", "2026-01-01T00:00:01+00:00"),
        ]
        result = mod.aggregate_permission_breakdowns(events)
        metrics = subagent_metrics.aggregate_subagent_metrics(events)
        subs = result["subagent"]
        assert subs[0]["subagent_type"] == "Y"
        assert subs[0]["invocation_count"] == metrics["Y"]["count"]

    def test_permission_rate_calculation(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # prompt=2, invocation=8 → rate=0.25
        events = []
        for i in range(8):
            events.append(_skill("X", "s", f"2026-01-01T00:00:{i:02d}+00:00"))
        events.append(_notification("s", "2026-01-01T00:00:01+00:00"))
        events.append(_notification("s", "2026-01-01T00:00:03+00:00"))
        result = mod.aggregate_permission_breakdowns(events)
        skills = result["skill"]
        assert skills[0]["permission_rate"] == 0.25

    def test_rate_can_exceed_one(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # 1 invocation で 2 permission → rate=2.0 (clamp しない)
        events = [
            _skill("X", "s", "2026-01-01T00:00:00+00:00"),
            _notification("s", "2026-01-01T00:00:01+00:00"),
            _notification("s", "2026-01-01T00:00:02+00:00"),
        ]
        result = mod.aggregate_permission_breakdowns(events)
        skills = result["skill"]
        assert skills[0]["prompt_count"] == 2
        assert skills[0]["invocation_count"] == 1
        assert skills[0]["permission_rate"] == 2.0

    def test_top_n_cap(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # 12 skill が prompt を持つとき返り値は 10 件
        events = []
        for i in range(12):
            name = f"skill_{i:02d}"
            events.append(_skill(name, f"s{i}", "2026-01-01T00:00:00+00:00"))
            events.append(_notification(f"s{i}", "2026-01-01T00:00:01+00:00"))
        result = mod.aggregate_permission_breakdowns(events)
        assert len(result["skill"]) == 10

    def test_sort_by_prompt_count_desc_then_name_asc(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # alpha:3 prompts, beta:3 prompts, gamma:2 prompts
        events = []
        for name, n in [("alpha", 3), ("beta", 3), ("gamma", 2)]:
            for j in range(n):
                events.append(_skill(name, f"s_{name}_{j}", "2026-01-01T00:00:00+00:00"))
                events.append(_notification(f"s_{name}_{j}", "2026-01-01T00:00:01+00:00"))
        result = mod.aggregate_permission_breakdowns(events)
        names_in_order = [s["skill"] for s in result["skill"]]
        # prompt_count 降順 (3,3,2)、同点は name 昇順 (alpha < beta)
        assert names_in_order == ["alpha", "beta", "gamma"]

    def test_zero_prompt_skill_not_in_output(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # skill X は invocation=10 / prompt=0 → 出力に含まれない
        events = []
        for i in range(10):
            events.append(_skill("X", "s", f"2026-01-01T00:00:{i:02d}+00:00"))
        # no notifications
        result = mod.aggregate_permission_breakdowns(events)
        assert result["skill"] == []

    def test_subagent_attribution_count_matches_metrics_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # P2 反映: drift guard。type 単位 invocation_count 合計が
        # aggregate_subagent_metrics[name].count と常に一致
        events = [
            _subagent_start("Explore", "s1", "2026-01-01T00:00:00+00:00"),
            _subagent_start("Explore", "s2", "2026-01-01T00:01:00+00:00"),
            _subagent_lifecycle("Explore", "s2", "2026-01-01T00:01:00.500+00:00"),  # merged with above
            _subagent_start("Plan", "s1", "2026-01-01T00:02:00+00:00"),
            _notification("s1", "2026-01-01T00:00:05+00:00"),
            _notification("s1", "2026-01-01T00:02:05+00:00"),
        ]
        result = mod.aggregate_permission_breakdowns(events)
        metrics = subagent_metrics.aggregate_subagent_metrics(events)
        for entry in result["subagent"]:
            t = entry["subagent_type"]
            assert entry["invocation_count"] == metrics[t]["count"], \
                f"drift: subagent table invocation_count != metrics count for {t}"

    def test_skill_and_subagent_prompt_counts_sum_le_total_notifications(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # 2-Q1 反映: 合算 invariant。1 notification は skill OR subagent の 1 候補のみ
        # → 合算は notification 数を超えない (orphan あれば等号未満)
        events = [
            _skill("a", "s", "2026-01-01T00:00:00+00:00"),
            _subagent_start("Explore", "s", "2026-01-01T00:01:00+00:00", duration_ms=5_000),
            _notification("s", "2026-01-01T00:00:05+00:00"),  # → skill
            _notification("s", "2026-01-01T00:01:00+00:00"),  # → subagent (within interval)
            _notification("s", "2026-02-01T00:00:00+00:00"),  # 遠すぎる → orphan (どちらにも帰属しない)
        ]
        total_notifications = sum(
            1 for ev in events
            if ev.get("event_type") == "notification"
            and ev.get("notification_type") in {"permission", "permission_prompt"}
        )
        result = mod.aggregate_permission_breakdowns(events)
        attributed = (
            sum(s["prompt_count"] for s in result["skill"])
            + sum(s["prompt_count"] for s in result["subagent"])
        )
        assert attributed <= total_notifications
        # 構造的に「合算で 2 / orphan 1」になる構成
        assert attributed == 2


# ============================================================
#  TestCompactDensity — A3
# ============================================================

class TestCompactDensity:
    def test_empty_events_returns_zero_buckets(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        result = mod.aggregate_compact_density([])
        assert result["histogram"] == {"0": 0, "1": 0, "2": 0, "3+": 0}
        assert result["worst_sessions"] == []

    def test_session_with_zero_compacts_in_bucket_zero(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_session_start("s", "2026-01-01T00:00:00+00:00")]
        result = mod.aggregate_compact_density(events)
        assert result["histogram"]["0"] == 1

    def test_session_with_one_compact_in_bucket_one(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _session_start("s", "2026-01-01T00:00:00+00:00"),
            _compact_start("s", "2026-01-01T00:30:00+00:00"),
        ]
        result = mod.aggregate_compact_density(events)
        assert result["histogram"]["1"] == 1
        assert result["histogram"]["0"] == 0

    def test_session_with_two_compacts_in_bucket_two(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _session_start("s", "2026-01-01T00:00:00+00:00"),
            _compact_start("s", "2026-01-01T00:30:00+00:00"),
            _compact_start("s", "2026-01-01T01:00:00+00:00"),
        ]
        result = mod.aggregate_compact_density(events)
        assert result["histogram"]["2"] == 1

    def test_session_with_three_compacts_in_bucket_3plus(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _session_start("s", "2026-01-01T00:00:00+00:00"),
            _compact_start("s", "2026-01-01T00:30:00+00:00"),
            _compact_start("s", "2026-01-01T01:00:00+00:00"),
            _compact_start("s", "2026-01-01T01:30:00+00:00"),
        ]
        result = mod.aggregate_compact_density(events)
        assert result["histogram"]["3+"] == 1

    def test_session_with_five_compacts_in_bucket_3plus(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_session_start("s", "2026-01-01T00:00:00+00:00")]
        for i in range(5):
            events.append(_compact_start("s", f"2026-01-01T0{i}:00:00+00:00"))
        result = mod.aggregate_compact_density(events)
        # 3 / 4 / 5 すべて "3+"
        assert result["histogram"]["3+"] == 1

    def test_orphan_session_excluded_from_histogram(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # compact_start のみで session_start が無い → histogram に含まれない
        events = [_compact_start("orphan", "2026-01-01T00:30:00+00:00")]
        result = mod.aggregate_compact_density(events)
        for v in result["histogram"].values():
            assert v == 0

    def test_orphan_session_included_in_worst_sessions(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # session_start 無しでも worst_sessions には乗る
        events = [
            _compact_start("orphan", "2026-01-01T00:30:00+00:00"),
            _compact_start("orphan", "2026-01-01T01:00:00+00:00"),
        ]
        result = mod.aggregate_compact_density(events)
        assert any(w["session_id"] == "orphan" and w["count"] == 2 for w in result["worst_sessions"])

    def test_worst_sessions_sorted_count_desc_sid_asc(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _session_start("a", "2026-01-01T00:00:00+00:00"),
            _session_start("b", "2026-01-01T00:00:00+00:00"),
            _session_start("c", "2026-01-01T00:00:00+00:00"),
            _compact_start("c", "2026-01-01T00:30:00+00:00"),
            _compact_start("c", "2026-01-01T00:31:00+00:00"),
            _compact_start("a", "2026-01-01T00:30:00+00:00"),
            _compact_start("a", "2026-01-01T00:31:00+00:00"),
            _compact_start("b", "2026-01-01T00:30:00+00:00"),
        ]
        result = mod.aggregate_compact_density(events)
        # count 降順: a=2, c=2, b=1。同点 (a, c) は session_id 昇順
        ws = result["worst_sessions"]
        assert ws[0]["session_id"] == "a" and ws[0]["count"] == 2
        assert ws[1]["session_id"] == "c" and ws[1]["count"] == 2
        assert ws[2]["session_id"] == "b" and ws[2]["count"] == 1

    def test_worst_sessions_top_n_cap(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = []
        for i in range(11):
            events.append(_session_start(f"s{i:02d}", "2026-01-01T00:00:00+00:00"))
            events.append(_compact_start(f"s{i:02d}", "2026-01-01T00:30:00+00:00"))
        result = mod.aggregate_compact_density(events)
        assert len(result["worst_sessions"]) == 10

    def test_worst_session_uses_last_seen_project(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _session_start("s", "2026-01-01T00:00:00+00:00", project="A"),
            _compact_start("s", "2026-01-01T00:30:00+00:00", project="A"),
            _compact_start("s", "2026-01-01T01:00:00+00:00", project="B"),
        ]
        result = mod.aggregate_compact_density(events)
        assert result["worst_sessions"][0]["project"] == "B"

    def test_histogram_keys_are_strings(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        result = mod.aggregate_compact_density([])
        for k in result["histogram"]:
            assert isinstance(k, str)
        assert set(result["histogram"].keys()) == {"0", "1", "2", "3+"}

    def test_histogram_keys_always_present(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # 観測 0 でも 4 キーすべて 0 で出力
        result = mod.aggregate_compact_density([])
        assert result["histogram"] == {"0": 0, "1": 0, "2": 0, "3+": 0}


# ============================================================
#  TestBuildDashboardDataIncludesFrictionFields — Phase 3
# ============================================================

class TestBuildDashboardDataIncludesFrictionFields:
    def test_permission_prompt_skill_breakdown_key_present(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        data = mod.build_dashboard_data([])
        assert "permission_prompt_skill_breakdown" in data
        assert data["permission_prompt_skill_breakdown"] == []

    def test_permission_prompt_subagent_breakdown_key_present(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        data = mod.build_dashboard_data([])
        assert "permission_prompt_subagent_breakdown" in data
        assert data["permission_prompt_subagent_breakdown"] == []

    def test_compact_density_key_present(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        data = mod.build_dashboard_data([])
        assert "compact_density" in data
        assert data["compact_density"]["histogram"] == {"0": 0, "1": 0, "2": 0, "3+": 0}
        assert data["compact_density"]["worst_sessions"] == []

    def test_empty_events_returns_safe_defaults(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        data = mod.build_dashboard_data([])
        # 3 キーすべて defensive 不要な safe defaults
        assert isinstance(data["permission_prompt_skill_breakdown"], list)
        assert isinstance(data["permission_prompt_subagent_breakdown"], list)
        assert isinstance(data["compact_density"], dict)

    def test_constant_PERMISSION_LINK_WINDOW_SECONDS_value(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # 値変更時に明示的に test 更新を強制する
        assert mod.PERMISSION_LINK_WINDOW_SECONDS == 30
