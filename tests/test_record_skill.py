"""tests/test_record_skill.py — record_skill.py のテスト"""
import json
import os
import subprocess
import sys
from pathlib import Path

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


class TestPostToolUseSkillMetaFields:
    """Issue #6: PostToolUse の付加情報フィールド (duration_ms / success / permission_mode / tool_use_id)"""

    def test_all_meta_fields_are_recorded(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "user-story-creation", "args": "6"},
            "tool_response": {"success": True},
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
            "duration_ms": 1234,
            "permission_mode": "auto",
            "tool_use_id": "toolu_01ABC",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert len(events) == 1
        ev = events[0]
        assert ev["duration_ms"] == 1234
        assert ev["success"] is True
        assert ev["permission_mode"] == "auto"
        assert ev["tool_use_id"] == "toolu_01ABC"

    def test_success_false_is_preserved(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "my-skill"},
            "tool_response": {"success": False},
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert events[0]["success"] is False

    def test_meta_fields_are_omitted_when_absent(self, tmp_path):
        """既存ログとの後方互換: 付加フィールドが入力に無いときはイベントにも入れない"""
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
        ev = events[0]
        for key in ("duration_ms", "success", "permission_mode", "tool_use_id"):
            assert key not in ev

    def test_tool_response_without_success_does_not_add_success(self, tmp_path):
        """tool_response はあるが success キーが無いケースでも success を入れない"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "my-skill"},
            "tool_response": {"filePath": "/x"},
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        events = read_events(usage_file)
        assert "success" not in events[0]

    def test_partial_meta_fields_only_added_when_present(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "my-skill"},
            "session_id": "s1",
            "cwd": "/p",
            "duration_ms": 99,
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["duration_ms"] == 99
        assert "success" not in ev
        assert "permission_mode" not in ev
        assert "tool_use_id" not in ev

    def test_existing_keys_remain_unchanged(self, tmp_path):
        """後方互換: 既存の key (event_type / skill / args / project / session_id / timestamp) は維持"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "user-story-creation", "args": "6"},
            "tool_response": {"success": True},
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
            "duration_ms": 12,
            "permission_mode": "default",
            "tool_use_id": "toolu_01ABC",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "skill_tool"
        assert ev["skill"] == "user-story-creation"
        assert ev["args"] == "6"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "abc123"
        assert "timestamp" in ev


class TestPostToolUseFailureSkill:
    """Issue #8: PostToolUseFailure で skill_tool を success: false として記録"""

    def test_failure_records_skill_tool_with_success_false(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Skill",
            "tool_input": {"skill": "user-story-creation", "args": "6"},
            "tool_use_id": "toolu_01ABC",
            "error": "Skill not found",
            "duration_ms": 50,
            "permission_mode": "default",
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "skill_tool"
        assert ev["skill"] == "user-story-creation"
        assert ev["args"] == "6"
        assert ev["success"] is False
        assert ev["error"] == "Skill not found"
        assert ev["duration_ms"] == 50
        assert ev["tool_use_id"] == "toolu_01ABC"
        assert ev["permission_mode"] == "default"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "abc123"

    def test_failure_for_non_skill_tool_is_ignored(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "error": "boom",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        assert read_events(usage_file) == []

    def test_failure_with_is_interrupt(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Skill",
            "tool_input": {"skill": "my-skill"},
            "error": "interrupted",
            "is_interrupt": True,
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["success"] is False
        assert ev["is_interrupt"] is True

    def test_failure_without_error_still_records(self, tmp_path):
        """error フィールドが無くても success: false で記録される"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Skill",
            "tool_input": {"skill": "my-skill"},
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["success"] is False
        assert "error" not in ev


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


class TestUserPromptExpansion:
    """Issue #7: UserPromptExpansion を観測ポイントとして追加（XML 正規表現脱却）"""

    def test_slash_command_expansion_is_recorded(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command",
            "command_name": "insights",
            "command_args": "",
            "command_source": "plugin",
            "prompt": "/insights",
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

    def test_command_name_with_leading_slash_preserved(self, tmp_path):
        """command_name が既に '/' で始まっとっても二重にならん"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command",
            "command_name": "/insights",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["skill"] == "/insights"

    def test_mcp_prompt_expansion_is_ignored(self, tmp_path):
        """expansion_type が slash_command 以外（mcp_prompt 等）は記録せん"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "mcp_prompt",
            "command_name": "some-mcp-prompt",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        assert read_events(usage_file) == []

    def test_builtin_command_expansion_is_ignored(self, tmp_path):
        """組み込みコマンド（/clear など）の expansion は記録せん"""
        usage_file = str(tmp_path / "usage.jsonl")
        for name in ("clear", "help", "exit", "compact"):
            stdin = {
                "hook_event_name": "UserPromptExpansion",
                "expansion_type": "slash_command",
                "command_name": name,
                "session_id": "s1",
                "cwd": "/p",
            }
            run_script(stdin, usage_file)
        assert read_events(usage_file) == []

    def test_empty_command_name_is_ignored(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command",
            "command_name": "",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        assert read_events(usage_file) == []


class TestUserPromptExpansionDedup:
    """Issue #7: Expansion + Submit 連続発火でダブルカウントせん"""

    def _expansion(self, name: str, session: str = "s1", cwd: str = "/Users/kkoichi/Developer/personal/chirper") -> dict:
        return {
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command",
            "command_name": name,
            "session_id": session,
            "cwd": cwd,
        }

    def _submit(self, command: str, session: str = "s1", cwd: str = "/Users/kkoichi/Developer/personal/chirper") -> dict:
        return {
            "hook_event_name": "UserPromptSubmit",
            "prompt": f"<command-name>{command}</command-name>",
            "session_id": session,
            "cwd": cwd,
        }

    def test_expansion_then_submit_records_only_once(self, tmp_path):
        """同一 session/command の連続発火 → 1 件のみ"""
        usage_file = str(tmp_path / "usage.jsonl")
        run_script(self._expansion("insights"), usage_file)
        run_script(self._submit("/insights"), usage_file)
        events = read_events(usage_file)
        assert len(events) == 1
        assert events[0]["event_type"] == "user_slash_command"
        assert events[0]["skill"] == "/insights"

    def test_submit_alone_still_records(self, tmp_path):
        """UserPromptExpansion が来ない経路では従来通り Submit から記録（fallback）"""
        usage_file = str(tmp_path / "usage.jsonl")
        run_script(self._submit("/codex-review"), usage_file)
        events = read_events(usage_file)
        assert len(events) == 1
        assert events[0]["skill"] == "/codex-review"

    def test_different_command_does_not_dedup(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        run_script(self._expansion("insights"), usage_file)
        run_script(self._submit("/codex-review"), usage_file)
        events = read_events(usage_file)
        assert len(events) == 2
        assert {e["skill"] for e in events} == {"/insights", "/codex-review"}

    def test_different_session_does_not_dedup(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        run_script(self._expansion("insights", session="sA"), usage_file)
        run_script(self._submit("/insights", session="sB"), usage_file)
        events = read_events(usage_file)
        assert len(events) == 2

    def test_two_unrelated_expansions_both_recorded(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        run_script(self._expansion("insights"), usage_file)
        run_script(self._expansion("codex-review"), usage_file)
        events = read_events(usage_file)
        assert len(events) == 2
        assert {e["skill"] for e in events} == {"/insights", "/codex-review"}

    def test_dedup_window_is_finite(self, tmp_path, monkeypatch):
        """時間窓を越えた古い expansion とは dedup されへん（fallback で記録される）"""
        usage_file = str(tmp_path / "usage.jsonl")
        old_event = {
            "event_type": "user_slash_command",
            "skill": "/insights",
            "args": "",
            "project": "chirper",
            "session_id": "s1",
            "timestamp": "2020-01-01T00:00:00+00:00",
        }
        Path(usage_file).write_text(json.dumps(old_event) + "\n", encoding="utf-8")
        run_script(self._submit("/insights"), usage_file)
        events = read_events(usage_file)
        assert len(events) == 2

    def test_plain_slash_command_after_expansion_is_deduped(self, tmp_path):
        """<command-name> タグ無しのプレーン slash コマンドも dedup 対象"""
        usage_file = str(tmp_path / "usage.jsonl")
        run_script(self._expansion("codex-review"), usage_file)
        plain_submit = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "/codex-review",
            "session_id": "s1",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        run_script(plain_submit, usage_file)
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
