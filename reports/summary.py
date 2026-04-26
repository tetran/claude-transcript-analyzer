"""reports/summary.py — usage.jsonl の集計レポートを表示する。"""
import json
import os
from collections import Counter
from pathlib import Path

_DEFAULT_PATH = Path.home() / ".claude" / "transcript-analyzer" / "usage.jsonl"
DATA_FILE = Path(os.environ.get("USAGE_JSONL", str(_DEFAULT_PATH)))


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
    counter: Counter = Counter()
    failure_counter: Counter = Counter()
    stop_durations: dict[str, list[float]] = {}
    start_durations: dict[str, list[float]] = {}
    for ev in events:
        et = ev.get("event_type")
        name = ev.get("subagent_type", "")
        if not name:
            continue
        if et == "subagent_start":
            counter[name] += 1
            if ev.get("success") is False:
                failure_counter[name] += 1
            d = ev.get("duration_ms")
            if isinstance(d, (int, float)):
                start_durations.setdefault(name, []).append(float(d))
        elif et == "subagent_stop":
            if ev.get("success") is False:
                failure_counter[name] += 1
            d = ev.get("duration_ms")
            if isinstance(d, (int, float)):
                stop_durations.setdefault(name, []).append(float(d))
    stats = {}
    for name, count in counter.items():
        failure = failure_counter.get(name, 0)
        durations = stop_durations.get(name) or start_durations.get(name) or []
        avg_duration = (sum(durations) / len(durations)) if durations else None
        stats[name] = {
            "count": count,
            "failure_count": failure,
            "failure_rate": (failure / count) if count else 0.0,
            "avg_duration_ms": avg_duration,
        }
    return stats


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
        elif et == "notification" and ev.get("notification_type") == "permission_prompt":
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


if __name__ == "__main__":
    all_events = load_events()
    print_report(all_events)

