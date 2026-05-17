"""dashboard/aggregate.py — period filter + 集計群 (Issue #123 Phase 1).

dashboard/server.py から区画 B を切り出した。period filter helper・全
aggregate_* 関数・load_events / load_health_alerts を保持する。
"""
import itertools
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from analyzer.subagent import (
    _bucket_events,
    _build_invocations,
    _pair_invocations_with_stops,
    aggregate_subagent_metrics,
    usage_invocation_events,
    usage_invocation_intervals,
)

from dashboard.config import (
    ALERTS_FILE,
    DATA_FILE,
    TOP_N,
    _PERIOD_DELTAS,
    _PERMISSION_NOTIFICATION_TYPES,
    _SKILL_USAGE_EVENT_TYPES,
)


def _filter_events_by_period(
    events: list[dict],
    period: str,
    *,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Overview / Patterns aggregator にのみ渡す view を返す。Quality / Surface aggregator は unfiltered events を受ける (Issue #85 plan §3 Step 1).

    使い分け契約 (Quality / Surface 系の filtering を防ぐため、build_dashboard_data の call site で
    本 helper を呼ばないことで対応する。本 helper は誤用防止のため call site でのみ使う):
      - period 適用 12 field (KPI 4 + Overview 4 + Patterns 3 + session_stats 1) には本 view を渡す
      - 全期間 7 field (Quality 4 + Surface 3) には未 filter events を渡す

    `period` が allow-list (`7d` / `30d` / `90d` / `all`) 以外、または `all` のときは
    events を index 同値で返す (parse 不能 timestamp も保持)。

    period ∈ {7d, 30d, 90d} のときは二段 filter:

      第一段 (rolling window): `cutoff <= ts <= now` の event を保持。
         timestamp 不在 / 不正 / `ts > now` (clock skew) → drop。
         `cutoff = now - timedelta(days=N)`。

      第二段 (canonical invocation/pair propagation): 各 (session_id, subagent_type)
         バケットの invocation/stop pair を `subagent_metrics._bucket_events`
         + `_build_invocations` + `_pair_invocations_with_stops` に **委譲** して
         構築する (= canonical 経路と同一の同定 / pairing semantic を再利用)。
         pair (= invocation の全 event + paired stop) のうち 1 event でも第一段で
         kept なら、pair 内全 event を再 include する。
         これにより `failure_rate` / `avg_duration_ms` / pXX duration が period
         boundary 跨ぎで silent drift しないことを構造的に保証する。
         clock skew で `ts > now` と落ちた stop は第一段の排除を尊重し、
         pair 一括 include の対象から除外する (codex round 2 / Issue #85)。
         canonical 側の `(session_id, subagent_id) min(timestamp)` dedup
         (Issue #100) も同経路で適用される — dup stops は同 session 内で
         timestamp が clustered なので period 境界跨ぎ問題は起きず、stage 1 で
         個別に kept される (実害ゼロ)。
    """
    if now is None:
        now = datetime.now(timezone.utc)
    days = _PERIOD_DELTAS.get(period)
    if days is None:
        return list(events)
    cutoff = now - timedelta(days=days)

    parsed_idx: dict[int, datetime] = {}
    for i, ev in enumerate(events):
        ts = _parse_iso_utc(ev.get("timestamp", ""))
        if ts is not None:
            parsed_idx[i] = ts

    kept: set[int] = set()
    for i, ts in parsed_idx.items():
        if cutoff <= ts <= now:
            kept.add(i)

    # 第二段: canonical helper に invocation 同定 + pair-with-stop を委譲。
    # 旧実装は手で mirror していたが Issue #85 / #100 で sync drift リスクが
    # 顕在化したため canonical 直 import に reroute (= mirror 撤去)。
    # `id(ev)` で events list 内の index に逆引きする (events は live 中に
    # GC されないので id は stable)。
    # Stage 1 で drop された bad-ts event は canonical の pairing input から
    # 除外する: 含めると `_pair_invocations_with_stops` が `ts is None` を
    # 「window 制約 skip」と解釈し、bad-ts invocation が valid stop を誤って
    # 消費して boundary-crossing invocation の pull-back を奪う
    # (regression は test_bad_ts_subagent_event_does_not_consume_pair_stop_for_valid_invocation
    # で pin)。
    idx_by_id = {id(ev): i for i, ev in enumerate(events)}
    parsed_events = [events[i] for i in sorted(parsed_idx)]
    starts_by_key, stops_by_key, lifecycle_by_key = _bucket_events(parsed_events)
    for key in set(starts_by_key) | set(lifecycle_by_key):
        starts_sorted = sorted(starts_by_key.get(key, []), key=lambda e: e.get("timestamp", ""))
        lifecycle_sorted = sorted(lifecycle_by_key.get(key, []), key=lambda e: e.get("timestamp", ""))
        stops_sorted = sorted(stops_by_key.get(key, []), key=lambda e: e.get("timestamp", ""))
        invocations = _build_invocations(starts_sorted, lifecycle_sorted)
        for inv, paired_stop in _pair_invocations_with_stops(invocations, stops_sorted):
            members: list[int] = []
            for source_ev in (inv.get("start"), inv.get("lifecycle")):
                if source_ev is None:
                    continue
                idx = idx_by_id.get(id(source_ev))
                if idx is None:
                    continue
                members.append(idx)
            if paired_stop is not None:
                stop_idx = idx_by_id.get(id(paired_stop))
                if stop_idx is not None and parsed_idx[stop_idx] <= now:
                    members.append(stop_idx)
            if any(j in kept for j in members):
                for j in members:
                    kept.add(j)

    return [ev for i, ev in enumerate(events) if i in kept]


def _filter_usage_events(
    events: list[dict],
    *,
    period_cutoff: Optional[datetime] = None,
) -> list[dict]:
    """headline 集計用に usage 系イベントを返す。subagent は invocation 単位 dedup。

    `subagent_start` (PostToolUse 由来) と `subagent_lifecycle_start` (SubagentStart 由来) は
    通常ペアで発火し、PostToolUse が flaky / 不在な環境では lifecycle のみが届く。
    `usage_invocation_events()` で `aggregate_subagent_metrics()` と同じ invocation 同定を行い、
    各 invocation の代表イベント 1 件だけを採用することで、subagent_ranking と
    total_events / daily_trend / project_breakdown を必ず一致させる。

    period_cutoff: 指定時、rep event の timestamp が cutoff より過去 (= 第二段で
    pull-back された boundary-straddling lifecycle-only invocation の rep) の場合、
    headline metrics (`daily_trend` / `hourly_heatmap` / `project_breakdown`) に
    pre-cutoff 日付が leak しないよう同 invocation の paired stop の timestamp で
    上書きした synthetic rep を返す (codex round 4 / Issue #85)。
    `aggregate_subagent_metrics` 側は別経路で raw events を受けるため影響なし。
    """
    skill_events = [ev for ev in events if ev.get("event_type") in _SKILL_USAGE_EVENT_TYPES]
    rep_events = usage_invocation_events(events)
    if period_cutoff is None:
        return skill_events + rep_events

    # rep の timestamp が cutoff より過去なら同 (session_id, subagent_type) バケットの
    # 直後 stop の timestamp で synthesize し直す
    stops_by_key: dict[tuple[str, str], list[dict]] = {}
    for ev in events:
        if ev.get("event_type") != "subagent_stop":
            continue
        key = (ev.get("session_id", ""), ev.get("subagent_type", ""))
        stops_by_key.setdefault(key, []).append(ev)
    for stops in stops_by_key.values():
        stops.sort(key=lambda e: e.get("timestamp", ""))

    adjusted: list[dict] = []
    for rep in rep_events:
        rep_ts = _parse_iso_utc(rep.get("timestamp", ""))
        if rep_ts is None or rep_ts >= period_cutoff:
            adjusted.append(rep)
            continue
        # cutoff より過去 → 同バケットで rep.ts 以降の最初の stop を探す
        key = (rep.get("session_id", ""), rep.get("subagent_type", ""))
        stop_match: Optional[dict] = None
        for stop in stops_by_key.get(key, []):
            stop_ts = _parse_iso_utc(stop.get("timestamp", ""))
            if stop_ts is not None and stop_ts >= rep_ts and stop_ts >= period_cutoff:
                stop_match = stop
                break
        if stop_match is not None:
            # rep の timestamp のみ stop の timestamp で上書き (project / subagent_type 等は維持)
            adjusted.append({**rep, "timestamp": stop_match["timestamp"]})
        else:
            # paired stop が見つからない → そのまま (defensive: stage 2 で pull-back されたなら
            # 必ず paired stop が同バケットに居るはずだが、想定外データ形にも壊れない)
            adjusted.append(rep)
    return skill_events + adjusted


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_events() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    events = []
    for line in DATA_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def aggregate_skills(events: list[dict], top_n: int = TOP_N) -> list[dict]:
    counter: Counter = Counter()
    failure_counter: Counter = Counter()
    for ev in events:
        et = ev.get("event_type")
        if et in ("skill_tool", "user_slash_command"):
            key = ev.get("skill", "")
            if not key:
                continue
            counter[key] += 1
            if et == "skill_tool" and ev.get("success") is False:
                failure_counter[key] += 1
    items = []
    for name, count in counter.most_common(top_n):
        failure = failure_counter.get(name, 0)
        items.append({
            "name": name,
            "count": count,
            "failure_count": failure,
            "failure_rate": (failure / count) if count else 0.0,
        })
    return items


def aggregate_subagents(events: list[dict], top_n: int = TOP_N) -> list[dict]:
    metrics = aggregate_subagent_metrics(events)
    ranked = sorted(metrics.items(), key=lambda kv: -kv[1]["count"])[:top_n]
    return [{"name": name, **m} for name, m in ranked]


def aggregate_daily(events: list[dict]) -> list[dict]:
    """events を UTC 日付で daily bucket に集計する。

    NOTE (Issue #65): dashboard frontend は local TZ で再集計するため、本関数の
    戻り値 (= /api/data の `daily_trend` field) を **直接は使わない**。frontend は
    `hourly_heatmap.buckets` を `localDailyFromHourly()` (10_helpers.js) で
    rebucket して sparkline / KPI subtitle に出している。
    `daily_trend` field は /api/data の backward-compat のため残しているが、
    将来 consumer が無いことが確定したら削除候補。
    """
    counter: Counter = Counter()
    for ev in events:
        ts = ev.get("timestamp", "")
        if ts and len(ts) >= 10:
            date = ts[:10]
            counter[date] += 1
    return [{"date": date, "count": count} for date, count in sorted(counter.items(), reverse=True)]


def aggregate_projects(events: list[dict], top_n: int = TOP_N) -> list[dict]:
    counter: Counter = Counter()
    for ev in events:
        project = ev.get("project", "")
        if project:
            counter[project] += 1
    return [{"project": project, "count": count} for project, count in counter.most_common(top_n)]


def aggregate_skill_cooccurrence(events: list[dict], top_n: int = 100) -> list[dict]:
    """同一 session 内の skill pair を集計し top_n 件返す (Issue #59 / B1)。

    入力 events は **未 filter** (build_dashboard_data からは raw events を渡す)。
    内部で `skill_tool` / `user_slash_command` のみに絞り、subagent は除外
    (= aggregate_skills と同じ filter 慣習)。

    挙動:
      - session_id ごとに skill 名を unique 集合化 (空 session_id / 空 skill は skip)
      - 各 session の skill 集合に対して itertools.combinations で 2-pair 列挙
      - Counter で全 session 合算
      - count 降順 + pair lexicographic 昇順で **明示 sort**
        (`Counter.most_common` の暗黙順序 = insertion order 依存を避ける)
      - top_n で切る (default 100)

    出力 list[{"pair": [a, b], "count": N}]
      - pair は a <= b で正規化済み
      - count は session 数 (両 skill が両方登場した unique session_id 数)。
        同 session 内の重複呼び出しは 1 回扱い
    """
    sessions: dict[str, set[str]] = {}
    for ev in events:
        if ev.get("event_type") not in ("skill_tool", "user_slash_command"):
            continue
        session_id = ev.get("session_id", "")
        if not session_id:
            continue
        skill = ev.get("skill", "")
        if not skill:
            continue
        sessions.setdefault(session_id, set()).add(skill)

    pair_counter: Counter = Counter()
    for skill_set in sessions.values():
        for a, b in itertools.combinations(sorted(skill_set), 2):
            pair_counter[(a, b)] += 1

    ranked = sorted(pair_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    return [{"pair": [a, b], "count": count} for (a, b), count in ranked]


def aggregate_project_skill_matrix(
    events: list[dict],
    top_projects: int = TOP_N,
    top_skills: int = TOP_N,
) -> dict:
    """project × skill の dense 2D matrix を返す (Issue #59 / B2)。

    入力 events は **未 filter** (build_dashboard_data からは raw events を渡す)。
    内部で `skill_tool` / `user_slash_command` のみに絞る (subagent 除外)。

    挙動:
      - skill_tool / user_slash_command のみ対象、空 project / 空 skill は skip
      - project / skill それぞれ count 降順で top_projects / top_skills に切る
      - (project, skill) ペアの count を 2D dense matrix で組み立てる (cell 0 含む)
      - "other" 集約は採用しない (top 漏れは drop) が、covered_count / total_count
        を返してカバー率の可視化を可能にする
    """
    cell_counter: Counter = Counter()
    project_counter: Counter = Counter()
    skill_counter: Counter = Counter()
    total_count = 0
    for ev in events:
        if ev.get("event_type") not in ("skill_tool", "user_slash_command"):
            continue
        project = ev.get("project", "")
        skill = ev.get("skill", "")
        if not project or not skill:
            continue
        cell_counter[(project, skill)] += 1
        project_counter[project] += 1
        skill_counter[skill] += 1
        total_count += 1

    top_project_names = [name for name, _ in project_counter.most_common(top_projects)]
    top_skill_names = [name for name, _ in skill_counter.most_common(top_skills)]

    counts = [
        [cell_counter.get((project, skill), 0) for skill in top_skill_names]
        for project in top_project_names
    ]
    covered_count = sum(sum(row) for row in counts)
    return {
        "projects": top_project_names,
        "skills": top_skill_names,
        "counts": counts,
        "covered_count": covered_count,
        "total_count": total_count,
    }


def aggregate_hourly_heatmap(usage_events: list[dict]) -> dict:
    """usage 系 events を UTC hour bucket に集計する (Issue #58)。

    呼び出し慣習: `_filter_usage_events()` で usage 系のみ + subagent invocation
    dedup 済みの list を受け取る (= aggregate_daily / aggregate_projects と同じ)。

    server 側は UTC のまま hour-truncate して返し、browser 側で local TZ 変換 +
    (weekday, hour) bin する設計 (option 3 hour-bucketed UTC)。

    parse 失敗 (空文字 / not parseable / `timestamp` キー不在) は silent skip。
    """
    counter: Counter = Counter()
    for ev in usage_events:
        ts = ev.get("timestamp", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            # naive datetime は UTC として扱う (`subagent_metrics._week_start_iso`
            # と同じ policy)。`rescan_transcripts.py --append` 経由で過去 transcript
            # から再投入された event が naive のまま流れるケースで silent drop しない
            # (Issue #71)。
            dt = dt.replace(tzinfo=timezone.utc)
        hour_dt = dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        counter[hour_dt.isoformat()] += 1
    buckets = [
        {"hour_utc": hour_utc, "count": count}
        for hour_utc, count in sorted(counter.items())
    ]
    return {"timezone": "UTC", "buckets": buckets}


# Issue #61 / A2: permission notification を直前 skill_tool / subagent invocation に
# 帰属させる際の backward fallback 窓 (秒)。execution-window (interval-cover) で拾えない
# permission を「直前 30 秒以内に終了した invocation」に紐付けるためのもの。
# 値の根拠は Issue #61 本文。実機 orphan ratio (= attribution 失敗 / 全 permission)
# を見て fine-tune する (memory/friction_signals.md 参照)。
PERMISSION_LINK_WINDOW_SECONDS = 30


def _skill_event_interval(ev: dict) -> tuple[float, float]:
    """skill_tool event の execution interval `[end - duration, end]` を返す。

    skill_tool の timestamp は PostToolUse 発火時刻 = ツール終了時刻なので、
    `end_ts = ev.timestamp` / `start_ts = end_ts - duration_ms/1000`。
    duration_ms 不在 / 不正 timestamp は `start == end` (point interval) に倒す。

    subagent 側は `subagent_metrics.usage_invocation_intervals()` に委譲する
    (subagent_metrics.py の責務スコープ内)。skill_tool 側は本モジュール内に残置:
    skill_tool は subagent_metrics.py の責務スコープ外なので、共有化圧力 = reports
    /summary.py 等が同 algorithm を必要とする — が来たときに改めて移管先を判断する。
    """
    ts_str = ev.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(ts_str)
    except (TypeError, ValueError):
        return (0.0, 0.0)
    end = dt.timestamp()
    duration_ms = ev.get("duration_ms")
    if not isinstance(duration_ms, (int, float)) or duration_ms <= 0:
        return (end, end)
    return (end - float(duration_ms) / 1000.0, end)


def _attribute_permission(
    notif_ts: float,
    skill_candidates: list[tuple[float, float, dict]],
    subagent_candidates: list[tuple[float, float, dict]],
) -> tuple[str, dict] | None:
    """1 notification を skill / subagent 候補列から「直近 1 個」に帰属させる。

    `(start_ts, end_ts, ev)` の tuple 列を受け取り、execution interval covers
    (`start_ts <= notif_ts <= end_ts`) 優先 / なければ backward window
    (`end_ts <= notif_ts <= end_ts + PERMISSION_LINK_WINDOW_SECONDS`) で候補化。
    skill / subagent をまとめて評価し、最も直近 (covers なら start_ts 最大、
    after なら end_ts 最大) の 1 個に帰属させる。どちらの window にも入らなければ
    `None` を返す (= orphan permission)。

    返り値: `("skill" | "subagent", ev)` または `None`。
    """
    covers: list[tuple[float, str, dict]] = []
    afters: list[tuple[float, str, dict]] = []
    for kind, candidates in (("skill", skill_candidates), ("subagent", subagent_candidates)):
        for start_ts, end_ts, ev in candidates:
            if start_ts <= notif_ts <= end_ts:
                covers.append((start_ts, kind, ev))
            elif end_ts <= notif_ts <= end_ts + PERMISSION_LINK_WINDOW_SECONDS:
                afters.append((end_ts, kind, ev))
    if covers:
        # covers: 直近 = start_ts が最大 (= notification 直前に始まった interval)
        covers.sort(key=lambda x: x[0], reverse=True)
        _, kind, ev = covers[0]
        return (kind, ev)
    if afters:
        afters.sort(key=lambda x: x[0], reverse=True)
        _, kind, ev = afters[0]
        return (kind, ev)
    return None


def aggregate_permission_breakdowns(events: list[dict], top_n: int = TOP_N) -> dict:
    """notification(permission) を直前 skill_tool / subagent invocation に帰属させる。

    algorithm: execution-window (interval-cover) 優先 + backward fallback の 2 段階。
    long-running subagent の途中で発火した permission を構造的に取りこぼさないため
    interval-cover を併用する。1 notification は **skill OR subagent の 1 候補のみ**
    に帰属 (= skill table と subagent table の prompt_count は disjoint で合算可能)。

    user_slash_command は対象外 (Issue 本文「`skill_tool` / `subagent_start`」明記;
    slash command はモデル発話の prefix で tool 実行を伴わず permission の起因に
    ならない)。subagent invocation の interval 解釈は
    `subagent_metrics.usage_invocation_intervals()` に委譲する (paired stop の
    duration_ms を fallback に使うことで lifecycle-only invocation の interval
    縮退を防ぐ)。

    sort: prompt_count 降順 → 同点は name 昇順。`prompt_count == 0` は出力から除外
    (top-N は prompt_count 降順で切る慣習)。`permission_rate > 1.0` は normal な
    状態 (1 invocation で複数 permission) なので clamp しない。

    返り値: `{"skill": [...], "subagent": [...]}` 各要素は
        skill: `{"skill": str, "prompt_count": int, "invocation_count": int, "permission_rate": float}`
        subagent: `{"subagent_type": str, "prompt_count": int, "invocation_count": int, "permission_rate": float}`
    """
    # session 単位で skill_tool / subagent invocation / notification を集める
    skill_by_session: dict[str, list[dict]] = {}
    notif_by_session: dict[str, list[dict]] = {}
    for ev in events:
        et = ev.get("event_type")
        session = ev.get("session_id", "")
        if not session:
            continue
        if et == "skill_tool":
            skill_by_session.setdefault(session, []).append(ev)
        elif et == "notification" and ev.get("notification_type") in _PERMISSION_NOTIFICATION_TYPES:
            notif_by_session.setdefault(session, []).append(ev)

    # subagent invocation は usage_invocation_intervals で dedup 済み 1 invocation 1 interval を取る。
    # paired stop の duration_ms を fallback に使うため lifecycle-only invocation
    # (`record_subagent.py` は subagent_lifecycle_start に duration_ms を書かない)
    # でも interval が point に縮退せず長時間 invocation 中の permission を拾える。
    subagent_intervals_by_session: dict[str, list[tuple[float, float, dict]]] = {}
    for start_ts, end_ts, ev in usage_invocation_intervals(events):
        session = ev.get("session_id", "")
        if not session:
            continue
        if start_ts == 0.0 and end_ts == 0.0:
            continue
        subagent_intervals_by_session.setdefault(session, []).append((start_ts, end_ts, ev))

    # invocation_count は metrics と一致させる (drift guard)
    subagent_metrics_by_name = aggregate_subagent_metrics(events)
    skill_invocation_count: Counter = Counter()
    for evs in skill_by_session.values():
        for ev in evs:
            name = ev.get("skill", "")
            if name:
                skill_invocation_count[name] += 1

    skill_prompt_count: Counter = Counter()
    subagent_prompt_count: Counter = Counter()
    for session in set(notif_by_session) | set(skill_by_session) | set(subagent_intervals_by_session):
        notifs = sorted(notif_by_session.get(session, []), key=lambda e: e.get("timestamp", ""))
        skill_candidates: list[tuple[float, float, dict]] = []
        for ev in skill_by_session.get(session, []):
            start_ts, end_ts = _skill_event_interval(ev)
            if start_ts == 0.0 and end_ts == 0.0:
                continue
            skill_candidates.append((start_ts, end_ts, ev))
        subagent_candidates: list[tuple[float, float, dict]] = list(
            subagent_intervals_by_session.get(session, [])
        )
        for notif in notifs:
            try:
                notif_ts = datetime.fromisoformat(notif.get("timestamp", "")).timestamp()
            except (TypeError, ValueError):
                continue
            attr = _attribute_permission(notif_ts, skill_candidates, subagent_candidates)
            if attr is None:
                continue
            kind, ev = attr
            if kind == "skill":
                name = ev.get("skill", "")
                if name:
                    skill_prompt_count[name] += 1
            else:
                name = ev.get("subagent_type", "")
                if name:
                    subagent_prompt_count[name] += 1

    skill_items = []
    for name, prompt in skill_prompt_count.items():
        inv = skill_invocation_count.get(name, 0)
        if inv == 0:
            continue
        skill_items.append({
            "skill": name,
            "prompt_count": prompt,
            "invocation_count": inv,
            "permission_rate": prompt / inv,
        })
    skill_items.sort(key=lambda r: (-r["prompt_count"], r["skill"]))
    skill_items = skill_items[:top_n]

    subagent_items = []
    for name, prompt in subagent_prompt_count.items():
        inv = subagent_metrics_by_name.get(name, {}).get("count", 0)
        if inv == 0:
            continue
        subagent_items.append({
            "subagent_type": name,
            "prompt_count": prompt,
            "invocation_count": inv,
            "permission_rate": prompt / inv,
        })
    subagent_items.sort(key=lambda r: (-r["prompt_count"], r["subagent_type"]))
    subagent_items = subagent_items[:top_n]

    return {"skill": skill_items, "subagent": subagent_items}


# Issue #74 / Surface 3 panel — 共通定数

TOP_N_SKILL_INVOCATION = 20
TOP_N_SKILL_LIFECYCLE = 20

_LIFECYCLE_NEW_THRESHOLD_DAYS = 14
_HIBERNATING_ACTIVE_DAYS = 14
_HIBERNATING_RESTING_DAYS = 30


def _normalize_skill_name(raw: str) -> str:
    """skill_tool / user_slash_command の skill 名表記差を吸収する。

    user_slash_command は先頭 `/` を含む形 (`"/codex-review"`)、skill_tool は
    含まない形 (`"codex-review"`) で記録される。`lstrip("/")` で先頭の slash
    を全て剥がし、`~/.claude/skills/<name>/` のディレクトリ名と同じカノニカル
    表現に揃える。空文字 / `"/"` 単独 / whitespace-only は呼び出し側で skip 判定。
    """
    if not isinstance(raw, str):
        return ""
    return raw.lstrip("/").strip()


def _parse_iso_utc(ts: str) -> Optional[datetime]:
    """ISO 8601 → tz-aware UTC datetime。失敗時 None。

    naive datetime (tzinfo 無し) は UTC とみなす (`hourly_heatmap` と同方針)。
    """
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def aggregate_skill_invocation_breakdown(
    events: list[dict],
    top_n: int = TOP_N_SKILL_INVOCATION,
) -> list[dict]:
    """skill_tool (LLM 自律) / user_slash_command (ユーザー手動) を skill ごとに集計。

    Mode は 3-way: dual / llm-only / user-only。autonomy_rate は dual のみ意味があり
    (round 4 桁丸め)、片側 only は None。skill 名は `_normalize_skill_name()` で
    先頭 `/` を全部剥がして同一 key にマージ。失敗 event (success=False) も count に含む。
    """
    tool_count: Counter = Counter()
    slash_count: Counter = Counter()
    for ev in events:
        et = ev.get("event_type")
        if et not in ("skill_tool", "user_slash_command"):
            continue
        skill = _normalize_skill_name(ev.get("skill", ""))
        if not skill:
            continue
        if et == "skill_tool":
            tool_count[skill] += 1
        else:
            slash_count[skill] += 1

    rows = []
    for skill in set(tool_count) | set(slash_count):
        t = tool_count.get(skill, 0)
        s = slash_count.get(skill, 0)
        if t > 0 and s > 0:
            mode = "dual"
            autonomy_rate: Optional[float] = round(t / (t + s), 4)
        elif t > 0:
            mode = "llm-only"
            autonomy_rate = None
        else:
            mode = "user-only"
            autonomy_rate = None
        rows.append({
            "skill": skill,
            "mode": mode,
            "tool_count": t,
            "slash_count": s,
            "autonomy_rate": autonomy_rate,
        })
    rows.sort(key=lambda r: (-(r["tool_count"] + r["slash_count"]), r["skill"]))
    return rows[:top_n]


def aggregate_skill_lifecycle(
    events: list[dict],
    *,
    now: Optional[datetime] = None,
    top_n: int = TOP_N_SKILL_LIFECYCLE,
) -> list[dict]:
    """skill_tool + user_slash_command を skill 名正規化済みで merge し lifecycle を算出。

    trend 4 値: new (first_seen が 14 日以内なら最優先) / accelerating / stable /
    decelerating。observation_days に上限 cap は無い (Q2)。
    sort: last_seen desc → skill asc の 2-pass stable sort。
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff_30d = now - timedelta(days=30)

    by_skill: dict[str, dict] = {}
    for ev in events:
        et = ev.get("event_type")
        if et not in ("skill_tool", "user_slash_command"):
            continue
        skill = _normalize_skill_name(ev.get("skill", ""))
        if not skill:
            continue
        ts = _parse_iso_utc(ev.get("timestamp", ""))
        if ts is None:
            continue
        slot = by_skill.get(skill)
        if slot is None:
            slot = {"first": ts, "last": ts, "count_total": 0, "count_30d": 0}
            by_skill[skill] = slot
        if ts < slot["first"]:
            slot["first"] = ts
        if ts > slot["last"]:
            slot["last"] = ts
        slot["count_total"] += 1
        if cutoff_30d <= ts <= now:
            slot["count_30d"] += 1

    rows = []
    for skill, slot in by_skill.items():
        days_since_first = (now - slot["first"]).days
        if days_since_first < _LIFECYCLE_NEW_THRESHOLD_DAYS:
            trend = "new"
        else:
            observation_days = max(days_since_first, 1)
            recent_rate = slot["count_30d"] / 30
            overall_rate = slot["count_total"] / observation_days
            if overall_rate == 0:
                trend = "stable"
            else:
                ratio = recent_rate / overall_rate
                if ratio > 1.5:
                    trend = "accelerating"
                elif ratio < 0.5:
                    trend = "decelerating"
                else:
                    trend = "stable"
        rows.append({
            "skill": skill,
            "first_seen": slot["first"].isoformat(),
            "last_seen": slot["last"].isoformat(),
            "count_30d": slot["count_30d"],
            "count_total": slot["count_total"],
            "trend": trend,
        })
    # 2-pass stable sort: skill asc が tiebreaker、last_seen desc が一次キー
    rows.sort(key=lambda r: r["skill"])
    rows.sort(key=lambda r: r["last_seen"], reverse=True)
    return rows[:top_n]


def _resolve_skills_dir(skills_dir) -> Path:
    """skills_dir 引数 / SKILLS_DIR env / 既定 ~/.claude/skills の resolution。"""
    if skills_dir is not None:
        return Path(skills_dir).expanduser()
    env = os.environ.get("SKILLS_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude" / "skills"


def aggregate_skill_hibernating(
    events: list[dict],
    *,
    skills_dir=None,
    now: Optional[datetime] = None,
) -> dict:
    """user-level skills (~/.claude/skills/*/SKILL.md) と usage を cross-reference。

    14 日以内に呼ばれた skill は items から除外し active_excluded_count に計上。
    plugin-bundled skills (~/.claude/plugins/*/skills/) は対象外。
    skills_dir 不在 / 各 entry の OSError は silent skip。
    """
    if now is None:
        now = datetime.now(timezone.utc)
    skills_dir = _resolve_skills_dir(skills_dir)

    # skills_dir 不在 / アクセス不可 → empty + scope_note
    try:
        entries = list(skills_dir.iterdir()) if skills_dir.is_dir() else []
    except OSError:
        entries = []

    installed: dict[str, datetime] = {}
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            mtime = datetime.fromtimestamp(skill_md.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        installed[entry.name] = mtime

    # usage 側 last_seen (skill 名正規化済みで installed と key 一致)
    last_seen_by_skill: dict[str, datetime] = {}
    for ev in events:
        et = ev.get("event_type")
        if et not in ("skill_tool", "user_slash_command"):
            continue
        skill = _normalize_skill_name(ev.get("skill", ""))
        if not skill or skill not in installed:
            continue
        ts = _parse_iso_utc(ev.get("timestamp", ""))
        if ts is None:
            continue
        prev = last_seen_by_skill.get(skill)
        if prev is None or ts > prev:
            last_seen_by_skill[skill] = ts

    active_cutoff = now - timedelta(days=_HIBERNATING_ACTIVE_DAYS)

    items = []
    active_excluded_count = 0
    for skill, mtime in installed.items():
        last = last_seen_by_skill.get(skill)
        if last is not None and last >= active_cutoff:
            active_excluded_count += 1
            continue
        days_since_use = (now - last).days if last is not None else None
        days_since_install = (now - mtime).days

        if last is not None:
            status = "resting" if days_since_use <= _HIBERNATING_RESTING_DAYS else "idle"
        else:
            status = "warming_up" if days_since_install <= _HIBERNATING_ACTIVE_DAYS else "idle"

        items.append({
            "skill": skill,
            "status": status,
            "mtime": mtime.isoformat(),
            "last_seen": last.isoformat() if last is not None else None,
            "days_since_last_use": days_since_use,
            # sort 用 sidecar (return 直前に pop)
            "_mtime_dt": mtime,
            "_d_inst": days_since_install,
        })

    status_order = {"warming_up": 0, "resting": 1, "idle": 2}

    def _tiebreak(it: dict) -> tuple:
        if it["status"] == "warming_up":
            return (-it["_mtime_dt"].timestamp(),)
        if it["status"] == "resting":
            return (-(it["days_since_last_use"] or 0),)
        # idle
        d_use = it["days_since_last_use"] or 0
        return (-max(d_use, it["_d_inst"]),)

    items.sort(key=lambda it: (status_order[it["status"]], *_tiebreak(it), it["skill"]))

    for it in items:
        it.pop("_mtime_dt", None)
        it.pop("_d_inst", None)

    return {
        "items": items,
        "scope_note": "user-level only",
        "active_excluded_count": active_excluded_count,
    }


def aggregate_compact_density(events: list[dict], top_n: int = TOP_N) -> dict:
    """session 単位 compact_start を集計し histogram (0/1/2/3+) と worst_sessions を返す。

    histogram bucket:
      - "0": session_start を持つ session のうち compact_start 0 件
      - "1" / "2": 同 1 件 / 2 件
      - "3+": 同 3 件以上 (3 / 4 / 5 ... すべて同じ bucket)

    session pool は **session_start event の session_id 集合**。compact_start のみで
    session_start が無い orphan session_id は histogram から除外 (= 0 bucket への
    混入を避ける) するが worst_sessions には載せる。histogram の 0 bucket 分母を
    「実観測 session 数」に揃えるため。

    worst_sessions: 全 compact_start を session_id で groupby し count 降順で top_n。
    同点は session_id 昇順。各要素 `{"session_id": str, "count": int, "project": str}`。
    project は当該 session の **最後に観測した** compact_start.project (空なら "")。

    返り値:
      `{"histogram": {"0": int, "1": int, "2": int, "3+": int},
        "worst_sessions": [{"session_id": str, "count": int, "project": str}]}`
    """
    session_pool: set[str] = set()
    compact_count_by_session: Counter = Counter()
    last_project_by_session: dict[str, str] = {}
    for ev in events:
        et = ev.get("event_type")
        session = ev.get("session_id", "")
        if not session:
            continue
        if et == "session_start":
            session_pool.add(session)
        elif et == "compact_start":
            compact_count_by_session[session] += 1
            project = ev.get("project", "")
            last_project_by_session[session] = project

    histogram = {"0": 0, "1": 0, "2": 0, "3+": 0}
    for session in session_pool:
        count = compact_count_by_session.get(session, 0)
        if count == 0:
            histogram["0"] += 1
        elif count == 1:
            histogram["1"] += 1
        elif count == 2:
            histogram["2"] += 1
        else:
            histogram["3+"] += 1

    worst_sessions = [
        {
            "session_id": session,
            "count": count,
            "project": last_project_by_session.get(session, ""),
        }
        for session, count in compact_count_by_session.items()
    ]
    worst_sessions.sort(key=lambda r: (-r["count"], r["session_id"]))
    worst_sessions = worst_sessions[:top_n]

    return {"histogram": histogram, "worst_sessions": worst_sessions}


def aggregate_session_stats(events: list[dict]) -> dict:
    total_sessions = 0
    resume_count = 0
    compact_count = 0
    permission_prompt_count = 0
    for ev in events:
        et = ev.get("event_type")
        if et == "session_start":
            total_sessions += 1
            if ev.get("source") == "resume":
                resume_count += 1
        elif et == "compact_start":
            compact_count += 1
        elif et == "notification" and ev.get("notification_type") in _PERMISSION_NOTIFICATION_TYPES:
            permission_prompt_count += 1
    resume_rate = (resume_count / total_sessions) if total_sessions else 0.0
    return {
        "total_sessions": total_sessions,
        "resume_count": resume_count,
        "resume_rate": resume_rate,
        "compact_count": compact_count,
        "permission_prompt_count": permission_prompt_count,
    }


_MAX_ALERTS = 50


def load_health_alerts() -> list[dict]:
    if not ALERTS_FILE.exists():
        return []
    alerts = []
    for line in ALERTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            alerts.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return alerts[-_MAX_ALERTS:]
