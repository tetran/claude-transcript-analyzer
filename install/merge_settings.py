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
    # Issue #33: bash POSIX `command -v` fallback で python3/python の有無に依らず動く。
    # double-quote で囲み、Windows のスペース入りパス
    # (`C:\Program Files\Python311\python.exe`) で word splitting されないようにする
    # (codex P1 対応)。
    launcher = '"$(command -v python3 || command -v python)"'
    record_skill = f"{launcher} {repo_dir}/hooks/record_skill.py"
    record_subagent = f"{launcher} {repo_dir}/hooks/record_subagent.py"
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
    return (
        f'"$(command -v python3 || command -v python)" '
        f"{repo_dir}/hooks/verify_session.py"
    )


def _merge_stop_hook_list(existing: list, command: str) -> list:
    """Stop hook をコマンド文字列でべき等にマージする。

    Issue #33 codex P3: dedup は **`verify_session.py` パス marker** で行う。
    旧形式 (`python /repo/.../verify_session.py`) を持つ 0.5.0 ユーザーが
    0.5.1 にアップグレードしたとき、新形式
    (`"$(command -v python3 || command -v python)" /repo/.../verify_session.py`)
    が exact-match dedup で別物扱いになり 2 重登録されるのを防ぐ。

    挙動:
    - 既存 entry の中に `verify_session.py` を呼ぶ hook があれば、その hook の
      command を新形式で **置き換え**（旧形式からの自動マイグレーション）
    - 同 entry 内に複数の `verify_session.py` 参照があれば最初の 1 件だけ残す
    - `verify_session.py` を含まない他の Stop フック (例: ユーザー独自の
      `echo done`) は保持する
    - そもそも `verify_session.py` の参照が無ければ、末尾に新規 entry を追加
    - 形式が不正なエントリはそのまま保持してスキップ
    """
    target_marker = "verify_session.py"
    result: list = []
    found = False
    for entry in existing:
        if not isinstance(entry, dict):
            result.append(entry)
            continue
        hooks = entry.get("hooks", [])
        if not isinstance(hooks, list):
            result.append(entry)
            continue
        new_hooks: list = []
        for hook in hooks:
            if (
                isinstance(hook, dict)
                and isinstance(hook.get("command"), str)
                and target_marker in hook["command"]
            ):
                if not found:
                    # 既存の verify_session.py 参照を新形式 command に置き換える
                    new_hooks.append({"type": "command", "command": command})
                    found = True
                # 重複（同じ entry / 別 entry の重複参照）は drop
            else:
                new_hooks.append(hook)
        # 全 hook が drop された entry は除去（空 hooks の dangling entry を残さない）
        if new_hooks:
            new_entry = dict(entry)
            new_entry["hooks"] = new_hooks
            result.append(new_entry)
    if not found:
        result.append({"hooks": [{"type": "command", "command": command}]})
    return result


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
