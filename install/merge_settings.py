"""install/merge_settings.py

~/.claude/settings.json に claude-transcript-analyzer の hooks エントリを
べき等にマージする。

Usage:
    python3 merge_settings.py <repo_dir>
"""
import json
import os
import shutil
import sys
from pathlib import Path


def _settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    return json.loads(text)


def _build_new_entries(repo_dir: str) -> dict:
    record_skill = f"python3 {repo_dir}/hooks/record_skill.py"
    record_subagent = f"python3 {repo_dir}/hooks/record_subagent.py"
    return {
        "PostToolUse": [
            {
                "matcher": "Skill",
                "hooks": [{"type": "command", "command": record_skill}],
            },
            {
                "matcher": "Task",
                "hooks": [{"type": "command", "command": record_subagent}],
            },
        ],
        "UserPromptSubmit": [
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": record_skill}],
            },
        ],
    }


def _merge_hook_list(existing: list, new_entries: list) -> list:
    """新しいエントリを既存リストにべき等にマージする（matcher で重複排除）。"""
    existing_matchers = {entry.get("matcher"): i for i, entry in enumerate(existing)}
    result = list(existing)
    for entry in new_entries:
        matcher = entry.get("matcher")
        if matcher in existing_matchers:
            # 既存エントリを上書き
            result[existing_matchers[matcher]] = entry
        else:
            result.append(entry)
    return result


def merge(repo_dir: str) -> None:
    settings_path = _settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings = _load_settings(settings_path)

    # バックアップ作成（ファイルが存在する場合）
    if settings_path.exists():
        shutil.copy2(settings_path, settings_path.with_suffix(".json.bak"))

    hooks = settings.setdefault("hooks", {})
    new_entries = _build_new_entries(repo_dir)

    for event_name, entries in new_entries.items():
        existing = hooks.get(event_name, [])
        hooks[event_name] = _merge_hook_list(existing, entries)

    settings["hooks"] = hooks
    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Updated: {settings_path}")


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <repo_dir>", file=sys.stderr)
        sys.exit(1)
    repo_dir = sys.argv[1]
    merge(repo_dir)


if __name__ == "__main__":
    main()
