"""scripts/build_demo_fixture.py — README 用デモスクショ生成 fixture。

`/tmp/demo-fixture/dashboard-demo.html` に 4 タブ全て埋まった合成 dashboard を
出力する。プロジェクト名 / skill 名 / subagent 名は架空のものを使い、リアルな
利用パターンを模倣する (60 日分のヒストリー、共起、トレンド、警告色帯域)。

使い方:
    python3 scripts/build_demo_fixture.py
    open /tmp/demo-fixture/dashboard-demo.html

設計判断:
- `random.Random(42)` で seed 固定、再実行で同じ画面が出る (PR diff 安定)
- skill/project 名は意味の通る demo 用 (acme-saas, frontend-design など)
- 全 4 panel が meaningful に埋まる: Quality は failure / permission / compact 全てに
  しっかり数字を作る
- Surface の hibernating panel 用に SKILL.md fixture を `skills/` 配下に作成
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURE_DIR = Path("/tmp/demo-fixture")
USAGE_JSONL = FIXTURE_DIR / "usage.jsonl"
SKILLS_DIR = FIXTURE_DIR / "skills"
ALERTS_FILE = FIXTURE_DIR / "health_alerts.jsonl"
OUTPUT_HTML = FIXTURE_DIR / "dashboard-demo.html"

NOW = datetime.now(timezone.utc).replace(microsecond=0)
RNG = random.Random(42)

PROJECTS = [
    "acme-saas",
    "billing-service",
    "mobile-app",
    "docs-site",
    "internal-tools",
    "data-pipeline",
    "growth-experiments",
    "infra-iac",
]

# (name, weight) — weight は事前に整えた利用頻度
SKILLS_LLM = [
    ("frontend-design", 110),
    ("codex-review", 88),
    ("simplify", 72),
    ("user-story-creation", 64),
    ("python-testing-patterns", 55),
    ("claude-api", 48),
    ("update-config", 41),
    ("verify-bot-review", 38),
    ("webapp-testing", 30),
    ("chrome-devtools-mcp", 27),
    ("security-review", 22),
    ("llm-doc-authoring", 18),
    ("restful-controllers", 14),
    ("skill-creator", 11),
    ("claude-code-harness-reference", 8),
]

SLASH_COMMANDS = [
    ("/insights", 46),
    ("/usage-summary", 22),
    ("/usage-dashboard", 18),
    ("/codex-review", 14),  # dual-mode 検証
    ("/security-review", 9),
    ("/skill-creator", 6),
]

SUBAGENTS = [
    ("Explore", 220, 4500, 0.04),  # name, count, avg_ms, failure_rate
    ("general-purpose", 95, 12500, 0.08),
    ("Plan", 78, 7800, 0.03),
    ("plan-reviewer", 62, 5400, 0.02),
    ("ui-designer", 48, 18000, 0.06),
    ("claude-code-guide", 31, 3200, 0.01),
    ("skill-reviewer", 22, 6500, 0.05),
    ("statusline-setup", 15, 2100, 0.0),
    ("worktree-cleanup", 11, 1900, 0.18),  # 高失敗率を演出
]

# Hibernating panel 用 — 古い install 日を意図的に作る
HIBERNATING_SKILLS = [
    # (name, mtime_days_ago, last_use_days_ago_or_None)
    ("legacy-migration-helper", 92, None),     # idle (unused old install)
    ("ruby-gem-security-triage", 38, 35),       # idle (>30 days)
    ("stacked-pr-workflow", 60, 21),            # resting (15-30d)
    ("cross-os-python-portability", 58, 18),    # resting
    ("fork-and-detach-launcher-pattern", 4, None),  # warming_up
    ("frontend-tooltip-patterns", 11, None),    # warming_up boundary 内
    ("rails-restful-controllers", 250, None),   # 死蔵 (very old)
]


def iso(days_ago: float, hours_ago: float = 0.0, *, jitter_min: int = 0) -> str:
    """`days_ago` 日 + `hours_ago` 時間前 + jitter 分 の ISO 文字列を返す。"""
    minutes = jitter_min if jitter_min else RNG.randint(0, 59)
    t = NOW - timedelta(days=days_ago, hours=hours_ago, minutes=minutes,
                        seconds=RNG.randint(0, 59))
    return t.isoformat()


def session_id(seed: int) -> str:
    """deterministic な session id を seed から作る。"""
    r = random.Random(seed)
    return "{:08x}-{:04x}-{:04x}-{:04x}-{:012x}".format(
        r.getrandbits(32), r.getrandbits(16), r.getrandbits(16),
        r.getrandbits(16), r.getrandbits(48),
    )


def pick_project(rng: random.Random) -> str:
    # 上位 3 プロジェクトに重み付け (UI 上の stack bar が見栄え良くなる)
    return rng.choices(PROJECTS, weights=[10, 7, 5, 4, 3, 3, 2, 2])[0]


def pick_hour_of_day(rng: random.Random) -> int:
    # 平日業務時間帯に重み (heatmap が見栄え良くなる)
    return rng.choices(
        range(24),
        weights=[1,1,1,1, 1,1,2,3, 6,9,11,12, 11,10,11,12, 13,12,9,6, 4,3,2,1],
    )[0]


def make_skill_tool(skill: str, days_ago: float, *, project: str, sess: str,
                    success: bool = True, hour: int | None = None) -> dict:
    h = hour if hour is not None else 12
    duration = RNG.randint(150, 1200)
    return {
        "event_type": "skill_tool",
        "skill": skill,
        "args": "",
        "project": project,
        "session_id": sess,
        "timestamp": iso(days_ago, hours_ago=24 - h),
        "success": success,
        "duration_ms": duration,
        "permission_mode": "default",
        "tool_use_id": f"toolu_{RNG.randint(10**8, 10**9)}",
    }


def make_slash(skill: str, days_ago: float, *, project: str, sess: str,
               hour: int | None = None) -> dict:
    h = hour if hour is not None else 12
    return {
        "event_type": "user_slash_command",
        "skill": skill,
        "args": "",
        "source": "expansion",
        "project": project,
        "session_id": sess,
        "timestamp": iso(days_ago, hours_ago=24 - h),
    }


def make_subagent_pair(name: str, days_ago: float, *, project: str, sess: str,
                       avg_ms: int, success: bool = True,
                       hour: int | None = None) -> list[dict]:
    """subagent_start + subagent_stop ペアを返す (1 invocation)。"""
    h = hour if hour is not None else 12
    duration_ms = max(50, int(RNG.gauss(avg_ms, avg_ms * 0.35)))
    end_ts = iso(days_ago, hours_ago=24 - h)
    tool_id = f"toolu_{RNG.randint(10**8, 10**9)}"
    return [
        {
            "event_type": "subagent_start",
            "subagent_type": name,
            "project": project,
            "session_id": sess,
            "timestamp": end_ts,  # PostToolUse 由来 = 終了時刻
            "success": success,
            "duration_ms": duration_ms,
            "tool_use_id": tool_id,
        },
        {
            "event_type": "subagent_stop",
            "subagent_type": name,
            "project": project,
            "session_id": sess,
            "timestamp": end_ts,
            "success": success,
            "duration_ms": duration_ms,
        },
    ]


def make_session_start(days_ago: float, *, sess: str, source: str = "startup",
                       hour: int | None = None) -> dict:
    h = hour if hour is not None else 9
    return {
        "event_type": "session_start",
        "source": source,
        "project": pick_project(RNG),
        "session_id": sess,
        "timestamp": iso(days_ago, hours_ago=24 - h),
    }


def make_compact_start(days_ago: float, *, sess: str, project: str,
                       hour: int | None = None) -> dict:
    h = hour if hour is not None else 14
    return {
        "event_type": "compact_start",
        "project": project,
        "session_id": sess,
        "timestamp": iso(days_ago, hours_ago=24 - h),
    }


def make_permission_notif(days_ago: float, *, sess: str,
                          hour: int | None = None) -> dict:
    h = hour if hour is not None else 12
    return {
        "event_type": "notification",
        "notification_type": "permission",
        "session_id": sess,
        "timestamp": iso(days_ago, hours_ago=24 - h),
    }


def build_events() -> list[dict]:
    events: list[dict] = []
    sess_counter = [1]

    def next_sess() -> str:
        sid = session_id(sess_counter[0])
        sess_counter[0] += 1
        return sid

    # ── (1) skill_tool / slash_command を 60 日分散 ──────────
    for skill, weight in SKILLS_LLM:
        # weight 件を 60 日に分散。直近 30 日に bias をかけて trend 多様性を演出。
        for _ in range(weight):
            # 一部 skill は decelerating / accelerating になるよう bias
            if skill == "skill-creator":
                d = RNG.choices(range(1, 60), weights=[1] * 30 + [3] * 29)[0]
            elif skill == "frontend-design":
                d = RNG.choices(range(1, 60), weights=[3] * 30 + [1] * 29)[0]
            elif skill == "claude-code-harness-reference":
                d = RNG.randint(1, 60)
            else:
                d = RNG.randint(1, 60)
            project = pick_project(RNG)
            sess = next_sess() if RNG.random() < 0.7 else session_id(
                sess_counter[0] - RNG.randint(1, min(5, sess_counter[0]))
            )
            success = RNG.random() > 0.06  # 6% failure
            hour = pick_hour_of_day(RNG)
            events.append(make_skill_tool(
                skill, d, project=project, sess=sess, success=success, hour=hour))

    for slash, weight in SLASH_COMMANDS:
        for _ in range(weight):
            d = RNG.randint(1, 60)
            project = pick_project(RNG)
            sess = next_sess() if RNG.random() < 0.5 else session_id(
                sess_counter[0] - RNG.randint(1, min(5, sess_counter[0]))
            )
            hour = pick_hour_of_day(RNG)
            events.append(make_slash(
                slash, d, project=project, sess=sess, hour=hour))

    # ── (2) subagent invocation pairs ────────────────────
    for name, count, avg_ms, fail_rate in SUBAGENTS:
        for _ in range(count):
            d = RNG.uniform(1, 60)
            project = pick_project(RNG)
            sess = next_sess() if RNG.random() < 0.6 else session_id(
                sess_counter[0] - RNG.randint(1, min(8, sess_counter[0]))
            )
            success = RNG.random() > fail_rate
            hour = pick_hour_of_day(RNG)
            events.extend(make_subagent_pair(
                name, d, project=project, sess=sess, avg_ms=avg_ms,
                success=success, hour=hour))

    # ── (3) session_start events (compact density 用 0-bucket 確保) ──
    # 全 session に session_start を 1 件付ける (collected so far から unique を取る)
    seen_sessions = {ev["session_id"] for ev in events if ev.get("session_id")}
    for sess in seen_sessions:
        # ランダムに 0〜60 日前
        d = RNG.uniform(1, 60)
        events.append(make_session_start(d, sess=sess))

    # ── (4) compact_start events (Quality / compact density panel) ──
    # 一部の session に compact 1〜4 回を割り振る (worst session 演出)
    sessions_for_compact = list(seen_sessions)
    RNG.shuffle(sessions_for_compact)
    # histogram: 1 compact = 35 sessions, 2 = 18, 3+ = 9
    pools = [(1, 35), (2, 18), (3, 5), (4, 3), (5, 1)]
    pool_idx = 0
    for n, k in pools:
        for _ in range(k):
            if pool_idx >= len(sessions_for_compact):
                break
            sess = sessions_for_compact[pool_idx]
            pool_idx += 1
            project = RNG.choice(PROJECTS)
            for _ in range(n):
                d = RNG.uniform(1, 50)
                events.append(make_compact_start(d, sess=sess, project=project))

    # ── (5) permission notifications (Quality / permission breakdown) ──
    # skill_tool / subagent invocation の execution interval にカブるように作る。
    # 単純に各 skill_tool / subagent_start イベントから一定確率で生成して同 session の
    # 同 timestamp 直後に notification を打ち込む。
    skill_events_for_perm = [ev for ev in events if ev.get("event_type") == "skill_tool"]
    subagent_starts = [ev for ev in events if ev.get("event_type") == "subagent_start"]

    perm_skill_targets = {
        "frontend-design": 0.18,
        "update-config": 0.55,  # 高 rate を演出
        "webapp-testing": 0.42,
        "chrome-devtools-mcp": 0.30,
        "security-review": 0.25,
        "claude-api": 0.10,
    }
    perm_subagent_targets = {
        "Explore": 0.12,
        "ui-designer": 0.45,
        "general-purpose": 0.20,
        "worktree-cleanup": 0.30,
    }

    for ev in skill_events_for_perm:
        p = perm_skill_targets.get(ev.get("skill", ""), 0.04)
        if RNG.random() < p:
            ts = datetime.fromisoformat(ev["timestamp"]) + timedelta(
                milliseconds=RNG.randint(50, 500)
            )
            events.append({
                "event_type": "notification",
                "notification_type": "permission",
                "session_id": ev["session_id"],
                "timestamp": ts.isoformat(),
            })

    for ev in subagent_starts:
        p = perm_subagent_targets.get(ev.get("subagent_type", ""), 0.03)
        if RNG.random() < p:
            # subagent_start.timestamp は終了時刻なので、duration を遡って interval 内 ts を作る
            end = datetime.fromisoformat(ev["timestamp"])
            duration_ms = ev.get("duration_ms", 1000)
            offset_ms = RNG.randint(0, max(1, duration_ms - 100))
            ts = end - timedelta(milliseconds=offset_ms)
            events.append({
                "event_type": "notification",
                "notification_type": "permission",
                "session_id": ev["session_id"],
                "timestamp": ts.isoformat(),
            })

    return events


def build_skills_dir() -> None:
    """Surface tab の hibernating panel 用に SKILL.md fixture を作る。

    LLM 利用 skill の中で **active 内の skill** には mtime を 1〜10 日前で書き、
    14 日以内 last_use により hibernating から除外される (= active_excluded_count に
    寄与)。ヒューマンが UI に「hibernating だけ表示中」を読めるようになる。

    HIBERNATING_SKILLS で定義した古い skill は status 別に並ぶ。
    """
    if SKILLS_DIR.exists():
        shutil.rmtree(SKILLS_DIR)
    SKILLS_DIR.mkdir(parents=True)

    # active_excluded を寄与させる: 主要 skill は最近 mtime を持つ
    for skill_name, _ in SKILLS_LLM:
        d = SKILLS_DIR / skill_name
        d.mkdir(parents=True, exist_ok=True)
        f = d / "SKILL.md"
        f.write_text(
            f"---\nname: {skill_name}\ndescription: demo fixture\n---\n",
            encoding="utf-8",
        )
        ts = (NOW - timedelta(days=RNG.randint(1, 30))).timestamp()
        os.utime(f, (ts, ts))

    # Hibernating 専用 skill (usage events も後で別途付与)
    for skill_name, mtime_days, _last in HIBERNATING_SKILLS:
        d = SKILLS_DIR / skill_name
        d.mkdir(parents=True, exist_ok=True)
        f = d / "SKILL.md"
        f.write_text(
            f"---\nname: {skill_name}\ndescription: demo fixture\n---\n",
            encoding="utf-8",
        )
        ts = (NOW - timedelta(days=mtime_days)).timestamp()
        os.utime(f, (ts, ts))


def add_hibernating_usage(events: list[dict]) -> list[dict]:
    """HIBERNATING_SKILLS の last_use を usage event で表現する。"""
    extra: list[dict] = []
    for skill_name, _, last_use_days in HIBERNATING_SKILLS:
        if last_use_days is None:
            continue
        extra.append({
            "event_type": "skill_tool",
            "skill": skill_name,
            "args": "",
            "project": pick_project(RNG),
            "session_id": session_id(99000 + hash(skill_name) % 1000),
            "timestamp": iso(last_use_days),
            "success": True,
            "duration_ms": 200,
            "permission_mode": "default",
            "tool_use_id": "toolu_demo",
        })
    return events + extra


def main() -> None:
    if FIXTURE_DIR.exists():
        # skills dir 以外は消す
        for child in FIXTURE_DIR.iterdir():
            if child.is_dir() and child.name != "skills":
                shutil.rmtree(child)
            elif child.is_file():
                child.unlink()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    build_skills_dir()
    events = build_events()
    events = add_hibernating_usage(events)

    # JSONL 書き出し (timestamp で sort)
    events.sort(key=lambda e: e.get("timestamp", ""))
    with USAGE_JSONL.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    # health alerts は空でいい (panel 自体非表示で良い)
    ALERTS_FILE.write_text("", encoding="utf-8")

    # dashboard server を fixture に向ける
    os.environ["USAGE_JSONL"] = str(USAGE_JSONL)
    os.environ["SKILLS_DIR"] = str(SKILLS_DIR)
    os.environ["HEALTH_ALERTS_JSONL"] = str(ALERTS_FILE)

    from dashboard.server import build_dashboard_data, render_static_html

    data = build_dashboard_data(events)
    html = render_static_html(data)
    OUTPUT_HTML.write_text(html, encoding="utf-8")

    # confirm summary
    print("=" * 64)
    print("Demo fixture HTML 生成完了")
    print("=" * 64)
    print(f"出力:        {OUTPUT_HTML}")
    print(f"events:      {len(events)} 件")
    print(f"skills_dir:  {SKILLS_DIR}")
    print()
    print(f"total_events:        {data['total_events']}")
    print(f"skill_kinds_total:   {data['skill_kinds_total']}")
    print(f"subagent_kinds_total:{data['subagent_kinds_total']}")
    print(f"project_total:       {data['project_total']}")
    print()
    print("Skill ranking (top 5):")
    for r in data["skill_ranking"][:5]:
        print(f"  {r['name']:<35} count={r['count']:>3}")
    print("Subagent ranking (top 5):")
    for r in data["subagent_ranking"][:5]:
        print(f"  {r['name']:<25} count={r['count']:>3} avg={r.get('avg_duration_ms')}")
    print()
    print(f"open: file://{OUTPUT_HTML}")


if __name__ == "__main__":
    main()
