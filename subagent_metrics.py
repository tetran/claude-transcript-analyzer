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


def subagent_invocation_interval(ev: dict) -> tuple[float, float]:
    """subagent invocation の代表 event から `(start_epoch, end_epoch)` を返す。

    permission attribution (Issue #61 / A2) で、permission notification が
    invocation の execution interval にカブっていたかを判定するための helper。
    `usage_invocation_events()` は両 hook 発火 invocation で `subagent_start` を
    代表に選ぶため、通常は ev.timestamp が **終了時刻** → `[end - duration, end]`
    を返す。`event_type == "subagent_lifecycle_start"` (lifecycle-only invocation)
    のみ例外分岐: ev.timestamp が **開始時刻** → `[start, start + duration]` を返す。
    `duration_ms` 不在 / 不正 timestamp の場合は `start == end` (point interval)
    に倒し、caller 側で interval-cover 判定の "broken candidate" にしない。

    後続で `reports/summary.py` 等が permission breakdown を必要としたら、
    同じ helper をそのまま import して共有する (#60 2-Q1 教訓踏襲: subagent
    invocation の interval 解釈は `subagent_metrics.py` に閉じる)。
    """
    ts = _parse_ts(ev.get("timestamp", ""))
    if ts is None:
        return (0.0, 0.0)
    base = ts.timestamp()
    duration_ms = ev.get("duration_ms")
    if not isinstance(duration_ms, (int, float)) or duration_ms <= 0:
        return (base, base)
    duration_s = float(duration_ms) / 1000.0
    if ev.get("event_type") == "subagent_lifecycle_start":
        # lifecycle-only invocation: timestamp = 開始時刻
        return (base, base + duration_s)
    # subagent_start (PostToolUse 由来): timestamp = 終了時刻
    return (base - duration_s, base)


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
            # メインスレッド停止時の SubagentStop hook 誤発火を構造的に除外
            # (Issue #100 / #93)。本 helper は subagent_start / subagent_lifecycle_start
            # のみ iterate するので subagent_stop の dedup 対象は無く、filter のみ適用。
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


def usage_invocation_intervals(events: list[dict]) -> list[tuple[float, float, dict]]:
    """各 subagent invocation の `(start_epoch, end_epoch, rep_ev)` を返す。

    `usage_invocation_events()` と同じ invocation 同定 + 代表イベント選択を行いつつ、
    `aggregate_subagent_metrics` と同じ start↔stop pairing 結果を使って
    **paired stop の duration_ms を fallback** に組み込む。

    permission attribution (Issue #61 / A2) で execution-window cover 判定を行う
    ときに、lifecycle-only invocation (`record_subagent.py:_handle_subagent_start` は
    `duration_ms` を書き出さない) でも stop event の `duration_ms` を使って
    interval の長さを復元するための専用 helper。`subagent_invocation_interval(ev)`
    単体だと rep event に duration が無い lifecycle-only invocation が point
    interval (start==end) に縮退して長時間 invocation 中の permission を
    取りこぼすため。

    返り値: `[(start_epoch, end_epoch, rep_ev), ...]`。timestamp parse 失敗の
    invocation は (0.0, 0.0, rep_ev) で出すので caller 側で除外する。
    """
    starts_by_key, stops_by_key, lifecycle_by_key = _bucket_events(events)
    result: list[tuple[float, float, dict]] = []
    for key in set(starts_by_key) | set(lifecycle_by_key):
        starts_sorted = sorted(starts_by_key.get(key, []), key=_ts_key)
        lifecycle_sorted = sorted(lifecycle_by_key.get(key, []), key=_ts_key)
        stops_sorted = sorted(stops_by_key.get(key, []), key=_ts_key)
        invocations = _build_invocations(starts_sorted, lifecycle_sorted)
        for inv, stop in _pair_invocations_with_stops(invocations, stops_sorted):
            start = inv.get("start")
            lifecycle = inv.get("lifecycle")
            rep = start or lifecycle
            if rep is None:
                continue
            # rep に duration_ms が無いとき paired stop の duration_ms を fallback。
            # これで lifecycle-only invocation も interval 長さを保てる。
            duration_ms = rep.get("duration_ms")
            if not isinstance(duration_ms, (int, float)) or duration_ms <= 0:
                if stop is not None:
                    stop_d = stop.get("duration_ms")
                    if isinstance(stop_d, (int, float)) and stop_d > 0:
                        rep = {**rep, "duration_ms": stop_d}
            start_epoch, end_epoch = subagent_invocation_interval(rep)
            result.append((start_epoch, end_epoch, rep))
    return result


def _bucket_events(events: list[dict]) -> tuple[dict, dict, dict]:
    """events を `(session_id, subagent_type)` キーで starts / stops / lifecycle に振り分け。

    `subagent_type == ""` は構造的に除外 (Issue #100 / #93):
    SubagentStop hook はメインスレッド停止時にも誤発火し、その場合 type が空。
    実 subagent 不在 / per-subagent transcript file も不在 (#93 ローカル調査) なので
    aggregation 時 filter で 100% 即時救済できる。post-hoc heuristic ペアリングは
    対象不在 → 不採用。詳細は docs/reference/subagent-invocation-pairing.md
    "Known artifact" セクション参照。

    `subagent_stop` は `(session_id, subagent_id)` で **min(timestamp)** dedup
    (Issue #100 / #93): Claude Code が同 stop hook を最大 4 重発火する観察あり
    (3 組 / 全期間 7 件)。timestamp 最小の 1 件のみ採用 (rescan_transcripts.py
    --append 経由で input order が時間順と乖離しても正しく earliest を選ぶため
    2-pass 化)。subagent_id が空 ("") の場合は dedup key を共有せず個別扱い
    (= 既存ペアリング挙動を破壊しない)。

    Key 設計: dedup は `(session_id, subagent_id)` で集約。Issue 本文の文言通り。
    `subagent_id` がグローバル一意であっても session_id を含めることで
    over-keying は無害 (false-collapse は発生しない); 仮にグローバル衝突が
    あった場合の防御も兼ねる。

    Order: type='' filter → agent_id dedup. 逆順だと type='' stop の
    subagent_id が earliest_ts_by_dedup を汚染し、本物 subagent の dedup key と
    衝突するリスク (本リポでは agent_id 衝突は希少だが構造的に防ぐ)。
    """
    earliest_ts_by_dedup: dict = {}
    for ev in events:
        if ev.get("event_type") != "subagent_stop":
            continue
        if not ev.get("subagent_type", ""):
            continue
        sid = ev.get("subagent_id", "")
        if not sid:
            continue
        dedup_key = (ev.get("session_id", ""), sid)
        ts = ev.get("timestamp", "")
        cur = earliest_ts_by_dedup.get(dedup_key)
        if cur is None or ts < cur:
            earliest_ts_by_dedup[dedup_key] = ts

    starts_by_key: dict = {}
    stops_by_key: dict = {}
    lifecycle_by_key: dict = {}
    accepted_dedup_keys: set = set()
    for ev in events:
        et = ev.get("event_type")
        name = ev.get("subagent_type", "")
        if not name:
            continue
        key = (ev.get("session_id", ""), name)
        if et == "subagent_start":
            starts_by_key.setdefault(key, []).append(ev)
        elif et == "subagent_stop":
            sid = ev.get("subagent_id", "")
            if sid:
                dedup_key = (ev.get("session_id", ""), sid)
                if ev.get("timestamp", "") != earliest_ts_by_dedup[dedup_key]:
                    continue
                if dedup_key in accepted_dedup_keys:
                    continue
                accepted_dedup_keys.add(dedup_key)
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


def _pair_invocations_with_stops(
    invocations: list[dict], stops_sorted: list[dict]
) -> list[tuple[dict, dict | None]]:
    """invocation と stop を pairing し `[(invocation, paired_stop_or_None), ...]` を返す。

    `_process_bucket` と `_bucket_invocation_records` の両方から呼ばれる単一ペアリング
    関数。両者が同一 pair 列を共有することで failure_count drift を構造的に防ぐ
    (= `aggregate_subagent_metrics` と `aggregate_subagent_failure_trend` の
    failure_count が type 単位の合計で常に一致する)。

    - 件数一致 (`paired_stops = True`) → sequential 1:1 (重複発火扱い、timestamp 検査なし)
    - 件数不一致:
      - `start.success=False` → 起動失敗で stop なしと扱い stop プール非消費
      - `start.success=True` → **timestamp-window pairing**: `start.ts` 以降かつ次 invocation
        の `start.ts` 未満 (最終 invocation は +∞) の未消費 stop を最初に採る。
        sequential 1:1 だと「2 succeeded starts (W1, W2) + 1 failed stop (W2)」のような
        入力で stop[0] が start[0] にマッチし failure が earlier 週へ shift する
        cross-week 誤 attribute が起きるため、timestamp で window を切って防ぐ

    Note: dashboard/server.py:_filter_events_by_period 第三段 mirrors this pairing rule.
          Keep in sync.
    """
    paired_stops = len(invocations) == len(stops_sorted)
    if paired_stops:
        return list(zip(invocations, stops_sorted))

    inv_ts: list = []
    for inv in invocations:
        rep = inv.get("start") or inv.get("lifecycle")
        ts = _parse_ts(rep.get("timestamp", "")) if rep else None
        inv_ts.append(ts)

    stop_consumed = [False] * len(stops_sorted)
    pairs: list[tuple[dict, dict | None]] = []
    for i, inv in enumerate(invocations):
        start = inv.get("start")
        if bool(start) and start.get("success") is False:
            pairs.append((inv, None))
            continue
        this_ts = inv_ts[i]
        next_ts = inv_ts[i + 1] if i + 1 < len(invocations) else None
        chosen_idx: int | None = None
        for j, stop in enumerate(stops_sorted):
            if stop_consumed[j]:
                continue
            stop_ts = _parse_ts(stop.get("timestamp", ""))
            if this_ts is not None and stop_ts is not None:
                if stop_ts < this_ts:
                    continue
                if next_ts is not None and stop_ts >= next_ts:
                    continue
            chosen_idx = j
            break
        if chosen_idx is not None:
            stop_consumed[chosen_idx] = True
            pairs.append((inv, stops_sorted[chosen_idx]))
        else:
            pairs.append((inv, None))
    return pairs


def _process_bucket(
    invocations: list[dict],
    stops_sorted: list[dict],
) -> tuple[int, list[float]]:
    """1 バケット (session×type) の invocation 群を処理し (failure_count, durations) を返す。

    Pairing は `_pair_invocations_with_stops` に委譲。失敗判定は invocation 単位の
    `start.success=False OR paired_stop.success=False`。余り stops は durations に
    積まない: invocation 単位集計なので stop 単独イベントは sample にならず、
    `sample_count <= count` invariant を構造的に保つ (= percentile 母集団を invocation
    数と一致させる)。
    """
    failures = 0
    durations: list[float] = []
    for inv, stop in _pair_invocations_with_stops(invocations, stops_sorted):
        start = inv.get("start")
        start_failed = bool(start) and start.get("success") is False
        stop_failed = bool(stop) and stop.get("success") is False
        if start_failed or stop_failed:
            failures += 1
        inv_duration = _invocation_duration(start, stop)
        if inv_duration is not None:
            durations.append(inv_duration)
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
    という言い方はしない (test では既知サンプル `[1,2,3,4]` で p50=2.5 / p90=3.7 / p99=3.97
    を pin して method 切替えによる回帰を検出する)。
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

    Pairing は `_pair_invocations_with_stops` に委譲し `_process_bucket` と同一の
    pair 列を共有する。これにより `aggregate_subagent_metrics` の failure_count と
    `aggregate_subagent_failure_trend` の failure_count が type 単位の合計で常に
    一致する (drift guard / `test_failure_count_matches_metrics_failure_count`)。

    余り stops (`stops_sorted[len(invocations):]`) は **record 化しない**:
    invocation 単位集計なので stop 単独イベントは trend に寄与しないのが正解
    (`_process_bucket` も同じ理由で durations に積まない)。
    """
    records: list[dict] = []
    for inv, stop in _pair_invocations_with_stops(invocations, stops_sorted):
        start = inv.get("start")
        lifecycle = inv.get("lifecycle")
        # lifecycle.timestamp は SubagentStart hook 由来 (= 起動時刻に近い)。
        # start.timestamp は PostToolUse(Task|Agent) 由来で invocation 完了時刻
        # (record_subagent.py の `_now_iso()`)。週次 trend を起動週で bucket
        # するため lifecycle 優先、無ければ start に fallback (Issue #71)。
        rep = lifecycle or start
        ts = rep.get("timestamp", "") if rep else ""
        start_failed = bool(start) and start.get("success") is False
        stop_failed = bool(stop) and stop.get("success") is False
        records.append({
            "timestamp": ts,
            "subagent_type": name,
            "failed": start_failed or stop_failed,
        })
    return records


def invocation_records(events: list[dict]) -> list[dict]:
    """各 invocation を `{"timestamp": str, "subagent_type": str, "failed": bool}` で返す。

    `aggregate_subagent_metrics` と同じ invocation 同定 (`_bucket_events` +
    `_build_invocations` + start↔stop pairing) を使い、各 invocation の
    `failed` flag (start.success=False OR stop.success=False) を計算する。
    timestamp は invocation の代表時刻 = `lifecycle.timestamp` 優先 (= 起動時刻)、
    無ければ `start.timestamp` に fallback。

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

    naive datetime は UTC として扱う safety belt: `usage.jsonl` は通常 `+00:00` 付き ISO
    だが、Stop hook 経由 `_merge_stop_hook_list` や `rescan_transcripts.py --append`
    由来で naive ISO が紛れた場合に local TZ shift を構造的に塞ぐ。Python 3.11+ では
    naive `astimezone()` が local TZ 解釈で silent shift する非対称があるため特に厳格に。
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
    **server は top-N で切らず観測された全 (week, subagent_type) を返す**: client 側の
    top-5 描画は affordance であり schema には現れない (programmatic な consumer は
    全 type の trend を受け取る前提で読む)。
    naive datetime は UTC として扱う (`_week_start_iso` の safety belt 参照)。

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
