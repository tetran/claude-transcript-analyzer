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
    # newline="\n" 固定で Windows text mode の \r\n 変換を抑止 (Issue #24)。
    # POSIX で書いた jsonl に Win から続けて書くと混在 EOL になりパース不能リスク。
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DATA_FILE.open("a", encoding="utf-8", newline="\n") as f:
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


def _handle_post_tool_use_failure(data: dict) -> None:
    if data.get("tool_name") != "Skill":
        return
    tool_input = data.get("tool_input") or {}
    event = {
        "event_type": "skill_tool",
        "skill": tool_input.get("skill", ""),
        "args": tool_input.get("args", ""),
        "project": _project_from_cwd(data.get("cwd", "")),
        "session_id": data.get("session_id", ""),
        "timestamp": _now_iso(),
    }
    _enrich_with_failure_meta(event, data)
    _append_event(event)


_SLASH_COMMAND_RE = re.compile(r"^/[A-Za-z0-9][\w\-]*")

DEDUP_WINDOW_SECONDS = 5
_DEDUP_TAIL_BYTES = 16384


def _read_recent_events_tail() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    try:
        with DATA_FILE.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            seek_pos = max(0, size - _DEDUP_TAIL_BYTES)
            f.seek(seek_pos)
            tail = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    events = []
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _is_recent_duplicate_slash_command(session_id: str, command: str) -> bool:
    """submit ハンドラから呼ばれる dedup 判定。
    UserPromptExpansion 由来 (source != "submit") のレコードに対してのみ重複と判定する。
    submit 連打は別個の操作として両方記録するため、source=="submit" のレコードはマッチしない。
    過去フォーマット (source 欄なし) は expansion 由来とみなして従来挙動を維持。"""
    now = datetime.now(timezone.utc)
    for ev in reversed(_read_recent_events_tail()):
        if ev.get("event_type") != "user_slash_command":
            continue
        if ev.get("session_id") != session_id:
            continue
        if ev.get("skill") != command:
            continue
        if ev.get("source") == "submit":
            continue
        ts_str = ev.get("timestamp")
        if not ts_str:
            continue
        try:
            ev_ts = datetime.fromisoformat(ts_str)
        except ValueError:
            continue
        if abs((now - ev_ts).total_seconds()) <= DEDUP_WINDOW_SECONDS:
            return True
    return False


def _handle_user_prompt_expansion(data: dict) -> None:
    if data.get("expansion_type") != "slash_command":
        return
    raw_name = data.get("command_name", "")
    if not raw_name:
        return
    command = raw_name if raw_name.startswith("/") else "/" + raw_name
    if command in BUILTIN_COMMANDS:
        return
    event = {
        "event_type": "user_slash_command",
        "skill": command,
        "args": "",
        "source": "expansion",
        "project": _project_from_cwd(data.get("cwd", "")),
        "session_id": data.get("session_id", ""),
        "timestamp": _now_iso(),
    }
    _append_event(event)


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
    session_id = data.get("session_id", "")
    if _is_recent_duplicate_slash_command(session_id, command):
        return
    event = {
        "event_type": "user_slash_command",
        "skill": command,
        "args": "",
        "source": "submit",
        "project": _project_from_cwd(data.get("cwd", "")),
        "session_id": session_id,
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
    elif event_name == "PostToolUseFailure":
        _handle_post_tool_use_failure(data)
    elif event_name == "UserPromptSubmit":
        _handle_user_prompt_submit(data)
    elif event_name == "UserPromptExpansion":
        _handle_user_prompt_expansion(data)


if __name__ == "__main__":
    main()

