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
  │
  │  ── Archive 自動起動 (Issue #30) ──────────────────
  │  SessionStart                     →  hooks/launch_archive.py    (べき等 launcher)
  ↓
data/usage.jsonl          ← append-only イベントログ (hot tier / 直近 180 日)
  │  ├ hooks/_append.py          ← lock 付き append (blocking SH / cross-platform)
  │  └ scripts/archive_usage.py  ← 180 日超を月次 .jsonl.gz に gzip 圧縮で移動
  │       ↓
  │   archive/YYYY-MM.jsonl.gz   ← cold tier / immutable / opt-in で reader が読む
  │
  ├── reports/summary.py     →  ターミナル集計レポート (--include-archive で archive 込み)
  ├── reports/export_html.py →  スタンドアロン HTML レポート (--include-archive 同上)
  └── dashboard/server.py    →  ブラウザダッシュボード (hot tier のみ・仕様で 180 日固定)
```

実体保存先は `~/.claude/transcript-analyzer/usage.jsonl`（プラグイン更新で消えない位置）。
テスト用途では `USAGE_JSONL` / `HEALTH_ALERTS_JSONL` / `ARCHIVE_DIR` /
`ARCHIVE_STATE_FILE` / `USAGE_JSONL_LOCK` 環境変数で差し替えできる。

## 主要 spec / reference へのポインタ

詳細はトピック別に分割。CLAUDE.md からは概要 + 該当 doc へのポインタのみを置く。

仕分け基準: 「違反すると **バグ**」= spec (`docs/spec/`) / 「知らないと
**踏み抜く**」= reference (`docs/reference/`)。各ディレクトリの README に
詳細な振り分けポリシー。

### `docs/spec/` — 現行仕様 (contract)

| トピック | ファイル |
|---|---|
| 生 transcript フォーマット + Hook 入力 JSON schema + Archive schema 進化規約 | `docs/transcript-format.md` |
| `usage.jsonl` のイベント形式（収集後の event log schema） | `docs/spec/usage-jsonl-events.md` |
| `/api/data` レスポンス schema（dashboard backend → frontend） | `docs/spec/dashboard-api.md` |
| ライブダッシュボード運用仕様（起動条件 / URL 通知 / idle 停止 / 複数ページ router） | `docs/spec/dashboard-runtime.md` |
| Retention + 月次アーカイブ運用仕様（環境変数 / 手動コマンド / 並列耐性） | `docs/spec/archive-runtime.md` |
| GitHub Issue authoring 規約（Heavy / Light variant） | `docs/spec/issue-authoring.md` |

### `docs/reference/` — 設計判断・gotcha・パターン

| トピック | ファイル |
|---|---|
| ストレージ設計（JSONL primary / archive 不変性 / dedup 規律） | `docs/reference/storage.md` |
| Cross-platform / Python launcher trilemma / Windows porting checklist | `docs/reference/cross-platform.md` |
| Dashboard サーバー実装の非自明ポイント（SSE / JSON-in-`<script>` / component 分解） | `docs/reference/dashboard-server.md` |
| Subagent 二重観測の同定アルゴリズム + DRY 圧の教訓 | `docs/reference/subagent-invocation-pairing.md` |

## ファイル構成

```
claude-transcript-analyzer/
├── .claude-plugin/
│   ├── plugin.json           # プラグインメタデータ
│   └── marketplace.json      # marketplace 用メタデータ
├── hooks/
│   ├── hooks.json            # プラグイン用フック定義（${CLAUDE_PLUGIN_ROOT} 参照）
│   ├── _append.py            # lock 付き append + drop alert 記録 (Issue #30)
│   ├── _launcher_common.py   # OS 別 fork-and-detach の共通実装 (Issue #30)
│   ├── _lock.py              # POSIX/Windows lock 抽象 (Issue #44)
│   ├── record_skill.py       # PostToolUse(Skill) / PostToolUseFailure(Skill)
│   │                         # UserPromptSubmit / UserPromptExpansion 処理
│   ├── record_subagent.py    # PostToolUse(Task|Agent) / PostToolUseFailure(Task|Agent)
│   │                         # SubagentStart / SubagentStop 処理
│   ├── record_session.py     # SessionStart/End, PreCompact/PostCompact,
│   │                         # Notification, InstructionsLoaded 処理
│   ├── verify_session.py     # Stop hook: transcript vs usage 照合・異常検知
│   ├── launch_dashboard.py   # SessionStart / UserPromptExpansion / UserPromptSubmit /
│   │                         # PostToolUse: ダッシュボードを fork-and-detach でべき等起動
│   └── launch_archive.py     # SessionStart: archive job をべき等に fork-and-detach 起動 (Issue #30)
├── commands/                 # スラッシュコマンド定義
│   ├── restart-dashboard.md
│   ├── usage-archive.md
│   ├── usage-dashboard.md
│   ├── usage-export-html.md
│   └── usage-summary.md
├── dashboard/
│   └── server.py             # ローカル HTTP ダッシュボードサーバー
├── reports/
│   ├── _archive_loader.py    # archive/*.jsonl.gz を opt-in で読む共通 loader (Issue #30)
│   ├── summary.py            # ターミナル集計レポート (--include-archive 対応)
│   └── export_html.py        # 静的 HTML レポート生成 (--include-archive 対応)
├── subagent_metrics.py       # subagent 集計の共通ロジック (invocation 単位ペアリング)
├── scripts/
│   ├── archive_usage.py      # 180 日超を月次 .jsonl.gz にする archive job (Issue #30)
│   ├── build_surface_fixture.py  # Surface タブ視覚 fixture 生成
│   ├── restart_dashboard.py
│   └── rescan_transcripts.py # 過去トランスクリプトの遡及スキャン
├── data/
│   └── usage.jsonl           # append-only イベントログ（テスト時のみ。
│                             # プラグイン稼働時は ~/.claude/transcript-analyzer/）
├── tests/
└── docs/
    ├── transcript-format.md  # 生 transcript フォーマット + Hook 入力 schema + Archive 進化規約
    ├── spec/                 # 現行仕様 (contract) — README.md に振り分け基準
    │   │   # dashboard-api / dashboard-runtime / usage-jsonl-events /
    │   │   # archive-runtime / issue-authoring
    │   └── legacy/           # v0.1 時代の直接 parse 手順 (履歴アーカイブ)
    ├── reference/            # 設計判断・gotcha・パターン — README.md に振り分け基準
    │       # storage / cross-platform / dashboard-server / subagent-invocation-pairing
    ├── plans/
    │   └── archive/          # 完了済 plan (履歴アーカイブ)
    └── review/resolved/      # 解決済レビューメモ
```

> プラグイン稼働時の archive 出力先は `~/.claude/transcript-analyzer/archive/YYYY-MM.jsonl.gz`、
> state marker は `~/.claude/transcript-analyzer/.archive_state.json`。

## 開発規約

- **TDD** で実装する（テストを先に書く）
- **外部ライブラリ不使用**（stdlib のみ）
- テスト隔離: `USAGE_JSONL` で `DATA_FILE`、`HEALTH_ALERTS_JSONL` で `verify_session.py` の `ALERTS_FILE`、`ARCHIVE_DIR` / `ARCHIVE_STATE_FILE` / `USAGE_JSONL_LOCK` で archive 関連、`USAGE_RETENTION_DAYS` で retention をオーバーライド
- 組み込みコマンドは記録しない: `/exit /clear /help /compact /mcp /config /model /resume /context /skills /hooks /fast`

## よく使うコマンド

```bash
# テスト実行
python3 -m pytest tests/
```

## トランスクリプトのソースファイル

処理元となる Claude Code のトランスクリプトは `~/.claude/projects/` 以下にある。
詳細は `docs/transcript-format.md` を参照。
