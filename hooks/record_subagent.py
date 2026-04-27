#!/usr/bin/env python3
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
    # newline="\n" 固定で Windows text mode の \r\n 変換を抑止 (Issue #24)。
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DATA_FILE.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


_SUBAGENT_TOOL_NAMES = {"Task", "Agent"}


def _enrich_with_post_tool_use_meta(event: dict, data: dict) -> None:
    if "duration_ms" in data:
        event["duration_ms"] = data["duration_ms"]
    if "permission_mode" in data:
        event["permission_mode"] = data["permission_mode"]
    if "tool_use_id" in data:
        event["tool_use_id"] = data["tool_use_id"]
    tool_response = data.get("tool_response")
    if isinstance(tool_response, dict) and "success" in tool_response:
        event["success"] = tool_response["success"]


def _enrich_with_failure_meta(event: dict, data: dict) -> None:
    event["success"] = False
    if "error" in data:
        event["error"] = data["error"]
    if "is_interrupt" in data:
        event["is_interrupt"] = data["is_interrupt"]
    if "duration_ms" in data:
        event["duration_ms"] = data["duration_ms"]
    if "permission_mode" in data:
        event["permission_mode"] = data["permission_mode"]
    if "tool_use_id" in data:
        event["tool_use_id"] = data["tool_use_id"]


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
    _enrich_with_post_tool_use_meta(event, data)
    _append_event(event)


def _handle_post_tool_use_failure(data: dict) -> None:
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
    _enrich_with_failure_meta(event, data)
    _append_event(event)


def _handle_subagent_start(data: dict) -> None:
    """SubagentStart hook: ライフサイクル開始の補助記録。
    PostToolUse(Task|Agent) 経由の `subagent_start` と二重カウントしないよう
    別 event_type `subagent_lifecycle_start` を使う。集計の count には入らない。"""
    event = {
        "event_type": "subagent_lifecycle_start",
        "subagent_type": data.get("agent_type", ""),
        "project": _project_from_cwd(data.get("cwd", "")),
        "session_id": data.get("session_id", ""),
        "timestamp": _now_iso(),
    }
    _append_event(event)


def _handle_subagent_stop(data: dict) -> None:
    event = {
        "event_type": "subagent_stop",
        "subagent_type": data.get("agent_type", ""),
        "subagent_id": data.get("agent_id", ""),
        "project": _project_from_cwd(data.get("cwd", "")),
        "session_id": data.get("session_id", ""),
        "timestamp": _now_iso(),
    }
    if "duration_ms" in data:
        event["duration_ms"] = data["duration_ms"]
    if "success" in data:
        event["success"] = data["success"]
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
    elif event_name == "PostToolUseFailure":
        _handle_post_tool_use_failure(data)
    elif event_name == "SubagentStart":
        _handle_subagent_start(data)
    elif event_name == "SubagentStop":
        _handle_subagent_stop(data)


if __name__ == "__main__":
    main()

