"""scripts/build_surface_fixture.py — Surface タブ全パターン visual fixture 生成。

Issue #74 の C2 判断 (autonomy_rate 警告色閾値) を含む UI 確認のため、Panel 1
(起動経路 / autonomy_rate 全帯域 + 全 mode)、Panel 2 (lifecycle / 全 trend)、
Panel 3 (hibernating / 全 status + active 除外確認 + boundary) を網羅した
fixture を /tmp/issue-74-fixture/ に生成する。

使い方:
    python3 scripts/build_surface_fixture.py
    open /tmp/issue-74-fixture/surface-fixture.html

設計判断:
- 各 row で「何を確認するか」が一目でわかるよう skill 名を意味のある label に
  (例 "panel1-dual-rate-100" / "panel3-warming-boundary-14d")。
- 全 row が同 fixture HTML 内に並ぶことで色 / 形 / 余白を一括目視できる。
- 環境隔離のため SKILLS_DIR / USAGE_JSONL を tmp に向ける。本物の
  ~/.claude/skills は触らない。
"""
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURE_DIR = Path("/tmp/issue-74-fixture")
USAGE_JSONL = FIXTURE_DIR / "usage.jsonl"
SKILLS_DIR = FIXTURE_DIR / "skills"
OUTPUT_HTML = FIXTURE_DIR / "surface-fixture.html"

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _iso(days_ago: int, hours_ago: int = 0) -> str:
    return (NOW - timedelta(days=days_ago, hours=hours_ago)).isoformat()


def _tool(skill: str, *, days_ago: int = 1, success: bool = True) -> dict:
    return {
        "event_type": "skill_tool",
        "skill": skill,
        "project": "fixture",
        "session_id": "fixture",
        "timestamp": _iso(days_ago),
        "success": success,
        "duration_ms": 100,
        "permission_mode": "default",
        "tool_use_id": "fixture",
    }


def _slash(skill: str, *, days_ago: int = 1, source: str = "expansion") -> dict:
    return {
        "event_type": "user_slash_command",
        "skill": skill,
        "args": "",
        "source": source,
        "project": "fixture",
        "session_id": "fixture",
        "timestamp": _iso(days_ago),
    }


def _make_skill(skills_dir: Path, name: str, mtime_days_ago: int) -> None:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / "SKILL.md"
    f.write_text("---\nname: " + name + "\ndescription: fixture\n---\n", encoding="utf-8")
    ts = (NOW - timedelta(days=mtime_days_ago)).timestamp()
    os.utime(f, (ts, ts))


def _spread(skill: str, n: int, *, slash: bool = False) -> list[dict]:
    """`skill` の event を `n` 件、直近 30d に均等分散 + first を 31d 前に 1 件置く。

    Panel 2 で `stable` 扱いになるように設計 (recent_rate ≈ overall_rate)。
    `slash=True` の場合は user_slash_command を出す。
    """
    events: list[dict] = []
    factory = (lambda d: _slash("/" + skill, days_ago=d)) if slash else \
              (lambda d: _tool(skill, days_ago=d))
    if n <= 0:
        return events
    # first event を 31 日前に置いて Panel 2 で trend 判定対象にする
    events.append(factory(31))
    # 残り (n-1) 件は直近 30 日に均等分散 (1〜29 日前)
    remaining = n - 1
    if remaining > 0:
        for i in range(remaining):
            day = 1 + (i * 28 // max(remaining, 1))  # 1..29 に均等分散
            events.append(factory(day))
    return events


def build_panel_1_events() -> list[dict]:
    """Panel 1 — 全 mode + autonomy_rate 全帯域 (0.99 / 0.96 / 0.75 / 0.50 / 0.49 / 0.10 / 0.01)。

    各 skill のイベントを `_spread` で直近 30d 内に分散し、Panel 2 では `stable` に
    倒れるように設計 (= Panel 1 の表示が Panel 2 の trend 検証を邪魔しない)。
    """
    events: list[dict] = []
    # dual rate=0.99: tool=99, slash=1
    events += _spread("panel1-dual-rate-99", 99)
    events += _spread("panel1-dual-rate-99", 1, slash=True)

    # dual rate=0.96: tool=24, slash=1
    events += _spread("panel1-dual-rate-96", 24)
    events += _spread("panel1-dual-rate-96", 1, slash=True)

    # dual rate=0.75: tool=3, slash=1
    events += _spread("panel1-dual-rate-75", 3)
    events += _spread("panel1-dual-rate-75", 1, slash=True)

    # dual rate=0.50 (boundary): tool=5, slash=5
    events += _spread("panel1-dual-rate-50-boundary", 5)
    events += _spread("panel1-dual-rate-50-boundary", 5, slash=True)

    # dual rate=0.49 (peach 警告 ON 境界): tool=49, slash=51
    events += _spread("panel1-dual-rate-49", 49)
    events += _spread("panel1-dual-rate-49", 51, slash=True)

    # dual rate=0.10: tool=1, slash=9
    events += _spread("panel1-dual-rate-10", 1)
    events += _spread("panel1-dual-rate-10", 9, slash=True)

    # dual rate=0.01: tool=1, slash=99
    events += _spread("panel1-dual-rate-01", 1)
    events += _spread("panel1-dual-rate-01", 99, slash=True)

    # llm-only: tool=12, slash=0
    events += _spread("panel1-llm-only", 12)

    # user-only: tool=0, slash=8
    events += _spread("panel1-user-only", 8, slash=True)

    return events


def build_panel_2_events() -> list[dict]:
    """Panel 2 — 全 trend (accelerating / stable / decelerating / new)。"""
    events: list[dict] = []
    # accelerating: 60 日前 first / 直近 30d で 25 件 / それ以前は薄く
    events += [_tool("panel2-accelerating", days_ago=60)]
    events += [_tool("panel2-accelerating", days_ago=55)]
    events += [_tool("panel2-accelerating", days_ago=45)]
    events += [_tool("panel2-accelerating", days_ago=35)]
    events += [_tool("panel2-accelerating", days_ago=31)]
    for d in range(30, 5, -1):  # 25 件 in 30d
        events += [_tool("panel2-accelerating", days_ago=d)]

    # stable: 60 日 evenly に 60 件
    for d in range(60, 0, -1):
        events += [_tool("panel2-stable", days_ago=d)]

    # decelerating: 60 日前から evenly 60 件、直近 30 日には 5 件のみ
    for d in range(60, 30, -1):
        events += [_tool("panel2-decelerating", days_ago=d)]
        events += [_tool("panel2-decelerating", days_ago=d)]
    for d in [29, 25, 20, 15, 10]:
        events += [_tool("panel2-decelerating", days_ago=d)]

    # new (lifecycle 浅すぎ): 10 日前 first / 8 件
    for d in [10, 9, 8, 6, 5, 4, 3, 2]:
        events += [_tool("panel2-new", days_ago=d)]

    # last_seen 多様性: 今日 / 昨日 / 30 日前
    events += [_tool("panel2-last-today", days_ago=0)]
    events += [_tool("panel2-last-today", days_ago=20)]
    events += [_tool("panel2-last-today", days_ago=40)]

    return events


def build_panel_3_events_and_skills(skills_dir: Path) -> list[dict]:
    """Panel 3 — 全 status + active 除外 + boundary 検証用 fixture。"""
    # 各 skill は SKILL.md (mtime) と usage.jsonl の両方が必要 (cross-ref)
    # warming_up: mtime 3 日前 / 未使用
    _make_skill(skills_dir, "panel3-warming-recent", mtime_days_ago=3)
    # warming_up boundary: mtime 14 日前ぴったり / 未使用
    _make_skill(skills_dir, "panel3-warming-boundary-14d", mtime_days_ago=14)
    # idle (未使用 + 古い install): mtime 60 日前 / 未使用
    _make_skill(skills_dir, "panel3-idle-unused-old", mtime_days_ago=60)
    # idle (未使用 + 超古い install): mtime 200 日前 / 未使用
    _make_skill(skills_dir, "panel3-idle-unused-ancient", mtime_days_ago=200)
    # resting (15 日前 use)
    _make_skill(skills_dir, "panel3-resting-15d", mtime_days_ago=60)
    # resting boundary (30 日前ぴったり use)
    _make_skill(skills_dir, "panel3-resting-boundary-30d", mtime_days_ago=60)
    # idle (over 30 days use)
    _make_skill(skills_dir, "panel3-idle-31d", mtime_days_ago=60)
    _make_skill(skills_dir, "panel3-idle-60d", mtime_days_ago=60)
    # active (excluded): use 7 日前
    _make_skill(skills_dir, "panel3-active-7d", mtime_days_ago=60)
    # active boundary: use 14 日前ぴったり (exclude)
    _make_skill(skills_dir, "panel3-active-boundary-14d", mtime_days_ago=60)
    # SKILL.md 無し: listing から外れる確認
    (skills_dir / "panel3-no-skill-md").mkdir(parents=True, exist_ok=True)

    events: list[dict] = []
    events += [_tool("panel3-resting-15d", days_ago=15)]
    events += [_tool("panel3-resting-boundary-30d", days_ago=30)]
    events += [_tool("panel3-idle-31d", days_ago=31)]
    events += [_tool("panel3-idle-60d", days_ago=60)]
    events += [_tool("panel3-active-7d", days_ago=7)]
    events += [_tool("panel3-active-boundary-14d", days_ago=14)]
    return events


def main() -> None:
    if FIXTURE_DIR.exists():
        shutil.rmtree(FIXTURE_DIR)
    FIXTURE_DIR.mkdir(parents=True)
    SKILLS_DIR.mkdir(parents=True)

    events = build_panel_1_events() + build_panel_2_events() + \
        build_panel_3_events_and_skills(SKILLS_DIR)

    # usage.jsonl 書き出し
    with USAGE_JSONL.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    # 環境変数で dashboard を fixture に向ける
    os.environ["USAGE_JSONL"] = str(USAGE_JSONL)
    os.environ["SKILLS_DIR"] = str(SKILLS_DIR)

    # dashboard server を import (モジュール load 時に env を読む)
    from dashboard.server import build_dashboard_data, render_static_html

    data = build_dashboard_data(events)
    html = render_static_html(data)
    OUTPUT_HTML.write_text(html, encoding="utf-8")

    # 確認用 summary
    inv = data["skill_invocation_breakdown"]
    life = data["skill_lifecycle"]
    hib = data["skill_hibernating"]

    print("=" * 60)
    print("Surface fixture HTML 生成完了")
    print("=" * 60)
    print(f"出力: {OUTPUT_HTML}")
    print(f"events: {len(events)} 件 / skills_dir: {SKILLS_DIR}")
    print()
    print(f"Panel 1 (起動経路): {len(inv)} skill(s)")
    for row in inv:
        rate = row["autonomy_rate"]
        rate_str = f"{rate:.2%}" if rate is not None else "—"
        print(f"  {row['skill']:<40} mode={row['mode']:<10} tool={row['tool_count']:>3} "
              f"slash={row['slash_count']:>3} autonomy={rate_str}")
    print()
    print(f"Panel 2 (lifecycle): {len(life)} skill(s)")
    for row in life:
        print(f"  {row['skill']:<40} 30d={row['count_30d']:>3} total={row['count_total']:>3} "
              f"trend={row['trend']}")
    print()
    print(f"Panel 3 (hibernating): {len(hib['items'])} skill(s) / "
          f"active_excluded={hib['active_excluded_count']}")
    for it in hib["items"]:
        last = it["last_seen"][:10] if it["last_seen"] else "(unused)"
        days = it["days_since_last_use"]
        print(f"  {it['skill']:<40} status={it['status']:<11} mtime={it['mtime'][:10]} "
              f"last={last} days_since={days}")
    print()
    print(f"open: {OUTPUT_HTML}")
    print("ブラウザで surface タブを開いて 3 panel を目視確認してください。")


if __name__ == "__main__":
    main()
