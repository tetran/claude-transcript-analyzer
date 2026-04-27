"""install/merge_settings.py

~/.claude/settings.json に claude-transcript-analyzer の hooks エントリを
べき等にマージする。

Usage:
    python merge_settings.py <repo_dir>
"""
import json
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
    record_skill = f"python {repo_dir}/hooks/record_skill.py"
    record_subagent = f"python {repo_dir}/hooks/record_subagent.py"
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


def _stop_hook_command(repo_dir: str) -> str:
    return f"python {repo_dir}/hooks/verify_session.py"


def _merge_stop_hook_list(existing: list, command: str) -> list:
    """Stop hook をコマンド文字列でべき等にマージする。

    既存エントリのいずれかに同一コマンドが含まれていれば追加しない。
    既存の Stop フックは保持される。形式が不正なエントリはスキップする。
    """
    for entry in existing:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks", [])
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if isinstance(hook, dict) and hook.get("command") == command:
                return existing
    new_entry = {"hooks": [{"type": "command", "command": command}]}
    return list(existing) + [new_entry]


def _merge_hook_list(existing: list, new_entries: list) -> list:
    """新しいエントリを既存リストにべき等にマージする（matcher で重複排除）。

    同一 matcher の既存エントリは置換される。matcher ベースの置換は意図した仕様であり、
    claude-transcript-analyzer の Skill/Task フックを更新する用途を想定している。
    """
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

    # Stop hook の追加
    stop_cmd = _stop_hook_command(repo_dir)
    hooks["Stop"] = _merge_stop_hook_list(hooks.get("Stop", []), stop_cmd)

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
