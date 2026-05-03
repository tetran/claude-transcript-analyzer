# Issue #85 Implementation Plan — Dashboard Period Toggle

> Issue: https://github.com/tetran/claude-transcript-analyzer/issues/85
> Milestone: v0.7.3
> Base branch: `v0.7.3` (main から派生して remote に push する手順を Step 0 に含める)
> Feature branch: `feature/85-period-toggle`

## 1. Goal

ダッシュボードの **Overview / Patterns** ページに `7d / 30d / 90d / 全期間` の期間トグルを導入し、選択値を `/api/data?period=<v>` でサーバーに渡して **server-side で events を切ってから** 該当する **11 個** の response field（Overview/Patterns aggregator + KPI counter）を再計算する。Quality / Surface ページの **8 個** の field と static export は **常に全期間** を維持し、永続化 / URL hash 同期は行わない（reload で全期間に戻る）。

### `/api/data` 全 field の period 適用分類 (reviewer iter2 #1, #2 — 厳密 enumeration)

| Field | Group | period 適用 | 算出元 |
|---|---|---|---|
| `last_updated` | response metadata | n/a (常に server clock) | `_now_iso()` (※ Step 2 で `now=` 受け取り時のみ override 可) |
| `total_events` | KPI counter | **〇** | `len(period_events_usage)` |
| `skill_ranking` | Overview | **〇** | `aggregate_skills(period_events_raw)` |
| `subagent_ranking` | Overview | **〇** | `aggregate_subagents(period_events_raw)` [^stoppair] |
| `skill_kinds_total` | KPI counter | **〇** | `period_events_raw` 起点で skill 名 unique 集合 |
| `subagent_kinds_total` | KPI counter | **〇** | `len(aggregate_subagent_metrics(period_events_raw))` |
| `project_total` | KPI counter | **〇** | `period_events_usage` から project unique 集合 |
| `daily_trend` | Overview | **〇** | `aggregate_daily(period_events_usage)` |
| `project_breakdown` | Overview | **〇** | `aggregate_projects(period_events_usage)` |
| `hourly_heatmap` | Patterns | **〇** | `aggregate_hourly_heatmap(period_events_usage)` |
| `skill_cooccurrence` | Patterns | **〇** | `aggregate_skill_cooccurrence(period_events_raw)` |
| `project_skill_matrix` | Patterns | **〇** | `aggregate_project_skill_matrix(period_events_raw)` |
| `subagent_failure_trend` | Quality | × | `aggregate_subagent_failure_trend(events)` 全期間 |
| `permission_prompt_skill_breakdown` | Quality | × | `aggregate_permission_breakdowns(events)["skill"]` 全期間 |
| `permission_prompt_subagent_breakdown` | Quality | × | `aggregate_permission_breakdowns(events)["subagent"]` 全期間 |
| `compact_density` | Quality | × | `aggregate_compact_density(events)` 全期間 |
| `session_stats` | housekeeping | × (lifetime metric) | `aggregate_session_stats(events)` 全期間 |
| `health_alerts` | housekeeping | n/a (`load_health_alerts()` の独立 log 読み出し) | filtering 対象外 |
| `skill_invocation_breakdown` | Surface | × | `aggregate_skill_invocation_breakdown(events)` 全期間 |
| `skill_lifecycle` | Surface | × | `aggregate_skill_lifecycle(events, now=now)` |
| `skill_hibernating` | Surface | × | `aggregate_skill_hibernating(events, now=now)` |
| `period_applied` (新規) | response metadata | n/a (echo) | server で正規化した period 文字列 |

集計:
- **period 適用 (11 field)**: KPI counter 4 (`total_events` / `skill_kinds_total` / `subagent_kinds_total` / `project_total`) + Overview aggregator 4 (`skill_ranking` / `subagent_ranking` / `daily_trend` / `project_breakdown`) + Patterns aggregator 3 (`hourly_heatmap` / `skill_cooccurrence` / `project_skill_matrix`)
- **全期間 (8 field)**: Quality 4 (`subagent_failure_trend` / `permission_prompt_skill_breakdown` / `permission_prompt_subagent_breakdown` / `compact_density`) + Surface 3 (`skill_invocation_breakdown` / `skill_lifecycle` / `skill_hibernating`) + `session_stats`
- **filtering 対象外** (3 field): `last_updated` / `health_alerts` / `period_applied`

[^stoppair]: `subagent_ranking[i].failure_rate` / `avg_duration_ms` / `pXX_duration_ms` は `aggregate_subagent_metrics` 内部で `subagent_start ↔ subagent_stop` を pair して計算する。period boundary 跨ぎの pair が silent drift しないように、Step 1 §pair-straddling 第三段で stop 側も再 include する（reviewer iter4 #1）。

## 2. Critical files

### New

| Path | 役割 |
|---|---|
| `dashboard/template/scripts/05_period.js` | `currentPeriod` closure + `getCurrentPeriod()` / `setCurrentPeriod()` accessor + `wirePeriodToggle()`。`10_helpers.js` より前に concat する（toggle change → `scheduleLoadAndRender()` の dependency 順）。production code は **1 つの shared IIFE 内** に閉じる（`25_live_diff.js` の慣習踏襲: `__period` prefix で名前空間隔離） |
| `tests/test_dashboard_period_toggle.py` | server-side filter / API query / fallback / drift guard / template structure smoke を 1 ファイルに集約 |

### Changed

| Path | 変更内容 |
|---|---|
| `dashboard/server.py` | (a) `_filter_events_by_period(events, period)` helper を新設（usage event timestamp で `now - delta` 以降を保持、**Quality/Surface aggregator が必要とする全 event_type を保持** = filter 対象は `timestamp` のみ）。(b) `build_dashboard_data(events, period="all")` シグネチャ拡張: 渡す前に `period_events = _filter_events_by_period(events, period)` で切り、Overview/Patterns 系 9 aggregator には `period_events` を、Quality/Surface 系 6 aggregator には **未 filter の全 events** を渡す。(c) レスポンスに `period_applied: str` を additive で追加。(d) `_serve_api()` で `urllib.parse.urlparse` + `parse_qs` 経由で `period` 取得、不正値は `"all"` に fallback。(e) `_MAIN_JS_FILES` に `05_period.js` を `10_helpers.js` の前に追加 |
| `dashboard/template/shell.html` | `<nav class="page-nav">` 内 (4 タブの後 / heartbeat の前) に `<div class="period-toggle" id="periodToggle">` を追加。**ボタン 4 つ** (`data-period="7d"` / `30d` / `90d` / `all`)、`aria-pressed` で active 表現、`role="group" aria-label="集計期間"`。**Overview / Patterns 表示時のみ可視化** は CSS 側で `body[data-active-page="quality"] #periodToggle, body[data-active-page="surface"] #periodToggle { display: none; }` で実現（router の `body.dataset.activePage` 既存契約を利用） |
| `dashboard/template/styles/00_base.css` (or 30_pages.css) | `.period-toggle` の見た目 + page-scoped 非表示ルール |
| `dashboard/template/scripts/20_load_and_render.js` | `fetch('/api/data', ...)` を `fetch('/api/data?period=' + getCurrentPeriod(), ...)` に変更。response の `period_applied` を読んで Overview の `dailySub` / `projSub` / `skillSub` / Patterns の各 sub に「7d 集計」など badge 表示（`period === 'all'` ならば badge 出さない = 現状互換） |
| `dashboard/template/scripts/70_init_eventsource.js` | SSE `message` 受信時の `scheduleLoadAndRender()` は **現行のまま**（query は `getCurrentPeriod()` 経由で fetch 時に毎回読まれるので race なし） |
| `docs/spec/dashboard-api.md` | `/api/data` query param `period` + response `period_applied` の節を追加。「period 適用 scope」を 9 field 列挙で明記 |
| `tests/test_dashboard_router.py` (optional) | `#periodToggle` の存在 + page-scoped 非表示 CSS の structural pin |

> 触らないファイル: `reports/export_html.py` / `scripts/build_surface_fixture.py` (out-of-scope = 全期間固定)、`subagent_metrics.py` / Quality/Surface aggregator 群。

## 3. Ordered steps (TDD pace, commit 単位込み)

> **前段**: `git checkout main && git pull && git checkout -b v0.7.3 && git push -u origin v0.7.3 && git checkout -b feature/85-period-toggle`。base-branch は `v0.7.3`。

### Step 0 — release branch を切って push (commit 0)
- branch 作業のみ。以降の feature commit は `feature/85-period-toggle` に積む。

### Step 1 — server-side period filter helper (TDD, commit 1)
- **test 先行** (`tests/test_dashboard_period_toggle.py::TestFilterEventsByPeriod`):
  - `_filter_events_by_period(events, "7d", now=fixed_now)` が 8 日前 event を drop / 6 日前 event を保持
  - `"30d"` / `"90d"` の境界 (両端 inclusive な **rolling window** 仕様)
  - `"all"` で events が **そのまま** 返る（identity 比較ではなく要素同値）
  - timestamp parse 不能 / 欠損 event は `"all"` 以外で **silent drop**
  - naive timestamp は UTC とみなす（既存 `_parse_iso_utc` 慣習）
  - **pair-straddling inclusive policy** (reviewer iter3 #1): `subagent_start` が `now - 7d - 0.4s` (= cutoff 外) で paired `subagent_lifecycle_start` が `now - 7d + 0.4s` (= cutoff 内、`INVOCATION_MERGE_WINDOW_SECONDS = 1.0` 内) のとき、**両方とも保持** されることを assert（pair の片割れだけが落ちて invocation 集計が壊れる事を防ぐ）
- **実装**: `dashboard/server.py` に `_PERIOD_DELTAS = {"7d": 7, "30d": 30, "90d": 90}` + helper（`now: datetime | None = None` キーワード引数で test 注入可能、デフォルト `datetime.now(timezone.utc)`）。
  - **pair-straddling 二段 filter** (reviewer iter3 #1, iter4 #1): 
    1. 第一段: timestamp で `cutoff <= ts <= now` の rolling window cut
    2. 第二段 (start↔lifecycle pair): 第一段で残った `event_type ∈ {subagent_start, subagent_lifecycle_start}` を起点に、**同じ `(session_id, subagent_type)` バケット内** で `INVOCATION_MERGE_WINDOW_SECONDS = 1.0` 秒以内に発火した sibling 候補（第一段で落とされたもの）を **再 include**
    3. 第三段 (start↔stop pair, iter4 #1): 第一段で残った `subagent_start` を起点に、**同じ `(session_id, subagent_type)` バケット内** で `start.ts < stop.ts` を満たす **直後の `subagent_stop`**（第一段で cutoff より過去に落ちたもの）を **再 include**。逆向きに、第一段で残った `subagent_stop` を起点に、その直前の paired `subagent_start` を再 include する経路も対称に実装。これは `aggregate_subagent_metrics:_pair_invocations_with_stops` の "start_ts ≤ stop_ts < next_start_ts" pairing semantics を尊重するための補完で、`failure_rate` / `avg_duration_ms` / pXX duration が period boundary 跨ぎで silent drift しないことを保証
  - inclusive policy 採用理由: ユーザ直感 (「6.9 日前に走った invocation が 7d toggle で消える」のは奇異) + reviewer iter3/iter4 推奨。strict policy (drop unpaired halves) は実装は単純だが silent undercount を招く
  - **副作用**: stop 再 include により period_events_raw のサンプル数が「stage 1 のみ + stage 2 のみ」より僅かに増える。`aggregate_subagents` 内の `failure_count` / `avg_duration_ms` が pair 整合 representation に近づく方向の正の bias なので user 期待と整合
  - **第三段の test 追加**: Step 1 test list に `test_filter_period_includes_subagent_stop_paired_with_kept_start` (start@now-7d+0.4s 内側 + stop@now-7d-0.5s 外側 → 両方保持) と `test_filter_period_includes_subagent_start_paired_with_kept_stop` (対称ケース) を追加
  - **同バケット並列 invocation の boundary test** (reviewer iter5 #1): 同 `(session_id, subagent_type)` バケットに連続 2 invocation が並ぶケースを 1 件追加:
    ```
    start_A @ now-7d-2.0s   (cutoff 外, 第一段で drop)
    stop_A  @ now-7d-1.5s   (cutoff 外, 第一段で drop)
    start_B @ now-7d+0.3s   (cutoff 内, 保持)
    stop_B  @ now-7d+0.8s   (cutoff 内, 保持)
    ```
    test 名: `test_filter_period_does_not_pull_unrelated_stop_from_prior_invocation`。期待: `period_events_raw` に `stop_A` が含まれない (= start_B の paired stop は stop_B で確定し、stop_A まで遡らない)
  - **第三段の skip logic** (iter5 #1, iter6 #3): docstring 1 行で「逆経路 (kept stop → 直前 paired start) では `stop.ts ≥ start.ts` かつ **間に他の start が挟まらない** 直近の start のみ拾う。順経路 (kept start → 直後 paired stop) でも `start.ts ≤ stop.ts` かつ間に他の start が挟まらない直近の stop のみ。これは `_pair_invocations_with_stops` の `start_ts ≤ stop_ts < next_start_ts` semantics を尊重」と明記
  - **canonical source への cross-reference** (iter6 #3): 第三段 docstring + `subagent_metrics._pair_invocations_with_stops` 両方に keep-in-sync コメントを設置:
    - `dashboard/server.py:_filter_events_by_period` 第三段 docstring に: `# Mirrors subagent_metrics._pair_invocations_with_stops pairing semantics. Keep in sync.`
    - `subagent_metrics.py:_pair_invocations_with_stops` docstring 末尾に: `# Note: dashboard/server.py:_filter_events_by_period 第三段 mirrors this pairing rule. Keep in sync.`
    - これで grep "Keep in sync" で双方向に発見可能。pairing 仕様変更時の silent drift を防ぐ
- **scope contract** (重要): docstring 1 行目で **「Overview / Patterns aggregator にのみ渡す view を返す。Quality / Surface aggregator は unfiltered events を受ける」** を明記。ヘルパー名 `_filter_events_by_period` は汎用に見えるが、誤用防止のためこの scope 制約を docstring と build_dashboard_data の call site comment 両方に冗長で書く（reviewer iter1 #3）。advisory として **rename 候補** = `_filter_events_by_period_for_overview_patterns` (verbose だが誤用防止) を Section 5 に load (reviewer iter3 advisory #4)
- **rolling vs calendar window**: `now - timedelta(days=N)` の **rolling** を採用。既存コードベースの慣習 (`aggregate_skill_lifecycle:cutoff_30d = now - timedelta(days=30)` / `aggregate_skill_hibernating:active_cutoff = now - timedelta(days=14)`) と一貫。calendar window (`local-TZ midnight - (N-1) days`) は dashboard frontend の local-TZ 表示と若干アライメントが取れる利点があるが、本 issue scope 外（Section 5(g) で out-of-scope 化）。
- **commit**: `feat(dashboard): add _filter_events_by_period helper (Issue #85)`

### Step 2 — build_dashboard_data(period=...) 拡張 (TDD, commit 2)
- **test 先行** (`TestBuildDashboardDataWithPeriod`):
  - `build_dashboard_data(events, period="7d", now=fixed_now)` で 8 日前の `skill_tool` が **period 適用 11 field 全て** (`total_events` / `skill_ranking` / `subagent_ranking` / `skill_kinds_total` / `subagent_kinds_total` / `project_total` / `daily_trend` / `project_breakdown` / `hourly_heatmap` / `skill_cooccurrence` / `project_skill_matrix`) から消える
  - **同入力で全期間 8 field** (`subagent_failure_trend` / `permission_prompt_skill_breakdown` / `permission_prompt_subagent_breakdown` / `compact_density` / `session_stats` / `skill_invocation_breakdown` / `skill_lifecycle` / `skill_hibernating`) は **period に関わらず全期間で算出される** drift guard → `period="7d"` と `period="all"` の出力 8 field 部分が equal であることを assert
  - response に `period_applied` キーが乗る
  - `period="all"` の return は **既存 `build_dashboard_data(events)` (引数省略) と equal**（drift guard。`last_updated` 除外）
- **wall-clock flake 対策** (reviewer iter1 #1, iter2 #3): 
  - `aggregate_skill_lifecycle` / `aggregate_skill_hibernating` は内部で `datetime.now(timezone.utc)` を default 取得 → drift guard の equality test が day boundary で flake する可能性
  - 対策: `build_dashboard_data` に `now: Optional[datetime] = None` を追加し、内部で `aggregate_skill_lifecycle(..., now=now)` / `aggregate_skill_hibernating(..., now=now)` + `_filter_events_by_period(..., now=now)` に明示伝播
  - **`last_updated` 取り扱い** (iter2 #3): `now=` 引数が given のときは `last_updated = now.isoformat()` で override（test 用途）、`now=None` のときは現状通り `_now_iso()` (= `datetime.now(timezone.utc).isoformat()`)。これにより drift guard test を `last_updated` 込みで等価比較できる（が、test では「過剰互換」を避け `last_updated` を含む dict equality を assert する）
  - 本番 `_serve_api()` では `now=None` (= 現状互換) のまま呼ぶ
- **実装**: `build_dashboard_data` に `period: str = "all"` + `now: Optional[datetime] = None` を追加。
  - **two-flavor period_events split** (reviewer iter3 #3, iter5 advisory #4): 既存 `build_dashboard_data` は `events` (raw) と `usage_events = _filter_usage_events(events)` の **2 経路** で aggregator に渡している (line 884)。period filter を導入後も 2 経路を保つ:
    ```python
    # period_events_raw: timestamp + pair-straddling 三段 filter 適用後の raw events
    # period_events_usage: period_events_raw に _filter_usage_events (subagent invocation dedup) を適用後
    # 注: 三段で再 include した stop event は _filter_usage_events の dedup window (INVOCATION_MERGE_WINDOW_SECONDS = 1.0s)
    #     と同じ window で動くので再脱落しない (TestFilterEventsByPeriod::test_three_stage_filter_survives_filter_usage_events で pin)
    period_events_raw = _filter_events_by_period(events, period, now=now)
    period_events_usage = _filter_usage_events(period_events_raw)
    ```
  - **合成順 invariant test** (iter5 advisory #4): Step 1 test list に `test_three_stage_filter_survives_filter_usage_events` を 1 件追加 — 三段で再 include された stop event が `_filter_usage_events` 通過後も `period_events_usage` に残ることを assert
    - **`period_events_raw` を渡す aggregator / 計算**: `aggregate_skills` / `aggregate_subagents` / `aggregate_skill_cooccurrence` / `aggregate_project_skill_matrix` / `aggregate_subagent_metrics` (`subagent_kinds_total` 用) / inline `skill_kinds_set` 計算
    - **`period_events_usage` を渡す aggregator / 計算**: `aggregate_daily` / `aggregate_projects` / `aggregate_hourly_heatmap` / `len(period_events_usage)` (`total_events`) / inline `project_kinds_set` 計算 (`project_total`)
    - 既存 `_filter_usage_events` の subagent invocation dedup（INVOCATION_MERGE_WINDOW_SECONDS）も `period_events_raw` に対してかかるので、**Step 1 inclusive pair filter** との合成で raw → usage に落とす際にも pair 整合が保たれる
  - 全期間 8 field の aggregator には未 filter の `events` を渡しつつ `now` を伝播 (`aggregate_skill_lifecycle/hibernating` のみ `now` 受け取り、他 6 個は events のみ)
  - `"period_applied": period` を return dict に additive 追加
  - `last_updated` は `now.isoformat() if now is not None else _now_iso()`
  - **§1 表との整合**: §1 の "算出元" 列を本 split に合わせて更新する commit（同一 commit 内 OR Step 8 の docs commit 一緒に）
- **`daily_trend` の包含理由** (reviewer iter1 advisory #5, iter4 advisory #4): frontend は `localDailyFromHourly(hourly_heatmap.buckets)` から daily を再計算する経路に移行済みで `daily_trend` field 自体は backward-compat の deprecated field（`docs/spec/dashboard-api.md` 参照）。それでも period filter 集合に含めるのは「daily-shaped data は全部 period 適用」原則の維持と、誰かが直接 `daily_trend` を読み戻す日が来たときの整合性のため。
  - code comment 義務 (iter4 advisory #4): `aggregate_daily(period_events_usage)` 呼び出し直前に `# Issue #85: daily_trend stays in period-applied set despite frontend-deprecation (Issue #65)` を 1 行で書く（既存 Issue #65 sentinel docstring (`tests/test_dashboard_local_tz.py::TestServerSentinelDocstring`) の隣で grep ヒットさせるため）
  - **sentinel test pin を default 採用** (iter5 advisory #3): comment が rebase / refactor で削除されると検出不可になるリスクを test で塞ぐ。`TestServerSentinelDocstring` に必須で 1 件追加:
    ```python
    def test_issue_85_daily_trend_sentinel(self):
        source = (Path(__file__).parent.parent / "dashboard/server.py").read_text()
        assert "Issue #85: daily_trend stays in period-applied set" in source
    ```
    既存 Issue #65 sentinel pin と並べて配置。option 扱いから default 採用に格上げ
- **commit**: `feat(dashboard): build_dashboard_data accepts period keyword (Issue #85)`

### Step 3 — `/api/data?period=<v>` query param (TDD, commit 3)
- **test 先行** (`TestApiDataPeriodQuery`):
  - `urllib.request.urlopen("/api/data?period=7d")` の JSON で `period_applied == "7d"`
  - `?period=invalid` / `?period=` / 値欠落 → `period_applied == "all"` に fallback
  - `?period=all` 明示でも現状互換（既存 `test_api_data_returns_json_with_correct_structure` が pass）
- **実装**: `_serve_api()` で `from urllib.parse import urlparse, parse_qs` を import し、`urlparse(self.path).query` → `parse_qs` → `period = q.get("period", ["all"])[0]`、`period not in {"7d","30d","90d","all"}` → `"all"`。`build_dashboard_data(events, period=period)` を呼ぶ
- **plan note**: 不正値で 400 を返さない理由は閉じた loop での UX 優先（CLAUDE.md `dashboard-server.md` の lenient 慣習）。decision を docstring に書く
- **parse_qs gotcha pin** (reviewer iter1 question #3): `parse_qs(keep_blank_values=False)` (default) は `?period=` (空値) を dict から **drop** するので `q.get("period", ["all"])[0]` が `"all"` を返す。後続の allow-list check (`period not in {...}`) で empty case を別経路で処理するわけではない。将来 `keep_blank_values=True` に切り替える maintainer が「empty で fallback しない」誤動作を起こさないよう、**allow-list は dict lookup の後に必ず効かせる順序**を docstring に明記。
- **commit**: `feat(dashboard): /api/data accepts period query (Issue #85)`

### Step 4 — frontend toggle UI + closure state (TDD, commit 4)
- **test 先行** (`TestPeriodToggleTemplate` in `tests/test_dashboard_period_toggle.py`):
  - assembled template に `id="periodToggle"` が存在
  - 4 ボタンが `data-period="7d|30d|90d|all"` の順で並ぶ
  - 初期状態 `aria-pressed="true"` は `data-period="all"` のボタン
  - `body[data-active-page="quality"] #periodToggle, body[data-active-page="surface"] #periodToggle { display: none; }` の CSS rule が assembled template に含まれる
  - `05_period.js` が `10_helpers.js` より前に concat されている（`_MAIN_JS_FILES` order pin）
  - **`test_static_export_hides_toggle`** (reviewer iter1 #2): `render_static_html(build_dashboard_data(events))` の出力に対して JSDOM round-trip で `#periodToggle` が `hidden` 属性付き or `display:none` で評価されることを assert（既存 `tests/test_dashboard_local_tz.py` の Node round-trip pattern 踏襲）
  - **`test_get_current_period_exposed_via_window_period`** (reviewer iter1 question #1): `window.__period.getCurrentPeriod()` で初期値 `"all"` が読めることを assert（namespace 公開 contract pin）
- **実装**:
  - `shell.html`: nav 内に toggle DOM を追加
  - `05_period.js`: 既存 `25_live_diff.js` の `window.__liveDiff = { ... }` パターンを踏襲して **`window.__period = { getCurrentPeriod, setCurrentPeriod, wirePeriodToggle }`** を expose。closure-private `__currentPeriod = "all"`。click handler で `aria-pressed` 付け替え + `setCurrentPeriod(p)` + `scheduleLoadAndRender()` 呼び出し
  - **lazy lookup contract** (reviewer iter2 advisory #4 / iter3 #2): `05_period.js` (concat order 05) は `25_live_diff.js` (concat order 25) より早く評価される → IIFE 評価時に `window.__liveDiff` は **未定義**。click handler 内では `window.__liveDiff?.scheduleLoadAndRender?.()` の **property lookup を呼び出し時に毎回行う** 形で書く（IIFE で関数参照を capture しない）。docstring 1 行で「concat order 上位の依存先は call-time lookup する」rule を明記
  - **`_concat_main_js()` helper 切り出し** (reviewer iter5 #2, iter6 #2): 現行 `dashboard/server.py:982` には `_build_html_template()` (full HTML を返す) はあるが **JS bundle 単体を返す helper は存在しない** (verify 済み)。test 側で `_MAIN_JS_FILES` + `_TEMPLATE_DIR` を import して self-concat する fallback もアリだが、再利用性を考えて **本 Step 4 で `_concat_main_js() -> str` を 1 commit 切り出し** する方針:
    - 実装: `_TEMPLATE_DIR / "scripts" / fname` を `_MAIN_JS_FILES` 順に読んで **`"".join(...)`** (separator 無し) で返すだけの薄い helper。`dashboard/server.py:990` の現行 inline form も `"".join(...)` で書かれているので **byte-identical** を保つ (iter6 #2: `"\n".join(...)` を使うと assembled `_HTML_TEMPLATE` の bytes が静かに変わって Step 9 SHA256 bump が refactor + feature を conflate する)
    - **byte-preservation invariant test** (iter6 #2): Step 4a に `test_concat_main_js_preserves_assembled_template_bytes` を 1 件追加 — refactor 前に 1 度 `_build_html_template()` を呼んで captured byte string を保存し、refactor 後に再呼び出しして byte 等価を assert (refactor 後 commit でも実行可能、TDD で「refactor 前後の byte equality」を test にする)。alternative: docstring に `# byte-identical to pre-refactor _HTML_TEMPLATE; do not introduce separators` の 1 行 pin
    - `_build_html_template()` 側もこの helper 経由に refactor (DRY)。docstring に「`_concat_main_js()` is a test seam exposed for `tests/test_dashboard_period_toggle.py`; not a public API」を 1 行追加 (iter6 question #2)
    - test (`test_period_calls_live_diff_via_call_time_lookup`) は `from dashboard.server import _concat_main_js` で import
    - **Step 4 commit を 4a (helper 切り出し refactor) + 4b (period toggle UI 本体) の 2 commit 分割** が clean。リスク低の refactor を先行 commit する利点 + reviewer から見て diff が読みやすい
    - **Step 9 hash bump timing** (iter6 question #1): Step 4a は byte-identical refactor のため SHA256 不変 → Step 9 hash bump は **4b 以降の累積 (Step 4b + 6 + 8) が template bytes を変えた結果として 1 度だけ** 行う
  - **lazy lookup test (behavioral)** (reviewer iter3 #2, iter4 #2, iter5 #2): substring grep だと captured-at-IIFE 形でも pass してしまうので **plain Node + 手書き window/document stub** で behavioral pin する（**JSDOM ではない**。既存 `tests/test_dashboard_local_tz.py` の `subprocess.run([_NODE, "-e", script], ...)` パターン踏襲、外部 lib 不可制約に整合）:
    1. Test 内で Node script を組み立て: **`globalThis.window = {}; globalThis.document = {...}` の形** で stub (iter5 #2 / iter6 #1 訂正: 現状 frontend bundle 内のどの script にも `'use strict'` 宣言は無い verify 済み — つまり `var window = {}` でも現状動く。それでも `globalThis.X = ...` を採用するのは **future-proof な選択** で、誰かが将来 script に `'use strict'` を入れた際にも壊れないため。strict / sloppy 両 mode で portable な形を選好) + `document.querySelectorAll` を mock 配列 (`[{addEventListener: (_, fn) => savedHandler = fn, dataset: {period: "7d"}, setAttribute: () => {}, ...}]`) で stub
    2. 組み立てた assembled JS bundle (`_concat_main_js()`) を `eval` 相当で評価 → `wirePeriodToggle()` 内の handler が `savedHandler` に capture される
    3. その時点で **`window.__liveDiff` 未定義**。`savedHandler()` を直接呼んでも `window.__liveDiff?.scheduleLoadAndRender?.()` が undefined-safe で何も呼ばない (= calls.length === 0)
    4. `window.__liveDiff = { scheduleLoadAndRender: () => calls.push(1) }` を **後から定義** → `savedHandler()` を再 invoke → `calls.length === 1` を assert
    5. これで「call-time lookup が effective」を behavior 面で pin できる。captured-at-IIFE 形の実装だと step 4 で mock が呼ばれず test 落ちる構造
    6. Test 名: `test_period_calls_live_diff_via_call_time_lookup`
  - **static-export 早期 return** (reviewer iter1 #2): `wirePeriodToggle()` の冒頭で `if (typeof window.__DATA__ !== 'undefined') { document.getElementById('periodToggle')?.setAttribute('hidden', ''); return; }`。`render_static_html` が `<script>window.__DATA__ = {...};</script>` を `</head>` 直前に inject する経路で、`05_period.js` が IIFE 評価する時点で `window.__DATA__` 既存 → toggle DOM を隠して click bind を skip。これにより static export には UI 出ない
  - `00_base.css` (or 30_pages.css): `.period-toggle` 見た目 + page-scoped hide CSS rule
  - `dashboard/server.py`: `_MAIN_JS_FILES` に `05_period.js` を `10_helpers.js` の前に追加。`05_` prefix で literal pin
- **commit**: `feat(dashboard): add period toggle UI + currentPeriod closure (Issue #85)`

### Step 5 — fetch 経路で period query を載せる (TDD, commit 5)
- **test 先行** (`TestPeriodAwareFetch`):
  - `20_load_and_render.js` の concat 結果に `'/api/data?period=' + getCurrentPeriod()` (or 等価リテラル) が含まれる
  - SSE `message` 経路 (`70_init_eventsource.js`) は **追加変更なし** で OK（fetch 時に毎回 `getCurrentPeriod()` を読むため race-free）
- **実装**: `20_load_and_render.js` の `fetch('/api/data', ...)` を `fetch('/api/data?period=' + encodeURIComponent(getCurrentPeriod()), ...)` に書き換え
- **commit**: `feat(dashboard): fetch /api/data with current period query (Issue #85)`

### Step 6 — period applied badge 表示 (TDD, commit 6)
- **test 先行** (`TestPeriodAppliedBadge`):
  - `period_applied: "7d"` の data で `dailySub` / `skillSub` 等の sub に "7d 集計" の文字列が露出する Node round-trip test (既存 `test_dashboard_local_tz.py` の round-trip pattern に倣う)
  - `period_applied: "all"` では sub に "7d/30d/90d" 文字列が露出しない
- **実装**: `20_load_and_render.js` の sub 構築箇所で `data.period_applied !== 'all'` のとき badge を prefix（既存 sub テキストを破壊しない additive 連結）
- **commit**: `feat(dashboard): show period_applied badge on Overview/Patterns subs (Issue #85)`

### Step 7 — drift guard (TDD, commit 7)
- **test** (すべて `now=fixed_now` を渡して wall-clock 依存を消す):
  - `TestPeriodDriftGuard::test_period_all_matches_legacy_signature` — `build_dashboard_data(events, now=fixed_now) == build_dashboard_data(events, period="all", now=fixed_now)` (`last_updated` も含む完全 dict equality。Step 2 で `last_updated = now.isoformat()` 化済み)
  - `TestPeriodDriftGuard::test_full_period_fields_unchanged` — events の半分が古いとき、`build_dashboard_data(events, period="7d", now=fixed_now)` と `build_dashboard_data(events, period="all", now=fixed_now)` の **全期間 8 field** (`subagent_failure_trend` / `permission_prompt_skill_breakdown` / `permission_prompt_subagent_breakdown` / `compact_density` / `session_stats` / `skill_invocation_breakdown` / `skill_lifecycle` / `skill_hibernating`) が equal であることを assert。**period 適用 11 field** との差分が period 切り替えで生じることも併せて assert（drift 観測点）
  - `TestStaticExportHasNoPeriodQuery` — `render_static_html(build_dashboard_data(events, now=fixed_now))` の HTML に `?period=` 文字列が含まれない（static export は period unaware の証跡）
- **wall-clock flake 注釈** (reviewer iter1 #1, iter2 #3, iter3 advisory #5): Step 2 で `build_dashboard_data` に `now=` 引数を追加し `last_updated` も override 経路を作った前提で、本 step の test は **同一 `fixed_now` を両呼び出しに渡す**。これで `aggregate_skill_lifecycle` / `aggregate_skill_hibernating` 内の `now or datetime.now(timezone.utc)` パス + `last_updated` が固定化され、day boundary flake が消える。
- **残存 wall-clock 経路** (reviewer iter3 advisory #5): `aggregate_subagent_failure_trend` (`subagent_metrics.py`) など `subagent_metrics` 配下の集計関数には `now=` 引数を伝播していない。が、これらは **全期間 8 field 側** に属するので drift guard では `period="7d"` / `period="all"` 両呼び出しで同一の wall-clock を経由 → 同一週バケットになり equality が崩れない。万一 day-boundary 跨ぎの μs 単位差で flake した場合は `monkeypatch.setattr('subagent_metrics.datetime', frozen_datetime)` で固定化する fallback を test に load する（追加コスト低）。
- **実装**: テスト通過のためコード変更不要のはず。落ちたら Step 2 の filter scope を見直す
- **commit**: `test(dashboard): drift guard for period field scope (Issue #85)`

### Step 8 — docs (commit 8)
- `docs/spec/dashboard-api.md`: 冒頭の query param 説明 + `period_applied` field 仕様 + 「period 適用 scope」の field リスト (Overview 6 + Patterns 3 = 9 個) + **rolling window 仕様** (`now - timedelta(days=N)` 起点) を明記
- `docs/spec/dashboard-runtime.md`: 「ダッシュボード複数ページ構成」の節に period toggle UI 仕様 (Overview / Patterns でのみ表示) を追記。**badge 文字列フォーマット** (`period_applied !== "all"` のとき該当 sub に `<period> 集計` の prefix が付く) を明示 (reviewer iter1 advisory #4 — test と spec の drift detection を有効化するため)
- **commit**: `docs(spec): document /api/data period query (Issue #85)`

### Step 9 — `EXPECTED_TEMPLATE_SHA256` 更新 (commit 9, 必要時)
- `tests/test_dashboard_template_split.py` の expected hash が変わる → 計算 → 更新 + 履歴コメント追記
- **計算手順 pin** (reviewer iter2 advisory #5): Step 4 / 6 / 8 すべて commit 済みの clean working tree で
  ```bash
  python3 -c "from dashboard.server import _HTML_TEMPLATE; import hashlib; print(hashlib.sha256(_HTML_TEMPLATE.encode('utf-8')).hexdigest())"
  ```
  を実行して値を取得 → `EXPECTED_TEMPLATE_SHA256` に書き込む。**partial commit の途中で計算しないこと** (working tree に未 stage 編集があるとそれも `_HTML_TEMPLATE` に乗って hash が変わる)。
- **commit**: `test(dashboard): bump EXPECTED_TEMPLATE_SHA256 for period toggle (Issue #85)`

> 各 commit で `python3 -m pytest tests/` 全件 green を確認。

## 4. TDD test plan — 追加テスト一覧

新規 `tests/test_dashboard_period_toggle.py` に下記 5 クラスを集約（既存 `test_dashboard.py` への scatter は dispersion を増やすので避ける、新ファイル分離のほうが reviewer 負担低）:

1. **`TestFilterEventsByPeriod`** — Step 1
   - 7d/30d/90d/all の境界、parse 不能 timestamp、naive timestamp 扱い
2. **`TestBuildDashboardDataWithPeriod`** — Step 2
   - `period="7d"` で Overview/Patterns 9 field が縮む / Quality/Surface 6 field は不変 / `period_applied` 出る / `period="all"` がレガシー sig 等価
3. **`TestApiDataPeriodQuery`** — Step 3
   - HTTP query param 経路 / 不正値 fallback / 互換 (既存 `test_api_data_returns_json_with_correct_structure` re-use)
4. **`TestPeriodToggleTemplate`** — Step 4
   - assembled template の DOM / CSS / concat 順
5. **`TestPeriodDriftGuardAndStaticExport`** — Step 7
   - all == legacy / Quality field invariance / `render_static_html` に `?period=` 不在

既存テストへの **小規模追加**:
- `tests/test_dashboard_router.py::TestRouterShellStructure` に `test_period_toggle_in_nav` (toggle DOM が `<nav class="page-nav">` 内に居る structural pin) を 1 件追加
- `tests/test_dashboard_template_split.py::EXPECTED_TEMPLATE_SHA256` を bump (Step 9)

> SSE refresh 中の period 維持テストは `getCurrentPeriod()` を毎 fetch 時に呼ぶ実装で **構造的に race-free** なので別 test 不要だが、心配なら Node round-trip で「`setCurrentPeriod("7d")` 後に `scheduleLoadAndRender()` を 2 連発しても 2 回とも `?period=7d` で fetch される」を pin する小テストを `TestPeriodAwareFetch` に足す。

## 5. Risks / tradeoffs

### (a) UI 配置 — 案 A (グローバル, page-scoped 非表示) vs 案 B (各ページ最上部にローカル)

**plan 推奨: 案 A (グローバル CSS `display:none` で Quality/Surface 時のみ隠す)**

| 観点 | 案 A (global + body[data-active-page] CSS hide) | 案 B (各ページの header に複製) |
|---|---|---|
| 実装複雑度 | **低** (1 DOM + 2 CSS rule) | 中 (Overview header / Patterns header の 2 箇所に DOM + state sync) |
| state 同期 | 単一 closure で完結 | 複数 toggle DOM の `aria-pressed` 同期コード必要 |
| router 切替時の UX | hide / show が CSS 経由で flicker なし | DOM 重複描画でちらつきリスク |
| 既存 router 契約への乗っかり | `body.dataset.activePage` 既存契約を CSS で消費するだけ | 不要 |
| 「効くページでだけ表示」の意図表現 | CSS rule 1 行で明示 | 各 page section に DOM が直書き = 重複 |

→ user 第一案 = 案 A と整合 / 実装コストも低い。**案 A を採用**。fallback (案 B) には Step 4 で部分的に倒せる（`05_period.js` の `wirePeriodToggle()` を query selector all 化するだけ）が、本 plan では着手しない。

### (b) `/api/data` schema 拡張の backward compat

- `period_applied` は **additive field**。古い frontend (period unaware) は読まない → 壊れない
- `period` query 不在 (= 旧 frontend) では server が `"all"` fallback → **完全に現状互換**
- static export は server を経由せず `render_static_html(build_dashboard_data(events))` で `period="all"` を default 引数で取る → 現状互換

`period_scope: ["skill_ranking", ...]` field は **採用しない** 判断:
- field 名 listing は spec doc 側で 1 度書けば十分（`docs/spec/dashboard-api.md` に「period 適用 scope」節）
- frontend が programmatic に scope を読む需要が現状ゼロ（badge は `period_applied !== 'all'` で出すだけで、対象 field の特定は不要）
- additive field を増やすほど SSE 帯域に効くわけではないが、YAGNI 採用

→ `period_applied` のみ追加 / `period_scope` は **将来 issue で再検討**。

### (c) SSE refresh × period 選択の race

- 設計: `fetch('/api/data?period=' + getCurrentPeriod())` を **毎呼び出し時** に評価する。`scheduleLoadAndRender()` 経由で in-flight serialization も既に効いている（`25_live_diff.js`）
- race scenario の検討:
  - SSE message 受信 → `scheduleLoadAndRender()` キック → `fetch` 直前に user が toggle 切替 → **新 period で fetch される**（user 期待と整合）
  - toggle 切替直後に SSE message 受信 → in-flight 中なら `__pendingRefresh = true` で coalesce → 1 回追加 fire される際に **その時点の period** で fetch
- → **構造的に race-free**。追加 lock 不要。

### (d) `period_applied` の必要性

- frontend で badge 出すだけならば **request 側の `getCurrentPeriod()`** でも足りる
- ただし server fallback (不正値 → all) のとき UI badge を「all」で出すべきという意味整合のため **server 側 echo を信頼源にする** ほうが clean
- → 採用（additive cost 1 field、SSE broadcast のサイズ影響無視できる）

### (e) `daily_trend` が `7d` 選択時に 7 点しか出ない

- spec で許容（user 確定）。`localDailyFromHourly` も `hourly_heatmap.buckets` を切ったあとの 7 日分しか rebucket しないので **整合**
- sparkline の x-axis 密度が低くなるが、`spark_x` densify ロジックは `start → end` の連続日埋めで自然対応

### (f) `static export` への漏れ

- Step 7 の `TestStaticExportHasNoPeriodQuery` で `?period=` literal が HTML に含まれないことを assert
- ただし `<script>05_period.js</script>` は static export の HTML にも concat される（template が共通） → **静的 HTML を開くと toggle UI が見える** のは UX バグ
- 対策: `05_period.js` の `wirePeriodToggle()` 内で `if (typeof window.__DATA__ !== 'undefined') { document.getElementById('periodToggle')?.setAttribute('hidden', ''); return; }` の static-export 分岐を入れる
- これも Step 4 のテストに `TestPeriodToggleTemplate::test_static_export_hides_toggle` で pin

### (g0) Filter helper rename (reviewer iter3 advisory #4) — 採用しない

helper 名を `_filter_events_by_period_for_overview_patterns` に rename する案を検討。
- 採用しない理由: 名前が verbose すぎて call site の readability を損なう。docstring + call site comment + Step 1 inclusive pair filter の構造的制約で、誤用リスクは reviewer 指摘当初想定よりも大幅に下がっている。
- 代案として `purpose: Literal["overview_patterns_only"] = "overview_patterns_only"` の **runtime ignored kwarg** を入れて call site で意図を可視化する案もあるが、「無視される引数」は将来の maintainer に「使い方を間違えてる？」と疑念を抱かせるノイズになるので不採用。
- 結論: docstring 強化 + call site comment 重複で十分とみなす。**将来 issue で再 rename 検討は open**。

### (g1) Rolling window vs calendar window (reviewer iter1 question #2)

**plan 採用: rolling window (`now - timedelta(days=N) <= ts <= now`)**

| 観点 | rolling | calendar (local-TZ midnight - (N-1) days) |
|---|---|---|
| 既存 codebase 慣習との整合 | **〇** (`aggregate_skill_lifecycle:cutoff_30d` / `aggregate_skill_hibernating:active_cutoff` がいずれも rolling) | × 新規概念導入 |
| dashboard frontend の local-TZ 表示との視覚アライメント | △ (両端が partial day になり sparkline 始端が中途半端) | **〇** (始端 / 終端の day boundary が綺麗) |
| 実装複雑度 | 低 (`now - timedelta(days=N)`) | 中 (server で local TZ 相当を扱うか / client offset を query で渡すか) |
| 7d 選択時のサンプル幅 | clicking 時刻によって 6.5〜8 日 | 常に 7 calendar 日（うち今日が partial） |

→ **rolling 採用**。calendar window 採用には server が client local TZ を知る必要があり、そのためには query に `tz_offset` を載せる or 単に server UTC で割って client 側で再 bucket するか、実装コストが上がる。本 issue ではコスパ的に rolling で十分と判断。calendar 化は **将来 issue** で、ユーザから「sparkline の境界が気になる」フィードバックが出たら検討（Section 6 に out-of-scope として記載）。

## 6. Out of scope (現状 disposition)

| 項目 | disposition |
|---|---|
| Quality / Surface ページにも period toggle を効かせる | **現状 disposition: 無し**。本 issue では 6 aggregator は全期間据え置き。**将来 issue** で再検討 (failure_trend は週単位 trend なのでそもそも 7d 適用すると 1 点しか出ない問題あり) |
| Period 選択の永続化 (localStorage / URL hash) | **現状 disposition: 無し**。reload で `"all"` リセット仕様で確定 (user 回答 #5) |
| URL hash 同期 (`#/?period=7d` など) | **現状 disposition: 無し**。同上。**将来 issue で再検討** |
| `reports/export_html.py` への period 対応 | **現状 disposition: 無し**。常に全期間 (user 回答 #7)。**将来 issue で再検討** |
| `archive/*.jsonl.gz` opt-in との連携 | **現状 disposition: 無し**。dashboard は hot tier (180d) only なので 90d 以下の period は archive 不要 / 「全期間」も hot tier 180d で十分。**将来 issue で再検討** |
| `period_scope` field を schema に追加 | **現状 disposition: 無し**。`docs/spec/dashboard-api.md` で field listing で代替。**実利用 feedback で必要性が確認できたら additive 追加** |
| Quality/Surface に「Overview/Patterns は period 適用中」の cross-page banner | **現状 disposition: 無し**。混乱が観測されたら将来検討 |
| Calendar window (local-TZ midnight 起点) | **現状 disposition: 無し**。本 issue では rolling window (`now - timedelta(days=N)`) を採用 (Section 5(g))。sparkline の境界が partial day で気になるフィードバックが出たら **将来 issue で再検討** |
