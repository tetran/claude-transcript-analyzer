"""tests/test_verify_session.py — hooks/verify_session.py のテスト"""
import io
import json
import sys
from pathlib import Path

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
        # Issue #24: Claude Code 本体のエンコード規則 (slash, backslash, colon, dot
        # → '-') に揃える。POSIX dot 入り cwd の latent bug 修正と Win 互換を兼ねる。
        cwd_encoded = vs._encode_cwd(cwd)
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
        usage_file.write_text("", encoding="utf-8")
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
        usage_file.write_text("", encoding="utf-8")
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
        usage_file.write_text("", encoding="utf-8")
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

    # ---- Issue #51: actionable fields ----

    def test_alert_includes_kind_and_project_and_cwd(self, tmp_path):
        """Issue #51: kind / project / cwd が記録され「どこで起きたか」即座に分かる。"""
        session_id = "sess-issue51-kind"
        cwd = "/Users/foo/myapp"

        alerts = self._run(
            tmp_path, session_id, cwd,
            transcript_rows=[
                make_transcript_row("assistant", session_id, cwd, [make_task_block("Plan")]),
            ],
            usage_events=[],
        )
        assert len(alerts) == 1
        a = alerts[0]
        assert a["kind"] == "transcript_mismatch"
        assert a["project"] == "myapp"  # basename of cwd
        assert a["cwd"] == cwd

    def test_alert_includes_transcript_path(self, tmp_path):
        """Issue #51: 該当トランスクリプトのフルパスが記録される (= 直接開ける)。"""
        session_id = "sess-issue51-path"
        cwd = "/Users/foo/myapp"

        alerts = self._run(
            tmp_path, session_id, cwd,
            transcript_rows=[
                make_transcript_row("assistant", session_id, cwd, [make_task_block("Plan")]),
            ],
            usage_events=[],
        )
        assert len(alerts) == 1
        # transcript_path は claude_home (= tmp_path) 配下の実パス
        expected = tmp_path / "projects" / "-Users-foo-myapp" / f"{session_id}.jsonl"
        assert alerts[0]["transcript_path"] == str(expected)

    def test_alert_includes_missing_samples_per_type(self, tmp_path):
        """Issue #51: 欠損 type ごとに transcript_count / usage_count / delta が分かる。

        actionability: 「skill_tool が 1 件欠けた」だけでなく
        「transcript には 3 件あったが usage には 2 件しかない」と即座に分かる。
        """
        session_id = "sess-issue51-samples"
        cwd = "/Users/foo/myapp"

        alerts = self._run(
            tmp_path, session_id, cwd,
            transcript_rows=[
                # transcript: skill_tool 3 件 + subagent_start 2 件
                make_transcript_row("assistant", session_id, cwd,
                                    [make_skill_block("commit")] * 3),
                make_transcript_row("assistant", session_id, cwd,
                                    [make_task_block("Explore")] * 2),
            ],
            usage_events=[
                # usage: skill_tool 2 件 (1 件不足) + subagent_start 0 件 (2 件不足)
                make_usage_event("skill_tool", session_id, skill="commit", args=""),
                make_usage_event("skill_tool", session_id, skill="commit", args=""),
            ],
        )
        assert len(alerts) == 1
        samples = alerts[0]["missing_samples"]
        assert isinstance(samples, list)
        # samples は missing_types と同じ集合
        sample_types = {s["event_type"] for s in samples}
        assert sample_types == {"skill_tool", "subagent_start"}
        # 各 sample に delta が入る
        skill_sample = next(s for s in samples if s["event_type"] == "skill_tool")
        assert skill_sample["transcript_count"] == 3
        assert skill_sample["usage_count"] == 2
        assert skill_sample["delta"] == 1
        sub_sample = next(s for s in samples if s["event_type"] == "subagent_start")
        assert sub_sample["transcript_count"] == 2
        assert sub_sample["usage_count"] == 0
        assert sub_sample["delta"] == 2

    def test_project_falls_back_when_cwd_is_empty(self, tmp_path):
        """cwd が空のときも project は空文字 / kind は付く (堅牢性)。"""
        session_id = "sess-issue51-emptycwd"
        cwd = ""  # 空 cwd
        cwd_encoded = vs._encode_cwd(cwd)
        transcript_dir = tmp_path / "projects" / cwd_encoded
        transcript_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl(transcript_dir / f"{session_id}.jsonl", [
            make_transcript_row("assistant", session_id, cwd, [make_task_block("Plan")]),
        ])
        usage_file = tmp_path / "usage.jsonl"
        write_jsonl(usage_file, [])
        alerts_file = tmp_path / "health_alerts.jsonl"
        vs.handle_stop(
            session_id=session_id, cwd=cwd, claude_home=tmp_path,
            usage_file=usage_file, alerts_file=alerts_file,
        )
        alerts = read_jsonl(alerts_file)
        assert len(alerts) == 1
        assert alerts[0]["kind"] == "transcript_mismatch"
        # project は空文字でも構わない (落ちないことが大事)
        assert alerts[0]["project"] == ""

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
        usage_file.write_text("", encoding="utf-8")
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
        (tmp_path / "usage.jsonl").write_text("", encoding="utf-8")

        # main は例外を発生させない
        vs.main()

    def test_exits_cleanly_with_invalid_json(self, tmp_path, monkeypatch):
        """不正 JSON でも正常終了（クラッシュしない）"""
        monkeypatch.setattr("sys.stdin", io.StringIO("not valid json{{"))
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
        monkeypatch.setenv("HEALTH_ALERTS_JSONL", str(tmp_path / "health_alerts.jsonl"))
        monkeypatch.setenv("CLAUDE_HOME", str(tmp_path))
        (tmp_path / "usage.jsonl").write_text("", encoding="utf-8")

        vs.main()  # no exception

    def test_exits_cleanly_when_session_id_missing(self, tmp_path, monkeypatch):
        """session_id がない場合でも正常終了"""
        payload = json.dumps({"hook_event_name": "Stop", "cwd": "/Users/foo/myapp"})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
        monkeypatch.setenv("HEALTH_ALERTS_JSONL", str(tmp_path / "health_alerts.jsonl"))
        monkeypatch.setenv("CLAUDE_HOME", str(tmp_path))
        (tmp_path / "usage.jsonl").write_text("", encoding="utf-8")

        vs.main()  # no exception


class TestEncodeCwd:
    """Issue #24: cwd を Claude Code 本体の transcript ディレクトリ命名規則に揃える。
    slash, backslash, colon, dot を全て `-` に変換する。
    POSIX dot 入り cwd の latent bug 修正と Windows 互換を兼ねる。
    """

    def test_posix_basic_path_unchanged_behavior(self):
        """既存の POSIX 単純パス: スラッシュのみ → ハイフン (従来通り)。"""
        assert vs._encode_cwd("/Users/foo/myapp") == "-Users-foo-myapp"

    def test_posix_with_dot_in_path(self):
        """latent bug 修正: ドット入り POSIX パスもハイフンに変換。
        例: `/Users/foo/.worktrees/issue-1` → `-Users-foo--worktrees-issue-1`
        現状の `cwd.replace('/', '-')` だけでは `.worktrees` が残り transcript 解決が外れる。
        実機 ls ~/.claude/projects/ で `--worktrees-` パターンを確認済み。"""
        assert (
            vs._encode_cwd("/Users/foo/.worktrees/issue-1")
            == "-Users-foo--worktrees-issue-1"
        )

    def test_posix_with_dot_in_filename(self):
        """ドットを含むディレクトリ名 (例: `my.app`) もハイフンに変換。"""
        assert vs._encode_cwd("/Users/foo/my.app/sub") == "-Users-foo-my-app-sub"

    def test_windows_drive_path(self):
        """Windows ドライブ + バックスラッシュ + コロン → 全て `-` に変換。
        例: `C:\\Users\\foo\\myapp` → `C--Users-foo-myapp`"""
        assert vs._encode_cwd("C:\\Users\\foo\\myapp") == "C--Users-foo-myapp"

    def test_windows_path_with_dot(self):
        """Windows パス + ドット入りディレクトリ。
        例: `C:\\Users\\foo\\.config\\app` → `C--Users-foo--config-app`"""
        assert vs._encode_cwd("C:\\Users\\foo\\.config\\app") == "C--Users-foo--config-app"

    def test_transcript_path_uses_encoded_cwd(self, tmp_path):
        """_transcript_path() が _encode_cwd() を経由してパス組み立てする。"""
        # POSIX dot 入り
        p = vs._transcript_path(tmp_path, "/Users/foo/.worktrees/x", "sess-1")
        assert p == tmp_path / "projects" / "-Users-foo--worktrees-x" / "sess-1.jsonl"
        # Windows
        p = vs._transcript_path(tmp_path, "C:\\Users\\foo\\app", "sess-2")
        assert p == tmp_path / "projects" / "C--Users-foo-app" / "sess-2.jsonl"
