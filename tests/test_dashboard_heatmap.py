"""tests/test_dashboard_heatmap.py — Issue #58 hourly heatmap aggregator のテスト。

server 側は usage 系 events を hour-truncated UTC bucket に集計し、
`{"timezone": "UTC", "buckets": [...]}` の dict を返す。browser 側で local TZ
変換 + (weekday, hour) bin される設計 (option 3 hour-bucketed UTC)。

集計対象は usage 系 events のみで、subagent は invocation 単位 dedup 済み。
詳細は `docs/plans/archive/issue-58-hourly-heatmap.md` を参照。
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
        spec = importlib.util.spec_from_file_location("dashboard_server_heatmap", _DASHBOARD_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del os.environ["USAGE_JSONL"]
        if alerts_jsonl is not None:
            del os.environ["HEALTH_ALERTS_JSONL"]
    return mod


# ============================================================
#  TestAggregateHourlyHeatmap (基本 14 + Proposal 2 強化 3 = 17 tests)
# ============================================================
class TestAggregateHourlyHeatmap:
    def test_empty_events_returns_empty_buckets(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        result = mod.aggregate_hourly_heatmap([])
        assert result == {"timezone": "UTC", "buckets": []}

    def test_single_event_creates_one_bucket(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:30:45+00:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert result["timezone"] == "UTC"
        assert result["buckets"] == [
            {"hour_utc": "2026-04-28T10:00:00+00:00", "count": 1},
        ]

    def test_same_hour_multiple_events_increment_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "b", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:30:00+00:00"},
            {"event_type": "user_slash_command", "skill": "/foo", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:59:59+00:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert result["buckets"] == [
            {"hour_utc": "2026-04-28T10:00:00+00:00", "count": 3},
        ]

    def test_different_hours_create_separate_buckets(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T11:00:00+00:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert result["buckets"] == [
            {"hour_utc": "2026-04-28T10:00:00+00:00", "count": 1},
            {"hour_utc": "2026-04-28T11:00:00+00:00", "count": 1},
        ]

    def test_non_utc_timestamp_normalizes_to_utc(self, tmp_path):
        """+09:00 の 19:00 → UTC 10:00 bucket に入る。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T19:00:00+09:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert result["buckets"] == [
            {"hour_utc": "2026-04-28T10:00:00+00:00", "count": 1},
        ]

    def test_microsecond_timestamp_parses(self, tmp_path):
        """microsecond 付き timestamp も hour truncate される。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:30:45.123456+00:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert result["buckets"] == [
            {"hour_utc": "2026-04-28T10:00:00+00:00", "count": 1},
        ]

    def test_minute_truncation_to_hour(self, tmp_path):
        """同じ hour 内の 10:00 / 10:30 / 10:59 が 1 bucket に集約される。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:30:00+00:00"},
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:59:59+00:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert result["buckets"] == [
            {"hour_utc": "2026-04-28T10:00:00+00:00", "count": 3},
        ]

    def test_malformed_timestamp_silently_skipped(self, tmp_path):
        """parse 失敗 / 欠損 timestamp は silent skip。他 event は集計に残る。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": ""},
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "not-a-date"},
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s"},
            # ↑ timestamp 欠損
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:00:00+00:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert result["buckets"] == [
            {"hour_utc": "2026-04-28T10:00:00+00:00", "count": 1},
        ]

    def test_naive_timestamp_treated_as_utc(self, tmp_path):
        """Issue #71 Finding 2: naive datetime は UTC として扱う。

        `subagent_metrics._week_start_iso` と同じ policy。`rescan_transcripts.py
        --append` 経由で過去 transcript から再投入された event が naive のまま
        流れるケースで、heatmap だけ silent drop されると集計の整合性が崩れる。
        """
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            # naive timestamp (TZ 情報なし) → UTC として 10:00 bucket に入るべき
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:30:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert result["buckets"] == [
            {"hour_utc": "2026-04-28T10:00:00+00:00", "count": 1},
        ]

    def test_week_boundary_separate_buckets(self, tmp_path):
        """日曜 23:00 と月曜 00:00 が別 bucket (時刻 truncate 確認)。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            # 2026-04-26 (Sun) 23:30 UTC
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-26T23:30:00+00:00"},
            # 2026-04-27 (Mon) 00:30 UTC
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-27T00:30:00+00:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert result["buckets"] == [
            {"hour_utc": "2026-04-26T23:00:00+00:00", "count": 1},
            {"hour_utc": "2026-04-27T00:00:00+00:00", "count": 1},
        ]

    def test_buckets_sorted_ascending(self, tmp_path):
        """入力が時刻順でなくても buckets は hour_utc 昇順で出力。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T15:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T12:00:00+00:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        hours = [b["hour_utc"] for b in result["buckets"]]
        assert hours == sorted(hours)

    def test_timezone_field_is_utc(self, tmp_path):
        """任意の入力で payload.timezone == 'UTC' (server 側が UTC 集計したことを明示)。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        # empty
        assert mod.aggregate_hourly_heatmap([])["timezone"] == "UTC"
        # with data
        usage_events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:00:00+00:00"},
        ]
        assert mod.aggregate_hourly_heatmap(usage_events)["timezone"] == "UTC"

    def test_count_is_integer_not_float(self, tmp_path):
        """count は int で返ること (JSON 互換 + browser 側 fmtN 用)。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:00:00+00:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert isinstance(result["buckets"][0]["count"], int)

    def test_subagent_invocation_event_counted(self, tmp_path):
        """`_filter_usage_events()` 経由で subagent invocation event (1 件 = 1 invocation
        の代表) が渡されている前提のテスト。aggregate_hourly_heatmap 自体は filter 済み
        list を受け取る慣習なので、subagent_start 1 件を pass through で 1 count にする。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:00:00+00:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert result["buckets"] == [
            {"hour_utc": "2026-04-28T10:00:00+00:00", "count": 1},
        ]

    def test_all_three_usage_types_in_same_bucket(self, tmp_path):
        """skill_tool / user_slash_command / subagent_start (= invocation 代表) を同 hour
        に並べると同一 bucket で count=3 になる。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:00:00+00:00"},
            {"event_type": "user_slash_command", "skill": "/foo", "source": "expansion",
             "project": "p", "session_id": "s", "timestamp": "2026-04-28T10:15:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Explore",
             "project": "p", "session_id": "s", "timestamp": "2026-04-28T10:30:00+00:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert result["buckets"] == [
            {"hour_utc": "2026-04-28T10:00:00+00:00", "count": 3},
        ]

    # ---- Proposal 2: 境界・カバレッジ強化 ----

    def test_dst_spring_forward_no_bucket_skipped(self, tmp_path):
        """DST 切替日 (US は 2026-03-08) の UTC 連続 hour 境界に events を並べ、
        server が UTC bucket を skip なく出すことを確認。browser 側 DST shift と
        切り分けられるよう、server テストレベルで pin する。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            # US DST 切替の前後 UTC hour 連続 (06, 07, 08, 09 UTC)
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-03-08T06:30:00+00:00"},
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-03-08T07:30:00+00:00"},
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-03-08T08:30:00+00:00"},
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-03-08T09:30:00+00:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        hours = [b["hour_utc"] for b in result["buckets"]]
        assert hours == [
            "2026-03-08T06:00:00+00:00",
            "2026-03-08T07:00:00+00:00",
            "2026-03-08T08:00:00+00:00",
            "2026-03-08T09:00:00+00:00",
        ]
        assert all(b["count"] == 1 for b in result["buckets"])

    def test_full_168_coverage_synthetic(self, tmp_path):
        """7×24=168 の hour すべてに usage event を 1 件ずつ投入し、buckets が 168 件、
        各 count=1 で取りこぼし / 重複なくバケットされることを pin する。180 日 cap (4320)
        以下のサイズで全 hour を埋める。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        # 2026-04-20 (Mon) 〜 2026-04-26 (Sun) の 7 日 × 24 hour = 168 hour
        usage_events = []
        for day in range(7):
            for hour in range(24):
                ts = f"2026-04-{20 + day:02d}T{hour:02d}:00:00+00:00"
                usage_events.append({
                    "event_type": "skill_tool", "skill": "a", "project": "p",
                    "session_id": "s", "timestamp": ts,
                })
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert len(result["buckets"]) == 168
        assert all(b["count"] == 1 for b in result["buckets"])
        # 全部 unique
        assert len({b["hour_utc"] for b in result["buckets"]}) == 168

    def test_hour_zero_and_twenty_three_truncate(self, tmp_path):
        """00:00 / 00:59 / 23:00 / 23:59 → 2 buckets (各 count=2) で hour 端 truncate を pin。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        usage_events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T00:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T00:59:59+00:00"},
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T23:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T23:59:59+00:00"},
        ]
        result = mod.aggregate_hourly_heatmap(usage_events)
        assert result["buckets"] == [
            {"hour_utc": "2026-04-28T00:00:00+00:00", "count": 2},
            {"hour_utc": "2026-04-28T23:00:00+00:00", "count": 2},
        ]


# ============================================================
#  TestBuildDashboardDataWithHeatmap — build_dashboard_data 統合
# ============================================================
class TestBuildDashboardDataWithHeatmap:
    def test_build_dashboard_data_includes_hourly_heatmap_key(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        data = mod.build_dashboard_data([])
        assert "hourly_heatmap" in data
        assert data["hourly_heatmap"] == {"timezone": "UTC", "buckets": []}

    def test_hourly_heatmap_count_matches_total_events_in_simple_case(self, tmp_path):
        """同一 hour に usage 3 件 → total_events == sum(b.count) == 3 で filter 整合。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "b", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:15:00+00:00"},
            {"event_type": "user_slash_command", "skill": "/foo", "source": "expansion",
             "project": "p", "session_id": "s", "timestamp": "2026-04-28T10:30:00+00:00"},
            # 以下は除外 (housekeeping)
            {"event_type": "session_start", "source": "startup",
             "project": "p", "session_id": "s", "timestamp": "2026-04-28T10:01:00+00:00"},
            {"event_type": "notification", "notification_type": "idle",
             "project": "p", "session_id": "s", "timestamp": "2026-04-28T10:02:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        bucket_total = sum(b["count"] for b in data["hourly_heatmap"]["buckets"])
        assert bucket_total == data["total_events"] == 3

    def test_build_dashboard_data_excludes_housekeeping_from_heatmap(self, tmp_path):
        """session_* / notification / instructions_loaded / compact_* / subagent_stop は
        heatmap に入らない (= total_events と同じ filter 慣習を踏襲)。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "session_start", "source": "startup",
             "project": "p", "session_id": "s", "timestamp": "2026-04-28T10:00:00+00:00"},
            {"event_type": "notification", "notification_type": "idle",
             "project": "p", "session_id": "s", "timestamp": "2026-04-28T10:00:00+00:00"},
            {"event_type": "instructions_loaded", "file_path": "/x",
             "project": "p", "session_id": "s", "timestamp": "2026-04-28T10:00:00+00:00"},
            {"event_type": "compact_start", "trigger": "auto",
             "project": "p", "session_id": "s", "timestamp": "2026-04-28T10:00:00+00:00"},
            {"event_type": "subagent_stop", "subagent_type": "Explore",
             "project": "p", "session_id": "s", "timestamp": "2026-04-28T10:00:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert data["hourly_heatmap"]["buckets"] == []

    def test_build_dashboard_data_dedupes_subagent_invocation_in_heatmap(self, tmp_path):
        """subagent_start + subagent_lifecycle_start (1 sec 以内 / 同 session+type) は
        invocation 単位 dedup で heatmap に 1 count しか出ない。"""
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore",
             "tool_use_id": "toolu_a", "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:00:00+00:00"},
            {"event_type": "subagent_lifecycle_start", "subagent_type": "Explore",
             "project": "p", "session_id": "s",
             "timestamp": "2026-04-28T10:00:00.500000+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        bucket_total = sum(b["count"] for b in data["hourly_heatmap"]["buckets"])
        assert bucket_total == data["total_events"] == 1
