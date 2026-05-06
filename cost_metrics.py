"""cost_metrics.py — assistant_usage events から session 単位の cost を導出する純関数群。

Issue #99 / v0.8.0〜。`docs/reference/cost-calculation-design.md` §9-§10 で
採用した「per-message 集計 (raw token + model 永続化、表示時にオンデマンド計算)」
方針の実装。価格表は **DB / event log に保存しない**: 価格改定があれば
本 module の `MODEL_PRICING` を update するだけで全期間の cost が再計算される。

## 価格表の出典 (verbatim pin)

価格は 2026-05-06 に Anthropic 公式 docs から目視で pin した:
  https://platform.claude.com/docs/en/about-claude/pricing

「Model pricing」table の値を **per-1M-token USD** で転記。本表記は cost を
USD per 1M token で揃える AgenticSec / cost-calculation-design.md §2 の慣習に従う。

| Model              | 公式 model ID prefix     | input | output | cache_read | 5m cache_creation |
|--------------------|--------------------------|-------|--------|------------|-------------------|
| Claude Opus 4.7    | `claude-opus-4-7`        | $5    | $25    | $0.50      | $6.25             |
| Claude Opus 4.6    | `claude-opus-4-6`        | $5    | $25    | $0.50      | $6.25             |
| Claude Opus 4.5    | `claude-opus-4-5`        | $5    | $25    | $0.50      | $6.25             |
| Claude Opus 4.1    | `claude-opus-4-1`        | $15   | $75    | $1.50      | $18.75            |
| Claude Opus 4      | `claude-opus-4`          | $15   | $75    | $1.50      | $18.75            |
| Claude Sonnet 4.6  | `claude-sonnet-4-6`      | $3    | $15    | $0.30      | $3.75             |
| Claude Sonnet 4.5  | `claude-sonnet-4-5`      | $3    | $15    | $0.30      | $3.75             |
| Claude Sonnet 4    | `claude-sonnet-4`        | $3    | $15    | $0.30      | $3.75             |
| Claude Haiku 4.5   | `claude-haiku-4-5`       | $1    | $5     | $0.10      | $1.25             |
| Claude Sonnet 3.7  | `claude-3-7-sonnet`      | $3    | $15    | $0.30      | $3.75             |
| Claude Sonnet 3.5  | `claude-3-5-sonnet`      | $3    | $15    | $0.30      | $3.75             |
| Claude Haiku 3.5   | `claude-3-5-haiku`       | $0.80 | $4     | $0.08      | $1                |
| Claude Haiku 3     | `claude-3-haiku`         | $0.25 | $1.25  | $0.03      | $0.30             |
| Claude Opus 3      | `claude-3-opus`          | $15   | $75    | $1.50      | $18.75            |

**Naming convention 注意**: Claude 4.x は `claude-{model}-{version}` (例:
`claude-haiku-4-5-20251001`)、Claude 3.x は `claude-{version}-{model}-{date}`
(例: `claude-3-5-haiku-20241022`) と order が逆。token-boundary prefix match は
両方の convention をそのまま受ける (`_get_pricing` の longest-prefix logic で
正しい model に hit する)。codex review Round 2 / P2 で指摘されたため pin。

## 既知 limitation (cost-calculation-design.md §10 / 設計判断)

- **5-minute cache write のみ採用**: Anthropic は 5m / 1h で cache write 単価が異なる
  (5m: 1.25x base、1h: 2x base)。transcript の `cache_creation_input_tokens` には
  TTL の区別が無い (= 観測不能)。default の 5m を採用。1h 利用が一般化したら
  schema 拡張で別 field 化する将来 issue。
- **`inference_geo` の 1.1x multiplier 未適用**: data-residency 機能 (US-only routing)
  使用時 +10% だが、global routing が default のため大半は影響なし。本 module は
  applied として扱わない (= silent under-estimate になる data-residency ユーザーは
  少数前提)。`assistant_usage.inference_geo` には raw 値が記録される。
- **Sonnet 4.6 fallback**: 未知 model は Sonnet 4.6 rate で計算 (cost-calculation-design.md
  §2 の中央値プロキシ規律)。新 model 登場で UI が壊れない / panel が空にならない安全側設計。
- **値の "参考性"**: cost は実測 token × 価格表掛け算による参考値。価格改定で
  過去値も動く (DB に snapshot しない方針 / cost-calculation-design.md §4 trade-off)。
  監査用途は scope 外。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

# subagent_metrics は repo root 直下なので sys.path 経由で import (既存慣習)
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from subagent_metrics import session_subagent_counts  # noqa: E402


TOP_N_SESSIONS = 20


class ModelPricing(NamedTuple):
    """per-1M-token USD rate. 4 dimension (input / output / cache_read / cache_creation)。"""
    input: float
    output: float
    cache_read: float
    cache_creation: float


# 公式値 verbatim pin (2026-05-06、出典 module docstring)。
# key は **公式 model ID prefix** (date suffix なし)。`_get_pricing` の
# longest-prefix match が `claude-haiku-4-5-20251001` (4.x) も
# `claude-3-5-haiku-20241022` (3.x) も正しい model に解決する。
MODEL_PRICING: dict[str, ModelPricing] = {
    # Claude 4.x: naming convention は `claude-{model}-{version}-{date?}`
    "claude-opus-4-7":   ModelPricing(input=5.00,  output=25.00, cache_read=0.50, cache_creation=6.25),
    "claude-opus-4-6":   ModelPricing(input=5.00,  output=25.00, cache_read=0.50, cache_creation=6.25),
    "claude-opus-4-5":   ModelPricing(input=5.00,  output=25.00, cache_read=0.50, cache_creation=6.25),
    "claude-opus-4-1":   ModelPricing(input=15.00, output=75.00, cache_read=1.50, cache_creation=18.75),
    "claude-opus-4":     ModelPricing(input=15.00, output=75.00, cache_read=1.50, cache_creation=18.75),
    "claude-sonnet-4-6": ModelPricing(input=3.00,  output=15.00, cache_read=0.30, cache_creation=3.75),
    "claude-sonnet-4-5": ModelPricing(input=3.00,  output=15.00, cache_read=0.30, cache_creation=3.75),
    "claude-sonnet-4":   ModelPricing(input=3.00,  output=15.00, cache_read=0.30, cache_creation=3.75),
    "claude-haiku-4-5":  ModelPricing(input=1.00,  output=5.00,  cache_read=0.10, cache_creation=1.25),
    # Claude 3.x: naming convention は `claude-{version}-{model}-{date}` で 4.x と逆順 (codex review Round 2 / P2)。
    # 古い transcript / archive backfill (Issue #104) で当たる可能性があるため明示 pin。
    "claude-3-7-sonnet": ModelPricing(input=3.00,  output=15.00, cache_read=0.30, cache_creation=3.75),
    "claude-3-5-sonnet": ModelPricing(input=3.00,  output=15.00, cache_read=0.30, cache_creation=3.75),
    "claude-3-5-haiku":  ModelPricing(input=0.80,  output=4.00,  cache_read=0.08, cache_creation=1.00),
    "claude-3-haiku":    ModelPricing(input=0.25,  output=1.25,  cache_read=0.03, cache_creation=0.30),
    "claude-3-opus":     ModelPricing(input=15.00, output=75.00, cache_read=1.50, cache_creation=18.75),
}

DEFAULT_PRICING: ModelPricing = MODEL_PRICING["claude-sonnet-4-6"]


def _get_pricing(model: str) -> ModelPricing:
    """model ID → ModelPricing (longest-prefix match + Sonnet fallback)。

    Anthropic は date-suffix 付き ID (`claude-haiku-4-5-20251001`) を返すことがあるため
    完全一致だけでなく **token-boundary prefix match** も許容する。

    Prefix collision の取り扱い: `claude-opus-4` と `claude-opus-4-5` のように
    片方が他方の prefix になる場合は **longest match wins** ($15 と $5 が
    別物なので取り違えると致命的)。
    """
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    # 末尾に "-" を付けて prefix match することで、`claude-opus-4` が
    # `claude-opus-4-5-20260101` の prefix として **同じ** に扱われない:
    # `claude-opus-4-` と `claude-opus-4-5-` の両方が startswith で hit するため
    # longest match を取れば 4-5 が勝つ。
    matches = [p for p in MODEL_PRICING if model.startswith(p + "-")]
    if matches:
        return MODEL_PRICING[max(matches, key=len)]
    return DEFAULT_PRICING


def calculate_message_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
) -> float:
    """1 assistant message の cost (USD) を 4 桁精度で返す純関数。

    数式: `(tokens / 1_000_000) × per-1M-rate` を 4 dimension 合算。
    丸め: USD 0.0001 = 1/100 セント精度 (cost-calculation-design.md §3 と同じ)。
    """
    p = _get_pricing(model)
    cost = (
        (input_tokens / 1_000_000) * p.input
        + (output_tokens / 1_000_000) * p.output
        + (cache_read_tokens / 1_000_000) * p.cache_read
        + (cache_creation_tokens / 1_000_000) * p.cache_creation
    )
    return round(cost, 4)


_FAMILY_CANONICAL_ORDER = ("opus", "sonnet", "haiku")


def infer_model_family(model: str | None) -> str:
    """raw model ID → 'opus' / 'sonnet' / 'haiku' family 文字列.

    substring match (lowercase)。未知 model や空文字 / None は 'sonnet' fallback
    (= `DEFAULT_PRICING` (sonnet-4-6) と意味論を一致させ、cost 推計と family
    rollup の double standard を作らない、cost-calculation-design.md §10 整合)。

    JS 側の `inferModelFamily` (45_renderers_sessions.js) と semantics を 1:1 に
    保つことを load-bearing 規約とする。priority 順は opus → haiku → sonnet で、
    両方を含む model 名 (例: "opus-foo-bar-haiku") は opus を勝者とする
    (= prefix match の `_get_pricing` とは別の抽象階層、Issue #106 plan R7 /
    Phase 1 `TestPricingHelperSemanticsContrast` で test レベルの drift guard 済)。
    """
    m = (model or "").lower()
    if "opus" in m:
        return "opus"
    if "haiku" in m:
        return "haiku"
    if "sonnet" in m:
        return "sonnet"
    return "sonnet"


def aggregate_model_distribution(events: list[dict]) -> dict:
    """events から family rollup の messages / cost 分布を組み立てる純関数 (Issue #106).

    出力形:
        {
            "families": [
                {"family": "opus",   "messages": int, "messages_pct": float,
                 "cost_usd": float, "cost_pct": float},
                ... (sonnet, haiku 同形)
            ],
            "messages_total": int,
            "cost_total": float,
        }

    contract 不変条件:
    - `families` は **常に 3 行** (opus → sonnet → haiku, canonical 固定順)。
      family 数が 0 / 1 / 2 でも未出現 family は messages=0 行で埋める
    - 空 events / `messages_total == 0` のとき `messages_pct = 0.0` (NaN guard)
    - `cost_total == 0` のとき `cost_pct = 0.0` (NaN guard)
    - 未知 model は `infer_model_family` の sonnet fallback で sonnet 行に集計、
      cost は `calculate_message_cost` の DEFAULT_PRICING (sonnet-4-6) で推計
    - `cost_usd` / `cost_total` は 4 桁丸め (cost-calculation-design.md §3 慣習)
    - `*_pct` は **server 側で丸めない**。AC 「合計 ±0.5%」を満たすため UI 側で丸める
    """
    messages: dict[str, int] = {f: 0 for f in _FAMILY_CANONICAL_ORDER}
    cost: dict[str, float] = {f: 0.0 for f in _FAMILY_CANONICAL_ORDER}

    for ev in events:
        if ev.get("event_type") != "assistant_usage":
            continue
        model = ev.get("model", "") or ""
        fam = infer_model_family(model)
        messages[fam] += 1
        cost[fam] += calculate_message_cost(
            model,
            int(ev.get("input_tokens") or 0),
            int(ev.get("output_tokens") or 0),
            int(ev.get("cache_read_tokens") or 0),
            int(ev.get("cache_creation_tokens") or 0),
        )

    messages_total = sum(messages.values())
    cost_total = round(sum(cost.values()), 4)

    families = []
    for fam in _FAMILY_CANONICAL_ORDER:
        m = messages[fam]
        c = round(cost[fam], 4)
        families.append({
            "family": fam,
            "messages": m,
            "messages_pct": (m / messages_total) if messages_total else 0.0,
            "cost_usd": c,
            "cost_pct": (c / cost_total) if cost_total else 0.0,
        })

    return {
        "families": families,
        "messages_total": messages_total,
        "cost_total": cost_total,
    }


def calculate_session_cost(events_for_session: list[dict]) -> float:
    """events list から `assistant_usage` event のみを取り出し session cost を合算。

    cost-calculation-design.md §5 の「model 別集約 → reduce 合算」を踏襲: 各 event は
    既に model 単位の (token, rate) ペアを持つので、per-event cost を sum するだけで
    混在 sum の罠を踏まない (= `calculate_message_cost` 内で model 別 rate が当たる)。
    """
    total = 0.0
    for ev in events_for_session:
        if ev.get("event_type") != "assistant_usage":
            continue
        total += calculate_message_cost(
            ev.get("model", ""),
            int(ev.get("input_tokens") or 0),
            int(ev.get("output_tokens") or 0),
            int(ev.get("cache_read_tokens") or 0),
            int(ev.get("cache_creation_tokens") or 0),
        )
    return round(total, 4)


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _build_session_row(
    session_id: str,
    boundary_evs: list[dict],
    content_evs: list[dict],
    subagent_count: int,
) -> dict | None:
    """1 session 分の events から row dict を組み立てる。

    boundary_evs (= unfiltered full events): `session_start` / `session_end` の lookup 専用。
    period 跨ぎ session で session_start が pre-cutoff にあっても、period content は
    in-period 限定で count しつつ session 自体は render するために boundary だけ
    全期間を見る (codex review Round 1 / period cross-cutoff regression 修正)。

    content_evs (= period-filtered subset): `assistant_usage` / `skill_tool` /
    `user_slash_command` の集計。tokens / cost / models / service_tier_breakdown /
    skill_count はすべて in-period のみで count される。

    boundary_evs に session_start を持たない session (orphan) は None を返して
    caller 側で drop する。
    """
    starts = [e for e in boundary_evs if e.get("event_type") == "session_start"]
    if not starts:
        return None
    ends = [e for e in boundary_evs if e.get("event_type") == "session_end"]
    usage_evs = [e for e in content_evs if e.get("event_type") == "assistant_usage"]
    skill_evs = [
        e for e in content_evs
        if e.get("event_type") in ("skill_tool", "user_slash_command")
    ]

    started_at = starts[0].get("timestamp", "") or ""
    ended_at: str | None = ends[0].get("timestamp") if ends else None
    duration_seconds: float | None = None
    if started_at and ended_at:
        s_dt = _parse_iso(started_at)
        e_dt = _parse_iso(ended_at)
        if s_dt is not None and e_dt is not None:
            duration_seconds = (e_dt - s_dt).total_seconds()

    project = starts[0].get("project") or ""

    models: dict[str, int] = {}
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    service_tier_breakdown: dict[str, int] = {}
    cost_total = 0.0
    for u in usage_evs:
        m = u.get("model", "") or ""
        models[m] = models.get(m, 0) + 1

        in_t = int(u.get("input_tokens") or 0)
        out_t = int(u.get("output_tokens") or 0)
        cr_t = int(u.get("cache_read_tokens") or 0)
        cc_t = int(u.get("cache_creation_tokens") or 0)
        tokens["input"] += in_t
        tokens["output"] += out_t
        tokens["cache_read"] += cr_t
        tokens["cache_creation"] += cc_t

        # service_tier は欠損 / null を breakdown に出さない (real value のみ集計)
        tier = u.get("service_tier")
        if tier:
            service_tier_breakdown[tier] = service_tier_breakdown.get(tier, 0) + 1

        cost_total += calculate_message_cost(m, in_t, out_t, cr_t, cc_t)

    return {
        "session_id": session_id,
        "project": project,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
        "models": models,
        "tokens": tokens,
        "estimated_cost_usd": round(cost_total, 4),
        "service_tier_breakdown": service_tier_breakdown,
        "skill_count": len(skill_evs),
        "subagent_count": subagent_count,
    }


def aggregate_session_breakdown(
    events: list[dict],
    *,
    period_events: list[dict] | None = None,
    now: datetime | None = None,
    top_n: int = TOP_N_SESSIONS,
) -> list[dict]:
    """events から session 単位の summary list を組み立てて返す。

    出力: `[{session_id, project, started_at, ended_at|null,
            duration_seconds|null, models: {name: count},
            tokens: {input, output, cache_read, cache_creation},
            estimated_cost_usd, service_tier_breakdown: {tier: count},
            skill_count, subagent_count}, ...]`

    sort: `started_at` 降順 (最新 session が先頭)。
    cap: `top_n` (default `TOP_N_SESSIONS = 20`)。

    `now` は将来拡張用 (active session の age 表示等) の hook として受けるが、
    本実装では使わない (= active session は `ended_at = null` / `duration_seconds = null`)。

    引数の使い分け (codex review Round 1 / period cross-cutoff regression 対策):
    - `events`: **全期間 unfiltered events**。`session_start` / `session_end` /
      session pool の boundary 解決に使う。period 跨ぎで session_start が pre-cutoff
      に居る場合でも boundary を見失わないようにここで全期間を保持する
    - `period_events` (optional): **period-filtered subset**。`assistant_usage` /
      `skill_tool` / `user_slash_command` の token / cost / models / service_tier /
      skill_count を in-period 限定で count するための入力。`None` のときは
      `events` を流用 (= period 適用なしと同じ振る舞い、後方互換)
    - **session pool 定義**: `period_events` に **少なくとも 1 件** event がある
      session のみ render 対象。session_start が pre-cutoff でも、in-period に
      assistant_usage / skill / subagent event があれば cost/token は in-period
      分だけ集計して render する (= period 跨ぎ visible)。

    subagent_count は `period_events` 経由で計算 (= in-period invocation のみ count)。
    cost / token と semantics を揃えて in-period 限定とする。
    """
    del now  # 将来拡張用 hook、本実装では未使用

    if period_events is None:
        period_events = events

    # Group full events by session for boundary lookup
    full_by_session: dict[str, list[dict]] = {}
    for ev in events:
        sid = ev.get("session_id", "")
        if not sid:
            continue
        full_by_session.setdefault(sid, []).append(ev)

    # Group period events by session for content lookup
    # かつ session pool (= period 内に少なくとも 1 event ある session) を確定
    period_by_session: dict[str, list[dict]] = {}
    for ev in period_events:
        sid = ev.get("session_id", "")
        if not sid:
            continue
        period_by_session.setdefault(sid, []).append(ev)

    # subagent_count は in-period 限定で計算 (cost/token と semantics を揃える)
    subagent_counts = session_subagent_counts(period_events)

    rows: list[dict] = []
    for sid in period_by_session:
        boundary_evs = full_by_session.get(sid, [])
        content_evs = period_by_session[sid]
        row = _build_session_row(
            sid, boundary_evs, content_evs, subagent_counts.get(sid, 0),
        )
        if row is not None:
            rows.append(row)

    rows.sort(key=lambda r: r["started_at"] or "", reverse=True)
    return rows[:top_n]
