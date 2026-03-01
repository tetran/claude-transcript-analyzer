"""hooks/record_subagent.py

PostToolUse(Task/Agent) イベントを受け取り、usage.jsonl にイベントを追記する。

Claude Code Hook として stdin から JSON を受け取る。
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_PATH = Path.home() / ".claude" / "transcript-analyzer" / "usage.jsonl"
DATA_FILE = Path(os.environ.get("USAGE_JSONL", str(_DEFAULT_PATH)))


def _project_from_cwd(cwd: str) -> str:
    return Path(cwd).name


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_event(event: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DATA_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


_SUBAGENT_TOOL_NAMES = {"Task", "Agent"}


def _handle_post_tool_use(data: dict) -> None:
    if data.get("tool_name") not in _SUBAGENT_TOOL_NAMES:
        return
    tool_input = data.get("tool_input") or {}
    event = {
        "event_type": "subagent_start",
        "subagent_type": tool_input.get("subagent_type", ""),
        "project": _project_from_cwd(data.get("cwd", "")),
        "session_id": data.get("session_id", ""),
        "timestamp": _now_iso(),
    }
    _append_event(event)


def _handle_subagent_start(data: dict) -> None:
    event = {
        "event_type": "subagent_start",
        "subagent_type": data.get("agent_type", ""),
        "project": _project_from_cwd(data.get("cwd", "")),
        "session_id": data.get("session_id", ""),
        "timestamp": _now_iso(),
    }
    _append_event(event)


def main() -> None:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    event_name = data.get("hook_event_name", "")
    if event_name == "PostToolUse":
        _handle_post_tool_use(data)
    elif event_name == "SubagentStart":
        _handle_subagent_start(data)


if __name__ == "__main__":
    main()

