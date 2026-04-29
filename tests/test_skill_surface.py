"""tests/test_skill_surface.py — Issue #62 skill surface (A4 + B4) のテスト。

A4: user_slash_command.source を skill ごとに集計し、expansion_rate を返す。
    旧 schema (source 欠落) や未知 source 値は **silent skip** (= エラー回避のみ)。
    集計に含めない / output に出さない (modern data 0 件の skill は出力対象外)。

B4: instructions_loaded event の memory_type / load_reason / file_path を集計。
    glob_match_top は file_path home 圧縮 + count desc / path asc + top_n=10。
    memory_type_dist / load_reason_dist は dict だが count desc / key asc の
    insertion order を契約 (P2 反映 / Python 3.7+ + JSON / ECMAScript 仕様で保持)。

詳細は `docs/plans/issue-62-skill-surface.md` を参照。
"""
# pylint: disable=line-too-long
import importlib.util
import json
import os
from pathlib import Path

_DASHBOARD_PATH = Path(__file__).parent.parent / "dashboard" / "server.py"


def load_dashboard_module(usage_jsonl: Path, alerts_jsonl: Path | None = None):
    """USAGE_JSONL をパッチした状態で dashboard モジュールを読み込む (test_dashboard.py 流)。"""
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

def _slash(name, source=None, project="p", session="s", ts="2026-04-01T00:00:00+00:00"):
    """user_slash_command event factory.

    source=None で「旧 schema (source 欠落)」を表現する (= legacy 扱い)。
    """
    ev = {
        "event_type": "user_slash_command",
        "skill": name,
        "args": "",
        "project": project,
        "session_id": session,
        "timestamp": ts,
    }
    if source is not None:
        ev["source"] = source
    return ev


def _instr(memory_type="Project", load_reason="session_start", file_path="/etc/foo/CLAUDE.md",
           project="p", session="s", ts="2026-04-01T00:00:00+00:00"):
    return {
        "event_type": "instructions_loaded",
        "memory_type": memory_type,
        "load_reason": load_reason,
        "file_path": file_path,
        "project": project,
        "session_id": session,
        "timestamp": ts,
    }


# ============================================================
#  TestSlashCommandSourceBreakdown — A4 集計仕様
# ============================================================
class TestSlashCommandSourceBreakdown:
    def test_empty_events_returns_empty_list(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        assert mod.aggregate_slash_command_source_breakdown([]) == []

    def test_single_expansion_only_skill(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_slash("/foo", source="expansion") for _ in range(3)]
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert len(out) == 1
        row = out[0]
        assert row["skill"] == "/foo"
        assert row["expansion_count"] == 3
        assert row["submit_count"] == 0
        assert row["expansion_rate"] == 1.0
        assert "legacy_count" not in row

    def test_single_submit_only_skill(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_slash("/bar", source="submit") for _ in range(4)]
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert len(out) == 1
        row = out[0]
        assert row["expansion_count"] == 0
        assert row["submit_count"] == 4
        assert row["expansion_rate"] == 0.0
        assert "legacy_count" not in row

    def test_mixed_expansion_and_submit_skill(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_slash("/m", source="expansion") for _ in range(3)]
            + [_slash("/m", source="submit")]
        )
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert len(out) == 1
        row = out[0]
        assert row["expansion_count"] == 3
        assert row["submit_count"] == 1
        assert row["expansion_rate"] == 0.75

    def test_old_schema_silently_skipped(self, tmp_path):
        # 旧 schema (source 欠落) は silent skip。modern が 0 なら skill 自体が出力対象外。
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_slash("/old-only", source=None) for _ in range(5)]
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert out == []

    def test_unknown_source_value_silently_skipped(self, tmp_path):
        # 未知 source 値も silent skip。modern が 0 なら skill 自体が出力対象外。
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_slash("/u", source="something_new") for _ in range(2)]
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert out == []

    def test_old_schema_ignored_in_rate_with_modern_present(self, tmp_path):
        # modern (expansion=2, submit=2) + 旧 schema 10 件 → rate = 2/(2+2) = 0.5
        # 旧 schema は count に積まず (silent skip)、rate 分母にも入らない
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_slash("/x", source="expansion") for _ in range(2)]
            + [_slash("/x", source="submit") for _ in range(2)]
            + [_slash("/x", source=None) for _ in range(10)]
        )
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert len(out) == 1
        row = out[0]
        assert row["expansion_count"] == 2
        assert row["submit_count"] == 2
        assert row["expansion_rate"] == 0.5

    def test_empty_skill_name_skipped(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_slash("", source="expansion") for _ in range(3)]
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert out == []

    def test_modern_only_skill_emitted(self, tmp_path):
        # 旧 schema が他 skill に紛れていても、modern data ある skill は出力される
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _slash("/other", source="expansion"),
            _slash("/legacy", source=None),
        ]
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert [r["skill"] for r in out] == ["/other"]

    def test_sort_by_total_desc_then_skill_asc(self, tmp_path):
        # total = expansion + submit 降順 / skill 昇順
        # alpha total=5 / beta total=5 / gamma total=3 → alpha, beta, gamma
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_slash("alpha", source="expansion") for _ in range(5)]
            + [_slash("beta", source="expansion") for _ in range(5)]
            + [_slash("gamma", source="expansion") for _ in range(3)]
        )
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert [r["skill"] for r in out] == ["alpha", "beta", "gamma"]

    def test_top_n_cap(self, tmp_path):
        # 25 skill が observed のとき返り値は 20 件
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = []
        # total 降順を一意にして安定 sort をテスト: skill_NN は (NN+1) 件 expansion
        for i in range(25):
            events.extend([_slash(f"/skill_{i:02d}", source="expansion") for _ in range(i + 1)])
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert len(out) == 20

    def test_expansion_rate_when_no_submit(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_slash("/e", source="expansion") for _ in range(10)]
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert out[0]["expansion_rate"] == 1.0
        assert out[0]["expansion_rate"] is not None

    def test_expansion_rate_when_no_expansion(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_slash("/s", source="submit") for _ in range(5)]
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert out[0]["expansion_rate"] == 0.0

    def test_expansion_rate_rounded_to_4_decimals(self, tmp_path):
        # Q2 反映: expansion=2, submit=1 (modern=3) → 0.6667 (4 桁丸め)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_slash("/r", source="expansion") for _ in range(2)]
            + [_slash("/r", source="submit")]
        )
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert out[0]["expansion_rate"] == 0.6667

    def test_skill_tool_events_ignored(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [{
            "event_type": "skill_tool",
            "skill": "/x",
            "session_id": "s",
            "timestamp": "2026-04-01T00:00:00+00:00",
            "success": True,
        }]
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert out == []

    def test_other_event_types_ignored(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            {"event_type": "session_start", "session_id": "s", "timestamp": "2026-04-01T00:00:00+00:00"},
            {"event_type": "notification", "notification_type": "permission",
             "session_id": "s", "timestamp": "2026-04-01T00:00:01+00:00"},
        ]
        out = mod.aggregate_slash_command_source_breakdown(events)
        assert out == []


# ============================================================
#  TestInstructionsLoadedBreakdown — B4 集計仕様
# ============================================================
class TestInstructionsLoadedBreakdown:
    def test_empty_events_returns_safe_defaults(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        out = mod.aggregate_instructions_loaded_breakdown([])
        assert out == {"memory_type_dist": {}, "load_reason_dist": {}, "glob_match_top": []}

    def test_memory_type_distribution_counted(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_instr(memory_type="Project") for _ in range(3)]
            + [_instr(memory_type="User") for _ in range(2)]
        )
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert out["memory_type_dist"] == {"Project": 3, "User": 2}

    def test_load_reason_distribution_counted(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_instr(load_reason="session_start") for _ in range(5)]
            + [_instr(load_reason="glob_match", file_path="/x") for _ in range(2)]
        )
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert out["load_reason_dist"] == {"session_start": 5, "glob_match": 2}

    def test_titlecase_passthrough_no_normalization(self, tmp_path):
        # "Project" と "project" は別キーとして集計される (lower-case しない)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _instr(memory_type="Project"),
            _instr(memory_type="project"),
        ]
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert "Project" in out["memory_type_dist"]
        assert "project" in out["memory_type_dist"]
        assert out["memory_type_dist"]["Project"] == 1
        assert out["memory_type_dist"]["project"] == 1

    def test_empty_memory_type_skipped(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_instr(memory_type="")]
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert out["memory_type_dist"] == {}

    def test_empty_load_reason_skipped(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_instr(load_reason="")]
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert out["load_reason_dist"] == {}

    def test_memory_type_dist_iteration_order_is_count_desc_then_key_asc(self, tmp_path):
        # P2 反映: count desc → key asc
        # A=2, B=5, C=5 → list(dict.keys()) == ["B", "C", "A"]
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_instr(memory_type="A") for _ in range(2)]
            + [_instr(memory_type="B") for _ in range(5)]
            + [_instr(memory_type="C") for _ in range(5)]
        )
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert list(out["memory_type_dist"].keys()) == ["B", "C", "A"]

    def test_load_reason_dist_iteration_order_is_count_desc_then_key_asc(self, tmp_path):
        # P2 反映: count desc → key asc
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_instr(load_reason="zeta") for _ in range(2)]
            + [_instr(load_reason="alpha") for _ in range(5)]
            + [_instr(load_reason="beta") for _ in range(5)]
        )
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert list(out["load_reason_dist"].keys()) == ["alpha", "beta", "zeta"]

    def test_glob_match_top_sort_count_desc_path_asc(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_instr(load_reason="glob_match", file_path="/p/a") for _ in range(5)]
            + [_instr(load_reason="glob_match", file_path="/p/b") for _ in range(5)]
            + [_instr(load_reason="glob_match", file_path="/p/c") for _ in range(3)]
        )
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert out["glob_match_top"] == [
            {"file_path": "/p/a", "count": 5},
            {"file_path": "/p/b", "count": 5},
            {"file_path": "/p/c", "count": 3},
        ]

    def test_glob_match_top_n_cap(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = []
        for i in range(12):
            events.extend([_instr(load_reason="glob_match",
                                   file_path=f"/p/file_{i:02d}") for _ in range(i + 1)])
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert len(out["glob_match_top"]) == 10

    def test_glob_match_only_for_glob_match_load_reason(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            _instr(load_reason="session_start", file_path="/p/x"),
            _instr(load_reason="session_start", file_path="/p/y"),
        ]
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert out["glob_match_top"] == []

    def test_glob_match_top_counts_only_within_glob_match_scope(self, tmp_path):
        # Q1 反映: 同じ file_path X が glob_match で 3 件 + session_start で 5 件
        # → glob_match_top の X は count=3
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_instr(load_reason="glob_match", file_path="/p/X") for _ in range(3)]
            + [_instr(load_reason="session_start", file_path="/p/X") for _ in range(5)]
        )
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert out["glob_match_top"] == [{"file_path": "/p/X", "count": 3}]

    def test_glob_match_empty_file_path_skipped(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_instr(load_reason="glob_match", file_path="")]
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert out["glob_match_top"] == []

    def test_file_path_home_compression(self, tmp_path, monkeypatch):
        # /Users/<HOME>/.claude/skills/foo/SKILL.md → ~/.claude/skills/foo/SKILL.md
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        monkeypatch.setattr(os, "sep", "/")
        monkeypatch.setattr(os.path, "expanduser", lambda p: "/Users/foo" if p == "~" else p)
        events = [_instr(load_reason="glob_match",
                         file_path="/Users/foo/.claude/skills/x/SKILL.md")]
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert out["glob_match_top"] == [
            {"file_path": "~/.claude/skills/x/SKILL.md", "count": 1}
        ]

    def test_file_path_outside_home_unchanged(self, tmp_path, monkeypatch):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        monkeypatch.setattr(os, "sep", "/")
        monkeypatch.setattr(os.path, "expanduser", lambda p: "/Users/foo" if p == "~" else p)
        events = [_instr(load_reason="glob_match", file_path="/etc/foo/bar/CLAUDE.md")]
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert out["glob_match_top"] == [
            {"file_path": "/etc/foo/bar/CLAUDE.md", "count": 1}
        ]

    def test_aggregator_does_not_mutate_input_events(self, tmp_path, monkeypatch):
        # P3 反映: aggregator が events[*]["file_path"] を in-place rewrite しないこと
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        monkeypatch.setattr(os, "sep", "/")
        monkeypatch.setattr(os.path, "expanduser", lambda p: "/Users/foo" if p == "~" else p)
        original_path = "/Users/foo/.claude/skills/x/SKILL.md"
        events = [_instr(load_reason="glob_match", file_path=original_path)]
        mod.aggregate_instructions_loaded_breakdown(events)
        assert events[0]["file_path"] == original_path

    def test_dict_iteration_order_survives_json_roundtrip(self, tmp_path):
        # 2-P1 反映: aggregator → json.dumps → json.loads 後でもキー順保持
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = (
            [_instr(memory_type="A") for _ in range(2)]
            + [_instr(memory_type="B") for _ in range(5)]
            + [_instr(memory_type="C") for _ in range(5)]
        )
        out = mod.aggregate_instructions_loaded_breakdown(events)
        roundtripped = json.loads(json.dumps(out))
        assert list(roundtripped["memory_type_dist"].keys()) == ["B", "C", "A"]

    def test_other_event_types_ignored(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "/x", "session_id": "s",
             "timestamp": "2026-04-01T00:00:00+00:00"},
            {"event_type": "notification", "notification_type": "permission",
             "session_id": "s", "timestamp": "2026-04-01T00:00:01+00:00"},
        ]
        out = mod.aggregate_instructions_loaded_breakdown(events)
        assert out == {"memory_type_dist": {}, "load_reason_dist": {}, "glob_match_top": []}


# ============================================================
#  TestCompressHomePath — _compress_home_path() 単体テスト
# ============================================================
class TestCompressHomePath:
    """`_compress_home_path()` の単体テスト。aggregator の path 圧縮 helper。"""
    def test_home_prefix_compressed(self, tmp_path, monkeypatch):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        monkeypatch.setattr(os, "sep", "/")
        monkeypatch.setattr(os.path, "expanduser", lambda p: "/Users/foo" if p == "~" else p)
        assert mod._compress_home_path("/Users/foo/.claude/x") == "~/.claude/x"

    def test_home_exact_match_compressed(self, tmp_path, monkeypatch):
        # path=/Users/foo (HOME と完全一致) → 圧縮しない (sep が無いので prefix 一致しない仕様)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        monkeypatch.setattr(os, "sep", "/")
        monkeypatch.setattr(os.path, "expanduser", lambda p: "/Users/foo" if p == "~" else p)
        assert mod._compress_home_path("/Users/foo") == "/Users/foo"

    def test_path_outside_home_unchanged(self, tmp_path, monkeypatch):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        monkeypatch.setattr(os, "sep", "/")
        monkeypatch.setattr(os.path, "expanduser", lambda p: "/Users/foo" if p == "~" else p)
        assert mod._compress_home_path("/etc/foo") == "/etc/foo"

    def test_empty_path_unchanged(self, tmp_path, monkeypatch):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        monkeypatch.setattr(os, "sep", "/")
        monkeypatch.setattr(os.path, "expanduser", lambda p: "/Users/foo" if p == "~" else p)
        assert mod._compress_home_path("") == ""

    def test_home_substring_not_falsely_compressed(self, tmp_path, monkeypatch):
        # HOME=/Users/foo, path=/Users/foo-extended/x → 無加工 ( `home + os.sep` 比較)
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        monkeypatch.setattr(os, "sep", "/")
        monkeypatch.setattr(os.path, "expanduser", lambda p: "/Users/foo" if p == "~" else p)
        assert mod._compress_home_path("/Users/foo-extended/x") == "/Users/foo-extended/x"


# ============================================================
#  TestBuildDashboardDataIncludesSurfaceFields — payload 統合
# ============================================================
class TestBuildDashboardDataIncludesSurfaceFields:
    def test_slash_command_source_breakdown_key_present(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_slash("/x", source="expansion")]
        data = mod.build_dashboard_data(events)
        assert "slash_command_source_breakdown" in data
        assert isinstance(data["slash_command_source_breakdown"], list)

    def test_instructions_loaded_breakdown_key_present(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        events = [_instr()]
        data = mod.build_dashboard_data(events)
        assert "instructions_loaded_breakdown" in data
        b = data["instructions_loaded_breakdown"]
        assert isinstance(b, dict)
        assert "memory_type_dist" in b
        assert "load_reason_dist" in b
        assert "glob_match_top" in b

    def test_empty_events_returns_safe_defaults(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        data = mod.build_dashboard_data([])
        assert data["slash_command_source_breakdown"] == []
        assert data["instructions_loaded_breakdown"] == {
            "memory_type_dist": {}, "load_reason_dist": {}, "glob_match_top": []
        }

    def test_constant_TOP_N_SLASH_COMMAND_BREAKDOWN(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        assert mod.TOP_N_SLASH_COMMAND_BREAKDOWN == 20

    def test_constant_TOP_N_GLOB_MATCH(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "n.jsonl")
        assert mod.TOP_N_GLOB_MATCH == 10
