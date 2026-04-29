"""dashboard/server.py — ローカル HTTP サーバーでダッシュボードを提供する。"""
import itertools
import json
import os
import select
import signal
import socket
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subagent_metrics import (
    aggregate_subagent_failure_trend,
    aggregate_subagent_metrics,
    usage_invocation_events,
    usage_invocation_intervals,
)
# Issue #24 PR#31 codex P2: server.json の lock + compare-and-delete primitives は
# `server_registry` に切り出して `hooks/launch_dashboard.py` の cleanup パスと
# 共有する。本モジュール内では従来 API 名で再 export し、既存テスト
# (mod._file_lock / mod.write_server_json / mod.remove_server_json 等) との互換を保つ。
# 内部実装の monkeypatch (例: `_lock_fd` の差し替え) は本モジュールではなく
# `server_registry` に対して行う必要がある (binding は ref ではなく値コピーのため)。
import server_registry  # pylint: disable=wrong-import-position

_file_lock = server_registry._file_lock
_lock_path_for = server_registry._lock_path_for
_pid_matches = server_registry._pid_matches
write_server_json = server_registry.write_server_json
remove_server_json = server_registry.remove_server_json

_DEFAULT_PATH = Path.home() / ".claude" / "transcript-analyzer" / "usage.jsonl"
DATA_FILE = Path(os.environ.get("USAGE_JSONL", str(_DEFAULT_PATH)))

_DEFAULT_ALERTS_PATH = Path.home() / ".claude" / "transcript-analyzer" / "health_alerts.jsonl"
ALERTS_FILE = Path(os.environ.get("HEALTH_ALERTS_JSONL", str(_DEFAULT_ALERTS_PATH)))

_DEFAULT_SERVER_JSON_PATH = Path.home() / ".claude" / "transcript-analyzer" / "server.json"
SERVER_JSON_PATH = Path(os.environ.get("DASHBOARD_SERVER_JSON", str(_DEFAULT_SERVER_JSON_PATH)))


def _resolve_port() -> int:
    """`DASHBOARD_PORT` 未指定 or `0` → OS 任せ、具体値 → そのまま。"""
    raw = os.environ.get("DASHBOARD_PORT")
    if raw is None:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def _resolve_idle_seconds() -> float:
    """`DASHBOARD_IDLE_SECONDS` 未指定 → 600s デフォルト、`0` → 無効。"""
    raw = os.environ.get("DASHBOARD_IDLE_SECONDS")
    if raw is None:
        return 600.0
    try:
        return float(raw)
    except ValueError:
        return 600.0


def _resolve_poll_interval() -> float:
    """`DASHBOARD_POLL_INTERVAL` 未指定 → 1.0s デフォルト、`0` → ファイル監視を無効化。"""
    raw = os.environ.get("DASHBOARD_POLL_INTERVAL")
    if raw is None:
        return 1.0
    try:
        return float(raw)
    except ValueError:
        return 1.0


PORT = _resolve_port()
IDLE_SECONDS = _resolve_idle_seconds()
POLL_INTERVAL = _resolve_poll_interval()

TOP_N = 10

# Notification.notification_type は公式仕様で `permission`、過去実装/テストでは `permission_prompt` を観測。
# 両方を許可ダイアログ系としてカウントする。
_PERMISSION_NOTIFICATION_TYPES = frozenset({"permission", "permission_prompt"})

# total_events / aggregate_daily / aggregate_projects はプロジェクト目的の
# 「Skills と Subagents の使用状況」を示すメトリクスなので、usage 系イベントだけで集計する。
# session_*, notification, instructions_loaded, compact_*, subagent_stop は session_stats /
# health_alerts 等に分かれて表示されるため、ここで二重カウントしない。
_SKILL_USAGE_EVENT_TYPES = frozenset({
    "skill_tool",
    "user_slash_command",
})


def _filter_usage_events(events: list[dict]) -> list[dict]:
    """headline 集計用に usage 系イベントを返す。subagent は invocation 単位 dedup。

    `subagent_start` (PostToolUse 由来) と `subagent_lifecycle_start` (SubagentStart 由来) は
    通常ペアで発火し、PostToolUse が flaky / 不在な環境では lifecycle のみが届く。
    `usage_invocation_events()` で `aggregate_subagent_metrics()` と同じ invocation 同定を行い、
    各 invocation の代表イベント 1 件だけを採用することで、subagent_ranking と
    total_events / daily_trend / project_breakdown を必ず一致させる。
    """
    skill_events = [ev for ev in events if ev.get("event_type") in _SKILL_USAGE_EVENT_TYPES]
    return skill_events + usage_invocation_events(events)


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
            # naive datetime は仕様外 (hooks は必ず +00:00 / +HH:MM 付き)。silent skip。
            continue
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


TOP_N_SLASH_COMMAND_BREAKDOWN = 20
TOP_N_GLOB_MATCH = 10


def _compress_home_path(path: str) -> str:
    """`/Users/<user>/...` を `~/...` に圧縮する。一致しなければ無加工 path を返す。

    `home + os.sep` で prefix 比較するため "/Users/foo-extended/x" は HOME="/Users/foo"
    に false-match しない。HOME と完全一致 (sep 無し) の path も圧縮しない仕様。
    """
    home = os.path.expanduser("~")
    if home and path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def aggregate_instructions_loaded_breakdown(
    events: list[dict],
    top_n: int = TOP_N_GLOB_MATCH,
) -> dict:
    """instructions_loaded event を memory_type / load_reason / glob_match_top に集計。

    memory_type / load_reason の値はそのまま (lower-case 正規化しない / 実機データの
    真実を歪めない)。glob_match_top は file_path home 圧縮済み。

    P2 反映: dict (memory_type_dist / load_reason_dist) は count 降順 → key 昇順
    の insertion order で組み立てる。Python 3.7+ dict / JSON 仕様で順序保持される
    ため、consumer (renderer / static export) はこの順を信頼可能。

    Q1 反映: glob_match_top は load_reason="glob_match" の event だけを対象に
    file_path で count する。同じ file_path が他の load_reason で出現しても
    glob_match スコープ内の count しか積まない。

    3-P2 反映: top_n は glob_match_top にのみ適用。memory_type_dist /
    load_reason_dist は全観測キーを返す (キー数が hooks 仕様で bounded =
    `{"User", "Project", "Skill", ...}` の固定値域に収まるため cap 不要)。

    P3 反映: input events は in-place rewrite しない (集計後の dict キーに対してのみ
    `_compress_home_path` を適用)。

    実装注意 (2-P1 反映): `json.dumps(..., sort_keys=True)` を本関数の戻り値の
    serialize 経路で混入させると dict 順序契約が破壊される。
    `tests/test_skill_surface.py::test_dict_iteration_order_survives_json_roundtrip`
    が regression guard として動く。
    """
    memory_type_counter: Counter = Counter()
    load_reason_counter: Counter = Counter()
    glob_match_counter: Counter = Counter()
    for ev in events:
        if ev.get("event_type") != "instructions_loaded":
            continue
        mt = ev.get("memory_type", "")
        lr = ev.get("load_reason", "")
        fp = ev.get("file_path", "")
        if mt:
            memory_type_counter[mt] += 1
        if lr:
            load_reason_counter[lr] += 1
        if lr == "glob_match" and fp:
            glob_match_counter[_compress_home_path(fp)] += 1

    def _to_sorted_dict(counter: Counter) -> dict:
        # count desc → key asc。Python 3.7+ dict は insertion order を保持。
        return {k: c for k, c in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))}

    glob_match_top = [
        {"file_path": k, "count": c}
        for k, c in sorted(glob_match_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    ]
    return {
        "memory_type_dist": _to_sorted_dict(memory_type_counter),
        "load_reason_dist": _to_sorted_dict(load_reason_counter),
        "glob_match_top": glob_match_top,
    }


# 既知の source 値。これ以外 (None / 未知文字列) は legacy 扱いに倒す。
_SOURCE_EXPANSION = "expansion"
_SOURCE_SUBMIT = "submit"


def aggregate_slash_command_source_breakdown(
    events: list[dict],
    top_n: int = TOP_N_SLASH_COMMAND_BREAKDOWN,
) -> list[dict]:
    """user_slash_command event を skill ごとに source 分類して expansion_rate を返す。

    P1 反映: source 値を {"expansion", "submit", legacy (= それ以外 / source 不在)}
    の 3 分類で集計。expansion_rate の分母は modern (= expansion + submit) のみ、
    legacy は観測値として記録するが rate 計算からは除外する。modern == 0 の skill は
    expansion_rate=None を返す (= 観測待ち)。Q2 反映: rate は 4 桁小数で丸める。

    sort: (expansion + submit + legacy) 降順 → skill 昇順、top_n 件で cap。
    legacy も sort 分母に入れることで retention 経過後も上位順位の安定を保つ
    (trade-off は memory/skill_surface.md 参照)。
    """
    expansion_count: Counter = Counter()
    submit_count: Counter = Counter()
    legacy_count: Counter = Counter()
    for ev in events:
        if ev.get("event_type") != "user_slash_command":
            continue
        skill = ev.get("skill", "")
        if not skill:
            continue
        src = ev.get("source")
        if src == _SOURCE_EXPANSION:
            expansion_count[skill] += 1
        elif src == _SOURCE_SUBMIT:
            submit_count[skill] += 1
        else:
            legacy_count[skill] += 1
    skills = set(expansion_count) | set(submit_count) | set(legacy_count)
    rows = []
    for skill in skills:
        e = expansion_count.get(skill, 0)
        s = submit_count.get(skill, 0)
        lg = legacy_count.get(skill, 0)
        modern_total = e + s
        if modern_total > 0:
            rate = round(e / modern_total, 4)
        else:
            rate = None
        rows.append({
            "skill": skill,
            "expansion_count": e,
            "submit_count": s,
            "legacy_count": lg,
            "expansion_rate": rate,
        })
    rows.sort(key=lambda r: (-(r["expansion_count"] + r["submit_count"] + r["legacy_count"]),
                              r["skill"]))
    return rows[:top_n]


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


def build_dashboard_data(events: list[dict]) -> dict:
    usage_events = _filter_usage_events(events)
    permission_breakdowns = aggregate_permission_breakdowns(events)
    return {
        "last_updated": _now_iso(),
        "total_events": len(usage_events),
        "skill_ranking": aggregate_skills(events),
        "subagent_ranking": aggregate_subagents(events),
        "daily_trend": aggregate_daily(usage_events),
        "project_breakdown": aggregate_projects(usage_events),
        "hourly_heatmap": aggregate_hourly_heatmap(usage_events),
        "skill_cooccurrence": aggregate_skill_cooccurrence(events),
        "project_skill_matrix": aggregate_project_skill_matrix(events),
        "subagent_failure_trend": aggregate_subagent_failure_trend(events),
        "permission_prompt_skill_breakdown": permission_breakdowns["skill"],
        "permission_prompt_subagent_breakdown": permission_breakdowns["subagent"],
        "compact_density": aggregate_compact_density(events),
        "session_stats": aggregate_session_stats(events),
        "health_alerts": load_health_alerts(),
        "slash_command_source_breakdown": aggregate_slash_command_source_breakdown(events),
        "instructions_loaded_breakdown": aggregate_instructions_loaded_breakdown(events),
    }


def render_static_html(data: dict) -> str:
    """データをインライン埋め込みしたスタンドアロン HTML を返す。

    `<script>` ブロック内の JSON literal は HTML script-data-state パーサーから見て
    `</` で始まるシーケンスを含むと早期終了させられうる (`</script>` 直接抜けの他、
    `<!--` で script-data-escaped state に入った後 `</script>` で抜けるパスもある)。
    `</` 全般を `<\\/` に escape することで両方のパスを構造的に塞ぐ。
    `\\/` は RFC 8259 で許可された JSON escape のため `JSON.parse` ラウンドトリップ
    でブラウザには元の `</` として復元される。

    `<!--` 単体は escape 不要: `</` 全般 escape の時点で script-data-escaped 経路の
    `</script>` 抜け道は塞がれており、`<!--` 自体は構造化データの一部として
    そのまま通せる (claude[bot] PR#27 review #1 / 再レビュー対応)。
    """
    json_str = json.dumps(data, ensure_ascii=False).replace("</", r"<\/")
    inline = f'<script>window.__DATA__ = {json_str};</script>\n'
    return _HTML_TEMPLATE.replace('</head>', inline + '</head>', 1)


_HTML_TEMPLATE = (Path(__file__).resolve().parent / "template.html").read_text(encoding="utf-8")


# SSE handler の peer-disconnect チェック周期 (秒)。
# `sse_keepalive` が長い (本番 15s) ときも、この周期で peer 検知を回すことで
# ブラウザを閉じた直後に handler が抜け、idle watchdog が再開できる。
# テストの場合 sse_keepalive をこの値より短くすれば peer check も追従する
# (ループは min(keepalive, _SSE_PEER_CHECK_INTERVAL) 周期で回る)。
_SSE_PEER_CHECK_INTERVAL = 1.0


def _peer_disconnected(sock) -> bool:
    """`sock` の対向が FIN / RST を送って切断したかを non-blocking に判定する。

    SSE は server→client の単方向ストリームなので、client から read 可能になる
    のは EOF / RST のときだけ。`select` で読み取り可能を検知し `MSG_PEEK` で覗く。
    """
    try:
        readable, _, _ = select.select([sock], [], [], 0)
    except (ValueError, OSError):
        return True
    if not readable:
        return False
    try:
        peek = sock.recv(1, socket.MSG_PEEK)
    except (BlockingIOError, InterruptedError):
        return False
    except OSError:
        return True
    return not peek  # b"" なら EOF


class SSEClient:
    """`/events` で接続中の 1 クライアントを表現する。

    write は背景の broadcaster と handler 側 keepalive の両方から走るので
    `write_lock` で直列化する。書き込み失敗を観測したら `alive` を落とし、
    server 側の broadcast から自動で除外される。
    """

    def __init__(self, wfile):
        self.wfile = wfile
        self._write_lock = threading.Lock()
        self.alive = threading.Event()
        self.alive.set()

    def send(self, payload: bytes) -> bool:
        with self._write_lock:
            if not self.alive.is_set():
                return False
            try:
                self.wfile.write(payload)
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.alive.clear()
                return False


class _SseState:
    """SSE クライアント集合と書き込み排他制御。

    DashboardServer から SSE 配信状態を切り出してインスタンス属性数を抑える。
    `register` / `unregister` / `count` / `broadcast` は thread-safe。
    """

    def __init__(self, keepalive: float):
        self.clients: list[SSEClient] = []
        self.lock = threading.Lock()
        self.keepalive = float(keepalive)

    def register(self, client: SSEClient) -> None:
        with self.lock:
            self.clients.append(client)

    def unregister(self, client: SSEClient) -> None:
        with self.lock:
            try:
                self.clients.remove(client)
            except ValueError:
                pass

    def count(self) -> int:
        with self.lock:
            return len(self.clients)

    def broadcast(self, payload: bytes) -> int:
        with self.lock:
            clients = list(self.clients)
        sent = 0
        dead: list[SSEClient] = []
        for c in clients:
            if c.send(payload):
                sent += 1
            else:
                dead.append(c)
        if dead:
            with self.lock:
                for c in dead:
                    try:
                        self.clients.remove(c)
                    except ValueError:
                        pass
        return sent


class _FileWatcher:
    """`(inode, size, mtime)` ベースの軽量ファイル監視。

    GB 級でも内容を読まずに変化を検知する（受け入れ条件）。
    `interval <= 0` で無効化、`path` 不在は `None` 署名扱いで一度も変更検知しない。
    """

    def __init__(self, path: Optional[Path], interval: float):
        self.path: Optional[Path] = Path(path) if path is not None else None
        self.interval = float(interval)
        self.thread: Optional[threading.Thread] = None

    def start(self, stop_event: threading.Event, on_change: Callable[[], None]) -> None:
        if self.interval <= 0:
            return
        self.thread = threading.Thread(
            target=self._loop, args=(stop_event, on_change),
            daemon=True, name="DashboardFileWatcher",
        )
        self.thread.start()

    def _loop(self, stop_event: threading.Event, on_change: Callable[[], None]) -> None:
        last = self._signature()
        while not stop_event.wait(self.interval):
            cur = self._signature()
            if cur != last:
                last = cur
                on_change()

    def _signature(self):
        if self.path is None:
            return None
        try:
            st = self.path.stat()
        except (FileNotFoundError, OSError):
            return None
        if sys.platform == "win32":
            # Issue #24 N2: Win NTFS では st_ino が 0 / 不安定で signature 比較が
            # 壊れることがある。size + mtime_ns のみで実用上の検出精度は十分。
            return (st.st_size, st.st_mtime_ns)
        return (st.st_ino, st.st_size, st.st_mtime_ns)


class _IdleTracker:
    """idle カウンタと watchdog スレッド。

    `seconds <= 0` で watchdog 無効。SSE 接続中は `sse_count_fn() > 0` が
    返る前提で外部から touch を継続発火させ、idle 進行を凍結する。
    """

    def __init__(self, seconds: float):
        self.seconds = float(seconds)
        self.activity_lock = threading.Lock()
        self.last_activity = time.monotonic()
        self.thread: Optional[threading.Thread] = None

    def touch(self) -> None:
        with self.activity_lock:
            self.last_activity = time.monotonic()

    def idle_for(self) -> float:
        with self.activity_lock:
            return time.monotonic() - self.last_activity

    def start(self, stop_event: threading.Event,
              sse_count_fn: Callable[[], int],
              on_idle: Callable[[], None]) -> None:
        if self.seconds <= 0:
            return
        check_interval = max(0.05, min(self.seconds / 2.0, 1.0))
        self.thread = threading.Thread(
            target=self._loop, args=(stop_event, sse_count_fn, on_idle, check_interval),
            daemon=True, name="DashboardIdleWatchdog",
        )
        self.thread.start()

    def _loop(self, stop_event, sse_count_fn, on_idle, check_interval) -> None:
        while not stop_event.wait(check_interval):
            # SSE クライアントが 1 つでもあれば idle 進行を凍結（受け入れ条件）。
            # touch() で last_activity を更新するので idle_for() も同時にリセットされる。
            if sse_count_fn() > 0:
                self.touch()
                continue
            if self.idle_for() > self.seconds:
                on_idle()
                return


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # idle カウンタリセット (DashboardServer 利用時のみ; 旧 HTTPServer 直叩きテスト互換のため defensive)
        touch = getattr(self.server, "touch", None)
        if callable(touch):
            touch()
        if self.path == "/api/data":
            self._serve_api()
        elif self.path == "/healthz":
            self._serve_healthz()
        elif self.path == "/events":
            self._serve_events()
        else:
            self._serve_html()

    def _serve_api(self):
        events = load_events()
        data = build_dashboard_data(events)
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_healthz(self):
        started_at = getattr(self.server, "started_at", _now_iso())
        payload = {"status": "ok", "started_at": started_at}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        body = _HTML_TEMPLATE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self):
        """`/events` SSE エンドポイント。サーバー shutdown / クライアント切断まで block する。

        - 初回に comment 行を flush して EventSource.onopen を即発火
        - 登録した `SSEClient` は usage.jsonl 変更時に server 側からブロードキャストされる
        - keepalive ごとに idle カウンタを touch（SSE 接続中に idle で落とさないため）
        """
        register = getattr(self.server, "register_sse_client", None)
        if register is None:
            # DashboardServer 以外で叩かれたら 501 (旧 HTTPServer 直叩きテスト互換)
            self.send_error(501, "SSE not supported on this server")
            return

        # この接続では HTTP keep-alive で次のリクエストを処理しない
        self.close_connection = True

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        # nginx などのバッファリングを抑止 (localhost 用途では実害無いが慣例)
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        client = SSEClient(self.wfile)
        if not client.send(b": connected\n\n"):
            return
        register(client)

        unregister = getattr(self.server, "unregister_sse_client", None)
        stop_event = getattr(self.server, "_stop_event", None)
        touch = getattr(self.server, "touch", None)
        # SSE keepalive 周期 (秒)。テストでは短く、本番ではデフォ 15s。
        keepalive = getattr(self.server, "sse_keepalive", 15.0)
        # peer check はこの値以下の周期で回す (codex Finding 2 対策)。
        # keepalive がこれより短ければ keepalive 周期に揃える。
        tick = min(keepalive, _SSE_PEER_CHECK_INTERVAL) if keepalive > 0 else _SSE_PEER_CHECK_INTERVAL
        sock = self.connection  # 切断 (FIN/RST) 検出用
        last_keepalive = time.monotonic()

        try:
            # サーバー停止 / クライアント切断まで long-poll。
            # tick (≤1s) 周期で peer 切断を検知 → handler が抜けて unregister
            # → idle watchdog が再開できる。keepalive 送信は経過時間ベース。
            while client.alive.is_set():
                if stop_event is not None and stop_event.wait(tick):
                    break
                if stop_event is None:
                    time.sleep(tick)
                if not client.alive.is_set():
                    break
                if _peer_disconnected(sock):
                    break
                now = time.monotonic()
                # keepalive=0 は「無効化」の意図で渡される想定。`now - last_keepalive >= 0` が
                # 常に True になって毎 tick で comment が飛ぶのを防ぐため、下限 0 を明示的に
                # 含める chained comparison でガード。
                if 0 < keepalive <= now - last_keepalive:
                    if not client.send(b": keepalive\n\n"):
                        break
                    last_keepalive = now
                    if callable(touch):
                        touch()
        finally:
            if callable(unregister):
                unregister(client)

    def log_message(self, fmt, *args):
        pass


class DashboardServer(ThreadingHTTPServer):
    """ライブダッシュボード用 HTTP サーバー。

    - ThreadingHTTPServer で並行リクエスト処理
    - `idle_seconds > 0` で idle watchdog を起動し、最終リクエストから
      `idle_seconds` 経過で graceful shutdown（SSE 接続が 1 つ以上ある間は
      idle カウンタを touch して凍結）
    - `poll_interval > 0` で usage.jsonl の (inode, size, mtime) を監視し、
      変化検知時に SSE クライアントへ `data: refresh\\n\\n` をブロードキャスト
    - `touch()` / `idle_for()` でハンドラから idle カウンタを操作
    """

    daemon_threads = True
    # Issue #24 N1: Win で True にすると SO_REUSEADDR の Win 仕様差で別プロセスに
    # ポートを横取りされる懸念がある。POSIX のみ True (TIME_WAIT 中の再利用許可)、
    # Win は default False にして OS の自然解放に任せる。
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, server_address, RequestHandlerClass, *,
                 idle_seconds: float = 0.0,
                 poll_interval: float = 0.0,
                 usage_jsonl_path: Optional[Path] = None,
                 sse_keepalive: float = 15.0):
        # bind/activate 失敗時、親 TCPServer.__init__ が `except: self.server_close()` で
        # 我々の override (`_stop_event.set()` を触る) を呼ぶ。属性が無いと AttributeError で
        # 本来の OSError をマスクするため、必ず super().__init__() より前に初期化する。
        self._stop_event = threading.Event()
        self._idle = _IdleTracker(idle_seconds)
        self._sse = _SseState(keepalive=sse_keepalive)
        self._watcher = _FileWatcher(path=usage_jsonl_path, interval=poll_interval)
        self.started_at = _now_iso()
        super().__init__(server_address, RequestHandlerClass)
        self._idle.start(
            stop_event=self._stop_event,
            sse_count_fn=self._sse.count,
            on_idle=self._initiate_shutdown,
        )
        self._watcher.start(
            stop_event=self._stop_event,
            on_change=lambda: self._sse.broadcast(b"data: refresh\n\n"),
        )

    # --- public な構成値 (handler / テスト互換のため property で公開) -----

    @property
    def idle_seconds(self) -> float:
        return self._idle.seconds

    @property
    def poll_interval(self) -> float:
        return self._watcher.interval

    @property
    def usage_jsonl_path(self) -> Optional[Path]:
        return self._watcher.path

    @property
    def sse_keepalive(self) -> float:
        return self._sse.keepalive

    # --- idle カウンタ -------------------------------------------------

    def touch(self) -> None:
        self._idle.touch()

    def idle_for(self) -> float:
        return self._idle.idle_for()

    # --- SSE 配信 -------------------------------------------------------

    def register_sse_client(self, client: SSEClient) -> None:
        self._sse.register(client)

    def unregister_sse_client(self, client: SSEClient) -> None:
        self._sse.unregister(client)

    def sse_client_count(self) -> int:
        return self._sse.count()

    def broadcast_sse(self, payload: bytes) -> int:
        return self._sse.broadcast(payload)

    # --- ライフサイクル -------------------------------------------------

    def _initiate_shutdown(self) -> None:
        # serve_forever が exit するまで shutdown はブロックするので別スレで叩く。
        # ThreadingHTTPServer.shutdown を直接参照することで、override 越しの
        # 自己再帰 (shutdown → _stop_event.set → 既に set 済み → super().shutdown) を回避。
        threading.Thread(
            target=ThreadingHTTPServer.shutdown, args=(self,), daemon=True,
        ).start()

    def shutdown(self) -> None:
        # 外部 / 内部いずれの shutdown 経路でも watchdog / watcher ループを止める
        self._stop_event.set()
        super().shutdown()

    def server_close(self) -> None:
        self._stop_event.set()
        super().server_close()


def create_server(
    port: int = 0,
    idle_seconds: float = 0.0,
    handler_cls=None,
    # IPv4 loopback を直接指定し `getaddrinfo("localhost", ...)` を skip する。
    # `localhost` 解決は IPv6/IPv4 dual-stack の mDNSResponder 起因で遅延・hang する
    # 環境 (例: GitHub Actions macOS arm64 runner) があり、bind が無限ブロックする。
    # `run()` の URL は `http://localhost:N` のままで OK (loopback 同一)。
    host: str = "127.0.0.1",
    poll_interval: float = 0.0,
    usage_jsonl_path: Optional[Path] = None,
    sse_keepalive: float = 15.0,
) -> DashboardServer:
    """Phase A/B 仕様の DashboardServer を返す（serve_forever は呼び出し側）。

    `poll_interval > 0` で usage.jsonl の変化監視を有効化（Phase B SSE）。
    `usage_jsonl_path` 未指定時はモジュール変数 `DATA_FILE` を採用。
    `sse_keepalive` は SSE keepalive ping 周期（秒）。テストでは短く設定。
    """
    return DashboardServer(
        (host, port),
        handler_cls or DashboardHandler,
        idle_seconds=idle_seconds,
        poll_interval=poll_interval,
        usage_jsonl_path=usage_jsonl_path if usage_jsonl_path is not None else DATA_FILE,
        sse_keepalive=sse_keepalive,
    )


def run(
    server: DashboardServer,
    server_json_path: Path,
    *,
    install_signals: bool = True,
    on_ready: Optional[Callable[[], None]] = None,
    log_stream=sys.stderr,
) -> None:
    """server を起動し、server.json の write/remove を結線する。

    - `install_signals=True` で SIGTERM / SIGINT を graceful shutdown にフック
    - `on_ready` は server.json を書いた直後に呼ばれる（テスト用同期点）
    """
    actual_port = server.server_address[1]
    info = {
        "pid": os.getpid(),
        "port": actual_port,
        "url": f"http://localhost:{actual_port}",
        "started_at": server.started_at,
    }
    write_server_json(server_json_path, info)

    if install_signals:
        def _signal_shutdown(_signum, _frame):  # pragma: no cover - signal path
            threading.Thread(target=server.shutdown, daemon=True).start()
        try:
            signal.signal(signal.SIGTERM, _signal_shutdown)
            signal.signal(signal.SIGINT, _signal_shutdown)
        except ValueError:
            # signal.signal はメインスレッド以外では ValueError。テスト経路で起こりうる
            pass

    print(f"Dashboard available: {info['url']}", file=log_stream)
    if on_ready is not None:
        on_ready()

    try:
        server.serve_forever()
    finally:
        # compare-and-delete: 他インスタンスが上書きした server.json は消さない
        remove_server_json(server_json_path, expected_pid=info["pid"])
        server.server_close()


def main() -> None:
    server = create_server(
        port=PORT,
        idle_seconds=IDLE_SECONDS,
        poll_interval=POLL_INTERVAL,
    )
    run(server, SERVER_JSON_PATH)


if __name__ == "__main__":
    main()
