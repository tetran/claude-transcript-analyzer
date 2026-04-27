"""tests/test_rescan_transcripts.py — rescan_transcripts.py のテスト"""
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

# scripts/ ディレクトリをパスに追加してモジュールを直接インポート
_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import rescan_transcripts as rs  # noqa: E402

SCRIPT = _SCRIPTS_DIR / "rescan_transcripts.py"


# ---------- ヘルパー ----------

def make_transcript_row(row_type: str, session_id: str, cwd: str,
                         timestamp: str, content) -> dict:
    return {
        "type": row_type,
        "sessionId": session_id,
        "cwd": cwd,
        "timestamp": timestamp,
        "message": {"role": row_type, "content": content},
    }


def make_skill_block(skill: str, args=None) -> dict:
    inp = {"skill": skill}
    if args is not None:
        inp["args"] = args
    return {"type": "tool_use", "name": "Skill", "input": inp}


def make_task_block(subagent_type: str, tool_name: str = "Task") -> dict:
    return {
        "type": "tool_use",
        "name": tool_name,
        "input": {"subagent_type": subagent_type, "description": "..."},
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_events(path: Path | str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run_script(args: list[str], usage_jsonl: str,
               transcripts_dir: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["USAGE_JSONL"] = usage_jsonl
    cmd = [sys.executable, str(SCRIPT)] + args
    if transcripts_dir is not None:
        cmd += ["--transcripts-dir", transcripts_dir]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


# ========== Phase 1: 純粋関数 ==========

class TestProjectFromCwd:
    def test_project_from_cwd_returns_basename(self):
        assert rs._project_from_cwd("/Users/foo/chirper") == "chirper"

    def test_project_from_cwd_returns_empty_for_empty_string(self):
        assert rs._project_from_cwd("") == ""

    def test_project_from_cwd_handles_trailing_slash(self):
        assert rs._project_from_cwd("/Users/foo/chirper/") == "chirper"


class TestParseTimestamp:
    def test_parse_timestamp_converts_z_suffix(self):
        result = rs._parse_timestamp("2026-02-09T00:59:17.027Z")
        assert result == "2026-02-09T00:59:17.027000+00:00"

    def test_parse_timestamp_returns_original_on_invalid(self):
        original = "not-a-timestamp"
        assert rs._parse_timestamp(original) == original


# ========== Phase 2: _extract_events_from_row ==========

class TestExtractEventsFromRow:
    _TS = "2026-02-09T00:59:17.027Z"
    _TS_EXPECTED = "2026-02-09T00:59:17.027000+00:00"

    def _make_assistant_row(self, blocks: list[dict]) -> dict:
        return make_transcript_row(
            "assistant", "sess1", "/Users/foo/chirper", self._TS, blocks
        )

    def _make_user_row(self, content: str) -> dict:
        return make_transcript_row(
            "user", "sess1", "/Users/foo/chirper", self._TS, content
        )

    def test_skill_tool_event_from_assistant_row(self):
        row = self._make_assistant_row([make_skill_block("user-story-creation", "6")])
        events = rs._extract_events_from_row(row)
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "skill_tool"
        assert ev["skill"] == "user-story-creation"
        assert ev["args"] == "6"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "sess1"
        assert ev["timestamp"] == self._TS_EXPECTED

    def test_skill_args_none_becomes_empty_string(self):
        # input に args キー自体がない場合
        row = self._make_assistant_row([make_skill_block("webapp-testing")])
        events = rs._extract_events_from_row(row)
        assert len(events) == 1
        assert events[0]["args"] == ""

    def test_subagent_start_from_task_tool_use(self):
        row = self._make_assistant_row([make_task_block("Explore", "Task")])
        events = rs._extract_events_from_row(row)
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "subagent_start"
        assert ev["subagent_type"] == "Explore"
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "sess1"
        assert ev["timestamp"] == self._TS_EXPECTED

    def test_subagent_start_from_agent_tool_use(self):
        row = self._make_assistant_row([make_task_block("Plan", "Agent")])
        events = rs._extract_events_from_row(row)
        assert len(events) == 1
        assert events[0]["event_type"] == "subagent_start"
        assert events[0]["subagent_type"] == "Plan"

    def test_user_slash_command_from_user_row(self):
        content = "<command-name>/insights</command-name>\n<command-message>foo</command-message>"
        row = self._make_user_row(content)
        events = rs._extract_events_from_row(row)
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "user_slash_command"
        assert ev["skill"] == "/insights"
        assert ev["args"] == ""
        assert ev["project"] == "chirper"
        assert ev["session_id"] == "sess1"

    def test_builtin_command_is_excluded(self):
        content = "<command-name>/clear</command-name>"
        row = self._make_user_row(content)
        assert rs._extract_events_from_row(row) == []

    def test_all_builtin_commands_excluded(self):
        builtins = ["/exit", "/clear", "/help", "/compact", "/mcp", "/config",
                    "/model", "/resume", "/context", "/skills", "/hooks", "/fast"]
        for cmd in builtins:
            content = f"<command-name>{cmd}</command-name>"
            row = self._make_user_row(content)
            assert rs._extract_events_from_row(row) == [], f"{cmd} should be excluded"

    def test_non_skill_tool_use_is_ignored(self):
        # Bash など Skill/Task 以外は無視
        bash_block = {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
        row = self._make_assistant_row([bash_block])
        assert rs._extract_events_from_row(row) == []

    def test_unknown_row_type_returns_empty_list(self):
        row = {"type": "file-history-snapshot", "snapshot": {}}
        assert rs._extract_events_from_row(row) == []

    def test_multiple_tool_uses_in_one_row(self):
        # 1行に Skill + Task が混在 → 2イベント
        blocks = [
            make_skill_block("user-story-creation", "6"),
            make_task_block("Explore", "Task"),
        ]
        row = self._make_assistant_row(blocks)
        events = rs._extract_events_from_row(row)
        assert len(events) == 2
        assert events[0]["event_type"] == "skill_tool"
        assert events[1]["event_type"] == "subagent_start"


# ========== Phase 3: ファイル/ディレクトリスキャン ==========

class TestScanTranscriptFile:
    _TS = "2026-02-09T00:59:17.027Z"

    def test_scan_transcript_file_returns_empty_for_empty_file(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("", encoding="utf-8")
        assert rs._scan_transcript_file(f) == []

    def test_scan_transcript_file_returns_events_from_valid_file(self, tmp_path):
        row = make_transcript_row(
            "assistant", "s1", "/p/myapp", self._TS,
            [make_skill_block("my-skill", "1")]
        )
        f = tmp_path / "session.jsonl"
        write_jsonl(f, [row])
        events = rs._scan_transcript_file(f)
        assert len(events) == 1
        assert events[0]["event_type"] == "skill_tool"

    def test_scan_transcript_file_skips_invalid_json_lines(self, tmp_path):
        f = tmp_path / "session.jsonl"
        f.write_text('{"type": "file-history-snapshot"}\nnot valid json{{{\n', encoding="utf-8")
        # JSON エラーの行はスキップ、クラッシュしない
        events = rs._scan_transcript_file(f)
        assert events == []

    def test_scan_transcript_file_skips_blank_lines(self, tmp_path):
        row = make_transcript_row(
            "assistant", "s1", "/p/myapp", self._TS,
            [make_skill_block("my-skill")]
        )
        f = tmp_path / "session.jsonl"
        f.write_text("\n\n" + json.dumps(row) + "\n\n", encoding="utf-8")
        events = rs._scan_transcript_file(f)
        assert len(events) == 1


class TestFindTranscriptFiles:
    def test_find_transcript_files_returns_session_jsonl(self, tmp_path):
        proj_dir = tmp_path / "-Users-foo-myapp"
        proj_dir.mkdir()
        session_file = proj_dir / "abc123.jsonl"
        session_file.write_text("", encoding="utf-8")
        files = rs._find_transcript_files(tmp_path)
        assert session_file in files

    def test_find_transcript_files_skips_subagents_dir(self, tmp_path):
        proj_dir = tmp_path / "-Users-foo-myapp"
        subagents_dir = proj_dir / "abc123" / "subagents"
        subagents_dir.mkdir(parents=True)
        agent_file = subagents_dir / "agent-xxx.jsonl"
        agent_file.write_text("", encoding="utf-8")
        files = rs._find_transcript_files(tmp_path)
        assert agent_file not in files
        assert len(files) == 0

    def test_find_transcript_files_returns_empty_when_no_jsonl(self, tmp_path):
        # .jsonl ファイルがない場合
        files = rs._find_transcript_files(tmp_path)
        assert files == []

    def test_find_transcript_files_ignores_non_jsonl_files(self, tmp_path):
        proj_dir = tmp_path / "-Users-foo-myapp"
        proj_dir.mkdir()
        (proj_dir / "readme.txt").write_text("hello", encoding="utf-8")
        session_file = proj_dir / "session.jsonl"
        session_file.write_text("", encoding="utf-8")
        files = rs._find_transcript_files(tmp_path)
        assert session_file in files
        assert all(f.suffix == ".jsonl" for f in files)


# ========== Phase 4: scan_all ==========

class TestScanAll:
    _TS_EARLY = "2026-01-01T00:00:00.000Z"
    _TS_LATE = "2026-02-01T00:00:00.000Z"

    def _write_session(self, proj_dir: Path, name: str, rows: list[dict]) -> None:
        write_jsonl(proj_dir / name, rows)

    def test_scan_all_returns_events_sorted_by_timestamp(self, tmp_path):
        proj_dir = tmp_path / "-Users-foo-myapp"
        proj_dir.mkdir()

        row_late = make_transcript_row(
            "assistant", "s1", "/p/myapp", self._TS_LATE,
            [make_skill_block("skill-b")]
        )
        row_early = make_transcript_row(
            "assistant", "s1", "/p/myapp", self._TS_EARLY,
            [make_skill_block("skill-a")]
        )
        # ファイルに逆順で書く
        write_jsonl(proj_dir / "sess1.jsonl", [row_late, row_early])

        events = rs.scan_all(tmp_path)
        assert len(events) == 2
        assert events[0]["skill"] == "skill-a"
        assert events[1]["skill"] == "skill-b"

    def test_scan_all_events_without_timestamp_go_last(self, tmp_path):
        proj_dir = tmp_path / "-Users-foo-myapp"
        proj_dir.mkdir()

        row_with_ts = make_transcript_row(
            "assistant", "s1", "/p/myapp", self._TS_EARLY,
            [make_skill_block("skill-with-ts")]
        )
        # timestamp なし
        row_no_ts = {
            "type": "assistant",
            "sessionId": "s1",
            "cwd": "/p/myapp",
            "message": {"role": "assistant", "content": [make_skill_block("skill-no-ts")]},
        }
        write_jsonl(proj_dir / "sess1.jsonl", [row_no_ts, row_with_ts])

        events = rs.scan_all(tmp_path)
        assert len(events) == 2
        assert events[0]["skill"] == "skill-with-ts"
        assert events[1]["skill"] == "skill-no-ts"

    def test_scan_all_returns_empty_when_no_files(self, tmp_path):
        events = rs.scan_all(tmp_path)
        assert events == []

    def test_scan_all_prints_progress_to_stderr(self, tmp_path):
        proj_dir = tmp_path / "-Users-foo-myapp"
        proj_dir.mkdir()
        row = make_transcript_row(
            "assistant", "s1", "/p/myapp", self._TS_EARLY,
            [make_skill_block("my-skill")]
        )
        write_jsonl(proj_dir / "sess1.jsonl", [row])

        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            rs.scan_all(tmp_path)

        assert "Scanning" in stderr_buf.getvalue()


# ========== Phase 5: write_events ==========

class TestWriteEvents:
    def _make_event(self, skill: str) -> dict:
        return {
            "event_type": "skill_tool",
            "skill": skill,
            "args": "",
            "project": "myapp",
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00+00:00",
        }

    def test_write_events_overwrites_existing_file(self, tmp_path):
        out = tmp_path / "usage.jsonl"
        out.write_text(json.dumps(self._make_event("old-skill")) + "\n", encoding="utf-8")

        rs.write_events([self._make_event("new-skill")], out, append=False)

        events = read_events(out)
        assert len(events) == 1
        assert events[0]["skill"] == "new-skill"

    def test_write_events_append_mode_adds_to_existing(self, tmp_path):
        out = tmp_path / "usage.jsonl"
        rs.write_events([self._make_event("skill-a")], out, append=False)
        rs.write_events([self._make_event("skill-b")], out, append=True)

        events = read_events(out)
        assert len(events) == 2
        assert events[0]["skill"] == "skill-a"
        assert events[1]["skill"] == "skill-b"

    def test_write_events_creates_parent_directory(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c" / "usage.jsonl"
        rs.write_events([self._make_event("skill-x")], nested, append=False)
        assert nested.exists()

    def test_write_events_writes_valid_jsonl(self, tmp_path):
        out = tmp_path / "usage.jsonl"
        events = [self._make_event("skill-a"), self._make_event("skill-b")]
        rs.write_events(events, out, append=False)

        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)  # パースできることを確認
            assert "event_type" in obj


# ========== Phase 6: main CLI ==========

class TestMainCLI:
    _TS = "2026-01-15T10:00:00.000Z"

    def _write_transcript(self, transcripts_dir: Path,
                          proj: str, session: str, rows: list[dict]) -> None:
        proj_dir = transcripts_dir / proj
        proj_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl(proj_dir / f"{session}.jsonl", rows)

    def _skill_row(self, skill: str) -> dict:
        return make_transcript_row(
            "assistant", "s1", f"/p/{skill}", self._TS,
            [make_skill_block(skill)]
        )

    def test_main_dry_run_prints_count_and_no_file_created(self, tmp_path):
        transcripts_dir = tmp_path / "transcripts"
        self._write_transcript(transcripts_dir, "-p-myapp", "sess1",
                               [self._skill_row("my-skill")])
        usage_file = str(tmp_path / "usage.jsonl")

        result = run_script(["--dry-run"], usage_file,
                            transcripts_dir=str(transcripts_dir))

        assert result.returncode == 0
        assert "1" in result.stdout  # イベント数が表示される
        assert not Path(usage_file).exists()  # ファイルは作られない

    def test_main_default_overwrites_output_file(self, tmp_path):
        transcripts_dir = tmp_path / "transcripts"
        self._write_transcript(transcripts_dir, "-p-myapp", "sess1",
                               [self._skill_row("new-skill")])
        usage_file = tmp_path / "usage.jsonl"
        # 既存の内容を書いておく
        usage_file.write_text(
            json.dumps({"event_type": "skill_tool", "skill": "old-skill"}) + "\n"
        )

        run_script([], str(usage_file), transcripts_dir=str(transcripts_dir))

        events = read_events(usage_file)
        skills = [e["skill"] for e in events if "skill" in e]
        assert "old-skill" not in skills
        assert "new-skill" in skills

    def test_main_append_flag_preserves_existing_events(self, tmp_path):
        transcripts_dir = tmp_path / "transcripts"
        self._write_transcript(transcripts_dir, "-p-myapp", "sess1",
                               [self._skill_row("new-skill")])
        usage_file = tmp_path / "usage.jsonl"
        old_event = {"event_type": "skill_tool", "skill": "old-skill",
                     "args": "", "project": "myapp", "session_id": "s0",
                     "timestamp": "2025-01-01T00:00:00+00:00"}
        usage_file.write_text(json.dumps(old_event) + "\n", encoding="utf-8")

        run_script(["--append"], str(usage_file),
                   transcripts_dir=str(transcripts_dir))

        events = read_events(usage_file)
        skills = [e["skill"] for e in events if "skill" in e]
        assert "old-skill" in skills
        assert "new-skill" in skills

    def test_main_custom_transcripts_dir(self, tmp_path):
        custom_dir = tmp_path / "custom_transcripts"
        self._write_transcript(custom_dir, "-p-myapp", "sess1",
                               [self._skill_row("custom-skill")])
        usage_file = str(tmp_path / "usage.jsonl")

        result = run_script([], usage_file, transcripts_dir=str(custom_dir))

        assert result.returncode == 0
        events = read_events(usage_file)
        assert any(e.get("skill") == "custom-skill" for e in events)

    def test_main_output_contains_valid_jsonl(self, tmp_path):
        transcripts_dir = tmp_path / "transcripts"
        rows = [
            self._skill_row("skill-a"),
            make_transcript_row(
                "assistant", "s1", "/p/myapp", self._TS,
                [make_task_block("Explore", "Task")]
            ),
        ]
        self._write_transcript(transcripts_dir, "-p-myapp", "sess1", rows)
        usage_file = tmp_path / "usage.jsonl"

        run_script([], str(usage_file), transcripts_dir=str(transcripts_dir))

        lines = usage_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert "event_type" in obj
            assert "timestamp" in obj
