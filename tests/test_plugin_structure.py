"""tests/test_plugin_structure.py

プラグイン構造（.claude-plugin/plugin.json, hooks/hooks.json）を検証するテスト。
"""
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


class TestPluginJson:
    def setup_method(self):
        self.plugin_json_path = PROJECT_ROOT / ".claude-plugin" / "plugin.json"

    def test_file_exists(self):
        assert self.plugin_json_path.exists(), ".claude-plugin/plugin.json が存在せんかった"

    def test_valid_json(self):
        content = self.plugin_json_path.read_text(encoding="utf-8")
        data = json.loads(content)
        assert isinstance(data, dict)

    def test_has_name(self):
        data = json.loads(self.plugin_json_path.read_text(encoding="utf-8"))
        assert "name" in data
        assert isinstance(data["name"], str)
        assert data["name"] != ""

    def test_has_description(self):
        data = json.loads(self.plugin_json_path.read_text(encoding="utf-8"))
        assert "description" in data
        assert isinstance(data["description"], str)
        assert data["description"] != ""

    def test_has_author(self):
        data = json.loads(self.plugin_json_path.read_text(encoding="utf-8"))
        assert "author" in data
        assert isinstance(data["author"], dict)
        assert "name" in data["author"]


class TestHooksJson:
    def setup_method(self):
        self.hooks_json_path = PROJECT_ROOT / "hooks" / "hooks.json"

    def test_file_exists(self):
        assert self.hooks_json_path.exists(), "hooks/hooks.json が存在せんかった"

    def test_valid_json(self):
        content = self.hooks_json_path.read_text(encoding="utf-8")
        data = json.loads(content)
        assert isinstance(data, dict)

    def test_has_hooks_key(self):
        data = json.loads(self.hooks_json_path.read_text(encoding="utf-8"))
        assert "hooks" in data

    def test_has_post_tool_use(self):
        data = json.loads(self.hooks_json_path.read_text(encoding="utf-8"))
        assert "PostToolUse" in data["hooks"]

    def test_has_user_prompt_submit(self):
        data = json.loads(self.hooks_json_path.read_text(encoding="utf-8"))
        assert "UserPromptSubmit" in data["hooks"]

    def test_has_user_prompt_expansion(self):
        """Issue #7: UserPromptExpansion でスラッシュコマンド展開を直接観測"""
        data = json.loads(self.hooks_json_path.read_text(encoding="utf-8"))
        assert "UserPromptExpansion" in data["hooks"]

    def test_has_stop(self):
        data = json.loads(self.hooks_json_path.read_text(encoding="utf-8"))
        assert "Stop" in data["hooks"]

    def _collect_commands(self, data: dict) -> list[str]:
        """hooks.json から全 command 文字列を収集する。"""
        commands = []
        for hook_list in data["hooks"].values():
            for entry in hook_list:
                for hook in entry.get("hooks", []):
                    if hook.get("type") == "command":
                        commands.append(hook["command"])
        return commands

    def test_plugin_root_var_used(self):
        """全コマンドが ${CLAUDE_PLUGIN_ROOT} を使っとることを確認する。"""
        data = json.loads(self.hooks_json_path.read_text(encoding="utf-8"))
        commands = self._collect_commands(data)
        assert len(commands) > 0
        for cmd in commands:
            assert "${CLAUDE_PLUGIN_ROOT}" in cmd, (
                f"コマンドに ${'{CLAUDE_PLUGIN_ROOT}'} が含まれとらんかった: {cmd}"
            )

    def test_record_skill_referenced(self):
        """record_skill.py が PostToolUse か UserPromptSubmit から参照されとることを確認する。"""
        data = json.loads(self.hooks_json_path.read_text(encoding="utf-8"))
        commands = self._collect_commands(data)
        assert any("record_skill.py" in cmd for cmd in commands)

    def test_record_subagent_referenced(self):
        """record_subagent.py が PostToolUse から参照されとることを確認する。"""
        data = json.loads(self.hooks_json_path.read_text(encoding="utf-8"))
        commands = self._collect_commands(data)
        assert any("record_subagent.py" in cmd for cmd in commands)

    def test_verify_session_referenced(self):
        """verify_session.py が Stop から参照されとることを確認する。"""
        data = json.loads(self.hooks_json_path.read_text(encoding="utf-8"))
        commands = self._collect_commands(data)
        assert any("verify_session.py" in cmd for cmd in commands)

    def test_launch_archive_referenced_on_session_start(self):
        """Issue #30 Phase C: launch_archive.py が SessionStart に登録されとる。"""
        data = json.loads(self.hooks_json_path.read_text(encoding="utf-8"))
        session_start = data["hooks"]["SessionStart"]
        commands = []
        for entry in session_start:
            for hook in entry.get("hooks", []):
                if hook.get("type") == "command":
                    commands.append(hook["command"])
        assert any("launch_archive.py" in cmd for cmd in commands)


class TestPhase30Files:
    """Issue #30 で追加された主要ファイルの存在確認。"""

    def test_archive_usage_script_exists(self):
        assert (PROJECT_ROOT / "scripts" / "archive_usage.py").exists()

    def test_launch_archive_hook_exists(self):
        assert (PROJECT_ROOT / "hooks" / "launch_archive.py").exists()

    def test_append_helper_exists(self):
        assert (PROJECT_ROOT / "hooks" / "_append.py").exists()

    def test_launcher_common_exists(self):
        assert (PROJECT_ROOT / "hooks" / "_launcher_common.py").exists()

    def test_archive_loader_exists(self):
        assert (PROJECT_ROOT / "reports" / "_archive_loader.py").exists()

    def test_usage_archive_command_exists(self):
        assert (PROJECT_ROOT / "commands" / "usage-archive.md").exists()

