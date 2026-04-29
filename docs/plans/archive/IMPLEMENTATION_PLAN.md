# Skills & Subagents 使用状況の自動収集機能

## Context

現在、Skills と Subagents の使用状況を調べる手順が `skill-usage-analysis.md` と `subagent-usage-analysis.md` に手動手順として記録されている。
毎回 `.jsonl` ファイルを手動で grep/parse する必要があり手間がかかる。
Claude Code の Hooks 機能を使い、使用時に自動でイベントを収集する仕組みに変える。

## 採用方式: PostToolUse + UserPromptSubmit Hook

3つの方式を比較した結果、**Hook リアルタイム収集方式**を採用する。

| 方式 | 評価 |
|------|------|
| PostToolUse Hook | ✅ ツール呼び出しのたびに自動発火、JSONL 再 parse 不要 |
| Stop Hook + JSONL 再 parse | △ セッション末にファイル全体を読み直す必要あり、処理済み管理が複雑 |
| 定期バッチ (cron 等) | ✗ 自動収集にならない、オフセット管理が必要 |

### 収集対象イベント

1. **PostToolUse(Skill)** — アシスタントが `Skill` ツールを呼び出したとき
2. **PostToolUse(Task)** — アシスタントが `Task` ツール (Subagent) を呼び出したとき
3. **UserPromptSubmit** — ユーザーが `/skill-name` を直接打ったとき

## ファイル構成

```
claude-transcript-analyzer/
├── hooks/
│   ├── record_skill.py       # PostToolUse(Skill) + UserPromptSubmit 処理
│   └── record_subagent.py    # PostToolUse(Task) 処理
├── data/
│   └── usage.jsonl           # append-only イベントログ（自動生成）
├── reports/
│   └── summary.py            # 集計レポート表示
└── tests/
    ├── test_record_skill.py
    ├── test_record_subagent.py
    └── test_summary.py
```

## 各ファイルの仕様

### `hooks/record_skill.py`

stdin から Claude Code が渡す JSON を読み、以下の2イベントを処理：

**PostToolUse(Skill) の stdin 例:**
```json
{
  "hook_event_name": "PostToolUse",
  "tool_name": "Skill",
  "tool_input": { "skill": "user-story-creation", "args": "6" },
  "session_id": "abc123",
  "cwd": "/Users/kkoichi/Developer/personal/chirper"
}
```

**UserPromptSubmit の stdin 例:**
```json
{
  "hook_event_name": "UserPromptSubmit",
  "prompt": "<command-name>/insights</command-name>\n...",
  "session_id": "abc123",
  "cwd": "/Users/kkoichi/Developer/personal/chirper"
}
```

出力イベント形式 (`data/usage.jsonl` に1行追記):
```json
{"event_type": "skill_tool", "skill": "user-story-creation", "args": "6", "project": "chirper", "session_id": "abc123", "timestamp": "2026-02-28T10:00:00+00:00"}
{"event_type": "user_slash_command", "skill": "/insights", "args": "", "project": "chirper", "session_id": "abc123", "timestamp": "2026-02-28T10:05:00+00:00"}
```

除外すべき組み込みコマンド（スクリプト内定数）:
```
/exit /clear /help /compact /mcp /config /model /resume /context /skills /hooks /fast
```

### `hooks/record_subagent.py`

**PostToolUse(Task) の stdin 例:**
```json
{
  "hook_event_name": "PostToolUse",
  "tool_name": "Task",
  "tool_input": { "subagent_type": "Explore", "description": "...", "run_in_background": false },
  "session_id": "abc123",
  "cwd": "/Users/kkoichi/Developer/personal/chirper"
}
```

出力イベント形式:
```json
{"event_type": "subagent_start", "subagent_type": "Explore", "project": "chirper", "session_id": "abc123", "timestamp": "2026-02-28T10:06:00+00:00"}
```

### テスト隔離

`DATA_FILE` パスを環境変数 `USAGE_JSONL` でオーバーライドできるようにする:
```python
DATA_FILE = os.environ.get('USAGE_JSONL', DEFAULT_PATH)
```

### `~/.claude/settings.json` への追加

現在 `hooks` セクションには `Notification` と `Stop` がある。以下を追加：

```json
"PostToolUse": [
  {
    "matcher": "Skill",
    "hooks": [{"type": "command", "command": "python3 /Users/kkoichi/Developer/personal/claude-transcript-analyzer/hooks/record_skill.py"}]
  },
  {
    "matcher": "Task",
    "hooks": [{"type": "command", "command": "python3 /Users/kkoichi/Developer/personal/claude-transcript-analyzer/hooks/record_subagent.py"}]
  }
],
"UserPromptSubmit": [
  {
    "matcher": "",
    "hooks": [{"type": "command", "command": "python3 /Users/kkoichi/Developer/personal/claude-transcript-analyzer/hooks/record_skill.py"}]
  }
]
```

## TDD 実装順序

1. `tests/test_record_skill.py` を書く
   - PostToolUse(Skill) → `skill_tool` イベントが追記される
   - UserPromptSubmit + カスタムコマンド → `user_slash_command` イベントが追記される
   - UserPromptSubmit + `/clear` → 何も追記されない
   - `<command-name>` タグなし → 何も追記されない
   - 不正 JSON → 例外なく exit(0)
   - `data/` ディレクトリ未存在時に自動作成される

2. `hooks/record_skill.py` を実装してテストをパス

3. `tests/test_record_subagent.py` を書く
   - PostToolUse(Task) → `subagent_start` イベントが追記される
   - `tool_name` が `Task` 以外 → 何も追記されない
   - 不正 JSON → 例外なく exit(0)

4. `hooks/record_subagent.py` を実装してテストをパス

5. `tests/test_summary.py` を書く
   - JSONL ファイルの読み込みと集計
   - ファイル未存在 → 空リストを返す

6. `reports/summary.py` を実装してテストをパス

7. `~/.claude/settings.json` を更新（セッション再起動が必要）

## 検証方法

1. テスト実行: `python3 -m pytest tests/` が全パス
2. 統合テスト: Claude Code を再起動し、任意の Skill を実行 → `data/usage.jsonl` に行が追記されることを確認
3. `python3 reports/summary.py` で集計が表示されることを確認

## 他の人への導入手順

### 課題：絶対パス問題

`~/.claude/settings.json` の hook コマンドは絶対パスで書く必要があるが、
ユーザーごとにリポジトリのクローン先が異なる。
これを解決するために **`install.sh`** を用意する。

### 追加ファイル

```
claude-transcript-analyzer/
├── install.sh        # セットアップスクリプト
└── README.md         # 導入手順の説明
```

### `install.sh` の動作

```bash
#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing claude-transcript-analyzer hooks from: $REPO_DIR"

# Python で settings.json を安全にマージ
python3 "$REPO_DIR/install/merge_settings.py" "$REPO_DIR"

echo "Done. Please restart Claude Code to activate the hooks."
```

### `install/merge_settings.py` の動作

- `~/.claude/settings.json` を読み込む（存在しない場合は `{}` から開始）
- `hooks.PostToolUse`, `hooks.UserPromptSubmit` のエントリを追加/マージ
- コマンドパスは `REPO_DIR` を使い動的に生成（ハードコードなし）
- 書き戻す前にバックアップ（`settings.json.bak`）を作成
- 外部ライブラリ不使用（stdlib `json` のみ）

### 導入手順（README.md に記載）

```
# インストール

前提条件: macOS / Linux、Python 3.8 以上、Claude Code インストール済み

git clone <repo-url> ~/claude-transcript-analyzer
cd ~/claude-transcript-analyzer
chmod +x install.sh
./install.sh
# → Claude Code を再起動する

# 動作確認
python3 -m pytest tests/
# 任意の Skill を実行してから:
python3 reports/summary.py
```

### TDD 追加スコープ

- `tests/test_merge_settings.py` — merge_settings.py のテスト
  - 空の settings.json に hooks エントリが追加される
  - 既存の hooks エントリが破壊されない
  - バックアップファイルが作成される

## 注意点

- `UserPromptSubmit` はすべてのメッセージで発火するので、スクリプトは素早く終了する必要あり
- ユーザーが `/skill-name` を打つと `user_slash_command` と `skill_tool` の2イベントが記録される（意図的：発火元が違う有用な情報）
- 外部ライブラリ不使用（stdlib のみ）
- `install.sh` は何度実行しても安全（べき等）にする
