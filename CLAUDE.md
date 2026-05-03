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

## Docs - spec / reference

詳細はトピック別に分割。

仕分け基準: 「違反すると **バグ**」= spec (`docs/spec/`) / 「知らないと**踏み抜く**」= reference (`docs/reference/`)。
各ディレクトリの README に詳細な振り分けポリシー。

## ファイル構成

```
claude-transcript-analyzer/
├── .claude-plugin/
│   ├── plugin.json           # プラグインメタデータ
│   └── marketplace.json      # marketplace 用メタデータ
├── hooks/                    # プラグイン用フック定義
├── commands/                 # スラッシュコマンド定義
├── dashboard/                # ローカル HTTP ダッシュボードサーバー
├── reports/                  # レポート生成スクリプト
├── subagent_metrics.py       # subagent 集計の共通ロジック (invocation 単位ペアリング)
├── scripts/                  # 手動/バッチ実行用のユーティリティ
├── data/                     # データ置き場（v0.7.3現在不使用）
├── tests/
└── docs/
    ├── transcript-format.md  # 生 transcript フォーマット + Hook 入力 schema + Archive 進化規約
    ├── spec/                 # 現行仕様 (contract) — README.md に振り分け基準
    │   └── legacy/           # v0.1 時代の直接 parse 手順 (履歴アーカイブ)
    ├── reference/            # 設計判断・gotcha・パターン — README.md に振り分け基準
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

## ブランチ運用

**release branch model** で運用する。

```
main
  └─ vX.Y.Z              ← release branch (リリース単位の集積点)
       ├─ feature/<issue-number>-<slug>  → PR → vX.Y.Z にマージ
       └─ ...             (リリース準備完了で vX.Y.Z → main の release PR)
```

- 新リリースサイクル開始時に `main` から `vX.Y.Z` を切って remote に push
- 個別 feature は `feature/<issue-number>-<slug>` で `vX.Y.Z` から派生 — feature PR の base は `vX.Y.Z` (main ではない)
- リリース準備完了で `vX.Y.Z` → `main` の release PR を立てる (詳細は `patch-release` skill)

## よく使うコマンド

```bash
# テスト実行
python3 -m pytest tests/
```

## トランスクリプトのソースファイル

処理元となる Claude Code のトランスクリプトは `~/.claude/projects/` 以下にある。
詳細は `docs/transcript-format.md` を参照。
