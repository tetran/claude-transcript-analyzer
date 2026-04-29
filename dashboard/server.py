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
from datetime import datetime, timedelta, timezone
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
        "skill_invocation_breakdown": aggregate_skill_invocation_breakdown(events),
        "skill_lifecycle": aggregate_skill_lifecycle(events),
        "skill_hibernating": aggregate_skill_hibernating(events),
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


# `template/` 配下に分割した shell + styles + scripts を起動時に concat する (Issue #67)。
# `_HTML_TEMPLATE` は外部から見たら従来通り「単一の HTML 文字列」契約を維持しているので、
# `render_static_html` の `</head>` replace 戦略や export_html 経路は無改修で動く。
#
# 連結順は CSS のカスケード順 / JS の closure 内での宣言順を再現するため厳密に決まっている。
# 個別ファイルへの分割境界は元 template.html のセクションコメント (例: `/* ---------- KPI ---------- */`)
# を踏襲。新しいセクションを追加するときはこの list に追記し、
# 既存セクション内へ追記するときは該当ファイルを開いて編集する。
_TEMPLATE_DIR = Path(__file__).resolve().parent / "template"
_CSS_FILES = (
    "00_base.css",          # root vars / reset / body / .app
    "10_components.css",    # header / live badge / KPI / panel / two-up / ranking / spark / projects / footer
    "20_help_tooltip.css",  # help button + data tooltip (graph data points)
    "30_pages.css",         # multipage shell (Issue #57)
    "40_patterns.css",      # hourly heatmap + skill cooccurrence + project×skill (Issue #58/59)
    "50_quality.css",       # subagent percentile/failure + permission breakdown + compact density (Issue #60/61)
    "60_surface.css",       # Surface 3 panel + tooltip border colors (Issue #74)
)
_MAIN_JS_FILES = (
    "10_helpers.js",              # esc / fmtN / pad / STATUS_LABEL / setConnStatus
    "20_load_and_render.js",      # async loadAndRender (KPI / ranking / sparkline / projects)
    "30_renderers_patterns.js",   # heatmap / cooccurrence / project×skill matrix renderers
    "40_renderers_quality.js",    # subagent percentile / failure / permission / compact renderers
    "50_renderers_surface.js",    # Surface invocation / lifecycle / hibernating + fmtDur
    "60_hashchange_listener.js",  # hashchange → loadAndRender 再実行 (Issue #58 Q2)
    "70_init_eventsource.js",     # 初回描画 + EventSource (live refresh)
    "80_help_popup.js",           # help popover behavior (click / Escape / resize)
    "90_data_tooltip.js",         # data tooltip ([data-tip] elements)
)


def _build_html_template() -> str:
    """`template/` 配下を起動時に 1 度だけ concat して `_HTML_TEMPLATE` を作る。

    shell.html に置いた `__INCLUDE_*\\n` センチネルを、styles / scripts の concat 結果で
    line-aligned に置換する (置換は trailing `\\n` ごと吸収するので前後の改行が二重化しない)。
    """
    styles = "".join((_TEMPLATE_DIR / "styles" / name).read_text(encoding="utf-8") for name in _CSS_FILES)
    router_js = (_TEMPLATE_DIR / "scripts" / "00_router.js").read_text(encoding="utf-8")
    main_js = "".join((_TEMPLATE_DIR / "scripts" / name).read_text(encoding="utf-8") for name in _MAIN_JS_FILES)
    shell = (_TEMPLATE_DIR / "shell.html").read_text(encoding="utf-8")
    return (shell
            .replace("__INCLUDE_STYLES__\n", styles)
            .replace("__INCLUDE_ROUTER_JS__\n", router_js)
            .replace("__INCLUDE_MAIN_JS__\n", main_js))


_HTML_TEMPLATE = _build_html_template()


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
