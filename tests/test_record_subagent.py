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
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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


class TestPostToolUseSubagentMetaFields:
    """Issue #6: PostToolUse の付加情報フィールド (duration_ms / success / permission_mode / tool_use_id)"""

    def test_all_meta_fields_are_recorded(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Task",
            "tool_input": {"subagent_type": "Explore", "description": "..."},
            "tool_response": {"success": True},
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
            "duration_ms": 5000,
            "permission_mode": "plan",
            "tool_use_id": "toolu_01XYZ",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["duration_ms"] == 5000
        assert ev["success"] is True
        assert ev["permission_mode"] == "plan"
        assert ev["tool_use_id"] == "toolu_01XYZ"

    def test_success_false_is_preserved(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "Plan"},
            "tool_response": {"success": False},
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        assert read_events(usage_file)[0]["success"] is False

    def test_meta_fields_are_omitted_when_absent(self, tmp_path):
        """既存ログとの後方互換: 付加フィールドが入力に無いときはイベントにも入れない"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Task",
            "tool_input": {"subagent_type": "Explore", "description": "..."},
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        for key in ("duration_ms", "success", "permission_mode", "tool_use_id"):
            assert key not in ev

    def test_tool_response_without_success_does_not_add_success(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Task",
            "tool_input": {"subagent_type": "Explore"},
            "tool_response": {"summary": "done"},
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        assert "success" not in read_events(usage_file)[0]

    def test_partial_meta_fields_only_added_when_present(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Task",
            "tool_input": {"subagent_type": "Explore"},
            "session_id": "s1",
            "cwd": "/p",
            "permission_mode": "auto",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["permission_mode"] == "auto"
        assert "duration_ms" not in ev
        assert "success" not in ev
        assert "tool_use_id" not in ev

    def test_existing_keys_remain_unchanged(self, tmp_path):
        """後方互換: 既存の key (event_type / subagent_type / project / session_id / timestamp) は維持"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Task",
            "tool_input": {"subagent_type": "Explore"},
            "tool_response": {"success": True},
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
            "duration_ms": 12,
            "permission_mode": "default",
            "tool_use_id": "toolu_01ABC",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "subagent_start"
        assert ev["subagent_type"] == "Explore"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "abc123"
        assert "timestamp" in ev

    def test_tool_input_null_with_meta_fields_still_works(self, tmp_path):
        """tool_input が null でも meta フィールドは取り込まれる"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "tool_input": None,
            "tool_response": {"success": True},
            "session_id": "s1",
            "cwd": "/p",
            "duration_ms": 7,
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["subagent_type"] == ""
        assert ev["duration_ms"] == 7
        assert ev["success"] is True


class TestSubagentStartEvent:
    """SubagentStart フックイベントのテスト。
    観測冗長性を保ちつつ count の二重計上を避けるため、
    PostToolUse(Task|Agent) 経由とは別の event_type "subagent_lifecycle_start" として記録する。"""

    def test_subagent_start_hook_event_is_recorded_as_lifecycle_start(self, tmp_path):
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
        assert ev["event_type"] == "subagent_lifecycle_start"
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
        assert events[0]["event_type"] == "subagent_lifecycle_start"
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
        assert events[0]["event_type"] == "subagent_lifecycle_start"
        assert events[0]["subagent_type"] == ""


class TestSubagentStopEvent:
    """Issue #8: SubagentStop フックイベントのテスト"""

    def test_subagent_stop_event_is_recorded(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SubagentStop",
            "agent_type": "Explore",
            "agent_id": "agent-abc123",
            "session_id": "abc123",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        events = read_events(usage_file)
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "subagent_stop"
        assert ev["subagent_type"] == "Explore"
        assert ev["subagent_id"] == "agent-abc123"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "abc123"
        assert "timestamp" in ev

    def test_subagent_stop_with_duration_and_success_payload_does_not_persist_them(self, tmp_path):
        """drift guard: 実 SubagentStop payload に duration_ms / success は **存在しない**
        (#93 ローカル観察)。仮に input に紛れていても event には書き出さないことを pin。"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SubagentStop",
            "agent_type": "Plan",
            "agent_id": "agent-xyz",
            "duration_ms": 45000,
            "success": True,
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "subagent_stop"
        assert "duration_ms" not in ev, "duration_ms は実 SubagentStop payload に存在しない (Issue #100 / #93)"
        assert "success" not in ev, "success は実 SubagentStop payload に存在しない (Issue #100 / #93)"

    def test_subagent_stop_with_failure_payload_does_not_persist_success(self, tmp_path):
        """drift guard: 実 SubagentStop payload に success は無いので、仮に false が
        入っていても event には書き出さない (Issue #100 / #93)。"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SubagentStop",
            "agent_type": "Explore",
            "agent_id": "agent-fail",
            "success": False,
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert "success" not in ev

    def test_subagent_stop_drops_duration_ms_field_when_provided(self, tmp_path):
        """drift guard: duration_ms / success が input に紛れても event には出さない
        ことを直接 pin (Issue #100 / #93)。"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SubagentStop",
            "agent_type": "Explore",
            "agent_id": "agent-x",
            "duration_ms": 99999,
            "success": True,
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "subagent_stop"
        assert "duration_ms" not in ev
        assert "success" not in ev

    def test_subagent_stop_captures_agent_transcript_path(self, tmp_path):
        """新規 capture: agent_transcript_path は filter validation の evidence。
        値そのものは下流 dedup / filter で使わない (capture only)。"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SubagentStop",
            "agent_type": "Explore",
            "agent_id": "agent-x",
            "agent_transcript_path": "/Users/kkoichi/.claude/projects/foo/agent-x.jsonl",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["agent_transcript_path"] == "/Users/kkoichi/.claude/projects/foo/agent-x.jsonl"

    def test_subagent_stop_omits_agent_transcript_path_when_absent(self, tmp_path):
        """payload に無いときは event に key を入れない (後方互換 + メイン誤発火検出シグナル)。
        Issue #93 観察: メインスレッド誤発火時は実 subagent 不在 → transcript file も不在。"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SubagentStop",
            "agent_type": "",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert "agent_transcript_path" not in ev

    def test_subagent_stop_missing_optional_fields(self, tmp_path):
        """duration_ms / success / agent_id が無くても記録される（後方互換）"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SubagentStop",
            "agent_type": "Explore",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "subagent_stop"
        assert ev["subagent_type"] == "Explore"
        for key in ("duration_ms", "success"):
            assert key not in ev
        assert ev.get("subagent_id", "") == ""

    def test_subagent_stop_missing_agent_type_uses_empty_string(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "SubagentStop",
            "agent_id": "agent-xyz",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "subagent_stop"
        assert ev["subagent_type"] == ""


class TestPostToolUseFailureSubagent:
    """Issue #8: PostToolUseFailure でも subagent_start を success: false として記録"""

    def test_failure_records_subagent_start_with_success_false(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Task",
            "tool_input": {"subagent_type": "Explore", "description": "..."},
            "tool_use_id": "toolu_01XYZ",
            "error": "Subagent crashed",
            "duration_ms": 1234,
            "session_id": "s1",
            "cwd": "/Users/kkoichi/Developer/personal/chirper",
        }
        result = run_script(stdin, usage_file)
        assert result.returncode == 0
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "subagent_start"
        assert ev["subagent_type"] == "Explore"
        assert ev["success"] is False
        assert ev["error"] == "Subagent crashed"
        assert ev["duration_ms"] == 1234
        assert ev["tool_use_id"] == "toolu_01XYZ"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "s1"

    def test_failure_for_agent_tool_is_recorded(self, tmp_path):
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "Plan"},
            "error": "boom",
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["event_type"] == "subagent_start"
        assert ev["success"] is False

    def test_failure_for_non_subagent_tool_is_ignored(self, tmp_path):
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
        """is_interrupt が true ならイベントに保存される"""
        usage_file = str(tmp_path / "usage.jsonl")
        stdin = {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Task",
            "tool_input": {"subagent_type": "Explore"},
            "error": "interrupted",
            "is_interrupt": True,
            "session_id": "s1",
            "cwd": "/p",
        }
        run_script(stdin, usage_file)
        ev = read_events(usage_file)[0]
        assert ev["success"] is False
        assert ev["is_interrupt"] is True


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
