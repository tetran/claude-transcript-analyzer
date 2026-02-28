"""tests/test_record_skill.py — record_skill.py のテスト"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "hooks" / "record_skill.py"


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


class TestPostToolUseSkill:
    def test_skill_tool_event_is_appended(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "user-story-creation", "args": "6"},
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        events = read_events(usage_file)
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "skill_tool"
        assert ev["skill"] == "user-story-creation"
        assert ev["args"] == "6"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "abc123"
        assert "timestamp" in ev

    def test_skill_tool_args_defaults_to_empty_string(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "my-skill"},
            "session_id": "s1",
            "cwd": "/Users/x/proj",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert events[0]["args"] == ""

    def test_multiple_events_are_appended(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        for skill in ("skill-a", "skill-b"):
            stdin = {
                "hook_event_name": "PostToolUse",
                "tool_name": "Skill",
                "tool_input": {"skill": skill},
                "session_id": "s1",
                "cwd": "/p",
            }
            run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 2
        assert events[0]["skill"] == "skill-a"
        assert events[1]["skill"] == "skill-b"

    def test_non_skill_tool_is_ignored(self, tmp_path):
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


class TestUserPromptSubmit:
    def test_custom_slash_command_is_recorded(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "<command-name>/insights</command-name>\nsome args",
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        events = read_events(usage_file)
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "user_slash_command"
        assert ev["skill"] == "/insights"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "abc123"
        assert "timestamp" in ev

    def test_builtin_clear_command_is_ignored(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "<command-name>/clear</command-name>",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 0

    def test_builtin_help_command_is_ignored(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "<command-name>/help</command-name>",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 0

    def test_all_builtin_commands_are_ignored(self, tmp_path):
        builtins = ["/exit", "/clear", "/help", "/compact", "/mcp", "/config",
                    "/model", "/resume", "/context", "/skills", "/hooks", "/fast"]
        usage_file = str(tmp_path / "usage.jsonl")
        for cmd in builtins:
            stdin = {
                "hook_event_name": "UserPromptSubmit",
                "prompt": f"<command-name>{cmd}</command-name>",
                "session_id": "s1",
                "cwd": "/p",
            }
            run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 0

    def test_no_command_name_tag_is_ignored(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Hello, tell me something interesting.",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 0

    def test_plain_slash_command_is_recorded(self, tmp_path):
        """<command-name> タグなしのプレーンな slash コマンドも記録される"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "/codex-review ",
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        events = read_events(usage_file)
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "user_slash_command"
        assert ev["skill"] == "/codex-review"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "abc123"

    def test_plain_slash_command_with_args_is_recorded(self, tmp_path):
        """プレーンな slash コマンドにスペース区切りで引数があっても記録される"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "/user-story-creation 42",
            "session_id": "s1",
            "cwd": "/p/proj",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 1
        assert events[0]["skill"] == "/user-story-creation"

    def test_plain_builtin_slash_command_is_ignored(self, tmp_path):
        """プレーンな組み込みコマンドは記録しない"""
        usage_file = str(tmp_path / "usage.jsonl")
        for cmd in ["/clear", "/help", "/exit", "/compact"]:
            stdin = {
                "hook_event_name": "UserPromptSubmit",
                "prompt": cmd,
                "session_id": "s1",
                "cwd": "/p",
            }
            run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 0

    def test_bare_slash_is_ignored(self, tmp_path):
        """裸の '/' はコマンドとして記録しない"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "/",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 0

    def test_slash_followed_by_spaces_is_ignored(self, tmp_path):
        """'/ ' のようにスラッシュ後がスペースだけのものは記録しない"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "/   ",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 0

    def test_leading_whitespace_slash_command_is_recorded(self, tmp_path):
        """先頭に空白があっても slash コマンドとして記録される"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "  /codex-review",
            "session_id": "s1",
            "cwd": "/p/proj",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 1
        assert events[0]["skill"] == "/codex-review"


class TestEdgeCases:
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
            "tool_name": "Skill",
            "tool_input": {"skill": "my-skill"},
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, str(nested))
        assert nested.exists()
