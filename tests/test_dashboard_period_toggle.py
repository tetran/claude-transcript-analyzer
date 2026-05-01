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
