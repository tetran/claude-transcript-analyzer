"""reports/summary.py — usage.jsonl の集計レポートを表示する。"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subagent_metrics import aggregate_subagent_metrics
from reports._archive_loader import archive_read_lock, iter_archive_events_unlocked

_DEFAULT_PATH = Path.home() / ".claude" / "transcript-analyzer" / "usage.jsonl"
DATA_FILE = Path(os.environ.get("USAGE_JSONL", str(_DEFAULT_PATH)))

# Notification.notification_type は公式仕様で `permission`、過去実装/テストでは `permission_prompt` を観測。
# 両方を許可ダイアログ系としてカウントする。
_PERMISSION_NOTIFICATION_TYPES = frozenset({"permission", "permission_prompt"})


def _read_hot_events() -> list[dict]:
    """usage.jsonl を 1 行 1 event でロード。parse 失敗行は silent skip。"""
    events: list[dict] = []
    if DATA_FILE.exists():
        for line in DATA_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def load_events(include_archive: bool = False) -> list[dict]:
    """hot tier (usage.jsonl) + 必要なら archive を集計用にロード。

    codex 5th review P2: include_archive=True のときは hot と archive を
    **同じ SH lock 下で読む** ことで archive job との atomic snapshot を実現。
    旧実装は hot を lock 外で読んでから archive 用 lock を取っていたため、
    その間に archive job が走ると event が hot + archive 両方に見えて
    double count する race window があった。
    """
    if not include_archive:
        return _read_hot_events()

    with archive_read_lock():
        events = _read_hot_events()
        events.extend(iter_archive_events_unlocked())
    return events


def aggregate_skills(events: list[dict]) -> Counter:
    counter: Counter = Counter()
    for ev in events:
        if ev.get("event_type") in ("skill_tool", "user_slash_command"):
            key = ev.get("skill", "")
            if key:
                counter[key] += 1
    return counter


def aggregate_subagents(events: list[dict]) -> Counter:
    counter: Counter = Counter()
    for ev in events:
        if ev.get("event_type") == "subagent_start":
            key = ev.get("subagent_type", "")
            if key:
                counter[key] += 1
    return counter


def aggregate_skill_stats(events: list[dict]) -> dict[str, dict]:
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
    stats = {}
    for name, count in counter.items():
        failure = failure_counter.get(name, 0)
        stats[name] = {
            "count": count,
            "failure_count": failure,
            "failure_rate": (failure / count) if count else 0.0,
        }
    return stats


def aggregate_subagent_stats(events: list[dict]) -> dict[str, dict]:
    return aggregate_subagent_metrics(events)


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


def _format_duration(ms: float | None) -> str:
    if ms is None:
        return "-"
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms:.0f}ms"


def print_report(events: list[dict]) -> None:
    skill_stats = aggregate_skill_stats(events)
    subagent_stats = aggregate_subagent_stats(events)
    session_stats = aggregate_session_stats(events)

    print(f"Total events: {len(events)}\n")

    print("=== Sessions ===")
    print(f"  Total sessions:       {session_stats['total_sessions']}")
    if session_stats["total_sessions"]:
        rate_pct = session_stats["resume_rate"] * 100
        print(f"  Resume rate:          {session_stats['resume_count']} ({rate_pct:.0f}%)")
    print(f"  Compact events:       {session_stats['compact_count']}")
    print(f"  Permission prompts:   {session_stats['permission_prompt_count']}")
    print()

    print("=== Skills (skill_tool + user_slash_command) ===")
    if skill_stats:
        for skill, s in sorted(skill_stats.items(), key=lambda kv: -kv[1]["count"]):
            fail = s["failure_count"]
            rate_str = f"{s['failure_rate'] * 100:.0f}%" if fail else "-"
            print(f"  {s['count']:4d}  fail={fail:3d} ({rate_str})  {skill}")
    else:
        print("  (no data)")

    print("\n=== Subagents ===")
    if subagent_stats:
        for subagent_type, s in sorted(subagent_stats.items(), key=lambda kv: -kv[1]["count"]):
            fail = s["failure_count"]
            rate_str = f"{s['failure_rate'] * 100:.0f}%" if fail else "-"
            avg = _format_duration(s["avg_duration_ms"])
            print(f"  {s['count']:4d}  fail={fail:3d} ({rate_str})  avg={avg:>7s}  {subagent_type}")
    else:
        print("  (no data)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Skills/Subagents 使用状況の集計レポート")
    parser.add_argument(
        "--include-archive",
        action="store_true",
        help="archive/*.jsonl.gz を読み込んで集計に含める (default: hot tier のみ)",
    )
    args = parser.parse_args(argv)
    print_report(load_events(include_archive=args.include_archive))


if __name__ == "__main__":
    main()

