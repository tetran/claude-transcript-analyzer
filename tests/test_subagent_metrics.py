"""tests/test_subagent_metrics.py — subagent_metrics の generic regression suite。

Issue #100 (= #93 調査結果対応): 同 (session_id, subagent_id) で複数発火した
subagent_stop を min(timestamp) で 1 件化する dedup の pin。
"""
import subagent_metrics


def _start(name, session, ts, success=True, duration_ms=None):
    ev = {"event_type": "subagent_start", "subagent_type": name,
          "session_id": session, "project": "p", "timestamp": ts, "success": success}
    if duration_ms is not None:
        ev["duration_ms"] = duration_ms
    return ev


def _stop(name, session, ts, agent_id="agent-x"):
    return {"event_type": "subagent_stop", "subagent_type": name,
            "subagent_id": agent_id, "session_id": session, "project": "p", "timestamp": ts}


class TestSubagentStopAgentIdDedup:
    def test_four_stops_same_agent_id_collapse_to_one_invocation(self):
        """同 (session, subagent_id) の subagent_stop が 4 件発火しても 1 invocation 扱い。
        最大 4 重複 (Issue #93 観察) を first-wins で 1 件化する pin。"""
        events = [
            _start("Explore", "s", "2026-04-22T10:00:00+00:00"),
            _stop("Explore",  "s", "2026-04-22T10:00:01+00:00", agent_id="agent-A"),
            _stop("Explore",  "s", "2026-04-22T10:00:02+00:00", agent_id="agent-A"),
            _stop("Explore",  "s", "2026-04-22T10:00:03+00:00", agent_id="agent-A"),
            _stop("Explore",  "s", "2026-04-22T10:00:04+00:00", agent_id="agent-A"),
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)
        assert m["Explore"]["count"] == 1, "start 1 件 → invocation 1 件"
        assert m["Explore"]["failure_count"] == 0

    def test_dedup_keeps_earliest_timestamp_stop_regardless_of_input_order(self):
        """dedup は **timestamp 最小** の stop を保持 (= 入力順 first ではない)。
        rescan_transcripts.py --append 経由で input order が timestamp 順と乖離する
        ケースに備え、min(timestamp) semantic を pin する (Issue #100 reviewer P2 由来)。

        入力順 (09 → 01 → 05) と timestamp 順 (01 → 05 → 09) が異なる構成で、
        surviving stop の timestamp が earliest (10:00:01) であることを直接 pin。

        Note: public API (`aggregate_subagent_metrics` / `invocation_records`) は
        surviving stop の timestamp を露出しないため、`_bucket_events()` を直接
        probe する。`_bucket_events` は dedup semantic の contract owner なので
        regression-pin として直 probe は適切 (reviewer iteration 2 P2 の判断)。"""
        events = [
            _start("Plan", "s", "2026-04-22T10:00:00+00:00"),
            _stop("Plan",  "s", "2026-04-22T10:00:09+00:00", agent_id="agent-B"),
            _stop("Plan",  "s", "2026-04-22T10:00:01+00:00", agent_id="agent-B"),
            _stop("Plan",  "s", "2026-04-22T10:00:05+00:00", agent_id="agent-B"),
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)
        assert m["Plan"]["count"] == 1
        trend = subagent_metrics.aggregate_subagent_failure_trend(events)
        from collections import Counter
        ct = Counter(r["subagent_type"] for r in trend)
        assert ct["Plan"] == 1
        _starts, stops, _lifecycle = subagent_metrics._bucket_events(events)
        plan_stops = stops[("s", "Plan")]
        assert len(plan_stops) == 1, "重複 stop が 1 件に集約される"
        assert plan_stops[0]["timestamp"] == "2026-04-22T10:00:01+00:00", \
            "min(timestamp) semantic: earliest stop が survive する (input-order first ではない)"

    def test_dedup_does_not_collapse_distinct_agent_ids(self):
        """異なる subagent_id は別 invocation として扱う (= 同 type 並行実行は dedup しない)。
        INVOCATION_MERGE_WINDOW=1s を超える間隔で 2 invocation を立て、両 stop が
        どちらも paired されることを pin (drift guard)。"""
        events = [
            _start("Explore", "s", "2026-04-22T10:00:00+00:00"),
            _start("Explore", "s", "2026-04-22T10:00:10+00:00"),
            _stop("Explore",  "s", "2026-04-22T10:00:05+00:00", agent_id="agent-X"),
            _stop("Explore",  "s", "2026-04-22T10:00:15+00:00", agent_id="agent-Y"),
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)
        assert m["Explore"]["count"] == 2
        assert m["Explore"]["failure_count"] == 0

    def test_dedup_missing_subagent_id_treats_each_stop_separately(self):
        """subagent_id="" の stop は dedup key を共有しない → 個別扱い (= 既存挙動)。
        record_subagent.py:107 が agent_id 不在時に "" を入れる現契約を pin。

        reviewer iteration 3 P2 強化: test name の主張「treats each stop separately」を
        `_bucket_events` 直接 probe で実際に pin する (= start count だけでは
        '空 agent_id stop が 2 件残ること' の挙動が assert されない問題の対処)。"""
        events = [
            _start("Explore", "s", "2026-04-22T10:00:00+00:00"),
            _stop("Explore",  "s", "2026-04-22T10:00:01+00:00", agent_id=""),
            _stop("Explore",  "s", "2026-04-22T10:00:02+00:00", agent_id=""),
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)
        assert m["Explore"]["count"] == 1
        _starts, stops, _lc = subagent_metrics._bucket_events(events)
        assert len(stops[("s", "Explore")]) == 2, \
            "subagent_id='' な stop は dedup key を共有しない → 2 件残る"


class TestEmptySubagentTypeStillExcluded:
    """既存 `if not name: continue` (= subagent_type == "" 暗黙除外) が
    本変更後も維持されていることの drift guard。Issue #93 で確認した
    メイン誤発火 type='' record が aggregator に漏れない pin。"""

    def test_type_empty_subagent_stop_does_not_create_invocation(self):
        events = [
            {"event_type": "subagent_stop", "subagent_type": "",
             "subagent_id": "agent-z", "session_id": "s1", "project": "p",
             "timestamp": "2026-04-22T10:00:00+00:00"},
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)
        assert m == {}, "type='' は構造的に除外されている (主流 invocation を生まない)"
