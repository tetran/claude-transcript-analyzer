"""tests/test_hooks_python_resolution.py — Issue #33

Multi-OS Python ランチャ解決の回帰防止テスト。

採用案: bash POSIX `command -v` fallback (案 J)
    $(command -v python3 || command -v python) ${CLAUDE_PLUGIN_ROOT}/hooks/foo.py

加えて全 hooks/*.py に shebang `#!/usr/bin/env python3` を付与し、
`$()` が空展開した場合 (python3 / python どちらも不在) でも適切なエラーになるよう二重保険。

このテストは以下を assert する:
    1. hooks/hooks.json の全 hook command が正規 prefix で始まる
    2. hooks/*.py 5 ファイル全てが shebang `#!/usr/bin/env python3` で始まる
"""
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 採用案 J の正規 prefix。先頭一致でテスト。
# `"$(command -v python3 || command -v python)" ` (double-quote で囲んで Windows の
# スペース入りパス `C:\Program Files\Python311\python.exe` で word splitting
# されないようにする。codex P1 対応)
_LAUNCHER_PREFIX_RE = re.compile(
    r'^"\$\(command -v python3 \|\| command -v python\)" '
)

# shebang
_SHEBANG = "#!/usr/bin/env python3"

# hooks/ 配下の Python スクリプト (Phase 2 で shebang 付与対象)
_HOOK_SCRIPTS = [
    "launch_dashboard.py",
    "record_session.py",
    "record_skill.py",
    "record_subagent.py",
    "verify_session.py",
]


class TestHooksJsonPythonResolution:
    """hooks/hooks.json の全 command が `command -v` fallback prefix を使う。"""

    def setup_method(self):
        self.hooks_json_path = PROJECT_ROOT / "hooks" / "hooks.json"

    def _collect_commands(self) -> list[str]:
        data = json.loads(self.hooks_json_path.read_text(encoding="utf-8"))
        commands = []
        for hook_list in data["hooks"].values():
            for entry in hook_list:
                for hook in entry.get("hooks", []):
                    if hook.get("type") == "command":
                        commands.append(hook["command"])
        return commands

    def test_all_commands_use_command_v_fallback(self):
        """全 hook command が `$(command -v python3 || command -v python) ` で始まる。"""
        commands = self._collect_commands()
        assert len(commands) > 0, "hooks.json に command エントリが無い"
        for cmd in commands:
            assert _LAUNCHER_PREFIX_RE.match(cmd), (
                f"command が `command -v` fallback prefix で始まっとらん: {cmd!r}"
            )

    def test_no_bare_python_prefix(self):
        """素の `python ` / `python3 ` で始まる command が残っとらんことを確認。

        部分置換ミスやマージ事故の検知用。
        """
        commands = self._collect_commands()
        for cmd in commands:
            assert not cmd.startswith("python "), (
                f"`python ` 直書きが残っとった (案 J prefix にすべき): {cmd!r}"
            )
            assert not cmd.startswith("python3 "), (
                f"`python3 ` 直書きが残っとった (案 J prefix にすべき): {cmd!r}"
            )


class TestHookScriptsHaveShebang:
    """hooks/*.py 5 ファイル全てが shebang `#!/usr/bin/env python3` で始まる。

    案 J の `$()` が空展開した場合の二重保険。chmod +x と組み合わせて
    `env: 'python3': No such file` という適切なエラーを出す。
    """

    def test_all_hook_scripts_start_with_shebang(self):
        hooks_dir = PROJECT_ROOT / "hooks"
        for script_name in _HOOK_SCRIPTS:
            path = hooks_dir / script_name
            assert path.exists(), f"hooks/{script_name} が存在せん"
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
            assert first_line == _SHEBANG, (
                f"hooks/{script_name} の 1 行目が shebang やない: {first_line!r}"
            )
