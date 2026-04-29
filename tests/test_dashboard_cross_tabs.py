"""tests/test_dashboard_cross_tabs.py — Issue #59 cross-tab viz aggregator のテスト。

B1: `aggregate_skill_cooccurrence(events, top_n=100)` — 同一 session 内の skill pair
  を集計し、count 降順 + pair lexicographic 昇順 の list を返す。
  `count` は **両 skill が両方登場した unique session 数** (invocation 数ではない)。

B2: `aggregate_project_skill_matrix(events, top_projects=10, top_skills=10)` —
  project (top 10) × skill (top 10) の dense 2D matrix + covered_count + total_count を返す。

両 aggregator とも `aggregate_skills` と同じ filter 慣習で raw events を受け取り、
内部で `skill_tool` / `user_slash_command` のみに絞る (subagent は対象外)。

詳細は `docs/plans/archive/issue-59-cross-tab-viz.md` を参照。
"""
# pylint: disable=line-too-long
import importlib.util
import os
from pathlib import Path

_DASHBOARD_PATH = Path(__file__).parent.parent / "dashboard" / "server.py"


def load_dashboard_module(usage_jsonl: Path, alerts_jsonl: Path | None = None):
    """USAGE_JSONL をパッチした状態で dashboard モジュールを読み込む (test_dashboard.py 流)。"""
    os.environ["USAGE_JSONL"] = str(usage_jsonl)
    if alerts_jsonl is not None:
        os.environ["HEALTH_ALERTS_JSONL"] = str(alerts_jsonl)
    try:
        spec = importlib.util.spec_from_file_location("dashboard_server_cross_tabs", _DASHBOARD_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del os.environ["USAGE_JSONL"]
        if alerts_jsonl is not None:
            del os.environ["HEALTH_ALERTS_JSONL"]
    return mod


def _skill(skill_name, session_id, project="proj", timestamp="2026-04-28T10:00:00+00:00"):
    return {
        "event_type": "skill_tool",
        "skill": skill_name,
        "project": project,
        "session_id": session_id,
        "timestamp": timestamp,
    }


def _slash(name, session_id, project="proj", timestamp="2026-04-28T10:00:00+00:00"):
    return {
        "event_type": "user_slash_command",
        "skill": name,
        "project": project,
        "session_id": session_id,
        "timestamp": timestamp,
    }


def _subagent(subagent_type, session_id, project="proj", timestamp="2026-04-28T10:00:00+00:00"):
    return {
        "event_type": "subagent_start",
        "subagent_type": subagent_type,
        "project": project,
        "session_id": session_id,
        "timestamp": timestamp,
        "tool_use_id": f"toolu_{session_id}_{subagent_type}",
    }


# ============================================================
#  B1: TestAggregateSkillCooccurrence
# ============================================================
class TestAggregateSkillCooccurrence:
    def test_empty_events_returns_empty_list(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        assert mod.aggregate_skill_cooccurrence([]) == []

    def test_single_session_single_skill_no_pair(self, tmp_path):
        # acceptance: unique skill が 1 件のとき pair 数は 0
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [_skill("a", "s1")]
        assert mod.aggregate_skill_cooccurrence(events) == []

    def test_single_session_two_skills_one_pair(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [_skill("a", "s1"), _skill("b", "s1")]
        assert mod.aggregate_skill_cooccurrence(events) == [
            {"pair": ["a", "b"], "count": 1},
        ]

    def test_single_session_three_skills_three_pairs(self, tmp_path):
        # C(3, 2) = 3 pair / 各 count=1
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [_skill("a", "s1"), _skill("b", "s1"), _skill("c", "s1")]
        result = mod.aggregate_skill_cooccurrence(events)
        assert result == [
            {"pair": ["a", "b"], "count": 1},
            {"pair": ["a", "c"], "count": 1},
            {"pair": ["b", "c"], "count": 1},
        ]

    def test_pair_normalized_to_sorted_order(self, tmp_path):
        # 異 session で skill が逆順に出現しても同じ pair として正規化される
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            _skill("foo", "s1"), _skill("bar", "s1"),  # session A: [foo, bar]
            _skill("bar", "s2"), _skill("foo", "s2"),  # session B: [bar, foo]
        ]
        result = mod.aggregate_skill_cooccurrence(events)
        assert result == [{"pair": ["bar", "foo"], "count": 2}]

    def test_self_pair_excluded(self, tmp_path):
        # 同じ skill が同 session 内で複数回呼ばれても unique 化で 1 → pair 0 件
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [_skill("a", "s1") for _ in range(5)]
        assert mod.aggregate_skill_cooccurrence(events) == []

    def test_user_slash_command_counted(self, tmp_path):
        # skill_tool 1 件 + user_slash_command 1 件 → 1 pair (両 event_type が拾われる)
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [_skill("a", "s1"), _slash("/b", "s1")]
        assert mod.aggregate_skill_cooccurrence(events) == [
            {"pair": ["/b", "a"], "count": 1},
        ]

    def test_subagent_excluded(self, tmp_path):
        # subagent_start 1 件 + skill_tool 1 件 → unique skill が 1 のみで pair 0 件
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [_subagent("Explore", "s1"), _skill("a", "s1")]
        assert mod.aggregate_skill_cooccurrence(events) == []

    def test_session_start_notification_excluded(self, tmp_path):
        # session_start / notification は filter で除外され pair に影響しない
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "session_start", "session_id": "s1", "project": "p", "timestamp": "2026-04-28T10:00:00+00:00"},
            {"event_type": "notification", "notification_type": "permission", "session_id": "s1", "project": "p", "timestamp": "2026-04-28T10:01:00+00:00"},
            _skill("a", "s1"), _skill("b", "s1"),
        ]
        assert mod.aggregate_skill_cooccurrence(events) == [
            {"pair": ["a", "b"], "count": 1},
        ]

    def test_empty_session_id_skipped(self, tmp_path):
        # session_id="" の events はグルーピング不能のため skip
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            _skill("a", ""), _skill("b", ""),  # 空 session_id → グルーピングしない
            _skill("a", "s1"), _skill("b", "s1"),  # 正常 session
        ]
        assert mod.aggregate_skill_cooccurrence(events) == [
            {"pair": ["a", "b"], "count": 1},  # session s1 のみ計上
        ]

    def test_empty_skill_name_skipped(self, tmp_path):
        # skill="" の events は skip (空 skill が pair に紛れ込まない)
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            _skill("", "s1"), _skill("a", "s1"), _skill("b", "s1"),
        ]
        assert mod.aggregate_skill_cooccurrence(events) == [
            {"pair": ["a", "b"], "count": 1},
        ]

    def test_top_n_cap_at_100_default(self, tmp_path):
        # 101 distinct pair を投入 → 返り値 100 件
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = []
        # 102 unique skill を 1 session で投入すれば C(102, 2) = 5151 pair (count=1 揃い)
        # → top 100 で打ち切り。シンプルに 101 distinct pair を作る:
        # session i (i=0..100) に skill "x" + skill "y_i" を入れると ("x", "y_i") の 101 pair (各 count=1)
        # …では top 100 cut の境界が安定しないので、pair count に勾配を作る:
        # pair (x, y_i) を i+1 回出現させる (count=i+1 / i=0..100 → 101 pair, count 1〜101)
        # 上位 100 が残り、count=1 の最下位 1 件が drop される
        for i in range(101):
            for k in range(i + 1):
                events.append(_skill("x", f"sess_{i}_{k}"))
                events.append(_skill(f"y_{i}", f"sess_{i}_{k}"))
        result = mod.aggregate_skill_cooccurrence(events)
        assert len(result) == 100
        # 最下位 (count=1) の pair が drop されているはず: ("x", "y_0") の count=1
        pairs = [tuple(item["pair"]) for item in result]
        assert ("x", "y_0") not in pairs

    def test_top_n_cap_custom(self, tmp_path):
        # top_n=5 で 5 件打ち切り
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = []
        for i in range(10):
            events.append(_skill("x", f"s_{i}"))
            events.append(_skill(f"y_{i}", f"s_{i}"))
        result = mod.aggregate_skill_cooccurrence(events, top_n=5)
        assert len(result) == 5

    def test_count_descending_order(self, tmp_path):
        # 異なる count の pair が count 降順で並ぶ
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = []
        # pair (a, b): count=3
        for s in ["s1", "s2", "s3"]:
            events.extend([_skill("a", s), _skill("b", s)])
        # pair (a, c): count=1
        events.extend([_skill("a", "sx"), _skill("c", "sx")])
        # pair (b, c): count=2
        for s in ["sy", "sz"]:
            events.extend([_skill("b", s), _skill("c", s)])
        result = mod.aggregate_skill_cooccurrence(events)
        assert result == [
            {"pair": ["a", "b"], "count": 3},
            {"pair": ["b", "c"], "count": 2},
            {"pair": ["a", "c"], "count": 1},
        ]

    def test_lexicographic_sort_within_same_count_reverse_input(self, tmp_path):
        # Proposal 3 反映: 逆順入力でも sort で正規化される
        # 全 count=1 同点 → 入力順に関係なく [(a,b), (a,c), (b,c)] の lexicographic 昇順
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = []
        # session s_bc: pair (b, c) を最初に
        events.extend([_skill("c", "s_bc"), _skill("b", "s_bc")])
        # session s_ac: pair (a, c)
        events.extend([_skill("c", "s_ac"), _skill("a", "s_ac")])
        # session s_ab: pair (a, b)
        events.extend([_skill("b", "s_ab"), _skill("a", "s_ab")])
        result = mod.aggregate_skill_cooccurrence(events)
        assert result == [
            {"pair": ["a", "b"], "count": 1},
            {"pair": ["a", "c"], "count": 1},
            {"pair": ["b", "c"], "count": 1},
        ]

    def test_count_unit_is_session_not_invocation(self, tmp_path):
        # Proposal 1 反映: 同 session で同じ pair を多数回トリガしても count=1
        # session_id=A で skill X / Y を交互に 5 回ずつ呼ぶ events を投入 → count=1
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = []
        for _ in range(5):
            events.extend([_skill("X", "s1"), _skill("Y", "s1")])
        result = mod.aggregate_skill_cooccurrence(events)
        assert result == [{"pair": ["X", "Y"], "count": 1}]


# ============================================================
#  B2: TestAggregateProjectSkillMatrix
# ============================================================
class TestAggregateProjectSkillMatrix:
    def test_empty_events_returns_empty_structure(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        assert mod.aggregate_project_skill_matrix([]) == {
            "projects": [], "skills": [], "counts": [],
            "covered_count": 0, "total_count": 0,
        }

    def test_single_event_creates_1x1_matrix(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [_skill("a", "s1", project="p")]
        result = mod.aggregate_project_skill_matrix(events)
        assert result == {
            "projects": ["p"], "skills": ["a"], "counts": [[1]],
            "covered_count": 1, "total_count": 1,
        }

    def test_top_n_projects_cut(self, tmp_path):
        # 11 distinct project → top 10 のみ残り、11 個目は drop
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = []
        # project_i に i+1 件の skill_tool を投入 (count: 1, 2, ..., 11)
        for i in range(11):
            for k in range(i + 1):
                events.append(_skill("a", f"s_{i}_{k}", project=f"proj_{i}"))
        result = mod.aggregate_project_skill_matrix(events)
        assert len(result["projects"]) == 10
        # 最下位 project_0 (count=1) が drop されている
        assert "proj_0" not in result["projects"]
        # 最上位は proj_10 (count=11)
        assert result["projects"][0] == "proj_10"

    def test_top_n_skills_cut(self, tmp_path):
        # 11 distinct skill → top 10 のみ残る
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = []
        for i in range(11):
            for k in range(i + 1):
                events.append(_skill(f"skill_{i}", f"s_{i}_{k}", project="p"))
        result = mod.aggregate_project_skill_matrix(events)
        assert len(result["skills"]) == 10
        assert "skill_0" not in result["skills"]

    def test_other_aggregation_not_applied(self, tmp_path):
        # top 漏れは "other" 行/列に集約されないことを invariant 化
        # (将来 (b) 案に倒すときの regression 検出用)
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = []
        for i in range(11):
            for k in range(i + 1):
                events.append(_skill("a", f"s_{i}_{k}", project=f"proj_{i}"))
        result = mod.aggregate_project_skill_matrix(events)
        assert "other" not in result["projects"]
        assert "Other" not in result["projects"]

    def test_counts_dimensions_match_axes(self, tmp_path):
        # invariant: len(counts) == len(projects) and all(len(row) == len(skills))
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            _skill("a", "s1", project="P1"),
            _skill("b", "s2", project="P2"),
            _slash("/c", "s3", project="P3"),
        ]
        result = mod.aggregate_project_skill_matrix(events)
        assert len(result["counts"]) == len(result["projects"])
        for row in result["counts"]:
            assert len(row) == len(result["skills"])

    def test_zero_cell_present_when_project_skill_no_overlap(self, tmp_path):
        # P1 では skill A のみ / P2 では skill B のみ → 2x2 matrix で対角だけ非ゼロ
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            _skill("A", "s1", project="P1"),
            _skill("B", "s2", project="P2"),
        ]
        result = mod.aggregate_project_skill_matrix(events)
        # projects/skills は count 降順、tie のとき lexicographic で安定
        assert sorted(result["projects"]) == ["P1", "P2"]
        assert sorted(result["skills"]) == ["A", "B"]
        # counts は 2x2 で対角のみ非ゼロ
        proj_idx = {p: i for i, p in enumerate(result["projects"])}
        skill_idx = {s: j for j, s in enumerate(result["skills"])}
        assert result["counts"][proj_idx["P1"]][skill_idx["A"]] == 1
        assert result["counts"][proj_idx["P2"]][skill_idx["B"]] == 1
        assert result["counts"][proj_idx["P1"]][skill_idx["B"]] == 0
        assert result["counts"][proj_idx["P2"]][skill_idx["A"]] == 0

    def test_subagent_excluded(self, tmp_path):
        # subagent_start のみの events → empty structure
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [_subagent("Explore", "s1", project="P1")]
        result = mod.aggregate_project_skill_matrix(events)
        assert result["projects"] == []
        assert result["skills"] == []
        assert result["counts"] == []
        assert result["covered_count"] == 0
        assert result["total_count"] == 0

    def test_user_slash_command_counted(self, tmp_path):
        # user_slash_command のみで 1x1 matrix
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [_slash("/foo", "s1", project="P1")]
        result = mod.aggregate_project_skill_matrix(events)
        assert result == {
            "projects": ["P1"], "skills": ["/foo"], "counts": [[1]],
            "covered_count": 1, "total_count": 1,
        }

    def test_empty_project_skipped(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            _skill("a", "s1", project=""),
            _skill("a", "s2", project="P1"),
        ]
        result = mod.aggregate_project_skill_matrix(events)
        assert result["projects"] == ["P1"]
        assert result["counts"] == [[1]]

    def test_empty_skill_name_skipped(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            _skill("", "s1", project="P1"),
            _skill("a", "s2", project="P1"),
        ]
        result = mod.aggregate_project_skill_matrix(events)
        assert result["skills"] == ["a"]
        assert result["counts"] == [[1]]

    def test_projects_skills_descending_by_total_count(self, tmp_path):
        # P1=10, P2=20, P3=15 → projects=[P2, P3, P1] (count 降順)
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = []
        for k in range(10):
            events.append(_skill("a", f"s1_{k}", project="P1"))
        for k in range(20):
            events.append(_skill("a", f"s2_{k}", project="P2"))
        for k in range(15):
            events.append(_skill("a", f"s3_{k}", project="P3"))
        result = mod.aggregate_project_skill_matrix(events)
        assert result["projects"] == ["P2", "P3", "P1"]

    def test_custom_top_args(self, tmp_path):
        # top_projects=2, top_skills=3 で軸が打ち切られる
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = []
        for i in range(5):
            for k in range(i + 1):
                events.append(_skill(f"sk_{i}", f"s_{i}_{k}", project=f"P_{i}"))
        result = mod.aggregate_project_skill_matrix(events, top_projects=2, top_skills=3)
        assert len(result["projects"]) == 2
        assert len(result["skills"]) == 3

    def test_asymmetric_axes_dimensions(self, tmp_path):
        # Proposal 4 反映: 3 project × 7 skill の非正方 matrix
        # → len(counts) == 3, all(len(row) == 7 for row in counts)
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = []
        for p in range(3):
            for s in range(7):
                events.append(_skill(f"sk_{s}", f"s_{p}_{s}", project=f"P_{p}"))
        result = mod.aggregate_project_skill_matrix(events)
        assert len(result["projects"]) == 3
        assert len(result["skills"]) == 7
        assert len(result["counts"]) == 3
        for row in result["counts"]:
            assert len(row) == 7

    def test_covered_count_equals_sum_of_matrix(self, tmp_path):
        # Proposal 2 反映: covered_count == sum(sum(row) for row in counts)
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = []
        # P1 / skill a に 3 件、P1 / skill b に 2 件、P2 / skill a に 4 件
        for k in range(3):
            events.append(_skill("a", f"s1a_{k}", project="P1"))
        for k in range(2):
            events.append(_skill("b", f"s1b_{k}", project="P1"))
        for k in range(4):
            events.append(_skill("a", f"s2a_{k}", project="P2"))
        result = mod.aggregate_project_skill_matrix(events)
        actual_sum = sum(sum(row) for row in result["counts"])
        assert result["covered_count"] == actual_sum
        assert result["covered_count"] == 9

    def test_total_count_includes_top_dropped_events(self, tmp_path):
        # Proposal 2 反映: 11 project × 1 skill, 各 project 1 件 →
        # covered_count=10 (top 10), total_count=11 (drop された 11 個目も含む)
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            _skill("a", f"s_{i}", project=f"P_{i}") for i in range(11)
        ]
        result = mod.aggregate_project_skill_matrix(events)
        assert result["covered_count"] == 10
        assert result["total_count"] == 11

    def test_total_count_zero_for_empty_input(self, tmp_path):
        # 空入力時に total_count=0, covered_count=0 (ZeroDivision 防止 invariant)
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        result = mod.aggregate_project_skill_matrix([])
        assert result["total_count"] == 0
        assert result["covered_count"] == 0

    def test_total_count_zero_when_only_subagent_events(self, tmp_path):
        # 二次レビュー Q2 反映: subagent_start のみで skill_tool / user_slash_command が 0
        # → total_count=0 / covered_count=0
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [_subagent("Explore", f"s_{i}", project=f"P_{i}") for i in range(3)]
        result = mod.aggregate_project_skill_matrix(events)
        assert result["total_count"] == 0
        assert result["covered_count"] == 0
        assert result["projects"] == []
        assert result["skills"] == []

    def test_aggregator_pure_no_input_mutation(self, tmp_path):
        # 二次レビュー Q3 反映: aggregator は events に対して pure
        # 同じ events を 2 回連続で呼んで戻り値が等しい + events が変化していない
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            _skill("a", "s1", project="P1"),
            _skill("b", "s2", project="P2"),
            _slash("/c", "s3", project="P1"),
        ]
        snapshot_before = [dict(ev) for ev in events]
        result1 = mod.aggregate_project_skill_matrix(events)
        result2 = mod.aggregate_project_skill_matrix(events)
        assert result1 == result2
        # events 自身が変化していない
        assert events == snapshot_before


# ============================================================
#  Integration: build_dashboard_data に新 field が乗るか
# ============================================================
class TestBuildDashboardDataIncludesCrossTabs:
    def test_skill_cooccurrence_key_present(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        data = mod.build_dashboard_data([])
        assert "skill_cooccurrence" in data
        assert data["skill_cooccurrence"] == []

    def test_project_skill_matrix_key_present(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        data = mod.build_dashboard_data([])
        assert "project_skill_matrix" in data
        assert data["project_skill_matrix"] == {
            "projects": [], "skills": [], "counts": [],
            "covered_count": 0, "total_count": 0,
        }

    def test_skill_cooccurrence_consistent_with_skill_ranking_filter(self, tmp_path):
        # subagent 混入なしの確認 — skill_ranking が pickup する skill のみが pair 候補
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            _skill("a", "s1"), _skill("b", "s1"),
            _subagent("Explore", "s1"),  # subagent は無視されるべき
        ]
        data = mod.build_dashboard_data(events)
        assert data["skill_cooccurrence"] == [{"pair": ["a", "b"], "count": 1}]
        # skill_ranking 側に subagent が混入していない (regression guard)
        skill_names = [item["name"] for item in data["skill_ranking"]]
        assert "Explore" not in skill_names
