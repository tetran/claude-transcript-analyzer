# claude-transcript-analyzer

Claude Code の Skills と Subagents の使用状況を自動収集・集計するツール。

## 仕組み

Claude Code の Hooks 機能を使い、以下のイベントをリアルタイムに `data/usage.jsonl` へ記録する：

| イベント | 収集タイミング |
|---------|--------------|
| `skill_tool` | アシスタントが `Skill` ツールを呼び出したとき |
| `user_slash_command` | ユーザーが `/skill-name` を直接入力したとき |
| `subagent_start` | アシスタントが `Task` ツール（Subagent）を呼び出したとき |

## ファイル構成

```
claude-transcript-analyzer/
├── hooks/
│   ├── record_skill.py       # PostToolUse(Skill) + UserPromptSubmit 処理
│   └── record_subagent.py    # PostToolUse(Task) 処理
├── data/
│   └── usage.jsonl           # append-only イベントログ（自動生成）
├── install/
│   └── merge_settings.py     # settings.json マージスクリプト
├── reports/
│   └── summary.py            # 集計レポート表示
├── tests/
│   ├── test_record_skill.py
│   ├── test_record_subagent.py
│   ├── test_summary.py
│   └── test_merge_settings.py
├── install.sh                # セットアップスクリプト
└── IMPLEMENTATION_PLAN.md    # 設計ドキュメント
```

## インストール

**前提条件:** macOS / Linux、Python 3.8 以上、Claude Code インストール済み

```bash
git clone <repo-url> ~/claude-transcript-analyzer
cd ~/claude-transcript-analyzer
chmod +x install.sh
./install.sh
# → Claude Code を再起動する
```

`install.sh` は `~/.claude/settings.json` に hooks エントリを追加する。
既存の設定は保持され、実行前にバックアップ（`settings.json.bak`）が作成される。
何度実行しても安全（べき等）。

## 動作確認

```bash
# テスト実行
python3 -m pytest tests/

# Claude Code で任意の Skill を実行してから集計を確認
python3 reports/summary.py
```

## 集計レポートの例

```
Total events: 42

=== Skills (skill_tool + user_slash_command) ===
  15  user-story-creation
   8  /insights
   6  ready-for-issue
   3  simplify

=== Subagents ===
  12  Explore
   7  Plan
   3  general-purpose
```

## 手動アンインストール

`~/.claude/settings.json` から以下のエントリを削除し、Claude Code を再起動する：

- `hooks.PostToolUse` の `"Skill"` と `"Task"` matcher エントリ
- `hooks.UserPromptSubmit` のエントリ
