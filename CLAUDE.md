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
  │
  │  ── ダッシュボード自動起動 ───────────────────────
  │  SessionStart                     →  hooks/launch_dashboard.py  (べき等 launcher)
  │  UserPromptExpansion              →  hooks/launch_dashboard.py
  │  UserPromptSubmit                 →  hooks/launch_dashboard.py
  │  PostToolUse                      →  hooks/launch_dashboard.py
  ↓
data/usage.jsonl          ← append-only イベントログ（単一ファイルに集約）
  │
  ├── reports/summary.py     →  ターミナル集計レポート
  ├── reports/export_html.py →  スタンドアロン HTML レポート
  └── dashboard/server.py    →  ブラウザダッシュボード（自動起動・空きポート bind）
```

実体保存先は `~/.claude/transcript-analyzer/usage.jsonl`（プラグイン更新で消えない位置）。
テスト用途では `USAGE_JSONL` / `HEALTH_ALERTS_JSONL` 環境変数で差し替えできる。

## ライブダッシュボードの運用仕様 (v0.3, Issue #14)

ダッシュボードは Claude Code セッションと一体化したライブビュー。手動コマンドは不要。

### 起動条件

`hooks/launch_dashboard.py` が以下の Hook で発火し、`server.json` を見て **べき等に**
起動判定する（既起動なら何もしない、未起動なら fork-and-detach で起動）：

| Hook | 役割 |
|------|------|
| `SessionStart` | Claude Code 起動の瞬間にダッシュボードも立ち上げる |
| `UserPromptExpansion` | slash command 経路の主観測点で発火 → 即時復活 |
| `UserPromptSubmit` | idle 後にユーザーが操作再開 → 自動復活（expansion fallback としても兼用） |
| `PostToolUse` | 道中の死活サイダーガード（任意のツール使用後にも復活窓を持つ） |

launcher は **常に < 100ms で exit 0**（Claude Code をブロックしない）。

### URL 確認方法

サーバー起動時に `~/.claude/transcript-analyzer/server.json` に `{pid, port, url, started_at}`
が atomic に書かれる。手動起動時は stderr にも `Dashboard available: http://localhost:<port>`
を 1 行出力する。

```bash
# URL を取得
cat ~/.claude/transcript-analyzer/server.json
```

### 停止条件

- **idle 自動停止**: 最後の HTTP リクエストから `DASHBOARD_IDLE_SECONDS`（デフォルト 600 秒 = 10 分）経過で graceful shutdown
  - SSE 接続中は idle カウンタが進まないため、ブラウザ開きっぱなしでは停止しない
- **手動停止**: `kill <pid>`（pid は server.json から取得）。SIGTERM / SIGINT で graceful shutdown
- 停止時に server.json は **compare-and-delete** で自動削除（多重インスタンス保護）

idle 停止後にユーザーが Claude Code 操作を再開すると、UserPromptExpansion /
UserPromptSubmit / PostToolUse hook が launch_dashboard を起動し直して **自動復活**
する（同 or 別ポート）。

### 環境変数

| 変数 | デフォルト | 意味 |
|------|-----------|------|
| `DASHBOARD_PORT` | `0`（OS 任せ） | 具体ポート指定時はそのポートで bind |
| `DASHBOARD_IDLE_SECONDS` | `600` | idle 停止の閾値秒。`0` で停止無効化 |
| `DASHBOARD_POLL_INTERVAL` | `1.0` | usage.jsonl 変更検知の polling 周期 (秒)。`0` で監視無効 |
| `DASHBOARD_SERVER_JSON` | `~/.claude/transcript-analyzer/server.json` | server.json のパス |

### 手動起動・停止

通常は hook 経由の自動起動で十分だが、手動でも起動可能：

```bash
# 手動起動 (launcher 経由でべき等 — 既起動なら何もしない)
python3 ${CLAUDE_PLUGIN_ROOT}/hooks/launch_dashboard.py
# /usage-dashboard スラッシュコマンドも同じ経路

# fg debug 用に直叩きする場合は事前に既起動確認 (二重起動防止)
cat ~/.claude/transcript-analyzer/server.json  # 既起動なら kill してから
python3 dashboard/server.py

# 手動停止
kill $(jq -r .pid ~/.claude/transcript-analyzer/server.json)
```

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
│   ├── verify_session.py     # Stop hook: transcript vs usage 照合・異常検知
│   └── launch_dashboard.py   # SessionStart / UserPromptExpansion / UserPromptSubmit /
│                             # PostToolUse: ダッシュボードを fork-and-detach でべき等起動
├── commands/                 # スラッシュコマンド定義
│   ├── usage-dashboard.md
│   ├── usage-export-html.md
│   └── usage-summary.md
├── dashboard/
│   └── server.py             # ローカル HTTP ダッシュボードサーバー
├── reports/
│   ├── summary.py            # ターミナル集計レポート
│   └── export_html.py        # 静的 HTML レポート生成
├── subagent_metrics.py       # subagent 集計の共通ロジック (invocation 単位ペアリング)
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
// source は "expansion" (主経路) / "submit" (フォールバック) のいずれか。
// dedup 判定は source!="submit" のレコードに対してのみ働き、submit 連打は両方記録される。
{"event_type": "user_slash_command", "skill": "/insights", "args": "", "source": "expansion",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:05:00+00:00"}

// Subagent 起動（PostToolUse(Task|Agent) のみが count に入る正規観測点）
{"event_type": "subagent_start", "subagent_type": "Explore",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:06:00+00:00",
 "duration_ms": 90000, "permission_mode": "default", "tool_use_id": "toolu_..."}

// Subagent ライフサイクル開始（SubagentStart 経由・補助観測。count に入らない）
{"event_type": "subagent_lifecycle_start", "subagent_type": "Explore",
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
// notification_type は `permission` / `permission_prompt` / `idle` / `idle_prompt` 等が観測される。
// 集計側 (dashboard / summary) は `permission` と `permission_prompt` を同一視してカウント。
{"event_type": "notification", "notification_type": "idle",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:45:00+00:00"}

// InstructionsLoaded（CLAUDE.md / memory / skill 等のロード）
{"event_type": "instructions_loaded", "file_path": "/path/to/CLAUDE.md",
 "memory_type": "project", "load_reason": "session_start",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:00:01+00:00"}
```

> **設計メモ — Subagent 観測点と invocation 単位集計**
> Subagent 起動は `PostToolUse(Task|Agent)` を **正規観測点** とし `event_type: subagent_start` で記録する。
> `SubagentStart` hook 由来は補助観測として `event_type: subagent_lifecycle_start` で記録。
> バケット `(session, type)` ごとに **timestamp 順マージ** で invocation を構築する：1 秒以内に発火した start と lifecycle は同一 invocation の重複扱い、それ以上離れていれば別 invocation。
> これにより両方発火・lifecycle のみ・start のみ・disjoint な flaky パターンすべてを取りこぼさず・二重計上もせず数える。
>
> 失敗・所要時間集計は `subagent_metrics.aggregate_subagent_metrics()` に統一。`(session_id, subagent_type)` でグルーピングし時系列順に start↔stop を **invocation 単位でペアリング** したうえで：
> - 各 invocation について `start.success=False OR stop.success=False` のとき 1 failure として計上（同 invocation の重複発火は構造的に 1 件に）
> - 起動失敗（start fail）で stop が来ないケースは「starts と stops の件数不一致」を見て stop プールを消費しないペアリングに切り替える
> - duration は **invocation ごと** に `stop.duration_ms` (end-to-end) を優先、無ければ `start.duration_ms` (起動オーバーヘッド) を fallback とする（type 単位 fallback はバイアスになるため不採用）
>
> tool_use_id ↔ agent_id の直接紐付け手段が無いため時系列近似だが、同 type の並行実行は実機では稀で v0.2 の精度要件を満たす。

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
