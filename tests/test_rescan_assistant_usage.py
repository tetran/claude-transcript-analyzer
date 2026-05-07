"""tests/test_rescan_assistant_usage.py — rescan の assistant_usage backfill 専用テスト (#104)。

カバー範囲:
- main transcript からの backfill (source="main")
- per-subagent transcript からの backfill (source="subagent")
- Issue #93 filter (subagent_type == "" skip)
- (session_id, message_id) first-wins dedup (idempotent)
- live hook ↔ rescan の二重観測 dedup
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _ROOT / "scripts"
_HOOKS_DIR = _ROOT / "hooks"
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_HOOKS_DIR))

SCRIPT = _SCRIPTS_DIR / "rescan_transcripts.py"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _assistant_rec(msg_id: str, ts: str = "2026-05-01T10:00:00.000Z",
                   model: str = "claude-sonnet-4-6") -> dict:
    return {
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "id": msg_id,
            "model": model,
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "content": [],
        },
    }


def _task_block(subagent_type: str, tool_use_id: str) -> dict:
    return {
        "type": "tool_use",
        "name": "Task",
        "id": tool_use_id,
        "input": {"subagent_type": subagent_type, "description": "..."},
    }


def _assistant_row_with_blocks(session_id: str, cwd: str, ts: str,
                                blocks: list[dict]) -> dict:
    return {
        "type": "assistant",
        "sessionId": session_id,
        "cwd": cwd,
        "timestamp": ts,
        "message": {"role": "assistant", "content": blocks},
    }


def _run_rescan(transcripts_dir: Path, usage_file: Path,
                extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["USAGE_JSONL"] = str(usage_file)
    cmd = [sys.executable, str(SCRIPT),
           "--transcripts-dir", str(transcripts_dir)] + (extra_args or [])
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _au_events(usage_file: Path) -> list[dict]:
    return [e for e in _read_events(usage_file)
            if e.get("event_type") == "assistant_usage"]


# ---------------------------------------------------------------------------
# TestRescanBackfill
# ---------------------------------------------------------------------------

class TestRescanBackfill:
    def test_main_transcript_assistant_usage_backfilled(self, tmp_path):
        transcripts_dir = tmp_path / "projects"
        proj_dir = transcripts_dir / "-Users-foo-myproj"
        session_file = proj_dir / "sess1.jsonl"
        _write_jsonl(session_file, [
            {"type": "user", "sessionId": "sess1", "cwd": "/Users/foo/myproj",
             "timestamp": "2026-05-01T09:59:00.000Z",
             "message": {"role": "user", "content": "hi"}},
            _assistant_rec("msg1", "2026-05-01T10:00:00.000Z"),
            _assistant_rec("msg2", "2026-05-01T10:01:00.000Z"),
        ])
        usage_file = tmp_path / "usage.jsonl"
        _run_rescan(transcripts_dir, usage_file)

        au = _au_events(usage_file)
        assert len(au) == 2
        assert all(e["source"] == "main" for e in au)
        assert {e["message_id"] for e in au} == {"msg1", "msg2"}

    def test_message_without_id_is_skipped(self, tmp_path):
        transcripts_dir = tmp_path / "projects"
        session_file = transcripts_dir / "-p-proj" / "sess1.jsonl"
        _write_jsonl(session_file, [
            {"timestamp": "2026-05-01T10:00:00.000Z",
             "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                         "usage": {"input_tokens": 10, "output_tokens": 5},
                         "content": []}},  # no "id"
            _assistant_rec("msg_ok", "2026-05-01T10:00:01.000Z"),
        ])
        usage_file = tmp_path / "usage.jsonl"
        _run_rescan(transcripts_dir, usage_file)

        au = _au_events(usage_file)
        assert len(au) == 1
        assert au[0]["message_id"] == "msg_ok"

    def test_naive_timestamp_is_skipped(self, tmp_path):
        transcripts_dir = tmp_path / "projects"
        session_file = transcripts_dir / "-p-proj" / "sess1.jsonl"
        _write_jsonl(session_file, [
            {"timestamp": "2026-05-01T10:00:00",  # no tz = naive
             "message": {"role": "assistant", "id": "msg_naive",
                         "model": "claude-sonnet-4-6",
                         "usage": {"input_tokens": 1, "output_tokens": 1},
                         "content": []}},
            _assistant_rec("msg_ok", "2026-05-01T10:00:01.000Z"),
        ])
        usage_file = tmp_path / "usage.jsonl"
        _run_rescan(transcripts_dir, usage_file)

        au = _au_events(usage_file)
        assert len(au) == 1
        assert au[0]["message_id"] == "msg_ok"

    def test_per_subagent_transcript_backfilled_with_subagent_source(self, tmp_path):
        transcripts_dir = tmp_path / "projects"
        proj_dir = transcripts_dir / "-Users-foo-myproj"
        session_file = proj_dir / "sess1.jsonl"

        # main transcript: 1 Task block (subagent_type="Explore", id="agent-A")
        _write_jsonl(session_file, [
            _assistant_row_with_blocks(
                "sess1", "/Users/foo/myproj", "2026-05-01T10:00:00.000Z",
                [_task_block("Explore", "agent-A")],
            ),
        ])
        # per-subagent transcript
        sa_file = proj_dir / "sess1" / "subagents" / "agent-agent-A.jsonl"
        _write_jsonl(sa_file, [_assistant_rec("sa_msg1", "2026-05-01T10:01:00.000Z")])

        usage_file = tmp_path / "usage.jsonl"
        _run_rescan(transcripts_dir, usage_file)

        au = _au_events(usage_file)
        sa_events = [e for e in au if e["source"] == "subagent"]
        assert len(sa_events) == 1
        assert sa_events[0]["message_id"] == "sa_msg1"

    def test_per_subagent_with_empty_subagent_type_skipped(self, tmp_path):
        transcripts_dir = tmp_path / "projects"
        proj_dir = transcripts_dir / "-Users-foo-myproj"
        session_file = proj_dir / "sess1.jsonl"

        # subagent_type="" → Issue #93 filter で除外
        _write_jsonl(session_file, [
            _assistant_row_with_blocks(
                "sess1", "/Users/foo/myproj", "2026-05-01T10:00:00.000Z",
                [_task_block("", "agent-B")],
            ),
        ])
        sa_file = proj_dir / "sess1" / "subagents" / "agent-agent-B.jsonl"
        _write_jsonl(sa_file, [_assistant_rec("sa_msg2", "2026-05-01T10:01:00.000Z")])

        usage_file = tmp_path / "usage.jsonl"
        _run_rescan(transcripts_dir, usage_file)

        au = _au_events(usage_file)
        assert all(e["source"] != "subagent" for e in au)

    def test_orphan_per_subagent_file_without_main_task_block_skipped(self, tmp_path):
        transcripts_dir = tmp_path / "projects"
        proj_dir = transcripts_dir / "-Users-foo-myproj"
        session_file = proj_dir / "sess1.jsonl"

        # main transcript に Task block なし
        _write_jsonl(session_file, [
            {"type": "user", "sessionId": "sess1", "cwd": "/Users/foo/myproj",
             "timestamp": "2026-05-01T10:00:00.000Z",
             "message": {"role": "user", "content": "hi"}},
        ])
        # orphan per-subagent ファイル (main 側に対応 Task block なし)
        sa_file = proj_dir / "sess1" / "subagents" / "agent-orphan-C.jsonl"
        _write_jsonl(sa_file, [_assistant_rec("orphan_msg", "2026-05-01T10:01:00.000Z")])

        usage_file = tmp_path / "usage.jsonl"
        _run_rescan(transcripts_dir, usage_file)

        au = _au_events(usage_file)
        assert all(e.get("source") != "subagent" for e in au)


# ---------------------------------------------------------------------------
# TestRescanIdempotent
# ---------------------------------------------------------------------------

class TestRescanIdempotent:
    def test_rescan_twice_does_not_increase_events(self, tmp_path):
        transcripts_dir = tmp_path / "projects"
        session_file = transcripts_dir / "-p-proj" / "sess1.jsonl"
        _write_jsonl(session_file, [_assistant_rec("m1", "2026-05-01T10:00:00.000Z")])
        usage_file = tmp_path / "usage.jsonl"

        _run_rescan(transcripts_dir, usage_file)
        count_after_first = len(_au_events(usage_file))
        _run_rescan(transcripts_dir, usage_file)
        count_after_second = len(_au_events(usage_file))

        assert count_after_first == 1
        assert count_after_second == count_after_first

    def test_rescan_then_live_then_rescan_idempotent(self, tmp_path):
        transcripts_dir = tmp_path / "projects"
        session_file = transcripts_dir / "-p-proj" / "sess1.jsonl"
        _write_jsonl(session_file, [_assistant_rec("m1", "2026-05-01T10:00:00.000Z")])
        usage_file = tmp_path / "usage.jsonl"

        _run_rescan(transcripts_dir, usage_file)

        # live hook 相当: 新規 message を 1 件追加
        live_event = {
            "event_type": "assistant_usage",
            "session_id": "sess1",
            "message_id": "m_live",
            "source": "main",
            "timestamp": "2026-05-01T10:02:00+00:00",
            "model": "claude-sonnet-4-6",
            "input_tokens": 50, "output_tokens": 20,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "project": "proj",
        }
        with usage_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(live_event) + "\n")

        count_before_second_rescan = len(_au_events(usage_file))
        _run_rescan(transcripts_dir, usage_file)
        count_after_second_rescan = len(_au_events(usage_file))

        assert count_after_second_rescan == count_before_second_rescan

    def test_rescan_twice_keeps_assistant_usage_count_stable_but_doubles_skill_tool_count(
        self, tmp_path
    ):
        """assistant_usage and session_start are deduped; skill_tool intentionally not.

        AC scope is `assistant_usage` + `session_start` idempotency only.
        skill_tool / subagent_start / user_slash_command are intentionally not deduped —
        use `--overwrite` for clean reset (see docs/reference/prompt-persistence.md v0.8.0).
        """
        transcripts_dir = tmp_path / "projects"
        session_file = transcripts_dir / "-p-proj" / "sess1.jsonl"
        skill_row = {
            "type": "assistant",
            "sessionId": "sess1", "cwd": "/p/proj",
            "timestamp": "2026-05-01T10:00:00.000Z",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Skill",
                 "input": {"skill": "my-skill", "args": ""}},
            ]},
        }
        _write_jsonl(session_file, [
            {"type": "user", "sessionId": "sess1", "cwd": "/p/proj",
             "timestamp": "2026-05-01T09:59:00.000Z",
             "message": {"role": "user", "content": "hi"}},
            skill_row,
            _assistant_rec("m1", "2026-05-01T10:00:01.000Z"),
        ])
        usage_file = tmp_path / "usage.jsonl"

        _run_rescan(transcripts_dir, usage_file)
        au_after_1 = len(_au_events(usage_file))
        skill_after_1 = sum(
            1 for e in _read_events(usage_file) if e.get("event_type") == "skill_tool"
        )
        session_start_after_1 = sum(
            1 for e in _read_events(usage_file) if e.get("event_type") == "session_start"
        )

        _run_rescan(transcripts_dir, usage_file)
        au_after_2 = len(_au_events(usage_file))
        skill_after_2 = sum(
            1 for e in _read_events(usage_file) if e.get("event_type") == "skill_tool"
        )
        session_start_after_2 = sum(
            1 for e in _read_events(usage_file) if e.get("event_type") == "session_start"
        )

        assert au_after_2 == au_after_1             # (a) assistant_usage は増えない
        assert session_start_after_2 == session_start_after_1  # (b) session_start も増えない
        assert skill_after_2 == skill_after_1 * 2  # (c) skill_tool は意図的に 2 倍


# ---------------------------------------------------------------------------
# TestLiveAndRescanNoDuplicate
# ---------------------------------------------------------------------------

class TestLiveAndRescanNoDuplicate:
    def test_live_recorded_event_not_duplicated_by_rescan(self, tmp_path):
        """既存 usage.jsonl に live hook 経由で書かれた event がある場合に重複しない。"""
        transcripts_dir = tmp_path / "projects"
        session_file = transcripts_dir / "-p-proj" / "sess1.jsonl"
        _write_jsonl(session_file, [_assistant_rec("m1", "2026-05-01T10:00:00.000Z")])

        # live hook 相当: 先に (sess1, m1) を書いておく
        usage_file = tmp_path / "usage.jsonl"
        live_event = {
            "event_type": "assistant_usage",
            "session_id": "sess1",
            "message_id": "m1",
            "source": "main",
            "timestamp": "2026-05-01T10:00:00+00:00",
            "model": "claude-sonnet-4-6",
            "input_tokens": 100, "output_tokens": 50,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "project": "proj",
        }
        _write_jsonl(usage_file, [live_event])

        _run_rescan(transcripts_dir, usage_file)

        au = _au_events(usage_file)
        m1_count = sum(1 for e in au if e["message_id"] == "m1")
        assert m1_count == 1
