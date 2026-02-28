# claude-transcript-analyzer

## プロジェクトの目的

Claude Code のトランスクリプト（`.jsonl`）を解析し、**Skills と Subagents の使用状況を自動収集・集計・可視化する**ツール。

Claude Code Hooks を使ってリアルタイムにイベントを収集し、`data/usage.jsonl` に蓄積する。
それをブラウザダッシュボードで見やすく表示する。

## データフロー

```
Claude Code の動作
  │  PostToolUse(Skill)  →  hooks/record_skill.py
  │  UserPromptSubmit    →  hooks/record_skill.py
  │  PostToolUse(Task)   →  hooks/record_subagent.py
  ↓
data/usage.jsonl          ← append-only イベントログ（単一ファイルに集約）
  │
  ├── reports/summary.py  →  ターミナル集計レポート
  └── dashboard/server.py →  ブラウザダッシュボード（http://localhost:8080）
```

## ファイル構成

```
claude-transcript-analyzer/
├── hooks/
│   ├── record_skill.py       # PostToolUse(Skill) + UserPromptSubmit 処理
│   └── record_subagent.py    # PostToolUse(Task) 処理
├── dashboard/
│   └── server.py             # ローカル HTTP ダッシュボードサーバー
├── data/
│   └── usage.jsonl           # append-only イベントログ（自動生成）
├── install/
│   └── merge_settings.py     # settings.json マージスクリプト（べき等）
├── reports/
│   └── summary.py            # 集計レポート表示
├── tests/
├── install.sh                # セットアップスクリプト
├── docs/
│   ├── transcript-format.md  # トランスクリプトファイルの場所と構造
│   └── specs/                # 仕様
```

## data/usage.jsonl のイベント形式

3種類のイベントが1ファイルに混在する（JSONL 形式）。

```jsonc
// Skill ツール呼び出し
{"event_type": "skill_tool", "skill": "user-story-creation", "args": "6", "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:00:00+00:00"}

// ユーザーの slash コマンド
{"event_type": "user_slash_command", "skill": "/insights", "args": "", "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:05:00+00:00"}

// Subagent 起動
{"event_type": "subagent_start", "subagent_type": "Explore", "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:06:00+00:00"}
```

## 開発規約

- **TDD** で実装する（テストを先に書く）
- **外部ライブラリ不使用**（stdlib のみ）
- テスト隔離: `USAGE_JSONL` 環境変数で `DATA_FILE` をオーバーライドする
- 組み込みコマンドは記録しない: `/exit /clear /help /compact /mcp /config /model /resume /context /skills /hooks /fast`

## よく使うコマンド

```bash
# テスト実行
python3 -m pytest tests/

# 集計レポート
python3 reports/summary.py

# ダッシュボード起動
python3 dashboard/server.py
# → http://localhost:8080 をブラウザで開く

# インストール（初回 or 更新時）
./install.sh
# → Claude Code を再起動する
```

## トランスクリプトのソースファイル

処理元となる Claude Code のトランスクリプトは `~/.claude/projects/` 以下にある。
詳細は `docs/transcript-format.md` を参照。
