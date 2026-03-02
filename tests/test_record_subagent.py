"""tests/test_record_subagent.py — record_subagent.py のテスト"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "hooks" / "record_subagent.py"


def run_script(stdin_data: dict, usage_jsonl: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["USAGE_JSONL"] = usage_jsonl
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(stdin_data),
        capture_output=True,
        text=True,
        env=env,
    )


def read_events(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


class TestPostToolUseTask:
    def test_subagent_start_event_is_appended(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Task",
            "tool_input": {
                "subagent_type": "Explore",
                "description": "Search for something",
                "run_in_background": False,
            },
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        events = read_events(usage_file)
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "subagent_start"
        assert ev["subagent_type"] == "Explore"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "abc123"
        assert "timestamp" in ev

    def test_multiple_subagents_are_appended(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        for subagent_type in ("Explore", "Plan"):
            stdin = {
                "hook_event_name": "PostToolUse",
                "tool_name": "Task",
                "tool_input": {"subagent_type": subagent_type, "description": "..."},
                "session_id": "s1",
                "cwd": "/p",
            }
            run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 2
        assert events[0]["subagent_type"] == "Explore"
        assert events[1]["subagent_type"] == "Plan"

    def test_non_task_tool_is_ignored(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 0

    def test_skill_tool_is_ignored(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "my-skill"},
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 0


class TestPostToolUseAgent:
    def test_agent_tool_subagent_start_event_is_appended(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "Explore",
                "description": "Search for something",
            },
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        events = read_events(usage_file)
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "subagent_start"
        assert ev["subagent_type"] == "Explore"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "abc123"
        assert "timestamp" in ev

    def test_task_and_agent_tools_both_recorded(self, tmp_path):
        """後方互換性: Task と Agent どちらも記録される"""
        usage_file = str(tmp_path / "usage.jsonl")
        for tool_name in ("Task", "Agent"):
            stdin = {
                "hook_event_name": "PostToolUse",
                "tool_name": tool_name,
                "tool_input": {"subagent_type": "Plan", "description": "..."},
                "session_id": "s1",
                "cwd": "/p",
            }
            run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 2
        for ev in events:
            assert ev["event_type"] == "subagent_start"
            assert ev["subagent_type"] == "Plan"
            assert ev["project"] == "p"
            assert ev["session_id"] == "s1"
            assert "timestamp" in ev


class TestSubagentStartEvent:
    """SubagentStart フックイベントのテスト"""

    def test_subagent_start_hook_event_is_recorded(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SubagentStart",
            "agent_type": "ui-designer",
            "agent_id": "agent-abc123",
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        events = read_events(usage_file)
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "subagent_start"
        assert ev["subagent_type"] == "ui-designer"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "abc123"
        assert "timestamp" in ev

    def test_subagent_start_general_purpose(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SubagentStart",
            "agent_type": "general-purpose",
            "agent_id": "agent-xyz",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 1
        assert events[0]["subagent_type"] == "general-purpose"

    def test_subagent_start_missing_agent_type_uses_empty_string(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SubagentStart",
            "agent_id": "agent-xyz",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 1
        assert events[0]["subagent_type"] == ""


class TestEdgeCases:
    def test_tool_input_null_exits_cleanly(self, tmp_path):
        """tool_input が null でもクラッシュしない"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "tool_input": None,
            "session_id": "s1",
            "cwd": "/p",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        events = read_events(usage_file)
        assert len(events) == 1
        assert events[0]["subagent_type"] == ""

    def test_invalid_json_exits_cleanly(self, tmp_path):
        env = os.environ.copy()
        env["USAGE_JSONL"] = str(tmp_path / "usage.jsonl")
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input="not valid json{{",
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0

    def test_data_dir_is_created_if_missing(self, tmp_path):
        nested = tmp_path / "a" / "b" / "usage.jsonl"
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Task",
            "tool_input": {"subagent_type": "Explore", "description": "..."},
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, str(nested))
        assert nested.exists()
