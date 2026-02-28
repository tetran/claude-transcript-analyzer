"""tests/test_merge_settings.py — install/merge_settings.py のテスト"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "install" / "merge_settings.py"
REPO_DIR = Path(__file__).parent.parent


def run_merge(settings_path: Path, repo_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(repo_dir)],
        capture_output=True,
        text=True,
        env={"HOME": str(settings_path.parent.parent), "PATH": "/usr/bin:/bin"},
        # settings_path = tmp_path/.claude/settings.json なので
        # HOME = tmp_path にする
    )


def run_merge_with_home(home: Path, repo_dir: Path) -> subprocess.CompletedProcess:
    import os
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(repo_dir)],
        capture_output=True,
        text=True,
        env=env,
    )


def read_settings(home: Path) -> dict:
    settings_file = home / ".claude" / "settings.json"
    return json.loads(settings_file.read_text())


class TestMergeSettings:
    def test_adds_hooks_to_empty_settings(self, tmp_path):
        # HOME/.claude/settings.json を空で作成
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings_file = claude_dir / "settings.json"
        settings_file.write_text("{}")

        result = run_merge_with_home(tmp_path, REPO_DIR)
        assert result.returncode == 0, result.stderr

        settings = read_settings(tmp_path)
        hooks = settings.get("hooks", {})
        assert "PostToolUse" in hooks
        assert "UserPromptSubmit" in hooks

    def test_post_tool_use_has_skill_and_task_matchers(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")

        run_merge_with_home(tmp_path, REPO_DIR)
        settings = read_settings(tmp_path)

        post_tool_use = settings["hooks"]["PostToolUse"]
        matchers = [entry["matcher"] for entry in post_tool_use]
        assert "Skill" in matchers
        assert "Task" in matchers

    def test_hook_commands_contain_repo_dir(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")

        run_merge_with_home(tmp_path, REPO_DIR)
        settings = read_settings(tmp_path)

        post_tool_use = settings["hooks"]["PostToolUse"]
        for entry in post_tool_use:
            for hook in entry["hooks"]:
                assert str(REPO_DIR) in hook["command"]

    def test_existing_hooks_are_preserved(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {
            "hooks": {
                "Stop": [
                    {"matcher": "", "hooks": [{"type": "command", "command": "echo done"}]}
                ]
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(existing))

        run_merge_with_home(tmp_path, REPO_DIR)
        settings = read_settings(tmp_path)

        # 既存の Stop フックが残っている
        assert "Stop" in settings["hooks"]
        assert settings["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo done"
        # 新しいエントリも追加されている
        assert "PostToolUse" in settings["hooks"]

    def test_backup_file_is_created(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text('{"existing": true}')

        run_merge_with_home(tmp_path, REPO_DIR)

        backup = claude_dir / "settings.json.bak"
        assert backup.exists()
        backup_data = json.loads(backup.read_text())
        assert backup_data.get("existing") is True

    def test_idempotent_when_run_twice(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")

        run_merge_with_home(tmp_path, REPO_DIR)
        run_merge_with_home(tmp_path, REPO_DIR)
        settings = read_settings(tmp_path)

        post_tool_use = settings["hooks"]["PostToolUse"]
        # Skill と Task のエントリが重複していない
        matchers = [entry["matcher"] for entry in post_tool_use]
        assert matchers.count("Skill") == 1
        assert matchers.count("Task") == 1

    def test_creates_claude_dir_if_missing(self, tmp_path):
        # .claude ディレクトリがない場合でも動作する
        result = run_merge_with_home(tmp_path, REPO_DIR)
        assert result.returncode == 0
        settings_file = tmp_path / ".claude" / "settings.json"
        assert settings_file.exists()
