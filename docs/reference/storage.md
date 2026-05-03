# ストレージ設計 — JSONL primary + dedup discipline + archive immutability

このプロジェクトが採用する **append-only JSONL を一次データとし、SQLite/Parquet 等は派生ビュー** とするストレージ方針の根拠と、**schema evolution に耐える dedup 規律**、**archive 不変性ポリシー** をまとめたリファレンス。

---

## §1. なぜ JSONL primary か（POSIX `O_APPEND` semantics）

Event log が **多数の short-lived プロセス（hook = フォークごとに 1 イベント）から書かれ、少数の long-lived プロセス（dashboard / reporter）から読まれる** というワークロードでは、POSIX `O_APPEND` JSONL のほうが SQLite より write-side で構造的に優位。

- POSIX `O_APPEND` は `PIPE_BUF`（Linux/macOS で 4096B）以下の write を **アトミック追記** として保証する。1 行が < 4KB の JSON line ならロック不要・retry 不要・コネクション共有不要で `open(path, "a")` するだけで競合しない。
- SQLite で同等のことをするには WAL + lock-retry が必須。`sqlite3` は stdlib 入りなので依存コストではなく **意味論コスト**（ロックを要するか否か）の選択。
- Reader 側のクエリ性能は SQLite が有利だが、本プロジェクトの規模（sub-GB）では JSONL.gz iteration で十分。SQLite はあくまで **再構築可能な派生ビュー** として後付け可能。

### 結論

- **書き込み側**: 多 process writer + 少 reader + < GB → 生 JSONL。`open(path, "a", encoding="utf-8", newline="\n")` で append。1 行 < 4KB を維持。
- **コールド層**: 時間バケットの `archive/YYYY-MM.jsonl.gz` を採用。SQLite ミラー化を将来やる場合も、生 JSONL を canonical に保つ。
- **将来の SQLite/DuckDB/Parquet 移行**: `for line in file: insert(parse(line))` で派生ビュー再構築。SQLite を一次にすると `SELECT * + reformat` ループに固定される。

「Archive だけ SQLite、hot は JSONL」は両軸を失う罠（reader code 二重化 / index は cold で効かない / human grep が cold で死ぬ）。**全 JSONL** か **JSONL primary + SQLite mirror** の二択。

---

## §2. Dedup 規律と archive 不変性 — schema evolution との整合性

「(a) schema は additive に進化、reader は `dict.get(key)` で吸収」と「(b) archive merge dedup は line-level string equality（`json.dumps` 出力が安定）」を **同じプランの中に同居させると数年単位で内部矛盾が顕在化する**。

### メカニズム

Archive の保持期間（5–10 年）の間に hook 側のコードは進化する：新フィールド追加、value domain 拡大、Python の dict 順序のデフォルト変更、`ensure_ascii` フラグの揺れ、library 更新、separator 違い。

新しく届いた event が archive にある event と **論理的同一性を持つ**（rescan / 手動再実行 / 再 archive シナリオ）にもかかわらず、`json.dumps(event)` の **byte 表現は異なりうる**。Line-level dedup ではこれを「別 event」として通過させてしまい、

- (1) archive に同一 event を二重計上する、または
- (2) 同一 event の複数表現で archive が断片化する

のいずれかが発生。

### 解決策

**Structural fingerprint** で dedup する。

```python
# Default fingerprint key (event-type 共通)
fingerprint = (event_type, session_id, timestamp, secondary_key)

# Secondary key の選び方
#   skill_tool / subagent_start → tool_use_id
#   notification → notification_type
#   instructions_loaded → file_path
#   ... event-type ごとに自然な二次キー

# Ultimate fallback（fingerprint key がない event 用）
fallback = hashlib.sha1(json.dumps(event, sort_keys=True).encode("utf-8")).hexdigest()
```

最適化として「**fast path: line equality, slow path: structural fingerprint**」を取れる。大半の event は string compare で O(1) 一致するが、schema が動いた瞬間の event だけ fingerprint 計算に落ちる。

### Archive 不変性ポリシー

Fingerprint collision が **既存 archive と新 event** の間で発生したら **既存を残す**（新スキーマ表現で書き換えない）。

これにより：

- Archive の bytes は最初の書き込み後に変わらない → `mtime` が「最初に archive 化された時刻」として意味を持つ
- Reproducible build / `gunzip | diff` がデバッグツールとして機能
- 古い reader コードが古い archive と互換性を維持

### プラン審査時のチェック

**プランに「schema は additive に evolve」ルールが入ったら、即座に同プラン内の dedup / equality / cache-key / hash ロジックを全スキャンし、structural になっているか確認**。author は別の mental mode で書いているので両立矛盾を見逃しがち。`plan-reviewer` 系 subagent は cross-section の自己矛盾検出に強い。

---

## §3. Multi-aggregator naive-timestamp policy convergence

`usage.jsonl` は **複数の aggregator が同じ stream を読む** 設計
(`subagent_metrics.py` / `dashboard/server.py:aggregate_hourly_heatmap` /
将来の `reports/summary.py`...)。**naive datetime (tz 無し) の扱いを
全 aggregator で揃えないと、同じ入力に対して divergent 出力** を出す。

### 観測されたミスマッチ

- `subagent_metrics._week_start_iso`: naive を **UTC として扱う** (`replace(tzinfo=UTC)`)
- `dashboard.server.aggregate_hourly_heatmap`: naive を **silent skip** (「hooks は必ず +HH:MM 付き」前提)

通常運用 (live hook path) では hook が tz-aware ISO を必ず書くので問題は
起きない。しかし `scripts/rescan_transcripts.py --append` が古い transcript
の naive timestamp を再注入すると、**heatmap が落とした event が failure
trend には残る** という divergence が発生 (Codex Round 2 P2)。

### 規律

- **「hooks は常に tz-aware を書く」は live path の不変条件であって、
  re-injection path では成立しない**。`replace(tzinfo=...)` を grep する /
  `--append` 系 path を grep するだけで naive が混ざる経路が見える
- **silent skip は中立 default ではない** — 明示的な policy 選択。stream を
  共有する全 aggregator で同じ choice を採る
- **新 aggregator の naive 扱いブロックは `_week_start_iso` から
  そのまま copy** する。reimplement しない、silent-skip しない

### Convention

`usage.jsonl` を consume する新 aggregator を書く時:

1. 既存 consumer の naive 扱い policy を audit して 1 個に converge
2. aggregator docstring に policy + back-pointer (例:
   "naive datetime は UTC として扱う (`subagent_metrics._week_start_iso`
   と統一)")
3. fixture test に「naive な event」「+09:00 な event」を両方入れて、
   per-aggregator で同じ bucket に入ることを assert

### 関連 reference

- naive 扱いの実装: `subagent_metrics.py:_week_start_iso()`
- naive を含む rescan path: `scripts/rescan_transcripts.py --append`
- hook 側で必ず tz-aware を書く live path: `hooks/_now_iso()` 経由

---

## §4. 関連コード

| 概念 | 実装場所 |
|---|---|
| `usage.jsonl` 一次書込 | `hooks/record_*.py` 各種（lock 付き append は `hooks/_append.py` / `hooks/_lock.py` 経由） |
| Hot tier ファイルパス解決 | `~/.claude/transcript-analyzer/usage.jsonl`（実体）/ `USAGE_JSONL` env で override |
| Archive merge / dedup | **実装済** (Issue #30, v0.6.0〜) — `scripts/archive_usage.py:_structural_fingerprint()` で tier 1/2/3 fingerprint dispatch。詳細仕様は `docs/transcript-format.md` の "Archive 互換性のための schema 進化規約" を参照 |
| Archive cold tier ファイルパス | `~/.claude/transcript-analyzer/archive/YYYY-MM.jsonl.gz` / `ARCHIVE_DIR` env で override |
| schema バージョニング | `event_type` ごとに optional フィールド追加（破壊的変更は新 `event_type` を作る） |

