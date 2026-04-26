"""subagent_metrics.py — subagent イベントを invocation 単位で集計する共通ロジック。

dashboard/server.py と reports/summary.py の両方から利用される。
"""
from collections import Counter
from datetime import datetime

# subagent_start (PostToolUse 由来) と subagent_lifecycle_start (SubagentStart 由来) を
# 同一 invocation とみなすための時間ウィンドウ。Claude Code は両 hook をほぼ同時に発火するため
# 1 秒以内なら同一 invocation の両ソース発火とみなす。これより離れた場合は別 invocation 扱い。
INVOCATION_MERGE_WINDOW_SECONDS = 1.0


def _parse_ts(ts_str: str):
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except ValueError:
        return None


def _build_invocations(starts_sorted: list[dict], lifecycle_sorted: list[dict]) -> list[dict]:
    """starts と lifecycle を timestamp 順にマージし、`INVOCATION_MERGE_WINDOW_SECONDS` 以内かつ
    同じ source が未割当の隣接ペアを 1 invocation として束ねる。

    各 invocation は `{"start": ev?, "lifecycle": ev?}` を返す（どちらか必ず存在）。
    """
    tagged: list[tuple[str, dict]] = (
        [("start", e) for e in starts_sorted]
        + [("lifecycle", e) for e in lifecycle_sorted]
    )
    tagged.sort(key=lambda x: x[1].get("timestamp", ""))

    invocations: list[dict] = []
    last_ts = None
    for source, ev in tagged:
        ts = _parse_ts(ev.get("timestamp", ""))
        merged = False
        if invocations and ts is not None and last_ts is not None:
            last_inv = invocations[-1]
            if source not in last_inv and abs((ts - last_ts).total_seconds()) <= INVOCATION_MERGE_WINDOW_SECONDS:
                last_inv[source] = ev
                merged = True
        if not merged:
            invocations.append({source: ev})
        if ts is not None:
            last_ts = ts
    return invocations


def usage_invocation_events(events: list[dict]) -> list[dict]:
    """各 subagent invocation の代表イベントを 1 件ずつ返す。

    `aggregate_subagent_metrics()` と同じ invocation 同定ロジック
    （`(session_id, subagent_type)` バケット × `INVOCATION_MERGE_WINDOW_SECONDS` マージ）
    を使い、各 invocation について `subagent_start` があればそれを、無ければ
    `subagent_lifecycle_start` を採用する。元イベントの timestamp / project / subagent_type を
    そのまま保持するので、daily_trend / project_breakdown 集計の入力に使える。

    用途: dashboard / summary 側で headline メトリクス (total_events / daily_trend /
    project_breakdown) を集計するときに、aggregate_subagent_metrics の count と
    必ず一致する dedup 済みイベント列を得るためのヘルパー。
    """
    starts_by_key: dict = {}
    lifecycle_by_key: dict = {}
    for ev in events:
        et = ev.get("event_type")
        name = ev.get("subagent_type", "")
        if not name:
            continue
        session = ev.get("session_id", "")
        key = (session, name)
        if et == "subagent_start":
            starts_by_key.setdefault(key, []).append(ev)
        elif et == "subagent_lifecycle_start":
            lifecycle_by_key.setdefault(key, []).append(ev)

    result: list[dict] = []
    for key in set(starts_by_key) | set(lifecycle_by_key):
        starts_sorted = sorted(starts_by_key.get(key, []), key=lambda e: e.get("timestamp", ""))
        lifecycle_sorted = sorted(lifecycle_by_key.get(key, []), key=lambda e: e.get("timestamp", ""))
        for inv in _build_invocations(starts_sorted, lifecycle_sorted):
            result.append(inv.get("start") or inv["lifecycle"])
    return result


def aggregate_subagent_metrics(events: list[dict]) -> dict[str, dict]:
    """subagent イベント列を invocation 単位でペアリングし、集計メトリクスを返す。

    invocation の同定:
      `(session_id, subagent_type)` でグルーピングし、各バケット内で subagent_start (PostToolUse 由来)
      と subagent_lifecycle_start (SubagentStart 由来) を timestamp 順にマージ。
      `INVOCATION_MERGE_WINDOW_SECONDS` 以内に発火した「異なる source」は同一 invocation の重複扱い、
      それ以上離れていれば別 invocation。これにより両 hook 並列発火・lifecycle のみ・PostToolUse のみ・
      flaky の混在いずれも正しく invocation 数を出せる。
      tool_use_id ↔ agent_id の直接紐付け手段が無いための時系列近似。

    failure 判定:
      各 invocation について `start.success=False OR stop.success=False` のとき 1 failure。
      starts と stops の件数が一致しない場合は「起動失敗で stop が来ない」シナリオとみなし
      stop プールを消費しないペアリングに切り替える。

    Duration:
      invocation ごとに `stop.duration_ms` (end-to-end) を優先、無ければ `start.duration_ms`
      (起動オーバーヘッド) を fallback。type 単位の or fallback はバイアスを生むため不採用。

    返却フォーマット:
      {name: {"count": int, "failure_count": int, "failure_rate": float, "avg_duration_ms": float|None}}
    """
    starts_by_key: dict = {}
    stops_by_key: dict = {}
    lifecycle_by_key: dict = {}
    for ev in events:
        et = ev.get("event_type")
        name = ev.get("subagent_type", "")
        if not name:
            continue
        session = ev.get("session_id", "")
        key = (session, name)
        if et == "subagent_start":
            starts_by_key.setdefault(key, []).append(ev)
        elif et == "subagent_stop":
            stops_by_key.setdefault(key, []).append(ev)
        elif et == "subagent_lifecycle_start":
            lifecycle_by_key.setdefault(key, []).append(ev)

    type_count: Counter = Counter()
    failure_counter: Counter = Counter()
    invocation_durations: dict[str, list[float]] = {}
    all_keys = set(starts_by_key) | set(lifecycle_by_key)
    for key in all_keys:
        _, name = key
        starts_sorted = sorted(starts_by_key.get(key, []), key=lambda e: e.get("timestamp", ""))
        lifecycle_sorted = sorted(lifecycle_by_key.get(key, []), key=lambda e: e.get("timestamp", ""))
        stops_sorted = sorted(stops_by_key.get(key, []), key=lambda e: e.get("timestamp", ""))

        invocations = _build_invocations(starts_sorted, lifecycle_sorted)
        n_invocations = len(invocations)
        type_count[name] += n_invocations

        paired_stops = n_invocations == len(stops_sorted)
        stop_idx = 0
        for inv in invocations:
            start = inv.get("start")
            start_failed = bool(start) and start.get("success") is False
            stop: dict | None = None
            if start_failed and not paired_stops:
                failure_counter[name] += 1
            else:
                stop = stops_sorted[stop_idx] if stop_idx < len(stops_sorted) else None
                if stop is not None:
                    stop_idx += 1
                if stop is None:
                    if start_failed:
                        failure_counter[name] += 1
                elif start_failed or stop.get("success") is False:
                    failure_counter[name] += 1
            inv_duration = None
            if stop is not None:
                d = stop.get("duration_ms")
                if isinstance(d, (int, float)):
                    inv_duration = float(d)
            if inv_duration is None and start is not None:
                d = start.get("duration_ms")
                if isinstance(d, (int, float)):
                    inv_duration = float(d)
            if inv_duration is not None:
                invocation_durations.setdefault(name, []).append(inv_duration)
        for stop in stops_sorted[stop_idx:]:
            d = stop.get("duration_ms")
            if isinstance(d, (int, float)):
                invocation_durations.setdefault(name, []).append(float(d))

    metrics: dict[str, dict] = {}
    for name, count in type_count.items():
        failure = failure_counter.get(name, 0)
        durations = invocation_durations.get(name, [])
        avg_duration = (sum(durations) / len(durations)) if durations else None
        metrics[name] = {
            "count": count,
            "failure_count": failure,
            "failure_rate": (failure / count) if count else 0.0,
            "avg_duration_ms": avg_duration,
        }
    return metrics
