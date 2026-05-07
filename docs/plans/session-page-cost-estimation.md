# Session Page + Cost (実測) Implementation Plan

> Tracking issues:
> - **#99** — infra コア (`assistant_usage` event + `cost_metrics` + main / per-subagent transcript 収集 + `/api/data` 拡張)
> - **#103** — Sessions ページ UI (5 番目タブ + cost 列 + service_tier 表示)
> - **#104** — rescan + reports 拡張 (`assistant_usage` backfill / `summary --include-cost` / `export_html` 反映)
>
> Milestone: v0.8.0 (cost feature は機能追加なので minor bump、`/usage-summary` / `/usage-export-html` も派生影響あり)
> Base branch: `v0.8.0` (release branch model に従い main から派生して push)
> Feature branch: 下記 §1.1 の scope split に合わせて 3 本 (`feature/99-*` / `feature/103-*` / `feature/104-*`)
>
> 全体スコープ = 本 plan + Issue #99 (旧版「per-subagent transcript の `message.usage` 実測ベース格上げ」) を統合。**実測 token × 価格表掛け算** による参考値計算を採用 (token は実測、cost は価格改定で動く参考値)。

## 0. Companion docs

- 設計の前提: `docs/reference/cost-calculation-design.md` (§9 per-message 集計の差別化案を本 plan で実装に倒す)
- spec 増補: `docs/spec/usage-jsonl-events.md` (新 event_type 追加) / `docs/spec/dashboard-api.md` (新 field 追加) / `docs/spec/dashboard-runtime.md` (5th page 追加)

## 1. Goal

ダッシュボードに **Sessions ページ (5番目のタブ)** を追加し、session 単位の以下情報を 1 行 1 session の表形式で可視化する:

| 列 | 内容 | データ源 |
|---|---|---|
| `session_id` (短縮) | 末尾 8 文字程度 | `session_start.session_id` |
| `project` | プロジェクト名 | `session_start.project` |
| 開始時刻 | local TZ 整形 | `session_start.timestamp` |
| 期間 | session_end - session_start (active なら "進行中") | `session_end.timestamp - session_start.timestamp` |
| 主モデル / model 内訳 | 使用モデル名 (複数なら count 比) | `assistant_usage.model` (新 event) |
| 入出力 token 合計 | 4 種別 (input / output / cache_read / cache_creation) | `assistant_usage.*_tokens` (新 event) |
| **推計コスト** (USD) | model 別集約 → price 適用 → reduce 合算 (= 実測 token × 価格表) | `cost_metrics.calculate_session_cost()` (新) |
| **service_tier 内訳** | priority / standard 等の比率 chip | `assistant_usage.service_tier` (Issue #99 由来) |
| skill / slash 件数 | session 内の `skill_tool` + `user_slash_command` 件数 | 既存 |
| subagent 件数 | session 内の subagent invocation 件数 (dedup 済) | `subagent_metrics.aggregate_subagent_metrics` |

加えて行クリックで **drill-down** (per-message timeline + model 切替の可視化) は **本 issue では実装しない** (§6 で out-of-scope 化)。

### コスト計算の前提

`docs/reference/cost-calculation-design.md` §3-§5 に従う:
- DB / event log にコスト値は **保存しない** (raw token + model のみ保存、表示時にオンデマンド計算)
- 4 トークン × per-1M-token rate
- model 別集約 → reduce 合算 (混在 sum の罠回避)
- 未知 model は Sonnet fallback
- **token は実測値** (transcript の `message.usage` から直接抽出、Issue #99 由来)、**cost は実測 token × 価格表掛け算による参考値** (価格改定で過去値も動く)
- 収集源は **メイン session transcript + per-subagent transcript の両方** (= subagent invocation 単位の cost も把握可能。Issue #93 の `subagent_type == ""` filter rule 適用後の type 入り invocation のみ対象)

## 1.1 Scope split (3 issue に分割済)

本 plan の全体スコープ (= plan 本文 + Issue #99 旧版の per-subagent transcript / service_tier / inference_geo) を **3 issue に分割**:

| Issue | 範囲 | 依存 |
|---|---|---|
| **#99 (infra)** | `assistant_usage` event (4 token + service_tier + inference_geo + source) + `cost_metrics.py` + Stop hook での **メイン + per-subagent transcript** 両収集 + `/api/data` への `session_breakdown` field 追加 (UI 無し) | なし。v0.8.0 base に直接 merge |
| **#103 (UI)** | dashboard Sessions ページ (5th nav tab + page section + renderer + cost 列 + service_tier 表示) | #99 完了が前提 |
| **#104 (rescan + reports)** | `scripts/rescan_transcripts.py` での過去分 backfill + `reports/summary.py --include-cost` + `reports/export_html.py` への展開 | #99 完了が前提 (#103 とは並列可) |

それぞれ独立 PR として `v0.8.0` にマージ。本 plan は 3 issue を **跨いで通しで** 記述する。実装着手時に各 issue のスコープに合わせて step を抽出する想定。

## 2. Critical files

### New

| Path | 役割 | 担当 sub-issue |
|---|---|---|
| `hooks/record_assistant_usage.py` | Stop hook で transcript の assistant message を読み、(model, 4 token カウント, message_id, ts) を `assistant_usage` event として `usage.jsonl` に追記 | A |
| `cost_metrics.py` | 価格表 + `calculate_message_cost()` / `calculate_session_cost()` / `aggregate_session_breakdown()` の純関数群 | A |
| `tests/test_record_assistant_usage.py` | hook の transcript 読み + dedup + jsonl 追記 test | A |
| `tests/test_cost_metrics.py` | 価格計算 / model 別集約 / unknown model fallback / session 集約 test | A |
| `tests/test_dashboard_sessions_api.py` | `/api/data` の `session_breakdown` field schema test | A |
| `dashboard/template/scripts/45_renderers_sessions.js` | Sessions ページ renderer (concat 順は §3 Step 7 で確定) | B |
| `dashboard/template/styles/55_sessions.css` | Sessions テーブルの見た目 (component-level) | B |
| `tests/test_dashboard_sessions_ui.py` | template / page-scoped early-out / row format の structural pin | B |
| `tests/test_rescan_assistant_usage.py` | rescan 経路で assistant_usage が backfill される + dedup が効く test | C |

### Changed

| Path | 変更内容 | 担当 |
|---|---|---|
| `hooks/hooks.json` | 既存 Stop hook (verify_session.py) と並列に `record_assistant_usage.py` を Stop hook として登録 | A |
| `docs/spec/usage-jsonl-events.md` | 新 event_type `assistant_usage` の schema (model + 4 種 token + message_id + timestamp) を追記 | A |
| `docs/spec/dashboard-api.md` | `session_breakdown` field 仕様を追加 (schema / 集計仕様 / sort / top-N / 価格表は別 doc 参照) | A |
| `docs/reference/cost-calculation-design.md` | 末尾に "本 plan で実装した版" セクション 1 つ追記 (本 plan link + 採用した設計判断) | A |
| `dashboard/server.py` | `aggregate_session_breakdown(events, top_n=...)` を追加。`build_dashboard_data` で `session_breakdown` を返す。`_MAIN_JS_FILES` に `45_renderers_sessions.js` を `40_renderers_quality.js` の後 `50_renderers_surface.js` の前に追加 | A (集計部) / B (concat) |
| `dashboard/template/shell.html` | nav の 4 タブ → 5 タブ化 (`#/sessions` 追加) + 5th `<section data-page="sessions">` 追加 | B |
| `dashboard/template/scripts/00_router.js` | `HASH_TO_PAGE` に `'#/sessions': 'sessions'` 追加 | B |
| `dashboard/template/scripts/20_load_and_render.js` | sessions page の page-scoped renderer 呼び出しを追加 (Patterns/Quality/Surface と同じ pattern) | B |
| `scripts/rescan_transcripts.py` | 過去 transcript から `assistant_usage` を backfill する。message_id ベース dedup で既存 event との重複回避 | C |
| `reports/summary.py` | `--include-cost` フラグ追加。session-level cost summary を出力 | C |
| `reports/export_html.py` | `render_static_html` で session_breakdown も embed (export 時は period 概念無いので全期間) | C |
| `subagent_metrics.py` | session 単位の subagent 件数を返す薄い helper (`session_subagent_counts(events) -> dict[session_id, count]`) を additive 追加 (既存集計ロジック再利用) | A |

### 触らないファイル

- `hooks/record_skill.py` / `hooks/record_subagent.py` / `hooks/record_session.py` — assistant_usage 収集は別 hook に切り出すため touch しない
- `hooks/verify_session.py` — assistant_usage 照合の追加は本 plan scope 外 (§6 out-of-scope)
- 既存 `aggregate_skills` / `aggregate_subagent_metrics` / `aggregate_compact_density` — session 単位の view を加えるが本体ロジックは触らない

## 3. Ordered steps (TDD pace, commit 単位込み)

> **前段** (sub-issue A 着手時): `git checkout main && git pull && git checkout -b v0.8.0 && git push -u origin v0.8.0 && git checkout -b feature/99-assistant-usage`

### Step 0 — release branch を切って push (commit 0, sub-issue A 起点)

- branch 作業のみ。以降の commit は `feature/99-assistant-usage` に積む。

### Step 1 — `assistant_usage` event spec (docs first, commit 1, sub-issue A)

- **作業**: `docs/spec/usage-jsonl-events.md` に新 event_type を追記:
  ```jsonc
  // assistant message ごとの token + model 観測 (Stop hook で main / per-subagent transcript から抽出)
  {"event_type": "assistant_usage",
   "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:00:00+00:00",
   "model": "claude-sonnet-4-6",
   "input_tokens": 1234, "output_tokens": 567,
   "cache_read_tokens": 8900, "cache_creation_tokens": 0,
   "message_id": "msg_abc...",
   "service_tier": "standard",        // 任意。transcript の message.usage.service_tier。欠損時 null
   "inference_geo": "us-east",        // 任意。transcript の message.usage.inference_geo。欠損時 null
   "source": "main"}                  // "main" | "subagent" — どの transcript から拾ったか (集計軸)
  ```
- transcript の `usage` キー名 (`cache_read_input_tokens` / `cache_creation_input_tokens`) と event field 名 (`cache_read_tokens` / `cache_creation_tokens`) のマッピング表を記載 (`cost-calculation-design.md` §6 と整合)
- `service_tier` / `inference_geo` は transcript の `message.usage` から passthrough。値の正規化はしない (real-world data quirks をそのまま見せる方針)
- **dedup key**: `(session_id, message_id)` の pair で **first wins** (rescan 二重実行 / hook 再発火 / main + subagent 経路の二重観測に対する idempotent 保証)
- naive timestamp / 欠損 message_id event は drop (silent skip)
- **commit**: `docs(spec): add assistant_usage event_type (Issue #99)`

### Step 2 — `cost_metrics.py` (TDD, commit 2, sub-issue A)

- **test 先行** (`tests/test_cost_metrics.py`):
  - `TestCalculateMessageCost`: 既知 model で `(model, in, out, cr, cc) → expected USD` を pin。4 桁丸めも明示
  - `TestUnknownModelFallback`: 未知 model 名で **Sonnet rate** が適用されることを assert (`docs/reference/cost-calculation-design.md` §2 採用)
  - `TestAggregateSessionBreakdown`: events list (assistant_usage + skill_tool + subagent_*) → session 別 dict に `{tokens_by_model: {}, total_cost_usd: float, model_share: [], skill_count: int, subagent_count: int}` を返すこと
  - `TestModelMixSessionCostInvariant`: 1 session 内で model A と B が混在するとき、**model 別集約 → 各 rate 適用 → reduce** で計算した値が「model A の小計 + model B の小計」と一致 (混在 sum の罠を踏まないことを drift guard)
  - `TestEmptyTokensReturnsZero`: 全 token = 0 の event でも error なく `0.0` を返す
- **実装**: stdlib only。`MODEL_PRICING: dict[str, ModelPricing]` (TypedDict ではなく `NamedTuple` か単純 dict) を module-level に持つ。
  - **⚠️ 価格表は本 step の commit 直前に Anthropic 公式 (https://www.anthropic.com/pricing) で値を pin する**。`cost-calculation-design.md` §2 の数値は AgenticSec からの孫引きで未検証。pin した時点の URL + 取得日時を docstring に明記 (CLAUDE.md "technical identifiers" ルールに従う)
  - 関数 signature:
    - `calculate_message_cost(model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens) -> float`
    - `calculate_session_cost(events_for_session) -> float` (events から session 1 件分の合計)
    - `aggregate_session_breakdown(events, *, now=None, top_n=20) -> list[dict]` ← `/api/data` へ供給する shape
- **commit**: `feat: add cost_metrics.py with per-message cost calculation (Issue #99)`

### Step 3 — `hooks/record_assistant_usage.py` (TDD, commit 3, sub-issue A)

- **test 先行** (`tests/test_record_assistant_usage.py`):
  - `TestStopHookEmitsAssistantUsage`: dummy main transcript fixture (assistant message 3 件、内 1 件は usage 欠損) を渡して、`USAGE_JSONL` が `source="main"` の assistant_usage 2 件で増えること
  - `TestSubagentTranscriptCollected`: dummy per-subagent transcript fixture (`<session_dir>/subagents/agent-<agent_id>.jsonl`) を渡して、`source="subagent"` の assistant_usage が記録されること
  - `TestSubagentTypeFilterRule`: `subagent_type == ""` の invocation に紐づく per-subagent transcript は **skip される** (Issue #93 確定 filter rule に整合)
  - `TestDedupByMessageId`: 同じ transcript で hook を 2 回発火 → events が増えない (= `(session_id, message_id)` で dedup)。main + subagent 経路で同 message_id を二重観測しても 1 件に集約
  - `TestModelSwitchInSession`: 同 session 内で model="opus" の message 1 件 → "haiku" の message 2 件 → assistant_usage 3 件すべて記録され、各 event の `model` が一致
  - `TestServiceTierCaptured`: `message.usage.service_tier` が event の `service_tier` field に passthrough される (priority/standard/null 各ケース)
  - `TestMissingMessageIdSkipped`: `message.id` 欠損 event は silent skip (drop alert は立てない、§Step 1 spec と整合)
  - `TestNaiveTimestampHandled`: TZ なし timestamp は UTC として扱う (既存 `_parse_iso_utc` 慣習)
- **実装**:
  - hook 入力 JSON から `transcript_path` を取得 (CLAUDE.md `docs/transcript-format.md` 参照)
  - **メイン session transcript** を line 単位で読み、`type == "assistant"` の record から `message.id` / `message.model` / `message.usage.{input,output,cache_read_input,cache_creation_input}_tokens` / `message.usage.service_tier` / `message.usage.inference_geo` / `timestamp` / `session_id` / `cwd` を抽出 (`source="main"`)
  - **per-subagent transcript** を `<session_dir>/subagents/agent-<agent_id>.jsonl` の glob で列挙し、Issue #93 で確定した `subagent_type == ""` filter rule 適用後の type 入り invocation のみ処理 (`source="subagent"`)。各ファイルから同様に `message.usage` を抽出
  - 既存 `usage.jsonl` を line 単位で scan し、`(session_id, message_id)` の存在 set を作る (大規模化したら index 化検討。現状 hot tier 180 日 = 上限 数十万行で実用問題なし、§5(d) で議論)
  - 新規分のみ `_append.py` の lock 付き append を使って書き込み (既存規律)
  - silent exit 0 契約 (Stop hook でブロックしない)
  - **subprocess fork は不要** (Stop hook なので transcript 読みは synchronously で完結する。`launch_dashboard.py` の fork-and-detach パターンとは別物)
- **hooks.json 更新**: 既存 Stop hook の `verify_session.py` と並列に `record_assistant_usage.py` を Stop matcher で登録 (順序は任意。Stop hook 群は独立)
- **commit**: `feat(hooks): record_assistant_usage at Stop hook (Issue #99)`

### Step 4 — `aggregate_session_breakdown` in server.py (TDD, commit 4, sub-issue A)

- **test 先行** (`tests/test_dashboard_sessions_api.py::TestSessionBreakdown`):
  - `test_session_breakdown_field_present`: `/api/data` レスポンスに `session_breakdown: list` が含まれる
  - `test_per_session_shape`: 各要素が `{session_id, project, started_at, ended_at|null, duration_seconds|null, models: {model_name: message_count}, tokens: {input,output,cache_read,cache_creation}, estimated_cost_usd: float, service_tier_breakdown: {tier_name: message_count}, skill_count: int, subagent_count: int}` を満たす
  - `test_sort_by_started_at_desc`: 最新 session が先頭
  - `test_top_n_cap`: cap=20 (定数 `TOP_N_SESSIONS = 20`、本 issue 内で `dashboard/server.py` に定数化)
  - `test_active_session_has_null_end`: `session_end` が無い session でも構造的に問題なく処理され、`ended_at = null` / `duration_seconds = null` で出る
  - `test_unknown_model_attributed_to_sonnet_fallback`: assistant_usage.model に未知 model が混在しても cost 計算が成立 (Step 2 と整合)
  - `test_empty_events_returns_empty_list`: 空入力で `[]`
- **`build_dashboard_data` への組込み**:
  - `session_breakdown = aggregate_session_breakdown(events, now=now, top_n=TOP_N_SESSIONS)`
  - 既存の `period` toggle (Issue #85) との関係: **本 field は period 適用 scope に入れる** (Overview/Patterns 系と同じ「最近の session を見る」性質。Quality/Surface のような lifetime metric ではない)。`build_dashboard_data` の period split に追加 (period_events_raw / period_events_usage どちらに乗せるかは §5(c) で議論)
- **drift guard test**:
  - `test_session_subagent_count_matches_metrics`: 各 session の `subagent_count` が `aggregate_subagent_metrics` に invocation 単位で集計したものと一致 (CLAUDE.md `docs/reference/subagent-invocation-pairing.md` の dedup 規律と整合)
- **commit**: `feat(dashboard): aggregate_session_breakdown in /api/data (Issue #99)`

### Step 5 — docs (commit 5, sub-issue A 完結)

- `docs/spec/dashboard-api.md`: `session_breakdown` 節を追加。Step 4 の test expectations を spec として明記
- `docs/reference/cost-calculation-design.md`: 末尾に "本 plan で採用した版 (v0.8.0)" 1 セクション追記 (採用した §9 / 採用しなかった §7 最頻モデル方式 / 価格表 pin の出典 URL)
- `CHANGELOG` (もし存在すれば — 本 repo には現状無いっぽいので skip)
- **commit**: `docs(spec): document session_breakdown field (Issue #99)`
- **PR 作成**: sub-issue A の PR を `v0.8.0` 宛に立てる。ここで一旦区切り。

---

### Step 6 — sub-issue B 着手 (branch 切替)

```bash
git checkout v0.8.0 && git pull
git checkout -b feature/103-sessions-page
```

### Step 7 — Sessions page DOM + nav 5 タブ化 (TDD, commit 6, sub-issue B)

- **test 先行** (`tests/test_dashboard_sessions_ui.py::TestSessionsPageTemplate`):
  - assembled template に `<a href="#/sessions">Sessions</a>` 相当が nav に存在
  - `<section data-page="sessions">` が 5 番目に存在
  - `45_renderers_sessions.js` が `_MAIN_JS_FILES` 内で `40_renderers_quality.js` の後 / `50_renderers_surface.js` の前に concat される
- **既存 router test の更新**:
  - `tests/test_dashboard_router.py::TestRouterShellStructure` の **「nav に 4 タブ」を pin している部分** を 5 タブに更新 (drift guard を壊さないよう test 名は維持して件数だけ変更)
- **実装**:
  - `shell.html` の `<nav class="page-nav">` に 5 番目のリンク追加。**period toggle (Issue #85) の隣に配置するか、4 タブ + sessions の後ろに置くか** は §5(g) で議論 → 結論: **4 タブの後**、period toggle の前に sessions リンクを追加
  - `00_router.js` の `HASH_TO_PAGE` に `'#/sessions': 'sessions'` を追加 (lexicographic 順は崩さない)
  - 新 `<section data-page="sessions" hidden>` に空の table skeleton (`<table id="sessionsTable">` + caption)
- **commit**: `feat(dashboard): add Sessions page DOM scaffold (Issue #103)`

### Step 8 — Sessions renderer (TDD, commit 7, sub-issue B)

- **test 先行** (Node round-trip pattern。既存 `tests/test_dashboard_local_tz.py` 踏襲):
  - `test_renders_session_rows`: data fixture (session 3 件) を `window.__DATA__` に注入 → renderer 実行 → `#sessionsTable tbody tr` が 3 行
  - `test_cost_format`: cost USD value が `$X.XXXX` 形式で表示 (4 桁) — 注釈付き ("参考値" tooltip)
  - `test_models_chip_format`: 1 session に 2 model の場合 `claude-opus (3) / claude-sonnet (1)` 風の表記
  - `test_service_tier_chip_format`: `service_tier_breakdown` が priority/standard 等の比率 chip で表示される
  - `test_active_session_pill`: `ended_at = null` の session は "進行中" pill 表示
  - `test_page_scoped_no_op_when_other_page`: `body[data-active-page="overview"]` のとき renderer は early-out (DOM 触らない) — 既存慣習踏襲
- **実装**:
  - `45_renderers_sessions.js` IIFE で `renderSessions(data)` を定義し、`window.__sessions = { renderSessions }` を expose (period closure 慣習踏襲、Issue #85 の `window.__period` と同パターン)
  - `20_load_and_render.js` の最後で `window.__sessions?.renderSessions?.(data)` を呼ぶ (call-time lookup、period の lazy lookup と同じ規律)
  - `55_sessions.css`: テーブル / pill / cost notation の styling
  - **「参考値」注釈**: cost 列 header に `<span class="help-pop" data-help-id="cost-disclaimer">` を置き、`80_help_popup.js` の既存 mechanism で「価格改定で過去値も動きます」notation を表示 (CLAUDE.md `cost-calculation-design.md` §4 の trade-off と整合)
- **commit**: `feat(dashboard): render Sessions table with cost annotation (Issue #103)`

### Step 9 — Sessions ページ docs + EXPECTED_TEMPLATE_SHA256 (commit 8, sub-issue B 完結)

- `docs/spec/dashboard-runtime.md`: 「ダッシュボード複数ページ構成」表を 4 → 5 ページに更新。Sessions ページの `body[data-active-page="sessions"]` と page-scoped early-out 慣習を明示
- `tests/test_dashboard_template_split.py::EXPECTED_TEMPLATE_SHA256` を bump (Issue #85 の Step 9 計算手順と同じ)
- **commit**: `docs(spec): document Sessions page + bump template hash (Issue #103)`
- **PR**: sub-issue B の PR を `v0.8.0` 宛に立てる。

---

### Step 10 — sub-issue C 着手 (branch 切替)

```bash
git checkout v0.8.0 && git pull
git checkout -b feature/104-rescan-cost
```

### Step 11 — `scripts/rescan_transcripts.py` で assistant_usage backfill (TDD, commit 9, sub-issue C)

- **test 先行** (`tests/test_rescan_assistant_usage.py`):
  - 過去 transcript fixture を読み、`assistant_usage` event が backfill される
  - **再実行 idempotent**: 2 回目は events が増えない (`(session_id, message_id)` dedup)
  - 既存 `assistant_usage` (= live hook 経由で記録済み) と rescan 経路の event が重複しない
- **実装**: `record_assistant_usage.py` の主要ロジックを **module-level 関数として export** し、`rescan_transcripts.py` から再利用 (DRY)。Step 3 で `cost_metrics.py` に置くべきか `record_assistant_usage.py` に置くべきか迷いポイント → §5(b) で議論
- **commit**: `feat: backfill assistant_usage in rescan_transcripts (Issue #104)`

### Step 12 — `reports/summary.py --include-cost` (TDD, commit 10, sub-issue C)

- **test 先行**: terminal 出力に `Total estimated cost: $X.XXXX` 行が出る + session top 10 cost が出る
- **実装**: `cost_metrics.aggregate_session_breakdown(events)` を呼んで terminal format で出力
- **commit**: `feat(reports): summary --include-cost (Issue #104)`

### Step 13 — `reports/export_html.py` への session breakdown 反映 (commit 11, sub-issue C)

- 既存 `render_static_html(build_dashboard_data(events))` 経路は **A の段階で自動的に session_breakdown を含む** ので追加 work は static HTML での render 確認のみ
- **test**: static HTML round-trip で session table が render されること (既存 export_html test 拡張)
- **commit**: `feat(reports): include sessions in export_html (Issue #104)`

### Step 14 — sub-issue C 完結 PR

- PR を `v0.8.0` 宛に。3 sub-issue 全部 merge 後、`v0.8.0` → `main` の release PR を立てる (`patch-release` skill 参照)。

## 4. TDD test plan — クラス一覧

### sub-issue A
- `tests/test_record_assistant_usage.py`: `TestStopHookEmitsAssistantUsage` / `TestDedupByMessageId` / `TestModelSwitchInSession` / `TestMissingMessageIdSkipped` / `TestNaiveTimestampHandled`
- `tests/test_cost_metrics.py`: `TestCalculateMessageCost` / `TestUnknownModelFallback` / `TestAggregateSessionBreakdown` / `TestModelMixSessionCostInvariant` / `TestEmptyTokensReturnsZero`
- `tests/test_dashboard_sessions_api.py`: `TestSessionBreakdown` (上記 7 件)
- 既存への小規模追加: `tests/test_dashboard.py::TestBuildDashboardData::test_session_breakdown_in_response`

### sub-issue B
- `tests/test_dashboard_sessions_ui.py`: `TestSessionsPageTemplate` (3 件) / `TestSessionsRenderer` (5 件 Node round-trip)
- 既存への小規模更新: `tests/test_dashboard_router.py::TestRouterShellStructure::test_nav_has_five_tabs` / `tests/test_dashboard_template_split.py::EXPECTED_TEMPLATE_SHA256` bump

### sub-issue C
- `tests/test_rescan_assistant_usage.py`: `TestRescanBackfill` / `TestRescanIdempotent` (3 件)
- 既存への小規模追加: `tests/test_summary.py::test_summary_include_cost` / `tests/test_export_html.py::test_export_html_includes_sessions`

## 5. Risks / tradeoffs

### (a) ⚠️ 価格表の出典 pin (CRITICAL)

`docs/reference/cost-calculation-design.md` §2 に書かれた価格数値は、外部 repo からの **孫引き未検証**:

```
"claude-opus-4-6":   { input: 5, output: 25, cache_read: 0.5, cache_creation: 6.25 },
"claude-sonnet-4-6": { input: 3, output: 15, cache_read: 0.3, cache_creation: 3.75 },
"claude-haiku-4-5":  { input: 1, output: 5,  cache_read: 0.1, cache_creation: 1.25 },
```

**Step 2 の commit 直前に必ず https://www.anthropic.com/pricing で実値を pin する**。pin した URL + 取得日時 + Claude 4.6 / 4.7 系の最新 model コード (`claude-opus-4-7` / `claude-sonnet-4-6` / `claude-haiku-4-5-20251001` 等) を `cost_metrics.py` の docstring に明記。

CLAUDE.md "Technical identifiers (CVE, RFC, version, SHA, etc.)" ルールに従い、**この値は LLM 確認の余地を残さず、人間が公式 pricing ページを目視で pin** すること。pin できない場合は「価格表は人手 update 必須」を docstring に flag し、Step 2 commit を **値が手当てされるまで保留** する。

### (b) `record_assistant_usage.py` ↔ `cost_metrics.py` のロジック分担

**plan 推奨**: `record_assistant_usage.py` は **transcript → assistant_usage event の変換のみ** を担当 (= 永続化のための shape 変換)。`cost_metrics.py` は **events → cost USD 計算** のみを担当 (= 純関数)。両者は API として疎結合。

| 観点 | record_assistant_usage 単独 | cost_metrics 単独 | 混在 |
|---|---|---|---|
| 単一責任 | ✓ shape 変換専門 | ✓ 計算専門 | ✗ |
| test しやすさ | hook 入出力 fixture | dict in/out 純関数 | 混在 fixture 必要 |
| rescan 再利用 (sub-issue C) | 関数 export で OK | events 配列を渡すだけ | 経路混在 |

採用: 両者分離。Step 11 (rescan) で record 側のロジック関数化 + import が clean。

### (c) period toggle (Issue #85) との関係

- 本 plan の `session_breakdown` は **period 適用 field 群に追加** (= `period_events_raw` / `period_events_usage` どちらかから集計)
- 検討:
  - `period_events_raw` 経由: `assistant_usage` event は `_filter_usage_events` の対象外 (= raw 側で生き残る) なので、`period_events_raw` から集計するのが正解
  - `period_events_usage` 経由: subagent invocation dedup を被る (= `subagent_count` の精度向上) が、`assistant_usage` 自体は dedup 対象外
- **結論**: `period_events_raw` から `assistant_usage` を抽出 / `period_events_usage` から `subagent_count` を取る、の両経路を session 単位 join。Step 4 でこの構造を実装し、drift guard test (`test_session_breakdown_period_split`) で固定
- **Issue #85 の §1 表に session_breakdown 行を additive で追記する** 必要あり。本 plan merge 時に Issue #85 spec も追従更新

### (d) `usage.jsonl` の dedup スキャンコスト

- `record_assistant_usage.py` は hook 発火時に既存 jsonl を scan して `(session_id, message_id)` set を作る
- 180 日 hot tier で 数万〜十数万行想定。1 hook 発火 ≈ 1 ファイル full scan は **Stop hook の 100ms 以内** に収まるか要計測 (small assistant message なら問題ない見込み)
- **scope 外**: index 化 (`(session_id, message_id) → offset` の sidecar file) は **将来 issue**。現状の I/O ボリュームでは過剰最適化
- 計測手順: Step 3 commit 直前に `time python3 hooks/record_assistant_usage.py < fixture` を実機 jsonl で 3 回計測。p99 > 200ms なら index 化を Step 3 内で前倒し

### (e) "Reference value" 明示の徹底

`cost-calculation-design.md` §4 の trade-off (価格改定で過去値が動く / 監査用途には使えない):

- ダッシュボード Sessions ページの cost 列 header に help-pop で注釈 (Step 8)
- `reports/summary.py --include-cost` 出力末尾に注釈行 (Step 12)
- `cost_metrics.py` module docstring の 1 段落で明示
- `docs/spec/dashboard-api.md` の `session_breakdown.estimated_cost_usd` 説明にも明示

ここで手を抜くと「先月のコスト 違ってない？」フィードバックが来たときに釈明コストが大きい。

### (f) skill / subagent 件数の出処

- skill 件数: session 内の `skill_tool` + `user_slash_command` event 数 (`/exit /clear ...` などの 組み込みコマンド除外は既存 filter 慣習を踏襲)
- subagent 件数: `subagent_metrics.aggregate_subagent_metrics` を session フィルタしてから `len()` ではなく、**invocation 単位 dedup 後の count** が必要 → Step 4 で `subagent_metrics.session_subagent_counts(events) -> dict[session_id, count]` を additive 追加

### (g) Sessions ページのソート / 表示件数

- **plan 採用**: started_at 降順 (= 最近 session が上)、`TOP_N_SESSIONS = 20`
- 検討:
  - cost 降順? → 「コストの高い session を上から見たい」需要に応える代替 sort も将来追加可能。本 issue では started_at 降順のみで ship、cost sort は §6 out-of-scope
  - `TOP_N_SESSIONS` を 50 / 100 にすると payload が大きくなり SSE 帯域に効くため、20 を初期値で ship
- 「全 session を見たい」需要は §6 (将来 issue) で「pagination + 検索」として議論

### (h) drill-down (per-message timeline) を本 issue で出さない

- `cost-calculation-design.md` §9 で明記したように **per-message** で生 event を持つので、将来 timeline drill-down は実装可能
- **本 issue では出さない理由**: scope 肥大化リスク。Sessions table 1 つで価値の最低ラインに到達できるため、本 issue は table only。drill-down は §6 / 別 issue

## 6. Out of scope (現状 disposition)

| 項目 | 現状 disposition | 将来検討 |
|---|---|---|
| Per-message timeline drill-down (model 切替を可視化) | **無し**。Sessions ページは 1 session = 1 row の table のみ。各 row の `models` 列で「どの model が何 message」までは見える | フィードバック次第で別 issue |
| cost 降順 sort (高コスト session を上に) | **無し**。started_at 降順固定 | 将来 issue で sort dropdown 検討 |
| Sessions ページの pagination / 検索 | **無し**。`TOP_N_SESSIONS = 20` で cap、それ以上は表示しない | session 数が増えてからフィードバックで検討 |
| Audit-grade コスト snapshot (時点固定値) | **無し**。価格改定で過去値も動く参考値仕様。help-pop / docstring で明示 | 監査要件があれば別 table 起票 |
| Archive (`archive/*.jsonl.gz`) opt-in での全期間コスト | **無し**。dashboard は hot tier 180d のみ | Issue #30 followup と一緒に検討 |
| `verify_session.py` での assistant_usage 照合 (transcript ↔ usage) | **無し**。本 issue は収集のみ。drop alert は出さない | 異常 (transcript にあるが usage に無い) が観測されれば追加 |
| 月次コスト trend グラフ | **無し**。Sessions table のみ | 将来 issue で daily/weekly trend 別 panel 検討 |
| `reports/export_html.py` の static HTML での period 選択 | **無し**。export_html は常に全期間 (Issue #85 と整合) | 同上 |
| Cost-aware alert (月予算超過で notification) | **無し** | 価格 pin が安定運用に乗ってから検討 |
| skill / subagent 別の cost 帰属 (どの skill で幾らかかった) | **無し**。session 単位の合計のみ | drill-down と一緒に別 issue |
| Plan 経由の `commands/` 文書化 (新 slash command 追加) | **無し**。Sessions ページは hash route のみで、slash command は設けない | Sessions ページのスタンドアロン起動需要があれば検討 |

## 7. Open questions (実装前に決定が必要)

1. **価格表の pin 主体**: Step 2 commit 直前に **誰** が公式 pricing ページを目視確認するか? 自動 fetch は CLAUDE.md "Technical identifiers" ルールで confabulation 高リスクなので **人手 pin 推奨**
2. **session の "active" 判定**: `session_end` 不在で active 扱いは妥当か? `session_start` から 24 時間経過などの timeout を設けるか? → plan 推奨: session_end 無し = active のまま (=「進行中」表示)。timeout は将来 issue
3. **未知 model の logging**: Sonnet fallback したときに warn / drop alert を出すか? → plan 推奨: silent fallback (UI / log を毒さない)。新 model 登場の検出は別経路 (`docs/spec/usage-jsonl-events.md` の event 観測) で
4. **`/api/data` の payload size**: Top 20 sessions × 各 token / model 内訳で SSE 帯域への影響は? → 1 session あたり ~500 bytes 想定 × 20 = 10KB 増。許容範囲
5. **sub-issue 分割の粒度**: 起票済み (Issue #99 / #103 / #104 の 3 分割で確定、すべて v0.8.0 マイルストーン)

これらは Step 1 着手前に Issue #99 で user 回答を取り付ける。

## 8. 関連 spec / reference

- 移植の前提研究: `docs/reference/cost-calculation-design.md` (§9 を本 plan で実装、Issue #99 で per-subagent transcript 経路追記)
- 新 event 追加先: `docs/spec/usage-jsonl-events.md` (Step 1 で `assistant_usage` 追記、`service_tier` / `inference_geo` / `source` 含む)
- API 拡張: `docs/spec/dashboard-api.md` (Step 5 で `session_breakdown` 追記、`service_tier_breakdown` 含む)
- 5th page 追加: `docs/spec/dashboard-runtime.md` (Step 9 で複数ページ構成表更新)
- subagent 集計の dedup 規律: `docs/reference/subagent-invocation-pairing.md` (Step 4 の drift guard で踏襲)
- period toggle interaction: `docs/plans/85-period-toggle.md` §1 の field 表に session_breakdown 行を additive で追記する後追い作業が本 plan merge 時に発生
- 前提 issue: Issue #93 (`subagent_type == ""` filter rule、agent_id dedup) — per-subagent transcript の対象絞り込みで依拠
- tracking issues: Issue #99 (infra) / Issue #103 (UI) / Issue #104 (rescan + reports)
