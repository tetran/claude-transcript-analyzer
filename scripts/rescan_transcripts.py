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
# hooks/ を sys.path に追加して record_assistant_usage からエクスポート関数を import
_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from record_assistant_usage import (  # noqa: E402
    extract_assistant_usage,
    agent_id_from_filename,
    scan_dedup_keys,
)


def _scan_existing_session_ids(data_file: Path) -> set[str]:
    """既存 usage.jsonl の session_start session_id 集合を返す。"""
    ids: set[str] = set()
    if not data_file.exists():
        return ids
    try:
        text = data_file.read_text(encoding="utf-8")
    except OSError:
        return ids
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if ev.get("event_type") == "session_start":
            sid = ev.get("session_id", "") or ""
            if sid:
                ids.add(sid)
    return ids


def write_events_with_dedup(
    events: list[dict],
    output_path: Path,
    existing_keys: set | None = None,
) -> None:
    """assistant_usage / session_start を dedup して output_path に追記。

    - assistant_usage: (session_id, message_id) first-wins
    - session_start: session_id first-wins (rescan 2 回目 / live hook 共存で重複しない)
    - その他 event (skill_tool / subagent_start 等): dedup せず append
    existing_keys が None のとき output_path から自動取得する。
    """
    if existing_keys is None:
        existing_keys = scan_dedup_keys(output_path)
    existing_session_ids = _scan_existing_session_ids(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8", newline="\n") as f:
        seen_keys: set = set(existing_keys)
        seen_sessions: set = set(existing_session_ids)
        for ev in events:
            et = ev.get("event_type")
            if et == "assistant_usage":
                key = (ev.get("session_id", ""), ev.get("message_id", ""))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
            elif et == "session_start":
                sid = ev.get("session_id", "") or ""
                if sid in seen_sessions:
                    continue
                seen_sessions.add(sid)
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

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
                    ev: dict = {
                        "event_type": "subagent_start",
                        "subagent_type": inp.get("subagent_type", ""),
                        "project": project,
                        "session_id": session_id,
                        "timestamp": ts,
                    }
                    tool_use_id = block.get("id")
                    if tool_use_id:
                        ev["tool_use_id"] = tool_use_id
                    events.append(ev)

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


def _derive_session_metadata(path: Path) -> tuple[str, str]:
    """transcript file の最初の有効行から (cwd, timestamp) を返す。

    cwd は _project_from_cwd() に渡してプロジェクト名を正確に導出するために使う。
    encoded path の replace("-", "/") による誤変換 (my-app → app 等) を回避する。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "", ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        cwd = row.get("cwd", "") or ""
        ts = row.get("timestamp", "") or ""
        if cwd and ts:
            return cwd, ts
    return "", ""


# NOTE: live-hook (record_assistant_usage._scan_existing_state) derives valid_agent_ids
# from `subagent_stop` events in usage.jsonl, not from main-transcript Task block .id.
# For transcripts predating reliable tool_use_id population, rescan may undercount
# per-subagent files that live-hook would have collected. This is Option A strict
# adherence — see docs/plans/104-rescan-cost.md §5 R2.
def derive_valid_agent_ids_from_transcript(main_transcript_path: Path) -> set[str]:
    """main transcript の Task/Agent block から valid_agent_ids を構築する。

    Issue #93 filter: subagent_type != "" の block の id のみ収集。
    """
    valid_ids: set[str] = set()
    if not main_transcript_path.exists():
        return valid_ids
    try:
        text = main_transcript_path.read_text(encoding="utf-8")
    except OSError:
        return valid_ids
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if row.get("type") != "assistant":
            continue
        content = row.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            if block.get("name") not in SUBAGENT_TOOL_NAMES:
                continue
            subagent_type = (block.get("input") or {}).get("subagent_type", "")
            if not subagent_type:
                continue  # Issue #93 filter
            agent_id = block.get("id", "")
            if agent_id:
                valid_ids.add(agent_id)
    return valid_ids


def scan_assistant_usage_for_session(
    main_transcript_path: Path,
    session_id: str,
    project: str,
) -> list[dict]:
    """main + per-subagent transcript から assistant_usage event を yield する。

    - main transcript → source="main"
    - per-subagent transcript (Issue #93 filter 適用) → source="subagent"
    """
    events: list[dict] = []

    # main transcript
    for ev in extract_assistant_usage(
        main_transcript_path,
        session_id=session_id,
        project=project,
        source="main",
    ):
        events.append(ev)

    # per-subagent transcripts
    valid_agent_ids = derive_valid_agent_ids_from_transcript(main_transcript_path)
    sa_dir = main_transcript_path.with_suffix("") / "subagents"
    if sa_dir.is_dir():
        for sa_file in sorted(sa_dir.glob("agent-*.jsonl")):
            agent_id = agent_id_from_filename(sa_file)
            if not agent_id or agent_id not in valid_agent_ids:
                continue
            for ev in extract_assistant_usage(
                sa_file,
                session_id=session_id,
                project=project,
                source="subagent",
            ):
                events.append(ev)

    return events


def scan_all(transcripts_dir: Path) -> list[dict]:
    files = _find_transcript_files(transcripts_dir)
    all_events = []
    for f in files:
        print(f"Scanning {f} ...", file=sys.stderr)
        all_events.extend(_scan_transcript_file(f))
        session_id = f.stem
        cwd, first_ts = _derive_session_metadata(f)
        project = _project_from_cwd(cwd) if cwd else ""
        if first_ts:
            all_events.append({
                "event_type": "session_start",
                "session_id": session_id,
                "project": project,
                "timestamp": _parse_timestamp(first_ts),
            })
        all_events.extend(
            scan_assistant_usage_for_session(f, session_id=session_id, project=project)
        )

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
        help="(deprecated since v0.8.0: now the default behavior)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing usage.jsonl instead of appending (BC break escape hatch)",
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

    if args.overwrite:
        write_events(events, DATA_FILE, append=False)
    else:
        write_events_with_dedup(events, DATA_FILE)
    print(f"Wrote {len(events)} events to {DATA_FILE}")


if __name__ == "__main__":
    main()

