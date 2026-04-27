# claude-transcript-analyzer

Claude Code の Skills と Subagents の使用状況を自動収集・集計するツール。

## 仕組み

Claude Code の Hooks 機能を使い、以下のイベントをリアルタイムに `~/.claude/transcript-analyzer/usage.jsonl` へ記録する：

| イベント | 収集タイミング |
|---------|--------------|
| `skill_tool` | アシスタントが `Skill` ツールを呼び出したとき |
| `user_slash_command` | ユーザーが `/skill-name` を直接入力したとき |
| `subagent_start` | アシスタントが `Task` ツール（Subagent）を呼び出したとき |

## インストール

**前提条件:** macOS / Linux / Windows、Python 3.8 以上、Claude Code インストール済み

Claude Code のチャット内で以下を実行する：

```
/plugin marketplace add https://github.com/tetran/claude-transcript-analyzer
/plugin install claude-transcript-analyzer@kkoichi-cc-plugin
```

その後、Claude Code を再起動する。

### Python ランチャの前提

プラグインの hook と slash command は `python` で Python 3.x を起動する規約に統一されている (Issue #24)。Claude Code hook 仕様で env-var 展開や OS 別エントリ分岐がサポートされていないため、`python` 単一名で各 OS をカバーする方針。

| OS | 対処 |
|----|------|
| Windows | 公式インストーラ / Microsoft Store から Python 3.x をインストールすると `python.exe` が PATH に通る。Python Launcher (`py -3`) を使っている場合は `python` の alias を作る必要がある |
| macOS (Homebrew) | `brew install python@3.11` で `python` シンボリックリンクが作られる。古い環境では `alias python=python3` を `~/.zshrc` 等に追記 |
| Linux | ディストロによっては `python` が無いため `sudo apt install python-is-python3` (Debian/Ubuntu) や同等措置 |

### アンインストール

```
/plugin uninstall claude-transcript-analyzer
```

macOS / Linux / Windows 共通で同じ手順。

## 使い方

インストール後は Claude Code のチャット内からスラッシュコマンドで使う。
コマンド名には `/claude-transcript-analyzer:` のプレフィックスが付く。

### `/claude-transcript-analyzer:usage-summary` — ターミナル集計レポート

```
/claude-transcript-analyzer:usage-summary
```

`~/.claude/transcript-analyzer/usage.jsonl` に記録された全イベントを集計し、Skills・Subagents の使用回数をターミナルに表示する。

出力例：

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

### `/claude-transcript-analyzer:usage-dashboard` — ブラウザダッシュボード

```
/claude-transcript-analyzer:usage-dashboard
```

ローカルサーバーを起動して `http://localhost:8080` でインタラクティブなダッシュボードを表示する。
ポートを変更したい場合は `DASHBOARD_PORT` 環境変数を使う。

### `/claude-transcript-analyzer:usage-export-html` — スタンドアロン HTML レポート

```
/claude-transcript-analyzer:usage-export-html
```

サーバー不要のスタンドアロン HTML ファイルを `~/.claude/transcript-analyzer/report.html` に生成する。
ブラウザで直接開けるほか、オフラインで共有・アーカイブするのにも使える。
出力先を変更したい場合は `--output` オプションを指定する。

## データの保存場所

イベントログは `~/.claude/transcript-analyzer/` に保存される：

```
~/.claude/transcript-analyzer/
├── usage.jsonl          # イベントログ（自動生成）
└── health_alerts.jsonl  # 異常検知アラートログ（自動生成）
```

Windows では `%USERPROFILE%\.claude\transcript-analyzer\` (Claude Code 本体の `HOME` 解決と同じ規約)。

---

## その他のインストール方法

### プラグインとして手動インストール

```bash
git clone https://github.com/tetran/claude-transcript-analyzer ~/.claude/plugins/claude-transcript-analyzer
# → Claude Code を再起動する
```

Claude Code が `~/.claude/plugins/` 以下のプラグインを自動認識して hooks を登録する。

### install.sh を使う（レガシー）

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

アンインストールするときは `~/.claude/settings.json` から以下のエントリを削除して Claude Code を再起動する：

- `hooks.PostToolUse` の `"Skill"` と `"Task"` matcher エントリ
- `hooks.UserPromptSubmit` のエントリ

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

