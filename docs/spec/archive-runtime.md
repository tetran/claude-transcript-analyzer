# Retention + 月次アーカイブの運用仕様 (Issue #30, v0.6.0〜)

`usage.jsonl` (hot tier) は **直近 180 日** だけを保持し、それを超えた event は `archive/YYYY-MM.jsonl.gz` (cold tier) に gzip 圧縮で月単位移動する。
これにより hot tier はサイズ上限のある定常状態に保たれ、reader (dashboard / summary / export_html) の parse コストが線形に伸びない。

詳細な schema 進化規約 (tier 1/2/3 fingerprint, secondary_key dispatch table) は `docs/transcript-format.md` の "Archive 互換性のための schema 進化規約" を参照。
ストレージ設計プリミティブ (JSONL primary / archive 不変性ポリシー) は `docs/reference/storage.md` を参照。

## 自動起動

`hooks/launch_archive.py` が `SessionStart` で発火し、`.archive_state.json` を読んで以下の **horizon-based gate** で起動判定する。launcher 自体は**常に < 100ms で exit 0**。

| 条件 | 動作 |
|---|---|
| `last_archived_month` が前月 (UTC) 以降 | カバー済 → skip |
| `last_archivable_horizon` ≥ 現在の archivable horizon | 同 horizon は worker が観測済 (no-op だった) → skip |
| その他 (state 不在 / 壊れた JSON / horizon 未記録 / horizon 古い) | `scripts/archive_usage.py` を fork-and-detach で spawn |

`horizon = previous_month(cutoff)` (`cutoff = now - retention_days`) を
launcher と worker が独立に同じ式で計算する。worker は no-op 完了でも
`last_archivable_horizon` を更新する (= 「ここまでは確認済」を残す)。

旧 `last_run_at == this_month` skip は廃止 (codex 6th review P2)。理由は次の 2 欠陥:

- **calendar-month skip**: retention boundary が月末を跨いだ mid-month で archive 対象が新たに発生しても、`last_run_at` が今月内なら launcher は skip。eligible event が hot tier に最大 30 日滞留する。
- **`last_archived_month`-only**: 新規インストールや全 event が retention 内で `last_archived_month` が永遠に未記録の場合、毎セッション spawn → worker no-op の無限ループ。

horizon ベースは「観測した最大 archivable 月」を state に書くため両欠陥を同時に回避する。

## 環境変数

| 変数 | デフォルト | 意味 |
|------|-----------|------|
| `USAGE_RETENTION_DAYS` | `180` | retention window (日)。1 など小さい値で動作確認可能 |
| `ARCHIVE_DIR` | `~/.claude/transcript-analyzer/archive` | archive 出力ディレクトリ |
| `ARCHIVE_STATE_FILE` | `~/.claude/transcript-analyzer/.archive_state.json` | state marker のパス |
| `USAGE_JSONL_LOCK` | `<USAGE_JSONL>.lock` | append/archive 排他用 lock file |

## 手動コマンド

```bash
# /usage-archive スラッシュコマンドと同じ経路 (べき等)
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_usage.py

# 動作確認用 (retention を 1 日に下げて即時 archive)
USAGE_RETENTION_DAYS=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/archive_usage.py

# 全期間集計が必要な分析
python3 ${CLAUDE_PLUGIN_ROOT}/reports/summary.py --include-archive
python3 ${CLAUDE_PLUGIN_ROOT}/reports/export_html.py --include-archive
```

## 並列耐性 / observability

- archive job: `EX` を取得して直列化、state marker 再 read で race-free 二重起動回避
- hook (`record_*.py`): **blocking SH** で archive の `EX` release を待ってから append (codex 5th review P1 で旧 `SH | NB × 5 retry × 100ms = 500ms upper-bound` の data loss 経路を撤廃)。
  - 取得失敗時は `health_alerts.jsonl` に `{"alert": "append_skipped_due_to_archive_lock", ...}` を 1 行記録して silent drop (実運用ではほぼ起きない / signal 起因等の異常系)
- lock 層 (`hooks/_lock.py` / Issue #44): POSIX (`fcntl.flock`) と Windows (`msvcrt.locking`) の差を吸収。Windows は SH 概念無しのため SH も EX 相当で動作 (concurrency 落ちるが correctness は保たれる)

## `rescan_transcripts.py` との運用注意

`rescan_transcripts.py --append` で 180 日超の event を再 append すると、次回 archive job 実行時にまた archive へ移される (idempotent / immutability で重複登録は起きない)。
**rescan 後は手動で `/usage-archive` を実行する** と hot tier がすぐ整理される (自動連動は意図的にしていない)。
