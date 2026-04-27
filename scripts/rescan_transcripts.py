"""scripts/rescan_transcripts.py

~/.claude/projects/ 以下の過去のトランスクリプト（.jsonl）を遡って解析し、
usage.jsonl を一から作り直す（または追記する）スクリプト。

Claude Code Hooks は導入後のイベントしか記録できないため、
このスクリプトで過去の使用履歴を遡及して収集できる。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

_DEFAULT_DATA_FILE = Path.home() / ".claude" / "transcript-analyzer" / "usage.jsonl"
DATA_FILE = Path(os.environ.get("USAGE_JSONL", str(_DEFAULT_DATA_FILE)))

BUILTIN_COMMANDS = frozenset([
    "/exit", "/clear", "/help", "/compact", "/mcp", "/config",
    "/model", "/resume", "/context", "/skills", "/hooks", "/fast",
])

SUBAGENT_TOOL_NAMES = frozenset(["Task", "Agent"])

_COMMAND_NAME_RE = re.compile(r"<command-name>(/\S+)</command-name>")


def _project_from_cwd(cwd: str) -> str:
    return Path(cwd).name


def _parse_timestamp(ts: str) -> str:
    if not ts:
        return ts
    try:
        normalized = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).isoformat()
    except (ValueError, AttributeError):
        return ts


def _extract_events_from_row(row: dict) -> list[dict]:
    row_type = row.get("type")
    session_id = row.get("sessionId", "")
    cwd = row.get("cwd", "")
    ts = _parse_timestamp(row.get("timestamp", ""))
    project = _project_from_cwd(cwd)

    events = []

    if row_type == "assistant":
        content = row.get("message", {}).get("content", [])
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                inp = block.get("input") or {}

                if name == "Skill":
                    args = inp.get("args")
                    if args is None:
                        args = ""
                    events.append({
                        "event_type": "skill_tool",
                        "skill": inp.get("skill", ""),
                        "args": args,
                        "project": project,
                        "session_id": session_id,
                        "timestamp": ts,
                    })
                elif name in SUBAGENT_TOOL_NAMES:
                    events.append({
                        "event_type": "subagent_start",
                        "subagent_type": inp.get("subagent_type", ""),
                        "project": project,
                        "session_id": session_id,
                        "timestamp": ts,
                    })

    elif row_type == "user":
        content = row.get("message", {}).get("content", "")
        if isinstance(content, str):
            m = _COMMAND_NAME_RE.search(content)
            if m:
                command = m.group(1)
                if command not in BUILTIN_COMMANDS:
                    events.append({
                        "event_type": "user_slash_command",
                        "skill": command,
                        "args": "",
                        "project": project,
                        "session_id": session_id,
                        "timestamp": ts,
                    })

    return events


def _scan_transcript_file(path: Path) -> list[dict]:
    events = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"WARN: JSON parse error in {path}: {e}", file=sys.stderr)
                    continue
                events.extend(_extract_events_from_row(row))
    except OSError as e:
        print(f"WARN: cannot read {path}: {e}", file=sys.stderr)
        return []
    return events


def _find_transcript_files(transcripts_dir: Path) -> list[Path]:
    if not transcripts_dir.exists():
        print(f"WARN: {transcripts_dir} does not exist", file=sys.stderr)
        return []
    result = []
    for f in transcripts_dir.rglob("*.jsonl"):
        if f.parent.name == "subagents":
            continue
        result.append(f)
    return result


def scan_all(transcripts_dir: Path) -> list[dict]:
    files = _find_transcript_files(transcripts_dir)
    all_events = []
    for f in files:
        print(f"Scanning {f} ...", file=sys.stderr)
        all_events.extend(_scan_transcript_file(f))

    def sort_key(ev: dict) -> str:
        ts = ev.get("timestamp", "")
        if not ts:
            return "\xff"  # timestamp なしは末尾
        return ts

    all_events.sort(key=sort_key)
    return all_events


def write_events(events: list[dict], output_path: Path, append: bool = False) -> None:
    # newline="\n" 固定で Windows text mode の \r\n 変換を抑止 (Issue #24)。
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with output_path.open(mode, encoding="utf-8", newline="\n") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def main() -> None:
    default_transcripts_dir = Path.home() / ".claude" / "projects"

    parser = argparse.ArgumentParser(
        description="Rescan past Claude Code transcripts and rebuild usage.jsonl"
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to existing usage.jsonl instead of overwriting",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count events without writing to file",
    )
    parser.add_argument(
        "--transcripts-dir",
        type=Path,
        default=default_transcripts_dir,
        help=(
            "Directory to scan for transcripts "
            "(default: ~/.claude/projects on POSIX, "
            "%%USERPROFILE%%\\.claude\\projects on Windows)"
        ),
    )
    args = parser.parse_args()

    events = scan_all(args.transcripts_dir)

    if args.dry_run:
        print(f"Found {len(events)} events (dry-run, not writing)")
        return

    write_events(events, DATA_FILE, append=args.append)
    print(f"Wrote {len(events)} events to {DATA_FILE}")


if __name__ == "__main__":
    main()

