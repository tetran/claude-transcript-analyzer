"""scripts/build_live_diff_fixture.py — Issue #69 live diff highlight + toast の手動視覚スモーク fixture。

ライブダッシュボードでしか発火しない pulse / toast の見た目と
`prefers-reduced-motion` の効きを実機で確認するための 2 段階 usage.jsonl 生成器。

使い方:
    python3 scripts/build_live_diff_fixture.py

出力:
    /tmp/issue-69-fixture/
      usage.jsonl       — snapshot A: 初期 events (live dashboard 起動時に読む)
      append.jsonl      — snapshot B 追加 events (動作確認のときに手動 append)
      run.sh            — dashboard 起動 + append コマンドの参考スクリプト

シナリオ:
    1. dashboard を fixture 向けに起動 (USAGE_JSONL=/tmp/issue-69-fixture/usage.jsonl)
    2. 初回描画では toast / highlight 出ない (ベースライン)
    3. `cat append.jsonl >> usage.jsonl` を実行
    4. ~1 秒後に SSE refresh が来て:
         - kpi-total が pulse + toast `+5 events · +1 subagent invocation`
         - 既存 skill の rank-row が pulse
         - 新登場 subagent の rank-row が pulse
    5. macOS の「視差効果を減らす」を ON にして同じ操作 → pulse は静止 outline のみ /
       toast は opacity fade のみ
    6. catch 経路の累積 delta 確認: 一時的に /api/data ハンドラに `raise RuntimeError`
       を入れて再起動 → append → toast 出ない (catch return) → revert + 再 append →
       復活 refresh で 2 回分の累積 delta が toast に出る

設計判断:
    - static export ではなく usage.jsonl を 2 段階に切る (= live dashboard 経由でのみ
      発火する pulse / toast を実機で見る)。
    - 環境隔離: USAGE_JSONL を tmp に向け、本物の ~/.claude/transcript-analyzer は
      触らない。
"""
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURE_DIR = Path("/tmp/issue-69-fixture")
USAGE_JSONL = FIXTURE_DIR / "usage.jsonl"
APPEND_JSONL = FIXTURE_DIR / "append.jsonl"
RUN_SH = FIXTURE_DIR / "run.sh"

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _iso(days_ago: float = 0, hours_ago: float = 0) -> str:
    return (NOW - timedelta(days=days_ago, hours=hours_ago)).isoformat()


def _tool(skill: str, *, hours_ago: float = 0, success: bool = True) -> dict:
    return {
        "event_type": "skill_tool",
        "skill": skill,
        "project": "issue-69-fixture",
        "session_id": "issue-69-fixture",
        "timestamp": _iso(hours_ago=hours_ago),
        "success": success,
        "duration_ms": 80,
        "permission_mode": "default",
        "tool_use_id": "issue-69-fixture",
    }


def _subagent_invocation(name: str, *, hours_ago: float = 0) -> list[dict]:
    """1 invocation を表現する PostToolUse(Task|Agent) + SubagentStop 2 件のペア。

    invocation 単位 dedup ロジック (subagent_metrics.py) が
    PostToolUse → SubagentStop の timestamp 差で 1 invocation にまとめるため、
    両方が必要。
    """
    base_ts = NOW - timedelta(hours=hours_ago)
    return [
        {
            "event_type": "subagent_start",
            "subagent_type": name,
            "project": "issue-69-fixture",
            "session_id": "issue-69-fixture",
            "timestamp": base_ts.isoformat(),
            "duration_ms": 1500,
            "tool_use_id": f"issue-69-{name}-{int(base_ts.timestamp())}",
        },
        {
            "event_type": "subagent_lifecycle_stop",
            "subagent_type": name,
            "project": "issue-69-fixture",
            "session_id": "issue-69-fixture",
            "timestamp": (base_ts + timedelta(seconds=1.5)).isoformat(),
            "duration_ms": 1500,
            "tool_use_id": f"issue-69-{name}-{int(base_ts.timestamp())}",
        },
    ]


def build_snapshot_a() -> list[dict]:
    """snapshot A — dashboard 起動時の初期状態 (1 セッション + 既存 skill 利用)。"""
    events: list[dict] = []
    events.append({
        "event_type": "session_start",
        "session_id": "issue-69-fixture",
        "project": "issue-69-fixture",
        "timestamp": _iso(hours_ago=2),
        "source": "startup",
    })
    # 既存の skill A が 3 件 (rank top に出る)
    for h in (1.5, 1.0, 0.5):
        events.append(_tool("codex-review", hours_ago=h))
    # 既存の skill B が 1 件
    events.append(_tool("simplify", hours_ago=1.2))
    # subagent invocation 1 件 (rank top)
    events += _subagent_invocation("Explore", hours_ago=0.8)
    return events


def build_snapshot_b_append() -> list[dict]:
    """snapshot A 上に **追加** で append する events。

    確認したい delta:
      - kpi-total: +5 events
      - kpi-skills: +1 (新登場 skill `frontend-design`)
      - kpi-subs: +1 (新登場 subagent `Plan`)
      - skill rank-row: codex-review が +2 で pulse / frontend-design が +3 で新登場 pulse
      - subagent rank-row: Plan が +1 で新登場 pulse
    """
    events: list[dict] = []
    # 既存 skill +2 (codex-review)
    events.append(_tool("codex-review", hours_ago=0.2))
    events.append(_tool("codex-review", hours_ago=0.1))
    # 新登場 skill +3 (frontend-design)
    for h in (0.05, 0.04, 0.03):
        events.append(_tool("frontend-design", hours_ago=h))
    # 新登場 subagent +1 (Plan)
    events += _subagent_invocation("Plan", hours_ago=0.02)
    return events


def main() -> None:
    if FIXTURE_DIR.exists():
        shutil.rmtree(FIXTURE_DIR)
    FIXTURE_DIR.mkdir(parents=True)

    snap_a = build_snapshot_a()
    append_b = build_snapshot_b_append()

    with USAGE_JSONL.open("w", encoding="utf-8") as f:
        for ev in snap_a:
            f.write(json.dumps(ev) + "\n")
    with APPEND_JSONL.open("w", encoding="utf-8") as f:
        for ev in append_b:
            f.write(json.dumps(ev) + "\n")

    run_sh = """#!/usr/bin/env bash
# Issue #69 live diff highlight + toast 手動視覚スモーク手順
set -e

FIXTURE_DIR=/tmp/issue-69-fixture
USAGE=$FIXTURE_DIR/usage.jsonl
APPEND=$FIXTURE_DIR/append.jsonl
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

echo "1) dashboard を fixture 向けに起動 (Ctrl-C で停止)"
echo "   ブラウザで http://localhost:<auto-port> を開いて Overview タブを確認"
echo ""
echo "   USAGE_JSONL=$USAGE python3 $REPO_DIR/dashboard/server.py"
echo ""
echo "   ※ 起動 log の 'Dashboard available: http://localhost:NNNN' を別ターミナルで使う"
echo ""
echo "2) 別ターミナルで append を実行 (1 秒以内に SSE refresh)"
echo "   cat $APPEND >> $USAGE"
echo ""
echo "3) 期待される観測:"
echo "   - kpi-total / kpi-skills / kpi-subs が mint pulse (1.5s で fade)"
echo "   - rank-row 'codex-review' が pulse (count +2)"
echo "   - rank-row 'frontend-design' が新登場で pulse (delta = 3)"
echo "   - rank-row 'Plan' が subagent panel に新登場で pulse"
echo "   - 画面右上に toast '+5 events · +1 skill · +1 subagent invocation' が 4s 表示"
echo ""
echo "4) macOS で 'システム設定 > アクセシビリティ > 表示 > 視差効果を減らす' を ON に"
echo "   して step 1-2 をやり直す → pulse が静止 outline のみ / toast が opacity fade のみ"
echo ""
echo "5) catch 経路の累積 delta:"
echo "   dashboard/server.py の _serve_api() に一時的に 'raise RuntimeError(\"forced 500\")'"
echo "   を仕込んで再起動 → cat 1 件 append → toast 出ない (loadAndRender catch return) →"
echo "   raise を revert → cat もう 1 件 append → 復活 refresh で 2 回分の累積 delta が toast に"
"""
    RUN_SH.write_text(run_sh, encoding="utf-8")
    RUN_SH.chmod(0o755)

    print("=" * 60)
    print("Issue #69 live diff fixture 生成完了")
    print("=" * 60)
    print(f"  snapshot A (初期): {USAGE_JSONL}  ({len(snap_a)} events)")
    print(f"  snapshot B (append): {APPEND_JSONL}  ({len(append_b)} events)")
    print(f"  手順スクリプト:    {RUN_SH}")
    print()
    print("次のコマンド:")
    print(f"  bash {RUN_SH}     # 手順を表示するだけ (実行はせず)")
    print()
    print("または手動で:")
    print(f"  USAGE_JSONL={USAGE_JSONL} python3 dashboard/server.py")
    print(f"  cat {APPEND_JSONL} >> {USAGE_JSONL}")


if __name__ == "__main__":
    main()
