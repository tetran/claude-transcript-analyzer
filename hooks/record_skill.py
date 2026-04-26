"""hooks/record_skill.py

PostToolUse(Skill) と UserPromptSubmit イベントを受け取り、
usage.jsonl にイベントを追記する。

Claude Code Hook として stdin から JSON を受け取る。
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_PATH = Path.home() / ".claude" / "transcript-analyzer" / "usage.jsonl"
DATA_FILE = Path(os.environ.get("USAGE_JSONL", str(_DEFAULT_PATH)))

BUILTIN_COMMANDS = frozenset([
    "/exit", "/clear", "/help", "/compact", "/mcp", "/config",
    "/model", "/resume", "/context", "/skills", "/hooks", "/fast",
])

_COMMAND_NAME_RE = re.compile(r"<command-name>(/\S+)</command-name>")


def _project_from_cwd(cwd: str) -> str:
    return Path(cwd).name


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_event(event: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DATA_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


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


def _handle_post_tool_use(data: dict) -> None:
    if data.get("tool_name") != "Skill":
        return
    tool_input = data.get("tool_input", {})
    event = {
        "event_type": "skill_tool",
        "skill": tool_input.get("skill", ""),
        "args": tool_input.get("args", ""),
        "project": _project_from_cwd(data.get("cwd", "")),
        "session_id": data.get("session_id", ""),
        "timestamp": _now_iso(),
    }
    _enrich_with_post_tool_use_meta(event, data)
    _append_event(event)


_SLASH_COMMAND_RE = re.compile(r"^/[A-Za-z0-9][\w\-]*")


def _handle_user_prompt_submit(data: dict) -> None:
    prompt = data.get("prompt", "")
    m = _COMMAND_NAME_RE.search(prompt)
    if m:
        command = m.group(1)
    else:
        stripped = prompt.lstrip()
        token = stripped.split()[0] if stripped.split() else ""
        if not _SLASH_COMMAND_RE.match(token):
            return
        command = token
    if command in BUILTIN_COMMANDS:
        return
    event = {
        "event_type": "user_slash_command",
        "skill": command,
        "args": "",
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
    elif event_name == "UserPromptSubmit":
        _handle_user_prompt_submit(data)


if __name__ == "__main__":
    main()

