# claude-transcript-analyzer

> このリポジトリの正規 AI エージェント向けガイドは **[CLAUDE.md](CLAUDE.md)** です。
> AGENTS.md は CLAUDE.md と内容を重複させないために、全エージェントに適用される
> 最低限の規約のみを保持します。データフロー / event_type 一覧 / ファイル構成 /
> Hook 配置などの詳細仕様は CLAUDE.md を参照してください。

## プロジェクトの目的（要約）

Claude Code の Skills と Subagents の使用状況を Hooks で自動収集・可視化するツール。
イベントログは `~/.claude/transcript-analyzer/usage.jsonl`（テスト時のみ
プロジェクト内 `data/usage.jsonl`）に append-only で蓄積され、
ダッシュボード / ターミナルレポート / HTML エクスポートで参照する。

## 全 AI エージェント共通の規約

- **TDD** で実装する（テストを先に書く）
- **外部ライブラリ不使用**（stdlib のみ）
- テスト隔離:
  - `USAGE_JSONL` で `DATA_FILE` をオーバーライド
  - `HEALTH_ALERTS_JSONL` で `verify_session.py` の `ALERTS_FILE` をオーバーライド
- 組み込みコマンドは記録しない: `/exit /clear /help /compact /mcp /config /model /resume /context /skills /hooks /fast`

## よく使うコマンド

```bash
python3 -m pytest tests/
```

## さらに詳しく

| 知りたいこと | 参照先 |
|------------|-------|
| プロジェクト全体仕様（データフロー / 観測対象 Hook / event_type） | [`CLAUDE.md`](CLAUDE.md) |
| トランスクリプト形式 + Hook 入力 JSON スキーマ | [`docs/transcript-format.md`](docs/transcript-format.md) |
| 仕様書 / 実装計画 | `docs/specs/` / `docs/plans/` |
| レビューメモ | `docs/review/` |

処理元の Claude Code トランスクリプト（`.jsonl`）は `~/.claude/projects/` 以下に保存される。
