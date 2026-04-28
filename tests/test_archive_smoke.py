"""tests/test_archive_smoke.py

scripts/archive_usage.py の CLI smoke test (Issue #30 D5)。

`USAGE_RETENTION_DAYS=1` で archive job を実機に近い形で起動し、
month 単位の `.jsonl.gz` 生成 + hot tier rewrite + state marker が
**実際に subprocess 経由で動く** ことを保証する。
これがないと TDD ユニットでは検出できない CLI 周りの env 解決 / log redirect /
exit code といった挙動の regression が握り潰される (Definition of Done より昇格)。
"""
import gzip
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
ARCHIVE_USAGE_PY = PROJECT_ROOT / "scripts" / "archive_usage.py"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock 限定")
class TestArchiveSmoke:
    def test_retention_one_day_archives_old_events(self, tmp_path):
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"
        state_file = tmp_path / ".archive_state.json"

        # 30 日前の event を 1 件、現在の event を 1 件 (retention=1 で前者だけ archive)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        recent_ts = datetime.now(timezone.utc).isoformat()
        old_event = {
            "event_type": "skill_tool",
            "session_id": "s_smoke",
            "timestamp": old_ts,
            "tool_use_id": "t_old",
            "skill": "/foo",
        }
        recent_event = {
            "event_type": "skill_tool",
            "session_id": "s_smoke",
            "timestamp": recent_ts,
            "tool_use_id": "t_recent",
            "skill": "/bar",
        }
        data_file.parent.mkdir(parents=True, exist_ok=True)
        with data_file.open("w", encoding="utf-8") as f:
            f.write(json.dumps(old_event) + "\n")
            f.write(json.dumps(recent_event) + "\n")

        env = os.environ.copy()
        env.update(
            {
                "USAGE_JSONL": str(data_file),
                "ARCHIVE_DIR": str(archive_dir),
                "ARCHIVE_STATE_FILE": str(state_file),
                "USAGE_JSONL_LOCK": str(tmp_path / "usage.jsonl.lock"),
                "USAGE_RETENTION_DAYS": "1",
                "HEALTH_ALERTS_JSONL": str(tmp_path / "health_alerts.jsonl"),
            }
        )

        result = subprocess.run(
            [sys.executable, str(ARCHIVE_USAGE_PY), "--log", "-"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"

        # archive ディレクトリに 1 つ以上の .jsonl.gz が存在
        archived_files = list(archive_dir.glob("*.jsonl.gz"))
        assert len(archived_files) == 1, (
            f"期待: 1 archive ファイル、実際: {[p.name for p in archived_files]}"
        )

        # archive の中身が old_event
        with gzip.open(archived_files[0], "rt", encoding="utf-8") as f:
            archived = [json.loads(line) for line in f if line.strip()]
        assert archived == [old_event]

        # hot tier には recent_event のみ
        hot_lines = [
            json.loads(line)
            for line in data_file.read_text().splitlines()
            if line.strip()
        ]
        assert hot_lines == [recent_event]

        # state marker が更新されている
        state = json.loads(state_file.read_text())
        assert "last_archived_month" in state
        assert "last_run_at" in state

    def test_idempotent_cli_invocations(self, tmp_path):
        """CLI で 2 回連続実行 → archive 内容と hot tier が同一."""
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"

        old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        old_event = {
            "event_type": "skill_tool",
            "session_id": "s_idem",
            "timestamp": old_ts,
            "tool_use_id": "t_old",
        }
        data_file.parent.mkdir(parents=True, exist_ok=True)
        with data_file.open("w", encoding="utf-8") as f:
            f.write(json.dumps(old_event) + "\n")

        env = os.environ.copy()
        env.update(
            {
                "USAGE_JSONL": str(data_file),
                "ARCHIVE_DIR": str(archive_dir),
                "ARCHIVE_STATE_FILE": str(tmp_path / ".archive_state.json"),
                "USAGE_JSONL_LOCK": str(tmp_path / "usage.jsonl.lock"),
                "USAGE_RETENTION_DAYS": "1",
                "HEALTH_ALERTS_JSONL": str(tmp_path / "health_alerts.jsonl"),
            }
        )

        for _ in range(2):
            result = subprocess.run(
                [sys.executable, str(ARCHIVE_USAGE_PY), "--log", "-"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"

        archived_files = list(archive_dir.glob("*.jsonl.gz"))
        assert len(archived_files) == 1
        with gzip.open(archived_files[0], "rt", encoding="utf-8") as f:
            archived = [json.loads(line) for line in f if line.strip()]
        assert archived == [old_event]
