"""tests/test_skill_surface.py — Issue #74 Surface タブ 3 panel 集計の TDD テスト。

3 aggregator:
- aggregate_skill_invocation_breakdown: Panel 1 (LLM 自律 vs ユーザー手動)
- aggregate_skill_lifecycle:            Panel 2 (初回・直近・30日・全期間・trend)
- aggregate_skill_hibernating:          Panel 3 (~/.claude/skills/*/SKILL.md cross-ref)

旧 Issue #62 の aggregate_slash_command_source_breakdown /
aggregate_instructions_loaded_breakdown / _compress_home_path は本 issue で全廃。

詳細仕様は `docs/spec/dashboard-api.md` を参照。
"""
# pylint: disable=line-too-long
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DASHBOARD_PATH = Path(__file__).parent.parent / "dashboard" / "server.py"


def load_dashboard_module(usage_jsonl: Path, alerts_jsonl: Path | None = None):
    """USAGE_JSONL をパッチした状態で dashboard モジュールを読み込む。"""
    os.environ["USAGE_JSONL"] = str(usage_jsonl)
    if alerts_jsonl is not None:
        os.environ["HEALTH_ALERTS_JSONL"] = str(alerts_jsonl)
    try:
        spec = importlib.util.spec_from_file_location("dashboard_server_surface", _DASHBOARD_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del os.environ["USAGE_JSONL"]
        if alerts_jsonl is not None:
            del os.environ["HEALTH_ALERTS_JSONL"]
    return mod


# ---- event factory helpers -------------------------------------------------

def _tool(skill, *, success=True, project="p", session="s", ts="2026-04-01T00:00:00+00:00"):
    """skill_tool event factory (PostToolUse(Skill) 由来)。"""
    return {
        "event_type": "skill_tool",
        "skill": skill,
        "project": project,
        "session_id": session,
        "timestamp": ts,
        "success": success,
    }


def _slash(skill, *, source="expansion", project="p", session="s", ts="2026-04-01T00:00:00+00:00"):
    """user_slash_command event factory (UserPromptExpansion / Submit 由来)。"""
    return {
        "event_type": "user_slash_command",
        "skill": skill,
        "args": "",
        "source": source,
        "project": project,
        "session_id": session,
        "timestamp": ts,
    }


# 固定 now (test 安定化のため、各 lifecycle / hibernating test で injection する)
_NOW = datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)


def _ts(days_ago: int) -> str:
    """now から days_ago 日前の ISO 8601 timestamp 文字列を返す。"""
    return (_NOW - timedelta(days=days_ago)).isoformat()


# ============================================================
#  TestNormalizeSkillName — skill 名正規化 (lstrip("/"))
# ============================================================
class TestNormalizeSkillName:
    """`_normalize_skill_name(raw)` は先頭 `/` を全て剥がす (Q1=A: lstrip)。"""

    def test_no_leading_slash_unchanged(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        assert mod._normalize_skill_name("codex-review") == "codex-review"

    def test_single_leading_slash_stripped(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        assert mod._normalize_skill_name("/codex-review") == "codex-review"

    def test_double_leading_slash_stripped_to_canonical(self, tmp_path):
        # Q1=A: lstrip("/") なので "//foo" も "foo" に正規化される
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        assert mod._normalize_skill_name("//codex-review") == "codex-review"

    def test_empty_string_stays_empty(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        assert mod._normalize_skill_name("") == ""

    def test_only_slashes_become_empty(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        assert mod._normalize_skill_name("/") == ""
        assert mod._normalize_skill_name("///") == ""


# ============================================================
#  TestSkillInvocationBreakdown — Panel 1
# ============================================================
class TestSkillInvocationBreakdown:
    """skill_tool / user_slash_command を skill ごとに集計し mode 3-way 分類。"""

    def test_empty_events_returns_empty_list(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        assert mod.aggregate_skill_invocation_breakdown([]) == []

    def test_dual_mode_with_autonomy_rate(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_tool("codex-review") for _ in range(24)]
            + [_slash("/codex-review")]
        )
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert len(out) == 1
        row = out[0]
        assert row["skill"] == "codex-review"
        assert row["mode"] == "dual"
        assert row["tool_count"] == 24
        assert row["slash_count"] == 1
        assert row["autonomy_rate"] == 0.96

    def test_llm_only_mode_autonomy_rate_null(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("frontend-design") for _ in range(5)]
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert len(out) == 1
        row = out[0]
        assert row["mode"] == "llm-only"
        assert row["tool_count"] == 5
        assert row["slash_count"] == 0
        assert row["autonomy_rate"] is None

    def test_user_only_mode_autonomy_rate_null(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_slash("/usage-archive") for _ in range(8)]
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert len(out) == 1
        row = out[0]
        assert row["skill"] == "usage-archive"
        assert row["mode"] == "user-only"
        assert row["tool_count"] == 0
        assert row["slash_count"] == 8
        assert row["autonomy_rate"] is None

    def test_skill_name_normalization_merges_tool_and_slash(self, tmp_path):
        # skill_tool="foo" と user_slash_command="/foo" は同一 skill に merge
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("foo"), _slash("/foo"), _slash("/foo")]
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert len(out) == 1
        assert out[0]["skill"] == "foo"
        assert out[0]["mode"] == "dual"
        assert out[0]["tool_count"] == 1
        assert out[0]["slash_count"] == 2

    def test_double_slash_skill_normalized_to_canonical(self, tmp_path):
        # Q1=A: "//foo" / "/foo" / "foo" すべて同一 skill に merge
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("foo"), _slash("/foo"), _slash("//foo")]
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert len(out) == 1
        assert out[0]["skill"] == "foo"
        assert out[0]["tool_count"] == 1
        assert out[0]["slash_count"] == 2

    def test_failed_skill_tool_counted(self, tmp_path):
        # B2: skill_tool.success=False (PostToolUseFailure 由来) も count に含める
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("foo", success=True), _tool("foo", success=False)]
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert out[0]["tool_count"] == 2
        assert out[0]["mode"] == "llm-only"

    def test_user_slash_source_value_ignored(self, tmp_path):
        # Panel 1 は source (expansion / submit) を区別しない
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _slash("/foo", source="expansion"),
            _slash("/foo", source="submit"),
            _slash("/foo", source="something_unknown"),
            _slash("/foo", source=None),
        ]
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert out[0]["slash_count"] == 4

    def test_empty_skill_name_skipped(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool(""), _tool("/"), _tool("//"), _slash("")]
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert out == []

    def test_whitespace_only_skill_name_skipped(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("   "), _slash("/   ")]
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert out == []

    def test_autonomy_rate_rounded_to_4_decimals(self, tmp_path):
        # tool=2, slash=1 → 0.6667 (4 桁丸め)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("foo"), _tool("foo"), _slash("/foo")]
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert out[0]["autonomy_rate"] == 0.6667

    def test_autonomy_rate_boundary_cases(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # 50/50
        events = [_tool("a"), _slash("/a")]
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert out[0]["autonomy_rate"] == 0.5
        # 1/0 (single tool, single slash → already tested above as 0.5)
        # 100/0 (= llm-only, autonomy_rate is null)
        events = [_tool("b") for _ in range(10)]
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert out[0]["autonomy_rate"] is None
        # 0/100 (= user-only, autonomy_rate is null)
        events = [_slash("/c") for _ in range(10)]
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert out[0]["autonomy_rate"] is None

    def test_sort_by_total_desc_then_skill_asc(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_tool("alpha") for _ in range(5)]    # total=5
            + [_tool("beta") for _ in range(5)]   # total=5 (skill 名昇順で alpha 後)
            + [_tool("gamma") for _ in range(3)]  # total=3
        )
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert [r["skill"] for r in out] == ["alpha", "beta", "gamma"]

    def test_top_n_cap_20(self, tmp_path):
        # 25 skill が観測のとき返り値は 20 件 (TOP_N_SKILL_INVOCATION = 20)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = []
        for i in range(25):
            events.extend([_tool(f"skill_{i:02d}") for _ in range(i + 1)])
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert len(out) == 20

    def test_constant_TOP_N_SKILL_INVOCATION(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        assert mod.TOP_N_SKILL_INVOCATION == 20

    def test_other_event_types_ignored(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            {"event_type": "session_start", "session_id": "s",
             "timestamp": "2026-04-01T00:00:00+00:00"},
            {"event_type": "notification", "notification_type": "permission",
             "session_id": "s", "timestamp": "2026-04-01T00:00:00+00:00"},
            {"event_type": "instructions_loaded", "memory_type": "Project",
             "load_reason": "session_start", "file_path": "/x",
             "session_id": "s", "timestamp": "2026-04-01T00:00:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Explore",
             "session_id": "s", "timestamp": "2026-04-01T00:00:00+00:00"},
        ]
        out = mod.aggregate_skill_invocation_breakdown(events)
        assert out == []


# ============================================================
#  TestSkillLifecycle — Panel 2
# ============================================================
class TestSkillLifecycle:
    """skill_tool + user_slash_command を merge して lifecycle 算出。"""

    def test_empty_events_returns_empty_list(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        assert mod.aggregate_skill_lifecycle([], now=_NOW) == []

    def test_first_seen_last_seen_iso_8601(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _tool("foo", ts=_ts(60)),
            _tool("foo", ts=_ts(1)),
            _tool("foo", ts=_ts(30)),
        ]
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert len(out) == 1
        row = out[0]
        # ISO 8601 で +00:00 付き
        assert row["first_seen"] == _ts(60)
        assert row["last_seen"] == _ts(1)
        assert "+00:00" in row["first_seen"]

    def test_count_30d_inclusive_both_ends(self, tmp_path):
        # B3: now - 30d <= ts <= now (両端 inclusive)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _tool("foo", ts=_ts(0)),    # 今 (含)
            _tool("foo", ts=_ts(30)),   # 30 日前ぴったり (含)
            _tool("foo", ts=_ts(31)),   # 31 日前 (含まない)
            _tool("foo", ts=_ts(60)),   # 60 日前 (含まない)
        ]
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert out[0]["count_30d"] == 2  # _ts(0) + _ts(30)
        assert out[0]["count_total"] == 4

    def test_skill_name_normalization_merge(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _tool("codex-review", ts=_ts(10)),
            _slash("/codex-review", ts=_ts(5)),
            _slash("//codex-review", ts=_ts(3)),
        ]
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert len(out) == 1
        assert out[0]["skill"] == "codex-review"
        assert out[0]["count_total"] == 3

    def test_failed_event_counted(self, tmp_path):
        # B2: success=False も count に含む
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _tool("foo", ts=_ts(10), success=True),
            _tool("foo", ts=_ts(5), success=False),
        ]
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert out[0]["count_total"] == 2

    def test_trend_new_when_first_seen_within_14_days(self, tmp_path):
        # days_since_first < 14 → "new" 最優先 (count に関係なく)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("fresh", ts=_ts(10)) for _ in range(20)]
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert out[0]["trend"] == "new"

    def test_trend_new_boundary_13_days(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("fresh", ts=_ts(13))]
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert out[0]["trend"] == "new"

    def test_trend_not_new_at_14_days(self, tmp_path):
        # days_since_first == 14 → new ではない (< 14 が new 条件)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("foo", ts=_ts(14))]
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert out[0]["trend"] != "new"

    def test_trend_accelerating(self, tmp_path):
        # first_seen=60d 前 / count_total=30 / count_30d=25
        # observation_days=60, recent_rate=25/30=0.833, overall_rate=30/60=0.5
        # ratio=0.833/0.5=1.667 > 1.5 → accelerating
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_tool("foo", ts=_ts(60))]
            + [_tool("foo", ts=_ts(50))]
            + [_tool("foo", ts=_ts(40))]
            + [_tool("foo", ts=_ts(35))]
            + [_tool("foo", ts=_ts(31))]
            + [_tool("foo", ts=_ts(d)) for d in range(30, 5, -1)]   # 25 件 in 30d window
        )
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert out[0]["trend"] == "accelerating"

    def test_trend_decelerating(self, tmp_path):
        # 60 日前から evenly 60 件、直近 30 日には 5 件しかない
        # observation_days=60, recent_rate=5/30=0.167, overall_rate=60/60=1.0
        # ratio=0.167 < 0.5 → decelerating
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("foo", ts=_ts(d)) for d in range(60, 30, -1)]  # 30 件 in 60-30d
        events += [_tool("foo", ts=_ts(d)) for d in range(60, 30, -1)]  # 60 件 total
        events += [_tool("foo", ts=_ts(d)) for d in [29, 25, 20, 15, 10]]  # 5 件 in 30d
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert out[0]["trend"] == "decelerating"

    def test_trend_stable(self, tmp_path):
        # 60 日 evenly に 60 件 → recent=overall → ratio=1.0 → stable
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("foo", ts=_ts(d)) for d in range(60, 0, -1)]
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert out[0]["trend"] == "stable"

    def test_observation_days_no_180_cap(self, tmp_path):
        # Q2: cap 撤廃。365 日 first / 200 件 / 直近 30d で 30 件
        # observation_days=365, recent_rate=1.0, overall_rate=200/365=0.548
        # ratio=1.0/0.548=1.825 > 1.5 → accelerating (cap 入れると stable に倒れる)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("foo", ts=_ts(d)) for d in range(365, 30, -2)][:170]  # ~170 件 in 365-30d
        events += [_tool("foo", ts=_ts(d)) for d in range(30, 0, -1)]         # 30 件 in 30d
        # ↑ 合計 200 件で first_seen=365 日前
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert out[0]["count_total"] >= 150
        # cap なしなら overall_rate < recent_rate になりやすく accelerating
        assert out[0]["trend"] == "accelerating"

    def test_sort_by_last_seen_desc_then_skill_asc(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _tool("apple", ts=_ts(20)),
            _tool("banana", ts=_ts(20)),  # 同 last_seen → skill 昇順で apple, banana
            _tool("cherry", ts=_ts(10)),  # last_seen より新しい → cherry が先頭
        ]
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert [r["skill"] for r in out] == ["cherry", "apple", "banana"]

    def test_top_n_cap_20(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = []
        for i in range(25):
            events.append(_tool(f"skill_{i:02d}", ts=_ts(i + 1)))
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert len(out) == 20

    def test_constant_TOP_N_SKILL_LIFECYCLE(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        assert mod.TOP_N_SKILL_LIFECYCLE == 20

    def test_unparseable_timestamp_skipped(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _tool("foo", ts="not-a-timestamp"),
            _tool("foo", ts=""),
            _tool("foo", ts=_ts(5)),
        ]
        out = mod.aggregate_skill_lifecycle(events, now=_NOW)
        assert out[0]["count_total"] == 1

    def test_now_defaults_to_utc_now_when_omitted(self, tmp_path):
        # now 未指定でもエラー無く動作することを smoke 確認
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("foo", ts=datetime.now(timezone.utc).isoformat())]
        out = mod.aggregate_skill_lifecycle(events)  # now omit
        assert len(out) == 1


# ============================================================
#  TestSkillHibernating — Panel 3
# ============================================================
def _make_skill_dir(skills_dir: Path, name: str, mtime_dt: datetime) -> Path:
    """skills_dir/<name>/SKILL.md を作成し mtime を設定。返り値は SKILL.md path。"""
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / "SKILL.md"
    f.write_text("placeholder\n", encoding="utf-8")
    ts = mtime_dt.timestamp()
    os.utime(f, (ts, ts))
    return f


class TestSkillHibernating:
    """~/.claude/skills/*/SKILL.md と usage を cross-reference して hibernation 分類。"""

    def test_skills_dir_absent_returns_empty(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        nonexistent = tmp_path / "does-not-exist"
        out = mod.aggregate_skill_hibernating([], skills_dir=nonexistent, now=_NOW)
        assert out == {"items": [], "scope_note": "user-level only", "active_excluded_count": 0}

    def test_empty_skills_dir_returns_empty_with_scope_note(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        out = mod.aggregate_skill_hibernating([], skills_dir=skills_dir, now=_NOW)
        assert out["items"] == []
        assert out["scope_note"] == "user-level only"
        assert out["active_excluded_count"] == 0

    def test_warming_up_unused_recent_install(self, tmp_path):
        # mtime 3 日前 / 未使用 → warming_up
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        _make_skill_dir(skills_dir, "fresh", _NOW - timedelta(days=3))
        out = mod.aggregate_skill_hibernating([], skills_dir=skills_dir, now=_NOW)
        assert len(out["items"]) == 1
        item = out["items"][0]
        assert item["skill"] == "fresh"
        assert item["status"] == "warming_up"
        assert item["last_seen"] is None
        assert item["days_since_last_use"] is None

    def test_warming_up_boundary_at_14_days(self, tmp_path):
        # mtime ちょうど 14 日前 / 未使用 → warming_up (= mtime >= now-14d)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        _make_skill_dir(skills_dir, "boundary", _NOW - timedelta(days=14))
        out = mod.aggregate_skill_hibernating([], skills_dir=skills_dir, now=_NOW)
        assert out["items"][0]["status"] == "warming_up"

    def test_idle_unused_old_install(self, tmp_path):
        # mtime 60 日前 / 未使用 → idle (古い install で死蔵)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        _make_skill_dir(skills_dir, "ancient", _NOW - timedelta(days=60))
        out = mod.aggregate_skill_hibernating([], skills_dir=skills_dir, now=_NOW)
        assert out["items"][0]["status"] == "idle"
        assert out["items"][0]["last_seen"] is None
        assert out["items"][0]["days_since_last_use"] is None

    def test_active_skill_excluded_from_items(self, tmp_path):
        # last_use 7 日前 → active → items に含まれない、active_excluded_count に 1
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        _make_skill_dir(skills_dir, "active", _NOW - timedelta(days=60))
        events = [_tool("active", ts=_ts(7))]
        out = mod.aggregate_skill_hibernating(events, skills_dir=skills_dir, now=_NOW)
        assert out["items"] == []
        assert out["active_excluded_count"] == 1

    def test_active_boundary_at_14_days_excluded(self, tmp_path):
        # last_use ちょうど 14 日前 → active 除外 (last_seen >= now - 14d は inclusive)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        _make_skill_dir(skills_dir, "edge", _NOW - timedelta(days=60))
        events = [_tool("edge", ts=_ts(14))]
        out = mod.aggregate_skill_hibernating(events, skills_dir=skills_dir, now=_NOW)
        assert out["items"] == []
        assert out["active_excluded_count"] == 1

    def test_resting_15_to_30_days_unused(self, tmp_path):
        # last_use 15 日前 → resting (14 < days_since_use <= 30)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        _make_skill_dir(skills_dir, "rest", _NOW - timedelta(days=60))
        events = [_tool("rest", ts=_ts(15))]
        out = mod.aggregate_skill_hibernating(events, skills_dir=skills_dir, now=_NOW)
        assert out["items"][0]["status"] == "resting"
        assert out["items"][0]["days_since_last_use"] == 15

    def test_resting_boundary_at_30_days(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        _make_skill_dir(skills_dir, "edge", _NOW - timedelta(days=60))
        events = [_tool("edge", ts=_ts(30))]
        out = mod.aggregate_skill_hibernating(events, skills_dir=skills_dir, now=_NOW)
        assert out["items"][0]["status"] == "resting"

    def test_idle_over_30_days_unused(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        _make_skill_dir(skills_dir, "idle1", _NOW - timedelta(days=90))
        events = [_tool("idle1", ts=_ts(31))]
        out = mod.aggregate_skill_hibernating(events, skills_dir=skills_dir, now=_NOW)
        assert out["items"][0]["status"] == "idle"
        assert out["items"][0]["days_since_last_use"] == 31

    def test_skill_name_cross_reference_normalization(self, tmp_path):
        # skills_dir のディレクトリ名と skill_tool.skill / user_slash_command.skill が
        # 正規化後一致する (Q1=A: lstrip("/") 適用)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        _make_skill_dir(skills_dir, "codex-review", _NOW - timedelta(days=60))
        events = [
            _tool("codex-review", ts=_ts(20)),    # skill_tool は slash なし
            _slash("/codex-review", ts=_ts(20)),  # slash command は / 付き
        ]
        out = mod.aggregate_skill_hibernating(events, skills_dir=skills_dir, now=_NOW)
        # 両方 active 判定: 20 日前 → resting
        assert out["items"][0]["status"] == "resting"

    def test_sort_status_order_warming_resting_idle(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        _make_skill_dir(skills_dir, "z-warm", _NOW - timedelta(days=3))    # warming_up
        _make_skill_dir(skills_dir, "a-rest", _NOW - timedelta(days=60))   # resting
        _make_skill_dir(skills_dir, "m-idle", _NOW - timedelta(days=90))   # idle
        events = [
            _tool("a-rest", ts=_ts(20)),  # resting
            _tool("m-idle", ts=_ts(40)),  # idle
        ]
        out = mod.aggregate_skill_hibernating(events, skills_dir=skills_dir, now=_NOW)
        assert [it["status"] for it in out["items"]] == ["warming_up", "resting", "idle"]

    def test_sort_warming_up_by_mtime_desc(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        _make_skill_dir(skills_dir, "older",  _NOW - timedelta(days=10))
        _make_skill_dir(skills_dir, "newer",  _NOW - timedelta(days=2))
        _make_skill_dir(skills_dir, "middle", _NOW - timedelta(days=5))
        out = mod.aggregate_skill_hibernating([], skills_dir=skills_dir, now=_NOW)
        # mtime desc → newer, middle, older
        assert [it["skill"] for it in out["items"]] == ["newer", "middle", "older"]

    def test_sort_resting_by_days_since_use_desc(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        _make_skill_dir(skills_dir, "a", _NOW - timedelta(days=60))
        _make_skill_dir(skills_dir, "b", _NOW - timedelta(days=60))
        events = [_tool("a", ts=_ts(20)), _tool("b", ts=_ts(28))]
        out = mod.aggregate_skill_hibernating(events, skills_dir=skills_dir, now=_NOW)
        # b: 28 日前 / a: 20 日前 → desc で b, a
        assert [it["skill"] for it in out["items"]] == ["b", "a"]

    def test_sort_idle_by_max_use_or_install_desc(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        # x: install 90 日前 / 未使用 → max(install=90, use=0) = 90
        # y: install 60 日前 / use 50 日前 → max(50, 60) = 60
        # z: install 200 日前 / use 35 日前 → max(35, 200) = 200
        _make_skill_dir(skills_dir, "x", _NOW - timedelta(days=90))
        _make_skill_dir(skills_dir, "y", _NOW - timedelta(days=60))
        _make_skill_dir(skills_dir, "z", _NOW - timedelta(days=200))
        events = [_tool("y", ts=_ts(50)), _tool("z", ts=_ts(35))]
        out = mod.aggregate_skill_hibernating(events, skills_dir=skills_dir, now=_NOW)
        # idle のうち desc で z(200), x(90), y(60)
        assert [it["skill"] for it in out["items"]] == ["z", "x", "y"]

    def test_env_override_skills_dir(self, tmp_path, monkeypatch):
        # 引数なし + SKILLS_DIR 環境変数で test fixture を指せる
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "alt-skills"
        _make_skill_dir(skills_dir, "via-env", _NOW - timedelta(days=3))
        monkeypatch.setenv("SKILLS_DIR", str(skills_dir))
        out = mod.aggregate_skill_hibernating([], now=_NOW)
        assert any(it["skill"] == "via-env" for it in out["items"])

    def test_dir_without_skill_md_skipped(self, tmp_path):
        # SKILL.md が無いディレクトリは listing から除外
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "no-skill-md").mkdir()  # SKILL.md なし
        _make_skill_dir(skills_dir, "real", _NOW - timedelta(days=3))
        out = mod.aggregate_skill_hibernating([], skills_dir=skills_dir, now=_NOW)
        assert [it["skill"] for it in out["items"]] == ["real"]

    def test_broken_symlink_silently_skipped(self, tmp_path):
        # 改善#4: 壊れた symlink で OSError が出ても他の skill は処理される
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        # 壊れた symlink を skills_dir 直下に配置
        broken = skills_dir / "broken"
        broken.symlink_to(tmp_path / "does-not-exist")
        _make_skill_dir(skills_dir, "real", _NOW - timedelta(days=3))
        out = mod.aggregate_skill_hibernating([], skills_dir=skills_dir, now=_NOW)
        assert [it["skill"] for it in out["items"]] == ["real"]

    def test_active_excluded_count_increments(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        skills_dir = tmp_path / "skills"
        _make_skill_dir(skills_dir, "a-active", _NOW - timedelta(days=60))
        _make_skill_dir(skills_dir, "b-active", _NOW - timedelta(days=60))
        _make_skill_dir(skills_dir, "c-rest",   _NOW - timedelta(days=60))
        events = [
            _tool("a-active", ts=_ts(7)),
            _tool("b-active", ts=_ts(3)),
            _tool("c-rest",   ts=_ts(20)),
        ]
        out = mod.aggregate_skill_hibernating(events, skills_dir=skills_dir, now=_NOW)
        assert out["active_excluded_count"] == 2
        assert [it["skill"] for it in out["items"]] == ["c-rest"]


# ============================================================
#  TestBuildDashboardDataIncludesSurfaceFields — payload 統合
# ============================================================
class TestBuildDashboardDataIncludesSurfaceFields:
    def test_skill_invocation_breakdown_key_present(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("foo")]
        data = mod.build_dashboard_data(events)
        assert "skill_invocation_breakdown" in data
        assert isinstance(data["skill_invocation_breakdown"], list)

    def test_skill_lifecycle_key_present(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_tool("foo", ts=_ts(5))]
        data = mod.build_dashboard_data(events)
        assert "skill_lifecycle" in data
        assert isinstance(data["skill_lifecycle"], list)

    def test_skill_hibernating_key_present(self, tmp_path, monkeypatch):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        # build_dashboard_data 経路では SKILLS_DIR 既定 = ~/.claude/skills の可能性。
        # test 安定化のため空 tmp dir を SKILLS_DIR に設定。
        empty_dir = tmp_path / "empty-skills"
        empty_dir.mkdir()
        monkeypatch.setenv("SKILLS_DIR", str(empty_dir))
        data = mod.build_dashboard_data([])
        assert "skill_hibernating" in data
        h = data["skill_hibernating"]
        assert isinstance(h, dict)
        assert "items" in h
        assert "scope_note" in h
        assert "active_excluded_count" in h

    def test_old_fields_removed(self, tmp_path, monkeypatch):
        # regression guard: 旧 field 名が build_dashboard_data の output に残っていない
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        empty_dir = tmp_path / "empty-skills"
        empty_dir.mkdir()
        monkeypatch.setenv("SKILLS_DIR", str(empty_dir))
        data = mod.build_dashboard_data([])
        assert "slash_command_source_breakdown" not in data
        assert "instructions_loaded_breakdown" not in data

    def test_empty_events_returns_safe_defaults(self, tmp_path, monkeypatch):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        empty_dir = tmp_path / "empty-skills"
        empty_dir.mkdir()
        monkeypatch.setenv("SKILLS_DIR", str(empty_dir))
        data = mod.build_dashboard_data([])
        assert data["skill_invocation_breakdown"] == []
        assert data["skill_lifecycle"] == []
        assert data["skill_hibernating"] == {
            "items": [], "scope_note": "user-level only", "active_excluded_count": 0
        }

    def test_json_roundtrip_preserves_schema(self, tmp_path, monkeypatch):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        empty_dir = tmp_path / "empty-skills"
        empty_dir.mkdir()
        monkeypatch.setenv("SKILLS_DIR", str(empty_dir))
        data = mod.build_dashboard_data([])
        roundtripped = json.loads(json.dumps(data))
        assert "skill_invocation_breakdown" in roundtripped
        assert "skill_lifecycle" in roundtripped
        assert "skill_hibernating" in roundtripped
