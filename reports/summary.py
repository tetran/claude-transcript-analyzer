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


def print_report(events: list[dict]) -> None:
    skill_counts = aggregate_skills(events)
    subagent_counts = aggregate_subagents(events)

    print(f"Total events: {len(events)}\n")

    print("=== Skills (skill_tool + user_slash_command) ===")
    if skill_counts:
        for skill, count in skill_counts.most_common():
            print(f"  {count:4d}  {skill}")
    else:
        print("  (no data)")

    print("\n=== Subagents ===")
    if subagent_counts:
        for subagent_type, count in subagent_counts.most_common():
            print(f"  {count:4d}  {subagent_type}")
    else:
        print("  (no data)")


if __name__ == "__main__":
    all_events = load_events()
    print_report(all_events)

