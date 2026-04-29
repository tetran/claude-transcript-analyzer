"""subagent_metrics.py — subagent イベントを invocation 単位で集計する共通ロジック。

dashboard/server.py と reports/summary.py の両方から利用される。
"""
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone

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


def _bucket_events(events: list[dict]) -> tuple[dict, dict, dict]:
    """events を `(session_id, subagent_type)` キーで starts / stops / lifecycle に振り分け。"""
    starts_by_key: dict = {}
    stops_by_key: dict = {}
    lifecycle_by_key: dict = {}
    for ev in events:
        et = ev.get("event_type")
        name = ev.get("subagent_type", "")
        if not name:
            continue
        key = (ev.get("session_id", ""), name)
        if et == "subagent_start":
            starts_by_key.setdefault(key, []).append(ev)
        elif et == "subagent_stop":
            stops_by_key.setdefault(key, []).append(ev)
        elif et == "subagent_lifecycle_start":
            lifecycle_by_key.setdefault(key, []).append(ev)
    return starts_by_key, stops_by_key, lifecycle_by_key


def _invocation_duration(start: dict | None, stop: dict | None) -> float | None:
    """invocation 1 件の duration: stop.duration_ms 優先、無ければ start.duration_ms。"""
    for ev in (stop, start):
        if ev is None:
            continue
        d = ev.get("duration_ms")
        if isinstance(d, (int, float)):
            return float(d)
    return None


def _process_bucket(
    invocations: list[dict],
    stops_sorted: list[dict],
) -> tuple[int, list[float]]:
    """1 バケット (session×type) の invocation 群を処理し (failure_count, durations) を返す。"""
    failures = 0
    durations: list[float] = []
    paired_stops = len(invocations) == len(stops_sorted)
    stop_idx = 0
    for inv in invocations:
        start = inv.get("start")
        start_failed = bool(start) and start.get("success") is False
        stop: dict | None = None
        if start_failed and not paired_stops:
            failures += 1
        else:
            stop = stops_sorted[stop_idx] if stop_idx < len(stops_sorted) else None
            if stop is not None:
                stop_idx += 1
            if stop is None and start_failed:
                failures += 1
            elif stop is not None and (start_failed or stop.get("success") is False):
                failures += 1
        inv_duration = _invocation_duration(start, stop)
        if inv_duration is not None:
            durations.append(inv_duration)
    # 余り stops (`stops_sorted[stop_idx:]`) は durations に積まない。
    # invocation 単位集計なので stop 単独イベントは duration sample にも failure にも
    # 寄与しない (= `_bucket_invocation_records` と対称)。これにより
    # `sample_count <= count` invariant が常に成立し、Issue #60 percentile の母集団が
    # invocation 単位 count と一致する (Codex Round 1 / P2 反映)。
    return failures, durations


def _ts_key(ev: dict) -> str:
    return ev.get("timestamp", "")


def _aggregate_bucket(
    key: tuple, starts_by_key: dict, stops_by_key: dict, lifecycle_by_key: dict
) -> tuple[str, int, int, list[float]]:
    """1 バケット分の (name, n_invocations, failures, durations) を返す。"""
    _, name = key
    invocations = _build_invocations(
        sorted(starts_by_key.get(key, []), key=_ts_key),
        sorted(lifecycle_by_key.get(key, []), key=_ts_key),
    )
    failures, durations = _process_bucket(
        invocations, sorted(stops_by_key.get(key, []), key=_ts_key)
    )
    return name, len(invocations), failures, durations


def _percentiles(durations: list[float]) -> tuple[float | None, float | None, float | None]:
    """duration list から (p50, p90, p99) を返す。

    - 空 → (None, None, None)
    - 1 件 → 全 percentile が data[0] (退化扱い)
    - 2 件以上 → `statistics.quantiles(n=100, method="inclusive")` で 99 cuts を取り
      index 49/89/98 を採用

    `method="inclusive"` は **Excel `PERCENTILE.INC` 等価** (端点を含めた線形補間)。
    numpy の `method="linear"` (exclusive endpoints) とは別物なので「numpy default 等価」
    という言い方はしない (Issue #60 / P1)。
    """
    if not durations:
        return (None, None, None)
    if len(durations) == 1:
        v = durations[0]
        return (v, v, v)
    cuts = statistics.quantiles(durations, n=100, method="inclusive")
    return (cuts[49], cuts[89], cuts[98])


def _build_metrics(
    type_count: Counter,
    failure_counter: Counter,
    invocation_durations: dict[str, list[float]],
) -> dict[str, dict]:
    """集計済みカウンタから返却用 metrics dict を組み立てる。"""
    metrics: dict[str, dict] = {}
    for name, count in type_count.items():
        failure = failure_counter.get(name, 0)
        durations = invocation_durations.get(name, [])
        p50, p90, p99 = _percentiles(durations)
        metrics[name] = {
            "count": count,
            "failure_count": failure,
            "failure_rate": (failure / count) if count else 0.0,
            "avg_duration_ms": (sum(durations) / len(durations)) if durations else None,
            # ── Issue #60 / A5: percentile + sample_count (additive) ──
            "p50_duration_ms": p50,
            "p90_duration_ms": p90,
            "p99_duration_ms": p99,
            "sample_count": len(durations),
        }
    return metrics


def _bucket_invocation_records(
    invocations: list[dict], stops_sorted: list[dict], name: str
) -> list[dict]:
    """1 バケット (session×type) の invocation 単位 [(timestamp, name, failed)] を返す。

    ペアリング戦略は `_process_bucket` と同じ:
      - starts と stops の件数が一致 → 1:1 ペアリング (同 invocation の重複発火扱い)
      - 件数不一致 → start.success=False は「起動失敗で stop なし」とみなし stop プールを
        進めず failed=True

    余り stops (`stops_sorted[len(invocations):]`) は **record 化しない**。
    invocation 単位集計なので stop 単独イベントは trend に寄与しないのが正解。
    `_process_bucket` 側も余り stop の failure はカウントしないため、両者の failure_count は
    **構造的に一致** する (Issue #60 / Q1 drift guard / 2-P1)。
    """
    paired_stops = len(invocations) == len(stops_sorted)
    stop_idx = 0
    records: list[dict] = []
    for inv in invocations:
        start = inv.get("start")
        lifecycle = inv.get("lifecycle")
        rep = start or lifecycle
        ts = rep.get("timestamp", "") if rep else ""
        start_failed = bool(start) and start.get("success") is False
        if start_failed and not paired_stops:
            failed = True
        else:
            stop = stops_sorted[stop_idx] if stop_idx < len(stops_sorted) else None
            if stop is not None:
                stop_idx += 1
            stop_failed = bool(stop) and stop.get("success") is False
            failed = start_failed or stop_failed
        records.append({"timestamp": ts, "subagent_type": name, "failed": failed})
    return records


def invocation_records(events: list[dict]) -> list[dict]:
    """各 invocation を `{"timestamp": str, "subagent_type": str, "failed": bool}` で返す。

    `aggregate_subagent_metrics` と同じ invocation 同定 (`_bucket_events` +
    `_build_invocations` + start↔stop pairing) を使い、各 invocation の
    `failed` flag (start.success=False OR stop.success=False) を計算する。
    timestamp は invocation の代表時刻 = `start.timestamp` 優先 / 無ければ `lifecycle.timestamp`。

    用途: 週次 trend (`aggregate_subagent_failure_trend`) の入力など、invocation 単位
    時系列が必要な集計のための共通 helper (Issue #60 / B3)。
    """
    starts_by_key, stops_by_key, lifecycle_by_key = _bucket_events(events)
    result: list[dict] = []
    for key in set(starts_by_key) | set(lifecycle_by_key):
        _, name = key
        starts_sorted = sorted(starts_by_key.get(key, []), key=_ts_key)
        lifecycle_sorted = sorted(lifecycle_by_key.get(key, []), key=_ts_key)
        invocations = _build_invocations(starts_sorted, lifecycle_sorted)
        stops_sorted = sorted(stops_by_key.get(key, []), key=_ts_key)
        result.extend(_bucket_invocation_records(invocations, stops_sorted, name))
    return result


def _week_start_iso(timestamp: str) -> str | None:
    """ISO timestamp string → monday-UTC week_start ISO date string ("YYYY-MM-DD")。

    naive datetime は UTC として扱う safety belt (Issue #60 / P3): `usage.jsonl` は通常
    `+00:00` 付き ISO だが、Stop hook 経由 `_merge_stop_hook_list` や
    `rescan_transcripts.py --append` 由来で naive ISO が紛れた場合に local TZ shift を
    structurally 塞ぐ。Python 3.11+ では naive `astimezone()` が local TZ 解釈で silent
    shift する非対称があるため特に厳格に。
    """
    if not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    week_start = dt.date() - timedelta(days=dt.weekday())  # Mon=0..Sun=6
    return week_start.isoformat()


def aggregate_subagent_failure_trend(events: list[dict]) -> list[dict]:
    """subagent invocation を (monday-UTC week, subagent_type) で bucket して trend を返す。

    監視しているのは end-to-end 成功 (start.success=False OR stop.success=False を 1 failure)。
    sort: (week_start, subagent_type) lexicographic 昇順。
    **server は top-N で切らず観測された全 (week, subagent_type) を返す** (Issue #60 / P2)。
    UI 側の top-5 描画はあくまで affordance であり schema には現れない。
    naive datetime は UTC として扱う (Issue #60 / P3)。

    出力: list[{"week_start": "YYYY-MM-DD", "subagent_type": str,
               "count": int, "failure_count": int, "failure_rate": float}]
    """
    counts: Counter = Counter()
    failures: Counter = Counter()
    for rec in invocation_records(events):
        week = _week_start_iso(rec.get("timestamp", ""))
        if week is None:
            continue
        key = (week, rec["subagent_type"])
        counts[key] += 1
        if rec["failed"]:
            failures[key] += 1
    result = []
    for key in sorted(counts.keys()):
        c = counts[key]
        f = failures.get(key, 0)
        result.append({
            "week_start": key[0],
            "subagent_type": key[1],
            "count": c,
            "failure_count": f,
            "failure_rate": (f / c) if c else 0.0,
        })
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
    starts_by_key, stops_by_key, lifecycle_by_key = _bucket_events(events)

    type_count: Counter = Counter()
    failure_counter: Counter = Counter()
    invocation_durations: dict[str, list[float]] = {}
    for key in set(starts_by_key) | set(lifecycle_by_key):
        name, n_inv, failures, durations = _aggregate_bucket(
            key, starts_by_key, stops_by_key, lifecycle_by_key
        )
        type_count[name] += n_inv
        failure_counter[name] += failures
        if durations:
            invocation_durations.setdefault(name, []).extend(durations)

    return _build_metrics(type_count, failure_counter, invocation_durations)
