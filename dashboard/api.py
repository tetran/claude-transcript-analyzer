"""dashboard/api.py — build_dashboard_data 集計グルー (Issue #123 Phase 1).

dashboard/server.py から区画 C を切り出した。aggregate モジュールの集計群と
analyzer/ のメトリクスを束ねて /api/data JSON を組み立てる唯一の関数。
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

# Issue #99 / v0.8.0: session_breakdown for `/api/data` (cost / token / model 内訳)
from analyzer.cost import TOP_N_SESSIONS, aggregate_model_distribution, aggregate_session_breakdown
from analyzer.subagent import aggregate_subagent_failure_trend, aggregate_subagent_metrics

from dashboard.aggregate import (
    _filter_events_by_period,
    _filter_usage_events,
    _now_iso,
    aggregate_compact_density,
    aggregate_daily,
    aggregate_hourly_heatmap,
    aggregate_permission_breakdowns,
    aggregate_project_skill_matrix,
    aggregate_projects,
    aggregate_session_stats,
    aggregate_skill_cooccurrence,
    aggregate_skill_hibernating,
    aggregate_skill_invocation_breakdown,
    aggregate_skill_lifecycle,
    aggregate_skills,
    aggregate_subagents,
    load_health_alerts,
)
from dashboard.config import _PERIOD_DELTAS


def build_dashboard_data(
    events: list[dict],
    period: str = "all",
    *,
    now: Optional[datetime] = None,
) -> dict:
    """ダッシュボード API レスポンスを生成する (Issue #85: period toggle 対応).

    period: "7d" / "30d" / "90d" / "all" (それ以外は "all" 相当)。
      Overview / Patterns / KPI counter / session_stats の 12 field は period 適用後の events で集計する。
      Quality / Surface の 7 field は **常に全期間** で集計する (period 不変)。
    now: test 注入用。指定時は last_updated もこの値で override する (drift guard test 用途)。
    """
    # period 適用 view と未 filter view の二経路で aggregator に渡す。
    # period_events_raw: timestamp + 三段 pair-straddling filter 適用後の raw events.
    # period_events_usage: period_events_raw に _filter_usage_events (subagent invocation dedup) を適用後.
    # 三段で再 include した stop event は _filter_usage_events の dedup window
    # (INVOCATION_MERGE_WINDOW_SECONDS = 1.0s) と同じ window で動くので再脱落しない
    # (TestFilterEventsByPeriod::test_three_stage_filter_survives_filter_usage_events).
    period_events_raw = _filter_events_by_period(events, period, now=now)
    # codex round 4 / Issue #85: boundary-straddling lifecycle-only invocation で
    # rep event (= subagent_lifecycle_start) が pre-cutoff な場合、headline metrics
    # (`daily_trend` / `hourly_heatmap` / `project_breakdown`) に pre-cutoff 日付が
    # leak しないよう、_filter_usage_events に period cutoff を渡して rep ts を
    # paired stop で上書きする。`period == "all"` / 不正値 → cutoff=None で無効。
    _period_days = _PERIOD_DELTAS.get(period)
    _now_ref = now if now is not None else datetime.now(timezone.utc)
    _period_cutoff = (
        _now_ref - timedelta(days=_period_days) if _period_days is not None else None
    )
    period_events_usage = _filter_usage_events(period_events_raw, period_cutoff=_period_cutoff)

    # Quality / Surface 系は常に全期間。permission_breakdowns は raw events に適用。
    permission_breakdowns = aggregate_permission_breakdowns(events)

    # Issue #81 — Overview KPI 上段の "unique kinds" カウンタは TOP_N=10 cap を効かせない全件カウント。
    # ranking 配列 (`*_ranking` / `project_breakdown`) は引き続き上位 10 件 cap (UI 表示用)。
    # filter / dedup 慣習は aggregate_skills / aggregate_subagent_metrics / aggregate_projects と一致させる
    # (drift guard は test_dashboard.py::TestBuildDashboardData の `*_matches_*_when_below_cap`)。
    skill_kinds_set: set[str] = set()
    for ev in period_events_raw:
        if ev.get("event_type") in ("skill_tool", "user_slash_command"):
            name = ev.get("skill", "")
            if name:
                skill_kinds_set.add(name)
    subagent_kinds_total = len(aggregate_subagent_metrics(period_events_raw))
    project_kinds_set: set[str] = set()
    for ev in period_events_usage:
        project = ev.get("project", "")
        if project:
            project_kinds_set.add(project)

    last_updated = now.isoformat() if now is not None else _now_iso()

    return {
        "last_updated": last_updated,
        "total_events": len(period_events_usage),
        "skill_ranking": aggregate_skills(period_events_raw),
        "subagent_ranking": aggregate_subagents(period_events_raw),
        "skill_kinds_total": len(skill_kinds_set),
        "subagent_kinds_total": subagent_kinds_total,
        "project_total": len(project_kinds_set),
        # Issue #85: daily_trend stays in period-applied set despite frontend-deprecation (Issue #65)
        "daily_trend": aggregate_daily(period_events_usage),
        "project_breakdown": aggregate_projects(period_events_usage),
        "hourly_heatmap": aggregate_hourly_heatmap(period_events_usage),
        "skill_cooccurrence": aggregate_skill_cooccurrence(period_events_raw),
        "project_skill_matrix": aggregate_project_skill_matrix(period_events_raw),
        "subagent_failure_trend": aggregate_subagent_failure_trend(events),
        "permission_prompt_skill_breakdown": permission_breakdowns["skill"],
        "permission_prompt_subagent_breakdown": permission_breakdowns["subagent"],
        "compact_density": aggregate_compact_density(events),
        "session_stats": aggregate_session_stats(period_events_raw),
        "health_alerts": load_health_alerts(),
        "skill_invocation_breakdown": aggregate_skill_invocation_breakdown(events),
        "skill_lifecycle": aggregate_skill_lifecycle(events, now=now),
        "skill_hibernating": aggregate_skill_hibernating(events, now=now),
        # Issue #99 / v0.8.0: session 単位の token / cost / model 内訳 / service_tier。
        # boundary (session_start / session_end) は **全期間 events** から lookup、
        # content (assistant_usage / skill_tool) は **period_events_raw** で in-period
        # 限定。これで period 跨ぎ session (= session_start が pre-cutoff、in-period
        # に assistant_usage がある) も in-period の cost / token を保ったまま render
        # される (codex review Round 1 / cross-cutoff regression 対策)。
        "session_breakdown": aggregate_session_breakdown(
            events,
            period_events=period_events_raw,
            now=now,
            top_n=TOP_N_SESSIONS,
        ),
        # Issue #106 / v0.8.0: Overview モデル分布パネル。period_events_raw 経由で
        # session_breakdown と semantics を揃える (period 連動 / subagent assistant_usage 包含)。
        "model_distribution": aggregate_model_distribution(period_events_raw),
        "period_applied": period if period in _PERIOD_DELTAS or period == "all" else "all",
    }
