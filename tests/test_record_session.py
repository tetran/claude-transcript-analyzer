"""tests/test_record_session.py — record_session.py のテスト (Issue #9)"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "hooks" / "record_session.py"


def run_script(stdin_data: dict, usage_jsonl: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["USAGE_JSONL"] = usage_jsonl
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(stdin_data),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def read_events(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


class TestSessionStart:
    def test_session_start_with_source_startup(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SessionStart",
            "source": "startup",
            "model": "claude-sonnet-4-6",
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "session_start"
        assert ev["source"] == "startup"
        assert ev["model"] == "claude-sonnet-4-6"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "abc123"
        assert "timestamp" in ev

    def test_session_start_with_source_resume(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SessionStart",
            "source": "resume",
            "model": "claude-opus-4-7",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["source"] == "resume"

    def test_session_start_with_agent_type(self, tmp_path):
        """--agent <name> で起動した場合 agent_type が含まれる"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SessionStart",
            "source": "startup",
            "model": "claude-sonnet-4-6",
            "agent_type": "Explore",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["agent_type"] == "Explore"

    def test_session_start_without_agent_type_omits_field(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SessionStart",
            "source": "startup",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert "agent_type" not in ev


class TestSessionEnd:
    def test_session_end_with_reason(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SessionEnd",
            "reason": "logout",
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "session_end"
        assert ev["reason"] == "logout"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "abc123"
        assert "timestamp" in ev

    def test_session_end_without_reason_uses_empty_string(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SessionEnd",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["reason"] == ""


class TestCompact:
    def test_pre_compact_records_compact_start(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PreCompact",
            "trigger": "manual",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "compact_start"
        assert ev["trigger"] == "manual"

    def test_post_compact_records_compact_end(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostCompact",
            "trigger": "auto",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "compact_end"
        assert ev["trigger"] == "auto"

    def test_compact_without_trigger_uses_empty_string(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PreCompact",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["trigger"] == ""


class TestNotification:
    def test_notification_permission_prompt(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "Notification",
            "notification_type": "permission_prompt",
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "notification"
        assert ev["notification_type"] == "permission_prompt"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "abc123"

    def test_notification_idle_prompt(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "Notification",
            "notification_type": "idle_prompt",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["notification_type"] == "idle_prompt"


class TestInstructionsLoaded:
    def test_instructions_loaded_project_memory(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "InstructionsLoaded",
            "file_path": "/Users/kkoichi/Developer/personal/chirper/CLAUDE.md",
            "memory_type": "Project",
            "load_reason": "session_start",
            "session_id": "s1",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "instructions_loaded"
        assert ev["file_path"] == "/Users/kkoichi/Developer/personal/chirper/CLAUDE.md"
        assert ev["memory_type"] == "Project"
        assert ev["load_reason"] == "session_start"
        assert ev["project"] == "chirper"

    def test_instructions_loaded_user_memory(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "InstructionsLoaded",
            "file_path": "/Users/kkoichi/.claude/CLAUDE.md",
            "memory_type": "User",
            "load_reason": "session_start",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["memory_type"] == "User"

    def test_instructions_loaded_omits_optional_fields_when_absent(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "InstructionsLoaded",
            "file_path": "/p/CLAUDE.md",
            "memory_type": "Project",
            "load_reason": "session_start",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        for key in ("globs", "trigger_file_path", "parent_file_path"):
            assert key not in ev


class TestEdgeCases:
    def test_unknown_event_is_ignored(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        assert read_events(usage_file) == []

    def test_invalid_json_exits_cleanly(self, tmp_path):
        env = os.environ.copy()
        env["USAGE_JSONL"] = str(tmp_path / "usage.jsonl")
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input="not valid json{{",
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0

    def test_data_dir_is_created_if_missing(self, tmp_path):
        nested = tmp_path / "a" / "b" / "usage.jsonl"
        stdin = {
            "hook_event_name": "SessionStart",
            "source": "startup",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, str(nested))
        assert nested.exists()
