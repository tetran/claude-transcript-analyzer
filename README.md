# claude-transcript-analyzer

Claude Code の Skills と Subagents の使用状況を自動収集・集計するツール。

## 仕組み

Claude Code の Hooks 機能を使い、以下のイベントをリアルタイムに `~/.claude/transcript-analyzer/usage.jsonl` へ記録する：

| イベント | 収集タイミング |
|---------|--------------|
| `skill_tool` | アシスタントが `Skill` ツールを呼び出したとき |
| `user_slash_command` | ユーザーが `/skill-name` を直接入力したとき |
| `subagent_start` | アシスタントが `Task` ツール（Subagent）を呼び出したとき |

## ファイル構成

```
claude-transcript-analyzer/
├── hooks/                    # Claude Code Hook用スクリプト
├── data/                     # イベントログ（旧パス。現在は ~/.claude/transcript-analyzer/ を使用）
├── install/                  # インストール時の処理
├── reports/                  # 集計レポート表示
├── tests/
└── install.sh                # セットアップスクリプト
```

## インストール

**前提条件:** macOS / Linux、Python 3.8 以上、Claude Code インストール済み

### 方法 1: `/plugin` コマンドでインストール（推奨）

Claude Code のチャット内で以下を実行する：

```
/plugin marketplace add https://github.com/tetran/claude-transcript-analyzer
/plugin install claude-transcript-analyzer@kkoichi-cc-plugin
```

その後、Claude Code を再起動する。

### 方法 2: プラグインとして手動インストール

```bash
git clone https://github.com/tetran/claude-transcript-analyzer ~/.claude/plugins/claude-transcript-analyzer
# → Claude Code を再起動する
```

Claude Code が `~/.claude/plugins/` 以下のプラグインを自動認識して hooks を登録する。

### 方法 3: 従来の install.sh を使う

```bash
git clone https://github.com/tetran/claude-transcript-analyzer ~/claude-transcript-analyzer
cd ~/claude-transcript-analyzer
chmod +x install.sh
./install.sh
# → Claude Code を再起動する
```

`install.sh` は `~/.claude/settings.json` に hooks エントリを追加する。
既存の設定は保持され、実行前にバックアップ（`settings.json.bak`）が作成される。
何度実行しても安全（べき等）。

## データの保存場所

イベントログは `~/.claude/transcript-analyzer/` に保存される：

```
~/.claude/transcript-analyzer/
├── usage.jsonl          # イベントログ（自動生成）
└── health_alerts.jsonl  # 異常検知アラートログ（自動生成）
```

### 旧バージョンからのデータ移行

以前のバージョン（`data/usage.jsonl` に保存していた場合）はデータを移行できる：

```bash
mkdir -p ~/.claude/transcript-analyzer
mv data/usage.jsonl ~/.claude/transcript-analyzer/usage.jsonl
```

または環境変数で旧パスを指定したまま使い続けることもできる：

```bash
USAGE_JSONL=./data/usage.jsonl python3 dashboard/server.py
```

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

