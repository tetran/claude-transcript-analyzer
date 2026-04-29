# Legacy specs

v0.1 時代の **`.jsonl` を直接 grep / Python parse する手動手順** を保存するディレクトリ。
現アーキテクチャ (Hook 経由のリアルタイム収集) の前身として歴史的に残している。

| File | 内容 |
|---|---|
| `skill-usage-analysis.md` | Skill 使用状況をトランスクリプトから直接抽出する bash / Python レシピ |
| `subagent-usage-analysis.md` | Subagent (Task / Agent ツール) 使用状況を直接抽出するレシピ |

## 現アーキテクチャとの関係

`docs/transcript-format.md` 冒頭で明示されている通り、本プロジェクトは現在
**Claude Code Hooks** を使ってイベントをリアルタイム収集する方式を採用している:

```
PostToolUse(Skill) / PostToolUse(Task|Agent) / UserPromptExpansion
  → hooks/record_*.py → ~/.claude/transcript-analyzer/usage.jsonl
```

ここに置いた legacy spec は次の用途でのみ参照する:

- **トランスクリプトの構造を確認したい**（Hook を経由せず生 .jsonl を眺めたいデバッグ時）
- **Hook が来ない環境** で過去ログから集計を作りたい場合の代替アプローチ
- 設計判断の歴史を追う

## 既知の drift

- builtin command set に `/hooks` `/fast` が含まれていない（CLAUDE.md は更新済み）
- 末尾の「2026-02-28 時点の調査結果メモ」は古い数字
- 現行の dedup / 観測点の役割分担（`subagent_metrics.aggregate_subagent_metrics`）は反映していない

新規ロジックは `dashboard/server.py` / `subagent_metrics.py` / `reports/summary.py` を参照すること。
