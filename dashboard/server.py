"""dashboard/server.py — ローカル HTTP サーバーでダッシュボードを提供する。"""
import json
import os
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_DEFAULT_PATH = Path(__file__).parent.parent / "data" / "usage.jsonl"
DATA_FILE = Path(os.environ.get("USAGE_JSONL", str(_DEFAULT_PATH)))

PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))

TOP_N = 10


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
        events.append(json.loads(line))
    return events


def aggregate_skills(events: list[dict], top_n: int = TOP_N) -> list[dict]:
    counter: Counter = Counter()
    for ev in events:
        if ev.get("event_type") in ("skill_tool", "user_slash_command"):
            key = ev.get("skill", "")
            if key:
                counter[key] += 1
    return [{"name": name, "count": count} for name, count in counter.most_common(top_n)]


def aggregate_subagents(events: list[dict], top_n: int = TOP_N) -> list[dict]:
    counter: Counter = Counter()
    for ev in events:
        if ev.get("event_type") == "subagent_start":
            key = ev.get("subagent_type", "")
            if key:
                counter[key] += 1
    return [{"name": name, "count": count} for name, count in counter.most_common(top_n)]


def aggregate_daily(events: list[dict]) -> list[dict]:
    counter: Counter = Counter()
    for ev in events:
        ts = ev.get("timestamp", "")
        if ts and len(ts) >= 10:
            date = ts[:10]
            counter[date] += 1
    return [{"date": date, "count": count} for date, count in sorted(counter.items())]


def aggregate_projects(events: list[dict], top_n: int = TOP_N) -> list[dict]:
    counter: Counter = Counter()
    for ev in events:
        project = ev.get("project", "")
        if project:
            counter[project] += 1
    return [{"project": project, "count": count} for project, count in counter.most_common(top_n)]


def build_dashboard_data(events: list[dict]) -> dict:
    return {
        "last_updated": _now_iso(),
        "total_events": len(events),
        "skill_ranking": aggregate_skills(events),
        "subagent_ranking": aggregate_subagents(events),
        "daily_trend": aggregate_daily(events),
        "project_breakdown": aggregate_projects(events),
    }


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Claude Code Usage Dashboard</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, -apple-system, sans-serif; background: #0f0f1a; color: #e2e8f0; min-height: 100vh; }
    header { background: #1a1a2e; border-bottom: 1px solid #2d2d4e; padding: 1.25rem 2rem; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 0.5rem; }
    header h1 { font-size: 1.25rem; color: #818cf8; letter-spacing: -0.01em; }
    .meta { font-size: 0.8rem; color: #64748b; }
    main { padding: 1.5rem 2rem; max-width: 1200px; margin: 0 auto; }
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
    .stat-card { background: #1a1a2e; border: 1px solid #2d2d4e; border-radius: 8px; padding: 1rem 1.25rem; }
    .stat-card .label { font-size: 0.7rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.06em; }
    .stat-card .value { font-size: 1.8rem; font-weight: 700; color: #818cf8; margin-top: 0.4rem; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; margin-bottom: 1.25rem; }
    @media (max-width: 768px) { .grid-2 { grid-template-columns: 1fr; } }
    .card { background: #1a1a2e; border: 1px solid #2d2d4e; border-radius: 8px; padding: 1.25rem 1.5rem; }
    .card + .card { margin-top: 1.25rem; }
    .card h2 { font-size: 0.85rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 1rem; padding-bottom: 0.6rem; border-bottom: 1px solid #2d2d4e; }
    .no-data { color: #475569; font-size: 0.875rem; text-align: center; padding: 1.5rem 0; }
    .bar-row { display: flex; align-items: center; gap: 0.625rem; margin-bottom: 0.5rem; }
    .bar-label { font-size: 0.775rem; color: #94a3b8; width: 130px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace, monospace; }
    .bar-track { flex: 1; height: 18px; background: #0f0f1a; border-radius: 4px; overflow: hidden; }
    .bar-fill { height: 100%; border-radius: 4px; transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1); min-width: 2px; }
    .bar-fill-skill  { background: linear-gradient(90deg, #4f46e5, #818cf8); }
    .bar-fill-subagent { background: linear-gradient(90deg, #0891b2, #38bdf8); }
    .bar-fill-project { background: linear-gradient(90deg, #059669, #34d399); }
    .bar-fill-daily { background: linear-gradient(90deg, #d97706, #fbbf24); }
    .bar-count { font-size: 0.75rem; color: #64748b; width: 2rem; text-align: right; flex-shrink: 0; font-family: ui-monospace, monospace; }
  </style>
</head>
<body>
  <header>
    <h1>Claude Code Usage Dashboard</h1>
    <div class="meta">最終更新: <span id="last-updated">読み込み中...</span></div>
  </header>
  <main>
    <div class="stats-grid">
      <div class="stat-card">
        <div class="label">総イベント数</div>
        <div class="value" id="total-events">-</div>
      </div>
      <div class="stat-card">
        <div class="label">スキル種別数</div>
        <div class="value" id="skill-count">-</div>
      </div>
      <div class="stat-card">
        <div class="label">Subagent 種別数</div>
        <div class="value" id="subagent-count">-</div>
      </div>
      <div class="stat-card">
        <div class="label">プロジェクト数</div>
        <div class="value" id="project-count">-</div>
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <h2>スキル利用ランキング</h2>
        <div id="skill-chart"></div>
      </div>
      <div class="card">
        <h2>Subagent 利用ランキング</h2>
        <div id="subagent-chart"></div>
      </div>
    </div>

    <div class="card">
      <h2>日別利用件数推移</h2>
      <div id="daily-chart"></div>
    </div>

    <div class="card" style="margin-top:1.25rem">
      <h2>プロジェクト別利用状況</h2>
      <div id="project-chart"></div>
    </div>
  </main>

  <script>
    function renderBarChart(containerId, items, nameKey, countKey, fillClass) {
      const container = document.getElementById(containerId);
      if (!items || items.length === 0) {
        container.innerHTML = '<p class="no-data">データなし</p>';
        return;
      }
      const max = Math.max(...items.map(i => i[countKey]));
      container.innerHTML = items.map(item => {
        const pct = max > 0 ? (item[countKey] / max * 100) : 0;
        return [
          '<div class="bar-row">',
          '  <div class="bar-label" title="' + item[nameKey] + '">' + item[nameKey] + '</div>',
          '  <div class="bar-track">',
          '    <div class="bar-fill ' + fillClass + '" style="width:' + pct + '%"></div>',
          '  </div>',
          '  <div class="bar-count">' + item[countKey] + '</div>',
          '</div>',
        ].join('');
      }).join('');
    }

    async function loadAndRender() {
      try {
        const resp = await fetch('/api/data');
        const data = await resp.json();

        document.getElementById('last-updated').textContent =
          new Date(data.last_updated).toLocaleString('ja-JP');
        document.getElementById('total-events').textContent = data.total_events;
        document.getElementById('skill-count').textContent = data.skill_ranking.length;
        document.getElementById('subagent-count').textContent = data.subagent_ranking.length;
        document.getElementById('project-count').textContent = data.project_breakdown.length;

        renderBarChart('skill-chart', data.skill_ranking, 'name', 'count', 'bar-fill-skill');
        renderBarChart('subagent-chart', data.subagent_ranking, 'name', 'count', 'bar-fill-subagent');
        renderBarChart('daily-chart', data.daily_trend, 'date', 'count', 'bar-fill-daily');
        renderBarChart('project-chart', data.project_breakdown, 'project', 'count', 'bar-fill-project');
      } catch (e) {
        console.error('データの読み込みに失敗しました:', e);
      }
    }

    loadAndRender();
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

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("localhost", PORT), DashboardHandler)
    print(f"サーバーが起動しました: http://localhost:{PORT}")
    print("停止するには Ctrl+C を押してください。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nサーバーを停止しました。")
