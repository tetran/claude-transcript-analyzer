# Pythonファイルレビュー（tests除く）

実施日: 2026-02-28
対象: `hooks/`, `reports/`, `dashboard/`, `install/`, `scripts/` 配下の `.py`

## Findings

### 1. High: ダッシュボードに永続XSSのリスク
- ファイル: `dashboard/server.py:176`
- 問題: `item[nameKey]` をエスケープせずに `innerHTML` へ埋め込んでいる。
- 影響: `skill` / `subagent_type` / `project` に悪意ある文字列が入ると、ブラウザ上でスクリプト実行され得る。

### 2. High: 不正JSON 1行で API/レポート全体が失敗
- ファイル: `dashboard/server.py:29`, `reports/summary.py:19`
- 問題: `json.loads(line)` に例外処理がない。
- 影響: `usage.jsonl` の1行破損でダッシュボードAPIが失敗、またはレポートスクリプトが異常終了する。

### 3. Medium: settings マージ時に既存フックを意図せず上書きする可能性
- ファイル: `install/merge_settings.py:54`
- 問題: 重複判定が `matcher` のみ。
- 影響: 同一 `matcher` で別用途の既存設定が置換される可能性がある。

### 4. Medium: transcript再走査のメモリ効率
- ファイル: `scripts/rescan_transcripts.py:107`
- 問題: `read_text().splitlines()` で全読み込みしている。
- 影響: 大きな `.jsonl` でメモリ使用量が増える。

## Open Questions

1. ダッシュボードはローカル専用前提でXSSリスクを許容するか。
2. `merge_settings.py` は既存 `Skill` / `Task` hook を置換する仕様で問題ないか。

## 備考

- 本レビューはコードリーディングベース。
- 実行テストは未実施。

