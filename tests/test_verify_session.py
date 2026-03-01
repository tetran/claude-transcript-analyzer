"""tests/test_verify_session.py — hooks/verify_session.py のテスト"""
import io
import json
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).parent.parent / "hooks"
sys.path.insert(0, str(_HOOKS_DIR))

import verify_session as vs  # noqa: E402


# ---- helpers ----

def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def make_transcript_row(row_type: str, session_id: str, cwd: str, content) -> dict:
    return {
        "type": row_type,
        "sessionId": session_id,
        "cwd": cwd,
        "timestamp": "2026-03-01T10:00:00.000Z",
        "message": {"role": row_type, "content": content},
    }


def make_skill_block(skill: str, args: str = "") -> dict:
    return {"type": "tool_use", "name": "Skill", "input": {"skill": skill, "args": args}}


def make_task_block(subagent_type: str, tool_name: str = "Agent") -> dict:
    return {"type": "tool_use", "name": tool_name, "input": {"subagent_type": subagent_type}}


def make_usage_event(event_type: str, session_id: str, **kwargs) -> dict:
    ev = {
        "event_type": event_type,
        "session_id": session_id,
        "timestamp": "2026-03-01T10:05:00+00:00",  # hookの実行時刻（transcript とはズレる）
    }
    ev.update(kwargs)
    return ev


class TestHandleStop:
    def _run(
        self,
        tmp_path: Path,
        session_id: str,
        cwd: str,
        transcript_rows: list[dict],
        usage_events: list[dict],
        alerts_file: Path | None = None,
    ) -> list[dict]:
        cwd_encoded = cwd.replace("/", "-")
        transcript_dir = tmp_path / "projects" / cwd_encoded
        transcript_dir.mkdir(parents=True)
        write_jsonl(transcript_dir / f"{session_id}.jsonl", transcript_rows)

        usage_file = tmp_path / "usage.jsonl"
        write_jsonl(usage_file, usage_events)

        if alerts_file is None:
            alerts_file = tmp_path / "health_alerts.jsonl"

        vs.handle_stop(
            session_id=session_id,
            cwd=cwd,
            claude_home=tmp_path,
            usage_file=usage_file,
            alerts_file=alerts_file,
        )
        return read_jsonl(alerts_file)

    def test_no_alert_when_transcript_and_usage_match(self, tmp_path):
        """正常系: transcript と usage の件数が一致 → health_alerts が空のまま"""
        session_id = "sess-match"
        cwd = "/Users/foo/myapp"

        alerts = self._run(
            tmp_path, session_id, cwd,
            transcript_rows=[
                make_transcript_row("assistant", session_id, cwd, [make_task_block("Explore")]),
            ],
            usage_events=[
                make_usage_event("subagent_start", session_id, subagent_type="Explore"),
            ],
        )
        assert alerts == []

    def test_alert_when_subagent_missing_issue3_scenario(self, tmp_path):
        """Issue #3 再現: transcript に subagent_start 5件、usage に 0件 → アラート記録"""
        session_id = "sess-issue3"
        cwd = "/Users/foo/myapp"

        alerts = self._run(
            tmp_path, session_id, cwd,
            transcript_rows=[
                make_transcript_row("assistant", session_id, cwd,
                                    [make_task_block("Explore")] * 5),
            ],
            usage_events=[],
        )
        assert len(alerts) == 1
        assert alerts[0]["missing_count"] == 5
        assert "subagent_start" in alerts[0]["missing_types"]

    def test_alert_when_slash_command_missing_issue2_scenario(self, tmp_path):
        """Issue #2 再現: transcript に user_slash_command 2件、usage に 0件 → アラート記録"""
        session_id = "sess-issue2"
        cwd = "/Users/foo/myapp"
        cmd_content = "<command-name>/insights</command-name>"

        alerts = self._run(
            tmp_path, session_id, cwd,
            transcript_rows=[
                make_transcript_row("user", session_id, cwd, cmd_content),
                make_transcript_row("user", session_id, cwd, cmd_content),
            ],
            usage_events=[],
        )
        assert len(alerts) == 1
        assert alerts[0]["missing_count"] == 2
        assert "user_slash_command" in alerts[0]["missing_types"]

    def test_no_alert_when_transcript_not_found(self, tmp_path):
        """トランスクリプトが存在しない場合 → サイレント終了"""
        session_id = "sess-notfound"
        cwd = "/Users/foo/myapp"

        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("")
        alerts_file = tmp_path / "health_alerts.jsonl"

        # transcript ファイルは作らない
        vs.handle_stop(
            session_id=session_id,
            cwd=cwd,
            claude_home=tmp_path,
            usage_file=usage_file,
            alerts_file=alerts_file,
        )
        assert read_jsonl(alerts_file) == []

    def test_one_alert_per_session_even_when_count_grows(self, tmp_path):
        """設計仕様: missing_count が増えても同一セッション・同一タイプには2件目を出さない。

        1セッション1アラート設計を意図的に採用。
        ターンごとに件数が増えてもアラートは最初の1件のみ記録する。
        """
        session_id = "sess-grow"
        cwd = "/Users/foo/myapp"
        cwd_encoded = cwd.replace("/", "-")

        transcript_dir = tmp_path / "projects" / cwd_encoded
        transcript_dir.mkdir(parents=True)
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("")
        alerts_file = tmp_path / "health_alerts.jsonl"

        # 1回目: subagent_start が 1件不足
        write_jsonl(transcript_dir / f"{session_id}.jsonl", [
            make_transcript_row("assistant", session_id, cwd, [make_task_block("Explore")]),
        ])
        vs.handle_stop(session_id, cwd, claude_home=tmp_path,
                       usage_file=usage_file, alerts_file=alerts_file)

        assert len(read_jsonl(alerts_file)) == 1
        assert read_jsonl(alerts_file)[0]["missing_count"] == 1

        # 2回目: 同セッションで追加の turn が来て missing_count が 3 に増えた
        write_jsonl(transcript_dir / f"{session_id}.jsonl", [
            make_transcript_row("assistant", session_id, cwd,
                                [make_task_block("Explore")] * 3),
        ])
        vs.handle_stop(session_id, cwd, claude_home=tmp_path,
                       usage_file=usage_file, alerts_file=alerts_file)

        # 2件目は出ない（1セッション1アラート設計）
        alerts = read_jsonl(alerts_file)
        assert len(alerts) == 1

    def test_no_duplicate_alert_on_repeated_calls(self, tmp_path):
        """同セッションで2回呼ばれても重複アラートを出さない"""
        session_id = "sess-dup"
        cwd = "/Users/foo/myapp"
        cwd_encoded = cwd.replace("/", "-")

        transcript_dir = tmp_path / "projects" / cwd_encoded
        transcript_dir.mkdir(parents=True)
        write_jsonl(transcript_dir / f"{session_id}.jsonl", [
            make_transcript_row("assistant", session_id, cwd, [make_task_block("Explore")]),
        ])

        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("")
        alerts_file = tmp_path / "health_alerts.jsonl"

        vs.handle_stop(session_id, cwd, claude_home=tmp_path,
                       usage_file=usage_file, alerts_file=alerts_file)
        vs.handle_stop(session_id, cwd, claude_home=tmp_path,
                       usage_file=usage_file, alerts_file=alerts_file)

        alerts = read_jsonl(alerts_file)
        assert len(alerts) == 1

    def test_alert_contains_required_fields(self, tmp_path):
        """アラートに timestamp, session_id, missing_count, missing_types が含まれる"""
        session_id = "sess-fields"
        cwd = "/Users/foo/myapp"

        alerts = self._run(
            tmp_path, session_id, cwd,
            transcript_rows=[
                make_transcript_row("assistant", session_id, cwd, [make_task_block("Plan")]),
            ],
            usage_events=[],
        )
        assert len(alerts) == 1
        alert = alerts[0]
        assert "timestamp" in alert
        assert alert["session_id"] == session_id
        assert "missing_count" in alert
        assert "missing_types" in alert
        assert isinstance(alert["missing_types"], list)

    def test_cwd_encoding_keeps_leading_dash(self, tmp_path):
        """cwd エンコードで先頭の '-' が保持されること"""
        session_id = "sess-enc"
        cwd = "/Users/kkoichi/myapp"
        # encode: "-Users-kkoichi-myapp"（先頭 '-' を保持）
        cwd_encoded = cwd.replace("/", "-")  # "-Users-kkoichi-myapp"

        transcript_dir = tmp_path / "projects" / cwd_encoded
        transcript_dir.mkdir(parents=True)
        write_jsonl(transcript_dir / f"{session_id}.jsonl", [
            make_transcript_row("assistant", session_id, cwd, [make_task_block("Explore")]),
        ])

        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("")
        alerts_file = tmp_path / "health_alerts.jsonl"

        vs.handle_stop(
            session_id=session_id,
            cwd=cwd,
            claude_home=tmp_path,
            usage_file=usage_file,
            alerts_file=alerts_file,
        )
        # ファイルが見つかってアラートが出ることを確認（lstrip なら見つからない）
        alerts = read_jsonl(alerts_file)
        assert len(alerts) == 1

    def test_timestamp_difference_does_not_cause_false_positive(self, tmp_path):
        """transcript と usage で timestamp が違っても件数が一致すればアラートなし"""
        session_id = "sess-ts"
        cwd = "/Users/foo/myapp"

        cwd_encoded = cwd.replace("/", "-")
        transcript_dir = tmp_path / "projects" / cwd_encoded
        transcript_dir.mkdir(parents=True)
        # transcript の timestamp
        row = {
            "type": "assistant",
            "sessionId": session_id,
            "cwd": cwd,
            "timestamp": "2026-03-01T10:00:00.000Z",
            "message": {"content": [make_skill_block("commit")]},
        }
        write_jsonl(transcript_dir / f"{session_id}.jsonl", [row])

        usage_file = tmp_path / "usage.jsonl"
        # usage の timestamp は hook 実行時刻（ズレがある）
        write_jsonl(usage_file, [
            make_usage_event("skill_tool", session_id,
                             skill="commit", args="", project="myapp",
                             timestamp="2026-03-01T10:00:05+00:00"),
        ])

        alerts_file = tmp_path / "health_alerts.jsonl"
        vs.handle_stop(
            session_id=session_id,
            cwd=cwd,
            claude_home=tmp_path,
            usage_file=usage_file,
            alerts_file=alerts_file,
        )
        assert read_jsonl(alerts_file) == []

    def test_only_current_session_events_are_compared(self, tmp_path):
        """他のセッションのイベントは照合に含まれない"""
        session_id = "sess-current"
        other_session = "sess-other"
        cwd = "/Users/foo/myapp"

        cwd_encoded = cwd.replace("/", "-")
        transcript_dir = tmp_path / "projects" / cwd_encoded
        transcript_dir.mkdir(parents=True)
        write_jsonl(transcript_dir / f"{session_id}.jsonl", [
            make_transcript_row("assistant", session_id, cwd, [make_task_block("Explore")]),
        ])

        usage_file = tmp_path / "usage.jsonl"
        # 別セッションの subagent_start は current セッションの照合に使われない
        write_jsonl(usage_file, [
            make_usage_event("subagent_start", other_session, subagent_type="Explore"),
        ])

        alerts_file = tmp_path / "health_alerts.jsonl"
        vs.handle_stop(
            session_id=session_id,
            cwd=cwd,
            claude_home=tmp_path,
            usage_file=usage_file,
            alerts_file=alerts_file,
        )
        alerts = read_jsonl(alerts_file)
        assert len(alerts) == 1  # current セッションで 1 件不足


class TestMain:
    def test_exits_cleanly_with_valid_input(self, tmp_path, monkeypatch):
        """有効な入力で正常終了"""
        payload = json.dumps({
            "hook_event_name": "Stop",
            "session_id": "sess-main",
            "cwd": "/Users/foo/myapp",
        })
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
        monkeypatch.setenv("HEALTH_ALERTS_JSONL", str(tmp_path / "health_alerts.jsonl"))
        monkeypatch.setenv("CLAUDE_HOME", str(tmp_path))
        (tmp_path / "usage.jsonl").write_text("")

        # main は例外を発生させない
        vs.main()

    def test_exits_cleanly_with_invalid_json(self, tmp_path, monkeypatch):
        """不正 JSON でも正常終了（クラッシュしない）"""
        monkeypatch.setattr("sys.stdin", io.StringIO("not valid json{{"))
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
        monkeypatch.setenv("HEALTH_ALERTS_JSONL", str(tmp_path / "health_alerts.jsonl"))
        monkeypatch.setenv("CLAUDE_HOME", str(tmp_path))
        (tmp_path / "usage.jsonl").write_text("")

        vs.main()  # no exception

    def test_exits_cleanly_when_session_id_missing(self, tmp_path, monkeypatch):
        """session_id がない場合でも正常終了"""
        payload = json.dumps({"hook_event_name": "Stop", "cwd": "/Users/foo/myapp"})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
        monkeypatch.setenv("HEALTH_ALERTS_JSONL", str(tmp_path / "health_alerts.jsonl"))
        monkeypatch.setenv("CLAUDE_HOME", str(tmp_path))
        (tmp_path / "usage.jsonl").write_text("")

        vs.main()  # no exception
