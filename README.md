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

**前提条件:** macOS / Linux / Windows、Python 3.8 以上 (`python3` または `python` のいずれかが PATH 上にあること)、Claude Code インストール済み

Claude Code のチャット内で以下を実行する：

```
/plugin marketplace add https://github.com/tetran/claude-transcript-analyzer
/plugin install claude-transcript-analyzer@kkoichi-cc-plugin
```

その後、Claude Code を再起動する。

### Python 解決方式

プラグインの hook と slash command は **bash の POSIX `command -v` fallback** で Python を解決する (Issue #33)：

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/hooks/foo.py
```

`python3` を優先し、無ければ `python` にフォールバックする。Claude Code hook の `command` フィールドは全 OS でデフォルト bash で実行されるため、この POSIX 構文が macOS / Linux / Windows のどれでも一律に動く。`$(...)` を double-quote で囲むのは、Windows で Python が `C:\Program Files\Python311\python.exe` のようにスペース入りパスにインストールされていても bash の word splitting で分割されないようにするため。

| OS | 動作 |
|----|------|
| Windows | 公式インストーラ / Microsoft Store / Python Launcher のいずれで入れても `python.exe` または `python3.exe` のどちらかが PATH 上に来れば動く（alias を作る必要なし）。Claude Code hook は内部的に bash を spawn するため Git for Windows / WSL のいずれかが必要 |
| macOS (Homebrew) | `brew install python@3.x` で `python3` が入る。`python` symlink の有無は問わない |
| Linux | `python3` 単独提供が主流の Ubuntu 22+ / Debian 系をそのまま使える。`python-is-python3` 等の追加措置は不要 |

二重保険として、`hooks/*.py` の各スクリプトには shebang `#!/usr/bin/env python3` と実行ビット (`chmod +x`) が付与されている。`$()` が空展開した場合 (PATH 上に `python3` も `python` も無い極端なケース) でも、`env: 'python3': No such file or directory` という Python 不在の本当のエラーメッセージが出る。

> **経緯**: Issue #24 (PR #31) で `python` 統一としていたが、macOS Homebrew / Ubuntu 22+ の標準環境 (`python3` のみ) で hook が起動失敗するため、Issue #33 で `command -v` fallback に切り替えた。

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

