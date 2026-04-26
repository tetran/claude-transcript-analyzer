# claude-transcript-analyzer

## プロジェクトの目的

Claude Code のトランスクリプト（`.jsonl`）を解析し、**Skills と Subagents の使用状況を自動収集・集計・可視化する**ツール。

Claude Code Hooks を使ってリアルタイムにイベントを収集し、`data/usage.jsonl` に蓄積する。
それをブラウザダッシュボードで見やすく表示する。

## データフロー

```
Claude Code の動作
  │
  │  ── ツール／スキル系 ─────────────────────────────
  │  PostToolUse(Skill)               →  hooks/record_skill.py
  │  PostToolUseFailure(Skill)        →  hooks/record_skill.py
  │  UserPromptSubmit                 →  hooks/record_skill.py
  │  UserPromptExpansion              →  hooks/record_skill.py   (slash command 観測の主経路)
  │  PostToolUse(Task|Agent)          →  hooks/record_subagent.py
  │  PostToolUseFailure(Task|Agent)   →  hooks/record_subagent.py
  │  SubagentStart / SubagentStop     →  hooks/record_subagent.py
  │
  │  ── セッション／コンテキスト系 ──────────────────
  │  SessionStart / SessionEnd        →  hooks/record_session.py
  │  PreCompact / PostCompact         →  hooks/record_session.py
  │  Notification                     →  hooks/record_session.py
  │  InstructionsLoaded               →  hooks/record_session.py
  │
  │  ── 整合性チェック ─────────────────────────────
  │  Stop                             →  hooks/verify_session.py  (transcript ↔ usage 照合)
  ↓
data/usage.jsonl          ← append-only イベントログ（単一ファイルに集約）
  │
  ├── reports/summary.py     →  ターミナル集計レポート
  ├── reports/export_html.py →  スタンドアロン HTML レポート
  └── dashboard/server.py    →  ブラウザダッシュボード（http://localhost:8080）
```

実体保存先は `~/.claude/transcript-analyzer/usage.jsonl`（プラグイン更新で消えない位置）。
テスト用途では `USAGE_JSONL` / `HEALTH_ALERTS_JSONL` 環境変数で差し替えできる。

## ファイル構成

```
claude-transcript-analyzer/
├── .claude-plugin/
│   ├── plugin.json           # プラグインメタデータ
│   └── marketplace.json      # marketplace 用メタデータ
├── hooks/
│   ├── hooks.json            # プラグイン用フック定義（${CLAUDE_PLUGIN_ROOT} 参照）
│   ├── record_skill.py       # PostToolUse(Skill) / PostToolUseFailure(Skill)
│   │                         # UserPromptSubmit / UserPromptExpansion 処理
│   ├── record_subagent.py    # PostToolUse(Task|Agent) / PostToolUseFailure(Task|Agent)
│   │                         # SubagentStart / SubagentStop 処理
│   ├── record_session.py     # SessionStart/End, PreCompact/PostCompact,
│   │                         # Notification, InstructionsLoaded 処理
│   └── verify_session.py     # Stop hook: transcript vs usage 照合・異常検知
├── commands/                 # スラッシュコマンド定義
│   ├── usage-dashboard.md
│   ├── usage-export-html.md
│   └── usage-summary.md
├── dashboard/
│   └── server.py             # ローカル HTTP ダッシュボードサーバー
├── reports/
│   ├── summary.py            # ターミナル集計レポート
│   └── export_html.py        # 静的 HTML レポート生成
├── scripts/
│   └── rescan_transcripts.py # 過去トランスクリプトの遡及スキャン
├── install/
│   └── merge_settings.py     # settings.json マージ（べき等）
├── data/
│   └── usage.jsonl           # append-only イベントログ（テスト時のみ。
│                             # プラグイン稼働時は ~/.claude/transcript-analyzer/）
├── tests/
├── install.sh                # 後方互換のセットアップスクリプト
├── docs/
│   ├── transcript-format.md  # トランスクリプト形式 + Hook 入力 JSON スキーマ
│   ├── specs/                # 仕様
│   ├── plans/                # 実装計画
│   └── review/               # レビューメモ
```

## data/usage.jsonl のイベント形式

複数種のイベントが1ファイルに混在する（JSONL 形式）。下記は代表例。
PostToolUse 系には `duration_ms` / `permission_mode` / `tool_use_id` / `success`、
PostToolUseFailure 系には `success: false` と `error` / `is_interrupt` が付加される。

```jsonc
// Skill ツール呼び出し（PostToolUse(Skill)）
{"event_type": "skill_tool", "skill": "user-story-creation", "args": "6",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:00:00+00:00",
 "duration_ms": 1234, "permission_mode": "acceptEdits", "tool_use_id": "toolu_...", "success": true}

// Skill ツール失敗（PostToolUseFailure(Skill)）
{"event_type": "skill_tool", "skill": "user-story-creation", "args": "6",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:00:01+00:00",
 "success": false, "error": "...", "is_interrupt": false}

// ユーザーの slash コマンド（UserPromptExpansion / UserPromptSubmit）
{"event_type": "user_slash_command", "skill": "/insights", "args": "",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:05:00+00:00"}

// Subagent 起動（PostToolUse(Task|Agent) / SubagentStart いずれか先着）
{"event_type": "subagent_start", "subagent_type": "Explore",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:06:00+00:00"}

// Subagent 終了（SubagentStop）
{"event_type": "subagent_stop", "subagent_type": "Explore", "subagent_id": "agent_...",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:07:30+00:00",
 "duration_ms": 90000, "success": true}

// セッション開始 / 終了
{"event_type": "session_start", "source": "startup", "model": "claude-opus-4-7",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:00:00+00:00"}
{"event_type": "session_end", "reason": "logout",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T11:00:00+00:00"}

// コンテキスト圧縮
{"event_type": "compact_start", "trigger": "auto",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:30:00+00:00"}
{"event_type": "compact_end", "trigger": "auto",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:30:05+00:00"}

// Notification（idle, 確認待ち等）
{"event_type": "notification", "notification_type": "idle",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:45:00+00:00"}

// InstructionsLoaded（CLAUDE.md / memory / skill 等のロード）
{"event_type": "instructions_loaded", "file_path": "/path/to/CLAUDE.md",
 "memory_type": "project", "load_reason": "session_start",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:00:01+00:00"}
```

> **設計メモ — Subagent イベントの二重発火**
> Subagent の起動は `PostToolUse(Task|Agent)` と `SubagentStart` の両方で記録される（`event_type: subagent_start`）。
> どちらか一方だけが届かないケースに耐えるための冗長化で、ダウンストリーム側（`dashboard/server.py` / `reports/summary.py`）では今のところ重複排除は行っていない。
> 二重カウントを避けたい場合は `tool_use_id` を持つ方（PostToolUse 経路）を採用するなどの整理が必要。

## 開発規約

- **TDD** で実装する（テストを先に書く）
- **外部ライブラリ不使用**（stdlib のみ）
- テスト隔離: `USAGE_JSONL` で `DATA_FILE`、`HEALTH_ALERTS_JSONL` で `verify_session.py` の `ALERTS_FILE` をオーバーライド
- 組み込みコマンドは記録しない: `/exit /clear /help /compact /mcp /config /model /resume /context /skills /hooks /fast`

## よく使うコマンド

```bash
# テスト実行
python3 -m pytest tests/
```

## トランスクリプトのソースファイル

処理元となる Claude Code のトランスクリプトは `~/.claude/projects/` 以下にある。
詳細は `docs/transcript-format.md` を参照。
