"""dashboard/server.py — ローカル HTTP サーバーでダッシュボードを提供する。"""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subagent_metrics import aggregate_subagent_metrics, usage_invocation_events

_DEFAULT_PATH = Path.home() / ".claude" / "transcript-analyzer" / "usage.jsonl"
DATA_FILE = Path(os.environ.get("USAGE_JSONL", str(_DEFAULT_PATH)))

_DEFAULT_ALERTS_PATH = Path.home() / ".claude" / "transcript-analyzer" / "health_alerts.jsonl"
ALERTS_FILE = Path(os.environ.get("HEALTH_ALERTS_JSONL", str(_DEFAULT_ALERTS_PATH)))

PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))

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
    return {
        "last_updated": _now_iso(),
        "total_events": len(usage_events),
        "skill_ranking": aggregate_skills(events),
        "subagent_ranking": aggregate_subagents(events),
        "daily_trend": aggregate_daily(usage_events),
        "project_breakdown": aggregate_projects(usage_events),
        "session_stats": aggregate_session_stats(events),
        "health_alerts": load_health_alerts(),
    }


def render_static_html(data: dict) -> str:
    """データをインライン埋め込みしたスタンドアロン HTML を返す。"""
    json_str = json.dumps(data, ensure_ascii=False).replace("</script>", r"<\/script>")
    inline = f'<script>window.__DATA__ = {json_str};</script>\n'
    return _HTML_TEMPLATE.replace('</head>', inline + '</head>', 1)


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Code Usage</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg-app: #1c1d22;
    --bg-panel: #25272d;
    --bg-panel-2: #2b2e35;
    --bg-elevated: #2f3239;

    --line: #353841;
    --line-strong: #404453;

    --ink: #f1f2f5;
    --ink-soft: #b6bac6;
    --ink-faint: #7e8290;

    --mint: #6fe3c8;
    --mint-soft: #2f6f63;
    --coral: #ff8a76;
    --coral-soft: #803f37;
    --peri: #8aa6ff;
    --peri-soft: #3f4d80;
    --peach: #ffc97a;
    --peach-soft: #80633d;
    --rose: #ff6f9c;
    --rose-soft: #803757;

    --r-lg: 14px;
    --r-md: 10px;
    --r-sm: 6px;
    --r-pill: 8px;

    --ff-sans: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
    --ff-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin:0; padding:0; }
  body {
    font-family: var(--ff-sans);
    font-feature-settings: "ss01", "cv11";
    background: var(--bg-app);
    color: var(--ink);
    min-height: 100vh;
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
    background-image:
      radial-gradient(ellipse 60% 50% at 15% 0%, rgba(111,227,200,0.05), transparent 60%),
      radial-gradient(ellipse 40% 40% at 90% 10%, rgba(138,166,255,0.045), transparent 60%);
  }

  .app {
    max-width: 1480px;
    margin: 0 auto;
    padding: 22px 22px 32px;
  }

  /* ---------- header ---------- */
  .header {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 24px;
    align-items: end;
    margin-bottom: 22px;
    padding-bottom: 18px;
    border-bottom: 1px solid var(--line);
  }
  .header h1 {
    margin: 0 0 4px;
    font-size: 22px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ink);
  }
  .header h1 .accent {
    color: var(--mint);
    font-weight: 700;
  }
  .header .lede {
    color: var(--ink-soft);
    font-size: 13px;
  }
  .header .lede .num { font-family: var(--ff-mono); color: var(--ink); font-weight: 500; }
  .header .meta {
    text-align: right;
    font-size: 11.5px;
    color: var(--ink-faint);
    line-height: 1.7;
  }
  .header .meta .k { display: inline-block; min-width: 78px; color: var(--ink-faint); }
  .header .meta .v { color: var(--ink-soft); font-family: var(--ff-mono); }

  /* ---------- KPI ---------- */
  .kpi-row {
    display: grid;
    grid-template-columns: repeat(8, 1fr);
    gap: 10px;
    margin-bottom: 22px;
  }
  .kpi {
    background: var(--bg-panel);
    border: 1px solid var(--line);
    border-radius: var(--r-md);
    padding: 13px 14px 14px;
    position: relative;
    overflow: visible;
  }
  .kpi::before {
    content: "";
    position: absolute;
    top: 0; left: 14px; right: 14px;
    height: 2px;
    border-radius: 0 0 4px 4px;
    background: var(--mint);
    opacity: 0.85;
  }
  .kpi.c-coral::before { background: var(--coral); }
  .kpi.c-peri::before  { background: var(--peri); }
  .kpi.c-peach::before { background: var(--peach); }
  .kpi.c-mute::before  { background: var(--ink-faint); opacity: 0.5; }
  .kpi.warn::before    { background: var(--peach); }

  .kpi .k-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
  }
  .kpi .k {
    font-size: 10.5px;
    color: var(--ink-faint);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 500;
  }
  .kpi .v {
    font-family: var(--ff-mono);
    font-size: 26px;
    font-weight: 600;
    color: var(--ink);
    margin-top: 6px;
    line-height: 1.05;
    letter-spacing: -0.01em;
  }
  .kpi .v.sm { font-size: 20px; }
  .kpi .s {
    font-size: 11px;
    color: var(--ink-faint);
    margin-top: 6px;
  }
  .kpi .s em { font-style: normal; color: var(--ink-soft); font-family: var(--ff-mono); }
  .kpi.warn .v { color: var(--peach); }
  @media (max-width: 1200px) { .kpi-row { grid-template-columns: repeat(4, 1fr); } }
  @media (max-width: 720px)  { .kpi-row { grid-template-columns: repeat(2, 1fr); } }

  /* ---------- panel ---------- */
  .panel {
    background: var(--bg-panel);
    border: 1px solid var(--line);
    border-radius: var(--r-md);
  }
  .panel-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 14px 18px 12px;
    border-bottom: 1px solid var(--line);
    gap: 12px;
  }
  .panel-head .ttl-wrap {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .panel-head .ttl {
    font-size: 14px;
    font-weight: 600;
    color: var(--ink);
    letter-spacing: -0.005em;
  }
  .panel-head .ttl .dot {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--mint);
    margin-right: 9px;
    vertical-align: 1px;
  }
  .panel-head.c-coral .ttl .dot { background: var(--coral); }
  .panel-head.c-peri  .ttl .dot { background: var(--peri); }
  .panel-head.c-peach .ttl .dot { background: var(--peach); }
  .panel-head .sub {
    font-size: 11.5px;
    color: var(--ink-faint);
    font-family: var(--ff-mono);
  }
  .panel-body { padding: 14px 18px 18px; }

  /* ---------- two-up ---------- */
  .two-up { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; }
  @media (max-width: 1080px) { .two-up { grid-template-columns: 1fr; } }

  /* ---------- ranking rows ---------- */
  .rank-row {
    display: grid;
    grid-template-columns: 24px 1fr 64px 200px;
    gap: 12px;
    align-items: center;
    padding: 8px 0;
    border-bottom: 1px solid var(--line);
    font-size: 13px;
  }
  .rank-row:last-child { border-bottom: none; }
  .rank-row .rk {
    color: var(--ink-faint);
    font-family: var(--ff-mono);
    font-size: 11px;
    font-weight: 500;
    text-align: right;
  }
  .rank-row .rn {
    color: var(--ink);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-family: var(--ff-mono);
    font-size: 12.5px;
  }
  .rank-row .rn .slash { color: var(--mint); font-weight: 600; }
  .rank-row.subagent .rn .slash { color: var(--coral); }
  .rank-row .rn .ns { color: var(--ink-faint); }
  .rank-row .rv {
    color: var(--ink);
    text-align: right;
    font-weight: 600;
    font-size: 14px;
    font-family: var(--ff-mono);
  }
  .gauge-bar {
    height: 8px;
    background: rgba(111,227,200,0.10);
    border-radius: var(--r-pill);
    position: relative;
    overflow: hidden;
  }
  .gauge-bar .gb {
    position: absolute;
    top: 0; bottom: 0; left: 0;
    background: linear-gradient(90deg, var(--mint-soft), var(--mint));
    border-radius: var(--r-pill);
  }
  .rank-row.subagent .gauge-bar { background: rgba(255,138,118,0.10); }
  .rank-row.subagent .gauge-bar .gb { background: linear-gradient(90deg, var(--coral-soft), var(--coral)); }
  .rank-row .meta {
    grid-column: 2 / 5;
    font-size: 10.5px;
    color: var(--ink-faint);
    margin-top: 2px;
    font-family: var(--ff-mono);
  }
  .rank-row .meta .fail { color: var(--rose); font-weight: 500; }

  /* ---------- spark ---------- */
  .spark-wrap {
    display: grid;
    grid-template-columns: 1fr 220px;
    gap: 16px;
    align-items: stretch;
  }
  .spark-svg {
    width: 100%;
    height: 168px;
    display: block;
    border: 1px solid var(--line);
    border-radius: var(--r-md);
    background: var(--bg-panel-2);
  }
  .spark-stats {
    display: grid;
    grid-template-rows: repeat(4, auto);
    gap: 6px;
    align-content: start;
    padding: 12px 14px;
    border: 1px solid var(--line);
    border-radius: var(--r-md);
    background: var(--bg-panel-2);
  }
  .spark-stats .row { display: flex; justify-content: space-between; align-items: baseline; font-size: 12px; padding: 7px 0; border-bottom: 1px solid var(--line); }
  .spark-stats .row:last-child { border-bottom: none; }
  .spark-stats .k { color: var(--ink-faint); font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 500; }
  .spark-stats .v { color: var(--ink); font-weight: 600; font-size: 14px; font-family: var(--ff-mono); }

  /* ---------- projects ---------- */
  .stack {
    display: flex;
    height: 38px;
    border-radius: var(--r-md);
    overflow: hidden;
    margin-bottom: 12px;
    background: var(--bg-panel-2);
  }
  .stack .seg { position: relative; height: 100%; }
  .stack .seg + .seg { border-left: 1px solid rgba(28,29,34,0.4); }
  .stack-legend {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 4px 20px;
    font-size: 12px;
  }
  @media (max-width: 900px) { .stack-legend { grid-template-columns: 1fr; } }
  .stack-legend .leg-row {
    display: grid;
    grid-template-columns: 14px 1fr 64px 56px;
    gap: 10px;
    align-items: center;
    padding: 6px 0;
    border-bottom: 1px solid var(--line);
  }
  .stack-legend .leg-row:last-child { border-bottom: none; }
  .stack-legend .sw { width: 12px; height: 12px; border-radius: var(--r-sm); }
  .stack-legend .pn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink); font-family: var(--ff-mono); font-size: 12px; }
  .stack-legend .pc { color: var(--ink); text-align: right; font-weight: 600; font-family: var(--ff-mono); }
  .stack-legend .pp { color: var(--ink-faint); text-align: right; font-size: 11px; font-family: var(--ff-mono); }

  /* ---------- footer ---------- */
  footer {
    margin-top: 22px;
    padding-top: 14px;
    border-top: 1px solid var(--line);
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--ink-faint);
  }
  footer .accent { color: var(--mint); }

  /* ---------- help button & tooltip ---------- */
  .help-host { position: relative; display: inline-flex; align-items: center; }

  .help-btn {
    appearance: none;
    -webkit-appearance: none;
    border: 1px solid var(--line-strong);
    background: var(--bg-elevated);
    color: var(--ink-faint);
    width: 16px; height: 16px;
    border-radius: 50%;
    padding: 0;
    font-family: var(--ff-sans);
    font-size: 10px;
    font-weight: 600;
    line-height: 1;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: color 120ms ease, background 120ms ease, border-color 120ms ease;
  }
  .help-btn:hover,
  .help-btn:focus-visible {
    color: var(--ink);
    background: var(--bg-panel-2);
    border-color: var(--ink-faint);
    outline: none;
  }
  .help-btn:focus-visible {
    box-shadow: 0 0 0 2px rgba(138,166,255,0.45);
  }
  .help-btn[aria-expanded="true"] {
    color: var(--peri);
    border-color: var(--peri);
    background: rgba(138,166,255,0.10);
  }

  .help-pop {
    position: absolute;
    top: calc(100% + 8px);
    z-index: 50;
    width: max-content;
    max-width: 280px;
    padding: 10px 12px;
    background: var(--bg-elevated);
    border: 1px solid var(--line-strong);
    border-radius: var(--r-md);
    box-shadow:
      0 1px 2px rgba(0,0,0,0.30),
      0 8px 24px rgba(0,0,0,0.35);
    color: var(--ink-soft);
    font-size: 12px;
    line-height: 1.55;
    visibility: hidden;
    opacity: 0;
    transform: translateY(-2px);
    transition: opacity 120ms ease, transform 120ms ease, visibility 120ms;
    pointer-events: none;
  }
  .help-pop[data-place="right"] { left: -8px; }
  .help-pop[data-place="left"]  { right: -8px; }

  .help-host:hover > .help-pop,
  .help-pop[data-open="true"] {
    visibility: visible;
    opacity: 1;
    transform: translateY(0);
    pointer-events: auto;
  }
  .help-pop[data-open="true"] { transition-delay: 0ms; }

  .help-pop .pop-ttl {
    display: block;
    color: var(--ink);
    font-weight: 600;
    font-size: 12px;
    margin-bottom: 4px;
  }
  .help-pop .pop-body { color: var(--ink-soft); }
  .help-pop .pop-body code {
    font-family: var(--ff-mono);
    font-size: 11px;
    background: rgba(138,166,255,0.10);
    color: var(--ink);
    padding: 1px 5px;
    border-radius: 4px;
  }
</style>
</head>
<body>
  <div class="app">
    <header class="header">
      <div>
        <h1><span class="accent">Claude Code</span> Usage Overview</h1>
        <div class="lede">
          <span class="num" id="ledeEvents">—</span> events ·
          <span class="num" id="ledeDays">—</span> days observed ·
          <span class="num" id="ledeProjects">—</span> projects
        </div>
      </div>
      <div class="meta">
        <div><span class="k">最終更新</span><span class="v" id="lastRx">—</span></div>
        <div><span class="k">セッション</span><span class="v" id="sessVal">—</span></div>
      </div>
    </header>

    <div class="kpi-row" id="kpiRow"></div>

    <div class="two-up">
      <div class="panel">
        <div class="panel-head">
          <div class="ttl-wrap">
            <span class="ttl"><span class="dot"></span>スキル利用ランキング</span>
            <span class="help-host">
              <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-rank-skill" data-help-id="hp-rank-skill">?</button>
              <span class="help-pop" id="hp-rank-skill" role="tooltip" data-place="right">
                <span class="pop-ttl">スキル利用ランキング</span>
                <span class="pop-body">PostToolUse(Skill) と UserPromptExpansion / UserPromptSubmit を合算した利用回数の上位 10 件。失敗率は PostToolUseFailure(Skill) ÷ skill_tool 件数。<code>/exit</code> <code>/clear</code> <code>/help</code> などの組み込みコマンドは集計から除外。</span>
              </span>
            </span>
          </div>
          <span class="sub" id="skillSub"></span>
        </div>
        <div class="panel-body" id="skillBody"></div>
      </div>
      <div class="panel">
        <div class="panel-head c-coral">
          <div class="ttl-wrap">
            <span class="ttl"><span class="dot"></span>サブエージェント呼び出し</span>
            <span class="help-host">
              <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-rank-sub" data-help-id="hp-rank-sub">?</button>
              <span class="help-pop" id="hp-rank-sub" role="tooltip" data-place="right">
                <span class="pop-ttl">Subagent 利用ランキング</span>
                <span class="pop-body">PostToolUse(Task|Agent) を invocation 単位で集計。SubagentStart 補助観測と timestamp マージで <code>1 invocation = 1 件</code> に dedup。avg は SubagentStop の end-to-end 所要時間（fallback で start.duration_ms）。</span>
              </span>
            </span>
          </div>
          <span class="sub" id="subSub"></span>
        </div>
        <div class="panel-body" id="subBody"></div>
      </div>
    </div>

    <div class="panel" style="margin-top: 14px;">
      <div class="panel-head c-peri">
        <div class="ttl-wrap">
          <span class="ttl"><span class="dot"></span>日別利用件数の推移</span>
          <span class="help-host">
            <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-daily" data-help-id="hp-daily">?</button>
            <span class="help-pop" id="hp-daily" role="tooltip" data-place="right">
              <span class="pop-ttl">日別利用件数推移</span>
              <span class="pop-body"><code>skill_tool</code> + <code>user_slash_command</code> + subagent invocation の日次集計。session_start や notification は含めない。peak は最大値の日付、active は利用が観測された日数。</span>
            </span>
          </span>
        </div>
        <span class="sub" id="dailySub"></span>
      </div>
      <div class="panel-body">
        <div class="spark-wrap">
          <svg class="spark-svg" id="spark" viewBox="0 0 800 168" preserveAspectRatio="none"></svg>
          <div class="spark-stats" id="sparkStats"></div>
        </div>
      </div>
    </div>

    <div class="panel" style="margin-top: 14px;">
      <div class="panel-head c-peach">
        <div class="ttl-wrap">
          <span class="ttl"><span class="dot"></span>プロジェクト分布</span>
          <span class="help-host">
            <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-proj" data-help-id="hp-proj">?</button>
            <span class="help-pop" id="hp-proj" role="tooltip" data-place="right">
              <span class="pop-ttl">プロジェクト別利用状況</span>
              <span class="pop-body">cwd（プロジェクトディレクトリ）ごとの利用件数。上位 10 件を表示。帯は全体に占める割合、legend の数字は件数と全体比 (%)。</span>
            </span>
          </span>
        </div>
        <span class="sub" id="projSub"></span>
      </div>
      <div class="panel-body">
        <div class="stack" id="stack"></div>
        <div class="stack-legend" id="stackLegend"></div>
      </div>
    </div>

    <footer>
      <span><span class="accent">claude-transcript-analyzer</span> · v0.3</span>
      <span>stdlib only · no third-party js</span>
    </footer>
  </div>

<script>
(async function(){
  function esc(s){ return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"})[c]); }
  function fmtN(n){ return Number(n).toLocaleString('en-US'); }
  function pad(s,n){ s=String(s); return s.length>=n?s:('0'.repeat(n-s.length)+s); }

  let data;
  try {
    data = (typeof window.__DATA__ !== 'undefined')
      ? window.__DATA__
      : await (await fetch('/api/data')).json();
  } catch (e) {
    console.error('データの読み込みに失敗しました:', e);
    return;
  }
  const ss = data.session_stats || {};

  // header
  const dt = new Date(data.last_updated);
  const ts = dt.getUTCFullYear() + '-' + pad(dt.getUTCMonth()+1,2) + '-' + pad(dt.getUTCDate(),2) + ' ' + pad(dt.getUTCHours(),2) + ':' + pad(dt.getUTCMinutes(),2) + ' UTC';
  document.getElementById('lastRx').textContent = ts;
  document.getElementById('sessVal').textContent = (ss.total_sessions || 0) + ' sessions';
  document.getElementById('ledeEvents').textContent = fmtN(data.total_events);
  document.getElementById('ledeDays').textContent = (data.daily_trend||[]).length;
  document.getElementById('ledeProjects').textContent = (data.project_breakdown||[]).length;

  // ---- KPI definitions (ヘルプ本文を含む) ----
  const kpis = [
    { id: 'kpi-total', k: 'total events', v: fmtN(data.total_events), s: '<em>' + (data.daily_trend||[]).length + '</em> 日間の観測', cls: '',
      helpTtl: '総イベント数', helpBody: 'スキル利用と subagent invocation の合計件数。subagent は PostToolUse / SubagentStart の重複発火を <code>1 invocation = 1 件</code> に dedup 済み。session_start や notification は含めない。' },
    { id: 'kpi-skills', k: 'skills', v: (data.skill_ranking||[]).length, s: 'unique kinds', cls: '',
      helpTtl: 'スキル種別数', helpBody: '観測されたスキルの種類数（最大 10 件まで表示）。スキル本体（PostToolUse(Skill)）とユーザー入力のスラッシュコマンド（UserPromptExpansion / Submit）を合算してカウント。' },
    { id: 'kpi-subs', k: 'subagents', v: (data.subagent_ranking||[]).length, s: 'unique kinds', cls: 'c-coral',
      helpTtl: 'Subagent 種別数', helpBody: '観測された subagent の種類数（最大 10 件まで表示）。invocation 単位で dedup 済みのランキングからカウント。' },
    { id: 'kpi-projs', k: 'projects', v: (data.project_breakdown||[]).length, s: 'distinct cwds', cls: 'c-peach',
      helpTtl: 'プロジェクト数', helpBody: '利用が観測されたプロジェクト（cwd 単位、最大 10 件まで表示）。同じディレクトリ配下のセッションは同一プロジェクトとして集計。' },
    { id: 'kpi-sess', k: 'sessions', v: ss.total_sessions || 0, cls: 'c-peri',
      helpTtl: 'セッション数', helpBody: 'SessionStart hook で観測された Claude Code セッションの開始回数。同じ session_id の startup と resume は別セッションとして数える。' },
    { id: 'kpi-resume', k: 'resume rate', v: ss.total_sessions ? Math.round((ss.resume_rate||0)*100)+'%' : '--', sm: true, cls: 'c-mute',
      helpTtl: 'Resume 率', helpBody: 'セッション開始のうち <code>--resume</code> での再開（source="resume"）が占める割合。新規 startup と区別される。' },
    { id: 'kpi-compact', k: 'compactions', v: ss.compact_count || 0, sm: true, cls: 'c-mute',
      helpTtl: 'Compact 数', helpBody: 'コンテキスト自動圧縮（PreCompact hook）の発生回数。auto / manual の両方を合算。' },
    { id: 'kpi-perm', k: 'permission gate', v: ss.permission_prompt_count || 0, sm: true,
      cls: (ss.permission_prompt_count||0) > 5 ? 'warn' : 'c-mute',
      warn: (ss.permission_prompt_count||0) > 5,
      helpTtl: 'Permission Prompt', helpBody: '許可ダイアログ（Notification の type=<code>permission</code> / <code>permission_prompt</code>）の発生回数。多いと作業中の中断が増えていることを示す。' },
  ];

  document.getElementById('kpiRow').innerHTML = kpis.map(g => {
    const popId = 'hp-' + g.id;
    return '<div class="kpi ' + g.cls + (g.warn?' warn':'') + '">' +
      '<div class="k-row">' +
        '<span class="k">' + esc(g.k) + '</span>' +
        '<span class="help-host">' +
          '<button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="' + popId + '" data-help-id="' + popId + '">?</button>' +
          '<span class="help-pop" id="' + popId + '" role="tooltip" data-place="right">' +
            '<span class="pop-ttl">' + esc(g.helpTtl) + '</span>' +
            '<span class="pop-body">' + g.helpBody + '</span>' +
          '</span>' +
        '</span>' +
      '</div>' +
      '<div class="v' + (g.sm?' sm':'') + '">' + g.v + '</div>' +
      (g.s ? '<div class="s">' + g.s + '</div>' : '<div class="s">&nbsp;</div>') +
    '</div>';
  }).join('');

  // ---- ranking renderer ----
  function renderRank(elId, items, kind) {
    const el = document.getElementById(elId);
    if (!items.length) { el.innerHTML = '<div style="color:var(--ink-faint);text-align:center;padding:20px">no data</div>'; return; }
    const max = Math.max(...items.map(i => i.count));
    el.innerHTML = items.map((it, i) => {
      const slash = it.name.startsWith('/');
      let nameHtml;
      if (slash) {
        const rest = it.name.slice(1);
        const colon = rest.indexOf(':');
        if (colon > -1) nameHtml = '<span class="slash">/</span><span class="ns">' + esc(rest.slice(0,colon+1)) + '</span>' + esc(rest.slice(colon+1));
        else nameHtml = '<span class="slash">/</span>' + esc(rest);
      } else {
        nameHtml = esc(it.name);
      }
      const pct = max ? (it.count/max*100) : 0;
      const meta = [];
      if (it.failure_count > 0) meta.push('<span class="fail">FAIL ' + it.failure_count + ' (' + Math.round((it.failure_rate||0)*100) + '%)</span>');
      if (it.avg_duration_ms != null) meta.push('avg ' + (it.avg_duration_ms>=1000? (it.avg_duration_ms/1000).toFixed(1)+'s':Math.round(it.avg_duration_ms)+'ms'));
      const metaHtml = meta.length ? '<div class="meta">' + meta.join(' · ') + '</div>' : '';
      return '<div class="rank-row ' + kind + '">' +
        '<div class="rk">' + pad(i+1,2) + '</div>' +
        '<div class="rn" title="' + esc(it.name) + '">' + nameHtml + '</div>' +
        '<div class="rv">' + fmtN(it.count) + '</div>' +
        '<div class="gauge-bar"><div class="gb" style="width:' + pct + '%"></div></div>' +
        metaHtml +
      '</div>';
    }).join('');
  }
  renderRank('skillBody', data.skill_ranking || [], 'skill');
  renderRank('subBody', data.subagent_ranking || [], 'subagent');
  document.getElementById('skillSub').textContent = 'top ' + (data.skill_ranking||[]).length + ' · max ' + (((data.skill_ranking||[])[0]||{}).count || 0);
  document.getElementById('subSub').textContent = 'top ' + (data.subagent_ranking||[]).length + ' · max ' + (((data.subagent_ranking||[])[0]||{}).count || 0);

  // ---- sparkline ----
  const trend = (data.daily_trend||[]).slice().sort((a,b) => a.date<b.date?-1:1);
  if (trend.length) {
    const W = 800, H = 168, pad_x = 10, pad_y = 18;
    const byDate = new Map(trend.map(d=>[d.date, d.count]));
    const start = new Date(trend[0].date+'T00:00:00Z');
    const end = new Date(trend[trend.length-1].date+'T00:00:00Z');
    const days = [];
    for (let d = new Date(start); d <= end; d.setUTCDate(d.getUTCDate()+1)) {
      const ds = d.toISOString().slice(0,10);
      days.push({ date: ds, count: byDate.get(ds) || 0 });
    }
    const max = Math.max(...days.map(d=>d.count));
    const xs = (i) => pad_x + i * (W - 2*pad_x) / Math.max(1, days.length-1);
    const ys = (c) => H - pad_y - (max ? (c/max) * (H - 2*pad_y) : 0);

    const linePath = days.map((d,i)=> (i===0?'M':'L') + xs(i).toFixed(2) + ' ' + ys(d.count).toFixed(2)).join(' ');
    const areaPath = linePath + ' L' + xs(days.length-1).toFixed(2) + ' ' + (H-pad_y) + ' L' + xs(0).toFixed(2) + ' ' + (H-pad_y) + ' Z';

    const peakIdx = days.findIndex(d => d.count === max);
    const peakDate = days[peakIdx].date;

    const dots = days.map((d,i) => d.count > 0
      ? '<circle cx="' + xs(i).toFixed(2) + '" cy="' + ys(d.count).toFixed(2) + '" r="1.7" fill="#8aa6ff" fill-opacity="0.85"/>'
      : ''
    ).join('');

    const ticks = days.map((d,i) => i % Math.ceil(days.length/8) === 0
      ? '<text x="' + xs(i).toFixed(2) + '" y="' + (H - 3) + '" font-size="9.5" font-family="JetBrains Mono, monospace" fill="#7e8290" text-anchor="middle">' + d.date.slice(5) + '</text>'
      : ''
    ).join('');

    const grid = [0, 0.25, 0.5, 0.75, 1].map(p => {
      const y = pad_y + p*(H - 2*pad_y);
      return '<line x1="0" y1="' + y + '" x2="' + W + '" y2="' + y + '" stroke="rgba(138,166,255,0.06)" stroke-width="1"/>';
    }).join('');

    document.getElementById('spark').innerHTML = '' +
      '<defs><linearGradient id="g1" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0%" stop-color="#8aa6ff" stop-opacity="0.32"/>' +
        '<stop offset="100%" stop-color="#8aa6ff" stop-opacity="0"/>' +
      '</linearGradient></defs>' +
      grid +
      '<path d="' + areaPath + '" fill="url(#g1)"/>' +
      '<path d="' + linePath + '" stroke="#8aa6ff" stroke-width="1.6" fill="none" stroke-linejoin="round" stroke-linecap="round"/>' +
      dots +
      (max > 0 ? (
        '<line x1="' + xs(peakIdx) + '" y1="' + pad_y + '" x2="' + xs(peakIdx) + '" y2="' + (H-pad_y) + '" stroke="#ffc97a" stroke-dasharray="3,3" stroke-width="1" stroke-opacity="0.75"/>' +
        '<text x="' + xs(peakIdx) + '" y="' + (pad_y - 5) + '" font-size="9.5" font-family="JetBrains Mono, monospace" fill="#ffc97a" text-anchor="middle">peak ' + max + '</text>'
      ) : '') +
      ticks;

    const total = days.reduce((s,d)=>s+d.count, 0);
    const avg = total / days.length;
    const active = days.filter(d=>d.count>0).length;
    const sparkStats = [
      { k: 'peak',     v: max + (max > 0 ? ' / ' + peakDate.slice(5) : '') },
      { k: 'avg/day',  v: avg.toFixed(1) },
      { k: 'active',   v: active + '/' + days.length + 'd' },
      { k: 'window',   v: days[0].date.slice(5) + ' → ' + days[days.length-1].date.slice(5) },
    ];
    document.getElementById('sparkStats').innerHTML = sparkStats.map(r =>
      '<div class="row"><span class="k">' + r.k + '</span><span class="v">' + r.v + '</span></div>'
    ).join('');
    document.getElementById('dailySub').textContent = days.length + ' days · ' + active + ' active';
  }

  // ---- projects ----
  const projs = (data.project_breakdown||[]);
  const projTotal = projs.reduce((s,p)=>s+p.count, 0);
  const palette = ['#6fe3c8','#ff8a76','#8aa6ff','#ffc97a','#ff6f9c','#a78bfa','#7ed3a3','#ffa86b','#5dc9e2','#e6a8e8'];
  document.getElementById('stack').innerHTML = projs.map((p, i) => {
    const w = projTotal ? (p.count/projTotal*100) : 0;
    return '<div class="seg" title="' + esc(p.project) + ' · ' + p.count + '" style="background:' + palette[i % palette.length] + ';width:' + w + '%"></div>';
  }).join('');
  document.getElementById('stackLegend').innerHTML = projs.map((p, i) => {
    const pct = projTotal ? (p.count/projTotal*100).toFixed(1) + '%' : '0.0%';
    const display = p.project.length > 28 ? p.project.slice(0,26) + '…' : p.project;
    return '<div class="leg-row">' +
      '<div class="sw" style="background:' + palette[i % palette.length] + '"></div>' +
      '<div class="pn" title="' + esc(p.project) + '">' + esc(display) + '</div>' +
      '<div class="pc">' + fmtN(p.count) + '</div>' +
      '<div class="pp">' + pct + '</div>' +
    '</div>';
  }).join('');
  document.getElementById('projSub').textContent = projs.length + ' projects · Σ ' + fmtN(projTotal);

  // ============================================================
  //  Help popover behavior
  // ============================================================
  function closeAllPops(except) {
    document.querySelectorAll('.help-pop[data-open="true"]').forEach(pop => {
      if (pop === except) return;
      pop.removeAttribute('data-open');
      const btn = document.querySelector('button[data-help-id="' + pop.id + '"]');
      if (btn) btn.setAttribute('aria-expanded', 'false');
    });
  }

  function placePop(pop, btn) {
    pop.setAttribute('data-place', 'right');
    const prevOpen = pop.getAttribute('data-open');
    pop.setAttribute('data-open', 'true');
    const rect = pop.getBoundingClientRect();
    const vw = window.innerWidth;
    if (rect.right > vw - 8) {
      pop.setAttribute('data-place', 'left');
    }
    if (!prevOpen) pop.removeAttribute('data-open');
  }

  document.addEventListener('click', function(e) {
    const btn = e.target.closest('.help-btn');
    if (btn) {
      e.preventDefault();
      e.stopPropagation();
      const popId = btn.getAttribute('data-help-id');
      const pop = document.getElementById(popId);
      const isOpen = pop.getAttribute('data-open') === 'true';
      closeAllPops(isOpen ? null : pop);
      if (isOpen) {
        pop.removeAttribute('data-open');
        btn.setAttribute('aria-expanded', 'false');
      } else {
        placePop(pop, btn);
        pop.setAttribute('data-open', 'true');
        btn.setAttribute('aria-expanded', 'true');
      }
      return;
    }
    if (!e.target.closest('.help-host')) {
      closeAllPops(null);
    }
  });

  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      const opened = document.querySelector('.help-pop[data-open="true"]');
      if (opened) {
        const btn = document.querySelector('button[data-help-id="' + opened.id + '"]');
        closeAllPops(null);
        if (btn) btn.focus();
      }
    }
  });

  window.addEventListener('resize', function() {
    document.querySelectorAll('.help-pop[data-open="true"]').forEach(pop => {
      const btn = document.querySelector('button[data-help-id="' + pop.id + '"]');
      if (btn) placePop(pop, btn);
    });
  });
})();
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/data":
            self._serve_api()
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

    def _serve_html(self):
        body = _HTML_TEMPLATE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("localhost", PORT), DashboardHandler)
    print(f"サーバーが起動しました: http://localhost:{PORT}")
    print("停止するには Ctrl+C を押してください。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nサーバーを停止しました。")
