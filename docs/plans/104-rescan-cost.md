# Issue #104 — rescan + reports 拡張: assistant_usage backfill + summary --include-cost

> Tracking issue: #104
> Master plan: `docs/plans/session-page-cost-estimation.md` (sub-issue C / Steps 11-14)
> Base branch: `v0.8.0`
> Feature branch: `feature/104-rescan-cost`
> PR target: `v0.8.0`

## 📋 plan-reviewer 反映ログ

| Proposal | 内容 | 反映箇所 |
|----------|------|----------|
| (初稿) | — | — |

### 二次レビュー反映 (round 2)

| Proposal | 内容 | 反映箇所 |
|----------|------|----------|
| P1 | 既存 doc drift (`prompt-persistence.md:82-83` が fingerprint dedup を約束しているが script に dedup 実装が無い) を R1 で two-population framing に書き直し、Step 9 で当該 doc 行も修正対象に追加 | §5 R1 / §3 Step 9 (prompt-persistence.md 行 82-83 書き換えを追加) |
| P2 | `skill_tool` / `subagent_start` / `user_slash_command` の repeat-rescan duplication semantics を test で pin (現状の意図的振る舞い: append mode は assistant_usage のみ dedup、その他 event は重複)。`Out of scope` にも明示 | §3 Step 6 (`test_rescan_twice_keeps_assistant_usage_count_stable_but_doubles_skill_tool_count` 追加) / §6 Out of scope (`skill_tool` 等への dedup 拡張) |
| P3 | `--append` flag の意味論シフトを test で pin (旧: 全 event blindly append、新: assistant_usage のみ dedup append、他 event は従来通り blindly append) | §3 Step 5 (test rename `test_main_append_flag_now_dedups_assistant_usage`、追加 assert を含む) / §4 TDD plan |
| P4 (advisory) | Option A vs live-hook valid_agent_ids divergence を impl 内 comment で残す | §3 Step 4 (impl 内 NOTE comment 文言を pin) |
| P5 (advisory) | summary disclaimer を module-level constant `_COST_DISCLAIMER` に抽出し test の脆さを軽減 | §3 Step 7 (impl note + test assertion を `_COST_DISCLAIMER in stdout` に揃える) |

### 三次レビュー反映 (round 3)

| Proposal | 内容 | 反映箇所 |
|----------|------|----------|
| R3-P1 (advisory) | `_scan_existing_state` factor 後の `_scan_valid_agent_ids(data_file, session_id) -> set[str]` の signature pin (= rescan は呼ばないが live hook は per-session 引数を残す) を Step 1 に明示 | §3 Step 1 (impl bullet 2 段目) |
| R3-Q3 (advisory) | `prompt-persistence.md:82-83` の書き換え target を line number ではなく content match (= 「`rescan_transcripts.py` は **idempotent**` で始まる段落」) で指定し、上流の line shift に頑健にする | §3 Step 9 |
| R3-Q2 (note) | `top_n=10**9` の magic number は `cost_metrics.aggregate_session_breakdown` が `top_n: int = TOP_N_SESSIONS` を取る (None 未対応) ため pragmatic choice。本 issue では sentinel 化せず magic number で受ける | §3 Step 7 (脚注として補足) |

## 0. Companion docs

| 既存 doc | 本 plan での扱い |
|---------|------------------|
| `docs/plans/session-page-cost-estimation.md` | master plan。Step 11 (rescan) / Step 12 (summary --include-cost) / Step 13 (export_html 確認) を **本 plan で詳細化**。user-decided spec (rescan default を `--append` に切替 / Top10 cost desc / agent_id 由来 mapping) を ack して上書き |
| `docs/transcript-format.md` | 「per-subagent transcript = `<encoded-cwd>/<session_id>/subagents/agent-<agent_id>.jsonl`」「Issue #93 filter rule (`subagent_type == ""` skip)」の verbatim ソース。BC 変更後の rescan 運用 note を Phase 6 で追記 |
| `docs/spec/usage-jsonl-events.md` | `assistant_usage` event schema の正本。rescan が emit する row も同じ schema に従うことを Phase 4 docs で 1 行追記 |
| `docs/spec/dashboard-api.md` | export_html 経由の `session_breakdown` 配信形を確認するための参照のみ (本 issue で touch しない) |
| `docs/reference/prompt-persistence.md` | `python3 scripts/rescan_transcripts.py --append` の既存運用例。**default 切替後は `--append` を書かない例に揃える** ことを Phase 6 で判定 |

## 1. Goal

Issue #99 で確立した `assistant_usage` event schema を **過去 transcript から backfill** できるようにし、terminal report (`reports/summary.py --include-cost`) と static HTML (`reports/export_html.py`) の両方で cost 数値を見えるようにする。同時に `scripts/rescan_transcripts.py` の default 動作を **append + dedup** に切替えて、live hook 経由でしか取得できない event (transcript 部分欠損ケース等) を破壊しない安全側 default にする。

AC tie-back:
- `scripts/rescan_transcripts.py` が main + per-subagent transcript の両方から `assistant_usage` を backfill し、`(session_id, message_id)` で idempotent。
- `record_assistant_usage.py` の抽出ロジックを **module-level 関数として export** して rescan から再利用 (DRY)。
- `reports/summary.py --include-cost` flag を追加し、Total estimated cost / Top 10 sessions by estimated cost / 参考値 disclaimer を terminal に出す。
- `reports/export_html.py` は既に `build_dashboard_data(events)` 経由で `session_breakdown` を埋め込むため **追加実装不要 / regression guard test のみ追加**。
- rescan default を `--append` に変更し、新 `--overwrite` flag で legacy 上書き挙動を温存 (BC break、Risk §5 で受容)。
- Issue #93 filter (`subagent_type == ""` skip) を rescan でも適用するため、main-transcript 側の Task/Agent `tool_use_id` (= agent_id) と per-subagent ファイル名 `agent-<agent_id>.jsonl` を pair した mapping を rescan 内で構築する。

## 2. Critical files

### Changed

| Path | 役割 / 編集要点 |
|------|-----------------|
| `hooks/record_assistant_usage.py:65-176` | (a) `_extract_assistant_usage` を **module-public alias `extract_assistant_usage`** として再 export (= 既存 hook 経路は無改変、rescan から `from record_assistant_usage import extract_assistant_usage` で import 可能にする)。(b) `_scan_existing_state` を **2 関数に factor**: `scan_dedup_keys(data_file) -> set[(sid, mid)]` (rescan / live 共用) と既存 `_scan_existing_state` (live hook 専用、内部で `scan_dedup_keys` を call) に分割。(c) `_agent_id_from_filename` も module-public alias `agent_id_from_filename` で export |
| `scripts/rescan_transcripts.py` | (a) `extract_assistant_usage` を import し、main transcript + per-subagent transcript の両方から `assistant_usage` event を yield する `scan_assistant_usage_for_session(main_transcript_path, session_id, project) -> Iterator[dict]` を追加。(b) `derive_valid_agent_ids_from_transcript(main_transcript_path) -> set[str]` を追加 (Issue #93 filter 用、main-transcript の Task/Agent `tool_use_id` で `subagent_type != ""` のものだけ拾う)。(c) `subagent_start` event の emit に `tool_use_id` を **追加** (= live hook と schema 揃え、`docs/spec/usage-jsonl-events.md:39-41` に整合)。(d) main CLI: **default を `--append` (= dedup 込み append) に切替**、新 `--overwrite` flag で legacy 全消し再生成。(e) `write_events_with_dedup(events, output_path, existing_keys=None)` を追加 (= main + per-subagent 走査済み events に対し既存 `usage.jsonl` の `(session_id, message_id)` set で dedup し append) |
| `reports/summary.py` | `--include-cost` flag 追加。`print_report` 内で flag が True のとき `aggregate_session_breakdown(events, top_n=None)` を呼び、`estimated_cost_usd` desc で sort して top 10 を render。Total estimated cost 行と参考値 disclaimer (`※ 実測 token × 価格表掛け算による参考値。価格改定で過去値も動きます。`) を末尾に追加 |
| `tests/test_rescan_transcripts.py` | 既存 `test_main_default_overwrites_output_file` / `test_main_append_flag_preserves_existing_events` を BC 切替に合わせて **書き換え**: default が dedup append になる前提に変更し、`--overwrite` 経路の test を新規追加 (Phase 1 RED と一体運用) |
| `tests/test_summary.py` | `test_summary_include_cost` を追加 (`TestPrintReportIncludeCost` 等のクラス内) |
| `tests/test_export_html.py` | `test_export_html_includes_sessions` を追加 (`TestExportHtmlSessionBreakdown` 等のクラス内) — embedded `window.__DATA__` の `session_breakdown` field が non-empty で正しく描画される regression guard |
| `docs/spec/usage-jsonl-events.md:39-52` | `subagent_start` schema の `tool_use_id` field 説明に「rescan 経路でも emit」 1 行追加。`assistant_usage` 章 (line 103-) に「rescan 経路 (`scripts/rescan_transcripts.py`) でも同 schema で backfill される」 1 行追加 |
| `docs/transcript-format.md:363-371` | `rescan_transcripts.py --append` 例の文言を **default が append になった事実に整合**: `python3 scripts/rescan_transcripts.py` (= 旧 `--append` 等価) と `python3 scripts/rescan_transcripts.py --overwrite` (= 旧 default 等価) の 2 例を併記。AC「BC break のドキュメント反映」を満たす |
| `docs/reference/prompt-persistence.md:74` | 同上の文言整合 (= `--append` 明記の例を default 例に揃えるか、`--append` を残しつつ「v0.8.0 から default」 1 行 note を追加) |
| `commands/usage-archive.md:15` | `rescan_transcripts.py --append` 言及を 1 行更新 (= default 切替後の表記揃え) |

### New

| Path | 役割 |
|------|------|
| `tests/test_rescan_assistant_usage.py` | rescan の assistant_usage backfill 専用 test。`TestRescanBackfill` (main + per-subagent backfill / Issue #93 filter / source field 正しい付与) / `TestRescanIdempotent` (2 回目走行で events 増えない) / `TestLiveAndRescanNoDuplicate` (live hook 着弾済 jsonl に対し rescan で重複しない) を含む |
| `docs/plans/104-rescan-cost.md` | 本 plan |

### 無変更 (= touch すると AC 違反)

- `cost_metrics.py` (Issue #99 で確立済、本 issue は **呼ぶ側**)
- `dashboard/server.py` の `build_dashboard_data` / `render_static_html` (= 既に `session_breakdown` 込みの HTML を出すので Phase 3 で test 追加のみ)
- `hooks/record_assistant_usage.py:179-251` の `handle_stop` 本体 (= 抽出ロジックを export するだけで live hook 振る舞いは無変更)

## 3. Ordered steps (TDD pace, commit 単位込み)

各 step は **test first → impl → commit** の trio。code-changing step に test なしで commit しない。

### Step 1 — `record_assistant_usage` module-level export 整備

- **test first** (`tests/test_record_assistant_usage.py` 既存ファイルへ追加):
  - `TestModuleLevelExports::test_extract_assistant_usage_is_module_public`: `from record_assistant_usage import extract_assistant_usage` が成功し、callable
  - `TestModuleLevelExports::test_scan_dedup_keys_returns_session_message_id_set`: 既存 `usage.jsonl` (= `assistant_usage` 行のみ含む小 fixture) を渡すと `set[(session_id, message_id)]` を返す。session 引数を取らない (= rescan は全 session 横断で dedup する必要があるため)
  - `TestModuleLevelExports::test_agent_id_from_filename_is_module_public`: `agent-foo123.jsonl` → `foo123`、prefix なしは空文字
  - `TestExistingScanExistingStateUnchanged::test_scan_existing_state_still_returns_two_sets`: 既存 hook 経路の `_scan_existing_state` が依然として `(existing_keys, valid_agent_ids)` の 2-tuple を返す (= live hook 振る舞い不変)
- **impl**:
  - `_extract_assistant_usage` の前に `extract_assistant_usage = _extract_assistant_usage` の module-public alias 追加 (rename しない: 既存 internal callers を破壊しない)
  - `_agent_id_from_filename` も同様に `agent_id_from_filename` alias 追加
  - `_scan_existing_state` を 2 関数に **factor** (R3-P1 反映、両 signature を symmetric に pin):
    - `scan_dedup_keys(data_file) -> set[tuple[str, str]]` (rescan / live 共用): session 引数を**取らない**。全 session の `(session_id, message_id)` set を返す
    - `_scan_valid_agent_ids(data_file, session_id) -> set[str]` (live hook 専用): 既存 `_scan_existing_state` の per-session valid_agent_ids 抽出ロジックをそのまま移植。session_id 引数を**残す** (= live hook 側は per-session filter が必要、rescan は本関数を呼ばないので session 引数の有無は live 専用関心事)
    - 既存 `_scan_existing_state` は wrapper として残し、内部で 2 関数に dispatch (`return scan_dedup_keys(data_file), _scan_valid_agent_ids(data_file, session_id)`)。live hook 振る舞いは byte-for-byte 不変
- **commit**: `refactor(hooks): expose extract_assistant_usage / scan_dedup_keys as module-public for rescan reuse (#104)`

### Step 2 — rescan で `subagent_start` に `tool_use_id` を emit

- **test first** (`tests/test_rescan_transcripts.py::TestExtractEventsFromRow` 既存クラスに追加):
  - `test_subagent_start_emits_tool_use_id`: Task block (`tool_use_id="toolu_xyz"`) → emit された `subagent_start` event に `tool_use_id == "toolu_xyz"` が含まれる
  - `test_subagent_start_omits_tool_use_id_when_missing`: tool_use block 内に `id` (= `tool_use_id` source) がない場合は event に key が出ない (= live hook の `if "tool_use_id" in data` パターンと意味論を揃える、`hooks/record_subagent.py:38-39` 参照)
- **impl**: `scripts/rescan_transcripts.py:_extract_events_from_row` の `subagent_start` 構築箇所 (line 76-83) で `block.get("id")` を読み、truthy なら `tool_use_id` field を加える (= live hook の subagent_start schema と整合、`docs/spec/usage-jsonl-events.md:39-41`)
- **commit**: `feat(rescan): emit tool_use_id on subagent_start events (#104)`

### Step 3 — rescan で `assistant_usage` backfill (main transcript only)

- **test first** (`tests/test_rescan_assistant_usage.py` 新規):
  - `TestRescanBackfill::test_main_transcript_assistant_usage_backfilled`: 1 transcript file (assistant message 2 件 + usage block) → rescan が `event_type=assistant_usage` を 2 件 emit、`source=="main"` 付き
  - `TestRescanBackfill::test_message_without_id_is_skipped`: msg.id 欠損 row は dedup key 不能なので skip (= live hook と同 contract)
  - `TestRescanBackfill::test_naive_timestamp_is_skipped`: naive ISO は skip
- **impl**: `scripts/rescan_transcripts.py` に `scan_assistant_usage_for_session(main_transcript_path, session_id, project) -> Iterator[dict]` を追加。`extract_assistant_usage(transcript_path, session_id=..., project=..., source="main")` を呼ぶだけの thin wrapper。`scan_all` 関数の中で main transcript 1 つにつき本関数を呼び、events list に concat する
- **commit**: `feat(rescan): backfill assistant_usage from main transcript (#104)`

### Step 4 — rescan で per-subagent transcript backfill + Issue #93 filter

- **test first** (`tests/test_rescan_assistant_usage.py`):
  - `TestRescanBackfill::test_per_subagent_transcript_backfilled_with_subagent_source`: main transcript に Task block (`tool_use_id="agent-a"`, `subagent_type="Explore"`) + `<session>/subagents/agent-agent-a.jsonl` に 1 assistant message → rescan は per-subagent 経路の event を `source=="subagent"` で emit
  - `TestRescanBackfill::test_per_subagent_with_empty_subagent_type_skipped`: Task block の `subagent_type==""` (Issue #93 filter 対象) → 対応する per-subagent transcript の events は **emit されない**
  - `TestRescanBackfill::test_orphan_per_subagent_file_without_main_task_block_skipped`: main 側に対応 Task block を持たない per-subagent ファイルは skip (= valid_agent_ids に入らないため)
- **impl**:
  - `scripts/rescan_transcripts.py` に `derive_valid_agent_ids_from_transcript(main_transcript_path) -> set[str]` を追加。main transcript を line 走査し、`type == "assistant"` の content blocks のうち `name in {"Task", "Agent"}` かつ `input.subagent_type` が non-empty な block の `id` field を集めて set 化
  - `scan_assistant_usage_for_session` を拡張: `<session_dir>/subagents/agent-*.jsonl` を glob し、`agent_id_from_filename` で抽出した id が `valid_agent_ids` に含まれる file のみ `extract_assistant_usage(..., source="subagent")` で yield
  - `scan_all` の loop で「1 main transcript ごとに valid_agent_ids 構築 + per-subagent 走査」を 1 pass に統合
  - **impl コメント (P4 反映)**: `derive_valid_agent_ids_from_transcript` 関数定義の直前に
    ```python
    # NOTE: live-hook (record_assistant_usage._scan_existing_state) derives valid_agent_ids
    # from `subagent_stop` events in usage.jsonl, not from main-transcript Task block .id.
    # For transcripts predating reliable tool_use_id population, rescan may undercount
    # per-subagent files that live-hook would have collected. This is Option A strict
    # adherence — see docs/plans/104-rescan-cost.md §5 R2.
    ```
    の 5-line block を入れる (= future debugger が「rescan は拾わなかったが live-hook なら拾った」case を chase したとき rationale が impl 直近にある)
- **commit**: `feat(rescan): backfill assistant_usage from per-subagent transcripts with Issue #93 filter (#104)`

### Step 5 — rescan default を `--append` (dedup) に切替 + `--overwrite` flag 追加

- **test first** (`tests/test_rescan_transcripts.py::TestMainCLI` 既存クラスを書き換え + 新規 cases):
  - `test_main_default_appends_with_dedup` (= **既存 `test_main_default_overwrites_output_file` を置換**): default 動作で既存 `usage.jsonl` の event は **保持** され、新 events のうち既存 dedup key と被らない分のみ追記
  - `test_main_overwrite_flag_replaces_existing_file` (新規): `--overwrite` で legacy 全消し再生成 (= 旧 default 挙動)
  - `test_main_append_flag_now_dedups_assistant_usage` (P3 反映 = **既存 `test_main_append_flag_preserves_existing_events` を rename + 拡張**): `--append` を明示した場合の **意味論シフト** を pin。assert は 2 段:
    - (a) 旧挙動 carry-over: 既存 `usage.jsonl` の events は破壊されない (旧 `--append` semantics 維持)
    - (b) 新挙動 add-on: 同 transcript を repeat した二回目で `assistant_usage` event 数が増えない (= dedup 適用)
    - = 「`--append` は単なる no-op flag」では**ない**ことを test で明示。Population A doc-promised idempotency を充足する truthful 振る舞いに変わったことを構造で固定
  - `test_main_default_dedups_assistant_usage_by_session_message_id`: 既存 jsonl に `(s1, msg1)` の `assistant_usage` がある状態で同 transcript を rescan → events 数増えず (idempotent)
- **impl**:
  - `argparse`: `--append` を残しつつ deprecated note (no-op として残す = BC 互換のため)、`--overwrite` flag を追加
  - `main()` のロジックを切替: default `--overwrite` 不在のとき `scan_dedup_keys(DATA_FILE)` で既存 `(sid, mid)` set を取得し、scan 結果の `assistant_usage` event をその set で filter してから append。**`assistant_usage` 以外の event (skill_tool / subagent_start / user_slash_command 等) も append される** が、これらは別 dedup 経路で run-time に集計側で吸収される設計 (= `subagent_metrics` の min(timestamp) dedup 等、`docs/plans/100-subagent-stop-payload-and-dedup.md:618` 既知)
  - `--overwrite` のとき: 旧 default 通り全消し再生成 (`write_events(events, DATA_FILE, append=False)`)
- **commit**: `feat(rescan): switch default to append+dedup, add --overwrite flag (BC break, #104)`

### Step 6 — live hook ↔ rescan 経路の二重観測 dedup integration test

- **test first** (`tests/test_rescan_assistant_usage.py`):
  - `TestLiveAndRescanNoDuplicate::test_live_recorded_event_not_duplicated_by_rescan`: 既存 `usage.jsonl` に live hook 経由で書かれた `assistant_usage` 1 件 (`session_id=s1, message_id=m1, source="main"`) がある状態で同じ transcript を rescan → 最終 jsonl の `(s1, m1)` 件数は **1** (rescan 経路の同 message_id 行は dedup される)
  - `TestRescanIdempotent::test_rescan_twice_does_not_increase_events`: rescan を 2 連続実行で **`assistant_usage`** の行数同じ
  - `TestRescanIdempotent::test_rescan_then_live_then_rescan_idempotent`: rescan → live hook 1 件追加 → rescan の三段で `assistant_usage` 行数 1 件分のみ増加
  - `TestRescanIdempotent::test_rescan_twice_keeps_assistant_usage_count_stable_but_doubles_skill_tool_count` (P2 反映 — **意図的振る舞いの structural pin**): default mode で同 transcript を 2 回 rescan したとき:
    - (a) `assistant_usage` の行数: 1 回目と同じ (= dedup が効く)
    - (b) `skill_tool` / `subagent_start` / `user_slash_command` の行数: 2 倍 (= 意図的に dedup されない、`--overwrite` 推奨が `prompt-persistence.md` で示される)
    - test docstring に「AC scope is `assistant_usage` idempotency only. Other event types intentionally not deduped — use `--overwrite` for clean reset (see docs/reference/prompt-persistence.md v0.8.0)」と明記。**将来 contributor が「duplication = bug」と誤読し silently 修正することを防ぐ structural guard**
- **impl**: 既に Step 5 までで dedup が効くため、本 step は test 追加のみで GREEN (= integration verification の役割)
- **commit**: `test(rescan): pin live↔rescan no-duplicate invariant (#104)`

### Step 7 — `reports/summary.py --include-cost`

- **test first** (`tests/test_summary.py` に `TestPrintReportIncludeCost` クラス追加):
  - `test_summary_include_cost_prints_total_line`: `assistant_usage` 数件入りの fixture で `--include-cost` → stdout に `Total estimated cost: $` 文字列が含まれる
  - `test_summary_include_cost_prints_top10_header`: stdout に `Top 10 sessions by estimated cost` を含む
  - `test_summary_include_cost_sorts_descending`: 既知 cost 値の sessions 3 つを入れ、出力順が cost desc であること
  - `test_summary_include_cost_caps_at_10`: 11 sessions 入れて 10 行のみ render
  - `test_summary_include_cost_prints_disclaimer` (P5 反映): stdout 末尾に `_COST_DISCLAIMER` (= module-level constant) が含まれる。assert は `assert summary._COST_DISCLAIMER in stdout` の形 (= 正確な文字列 match を 1 箇所に集約、copy-edit に対する test の脆さを軽減)
  - `test_summary_without_include_cost_omits_cost_section`: flag なしで上記 4 文字列が **どれも含まれない**
  - `test_summary_include_cost_handles_empty_events`: events なしで「Total estimated cost: $0.0000」+ Top 10 header 下「(no data)」相当 (= AC 文面に強制はないが、空表示 case を pin)
- **impl**:
  - `reports/summary.py` の argparse に `--include-cost` 追加
  - **module-level constant 追加 (P5 反映)**: file 上部 (DATA_FILE 定義の直後あたり) に `_COST_DISCLAIMER = "※ 実測 token × 価格表掛け算による参考値。価格改定で過去値も動きます。"` を 1 行で定義 (= test と impl が同 constant を参照、copy-edit 時に文言修正は 1 箇所だけで済む)
  - `print_report(events, *, include_cost=False)` に flag 受け取り。`include_cost=True` のとき:
    1. `from cost_metrics import aggregate_session_breakdown` (= 既に存在、Issue #99)
    2. `breakdown = aggregate_session_breakdown(events, top_n=10**9)` で全 session を取り出し (R3-Q2 note: `cost_metrics.aggregate_session_breakdown` の `top_n: int` parameter は None 未対応 = 大きな magic number で「事実上無制限」を表現する pragmatic choice。sentinel 化は本 PR で行わない)
    3. `total = round(sum(b["estimated_cost_usd"] for b in breakdown), 4)` で Total を計算
    4. `top10 = sorted(breakdown, key=lambda b: -b["estimated_cost_usd"])[:10]`
    5. `print(f"\n=== Cost (estimated) ===")` → `print(f"  Total estimated cost: ${total:.4f}")` → `print("\n  Top 10 sessions by estimated cost")` → 各 row `print(f"  ${b['estimated_cost_usd']:>9.4f}  {b['session_id'][:8]}  {b['project']}")`
    6. 末尾 `print(f"\n{_COST_DISCLAIMER}")`
  - `main()` で `print_report(load_events(...), include_cost=args.include_cost)`
- **commit**: `feat(summary): add --include-cost flag with Top 10 sessions ranking (#104)`

### Step 8 — `reports/export_html.py` regression guard test

- **test first** (`tests/test_export_html.py::TestExportHtmlSessionBreakdown` 新規クラス):
  - `test_export_html_embeds_session_breakdown_field`: 1 session の `assistant_usage` を入れた fixture で `export_html` を走らせ、出力 HTML 内の `window.__DATA__` JSON に `session_breakdown` key があり、length >= 1
  - `test_export_html_session_breakdown_includes_estimated_cost`: 同 row の `estimated_cost_usd` field が存在し float
  - `test_export_html_with_empty_events_has_empty_session_breakdown`: events 0 → `session_breakdown` key 自体は存在 (空 list)
- **impl**: 実装変更不要 (Issue #99 で `build_dashboard_data` が既に `session_breakdown` を埋め込む)。test を追加して回帰 guard とする
- **commit**: `test(export_html): pin session_breakdown embedded in static HTML (#104)`

### Step 9 — docs (usage-jsonl-events.md / transcript-format.md / commands/usage-archive.md / prompt-persistence.md)

- **test first**: 文字列 spec test は既存 doc には無いので、本 step は manual review checklist (= Phase 9 commit 単位で `git diff docs/` を verbatim 確認)
- **impl**:
  - `docs/spec/usage-jsonl-events.md:39-41` の `subagent_start` 例の `tool_use_id` 説明に「rescan / live 共通 schema」 1 行追加
  - 同 file の `assistant_usage` 章 (line 103-) に「rescan (`scripts/rescan_transcripts.py`) でも `(session_id, message_id)` first-wins で同 schema で backfill される」 1 行追加
  - `docs/transcript-format.md:363-371` の運用 note を default 切替に追従 (`--append` 例 → default 例 + `--overwrite` 例)
  - `docs/reference/prompt-persistence.md:74` の `--append` 用例を default 例に書き換える (= flag 不要に)
  - **`docs/reference/prompt-persistence.md` の「fingerprint dedup」嘘記述を書き換え** (P1 反映、最重要、**target は content match で指定**: 「`rescan_transcripts.py` は **idempotent**` で始まる段落」、line number は上流 edit で shift する可能性あり、R3-Q3 反映): 旧文 `rescan_transcripts.py は **idempotent** (同じ transcript を 2 回流しても重複追記しない fingerprint dedup)。` → 新文に差し替え:
    > `rescan_transcripts.py` は **`assistant_usage` event について idempotent** (`(session_id, message_id)` first-wins、v0.8.0 から)。`skill_tool` / `subagent_start` / `user_slash_command` 等は dedup されないため、確定的にクリーンな再生成が要るときは `--overwrite` flag を使う。
  - `commands/usage-archive.md:15` の `--append` 言及を整合
- **commit**: `docs: rescan default → append+dedup; document assistant_usage backfill (#104)`

### Step 10 — PR 作成

- branch `feature/104-rescan-cost` (base `v0.8.0`) を push
- `gh pr create --base v0.8.0 --title "feat(rescan,reports): assistant_usage backfill + summary --include-cost (#104)"`
- PR body Test plan:
  - `pytest tests/test_rescan_transcripts.py tests/test_rescan_assistant_usage.py tests/test_record_assistant_usage.py tests/test_summary.py tests/test_export_html.py tests/test_cost_metrics.py`
  - 実 `~/.claude/projects/` に対し `python3 scripts/rescan_transcripts.py --dry-run` で count が想定範囲か smoke
  - `python3 reports/summary.py --include-cost` を実 jsonl で走らせて Top 10 行 / disclaimer が出ること目視
  - `python3 reports/export_html.py --output /tmp/r.html` 後に file を chrome-devtools MCP で開き Sessions table が描画されることを確認
  - **BC break smoke**: 旧 default に依存していたユーザーが `--overwrite` で旧挙動取得できること verbatim 確認

## 4. TDD test plan — クラス一覧

### 新規ファイル `tests/test_rescan_assistant_usage.py`

| Class | Cases |
|-------|-------|
| `TestRescanBackfill` | `test_main_transcript_assistant_usage_backfilled` / `test_message_without_id_is_skipped` / `test_naive_timestamp_is_skipped` / `test_per_subagent_transcript_backfilled_with_subagent_source` / `test_per_subagent_with_empty_subagent_type_skipped` / `test_orphan_per_subagent_file_without_main_task_block_skipped` |
| `TestRescanIdempotent` | `test_rescan_twice_does_not_increase_events` / `test_rescan_then_live_then_rescan_idempotent` / `test_rescan_twice_keeps_assistant_usage_count_stable_but_doubles_skill_tool_count` (P2 反映 — 意図的な partial dedup を pin) |
| `TestLiveAndRescanNoDuplicate` | `test_live_recorded_event_not_duplicated_by_rescan` |

### 既存ファイル追記

| File | Class / Cases | Phase / Step |
|------|----------------|----------------|
| `tests/test_record_assistant_usage.py` | `TestModuleLevelExports::test_extract_assistant_usage_is_module_public` / `::test_scan_dedup_keys_returns_session_message_id_set` / `::test_agent_id_from_filename_is_module_public` / `TestExistingScanExistingStateUnchanged::test_scan_existing_state_still_returns_two_sets` | Step 1 |
| `tests/test_rescan_transcripts.py::TestExtractEventsFromRow` | `test_subagent_start_emits_tool_use_id` / `test_subagent_start_omits_tool_use_id_when_missing` | Step 2 |
| `tests/test_rescan_transcripts.py::TestMainCLI` | `test_main_default_appends_with_dedup` (既存 `test_main_default_overwrites_output_file` を置換) / `test_main_overwrite_flag_replaces_existing_file` (新規) / `test_main_append_flag_now_dedups_assistant_usage` (P3 反映 = 既存 append test を rename + 拡張、(a) 既存 events 保護 + (b) `assistant_usage` repeat dedup の 2 段 assert) / `test_main_default_dedups_assistant_usage_by_session_message_id` | Step 5 |
| `tests/test_summary.py::TestPrintReportIncludeCost` (新規) | `test_summary_include_cost_prints_total_line` / `test_summary_include_cost_prints_top10_header` / `test_summary_include_cost_sorts_descending` / `test_summary_include_cost_caps_at_10` / `test_summary_include_cost_prints_disclaimer` / `test_summary_without_include_cost_omits_cost_section` / `test_summary_include_cost_handles_empty_events` | Step 7 |
| `tests/test_export_html.py::TestExportHtmlSessionBreakdown` (新規) | `test_export_html_embeds_session_breakdown_field` / `test_export_html_session_breakdown_includes_estimated_cost` / `test_export_html_with_empty_events_has_empty_session_breakdown` | Step 8 |

## 5. Risks / tradeoffs

### R1. rescan default `--append` 切替は **BC break + 既存 doc drift 修正の合せ技**

- **two-population framing** (P1 反映):
  - **Population A (`--append` user)**: `docs/reference/prompt-persistence.md:82-83` は既に「`rescan_transcripts.py` は idempotent (fingerprint dedup)」と約束していたが、現行 script (`scripts/rescan_transcripts.py:1-202`) には fingerprint も dedup logic も**存在しない** (verbatim 確認: `grep fingerprint|dedup` で 0 件 hit)。本 PR で `(session_id, message_id)` first-wins dedup を導入することは Population A から見ると**doc-promised behavior が初めて truthful になる strict improvement**。
  - **Population B (no-flag user)**: 旧 default は overwrite。本 PR で append + dedup に切替えるため、観測される動作が変わる**真の BC break**。`--overwrite` flag で旧挙動を取得できる escape hatch を提供。
- **採用判断**: 「live hook 経路でしか捕れない event の sample (透過保護)」 > 「default 直感が変わる驚き」。理由は `assistant_usage` event が transcript 切詰め / 古い `~/.claude/projects/` 削除等で **rescan からは再導出不能** な場合があり、上書き default は user 観測値を消失させる。さらに既存 doc が約束していた idempotency をようやく実装で支える契機。
- **Mitigation**:
  - `--overwrite` 新 flag で legacy 挙動を温存。docs / PR description / commit message で「BC break / `--overwrite` で旧挙動」を verbatim 明記。
  - `--append` を deprecated no-op として残す (= 既存スクリプト破壊回避)。出力時に warning は出さない (CLI noise を増やさない)。
  - Phase 9 docs で `prompt-persistence.md:82-83` の「fingerprint dedup」嘘記述を **`(session_id, message_id)` first-wins for assistant_usage** という truthful 表現に書き換える (= 残る非対称: 他 event 種別は repeat rescan で重複 — R8 / `--overwrite` 推奨)。
- **Rejected alternative**: feature flag (`USAGE_RESCAN_DEFAULT_APPEND=1`) gating で段階移行する案 → 1 flag が長期に残る overhead が AC「明確な BC break 1 回」より大きい。

### R2. agent_id ↔ subagent_type mapping の正しさ (Option A: strict)

- main-transcript の Task/Agent block の `tool_use_id` (= `id` field) が per-subagent ファイル名 `agent-<agent_id>.jsonl` の suffix と等しい、という前提が **transcript 仕様** (`docs/transcript-format.md` の subagent layout 章)。
- mapping が壊れる失敗モード:
  - (a) main transcript が **truncate** されて Task block より前で切れている → valid_agent_ids が undercount → 対応 per-subagent file が **誤って skip**。受容: rescan は best-effort であり、live hook で着弾済み event は temaining (= R1 の保護対象と整合)
  - (b) per-subagent file の filename が schema 違反 (例: `agent-foo-bar.jsonl` で agent_id 部に hyphen) → `agent_id_from_filename` は `foo-bar` を返すので fine。**Issue 化なし**
  - (c) main 側 Task block の `id` が空文字 / 欠損 → valid_agent_ids に入らず → per-subagent 全 skip。これは Issue #93 filter rule の strict 実装であり、AC 違反ではない (`docs/spec/usage-jsonl-events.md:90-95`)
- **採用判断**: Option A strict (= 確実に Issue #93 filter を当てる)。Option B (= live hook の `_scan_existing_state` から `valid_agent_ids` を引き継ぐ) は (1) live hook が動いていない過去環境では `usage.jsonl` に `subagent_stop` 自体が無く valid_agent_ids が空 → backfill が一切走らない致命的副作用、(2) rescan 自体の純度 (transcript only) を破壊する → 不採用。
- **Risk pin**: Phase 4 test `test_orphan_per_subagent_file_without_main_task_block_skipped` で structural pin。

### R3. `subagent_id` 同時 emit の判断 (subagent_start に `tool_use_id` を加える)

- 現状 rescan は `subagent_start` event に `subagent_id` も `tool_use_id` も emit しない (= live hook と schema 不一致)。`docs/spec/usage-jsonl-events.md:39-41` の正本 schema は `tool_use_id` を持つので、rescan も live hook と同 schema に揃えるのが正しい。
- 採用: **`tool_use_id` のみ emit** (Step 2)。`subagent_id` は live hook の `subagent_stop` event 専用 field (`docs/spec/usage-jsonl-events.md:50-52`) で「stop hook payload の `agent_id`」を写しただけ。rescan は stop hook payload を観測できないので `subagent_id` は emit しない (= AC「stripped subagent_id from existing rescan」を継承)。
- **Tradeoff**: live hook の `subagent_stop.subagent_id` と rescan の `subagent_start.tool_use_id` は **同じ agent_id を指すが field 名が違う** という非対称が残る。これは Issue #99/#93 で確立済の schema convention であり本 issue で揃えるべきではない (= scope 外)。

### R4. `_scan_existing_state` 二役問題の解消

- 現状: `hooks/record_assistant_usage.py:_scan_existing_state` は **dedup key set + valid_agent_ids set** の 2 役を 1 関数が同時に担う。rescan からは valid_agent_ids 部分は **transcript 由来で別途構築** したいので 2 役の片方だけが欲しい。
- 採用: **factor into 2 functions** (Step 1)。`scan_dedup_keys(data_file) -> set` (rescan / live 共用) + 既存 `_scan_existing_state` は wrapper として残し内部で `scan_dedup_keys` + `_scan_valid_agent_ids` を call。理由:
  - (a) rescan が hook 内部の private helper を import するのは layering 違反、module-public alias を作るべき
  - (b) 1 関数に 2 役を残したまま rescan からも呼ぶと、rescan が valid_agent_ids 部分の戻り値を捨てる無駄走査になる
  - (c) factor すれば live hook 側コードは 1 行 (= `scan_dedup_keys(data_file)` を呼ぶだけ) の差分で byte-equivalent
- **Rejected alternative**: rescan 用に `derive_valid_agent_ids_from_transcripts(main_transcript_path)` だけ別 module で実装、hook 側 `_scan_existing_state` は触らない → dedup key set 側の DRY を諦めることになる (= rescan が独自実装する重複)。

### R5. `summary --include-archive` との composability

- `reports/summary.py` には既に `--include-archive` flag がある (line 173-176)。`--include-cost` と同時指定 (`summary.py --include-archive --include-cost`) のとき、cost 集計は **archive を含む全 events** に対して行われるべき。
- 採用: `print_report` が受け取る events list は `load_events(include_archive=...)` で既に決まっているので、`include_cost` は単に「集計の追加 section を render するか」flag に閉じる。`--include-cost` は archive の有無を変えない (= flag 直交)。
- **Risk pin**: Step 7 の `test_summary_include_cost_*` は archive を含めない fixture でのみ走らせ、archive 経路は既存 `tests/test_summary_archive.py` (もしあれば) と直交させる。本 issue で archive × cost の cross test は **追加しない** (= scope 外)。

### R6. `export_html` test の scope

- AC は「render_static_html(build_dashboard_data(events)) 経路は #99 完了時点で session_breakdown を含むため、static HTML 上で sessions table が render されることを確認」とあり、**実装変更不要 / regression guard test のみ** が要求。
- 採用: Step 8 で `tests/test_export_html.py` に `TestExportHtmlSessionBreakdown` クラス 3 case を追加。`session_breakdown` field の embed と `estimated_cost_usd` の有無のみ pin。**DOM レベル / chrome-devtools MCP visual smoke は本 issue では追加しない** (= Issue #103 で UI 側の smoke は確立済、`export_html` の HTML structure pin は重複)。
- **Risk pin**: 既存 `TestStaticExportSurfaceTab::test_static_export_embeds_new_surface_data` (line 151-167) と同じ pattern を踏襲し、新 surface field 追加時の drift guard と同等 cohort で動くようにする。

### R7. 既存 `tests/test_rescan_transcripts.py::TestMainCLI::test_main_default_overwrites_output_file` の置換責任

- BC break が test レベルで露出するため、Step 5 で**この test 自体を「default が dedup append になる」前提に書き換える**。命名も `test_main_default_appends_with_dedup` に rename。
- bisect note: Step 5 commit 単独で「default 切替 + 既存 test 書き換え + `--overwrite` test 追加」が 1 commit に収まるので、`git bisect` で「BC break ←→ test 整合」が 1 commit に閉じる。

### R8. `assistant_usage` 以外 event の rescan default append での重複懸念

- default が append + dedup になっても、**dedup は `assistant_usage` の `(session_id, message_id)` set のみで効く**。`skill_tool` / `subagent_start` / `user_slash_command` 等は rescan を 2 度走らせると **重複 append される**。
- 受容判断: 既存集計側 (`subagent_metrics.aggregate_subagent_metrics` の min(timestamp) dedup、`docs/plans/100-subagent-stop-payload-and-dedup.md` で確立) は重複行を吸収する設計。`skill_tool` は count metric なので重複が即数値ズレを生むが、本 issue は「`assistant_usage` の idempotent を満たす」が AC であり、その他 event の rescan 重複は **既存挙動の延長** (= `--append` flag 既存挙動と同じ)。
- **Mitigation**: docs に「`--overwrite` を使えば全 event 種類で確定的にクリーンになる」 1 行を追加し、heavy rescan 利用者には `--overwrite` を推奨。

## 6. Out of scope (現状 disposition)

- archive (`archive/*.jsonl.gz`) opt-in での全期間 cost 集計 — Issue #30 followup と一緒に検討、本 issue は hot tier 限定
- audit-grade コスト snapshot (時点固定値) — 価格表 snapshot は将来 issue
- export_html での period 選択 — 常に全期間 (Issue #85 と整合)
- cost-aware alert (月予算超過 notification) — 将来 issue
- `subagent_id` を rescan の `subagent_start` event に同時 emit する — 採用しない (R3 参照、`tool_use_id` のみ揃える)
- Sessions UI / dashboard 側変更 — Issue #103 で完了済、本 issue は touch しない
- live hook (`hooks/record_assistant_usage.py:handle_stop`) の振る舞い変更 — Issue #99 で確立済、本 issue は **import される側の export 整備のみ** で振る舞い byte-for-byte 不変
- `summary --include-archive --include-cost` 同時指定の専用 cross test — flag 直交として扱い、本 issue で test 追加しない (R5 参照)
- `skill_tool` / `subagent_start` / `user_slash_command` への `(session_id, tool_use_id)` 等の dedup 拡張 (P2 反映) — 本 issue は AC「`assistant_usage` の idempotent」のみ。他 event の dedup は要件解析が別途必要 (例: `skill_tool` は同 session 内で同 skill を意図的に複数回呼ぶ valid case があるので dedup key 設計は非自明)。`--overwrite` recommendation を docs に置くことで対処。**Open question §6 として残す** (将来 issue 候補)

## 7. Open questions

1. **`--append` flag の deprecated note を CLI help に出すか?**
   - 案 (A): 出さない (= 静かに no-op、既存スクリプト破壊回避を最優先)。
   - 案 (B): `--append` の help 文に `(deprecated: now default behavior since v0.8.0)` を 1 行追加。
   - **暫定採用**: (A)。理由は CLI noise 増を避け、docs 側で BC break を verbatim 説明する方が読み手の認知負荷が低い。reviewer から指摘あれば (B) に切替可能。
2. **`scan_dedup_keys` の戻り型を `set[tuple[str, str]]` で固定するか、`frozenset` か?**
   - 暫定採用: `set` (= existing `_scan_existing_state` が `set` を返しているので signature を揃える)。caller 側で `in` check しか使わないので mutability は load-bearing でない。
3. **`reports/summary.py --include-cost` 出力フォーマットの Top 10 列構成**
   - AC「session top 10 cost ranking」のみで列構成は spec なし。**暫定採用**: `${cost:>9.4f}  ${session_id[:8]}  ${project}` (= 既存 Skills セクションの列幅 4-character cost + short session id + project name)。reviewer / user feedback で列追加 (model name / message count) を後追い拡張可。
4. **`derive_valid_agent_ids_from_transcript` の置き場所 (rescan_transcripts.py vs cost_metrics.py vs hooks/record_assistant_usage.py)**
   - 暫定採用: `rescan_transcripts.py`。理由は (a) 関数の責任が「main transcript を読んで Task block の id を集める」で rescan 固有の前処理、(b) hook 側は live で Task block を直接観測しないので不要、(c) `cost_metrics.py` は events 集計の純関数 module で transcript 読みは責任外。
5. **`subagent_start.tool_use_id` を rescan が emit するようになることで既存 dashboard 集計に副作用はないか?**
   - `subagent_metrics` は `tool_use_id` を invocation pairing key として既に使用 (`docs/transcript-format.md:262-267`)。rescan が今まで emit していなかった分が live と同等になることで、過去 session の invocation pairing が **改善** される側 (= regression なし)。Phase 5 PR test plan で実 jsonl smoke を行い、`/api/data` の subagent count が rescan 前後で連続性を保つことを確認。
6. **Non-`assistant_usage` event の dedup 拡張は将来 issue 化するか?**
   - Out of scope §6 で defer 記載済。**暫定 disposition**: 本 PR ship 後、ユーザーから「summary の skill 数が rescan で膨らんだ」フィードバックが来たら issue 起票。それまでは `--overwrite` 推奨で受ける。dedup key 設計が event 種別ごとに非自明 (例: `skill_tool` は同一 session 内 valid 重複を区別する key が必要) であり、AC スコープを越える spec 議論を要するため本 PR には含めない。
