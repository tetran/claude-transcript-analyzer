"""tests/test_subagent_quality.py — Issue #60 subagent quality (A5 percentile + B3 weekly trend) のテスト。

A5: subagent_type ごとの invocation duration を p50 / p90 / p99 + sample_count で集計し
    `subagent_ranking` 配列の各要素に additive で乗せる。
    計算手法は `statistics.quantiles(method="inclusive")` = Excel `PERCENTILE.INC` 等価。

B3: subagent invocation の week (monday-UTC start) × subagent_type で failure_rate trend を返す。
    server は top-N で切らず観測された全 (week, subagent_type) を返す (UI 側 top-5 は描画 affordance)。

詳細は `docs/plans/issue-60-subagent-quality.md` を参照。
"""
# pylint: disable=line-too-long
import importlib.util
import os
from pathlib import Path

import pytest

import subagent_metrics

_DASHBOARD_PATH = Path(__file__).parent.parent / "dashboard" / "server.py"


def load_dashboard_module(usage_jsonl: Path, alerts_jsonl: Path | None = None):
    """USAGE_JSONL をパッチした状態で dashboard モジュールを読み込む (test_dashboard.py 流)。"""
    os.environ["USAGE_JSONL"] = str(usage_jsonl)
    if alerts_jsonl is not None:
        os.environ["HEALTH_ALERTS_JSONL"] = str(alerts_jsonl)
    try:
        spec = importlib.util.spec_from_file_location("dashboard_server_quality", _DASHBOARD_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del os.environ["USAGE_JSONL"]
        if alerts_jsonl is not None:
            del os.environ["HEALTH_ALERTS_JSONL"]
    return mod


def _start(name, session, ts, success=True, duration_ms=None, project="p"):
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


def _stop(name, session, ts, success=True, duration_ms=None):
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


def _lifecycle(name, session, ts, project="p"):
    return {
        "event_type": "subagent_lifecycle_start",
        "subagent_type": name,
        "project": project,
        "session_id": session,
        "timestamp": ts,
    }


# ============================================================
#  TestPercentileEdgeCases — _percentiles helper
# ============================================================

class TestPercentileEdgeCases:
    def test_empty_list_returns_none_triple(self):
        assert subagent_metrics._percentiles([]) == (None, None, None)

    def test_single_value_repeated_for_all_percentiles(self):
        # len == 1 → 退化扱い: 全 percentile が data[0]
        assert subagent_metrics._percentiles([42.0]) == (42.0, 42.0, 42.0)

    def test_two_values_inclusive_method(self):
        # statistics.quantiles([1, 2], n=100, method="inclusive") の cuts:
        # index 49 → p50 = 1 + 0.5 = 1.5
        # index 89 → p50 + (89-49)*step ... = 1.9
        # index 98 → 1.99
        p50, p90, p99 = subagent_metrics._percentiles([1.0, 2.0])
        assert p50 == pytest.approx(1.5, abs=1e-6)
        assert p90 == pytest.approx(1.9, abs=1e-6)
        assert p99 == pytest.approx(1.99, abs=1e-6)

    def test_odd_count_n5(self):
        p50, p90, p99 = subagent_metrics._percentiles([1.0, 2.0, 3.0, 4.0, 5.0])
        # inclusive: p = k/100, index = p * (n-1) = p * 4
        # p50: 0.5*4=2.0 → sorted[2] = 3.0
        # p90: 0.9*4=3.6 → 4 + 0.6*1 = 4.6
        # p99: 0.99*4=3.96 → 4 + 0.96 = 4.96
        assert p50 == pytest.approx(3.0, abs=1e-6)
        assert p90 == pytest.approx(4.6, abs=1e-6)
        assert p99 == pytest.approx(4.96, abs=1e-6)

    def test_even_count_n10(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        p50, p90, p99 = subagent_metrics._percentiles(data)
        # inclusive: index = p * (n-1) = p * 9
        # p50: 4.5 → 5.0 + 0.5*1 = 5.5
        # p90: 8.1 → 9 + 0.1*1 = 9.1
        # p99: 8.91 → 9 + 0.91 = 9.91
        assert p50 == pytest.approx(5.5, abs=1e-6)
        assert p90 == pytest.approx(9.1, abs=1e-6)
        assert p99 == pytest.approx(9.91, abs=1e-6)

    def test_all_same_values(self):
        p50, p90, p99 = subagent_metrics._percentiles([5.0, 5.0, 5.0, 5.0])
        assert p50 == 5.0
        assert p90 == 5.0
        assert p99 == 5.0

    def test_known_sample_pin_excel_inclusive(self):
        """[1,2,3,4] に対し p50=2.5 / p90=3.7 / p99=3.97 を pin。
        Excel PERCENTILE.INC (= statistics.quantiles の `method="inclusive"`) との
        等価性を担保し、method 切替え (inclusive → exclusive 等) や numpy の
        `method="linear"` (exclusive endpoints) などへの差し替えによる回帰を検出する。"""
        p50, p90, p99 = subagent_metrics._percentiles([1.0, 2.0, 3.0, 4.0])
        assert p50 == pytest.approx(2.5, abs=1e-6)
        assert p90 == pytest.approx(3.7, abs=1e-6)
        assert p99 == pytest.approx(3.97, abs=1e-6)

    def test_monotonic_p50_le_p90_le_p99(self):
        """P6(a) 反映: 任意の入力で p50 <= p90 <= p99 が成立 (cut index off-by-one 検出)"""
        for data in [
            [1.0, 2.0, 3.0],
            [10.0, 20.0, 30.0, 40.0, 50.0],
            [100.0, 200.0],
            [1.0, 1.0, 5.0, 10.0, 10.0],
            [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 2.0, 3.0, 5.0, 10.0],
        ]:
            p50, p90, p99 = subagent_metrics._percentiles(data)
            assert p50 is not None and p90 is not None and p99 is not None
            assert p50 <= p90 <= p99, f"monotonic violated for {data}: {p50} / {p90} / {p99}"


# ============================================================
#  TestSubagentMetricsAddsPercentileFields — _build_metrics 拡張
# ============================================================

class TestSubagentMetricsAddsPercentileFields:
    def test_metrics_dict_has_percentile_keys(self):
        events = [
            _start("Explore", "s", "2026-04-21T00:00:00+00:00", duration_ms=1000),
            _stop("Explore", "s", "2026-04-21T00:00:01+00:00", duration_ms=1000),
            _start("Explore", "s", "2026-04-22T00:00:00+00:00", duration_ms=2000),
            _stop("Explore", "s", "2026-04-22T00:00:01+00:00", duration_ms=2000),
        ]
        metrics = subagent_metrics.aggregate_subagent_metrics(events)
        assert "Explore" in metrics
        m = metrics["Explore"]
        for key in ("p50_duration_ms", "p90_duration_ms", "p99_duration_ms", "sample_count"):
            assert key in m, f"key {key} missing"

    def test_p50_p90_p99_present_when_durations_present(self):
        events = [
            _start("Explore", "s", "2026-04-21T00:00:00+00:00", duration_ms=1000),
            _stop("Explore", "s", "2026-04-21T00:00:01+00:00", duration_ms=1000),
            _start("Explore", "s", "2026-04-22T00:00:00+00:00", duration_ms=2000),
            _stop("Explore", "s", "2026-04-22T00:00:01+00:00", duration_ms=2000),
            _start("Explore", "s", "2026-04-23T00:00:00+00:00", duration_ms=3000),
            _stop("Explore", "s", "2026-04-23T00:00:01+00:00", duration_ms=3000),
            _start("Explore", "s", "2026-04-24T00:00:00+00:00", duration_ms=4000),
            _stop("Explore", "s", "2026-04-24T00:00:01+00:00", duration_ms=4000),
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)["Explore"]
        # data = [1000, 2000, 3000, 4000] → p50=2500, p90=3700, p99=3970
        assert m["p50_duration_ms"] == pytest.approx(2500.0, abs=1.0)
        assert m["p90_duration_ms"] == pytest.approx(3700.0, abs=1.0)
        assert m["p99_duration_ms"] == pytest.approx(3970.0, abs=1.0)

    def test_percentile_none_when_no_durations(self):
        events = [
            # duration_ms 無しの invocation × 2
            _start("Plan", "s", "2026-04-21T00:00:00+00:00"),
            _stop("Plan", "s", "2026-04-21T00:00:01+00:00"),
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)["Plan"]
        assert m["p50_duration_ms"] is None
        assert m["p90_duration_ms"] is None
        assert m["p99_duration_ms"] is None
        assert m["sample_count"] == 0

    def test_avg_and_percentiles_share_sample_set(self):
        # 単一 sample 入力: avg == p50 == p90 == p99 (退化 case)
        events = [
            _start("Plan", "s", "2026-04-21T00:00:00+00:00", duration_ms=1234),
            _stop("Plan", "s", "2026-04-21T00:00:01+00:00", duration_ms=1234),
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)["Plan"]
        assert m["avg_duration_ms"] == 1234.0
        assert m["p50_duration_ms"] == 1234.0
        assert m["p90_duration_ms"] == 1234.0
        assert m["p99_duration_ms"] == 1234.0

    def test_sample_count_equals_len_durations(self):
        events = [
            _start("Explore", "s", "2026-04-21T00:00:00+00:00", duration_ms=1000),
            _stop("Explore", "s", "2026-04-21T00:00:01+00:00", duration_ms=1000),
            _start("Explore", "s", "2026-04-22T00:00:00+00:00", duration_ms=2000),
            _stop("Explore", "s", "2026-04-22T00:00:01+00:00", duration_ms=2000),
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)["Explore"]
        assert m["sample_count"] == 2

    def test_sample_count_le_count_invariant(self):
        """P6(b) 反映: durations が None の invocation で count > sample_count に
        なるケースを構築し、sample_count <= count を pin。"""
        events = [
            # invocation 1: duration あり
            _start("Explore", "s", "2026-04-21T00:00:00+00:00", duration_ms=1000),
            _stop("Explore", "s", "2026-04-21T00:00:01+00:00", duration_ms=1000),
            # invocation 2: duration 完全に欠損
            _start("Explore", "s", "2026-04-22T00:00:00+00:00"),
            _stop("Explore", "s", "2026-04-22T00:00:01+00:00"),
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)["Explore"]
        assert m["count"] == 2
        assert m["sample_count"] == 1
        assert m["sample_count"] <= m["count"]

    def test_orphan_stops_do_not_contaminate_percentile_samples(self):
        """同 (session, type) bucket に start 1 + stops 2 のような余り stops が
        あるとき、stop 単独イベントの duration_ms が durations に混入して
        sample_count > count を生まないことを pin (= orphan stop は invocation 単位
        集計の対象外、`sample_count <= count` invariant を構造的に維持)。"""
        events = [
            _start("Explore", "s", "2026-04-22T10:00:00+00:00", duration_ms=1000),
            _stop("Explore", "s", "2026-04-22T10:00:01+00:00", duration_ms=1000),
            # orphan stop: invocation がペアリングされない 2 つ目の stop
            _stop("Explore", "s", "2026-04-22T11:00:00+00:00", duration_ms=5000),
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)["Explore"]
        assert m["count"] == 1
        assert m["sample_count"] == 1, \
            f"orphan stop duration が percentile sample を汚染: sample_count={m['sample_count']}"
        assert m["sample_count"] <= m["count"]
        # avg / p50/p90/p99 はすべて invocation 1 件分の 1000.0 から (5000 が混入していない)
        assert m["avg_duration_ms"] == 1000.0
        assert m["p50_duration_ms"] == 1000.0
        assert m["p99_duration_ms"] == 1000.0

    def test_orphan_stops_do_not_affect_failure_count_drift(self):
        """orphan stop fix 後も failure_count drift guard (Q1) を破らない:
        _process_bucket と _bucket_invocation_records の failure_count が一致し続ける。"""
        events = [
            _start("Explore", "s", "2026-04-22T10:00:00+00:00", success=False),
            _stop("Explore",  "s", "2026-04-22T10:00:01+00:00", success=False),
            _stop("Explore",  "s", "2026-04-22T11:00:00+00:00", success=False),  # orphan
        ]
        metrics = subagent_metrics.aggregate_subagent_metrics(events)
        trend = subagent_metrics.aggregate_subagent_failure_trend(events)
        from collections import Counter
        trend_failures = Counter()
        for r in trend:
            trend_failures[r["subagent_type"]] += r["failure_count"]
        for name, m in metrics.items():
            assert trend_failures[name] == m["failure_count"]

    def test_existing_fields_unchanged(self):
        """count / failure_count / failure_rate / avg_duration_ms が破壊されていない (regression)"""
        events = [
            _start("Explore", "s", "2026-04-21T00:00:00+00:00", success=True, duration_ms=1000),
            _stop("Explore", "s", "2026-04-21T00:00:01+00:00", success=True, duration_ms=1000),
            _start("Explore", "s", "2026-04-22T00:00:00+00:00", success=False, duration_ms=2000),
            _stop("Explore", "s", "2026-04-22T00:00:01+00:00", success=False, duration_ms=2000),
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)["Explore"]
        assert m["count"] == 2
        assert m["failure_count"] == 1
        assert m["failure_rate"] == 0.5
        assert m["avg_duration_ms"] == 1500.0


# ============================================================
#  TestInvocationRecords — invocation_records helper (Phase 2)
# ============================================================

class TestInvocationRecords:
    def test_empty_returns_empty(self):
        assert subagent_metrics.invocation_records([]) == []

    def test_single_invocation_with_start_only(self):
        events = [_start("Explore", "s", "2026-04-21T00:00:00+00:00")]
        recs = subagent_metrics.invocation_records(events)
        assert len(recs) == 1
        assert recs[0]["subagent_type"] == "Explore"
        assert recs[0]["timestamp"] == "2026-04-21T00:00:00+00:00"
        assert recs[0]["failed"] is False

    def test_single_invocation_with_lifecycle_only(self):
        events = [_lifecycle("Explore", "s", "2026-04-21T00:00:00+00:00")]
        recs = subagent_metrics.invocation_records(events)
        assert len(recs) == 1
        assert recs[0]["subagent_type"] == "Explore"
        assert recs[0]["timestamp"] == "2026-04-21T00:00:00+00:00"
        assert recs[0]["failed"] is False

    def test_start_plus_stop_pair_one_invocation(self):
        events = [
            _start("Explore", "s", "2026-04-21T00:00:00+00:00"),
            _stop("Explore", "s", "2026-04-21T00:00:05+00:00"),
        ]
        recs = subagent_metrics.invocation_records(events)
        assert len(recs) == 1
        assert recs[0]["failed"] is False

    def test_start_failed_records_failed_true(self):
        events = [
            _start("Explore", "s", "2026-04-21T00:00:00+00:00", success=False),
        ]
        recs = subagent_metrics.invocation_records(events)
        assert len(recs) == 1
        assert recs[0]["failed"] is True

    def test_stop_failed_records_failed_true(self):
        events = [
            _start("Explore", "s", "2026-04-21T00:00:00+00:00", success=True),
            _stop("Explore", "s", "2026-04-21T00:00:05+00:00", success=False),
        ]
        recs = subagent_metrics.invocation_records(events)
        assert len(recs) == 1
        assert recs[0]["failed"] is True

    def test_both_succeeded_records_failed_false(self):
        events = [
            _start("Explore", "s", "2026-04-21T00:00:00+00:00", success=True),
            _stop("Explore", "s", "2026-04-21T00:00:05+00:00", success=True),
        ]
        recs = subagent_metrics.invocation_records(events)
        assert len(recs) == 1
        assert recs[0]["failed"] is False

    def test_timestamp_uses_start_when_present(self):
        events = [
            _start("Explore", "s", "2026-04-21T00:00:00+00:00"),
            _lifecycle("Explore", "s", "2026-04-21T00:00:00.500+00:00"),  # 0.5s 後 → 同一 invocation
        ]
        recs = subagent_metrics.invocation_records(events)
        assert len(recs) == 1
        assert recs[0]["timestamp"] == "2026-04-21T00:00:00+00:00"

    def test_timestamp_falls_back_to_lifecycle(self):
        events = [_lifecycle("Explore", "s", "2026-04-21T00:00:00+00:00")]
        recs = subagent_metrics.invocation_records(events)
        assert recs[0]["timestamp"] == "2026-04-21T00:00:00+00:00"

    def test_invocation_count_matches_metrics_count(self):
        """invocation_records と aggregate_subagent_metrics の type 別 count が一致。"""
        events = [
            _start("Explore", "s1", "2026-04-21T00:00:00+00:00"),
            _stop("Explore", "s1", "2026-04-21T00:00:05+00:00"),
            _start("Explore", "s2", "2026-04-22T00:00:00+00:00"),
            _stop("Explore", "s2", "2026-04-22T00:00:05+00:00"),
            _start("Plan", "s1", "2026-04-21T01:00:00+00:00"),
        ]
        recs = subagent_metrics.invocation_records(events)
        metrics = subagent_metrics.aggregate_subagent_metrics(events)
        from collections import Counter
        rec_counts = Counter(r["subagent_type"] for r in recs)
        for name, m in metrics.items():
            assert rec_counts[name] == m["count"], f"{name}: rec={rec_counts[name]} vs metrics.count={m['count']}"


# ============================================================
#  TestAggregateSubagentFailureTrend — Phase 3
# ============================================================

class TestAggregateSubagentFailureTrend:
    def test_empty_events_returns_empty_list(self):
        assert subagent_metrics.aggregate_subagent_failure_trend([]) == []

    def test_single_invocation_creates_one_bucket(self):
        events = [
            _start("Explore", "s", "2026-04-22T10:00:00+00:00"),
            _stop("Explore", "s", "2026-04-22T10:00:05+00:00"),
        ]
        result = subagent_metrics.aggregate_subagent_failure_trend(events)
        assert len(result) == 1
        assert result[0]["week_start"] == "2026-04-20"  # Monday of 2026-04-22 (Wed)
        assert result[0]["subagent_type"] == "Explore"
        assert result[0]["count"] == 1
        assert result[0]["failure_count"] == 0
        assert result[0]["failure_rate"] == 0.0

    def test_sunday_2359_and_monday_0000_are_different_weeks(self):
        """日曜 23:59:59 UTC と 月曜 00:00:00 UTC が別 week_start を持つ"""
        events = [
            _start("Explore", "s", "2026-04-26T23:59:59+00:00"),  # Sun
            _stop("Explore", "s", "2026-04-26T23:59:59.500+00:00"),
            _start("Explore", "s", "2026-04-27T00:00:00+00:00"),  # Mon
            _stop("Explore", "s", "2026-04-27T00:00:00.500+00:00"),
        ]
        result = subagent_metrics.aggregate_subagent_failure_trend(events)
        weeks = sorted({r["week_start"] for r in result})
        assert weeks == ["2026-04-20", "2026-04-27"]

    def test_monday_0000_is_new_week_boundary(self):
        events = [
            _start("Explore", "s", "2026-04-27T00:00:00+00:00"),
        ]
        result = subagent_metrics.aggregate_subagent_failure_trend(events)
        assert len(result) == 1
        assert result[0]["week_start"] == "2026-04-27"

    def test_weeks_normalized_to_monday_utc(self):
        """火 / 水 / 日 (同週) を投げて全部同じ week_start にまとまる"""
        events = [
            _start("Explore", "s", "2026-04-21T00:00:00+00:00"),  # Tue
            _start("Explore", "s2", "2026-04-22T00:00:00+00:00"),  # Wed
            _start("Explore", "s3", "2026-04-26T23:00:00+00:00"),  # Sun
        ]
        result = subagent_metrics.aggregate_subagent_failure_trend(events)
        assert len(result) == 1
        assert result[0]["week_start"] == "2026-04-20"
        assert result[0]["count"] == 3

    def test_naive_timestamp_treated_as_utc(self):
        """naive timestamp (TZ サフィックスなし) でも UTC として解釈され、local TZ shift
        が起きないことを pin: usage.jsonl は通常 aware ISO だが、Stop hook 経路や
        rescan_transcripts.py --append 由来で naive が紛れた場合に Python 3.11+ の
        `astimezone()` が local TZ 解釈で silent shift する非対称を構造的に塞ぐ。"""
        events = [
            # naive: TZ サフィックスなし
            {"event_type": "subagent_start", "subagent_type": "Explore",
             "session_id": "s1", "project": "p", "timestamp": "2026-04-22T10:00:00"},
            # aware: 同じ instant を UTC で表記
            _start("Explore", "s2", "2026-04-22T10:00:00+00:00"),
        ]
        result = subagent_metrics.aggregate_subagent_failure_trend(events)
        weeks = {r["week_start"] for r in result}
        assert weeks == {"2026-04-20"}, f"naive と aware の week_start が一致しない: {weeks}"

    def test_failure_rate_when_count_is_zero_invariant(self):
        """構造的に count=0 の bucket は出力しないが、念の為「もし 0 なら 0.0」を pin"""
        result = subagent_metrics.aggregate_subagent_failure_trend([])
        for r in result:
            assert not (r["count"] == 0 and r["failure_rate"] != 0.0)

    def test_failure_rate_calculation(self):
        events = [
            _start("Explore", f"s{i}", "2026-04-22T10:00:00+00:00", success=(i != 0))
            for i in range(4)
        ]
        result = subagent_metrics.aggregate_subagent_failure_trend(events)
        bucket = next(r for r in result if r["subagent_type"] == "Explore")
        assert bucket["count"] == 4
        assert bucket["failure_count"] == 1
        assert bucket["failure_rate"] == 0.25

    def test_multiple_subagent_types_separated(self):
        events = [
            _start("Explore", "s1", "2026-04-22T10:00:00+00:00"),
            _start("Plan", "s2", "2026-04-22T10:00:00+00:00"),
        ]
        result = subagent_metrics.aggregate_subagent_failure_trend(events)
        types = {r["subagent_type"] for r in result}
        assert types == {"Explore", "Plan"}

    def test_observed_zero_subagent_not_in_output(self):
        """subagent_type=A しか観測されていない期間に B の trend は出力されない"""
        events = [_start("Explore", "s", "2026-04-22T10:00:00+00:00")]
        result = subagent_metrics.aggregate_subagent_failure_trend(events)
        types = {r["subagent_type"] for r in result}
        assert "Plan" not in types

    def test_sort_by_week_then_type_lex(self):
        """(week_start ASC, subagent_type ASC) で sort される (insertion order 非依存)"""
        events = [
            _start("Plan",    "s1", "2026-04-27T10:00:00+00:00"),    # week B / type Plan
            _start("Explore", "s2", "2026-04-20T10:00:00+00:00"),    # week A / type Explore
            _start("Plan",    "s3", "2026-04-20T10:00:00+00:00"),    # week A / type Plan
            _start("Explore", "s4", "2026-04-27T10:00:00+00:00"),    # week B / type Explore
        ]
        result = subagent_metrics.aggregate_subagent_failure_trend(events)
        keys = [(r["week_start"], r["subagent_type"]) for r in result]
        assert keys == sorted(keys), f"sort 失敗: {keys}"

    def test_returns_all_types_no_top_n_cap(self):
        """server は top-N で切らず観測された全 (week, type) を返す。client 側 top-5
        は UI affordance であり schema 仕様には現れない (= programmatic consumer は
        全 type の trend を受け取る前提で読む)。"""
        events = []
        for i in range(6):
            events.append(_start(f"Type{i}", f"s{i}", "2026-04-22T10:00:00+00:00"))
        result = subagent_metrics.aggregate_subagent_failure_trend(events)
        types = {r["subagent_type"] for r in result}
        assert types == {f"Type{i}" for i in range(6)}, f"top-N で切られた: {types}"

    def test_orphan_start_does_not_misattribute_failure_to_earlier_week(self):
        """件数不一致 + start.success=True が sequential 消費されると、後続週の failed
        stop が earlier 週の successful start にマッチして failure が誤って earlier 週へ
        shift する問題への regression。timestamp-window pairing で防ぐ。

        scenario: 2 succeeded starts (W1, W2) + 1 stop(success=False) in W2.
        expected: W1 fail=0/1, W2 fail=1/1。"""
        events = [
            _start("Explore", "s", "2026-04-22T10:00:00+00:00", success=True),  # W1=2026-04-20
            _start("Explore", "s", "2026-04-29T10:00:00+00:00", success=True),  # W2=2026-04-27
            _stop("Explore",  "s", "2026-04-29T10:00:30+00:00", success=False),  # W2 stop, failed
        ]
        trend = subagent_metrics.aggregate_subagent_failure_trend(events)
        by_week = {r["week_start"]: r for r in trend}
        assert by_week["2026-04-20"]["failure_count"] == 0, \
            f"W1 should have 0 failures, got {by_week['2026-04-20']['failure_count']}"
        assert by_week["2026-04-27"]["failure_count"] == 1, \
            f"W2 should have 1 failure, got {by_week['2026-04-27']['failure_count']}"

    def test_failure_count_matches_metrics_failure_count(self):
        """`aggregate_subagent_failure_trend` 経由の failure_count と
        `aggregate_subagent_metrics` の failure_count が type 単位の合計で一致することを
        pin (drift guard)。両者は `_pair_invocations_with_stops` で共通 pair 列を
        共有しているため構造的に一致するが、将来の片側変更で drift しないようテストで
        固定する。"""
        events = [
            _start("Explore", "s1", "2026-04-22T10:00:00+00:00", success=True),
            _stop("Explore", "s1", "2026-04-22T10:00:05+00:00", success=True),
            _start("Explore", "s2", "2026-04-22T11:00:00+00:00", success=False),
            _stop("Explore", "s2", "2026-04-22T11:00:05+00:00", success=False),
            _start("Explore", "s3", "2026-04-29T11:00:00+00:00", success=True),
            _stop("Explore", "s3", "2026-04-29T11:00:05+00:00", success=False),
            _start("Plan", "s1", "2026-04-22T12:00:00+00:00", success=False),
        ]
        trend = subagent_metrics.aggregate_subagent_failure_trend(events)
        metrics = subagent_metrics.aggregate_subagent_metrics(events)
        from collections import Counter
        type_failures = Counter()
        for r in trend:
            type_failures[r["subagent_type"]] += r["failure_count"]
        for name, m in metrics.items():
            assert type_failures[name] == m["failure_count"], \
                f"{name}: trend.failure_count={type_failures[name]} != metrics.failure_count={m['failure_count']}"


# ============================================================
#  TestBuildDashboardDataIncludesQualityFields — Phase 4 integration
# ============================================================

class TestBuildDashboardDataIncludesQualityFields:
    def test_subagent_failure_trend_key_present_empty(self, tmp_path):
        usage = tmp_path / "usage.jsonl"
        usage.write_text("")
        mod = load_dashboard_module(usage, tmp_path / "alerts.jsonl")
        data = mod.build_dashboard_data([])
        assert "subagent_failure_trend" in data
        assert data["subagent_failure_trend"] == []

    def test_subagent_ranking_items_have_percentile_keys(self, tmp_path):
        usage = tmp_path / "usage.jsonl"
        usage.write_text("")
        mod = load_dashboard_module(usage, tmp_path / "alerts.jsonl")
        events = [
            _start("Explore", "s", "2026-04-22T10:00:00+00:00", duration_ms=1000),
            _stop("Explore", "s", "2026-04-22T10:00:01+00:00", duration_ms=1000),
        ]
        data = mod.build_dashboard_data(events)
        assert data["subagent_ranking"], "ranking should not be empty"
        item = data["subagent_ranking"][0]
        for key in ("p50_duration_ms", "p90_duration_ms", "p99_duration_ms", "sample_count"):
            assert key in item, f"key {key} missing from subagent_ranking item: {item}"

    def test_percentile_consistency_with_avg(self, tmp_path):
        """単一 sample 入力で avg == p50 == p90 == p99 (退化 case の sanity)"""
        usage = tmp_path / "usage.jsonl"
        usage.write_text("")
        mod = load_dashboard_module(usage, tmp_path / "alerts.jsonl")
        events = [
            _start("Plan", "s", "2026-04-22T10:00:00+00:00", duration_ms=2500),
            _stop("Plan", "s", "2026-04-22T10:00:01+00:00", duration_ms=2500),
        ]
        data = mod.build_dashboard_data(events)
        item = next(it for it in data["subagent_ranking"] if it["name"] == "Plan")
        assert item["avg_duration_ms"] == item["p50_duration_ms"] == item["p90_duration_ms"] == item["p99_duration_ms"]
