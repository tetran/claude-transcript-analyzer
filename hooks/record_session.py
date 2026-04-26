"""hooks/record_session.py (Issue #9)

セッション・コンテキスト・摩擦イベントを usage.jsonl に追記する。

対象イベント:
- SessionStart      → event_type: session_start (source, model, agent_type)
- SessionEnd        → event_type: session_end (reason)
- PreCompact        → event_type: compact_start (trigger)
- PostCompact       → event_type: compact_end (trigger)
- Notification      → event_type: notification (notification_type)
- InstructionsLoaded → event_type: instructions_loaded (file_path, memory_type, load_reason, ...)

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


def _base_event(event_type: str, data: dict) -> dict:
    return {
        "event_type": event_type,
        "project": _project_from_cwd(data.get("cwd", "")),
        "session_id": data.get("session_id", ""),
        "timestamp": _now_iso(),
    }


def _handle_session_start(data: dict) -> None:
    event = _base_event("session_start", data)
    event["source"] = data.get("source", "")
    event["model"] = data.get("model", "")
    if "agent_type" in data:
        event["agent_type"] = data["agent_type"]
    _append_event(event)


def _handle_session_end(data: dict) -> None:
    event = _base_event("session_end", data)
    event["reason"] = data.get("reason", "")
    _append_event(event)


def _handle_pre_compact(data: dict) -> None:
    event = _base_event("compact_start", data)
    event["trigger"] = data.get("trigger", "")
    _append_event(event)


def _handle_post_compact(data: dict) -> None:
    event = _base_event("compact_end", data)
    event["trigger"] = data.get("trigger", "")
    _append_event(event)


def _handle_notification(data: dict) -> None:
    event = _base_event("notification", data)
    event["notification_type"] = data.get("notification_type", "")
    _append_event(event)


def _handle_instructions_loaded(data: dict) -> None:
    event = _base_event("instructions_loaded", data)
    event["file_path"] = data.get("file_path", "")
    event["memory_type"] = data.get("memory_type", "")
    event["load_reason"] = data.get("load_reason", "")
    for opt in ("globs", "trigger_file_path", "parent_file_path"):
        if opt in data:
            event[opt] = data[opt]
    _append_event(event)


_HANDLERS = {
    "SessionStart": _handle_session_start,
    "SessionEnd": _handle_session_end,
    "PreCompact": _handle_pre_compact,
    "PostCompact": _handle_post_compact,
    "Notification": _handle_notification,
    "InstructionsLoaded": _handle_instructions_loaded,
}


def main() -> None:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    handler = _HANDLERS.get(data.get("hook_event_name", ""))
    if handler:
        handler(data)


if __name__ == "__main__":
    main()
