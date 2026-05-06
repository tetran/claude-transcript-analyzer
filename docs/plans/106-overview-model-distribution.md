# Plan: Issue #106 — Overview モデル分布パネル追加 (opus/sonnet/haiku 比率)

## 📋 plan-reviewer 反映ログ

| Proposal | 内容 | 反映箇所 |
|----------|------|----------|
| (初稿) | — | — |

### 二次レビュー反映 (Round 1 → Round 2)

| Proposal | 内容 | 反映箇所 |
|----------|------|----------|
| P1 (actionable) | 新ファイル invariant test を tuple 文字列 grep → tuple import + assertEqual に変更 | §6 test pin (TestModelDistCss `test_no_new_*_file_added` 書き直し) / §5 Phase 4 RED |
| P2 (actionable) | Phase 順序: DOM → CSS → Renderer JS → Renderer JS → CSS に入れ替え (renderer の class hook 命名を先に固めて、CSS が hook を当てに行く順序に) | §5 Phase 4 / Phase 5 swap / §6 test pin の TestModelDistRenderer / TestModelDistCss 順序 |
| P3 (actionable) | Renderer 配置を `45_renderers_sessions.js` 同居案撤回 → **`20_load_and_render.js` 末尾追記**に変更 (Issue 本文 "Overview KPI / spark / project stack を担当している箇所と同居" 回帰、line 234 `project_breakdown` render が同所) | §1 Goal / §4 Critical files / §5 Phase 5 GREEN / §9 R10 全面書き換え |
| P4 (actionable) | CSS 配置を `55_sessions.css` 末尾案撤回 → **`10_components.css` 末尾追記**に変更 (Issue 本文 "10_components.css 内の既存 `.stack-legend` の隣に minor extension" 回帰) | §1 Goal / §4 Critical files / §5 Phase 4 GREEN / §8 CSS additive scope 全面書き換え / §9 R10 |
| P5 (advisory) | SHA256 bump 運用を「PR 直前に最終 hash 確定」だけでなく "phase ごとに bump" 運用も明記 | §9 R5 |
| Q1 (advisory→采用) | `_get_pricing` (prefix match) と `infer_model_family` (substring match) の semantics 違いを Phase 1 RED に対比 test として追加 | §5 Phase 1 RED 末尾 / §9 R7 末尾 |
| Q2 (advisory→采用) | `session_breakdown` cap 超過 fixture で **mismatch を期待する** drift guard test を追加 | §5 Phase 2 RED 末尾 / §9 R8 末尾 |
| Q3 (advisory→采用) | help-pop 「opus 5x」削除判断の代替案 (= verbatim 数値「opus-4-7 vs sonnet-4-6 input 単価比 = 1.67x」を help-pop に書く案) を R6 に追記 | §9 R6 末尾 |

### 三次レビュー反映 (Round 2 → Round 3)

| Proposal | 内容 | 反映箇所 |
|----------|------|----------|
| P1 (actionable) | `EXPECTED_MAIN_JS_FILES` literal の事実誤認を修正: `00_router.js` は `_MAIN_JS_FILES` tuple に含まれず、`__INCLUDE_ROUTER_JS__` sentinel 経由で別経路 load される (`server.py:1204`)。13 file 構成に修正 | §6 line 237-244 literal pin / §5 Phase 5 RED test docstring 補足 |
| P2 (advisory→采用) | 新ファイル禁止 invariant test の literal 生成を「plan 文書に手書き」から「Phase 5 RED 開始時に `dashboard/server.py:1153-1178` から copy-verbatim」に倒す手順を明示 | §5 Phase 5 RED 第 1 step / §6 末尾コメント書き直し |
| P3 (advisory→采用) | `--rose` 「未使用 token」claim を narrowing: 実 grep で `10_components.css:253` の `.rank-row .meta .fail` で既に使用中。「panel-head dot 用途では未使用」とスコープを限定 | §1 Goal / §4 Critical files / §9 R10 末尾 |
| Q1 (advisory→采用) | DOM 挿入位置を「line 135 直後」→「`</section>` (line 137) **直前**」に変更。shell.html 上方の将来編集で line 数 ±1 ずれても安定 | §4 Critical files (shell.html 行) / §5 Phase 3 GREEN |
| Q2 (advisory→采用) | `20_load_and_render.js` の outer async IIFE 内 / loadAndRender 関数外 の **2 重 IIFE 構造** (= shell.html line 605-613 の `(async function(){ ... })()` 内に入れ子) を §5 Phase 4 GREEN で明示 | §5 Phase 4 GREEN 構造説明 |
| Q3 (advisory→采用) | Phase 3 単独 commit が「sha256 OK / DOM はあるが renderer 未実装で空 panel」中間状態になる旨を §9 R5 末尾に bisect note として追記 | §9 R5 末尾 |

## 1. Goal

Overview ページに「モデル分布」パネルを追加し、`assistant_usage` event を **family rollup (opus / sonnet / haiku)** で集計した **メッセージ数 / コスト** の二軸 donut chart として render する。集計は `/api/data` の新規 additive キー `model_distribution` を経由し、period toggle (Issue #85) と整合 (`period_events_raw` 経由 = Issue #99 の `session_breakdown` と同じ判断)。集計ソースは `assistant_usage.model` field (raw) → `cost_metrics` 価格表ベースの per-message cost (= `calculate_message_cost`) を family rollup で sum。表示は Comment #3 の **donut grid + 共有 legend** 構成、callout 閾値 5%、legend header は mono lowercase (`msgs` / `cost`)、center label は eyebrow + numeric の 2 段 (単位 suffix なし)、単一 family は 100% donut そのまま render。Sessions ページ側のモデル色 (`--coral` opus / `--mint` sonnet / `--peach` haiku) と完全整合させ、新パネル head dot は **panel-head 系で未使用** の token `--rose` を使う (`00_base.css:22` 定義済、現状 `10_components.css:253` の `.rank-row .meta .fail` でのみ使用、panel-head の `c-coral` / `c-peri` / `c-peach` 系では未採用)。Renderer は **`20_load_and_render.js` 末尾追記** (Issue 本文「Overview KPI / spark / project stack を担当している箇所と同居」)、CSS は **`10_components.css` 末尾追記** (Issue 本文「10_components.css 内の既存 `.stack-legend` の隣に minor extension」)。stdlib only / 既存 CSS / JS ファイルへの追記のみ (concat 順無改変) / TDD-first / help-pop = 集計ロジックの正本 verbatim を厳守。

## 2. 採用 spec まとめ (前提固定)

- **レイアウト**: donut grid (Comment #3)。`.stack` 流用案は破棄
- **モデル粒度**: family rollup (opus / sonnet / haiku)。未知 → sonnet fallback (`cost_metrics.DEFAULT_PRICING` の慣習および `inferModelFamily` JS と整合)
- **callout 閾値**: 5%
- **legend header**: mono lowercase (`msgs` / `cost`)
- **center label**: eyebrow (`MESSAGES` / `COST`) + mono numeric。単位 suffix 行なし
- **単一 family**: 100% donut そのまま (= `(a)`)
- **集計ソース field**: `assistant_usage.model` (raw)
- **period scope**: period 適用 (Overview の period scope に新たに追加)
- **API field 名 stable**: `model_distribution`
- **family 列挙順 (canonical)**: `opus → sonnet → haiku`。これを「donut の slice 並び順 (12 時から時計回り)」「legend 行順」「API 配列順」3 軸で同期させる。これにより **donut 幾何の決定論性** が保証される (random 順だと SHA256 / 視覚 snapshot が flaky になる)
- **空 events / 単一 family の handling**: `model_distribution` は **常に 3 行配列で返す** (各 family に対し `messages=0` / `cost_usd=0` のゼロ行を必ず含む)。空 events 時は 3 行とも 0 件、`messages_total=0` / `cost_total=0`、`messages_pct/cost_pct` は **NaN guard で 0.0 fallback**。これで client 側の "1 family しか dict に存在しない" 分岐も "完全空" 分岐もテンプレートとして一様 (= JS 側の defensive 分岐を最小化)

## 3. API contract: `model_distribution`

### shape

```json
{
  "model_distribution": {
    "families": [
      { "family": "opus",   "messages": 312, "messages_pct": 0.61, "cost_usd": 48.5012, "cost_pct": 0.74 },
      { "family": "sonnet", "messages": 175, "messages_pct": 0.34, "cost_usd": 14.0123, "cost_pct": 0.21 },
      { "family": "haiku",  "messages": 25,  "messages_pct": 0.05, "cost_usd": 3.4321,  "cost_pct": 0.05 }
    ],
    "messages_total": 512,
    "cost_total": 65.9456
  }
}
```

### 設計判断 (各点について必ず述べる)

- **並び順**: canonical 固定 `opus → sonnet → haiku` (cost 降順ではない)。理由: (a) donut の start-angle 決定論性、(b) Sessions ページの `inferModelFamily` の prefix-match 順と整合、(c) callout / legend / 視覚スナップショットの drift 防止
- **フィールド構成 (5 列)**: `family` (str) / `messages` (int) / `messages_pct` (float, 0..1) / `cost_usd` (float) / `cost_pct` (float, 0..1)
- **`pct` の数値表現**: **0..1 の float** (例: 0.61)。`session_breakdown` の `cost_pct` 表現と整合させ、frontend で `Math.round(pct * 100)` する慣習を踏襲
- **丸め**: `cost_usd` は 4 桁 (cost_metrics 慣習)、`*_pct` は **丸めずに float 全桁** (UI 側で round。AC「合計 ±0.5%」を満たすため server 側で先丸めしない)
- **集計合計を別 field**: `messages_total` (int) / `cost_total` (float, 4 桁) を **dict トップに別置き**。理由: (a) UI の center label 表示で配列再 sum を避ける、(b) 完全空 events で 3 行 zero でも total が 0 と明示できる、(c) help-pop の verbatim filter 条件 (= `Σ messages` が "Assistant メッセージ数" の正本) と整合
- **配列 vs オブジェクト**: トップは **`{ "families": [...], "messages_total": N, "cost_total": F }` 構造の dict**。配列直返しではない (= `messages_total` を含めるため)
- **常に 3 行**: family 数が 0 / 1 / 2 でも **必ず 3 行返す** (`messages=0` 行を埋める)。完全空のときも 3 行返す
- **`*_pct` の NaN guard**: `messages_total == 0` のとき `messages_pct = 0.0`。`cost_total == 0` のとき `cost_pct = 0.0`。div-by-zero を server 側で塞ぐ (frontend に NaN を漏らさない)
- **未知 model**: raw model 名は output に出さない (= family のみ rollup 出力)。未知 → `sonnet` family にロールアップ (理由: `cost_metrics.DEFAULT_PRICING = sonnet-4-6` と意味論を一致させ、cost 推計と family rollup の double standard を作らない)

### family rollup helper

`cost_metrics.py` に **`infer_model_family(model: str) -> str`** を新規追加 (純関数):

```python
def infer_model_family(model: str) -> str:
    """raw model ID → 'opus' / 'sonnet' / 'haiku' の family 文字列 (lowercase substring match, 未知 → 'sonnet')."""
    m = (model or "").lower()
    if "opus" in m: return "opus"
    if "haiku" in m: return "haiku"
    if "sonnet" in m: return "sonnet"
    return "sonnet"
```

理由: (a) JS 側の `inferModelFamily` (45_renderers_sessions.js:30) と semantics を 1:1 にして「unit test で同じ未知 model を投げて family が一致」を guard できる、(b) `cost_metrics._get_pricing` の longest-prefix match とは抽象階層が違う (= rate 解決 vs family rollup) ので分離、(c) Issue #103 の慣習 (raw model → family rollup chip 表示) を Python 側で初めて再生する

### aggregator helper

`cost_metrics.py` に **`aggregate_model_distribution(events: list[dict]) -> dict`** を新規追加:

- 入力: events list (= `period_events_raw`)。`event_type == "assistant_usage"` のみ filter
- 各 event について `family = infer_model_family(ev["model"])` → `messages[family] += 1` / `cost[family] += calculate_message_cost(model, in, out, cr, cc)`
- 出力 dict は前掲 shape に sort 済み (canonical order) で固定 3 行
- `cost_total` は 4 桁丸め後の sum を **再丸め** (`session_breakdown.estimated_cost_usd` と同じ regime)

## 4. Critical files (編集 / 新規作成一覧)

### 編集
- `cost_metrics.py` — `infer_model_family()` + `aggregate_model_distribution()` 追加
- `dashboard/server.py` — `build_dashboard_data` の return dict に `model_distribution` キーを additive で追加 (line 1086 周辺、`session_breakdown` の直前 / 直後)
- `dashboard/template/shell.html` — `</section><!-- /data-page="overview" -->` (line 137) **直前**に新 panel `<div class="panel" id="model-dist-panel">` を挿入 (= プロジェクト分布 panel `</div>` line 135 と overview セクション閉じ line 137 の間。「`</section>` 直前」と定義することで shell.html 上方の編集で line 数が ±1 ずれても挿入位置が一意に解決する、Round 2 reviewer Q1 反映)
- `dashboard/template/scripts/20_load_and_render.js` — **既存ファイル末尾に追記** (Issue 本文「Overview KPI / spark / project stack を担当している箇所と同居」回帰)。`loadAndRender` 関数本体のおわり (line 309 `} // end loadAndRender` 周辺) で `renderModelDistribution(data)` を呼び出し、helper / renderer を loadAndRender スコープ外で同 file 末尾に追加。`window.__modelDist = { ... }` で expose (Sessions と同じ pattern)。`_MAIN_JS_FILES` tuple は無改変
- `dashboard/template/styles/10_components.css` — **既存ファイル末尾に追記** (Issue 本文「10_components.css 内の既存 `.stack-legend` の隣に minor extension」回帰)。約 50–70 行の additive blocks (donut + legend + axis-pair grid + center label + callout)。`.stack-legend` rules (line 296〜) の直後に `.axis-pair` / `.donut` 系を加える。同 file 内の既存 `.panel-head.c-peach` 行直後 (line 185 周辺) に `.panel-head.c-rose .ttl .dot { background: var(--rose); }` 1 行も追加。`_CSS_FILES` tuple は無改変
- `docs/spec/dashboard-api.md` — line 31 (period scope), line 67 (top-level shape), line 806 直前 / 直後 に詳細セクション追加
- `docs/spec/dashboard-runtime.md` — line 99 の Overview 行「主な目的」に「モデル分布」追記
- `tests/test_dashboard_template_split.py` — `EXPECTED_TEMPLATE_SHA256` bump

### 新規作成
- `tests/test_model_distribution.py` — server-side aggregation (cost_metrics + build_dashboard_data 統合)
- `tests/test_model_distribution_template.py` — DOM / CSS / JS renderer / help-pop verbatim
- `docs/plans/106-overview-model-distribution.md` — 本プラン本体

## 5. 6-phase TDD ordered steps

### Phase 1 — `infer_model_family()` + `aggregate_model_distribution()` (cost_metrics.py)

**RED**: `tests/test_model_distribution.py` に以下を書く。実装前なので import / aggregator 呼び出しがすべて失敗する。

- `TestInferModelFamily::test_opus_4_7_rolls_up_to_opus` — `infer_model_family("claude-opus-4-7-20260101") == "opus"`
- `TestInferModelFamily::test_sonnet_4_6_rolls_up_to_sonnet` — sonnet
- `TestInferModelFamily::test_haiku_4_5_rolls_up_to_haiku` — haiku
- `TestInferModelFamily::test_legacy_3_5_haiku_rolls_up_to_haiku` — `claude-3-5-haiku-20241022` → haiku (3.x 命名規則 verbatim)
- `TestInferModelFamily::test_unknown_model_falls_back_to_sonnet` — `"made-up-model"` → sonnet (= `DEFAULT_PRICING` の意味論と整合)
- `TestInferModelFamily::test_empty_string_falls_back_to_sonnet` — `""` / `None` → sonnet
- `TestAggregateModelDistribution::test_returns_three_rows_with_canonical_order` — opus 1 件のみ → `[opus, sonnet, haiku]` 3 行 (order verbatim)、sonnet/haiku は 0 行
- `TestAggregateModelDistribution::test_messages_pct_sums_to_one_within_tolerance` — opus 3 / sonnet 5 / haiku 2 → `Σ messages_pct == 1.0 ± 0.005` (Issue AC「±0.5%」を `assertAlmostEqual(places=2)` で pin)
- `TestAggregateModelDistribution::test_cost_pct_sums_to_one_within_tolerance` — 同上、cost 軸
- `TestAggregateModelDistribution::test_messages_total_matches_array_sum` — `messages_total == sum(row.messages)`
- `TestAggregateModelDistribution::test_cost_total_matches_array_sum_to_4_decimals` — `cost_total == round(sum(row.cost_usd), 4)`
- `TestAggregateModelDistribution::test_empty_events_returns_three_zero_rows` — 空 list → 3 行とも 0、`messages_total=0`、`cost_total=0`、`*_pct=0.0` (NaN guard)
- `TestAggregateModelDistribution::test_only_assistant_usage_events_counted` — `session_start` / `skill_tool` / `subagent_start` 混在 → `assistant_usage` 以外は ignore
- `TestAggregateModelDistribution::test_unknown_model_rolls_up_to_sonnet_in_distribution` — `model="future-model-xyz"` の `assistant_usage` → sonnet 行に集計、cost は `DEFAULT_PRICING` (sonnet-4-6) で計算
- `TestAggregateModelDistribution::test_cost_uses_calculate_message_cost_per_event` — opus 1 件 (input=1M / output=1M) で cost = $5 + $25 = $30 を verbatim 期待 (cost_metrics の rate と整合する drift guard)
- `TestAggregateModelDistribution::test_zero_cost_event_does_not_zero_div` — token=0 / cost=0 のみの events → `cost_total=0` で **NaN なし** (= `cost_pct = 0.0`)
- `TestPricingHelperSemanticsContrast::test_get_pricing_uses_prefix_match` — `_get_pricing("claude-opus-4-5-20260101")` が **longest-prefix match** で `claude-opus-4-5` を当てて `claude-opus-4` ($15) ではなく `claude-opus-4-5` ($5) を返すことを verbatim pin (= rate 取り違えの致命性)
- `TestPricingHelperSemanticsContrast::test_infer_model_family_uses_substring_match` — `infer_model_family("opus-foo-bar-haiku")` が **substring match の前後関係 (opus check が先)** で `"opus"` を返すことを verbatim pin (= rate 解決とは別の抽象階層、family は取り違えても rate ほどの致命性が無いので semantics 違いを test で明示)
- 上記 2 test は **同じ test class 内に並べる** ことで「両 helper の semantics は意図的に違う」ことを将来の reviewer / maintainer に文書化 (R7 末尾参照)

**GREEN**: `cost_metrics.py` に `infer_model_family()` + `aggregate_model_distribution()` を実装。

**順序根拠**: helper (純関数) → aggregator → 統合の順。aggregator が helper を呼ぶので helper を先に GREEN にする。

### Phase 2 — `build_dashboard_data` 統合 (server.py)

**RED**: `tests/test_model_distribution.py` に integration テストを追加 (= Phase 1 と同ファイル)。

- `TestBuildDashboardDataModelDistribution::test_field_present_in_response` — return dict に `"model_distribution"` キーが存在
- `TestBuildDashboardDataModelDistribution::test_shape_has_families_and_totals` — `model_distribution` は dict で、`families` (list, len=3) / `messages_total` (int) / `cost_total` (float) を持つ
- `TestBuildDashboardDataModelDistribution::test_period_filter_applied` — period="7d" 指定で 8 日前の `assistant_usage` event が **除外** される (= `period_events_raw` 経由で再集計される) verbatim 検証。`session_breakdown` の period guard と同形
- `TestBuildDashboardDataModelDistribution::test_period_all_includes_all_events` — period="all" で全件含む
- `TestBuildDashboardDataModelDistribution::test_subagent_assistant_usage_included` — `source="subagent"` の `assistant_usage` も `model` field を持つので集計対象 (Issue body の "subagent token 別 model 扱い → 別 issue" out-of-scope は **per-message cost に subagent invocation の入れ子を作らない** という意味で、subagent の `assistant_usage` event 自体は normal な per-message として count される。verbatim pin)
- `TestBuildDashboardDataModelDistribution::test_session_breakdown_total_matches_model_distribution_total` — drift guard: `Σ row.estimated_cost_usd ≈ model_distribution.cost_total` を `assertAlmostEqual(places=4)` で pin (= 集計経路が一致する cross-aggregator invariant)
  - 注意: `session_breakdown` は top_n=20 cap があるので、cap を超える events では一致しない。テスト fixture は **20 session 未満** で組む
- `TestBuildDashboardDataModelDistribution::test_session_breakdown_total_diverges_from_model_distribution_above_cap` — **対偶 drift guard**: 21 session 以上の fixture で `Σ row.estimated_cost_usd < model_distribution.cost_total` (= 厳密 `<`、cap で 1 session 落ちる分だけ session_breakdown 側が小さくなる) を pin。R8 の「cap 超過は受容」を test レベルでも明示 (= 受容判断の load-bearing test)
- `TestBuildDashboardDataModelDistribution::test_empty_events_yields_three_zero_rows` — 空 events → 3 行 zero

**GREEN**: `server.py` の `build_dashboard_data` return dict (line 1086〜) に `"model_distribution": aggregate_model_distribution(period_events_raw)` を additive に追加。`session_breakdown` の上か下に配置 (period_events_raw 経由は同じ)。

**順序根拠**: aggregator 単体 (Phase 1) が GREEN になってから build_dashboard_data に noise なく組み込めるため。

### Phase 3 — Template DOM (shell.html) + SHA256 bump

**RED**: `tests/test_model_distribution_template.py` を新規作成して以下を書く。

- `TestModelDistPanelDOM::test_panel_exists_with_id` — assembled template に `id="model-dist-panel"` が含まれる
- `TestModelDistPanelDOM::test_panel_inside_overview_section` — `<section data-page="overview">...</section>` 内に存在 (= `_extract_section(template, 'overview')` 部分文字列 contain)
- `TestModelDistPanelDOM::test_panel_after_project_distribution_panel` — 文字列 index 比較で `id="stack"` (プロジェクト分布) → `id="model-dist-panel"` の順 (= AC 「プロジェクト分布の直後」)
- `TestModelDistPanelDOM::test_panel_head_uses_c_rose` — panel-head に `c-rose` class
- `TestModelDistPanelDOM::test_panel_title_is_モデル分布` — `<span class="ttl">` の中に `モデル分布` 含む
- `TestModelDistPanelDOM::test_help_pop_id_is_hp_model_dist` — `id="hp-model-dist"` で help-pop が存在
- `TestModelDistPanelDOM::test_help_pop_body_verbatim` — help-pop body 文字列が **section 4-axis verification に従って** 以下の filter 条件 verbatim を含む:
  - `Assistant メッセージごとの` `model` `フィールド` (= 集計 source の verbatim pin)
  - `family` キーワード (= rollup 軸の verbatim pin)
  - **解決判断**: help-pop body は **比率の具体数値は書かない方向に修正**。Comment #3 の verbatim を保持しつつ「(opus の単価は sonnet の約 5 倍)」を **削除** して「両軸を併置することで、message-count では見えないコスト偏りを発見できる」のみ残す。これにより 4-axis verification (= 文と価格表の verbatim 整合) が成立する。**最終 help-pop 文案 §7 を参照**
- `TestModelDistPanelDOM::test_panel_body_has_axis_pair_grid` — `<div class="axis-pair">` を body に持つ
- `TestModelDistPanelDOM::test_panel_body_has_two_axes` — body 内に `data-axis="messages"` と `data-axis="cost"` の 2 要素
- `TestModelDistPanelDOM::test_each_axis_has_donut_svg` — 各 axis に `<svg class="donut">` が存在
- `TestModelDistPanelDOM::test_each_axis_has_center_label` — 各 axis に `<div class="axis-center">` (eyebrow + numeric)
- `TestModelDistPanelDOM::test_panel_has_shared_legend` — `<div class="model-legend">` が body 内に存在
- `TestModelDistPanelDOM::test_legend_header_uses_lowercase_msgs_cost` — header 部分に `msgs` / `cost` が verbatim 含まれる (大文字 `MSGS` / `COST` ではない: Q4 採用)
- `TestSha256Bump::test_expected_sha256_updated` — (これは `test_dashboard_template_split.py` 既存 test の RED → GREEN サイクル。Phase 3 開始時に shell.html 編集すると `EXPECTED_TEMPLATE_SHA256` が即 fail。新 hash を pin)

**GREEN**: `dashboard/template/shell.html` の `</section><!-- /data-page="overview" -->` (line 137) **直前**に panel HTML 挿入 (Round 2 reviewer Q1 反映で `</section>` 直前を anchor に変更)。`tests/test_dashboard_template_split.py:28` の `EXPECTED_TEMPLATE_SHA256` を新 hash に bump。

**順序根拠**: API contract (Phase 1-2) が固まってから DOM の `id` / `data-axis` を pin する。renderer (Phase 4) が読む DOM hook を先に作る。

### Phase 4 — Renderer JS (20_load_and_render.js 末尾追記)

**注意**: 旧 plan は Phase 4 = CSS / Phase 5 = Renderer の順だった。round 1 reviewer P2 指摘により **Phase 4 / Phase 5 を入れ替え** (renderer の class hook 命名を先に固めて、Phase 5 で CSS が hook を当てに行く順序に変更。CSS rule に対する dead test を回避)。

**RED**: `tests/test_model_distribution_template.py` に Node round-trip テスト + smoke 追加。

- `TestModelDistRenderer::test_render_model_distribution_function_defined` — `20_load_and_render.js` 文字列に `function renderModelDistribution(` 含む
- `TestModelDistRenderer::test_window_modeldist_exposed` — `20_load_and_render.js` に `window.__modelDist = {` 含む
- `TestModelDistRenderer::test_load_and_render_calls_render_model_distribution` — `20_load_and_render.js` の `loadAndRender` 関数本体内 (line 309 `} // end loadAndRender` より前) に `renderModelDistribution(data)` 呼び出しが verbatim 含む
- `TestModelDistRenderer::test_overview_page_scoped_early_out` — renderer 関数本体に `dataset.activePage !== 'overview'` early-out 含む
- `TestModelDistRenderer::test_canonical_order_hardcoded` — JS 文字列に `['opus', 'sonnet', 'haiku']` (verbatim 順) を含む (= slice / legend 順の決定論性 pin)
- `TestModelDistRenderer::test_callout_threshold_5_percent` — JS 文字列に `0.05` リテラルを含む (Q3 採用 pin)
- `TestModelDistRenderer::test_buildDonutSvg_helper_via_node` — Node 経由で `__modelDist.buildDonutSvg([{family:'opus',messages:6,messages_pct:0.6},{family:'sonnet',messages:3,messages_pct:0.3},{family:'haiku',messages:1,messages_pct:0.1}], 'messages')` を呼び、戻り SVG 文字列に 3 つの `<circle class="donut-slice s-opus">` / `s-sonnet` / `s-haiku` (stroke-dasharray 形式) と stroke-dashoffset の累積角度が含まれることを検証 (= Phase 5 CSS rule 命名の hook 確定)
- `TestModelDistRenderer::test_buildDonutSvg_handles_zero_total_via_node` — 全 zero events → SVG が `<circle class="donut-empty">` 1 本だけになる degenerate handling (= Q6 (a) は 100% donut だが、**完全 zero** のときは 0% donut も成り立たないので空リング fallback。これは「単一 family 100%」と区別されるエッジケース)
- `TestModelDistRenderer::test_buildLegendHtml_includes_msgs_and_cost_columns` — Node 経由で legend HTML に `msgs` / `cost` (lowercase) header を含む
- `TestModelDistRenderer::test_buildLegendHtml_uses_canonical_order` — Node 経由で legend HTML に opus → sonnet → haiku 順で行が並ぶ (`indexOf` で順序確認)
- `TestModelDistRenderer::test_buildLegendHtml_uses_leg_class` — Node 経由で legend row に `leg-opus` / `leg-sonnet` / `leg-haiku` (`leg-${family}` 形式) class が含まれる (= Phase 5 CSS rule 命名の hook 確定)
- `TestModelDistRenderer::test_buildCalloutHtml_filters_below_5pct` — Node 経由で 4% slice → callout 出ない / 5% slice → callout 出る (boundary inclusive vs exclusive、`>= 0.05` で包括する)
- `TestModelDistRenderer::test_center_label_has_no_unit_suffix` — Node 経由で `buildCenterLabel({eyebrow:'MESSAGES', value:5432})` → 戻り HTML に `msgs` / `USD` 文字列を含まない (Q5 採用 pin)
- `TestModelDistRenderer::test_single_family_renders_full_circle` — Node 経由で opus 100% (sonnet=0/haiku=0) → opus slice が 360deg full circle (= Q6 (a) pin)

**GREEN**:
- `20_load_and_render.js` 末尾に新規 IIFE を追加 (= 既存 `loadAndRender` 関数とは別スコープ)。**構造の補足** (Round 2 reviewer Q2 反映): shell.html line 605-613 で `__INCLUDE_MAIN_JS__` 全体が `(async function(){ ... })();` の **outer async IIFE** で wrap される。本 plan の renderer IIFE は **outer async IIFE 内 / loadAndRender 関数外** の 2 重 IIFE 構造になる (`45_renderers_sessions.js:15` の `(function(){` Sessions IIFE と同じ pattern)。pure helpers: `buildDonutSvg(families, axis)` / `buildLegendHtml(families)` / `buildCalloutHtml(families, axis)` / `buildCenterLabel({eyebrow, value})` / `renderModelDistribution(data)`。`window.__modelDist` で expose (Sessions ページの `window.__sessions` pattern と整合)
- `loadAndRender` 関数本体内 (line 309 `} // end loadAndRender` より前) に `if (window.__modelDist?.renderModelDistribution) window.__modelDist.renderModelDistribution(data);` 1 行追加
- SVG donut の実装: `<circle class="donut-slice s-${family}" r=R cx=cy=center stroke-width=W stroke-dasharray="(pct * C) C" stroke-dashoffset="-prevAcc" transform="rotate(-90 cx cy)" />` 技。`C = 2πR`。canonical 順で acc を進めるので opus が 12 時から右回り、sonnet → haiku で続く
- callout: 各 family `pct >= 0.05` のとき、円弧中心角 (= prevAcc + pct/2) の極座標から leader 線端点を計算し `<line>` + `<text>` を absolute 配置
- legend row: `<div class="leg-row leg-${family}">…</div>` の class 命名 (= Phase 5 で CSS が `leg-opus` / `leg-sonnet` / `leg-haiku` に対して rule を打つ hook)

**順序根拠**: TDD-first 原則上「**観測可能な振る舞い (= renderer 出力 class hook)**」を先に固める。Phase 5 の CSS rule は **Phase 4 で確定した class 名** に対して打たれるので、CSS rule の dead test (= 文字列 contain だけで通る) を回避できる。

### Phase 5 — CSS additive (10_components.css 末尾追記)

**RED 第 1 step (Round 2 reviewer P2 反映)**: 本 phase RED 開始時に `dashboard/server.py:1153-1178` を **直接 Read** して `_CSS_FILES` (9 entries) / `_MAIN_JS_FILES` (13 entries — `00_router.js` は **含まれない**、`__INCLUDE_ROUTER_JS__` sentinel 経由で別経路 load される) を copy-verbatim で `tests/test_model_distribution_template.py` 冒頭の `EXPECTED_*` 定数に反映する。plan 文書の literal 例 (§6 line 233-244) は plan 作成時点のスナップショットなので、Phase 5 RED 開始時の最新 tuple と再 align する step を踏む。

**RED**: `tests/test_model_distribution_template.py` に CSS smoke を追加。

- `TestModelDistCss::test_panel_head_c_rose_dot_color` — `10_components.css` を文字列 read して `.panel-head.c-rose .ttl .dot { background: var(--rose); }` を verbatim 含む
- `TestModelDistCss::test_donut_class_defined` — `10_components.css` に `.donut {` を含む (Phase 4 が出す `donut-slice` の親 class)
- `TestModelDistCss::test_donut_slice_color_tokens_match_phase4_hooks` — Phase 4 で確定した `s-opus` / `s-sonnet` / `s-haiku` class に対して `10_components.css` 内の rule が `var(--coral)` / `var(--mint)` / `var(--peach)` の stroke 指定を持つ verbatim 整合 (= Sessions ページ整合の drift guard、3 軸同期 pin)
- `TestModelDistCss::test_axis_pair_grid_defined` — `.axis-pair {` + `display: grid` 含む
- `TestModelDistCss::test_axis_center_eyebrow_defined` — `.axis-head` (eyebrow) + `.axis-center` (中央 numeric)
- `TestModelDistCss::test_donut_callout_defined` — `.donut-callout` rule 存在
- `TestModelDistCss::test_model_legend_uses_canonical_color_tokens` — Phase 4 で確定した `leg-opus` / `leg-sonnet` / `leg-haiku` class に対して `var(--coral)` / `var(--mint)` / `var(--peach)` を使う verbatim 整合
- `TestModelDistCss::test_donut_empty_class_defined` — `.donut-empty` rule 存在 (Q6 完全 zero fallback)
- `TestModelDistCss::test_main_js_files_unchanged` — `dashboard.server._MAIN_JS_FILES` を **import** して `EXPECTED_MAIN_JS_FILES` (= test ファイル冒頭で literal pin した tuple) と `assertEqual`。新ファイル禁止 invariant の構造的 guard (= round 1 reviewer P1 反映、文字列 grep より robust)
- `TestModelDistCss::test_css_files_unchanged` — `dashboard.server._CSS_FILES` を **import** して `EXPECTED_CSS_FILES` と `assertEqual`。同上

```python
# tests/test_model_distribution_template.py 冒頭
# 本 literal は Round 3 反映時点 (Issue #106 plan 作成時) の dashboard/server.py:1153-1178 を verbatim copy。
# Phase 5 RED 開始時点で server.py の tuple を再 Read → 不一致なら本 literal を最新値に書き換える。
EXPECTED_CSS_FILES = (
    "00_base.css", "10_components.css", "15_heartbeat.css", "20_help_tooltip.css",
    "30_pages.css", "40_patterns.css", "50_quality.css", "55_sessions.css", "60_surface.css",
)
# 注: `00_router.js` は `_MAIN_JS_FILES` に含まれない。dashboard/server.py:1204 で `__INCLUDE_ROUTER_JS__`
# sentinel 経由で別経路 load されるため、本 tuple とは独立 (Round 2 reviewer P1 反映)。
EXPECTED_MAIN_JS_FILES = (
    "05_period.js", "10_helpers.js", "15_heartbeat.js",
    "20_load_and_render.js", "25_live_diff.js", "30_renderers_patterns.js",
    "40_renderers_quality.js", "45_renderers_sessions.js", "50_renderers_surface.js",
    "60_hashchange_listener.js", "70_init_eventsource.js", "80_help_popup.js", "90_data_tooltip.js",
)
```
※ 上記 literal は **plan Round 3 時点で `dashboard/server.py:1153-1178` を直接 Read した実測**。Phase 5 RED 開始時点で同 tuple を再 Read → 不一致なら literal を最新 tuple で pin する step を Phase 5 RED 第 1 step (上記) として明記済。

**GREEN**:
- `10_components.css` 末尾追記 (約 50〜70 行 additive、`.stack-legend` rules line 296 の直後付近に挿入する形でも OK):
  - `.panel-head.c-rose .ttl .dot { background: var(--rose); }` 1 行 (line 185 の `c-peach` 行直後挿入も可、final placement は実装時判断)
  - `.axis-pair` 2-col grid + 中央 hairline (`border-left: 1px solid var(--line)` を 2 番目の axis に)
  - `.axis-head` eyebrow (small uppercase letter-spacing)
  - `.axis-center` (eyebrow + numeric の縦積み、SVG 中心位置に絶対配置)
  - `.donut` (`width: ~140px` / `height: ~140px` / SVG `<circle>` の `stroke-width` / `transform: rotate(-90deg)`)
  - `.donut-slice.s-opus` / `.s-sonnet` / `.s-haiku` (stroke = `var(--coral)` / `var(--mint)` / `var(--peach)`)
  - `.donut-callout` (leader 線 + label, モデル色 / `--ink` 強調)
  - `.donut-empty` (Q6 完全 zero fallback、`stroke: var(--ink-faint)` / `stroke-opacity: 0.3`)
  - `.model-legend` table-like grid (1 row = family、`leg-row leg-${family}` で family 別 class hook、4 cells: dot / family-name / msgs / cost)
  - `.model-legend .leg-msgs` / `.leg-cost` (mono lowercase, font-size 9-10px, `letter-spacing: 0.04em`、`text-transform: none`) — Q4 採用

**順序根拠**: Phase 4 で renderer が出す class hook (`s-opus` / `leg-opus` / `axis-pair` / `donut-empty` 等) が確定した後、Phase 5 で CSS rule を「**Phase 4 で実証済の class**」に対して打てる。dead CSS rule (= 該当 DOM が無いまま CSS にだけ存在する rule) を回避できる。

### Phase 6 — docs (dashboard-api.md + dashboard-runtime.md)

**RED**: 既存 docs テストには文字列レベルの spec 整合 guard は無いので、本 phase は非 TDD。代わりに **manual review checklist**:

- `docs/spec/dashboard-api.md` line 31 (Period 適用 scope の Overview 行) に `model_distribution` を追記
- `docs/spec/dashboard-api.md` line 67 (top-level shape JSON 例) に `"model_distribution": { ... }` 行を追加
- `docs/spec/dashboard-api.md` 詳細セクション: `session_breakdown` (line 806 〜) と同じ形式で `## model_distribution (Issue #106 / v0.8.0〜)` セクションを追加。内容:
  - 形 (JSON 例 verbatim — §3 と完全一致)
  - 集計仕様 (assistant_usage の model field を family rollup → per-message cost の sum)
  - canonical order の load-bearing 性
  - 未知 model fallback (sonnet)
  - period 連動 (= period_events_raw 経由、session_breakdown と同じ判断)
  - NaN guard (空 events で `*_pct = 0.0`)
  - 空 events / 単一 family も常に 3 行
- `docs/spec/dashboard-runtime.md` line 99 Overview 行の「主な目的」末尾に `/ モデル分布 (Issue #106)` 追記

**GREEN**: 上記 docs 変更を反映。

**順序根拠**: 実装が固まってから docs を書く (= 仕様 drift 防止)。Phase 1-5 が GREEN になってから docs を pin する。

### Phase 7 — PR 作成 (TDD ではないが TODO)

- branch `feature/106-overview-model-distribution` (base `v0.8.0`) を push
- PR 本体は `gh pr create` で `--base v0.8.0` 指定。Test plan に visual smoke (chrome-devtools MCP で Overview ページの `#model-dist-panel` screenshot) を含める
- 必要 CI: `pytest tests/test_model_distribution.py tests/test_model_distribution_template.py tests/test_dashboard_template_split.py tests/test_dashboard_sessions_api.py tests/test_cost_metrics.py` 全 GREEN

## 6. Test files pin

### `tests/test_model_distribution.py` (server-side)

- TestInferModelFamily (6 cases): rollup の 4-7 / 4-6 / 4-5 / legacy 3.x / unknown / empty
- TestAggregateModelDistribution (10 cases): canonical order / pct sum tolerance / total invariants / empty / non-assistant_usage 除外 / unknown rollup / cost 数値 verbatim / NaN guard
- TestPricingHelperSemanticsContrast (2 cases): `_get_pricing` prefix match と `infer_model_family` substring match の semantics 違い対比 (round 1 reviewer Q1 反映)
- TestBuildDashboardDataModelDistribution (8 cases): field 存在 / shape / period filter / period all / subagent assistant_usage 包含 / session_breakdown drift guard cap 内 一致 / **session_breakdown drift cap 超過 mismatch** (round 1 reviewer Q2 反映) / 空 events

### `tests/test_model_distribution_template.py` (DOM + CSS + JS)

- TestModelDistPanelDOM (13 cases): panel 存在 / overview section 内 / プロジェクト分布の後 / c-rose / タイトル / help-pop ID / help-pop body verbatim / axis-pair / 2 axes / donut SVG / center label / 共有 legend / lowercase header
- TestModelDistRenderer (13 cases、Phase 4 で先に走る): Node round-trip / window expose / load-and-render 呼び出し / page-scoped early-out / canonical order pin / callout 5% pin / buildDonutSvg shape (`s-opus` 等 class hook) / zero degenerate (`donut-empty`) / legend lowercase / legend `leg-${family}` class hook / center label no unit / single family full circle / callout boundary
- TestModelDistCss (10 cases、Phase 5 で走る): c-rose dot / donut / **donut-slice color token = Phase 4 hook 整合** / axis-pair / axis-head / axis-center / donut-callout / **model-legend canonical color = Phase 4 hook 整合** / donut-empty / **`_CSS_FILES` import + assertEqual** / **`_MAIN_JS_FILES` import + assertEqual** (round 1 reviewer P1 反映)

## 7. Help-pop 文案 (最終形)

```
モデル分布の読み方
Assistant メッセージごとの model フィールドを family (opus / sonnet / haiku) にロールアップ
した、モデル別の負荷分担。メッセージ数では sonnet が workhorse、コストでは opus が
支配的になりがち — 両軸を併置することで、message-count では見えないコスト偏りを発見できる。
集計対象は assistant_usage event のみ (session_start / skill_tool / subagent invocation
は除外)。未知 model は sonnet family に寄せて集計する。
```

### 4-axis verification (load-bearing)

| 軸 | help-pop 文 verbatim | 実装 verbatim |
|----|----------------------|----------------|
| filter 条件 | 「`assistant_usage` event のみ」 | `aggregate_model_distribution` の `event_type == "assistant_usage"` filter |
| 集計式 | 「`model` フィールドを family にロールアップ」 | `infer_model_family(ev["model"])` |
| enum 値 | 「opus / sonnet / haiku」 | canonical order `["opus", "sonnet", "haiku"]` (Phase 5 で hard-coded pin) |
| fallback | 「未知 model は sonnet family に寄せる」 | `infer_model_family` の最後の `return "sonnet"` |

Comment #3 の「opus の単価は sonnet の約 5 倍」は **削除**。理由: `MODEL_PRICING` 現値 (opus-4-7 input $5 / sonnet-4-6 input $3) では 1.67x で、verbatim test が破綻する (= 4-axis verification 違反)。代わりに「コストでは opus が支配的になりがち」という定性表現に留める。

## 8. CSS additive scope (新ファイル不可 / 既存 token 改変不可)

**配置先**: `dashboard/template/styles/10_components.css` 1 ファイルに **集約** (round 1 reviewer P4 反映 / Issue 本文 verbatim 回帰)。理由 / 比較:

| 候補 | pros | cons | 採否 |
|------|------|------|------|
| (a) `10_components.css` 末尾追記 (採用) | Issue 本文「10_components.css 内の既存 `.stack-legend` の隣に minor extension で済むはず」と verbatim 整合 / Overview ページの一般 panel 系と semantically 自然な居場所 / 既存 `.stack-legend` rules (line 296〜) と隣接で legend 系 css 同居の readability up | 1 file の長さは増える | ✅ 採用 |
| (b) `55_sessions.css` 末尾追記 (旧案、撤回) | model color token (`m-opus` 等) が同 file にあって "near" | Sessions 用 file に Overview rule を入れる file 名 drift / model color は別 class (`s-opus` / `leg-opus`) なので "near" の strength は表面的 | ❌ 撤回 |
| (c) `40_patterns.css` 末尾追記 | heatmap 系視覚チャートと semantically 近い | heatmap rule と隣接で `.donut` / `.heatmap` 名前空間 risk、Issue 本文方針と乖離 | ❌ 不採用 |

| 既存 file | 追記内容 | 行数見込 |
|-----------|----------|----------|
| `dashboard/template/styles/10_components.css` | `.panel-head.c-rose .ttl .dot { background: var(--rose); }` 1 行 + `.axis-pair` grid / `.axis-head` / `.axis-center` / `.donut` SVG / `.donut-slice.s-{opus,sonnet,haiku}` / `.donut-callout` / `.model-legend` (header lowercase + canonical color、`leg-${family}` class hook) / `.donut-empty` | 50–70 |

`00_base.css` の token (`--coral` / `--mint` / `--peach` / `--rose`) は **無改変**。`_CSS_FILES` tuple (`server.py`) も無改変 (= 連結順 / sha256 logic に影響しない、Phase 5 RED の `test_css_files_unchanged` で structural pin)。

## 9. Risks / Tradeoffs

### R1. 単一 family 100% donut の視覚的 noise
- 受容済 (Q6 (a))
- 実 usage.jsonl の現実: 550 件すべて `claude-opus-4-7` → 開発者の自家 dogfooding だと **常に opus 100% donut** が見える
- mitigation: `axis-pair` の 2 軸を見せることで「全 100% でも message-count vs cost で同じ」 = 退屈ではあるが意味的に正しい
- 完全 zero (= empty events) のみ別 fallback (`.donut-empty` 1 本リング)

### R2. cost=0 event / 全 zero events の NaN guard
- `messages_total == 0` → `messages_pct = 0.0` (server 側で塞ぐ)
- `cost_total == 0` → `cost_pct = 0.0` (server 側で塞ぐ)
- frontend は `Math.round(pct * 100)` で OK (NaN を渡さない契約)
- Phase 1 RED test の `test_zero_cost_event_does_not_zero_div` で pin

### R3. donut start angle / canonical order の決定論性
- canonical order `["opus", "sonnet", "haiku"]` を **Python 側 / JS 側両方で hard-code**
- SVG `<circle transform="rotate(-90 cx cy)">` で 12 時起点を pin
- `stroke-dashoffset` を canonical 順で累積するので、slice 順 = canonical 順
- 視覚 snapshot (chrome-devtools MCP) で再現性を pin
- Phase 5 RED `test_canonical_order_hardcoded` + `test_buildDonutSvg_helper_via_node` で test-level pin

### R4. period toggle 切替で再集計
- `build_dashboard_data` は period_events_raw を渡すので period スイッチで `model_distribution` も再集計される
- Phase 2 RED `test_period_filter_applied` で pin
- frontend 側は SSE refresh 時に新 `data.model_distribution` を読むだけ (現行の `loadAndRender` 機構をそのまま使う)

### R5. SHA256 bump (template DOM 変更)
- 必須。shell.html に panel 追加 = 文字列変化 = sha256 不一致
- `tests/test_dashboard_template_split.py:28` の `EXPECTED_TEMPLATE_SHA256` を新 hash に bump (Phase 3 GREEN の一部)
- bump 値は `python3 -c "import hashlib, dashboard.server as d; print(hashlib.sha256(d._HTML_TEMPLATE.encode()).hexdigest())"` で実測
- **運用** (round 1 reviewer P5 advisory 反映):
  - 旧案「PR 直前に最終 hash 確定」だけだと、Phase 3 GREEN 後すぐに `test_dashboard_template_split` が RED で残ってしまい、Phase 4 / Phase 5 の TDD ループが回しにくい
  - **採用方針**: **Phase 3 GREEN / Phase 4 GREEN / Phase 5 GREEN の各 commit ごとに `EXPECTED_TEMPLATE_SHA256` を再計測 → bump** する。`shell.html` に手を入れた phase の commit に hash bump も同梱。これにより各 phase commit が自己完結 GREEN で残り、CI も clean
  - 代替: Phase 3-5 を **1 commit に固める** (= hash bump 1 回) 運用も可。`feature/106-...` ブランチ内の commit 数 trade-off で選ぶ判断 (本 plan は phase ごと commit を推奨、PR 内の review 単位を細かく保つため)
- **bisect note** (Round 2 reviewer Q3 反映): Phase 3 GREEN 単独 commit は「shell.html に panel DOM があるが Phase 4 renderer 未実装で空 panel 描画」中間状態になる。`git bisect` した将来の reader が「Phase 3 commit pickup で panel 空表示」を bug と誤認しないよう、(a) Phase 3-4 commit を **squash merge** で 1 commit に潰す、または (b) Phase 3 commit message に「intermediate: Phase 4 まで empty panel」と明記、のいずれかを Phase 3 開始時に決める

### R6. help-pop の「opus 5x」削除判断
- §7 で詳述。Comment #3 verbatim を採用すると 4-axis verification (= MODEL_PRICING との数値整合) が破綻
- 削除案を採用、定性表現に留める
- plan-reviewer round 1 で「Comment #3 verbatim 採用すべき」反対意見が出た場合は再検討する余地あり (反映ログに残す)
- **代替案** (round 1 reviewer Q3 反映、議論回し用): Comment #3 を維持しつつ verbatim 数値だけ `MODEL_PRICING` 整合の **`opus-4-7 vs sonnet-4-6` input 単価比 = 1.67x** に置き換える表現:
  > 「コストでは opus が支配的になりがち (現行価格表で opus-4-7 input は sonnet-4-6 の約 1.67 倍、output は約 1.67 倍)」
  この案を採れば Comment #3 の数値感は残せるが、`MODEL_PRICING` 改定で help-pop と価格表が drift する risk が残る (= 4-axis verification の数値レイヤを守る test の責任が増える)
- **本 plan の最終判断**: 削除案 (= 現行 §7 文案)。理由: (a) 価格表改定 drift risk を help-pop 側に背負わせない、(b) 定性表現でも UX 上の伝達ロスは小さい、(c) 数値を書くと「いつから何 x なのか」の世代依存が help-pop 文に紛れ込む

### R7. `infer_model_family` の重複 (Python 既存 helper 有無)
- `cost_metrics._get_pricing` は **rate 解決** (longest-prefix match) 用、family rollup ではない
- 既存 Python に family rollup helper は **無し** (grep 確認済)
- ∴ `cost_metrics.infer_model_family` を **新規作成** が妥当 (重複なし)
- JS 側 `inferModelFamily` (45_renderers_sessions.js:30) と semantics を 1:1 にする (substring match `opus` / `haiku` / `sonnet`)
- 注意: `cost_metrics._get_pricing` は **prefix match** (例: `claude-opus-4-` で startswith) だが、`infer_model_family` は **substring match** (例: model 文字列に `opus` がどこかに含まれていればよい)。これは前者が rate を取り違えると致命的 (例: `claude-opus-4` $15 vs `claude-opus-4-5` $5) に対し、後者は family を取り違える致命性が無い (どちらも opus family) ためで、両 helper の意味論が違うことを Phase 1 docstring + test で明示する
- **対比 test** (round 1 reviewer Q1 反映): Phase 1 RED の `TestPricingHelperSemanticsContrast` test class で `_get_pricing` (prefix match) と `infer_model_family` (substring match) を **同じ test class に並べる** ことで、両者の semantics が意図的に違うことを test レベルで文書化する

### R8. cross-aggregator drift (session_breakdown vs model_distribution)
- 同じ events / 同じ period で集計しているので **cost 合計は理論一致**
- ただし `session_breakdown` は top_n=20 cap、`model_distribution` は cap なし → 21 session 以上では発散
- Phase 2 RED `test_session_breakdown_total_matches_model_distribution_total` は **20 session 未満の fixture** で pin (= cap 内では一致する drift guard)
- **対偶 drift test** (round 1 reviewer Q2 反映): Phase 2 RED `test_session_breakdown_total_diverges_from_model_distribution_above_cap` で **21 session 以上の fixture を組んで、`Σ row.estimated_cost_usd < model_distribution.cost_total` (厳密 `<`) を pin**。これにより「cap 内一致 / cap 超過 mismatch」両側の drift guard が完全になる
- 21+ session 環境で UI に「Sessions ページの 4 KPI 合計」と「Overview 新パネル合計」が一致しない可能性は受容 (= cap 仕様の自然な帰結、Issue #103 と整合)。受容判断自体も上記対偶 test で load-bearing 化される

### R9. donut の SVG `stroke-dasharray` 技 vs canvas
- canvas 不使用 (= static export 経路 `render_static_html` と整合)
- pure SVG `<circle>` の `stroke-dasharray="(pct * C) C"` で円弧 1 本ずつ描く
- 弱点: 9% 以下の slice は線が短すぎてラベル不能 → callout 5% threshold で **0% < pct < 5% は legend のみ**, **pct == 0% は完全に slice 消滅** (stroke-dasharray=0 の degenerate)。0% の family は「3 行配列上には居るが視覚的には消える」運用

### R10. Renderer / CSS 配置の決定 (round 1 reviewer P3 / P4 反映)

旧案 (`45_renderers_sessions.js` 末尾追記 + `55_sessions.css` 末尾追記) は **撤回**。理由: Issue 本文が以下を verbatim 指定しているため、本文回帰が正解:

> Issue body 「Renderer」: `30_renderers_patterns.js` ではなく、Overview 専用の renderer (現状 Overview KPI / spark / project stack を担当している箇所と同居) で実装

→ "Overview KPI / spark / project stack を担当している箇所" は **`20_load_and_render.js`** (`loadAndRender()` の line 234-239 で `data.project_breakdown` を `<div id="stack">` に render 中)。Sessions file への同居は本文方針からの drift だった。

> Issue body 「CSS」: 新規 CSS は最小化 (`.stack` / `.stack-legend` を流用) ※ donut 採用で書き換え。`dashboard/template/styles/` の concat 順は既存通り、新ファイルは追加しない方針 (10_components.css 内の既存 `.stack-legend` の隣に minor extension で済むはず)

→ donut 採用で row 数は増えるが、**配置先 file (= `10_components.css`)** は本文 verbatim 指示を維持。`55_sessions.css` への同居は Sessions file の意味論的 drift を生むので不採用。

**最終配置**:
- Renderer: **`20_load_and_render.js` 末尾追記** (= Issue 本文回帰、`_MAIN_JS_FILES` tuple は無改変)
- CSS: **`10_components.css` 末尾追記** (= Issue 本文回帰、`_CSS_FILES` tuple は無改変)
- 同 file 末尾に **明示的 section comment** を入れる:
  ```js
  // ============================================================
  //  Overview "モデル分布" panel (Issue #106)
  //  ※ 新ファイル禁止のため 20_load_and_render.js 末尾に同居
  //     (Issue 本文「Overview KPI / spark / project stack を担当している箇所と同居」)
  // ============================================================
  ```
  ```css
  /* ============================================================
     Overview "モデル分布" panel (Issue #106 / donut grid + 共有 legend)
     Issue 本文「10_components.css 内の既存 .stack-legend の隣に minor extension」
     ============================================================ */
  ```
- 将来「scripts file 分割禁止」が緩和されたら別 file (例: `47_renderers_overview_extra.js`) に切り出す余地あり (今回は新ファイル禁止 hard rule に従う)

**`--rose` token 使用範囲の追記** (Round 2 reviewer P3 反映):
- `--rose: #ff6f9c` は `00_base.css:22` で定義済
- 現状の使用箇所: `10_components.css:253` の `.rank-row .meta .fail` (= ranking 行のフェイル件数 highlight) のみ
- panel-head 系 (`c-coral` / `c-peri` / `c-peach`) では未使用 → 本 plan で `c-rose` を新規導入しても token 意味の二重化なし
- Phase 5 RED 開始時に `grep -rn 'var(--rose)' dashboard/template/styles/` を再走させて使用範囲が変わっていないことを確認する step を入れる

## 10. Out of Scope (Issue 本文 🚫 セクション継承)

- モデル別の **時系列推移** (= 日別 / 週別の opus 比率 trend line) → 別 issue
- **per-project breakdown** (= プロジェクト × モデル の cross-tab) → 別 issue
- **subagent token を別 model 扱い** する設計 (= 現状は subagent invocation 内の `assistant_usage` も main session と同じ family rollup に集計される) → 別 issue
- raw model 名での breakdown (例: `claude-opus-4-7` vs `claude-opus-4-6` の世代別) → 別 issue (本 plan は family rollup のみ)
- cost 推計の精度向上 (1h cache write / inference_geo 1.1x) → cost-calculation-design.md の既知 limitation、別 issue
