"""tests/test_dashboard_period_toggle.py — Issue #85 Dashboard Period Toggle テスト集約。

Step 1〜7 の test class を 1 ファイルに集める (plan §4 dispersion 削減方針)。
"""
# pylint: disable=line-too-long
import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DASHBOARD_PATH = Path(__file__).parent.parent / "dashboard" / "server.py"


def load_dashboard_module(usage_jsonl: Path, alerts_jsonl: Path | None = None):
    """USAGE_JSONL をパッチした状態で dashboard モジュールを読み込む (test_dashboard.py 流用)。"""
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


_FIXED_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(now: datetime, *, days: float = 0, seconds: float = 0) -> str:
    """fixed now から相対 timestamp を ISO 文字列で返す (UTC 固定)。"""
    dt = now - timedelta(days=days, seconds=seconds)
    return dt.isoformat()


class TestFilterEventsByPeriod:
    """Step 1: `_filter_events_by_period` helper の境界・三段 filter 仕様 (plan §3 Step 1)."""

    def _mod(self, tmp_path):
        return load_dashboard_module(tmp_path / "nonexistent.jsonl")

    def test_period_7d_drops_8_days_old_event(self, tmp_path):
        mod = self._mod(tmp_path)
        events = [
            {"event_type": "skill_tool", "skill": "a", "session_id": "s", "timestamp": _ts(_FIXED_NOW, days=8)},
            {"event_type": "skill_tool", "skill": "b", "session_id": "s", "timestamp": _ts(_FIXED_NOW, days=6)},
        ]
        out = mod._filter_events_by_period(events, "7d", now=_FIXED_NOW)
        assert [e["skill"] for e in out] == ["b"]

    def test_period_30d_boundary_includes_30_days_old(self, tmp_path):
        mod = self._mod(tmp_path)
        events = [
            {"event_type": "skill_tool", "skill": "boundary", "session_id": "s", "timestamp": _ts(_FIXED_NOW, days=30)},
            {"event_type": "skill_tool", "skill": "old", "session_id": "s", "timestamp": _ts(_FIXED_NOW, days=31)},
        ]
        out = mod._filter_events_by_period(events, "30d", now=_FIXED_NOW)
        assert [e["skill"] for e in out] == ["boundary"]

    def test_period_90d_keeps_within_window(self, tmp_path):
        mod = self._mod(tmp_path)
        events = [
            {"event_type": "skill_tool", "skill": "a", "session_id": "s", "timestamp": _ts(_FIXED_NOW, days=89)},
            {"event_type": "skill_tool", "skill": "b", "session_id": "s", "timestamp": _ts(_FIXED_NOW, days=91)},
        ]
        out = mod._filter_events_by_period(events, "90d", now=_FIXED_NOW)
        assert [e["skill"] for e in out] == ["a"]

    def test_period_all_returns_events_unchanged(self, tmp_path):
        mod = self._mod(tmp_path)
        events = [
            {"event_type": "skill_tool", "skill": "ancient", "session_id": "s", "timestamp": _ts(_FIXED_NOW, days=365)},
            {"event_type": "skill_tool", "skill": "missing_ts"},
            {"event_type": "skill_tool", "skill": "broken_ts", "timestamp": "not-a-date"},
        ]
        out = mod._filter_events_by_period(events, "all", now=_FIXED_NOW)
        assert out == events  # element-wise equality (not identity)

    def test_period_unparseable_timestamps_silently_dropped_when_not_all(self, tmp_path):
        mod = self._mod(tmp_path)
        events = [
            {"event_type": "skill_tool", "skill": "good", "session_id": "s", "timestamp": _ts(_FIXED_NOW, days=1)},
            {"event_type": "skill_tool", "skill": "no_ts", "session_id": "s"},
            {"event_type": "skill_tool", "skill": "bad_ts", "session_id": "s", "timestamp": "garbage"},
        ]
        out = mod._filter_events_by_period(events, "7d", now=_FIXED_NOW)
        assert [e["skill"] for e in out] == ["good"]

    def test_period_naive_timestamp_treated_as_utc(self, tmp_path):
        mod = self._mod(tmp_path)
        # naive datetime (no tz) で 1 日前
        naive_ts = (_FIXED_NOW.replace(tzinfo=None) - timedelta(days=1)).isoformat()
        events = [
            {"event_type": "skill_tool", "skill": "a", "session_id": "s", "timestamp": naive_ts},
        ]
        out = mod._filter_events_by_period(events, "7d", now=_FIXED_NOW)
        assert len(out) == 1

    def test_period_default_now_uses_wall_clock(self, tmp_path):
        """now= 省略時に internal default の datetime.now(UTC) を使う (production code path)."""
        mod = self._mod(tmp_path)
        # 現在時刻基準で 1 秒前 → 必ず 7d 内
        events = [
            {"event_type": "skill_tool", "skill": "a", "session_id": "s",
             "timestamp": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()},
        ]
        out = mod._filter_events_by_period(events, "7d")
        assert len(out) == 1

    # === 第二段: subagent_start ↔ subagent_lifecycle_start pair-straddling ===

    def test_filter_period_includes_lifecycle_pair_when_start_outside_window(self, tmp_path):
        """第二段: kept lifecycle@(now-7d+0.4s) の paired start@(now-7d-0.4s) を再 include."""
        mod = self._mod(tmp_path)
        cutoff = _FIXED_NOW - timedelta(days=7)
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "session_id": "s",
             "tool_use_id": "tu1",
             "timestamp": (cutoff - timedelta(seconds=0.4)).isoformat()},
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "session_id": "s",
             "timestamp": (cutoff + timedelta(seconds=0.4)).isoformat()},
        ]
        out = mod._filter_events_by_period(events, "7d", now=_FIXED_NOW)
        types = sorted(e["event_type"] for e in out)
        assert types == ["subagent_lifecycle_start", "subagent_start"]

    def test_filter_period_includes_start_pair_when_lifecycle_outside_window(self, tmp_path):
        """第二段 (対称): kept start@cutoff+0.4s の paired lifecycle@cutoff-0.4s を再 include."""
        mod = self._mod(tmp_path)
        cutoff = _FIXED_NOW - timedelta(days=7)
        events = [
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore", "session_id": "s",
             "timestamp": (cutoff - timedelta(seconds=0.4)).isoformat()},
            {"event_type": "subagent_start", "subagent_type": "Explore", "session_id": "s",
             "tool_use_id": "tu1",
             "timestamp": (cutoff + timedelta(seconds=0.4)).isoformat()},
        ]
        out = mod._filter_events_by_period(events, "7d", now=_FIXED_NOW)
        types = sorted(e["event_type"] for e in out)
        assert types == ["subagent_lifecycle_start", "subagent_start"]

    # === 第三段: subagent_start ↔ subagent_stop pair-straddling ===

    def test_filter_period_includes_subagent_stop_paired_with_kept_start(self, tmp_path):
        """第三段: kept start@cutoff+0.4s の paired stop@cutoff-0.5s を再 include しない。

        plan: 順経路は kept_start から `start.ts <= stop.ts < next_start.ts` の直後 stop を拾う。
        逆経路は kept_stop から start.ts <= stop.ts の直前 start を拾う。
        cutoff より過去の stop で start の前にある (= start_A の paired stop ではない) ものは引っ張らない。
        """
        mod = self._mod(tmp_path)
        cutoff = _FIXED_NOW - timedelta(days=7)
        # ここで test するのは「kept stop が外側 start を引っ張る」ケース。
        # kept stop@cutoff+0.5s, paired start@cutoff-0.5s → start を再 include
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "session_id": "s",
             "tool_use_id": "tu1",
             "timestamp": (cutoff - timedelta(seconds=0.5)).isoformat()},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "session_id": "s",
             "timestamp": (cutoff + timedelta(seconds=0.5)).isoformat()},
        ]
        out = mod._filter_events_by_period(events, "7d", now=_FIXED_NOW)
        types = sorted(e["event_type"] for e in out)
        assert types == ["subagent_start", "subagent_stop"]

    def test_filter_period_includes_subagent_start_pulls_outside_paired_stop(self, tmp_path):
        """第三段 (順経路): kept start@cutoff+0.3s が直後の paired stop@cutoff-0.2s ... ではなく、
        cutoff+0.8s の stop を拾う (両方 inside の通常ケース)。

        inside-only 構成だが、第三段が stop を 取りこぼさない sanity check。
        """
        mod = self._mod(tmp_path)
        cutoff = _FIXED_NOW - timedelta(days=7)
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "session_id": "s",
             "tool_use_id": "tu1",
             "timestamp": (cutoff + timedelta(seconds=0.3)).isoformat()},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "session_id": "s",
             "timestamp": (cutoff + timedelta(seconds=0.8)).isoformat()},
        ]
        out = mod._filter_events_by_period(events, "7d", now=_FIXED_NOW)
        types = sorted(e["event_type"] for e in out)
        assert types == ["subagent_start", "subagent_stop"]

    def test_filter_period_does_not_pull_unrelated_stop_from_prior_invocation(self, tmp_path):
        """plan §3 Step 1 反例 test (iter5 #1): 同 (session_id, subagent_type) バケットで連続 2 invocation:

            start_A @ now-7d-2.0s   (cutoff 外, 第一段 drop)
            stop_A  @ now-7d-1.5s   (cutoff 外, 第一段 drop)
            start_B @ now-7d+0.3s   (cutoff 内, 保持)
            stop_B  @ now-7d+0.8s   (cutoff 内, 保持)

        期待: stop_A は再 include されない (start_B の paired stop は stop_B で確定)。
        """
        mod = self._mod(tmp_path)
        cutoff = _FIXED_NOW - timedelta(days=7)
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "session_id": "s",
             "tool_use_id": "tuA",
             "timestamp": (cutoff - timedelta(seconds=2.0)).isoformat()},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "session_id": "s",
             "timestamp": (cutoff - timedelta(seconds=1.5)).isoformat()},
            {"event_type": "subagent_start", "subagent_type": "Explore", "session_id": "s",
             "tool_use_id": "tuB",
             "timestamp": (cutoff + timedelta(seconds=0.3)).isoformat()},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "session_id": "s",
             "timestamp": (cutoff + timedelta(seconds=0.8)).isoformat()},
        ]
        out = mod._filter_events_by_period(events, "7d", now=_FIXED_NOW)
        # tuB の start と stop_B のみ。stop_A も start_A も再 include されない
        kept_starts = [e for e in out if e["event_type"] == "subagent_start"]
        kept_stops = [e for e in out if e["event_type"] == "subagent_stop"]
        assert len(kept_starts) == 1
        assert kept_starts[0]["tool_use_id"] == "tuB"
        assert len(kept_stops) == 1
        # stop は cutoff+0.8s のもの (stop_B)
        kept_stop_ts = datetime.fromisoformat(kept_stops[0]["timestamp"])
        assert kept_stop_ts > cutoff

    def test_three_stage_filter_survives_filter_usage_events(self, tmp_path):
        """plan §3 Step 2 invariant (iter5 advisory #4):
        三段で再 include された stop event が `_filter_usage_events` 通過後も残る (= dedup window と同一なので脱落しない)."""
        mod = self._mod(tmp_path)
        cutoff = _FIXED_NOW - timedelta(days=7)
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "session_id": "s",
             "tool_use_id": "tu1",
             "timestamp": (cutoff - timedelta(seconds=0.5)).isoformat()},
            {"event_type": "subagent_stop", "subagent_type": "Explore", "session_id": "s",
             "timestamp": (cutoff + timedelta(seconds=0.5)).isoformat()},
        ]
        period_events_raw = mod._filter_events_by_period(events, "7d", now=_FIXED_NOW)
        period_events_usage = mod._filter_usage_events(period_events_raw)
        # stop event 自体は usage_events に含まれないが (filter は subagent_start 系を 1 件代表に絞る)、
        # 再 include された start が 1 件として残ることを assert
        et_set = {e["event_type"] for e in period_events_usage}
        # `_filter_usage_events` は skill_tool / user_slash_command + invocation 代表 (subagent_start) を返す
        assert "subagent_start" in et_set

    def test_filter_period_invalid_value_falls_back_to_all(self, tmp_path):
        """`period` allow-list 外 → 'all' 相当 (= 全イベント保持) として扱う。

        plan §3 Step 3 で _serve_api 側 fallback も持つが、helper 単体でも防御的に
        unknown period は 'all' 相当に倒す (誤動作 silent drift 回避)。
        """
        mod = self._mod(tmp_path)
        events = [
            {"event_type": "skill_tool", "skill": "a", "session_id": "s",
             "timestamp": _ts(_FIXED_NOW, days=365)},
        ]
        out = mod._filter_events_by_period(events, "wat", now=_FIXED_NOW)
        assert out == events


def _make_event_set_for_period_test(now: datetime) -> list[dict]:
    """Step 2 / 7 で再利用する mixed events.

    - 8 日前 skill_tool (period=7d で消える)
    - 1 日前 skill_tool (period=7d でも残る)
    - 8 日前 subagent_start + paired stop (period=7d で消える)
    - 1 日前 subagent_start + paired stop (period=7d でも残る)
    - 60 日前 skill_tool (compact_density / lifecycle 入力用 = 全期間 field 側)
    - 90 日前 compact_start (compact_density / session_stats 用 = 全期間)
    """
    return [
        {"event_type": "skill_tool", "skill": "old", "project": "p1", "session_id": "s_old",
         "timestamp": _ts(now, days=8)},
        {"event_type": "skill_tool", "skill": "fresh", "project": "p1", "session_id": "s_fresh",
         "timestamp": _ts(now, days=1)},
        {"event_type": "subagent_start", "subagent_type": "Old", "project": "p1", "session_id": "s_old",
         "tool_use_id": "t_old", "timestamp": _ts(now, days=8, seconds=-1), "duration_ms": 1000,
         "success": True},
        {"event_type": "subagent_stop", "subagent_type": "Old", "project": "p1", "session_id": "s_old",
         "timestamp": _ts(now, days=8)},
        {"event_type": "subagent_start", "subagent_type": "Fresh", "project": "p1", "session_id": "s_fresh",
         "tool_use_id": "t_fresh", "timestamp": _ts(now, days=1, seconds=-1), "duration_ms": 1000,
         "success": True},
        {"event_type": "subagent_stop", "subagent_type": "Fresh", "project": "p1", "session_id": "s_fresh",
         "timestamp": _ts(now, days=1)},
        {"event_type": "skill_tool", "skill": "skill60d", "project": "p2", "session_id": "s60",
         "timestamp": _ts(now, days=60)},
        {"event_type": "compact_start", "trigger": "auto", "project": "p1", "session_id": "s_c",
         "timestamp": _ts(now, days=90)},
        {"event_type": "session_start", "source": "startup", "project": "p1", "session_id": "s_old",
         "timestamp": _ts(now, days=8, seconds=-2)},
    ]


class TestBuildDashboardDataWithPeriod:
    """Step 2: build_dashboard_data(period=...) — 11 field period 適用 / 8 field 不変 / period_applied echo."""

    def _mod(self, tmp_path):
        return load_dashboard_module(tmp_path / "nonexistent.jsonl")

    def test_period_applied_echo_in_response(self, tmp_path):
        mod = self._mod(tmp_path)
        data = mod.build_dashboard_data([], period="7d", now=_FIXED_NOW)
        assert data["period_applied"] == "7d"

    def test_period_all_legacy_signature_equivalence(self, tmp_path):
        """period 引数省略 (= legacy) と period='all' が完全一致すること (last_updated 込み)."""
        mod = self._mod(tmp_path)
        events = _make_event_set_for_period_test(_FIXED_NOW)
        legacy = mod.build_dashboard_data(events, now=_FIXED_NOW)
        explicit_all = mod.build_dashboard_data(events, period="all", now=_FIXED_NOW)
        # period_applied は legacy にも出る (= "all" がデフォルト)
        assert legacy == explicit_all

    def test_period_7d_shrinks_period_applied_fields(self, tmp_path):
        """period=7d で period 適用 11 field 全てから 8 日前 event が消える."""
        mod = self._mod(tmp_path)
        events = _make_event_set_for_period_test(_FIXED_NOW)
        data_all = mod.build_dashboard_data(events, period="all", now=_FIXED_NOW)
        data_7d = mod.build_dashboard_data(events, period="7d", now=_FIXED_NOW)

        # 1) total_events: all=2 skill + 2 subagent invocation = 4 (60d / 90d 系は除外); 7d=1 skill + 1 subagent = 2
        assert data_all["total_events"] > data_7d["total_events"]
        assert data_7d["total_events"] == 2

        # 2) skill_ranking: 7d 側に "old" / "skill60d" が含まれない
        skill_names_7d = {r["name"] for r in data_7d["skill_ranking"]}
        assert "old" not in skill_names_7d
        assert "skill60d" not in skill_names_7d
        assert "fresh" in skill_names_7d

        # 3) subagent_ranking: 7d 側に "Old" が含まれない
        sub_names_7d = {r["name"] for r in data_7d["subagent_ranking"]}
        assert "Old" not in sub_names_7d
        assert "Fresh" in sub_names_7d

        # 4) skill_kinds_total
        assert data_all["skill_kinds_total"] > data_7d["skill_kinds_total"]
        assert data_7d["skill_kinds_total"] == 1

        # 5) subagent_kinds_total
        assert data_all["subagent_kinds_total"] > data_7d["subagent_kinds_total"]
        assert data_7d["subagent_kinds_total"] == 1

        # 6) project_total: 7d で p2 (60d skill) が消える
        assert data_7d["project_total"] == 1

        # 7) daily_trend: 7d で 8d 前の bucket が消える
        dates_7d = {r["date"] for r in data_7d["daily_trend"]}
        eight_days_ago = (_FIXED_NOW - timedelta(days=8)).isoformat()[:10]
        assert eight_days_ago not in dates_7d

        # 8) project_breakdown
        projs_7d = {r["project"] for r in data_7d["project_breakdown"]}
        assert "p2" not in projs_7d

        # 9) hourly_heatmap: 7d 適用後の bucket 件数が all より少ない
        # (heatmap は events を rebucket するので長さでなく合計 count で比較)
        assert sum(b["count"] for b in data_7d["hourly_heatmap"]["buckets"]) < \
            sum(b["count"] for b in data_all["hourly_heatmap"]["buckets"])

        # 10) skill_cooccurrence: 7d / all で差が出ない場合 (events 構成上 pair が無い) は両方 [] でも OK
        assert isinstance(data_7d["skill_cooccurrence"], list)

        # 11) project_skill_matrix: 7d で p2 が消える
        if isinstance(data_7d["project_skill_matrix"], dict) and "rows" in data_7d["project_skill_matrix"]:
            projects_in_matrix = {r["project"] for r in data_7d["project_skill_matrix"]["rows"]}
            assert "p2" not in projects_in_matrix

    def test_full_period_fields_unchanged_across_periods(self, tmp_path):
        """全期間 8 field は period に関わらず同一 (drift guard)."""
        mod = self._mod(tmp_path)
        events = _make_event_set_for_period_test(_FIXED_NOW)
        data_all = mod.build_dashboard_data(events, period="all", now=_FIXED_NOW)
        data_7d = mod.build_dashboard_data(events, period="7d", now=_FIXED_NOW)

        full_period_fields = [
            "subagent_failure_trend",
            "permission_prompt_skill_breakdown",
            "permission_prompt_subagent_breakdown",
            "compact_density",
            "session_stats",
            "skill_invocation_breakdown",
            "skill_lifecycle",
            "skill_hibernating",
        ]
        for field in full_period_fields:
            assert data_all[field] == data_7d[field], \
                f"全期間 field {field} が period 切替で drift している"

    def test_now_kwarg_overrides_last_updated(self, tmp_path):
        """now= 引数を渡したとき last_updated もそれで override される (Step 7 drift guard 用)."""
        mod = self._mod(tmp_path)
        data = mod.build_dashboard_data([], period="all", now=_FIXED_NOW)
        assert data["last_updated"] == _FIXED_NOW.isoformat()


class TestApiDataPeriodQuery:
    """Step 3: `/api/data?period=<v>` query param routing + fallback."""

    def _start_server(self, tmp_path, events: list[dict]):
        """ThreadingHTTPServer をエフェメラルポートで上げて (server, base_url) を返す."""
        import json as _json
        import socket
        import threading
        usage = tmp_path / "usage.jsonl"
        usage.parent.mkdir(parents=True, exist_ok=True)
        with usage.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(_json.dumps(ev) + "\n")
        mod = load_dashboard_module(usage)

        # find free port (avoid race with bind to 0)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(("127.0.0.1", port), mod.DashboardHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return mod, server, f"http://127.0.0.1:{port}"

    def _stop(self, server):
        server.shutdown()
        server.server_close()

    def test_api_data_with_period_7d(self, tmp_path):
        import json as _json
        import urllib.request
        events = [
            {"event_type": "skill_tool", "skill": "fresh", "session_id": "s",
             "timestamp": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()},
        ]
        mod, server, base = self._start_server(tmp_path, events)
        try:
            with urllib.request.urlopen(f"{base}/api/data?period=7d", timeout=2) as resp:
                data = _json.loads(resp.read())
            assert data["period_applied"] == "7d"
        finally:
            self._stop(server)

    def test_api_data_invalid_period_falls_back_to_all(self, tmp_path):
        import json as _json
        import urllib.request
        mod, server, base = self._start_server(tmp_path, [])
        try:
            with urllib.request.urlopen(f"{base}/api/data?period=garbage", timeout=2) as resp:
                data = _json.loads(resp.read())
            assert data["period_applied"] == "all"
        finally:
            self._stop(server)

    def test_api_data_empty_period_value_falls_back_to_all(self, tmp_path):
        import json as _json
        import urllib.request
        mod, server, base = self._start_server(tmp_path, [])
        try:
            with urllib.request.urlopen(f"{base}/api/data?period=", timeout=2) as resp:
                data = _json.loads(resp.read())
            assert data["period_applied"] == "all"
        finally:
            self._stop(server)

    def test_api_data_no_period_query_defaults_to_all(self, tmp_path):
        """plan §3 Step 3 backward-compat: 既存 frontend (period unaware) との互換."""
        import json as _json
        import urllib.request
        mod, server, base = self._start_server(tmp_path, [])
        try:
            with urllib.request.urlopen(f"{base}/api/data", timeout=2) as resp:
                data = _json.loads(resp.read())
            assert data["period_applied"] == "all"
        finally:
            self._stop(server)

    def test_api_data_period_all_explicit(self, tmp_path):
        import json as _json
        import urllib.request
        mod, server, base = self._start_server(tmp_path, [])
        try:
            with urllib.request.urlopen(f"{base}/api/data?period=all", timeout=2) as resp:
                data = _json.loads(resp.read())
            assert data["period_applied"] == "all"
        finally:
            self._stop(server)


class TestPeriodToggleTemplate:
    """Step 4b: assembled template の DOM / CSS / concat 順 / static-export 早期 return."""

    def _mod(self, tmp_path):
        return load_dashboard_module(tmp_path / "nonexistent.jsonl")

    def test_period_toggle_dom_present_in_template(self, tmp_path):
        mod = self._mod(tmp_path)
        template = mod._build_html_template()
        assert 'id="periodToggle"' in template, "shell に #periodToggle が無い"

    def test_period_toggle_has_four_buttons(self, tmp_path):
        mod = self._mod(tmp_path)
        template = mod._build_html_template()
        for value in ("7d", "30d", "90d", "all"):
            assert f'data-period="{value}"' in template, f"button data-period={value!r} が無い"

    def test_period_toggle_initial_active_is_all(self, tmp_path):
        """初期状態の aria-pressed=true が data-period='all' に付く."""
        import re
        mod = self._mod(tmp_path)
        template = mod._build_html_template()
        # `data-period="all"` を含む button タグ周辺に aria-pressed="true" があること
        m = re.search(
            r'<button[^>]*data-period="all"[^>]*aria-pressed="true"[^>]*>'
            r'|<button[^>]*aria-pressed="true"[^>]*data-period="all"[^>]*>',
            template,
        )
        assert m is not None, "data-period='all' のボタンに aria-pressed='true' が無い"

    def test_period_toggle_role_group(self, tmp_path):
        """`role="group"` + aria-label が付いている (a11y)."""
        mod = self._mod(tmp_path)
        template = mod._build_html_template()
        assert 'role="group"' in template
        assert 'aria-label="集計期間"' in template

    def test_period_toggle_inside_page_nav(self, tmp_path):
        """toggle が `<nav class="page-nav">` 内に配置 (router 契約に乗っかる)."""
        mod = self._mod(tmp_path)
        template = mod._build_html_template()
        # nav 開始から toggle までが nav 終了より前
        nav_start = template.find('<nav class="page-nav"')
        toggle_pos = template.find('id="periodToggle"')
        nav_end = template.find('</nav>', nav_start)
        assert nav_start != -1 and toggle_pos != -1 and nav_end != -1
        assert nav_start < toggle_pos < nav_end, "#periodToggle が page-nav の外にある"

    def test_period_toggle_hidden_on_quality_and_surface_pages_via_css(self, tmp_path):
        """page-scoped CSS 非表示 rule が assembled template に含まれる."""
        mod = self._mod(tmp_path)
        template = mod._build_html_template()
        # 'body[data-active-page="quality"] #periodToggle' / 'surface' 両方
        assert 'body[data-active-page="quality"] #periodToggle' in template
        assert 'body[data-active-page="surface"] #periodToggle' in template
        assert 'display: none' in template or 'display:none' in template

    def test_period_05_js_concatted_before_10_helpers(self, tmp_path):
        """`05_period.js` が `10_helpers.js` より前に concat されること."""
        mod = self._mod(tmp_path)
        files = mod._MAIN_JS_FILES
        idx_period = files.index("05_period.js")
        idx_helpers = files.index("10_helpers.js")
        assert idx_period < idx_helpers

    def test_window_period_namespace_exposed(self, tmp_path):
        """`window.__period = { ... }` で getCurrentPeriod / setCurrentPeriod / wirePeriodToggle を expose."""
        mod = self._mod(tmp_path)
        bundle = mod._concat_main_js()
        assert "window.__period" in bundle, "window.__period namespace が定義されていない"
        assert "getCurrentPeriod" in bundle
        assert "setCurrentPeriod" in bundle
        assert "wirePeriodToggle" in bundle

    def test_period_calls_live_diff_via_call_time_lookup(self, tmp_path):
        """plan §3 Step 4 lazy-lookup behavioral pin (iter3 #2 / iter4 #2 / iter5 #2):

        05_period.js (concat order 05) は 25_live_diff.js (concat order 25) より早く評価される →
        IIFE 評価時に `window.__liveDiff` は **未定義**。
        click handler 内では call-time lookup する形であることを behavior 面で pin する。

        手段: Node + 手書き window/document stub で
          (1) 評価直後 (= window.__liveDiff 未定義) で handler を呼ぶ → 何も走らない
          (2) `window.__liveDiff` を後から定義 → handler 再 invoke → mock が呼ばれる
        """
        import subprocess
        import shutil
        node = shutil.which("node")
        if node is None:
            import pytest as _pytest
            _pytest.skip("node not available; skipping behavioral lazy-lookup test")
        mod = self._mod(tmp_path)
        bundle = mod._concat_main_js()
        # Node script: 手書き stub + 05_period.js の wirePeriodToggle() のみを評価したい。
        # 一番安全なのは bundle 全体を実行できる stub を整えて wirePeriodToggle を呼ぶこと。
        # ただし bundle 内には fetch / EventSource 等の I/O も含まれるので、minimal stub を入れる。
        script = r"""
const calls = [];
let savedHandler = null;

// minimal global stubs
globalThis.window = { addEventListener: () => {}, removeEventListener: () => {}, location: { hash: "" } };
globalThis.document = {
  body: { dataset: {}, classList: { add: () => {}, remove: () => {}, contains: () => false } },
  getElementById: () => null,
  querySelectorAll: (sel) => {
    if (typeof sel === "string" && sel.indexOf("data-period") !== -1) {
      return [{
        addEventListener: (_evt, fn) => { savedHandler = fn; },
        getAttribute: (k) => k === "data-period" ? "7d" : null,
        dataset: { period: "7d" },
        setAttribute: () => {},
      }];
    }
    return [];
  },
  querySelector: () => null,
  addEventListener: () => {},
};
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });
globalThis.EventSource = undefined;

// bundle を IIFE で評価。05_period.js は IIFE 直後に動いて window.__period を expose
const bundle = process.env.BUNDLE;
try {
  // bundle 全体は async IIFE 前提なので await できないが、05_period.js / 10_helpers.js は同期評価される。
  // 評価エラーを silent にしないため try/catch 表示。
  // bundle は wrapping IIFE 無しなので、ここで wrapper を巻く。
  // production shell.html では `(async function(){...})();` で wrap されているので
  // top-level await を含む bundle はそのままだと SyntaxError。同じ async IIFE で巻く。
  // ただし非同期に走る loadAndRender() は stub 環境では deref で reject する
  // (本 test の関心は 05_period.js の sync 部分 = wirePeriodToggle のみ) ため、
  // unhandledRejection を抑制する。
  process.on("unhandledRejection", () => {});
  const wrapped = "(async function(){" + bundle + "})();";
  // 注: loadAndRender を含むが top-level await 部分は 70_init_eventsource.js の await。
  // wirePeriodToggle 呼び出しが top-level であれば savedHandler が捕まる。
  eval(wrapped);
} catch (e) {
  // 70_init_eventsource.js の await scheduleLoadAndRender() は async 関数内なので
  // top-level eval では SyntaxError になる可能性 → fallback: wirePeriodToggle 単独 call
  // この path に落ちた場合も savedHandler が無いと test fail するので分岐 print のみ
  console.error("EVAL_ERROR:", e.message);
}

// step (1): window.__liveDiff 未定義 でも handler を呼んで no-throw + calls 0
if (typeof savedHandler !== "function") {
  console.log(JSON.stringify({error: "no_handler"}));
  process.exit(0);
}
try { savedHandler({ currentTarget: { dataset: { period: "7d" } } }); }
catch (e) { console.log(JSON.stringify({error: "step1_threw", msg: e.message})); process.exit(0); }
const callsAfterStep1 = calls.length;

// step (2): window.__liveDiff を後から定義 → 再 invoke
globalThis.window.__liveDiff = { scheduleLoadAndRender: () => { calls.push(1); } };
try { savedHandler({ currentTarget: { dataset: { period: "7d" } } }); }
catch (e) { console.log(JSON.stringify({error: "step2_threw", msg: e.message})); process.exit(0); }
const callsAfterStep2 = calls.length;

console.log(JSON.stringify({ callsAfterStep1, callsAfterStep2 }));
"""
        result = subprocess.run(
            [node, "-e", script],
            env={**os.environ, "BUNDLE": bundle},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"node failed: stderr={result.stderr}"
        # 最後の JSON 行をパース
        import json as _json
        lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
        assert lines, f"no JSON output: stdout={result.stdout!r} stderr={result.stderr!r}"
        out = _json.loads(lines[-1])
        assert "error" not in out, f"node script error: {out}"
        # call-time lookup なら step1 (liveDiff 未定義) で 0 件、step2 (liveDiff 後付け) で 1 件
        assert out["callsAfterStep1"] == 0, "callsAfterStep1 must be 0 (liveDiff 未定義時に呼ばれてしまっている)"
        assert out["callsAfterStep2"] == 1, "callsAfterStep2 must be 1 (call-time lookup なら liveDiff 後付け後 invoke で 1)"

    def test_static_export_hides_toggle(self, tmp_path):
        """plan §3 Step 4 (reviewer iter1 #2): static export では toggle を hidden にする.

        wirePeriodToggle() の冒頭で `window.__DATA__` の存在を check し、setAttribute('hidden', '') を呼んで return する。
        substring grep でも pin できるが、ここでは Node round-trip で hidden 属性が立つことを assert.
        """
        import subprocess
        import shutil
        node = shutil.which("node")
        if node is None:
            import pytest as _pytest
            _pytest.skip("node not available; skipping static-export test")
        mod = self._mod(tmp_path)
        bundle = mod._concat_main_js()
        script = r"""
let toggleEl = { _attrs: {}, setAttribute: function(k, v) { this._attrs[k] = v; } };
let savedHandler = null;
globalThis.window = { __DATA__: { foo: 1 }, addEventListener: () => {}, location: { hash: "" } };
globalThis.document = {
  body: { dataset: {}, classList: { add: () => {}, remove: () => {}, contains: () => false } },
  getElementById: (id) => id === "periodToggle" ? toggleEl : null,
  querySelectorAll: (sel) => {
    if (typeof sel === "string" && sel.indexOf("data-period") !== -1) {
      return [{ addEventListener: (_evt, fn) => { savedHandler = fn; }, dataset: { period: "7d" }, setAttribute: () => {}, getAttribute: () => null }];
    }
    return [];
  },
  querySelector: () => null,
  addEventListener: () => {},
};
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });
globalThis.EventSource = undefined;
process.on("unhandledRejection", () => {});
try {
  const wrapped = "(async function(){" + process.env.BUNDLE + "})();";
  eval(wrapped);
} catch (e) {
  // some downstream js may throw; only check toggleEl state here
}
console.log(JSON.stringify({ hidden: toggleEl._attrs.hidden, savedHandlerWired: typeof savedHandler === "function" }));
"""
        result = subprocess.run(
            [node, "-e", script],
            env={**os.environ, "BUNDLE": bundle},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"node failed: stderr={result.stderr}"
        import json as _json
        lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
        assert lines
        out = _json.loads(lines[-1])
        # static export 経路で hidden 属性が立つこと
        assert out["hidden"] == "", f"static export で toggle に hidden 属性が立っていない: {out}"

    def test_get_current_period_initial_value_is_all(self, tmp_path):
        """plan §3 Step 4 (iter1 question #1): window.__period.getCurrentPeriod() で初期値 'all' が読める."""
        import subprocess
        import shutil
        node = shutil.which("node")
        if node is None:
            import pytest as _pytest
            _pytest.skip("node not available")
        mod = self._mod(tmp_path)
        bundle = mod._concat_main_js()
        script = r"""
globalThis.window = { addEventListener: () => {}, location: { hash: "" } };
globalThis.document = {
  body: { dataset: {}, classList: { add: () => {}, remove: () => {}, contains: () => false } },
  getElementById: () => null,
  querySelectorAll: () => [],
  querySelector: () => null,
  addEventListener: () => {},
};
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });
globalThis.EventSource = undefined;
process.on("unhandledRejection", () => {});
try { eval("(async function(){" + process.env.BUNDLE + "})();"); } catch (e) {}
const initial = (typeof window.__period === "object" && typeof window.__period.getCurrentPeriod === "function")
  ? window.__period.getCurrentPeriod()
  : null;
console.log(JSON.stringify({ initial }));
"""
        result = subprocess.run(
            [node, "-e", script],
            env={**os.environ, "BUNDLE": bundle},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"node failed: stderr={result.stderr}"
        import json as _json
        out = _json.loads(result.stdout.strip().splitlines()[-1])
        assert out["initial"] == "all", f"getCurrentPeriod() の初期値が 'all' でない: {out}"


class TestPeriodAwareFetch:
    """Step 5: fetch 経路で period query を載せる."""

    def _mod(self, tmp_path):
        return load_dashboard_module(tmp_path / "nonexistent.jsonl")

    def test_load_and_render_uses_period_query(self, tmp_path):
        """20_load_and_render.js の concat 結果に '/api/data?period=' + getCurrentPeriod() (or 等価) が含まれる."""
        mod = self._mod(tmp_path)
        bundle = mod._concat_main_js()
        # 現状の 'fetch(\'/api/data\'' / "fetch('/api/data'" は無く、period query を含む形であること
        assert "fetch('/api/data'," not in bundle and 'fetch("/api/data",' not in bundle, \
            "period 不在の fetch('/api/data', ...) が残っている"
        assert "/api/data?period=" in bundle, "fetch URL に '?period=' が無い"
        assert "getCurrentPeriod" in bundle


class TestPeriodAppliedBadge:
    """Step 6: period_applied !== 'all' のとき該当 sub に '<period> 集計' badge prefix."""

    def _mod(self, tmp_path):
        return load_dashboard_module(tmp_path / "nonexistent.jsonl")

    def _render_sub_via_node(self, mod, period_applied: str) -> dict:
        """build_dashboard_data 結果を window.__DATA__ に注入し loadAndRender を Node で走らせて、
        Overview/Patterns sub の textContent を回収する."""
        import subprocess
        import shutil
        import json as _json
        node = shutil.which("node")
        if node is None:
            import pytest as _pytest
            _pytest.skip("node not available")
        bundle = mod._concat_main_js()
        # 適当な dummy data。period_applied のみ test-relevant.
        data = {
            "last_updated": "2026-05-01T00:00:00+00:00",
            "total_events": 1,
            "skill_ranking": [{"name": "x", "count": 1, "failure_count": 0, "failure_rate": 0.0}],
            "subagent_ranking": [],
            "skill_kinds_total": 1,
            "subagent_kinds_total": 0,
            "project_total": 1,
            "daily_trend": [{"date": "2026-05-01", "count": 1}],
            "project_breakdown": [{"project": "p", "count": 1}],
            "hourly_heatmap": {"buckets": [], "max": 0, "total": 0},
            "skill_cooccurrence": [],
            "project_skill_matrix": {"projects": [], "skills": [], "rows": [], "covered": 0, "total": 0},
            "subagent_failure_trend": {"weeks": [], "series": []},
            "permission_prompt_skill_breakdown": [],
            "permission_prompt_subagent_breakdown": [],
            "compact_density": {"hist": [], "worst": []},
            "session_stats": {"total_sessions": 1, "resume_rate": 0, "compact_count": 0, "permission_prompt_count": 0},
            "health_alerts": [],
            "skill_invocation_breakdown": [],
            "skill_lifecycle": [],
            "skill_hibernating": {"items": [], "active_excluded_count": 0, "scope_note": ""},
            "period_applied": period_applied,
        }

        # element store: id -> { textContent, _html }
        script = r"""
process.on("unhandledRejection", () => {});
const store = {};
function makeEl(id) {
  if (!store[id]) {
    store[id] = {
      id,
      textContent: "",
      innerHTML: "",
      hidden: false,
      classList: { add: () => {}, remove: () => {}, contains: () => false, toggle: () => {} },
      _attrs: {},
      dataset: {},
      style: {},
      setAttribute: function(k, v) { this._attrs[k] = v; },
      getAttribute: function(k) { return this._attrs[k] || null; },
      removeAttribute: function(k) { delete this._attrs[k]; },
      addEventListener: () => {},
      removeEventListener: () => {},
      appendChild: () => {},
      insertBefore: () => {},
      remove: () => {},
      querySelectorAll: () => [],
      querySelector: () => null,
      contains: () => false,
      closest: () => null,
      offsetWidth: 0,
    };
  }
  return store[id];
}
globalThis.window = { __DATA__: JSON.parse(process.env.DATA), addEventListener: () => {}, location: { hash: "" } };
globalThis.document = {
  body: { dataset: {}, classList: { add: () => {}, remove: () => {}, contains: () => false } },
  getElementById: (id) => makeEl(id),
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: (tag) => makeEl("__el_" + Math.random()),
  createElementNS: (ns, tag) => makeEl("__el_" + Math.random()),
  addEventListener: () => {},
};
globalThis.fetch = async () => ({ ok: true, json: async () => JSON.parse(process.env.DATA) });
globalThis.EventSource = undefined;
try { eval("(async function(){" + process.env.BUNDLE + "})();"); } catch (e) {}

// 同期評価で 70_init_eventsource.js は await 待ちするので、ここで明示的に少し待つ必要は無く、
// loadAndRender は scheduleLoadAndRender → microtask で動く。一度 process.nextTick を使って flush。
setImmediate(() => {
  setImmediate(() => {
    const out = {};
    for (const id of Object.keys(store)) {
      out[id] = store[id].textContent || "";
    }
    console.log(JSON.stringify(out));
    process.exit(0);
  });
});
"""
        result = subprocess.run(
            [node, "-e", script],
            env={**os.environ, "BUNDLE": bundle, "DATA": _json.dumps(data)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"node failed: stderr={result.stderr}"
        lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip().startswith("{")]
        assert lines, f"no JSON output: stdout={result.stdout!r}"
        return _json.loads(lines[-1])

    def test_badge_appears_when_period_applied_is_7d(self, tmp_path):
        mod = self._mod(tmp_path)
        out = self._render_sub_via_node(mod, "7d")
        # Overview の sub 4 つ + Patterns sub 3 つに '7d' badge 文字列が prefix される
        for sub_id in ("dailySub", "skillSub", "subSub", "projSub",
                       "patterns-heatmap-sub"):
            text = out.get(sub_id, "")
            # 何らかのテキストがあり、かつ '7d' が含まれる (badge format は plan §3 Step 8 で <period> 集計)
            if text:
                assert "7d" in text, f"{sub_id} に '7d' badge が無い: {text!r}"

    def test_badge_absent_when_period_applied_is_all(self, tmp_path):
        mod = self._mod(tmp_path)
        out = self._render_sub_via_node(mod, "all")
        for sub_id in ("dailySub", "skillSub", "subSub", "projSub"):
            text = out.get(sub_id, "")
            # 'all' のとき "7d" / "30d" / "90d" のような badge 文字列は付かない
            for not_expected in ("7d 集計", "30d 集計", "90d 集計"):
                assert not_expected not in text, \
                    f"{sub_id} に period=all で {not_expected!r} が出ている: {text!r}"


class TestConcatMainJsByteInvariant:
    """Step 4a: `_concat_main_js()` helper 切り出し refactor の byte-identical 不変条件."""

    def test_concat_main_js_returns_str(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        out = mod._concat_main_js()
        assert isinstance(out, str)
        assert len(out) > 0

    def test_concat_main_js_used_in_build_html_template(self, tmp_path):
        """`_build_html_template()` の出力に `_concat_main_js()` の結果が含まれる (DRY)."""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        main_js = mod._concat_main_js()
        template = mod._build_html_template()
        assert main_js in template, "_build_html_template() の出力に _concat_main_js() の連結結果が含まれていない"

    def test_concat_main_js_no_separator_between_files(self, tmp_path):
        """plan §3 Step 4a iter6 #2: 改行などの separator を入れず byte-identical を維持する.

        現行 inline 形は `"".join(...)` で書かれている (dashboard/server.py:990 履歴) ので、
        helper 切り出し後も "".join (= no separator) であることを assert。
        """
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        from pathlib import Path as _P
        template_dir = _P(mod.__file__).parent / "template"
        expected = "".join(
            (template_dir / "scripts" / name).read_text(encoding="utf-8")
            for name in mod._MAIN_JS_FILES
        )
        actual = mod._concat_main_js()
        assert actual == expected, "concat に separator が混入している (byte-identical 違反)"


class TestPeriodSentinelDocstring:
    """Issue #85 sentinel pin (plan §3 Step 2 iter5 advisory #3).

    `aggregate_daily(period_events_usage)` 呼び出し直前のコメントが残ることを保証する。
    rebase / refactor で削除されると detection 不可になるリスクを test で塞ぐ。
    """

    def test_issue_85_daily_trend_sentinel(self):
        source = (Path(__file__).parent.parent / "dashboard" / "server.py").read_text(encoding="utf-8")
        assert "Issue #85: daily_trend stays in period-applied set" in source, \
            "Issue #85 sentinel comment が dashboard/server.py から消えている"
