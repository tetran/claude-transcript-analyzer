# Issue #128 — Claude Fable 対応 実装計画

## 📋 plan-reviewer 反映ログ

| Proposal | 内容 | 反映箇所 |
|---|---|---|
| (初稿) | — | — |

### 二次レビュー反映 (iteration 1 の指摘)

| Proposal | 内容 | 反映箇所 |
|---|---|---|
| P1 (actionable) | dashboard-api.md の変更対象を「優先順記述更新」と総称せず、書き換える literal 文字列 3 箇所 + 例 JSON を行番号付きで列挙 | Phase 5 (dashboard-api.md 項)/ §2-3 表 |
| P2 (actionable) | `claude-opus-4-8[1m]` (実データ ×7) は suffix 正規化と prefix collision 解決の**両方**を同時に通る唯一の合成パス — 専用テストを追加 | Phase 1-A テスト一覧 / §6 リスク表 ([1m] 行) |
| P3 (advisory) | 既存 3-row Node fixture の扱いを明示: legend canonical 順テストは 4-row fixture に upgrade して `i_fable < i_opus < i_sonnet < i_haiku` を positive assert、3-row fixture は pad regression 用に 1 本だけ意図的に残す | Phase 3-A / §5 アサーション一覧表 |
| P4 (advisory) | 「`[` は context-window マーカー予約で base と同価格」という不変条件を plan 内だけでなくコードコメント + cost-calculation-design.md に永続化 | Phase 1-B (_get_pricing コメント) / Phase 5 (cost-calculation-design.md 項) |

### 三次レビュー反映 (iteration 2 の指摘 — actionable ゼロ、advisory 1 件のみ)

| Proposal | 内容 | 反映箇所 |
|---|---|---|
| P1 (advisory) | dashboard-api.md 968-972 の「3 行」token は 2 箇所でなく **3 箇所**(971 行 `…のときも 3 行` は `必ず` を含まない別フレーズで find-replace 漏れリスク)。3 edit を個別列挙 + 945 行の backtick 装飾維持を明記 | Phase 5 (dashboard-api.md 968-972 項) / 同 945 行注記 |

## 1. ゴール

新モデル **Claude Fable 5** (`claude-fable-5`、1M context 変種 `claude-fable-5[1m]`) を
コスト計算・モデル分布・Sessions UI の全レイヤーで第一級サポートする。
あわせて、調査で見つかった隣接バグ — **`claude-opus-4-8` の価格表未登録**
(`claude-opus-4` $15/$75 に prefix 誤マッチ、3 倍過大計上) と
**既存 `*[1m]` 変種の sonnet fallback 誤計上** — も本 issue のスコープとして修正する
(issue 本文「新しいモデルに対応する」の自然な範囲としてユーザー承認済み)。

- **価格表 (Fable 5)**: input $10 / output $50 / cache read $1 / 5m cache write $12.50 (per MTok)。
  公式 docs (platform.claude.com/docs/en/about-claude/pricing) を 2026-06-11 に pin。
  1h cache write は $20 だが、既存方針どおり 5m レートのみ採用 (cost.py docstring の既知 limitation 踏襲)。
- **価格表 (Opus 4.8)**: input $5 / output $25 / cache read $0.50 / 5m cache write $6.25 (同日 pin。4.7/4.6 と同額)。
- **第 4 の model family `'fable'`** を追加 (確定仕様)。canonical 順は価格帯降順で
  **fable → opus → sonnet → haiku** に変更 (確定仕様)。
- **表示色**: 新規 lavender 系 CSS 変数 (coral / mint / peach と区別可能な紫系) (確定仕様)。
- 実データ根拠: `~/.claude/transcript-analyzer/usage.jsonl` に
  `claude-fable-5` × 24 / `claude-fable-5[1m]` × 1 / `claude-opus-4-7[1m]` × 46 /
  `claude-opus-4-8[1m]` × 7 / `claude-sonnet-4-6[1m]` × 1 が存在 (2026-06-11 確認)。

## 2. 探索で確定した重要事実 (実装の前提)

### 2-1. `[1m]` suffix は prefix match に**乗らない** → `_get_pricing` で suffix 正規化する

`_get_pricing` (analyzer/cost.py:101-120) は exact match → token-boundary prefix match の 2 段:

```python
matches = [p for p in MODEL_PRICING if model.startswith(p + "-")]
```

`"claude-fable-5[1m]"` は `"claude-fable-5-"` で始まらないため、
base ID を登録しただけでは **DEFAULT_PRICING (sonnet $3/$15) に fallback してしまう**
(cost.py:111-119 現物確認済み)。

**対応方針: `_get_pricing` の冒頭で `[` 以降の suffix を落として正規化する**:

```python
base = model.split("[", 1)[0]  # "claude-fable-5[1m]" → "claude-fable-5"
```

- `[1m]` は「1M context window 利用」のマーカーであり別モデルではない。公式 pricing で
  Fable 5 / Opus 4.8 / 4.7 / 4.6 / Sonnet 4.6 の 1M context は **標準料金** (long-context
  プレミアムなし) と確認済みのため、base と同価格に解決するのが正しい。
- 変種ごとの exact key 重複登録 (代替案) は今後モデル追加のたびに 2 entry 必要になり
  登録漏れ事故 (今回の opus-4-7[1m] がまさにそれ) を再生産するため採らない。
- `infer_model_family` は substring match なので `[1m]` 形は既にカバーされる (テストで pin のみ)。

### 2-2. `claude-opus-4-8` は未登録のため `claude-opus-4` ($15) に誤マッチ中

`"claude-opus-4-8".startswith("claude-opus-4" + "-")` が真のため、longest-prefix で
`claude-opus-4` ($15/$75) が当たる。`"claude-opus-4-8"` を exact 登録して解消する
($5/$25。prefix collision は longest match wins なので date-suffix 形も正しく解決)。

### 2-3. 変更箇所の現物 (2026-06-11 時点の行番号)

| 箇所 | ファイル:行 | 現状 |
|---|---|---|
| 価格表 dict | `analyzer/cost.py:78-96` | `MODEL_PRICING` (Fable / opus-4-8 なし) |
| docstring 価格表 | `analyzer/cost.py:16-31` | pin 日 2026-05-06 |
| 価格解決 | `analyzer/cost.py:101-120` | `_get_pricing` (exact → `-` boundary prefix) |
| canonical 順 | `analyzer/cost.py:145` | `_FAMILY_CANONICAL_ORDER = ("opus", "sonnet", "haiku")` |
| family 推論 (Py) | `analyzer/cost.py:148-168` | `infer_model_family` — opus → haiku → sonnet 優先 |
| 分布集計 | `analyzer/cost.py:171-231` | docstring に「常に 3 行」「opus → sonnet → haiku」 |
| family 推論 (JS) | `dashboard/template/scripts/45_renderers_sessions.js:31-37` | Python と 1:1 契約 |
| donut canonical 順 | `dashboard/template/scripts/20_load_and_render.js:326` | `const FAMILIES = ['opus', 'sonnet', 'haiku'];` (322-323 のコメントも) |
| donut slice 色 | `dashboard/template/styles/10_components.css:469-471` | `.donut-slice.s-{opus,sonnet,haiku}` |
| callout 色 | `dashboard/template/styles/10_components.css:520-522` | `.donut-callout.c-{opus,sonnet,haiku}` |
| legend dot 色 | `dashboard/template/styles/10_components.css:578-580` | `.leg-{opus,sonnet,haiku} .leg-dot` |
| model chip 色 | `dashboard/template/styles/55_sessions.css:132-135` | `.model-chip.m-{sonnet,opus,haiku}` |
| 色 token 定義 | `dashboard/template/styles/00_base.css:14-23` | `--mint/--coral/--peach/--rose` 等 (lavender なし) |
| help テキスト | `dashboard/template/shell.html:137, 146` | コメント・help-pop 双方に「opus / sonnet / haiku」列挙 |
| API spec | `docs/spec/dashboard-api.md:917-988` | 「常に 3 行」「opus → sonnet → haiku」明文化 |

shell.html の help-pop (146 行) は「family (opus / sonnet / haiku) にロールアップ」と
実装への claim を述べているため、**4 family 化に合わせて必ず更新する**。
`dashboard-wording` skill の規約に従って文言を当てる。

## 3. スコープ / 非スコープ

### スコープ
1. `_get_pricing` に `[` suffix 正規化を追加 (既存 `*[1m]` 変種の誤計上も同時解消)
2. `MODEL_PRICING` に `claude-fable-5` / `claude-opus-4-8` を追加 (+docstring 表・pin 日追記)
3. `infer_model_family` (Py/JS 両方) に `fable` 分岐追加 — 優先順は **fable → opus → haiku → sonnet**
4. `_FAMILY_CANONICAL_ORDER` / JS `FAMILIES` を `fable → opus → sonnet → haiku` の 4 要素に
5. CSS: `--lavender` 系 token 新設 + `s-fable` / `c-fable` / `leg-fable` / `m-fable` フック
6. shell.html help テキスト・コメントの family 列挙更新
7. 既存テストの 3-family 前提の書き換え + Fable / opus-4-8 / `[1m]` 用テスト追加 (各 Phase で test first)
8. docs (cost-calculation-design.md / dashboard-api.md) 更新、memory 追記

### 非スコープ (やらない)
- 未知 model → sonnet fallback 方針の変更 (`DEFAULT_PRICING` は sonnet-4-6 のまま)
- claude-mythos-5 等、他モデルの追加
- 1h cache write レートの schema 拡張 (既存 limitation のまま)
- Sessions KPI「うち opus セッション」の意味変更 (opus 固定のまま)
- `feature/128-fable-support` ブランチ作成 (実装フェーズの冒頭で行う。本計画の手順外)

## 4. フェーズ構成 (各フェーズ: 失敗するテストを先に書く → 実装 → GREEN)

### Phase 1 — 価格解決 (`_get_pricing` 正規化 + `MODEL_PRICING` 追加)

**1-A. テスト先行** — `tests/test_cost_metrics.py` に追加 (全部 RED を確認):
- `test_fable_5_input_only`: `calculate_message_cost("claude-fable-5", 1_000_000, 0, 0, 0) == 10.0`
- `test_fable_5_all_dimensions`: 1M each × (10 + 50 + 1 + 12.5) = **73.5**
- `test_fable_5_1m_suffix_priced_as_fable`: `calculate_message_cost("claude-fable-5[1m]", 1_000_000, 0, 0, 0) == 10.0`
  — §2-1 のとおり base 登録だけでは RED。suffix 正規化で GREEN にする (本 issue の急所)
- `test_opus_4_8_priced_as_opus_4_8_not_opus_4`: `calculate_message_cost("claude-opus-4-8", 1_000_000, 0, 0, 0) == 5.0`
  (現状 15.0 になる RED を確認 — §2-2 の誤マッチの再現テスト)
- `test_opus_4_7_1m_suffix_priced_as_opus_4_7`: `"claude-opus-4-7[1m]"` → 5.0
- `test_opus_4_8_1m_suffix_priced_as_opus_4_8`: `"claude-opus-4-8[1m]"` → 5.0
  — **suffix 正規化 → prefix collision 解決 (opus-4 vs opus-4-8) の両修正を同時に通る唯一の合成パス**
  (実データ ×7 件)。どちらかの順序が壊れると 15.0 か 3.0 になるため regression 検出力が最も高い
- `test_sonnet_4_6_1m_suffix_priced_as_sonnet_4_6`: `"claude-sonnet-4-6[1m]"` → 3.0
  (fallback と同額のため `_get_pricing` 直叩きで identity を assert する等、fallback 経由でないことを区別する)
- `test_fable_5_does_not_fallback_to_sonnet`: fable と `claude-future-99-x` のコストが不一致
- `test_unknown_model_with_1m_suffix_still_falls_back`: `"claude-unknown[1m]"` → DEFAULT_PRICING
  (正規化が fallback 方針を壊さないことを pin)

**1-B. 実装** — `analyzer/cost.py`:
- `_get_pricing` 冒頭に正規化を追加:
  ```python
  model = model.split("[", 1)[0]  # "[1m]" 等の context-window suffix は別モデルではない
  ```
  docstring に「`[1m]` suffix は `-` boundary prefix match に乗らないため正規化で吸収」を追記。
  あわせて「**`[` は context-window マーカー予約**であり base と同価格に解決する。
  `[...]` 変種が premium 価格を持つモデルが現れたら再設計」の不変条件をコードコメントとして残す
- `MODEL_PRICING` に追加:
  ```python
  "claude-fable-5":    ModelPricing(input=10.00, output=50.00, cache_read=1.00, cache_creation=12.50),
  "claude-opus-4-8":   ModelPricing(input=5.00,  output=25.00, cache_read=0.50, cache_creation=6.25),
  ```
- docstring 表 (16-31 行) に Fable / Opus 4.8 行追加 + 「2026-06-11 に同 URL から pin」追記
- 1h cache write $20 は採用しない旨を既知 limitation (41-44 行) の文脈で一言補足

### Phase 2 — family 推論と分布集計 (Python)

**2-A. テスト先行** — `tests/test_model_distribution.py`:
- `TestInferModelFamily` 追加: `"claude-fable-5"` → `"fable"`、`"claude-fable-5[1m]"` → `"fable"`
- `TestPricingHelperSemanticsContrast` 追加: `infer_model_family("fable-opus-mix") == "fable"`
  (優先順 fable 最優先を pin、既存の opus 優先テストとの対比)
- `TestAggregateModelDistribution` 書き換え/追加:
  - `test_returns_three_rows_with_canonical_order` → `test_returns_four_rows_with_canonical_order`
    (期待値 `["fable", "opus", "sonnet", "haiku"]`)
  - `test_empty_events_returns_three_zero_rows` → 4 行版に改名・書き換え
  - 新規: fable 1M output → fable 行 `cost_usd == 50.0`
- `TestBuildDashboardDataModelDistribution`:
  - `test_shape_has_families_and_totals` の `len(md["families"]) == 3` → `4` (現 229 行)
  - `test_empty_events_yields_three_zero_rows` の `len == 3` → `4` (現 319 行)
- ファイル/クラス docstring の「3-row」表記も更新

**2-B. 実装** — `analyzer/cost.py`:
- `_FAMILY_CANONICAL_ORDER = ("fable", "opus", "sonnet", "haiku")`
- `infer_model_family` 冒頭に `if "fable" in m: return "fable"` を追加し、
  docstring の優先順記述を fable → opus → haiku → sonnet に更新 (JS との 1:1 契約も明記)
- `aggregate_model_distribution` docstring の「常に 3 行」「(sonnet, haiku 同形)」を 4 行版に更新

### Phase 3 — JS renderer (Python と 1:1 同期)

**3-A. テスト先行**:
- `tests/test_dashboard_sessions_ui.py`:
  - `test_infer_model_family_fable`: `inferModelFamily('claude-fable-5')` → `'fable'`
  - `test_infer_model_family_fable_1m_suffix`: `'claude-fable-5[1m]'` → `'fable'`
  - `test_build_model_chips_fable`: `buildModelChips({'claude-fable-5': 3})` に `class="model-chip m-fable"`
- `tests/test_model_distribution_template.py`:
  - `test_canonical_order_hardcoded` (現 207-212 行) の regex を 4 要素
    `['fable','opus','sonnet','haiku']` に書き換え
  - Node round-trip: 4-family fixture で `buildDonutSvg` が `s-fable` を出すこと、
    `buildLegendHtml` が `leg-fable` を出し順序が fable < opus < sonnet < haiku であること
  - **既存 3-row fixture の扱い (意図的なカバレッジ判断)**:
    - `test_buildLegendHtml_uses_canonical_order` (現 296-309 行) は **4-row fixture に upgrade** し
      `i_fable < i_opus < i_sonnet < i_haiku` を positive assert する
      (3-row のままだと fable が silent pad で素通りし、canonical 順テストが第 4 family を
      カバーしない「静かなカバレッジ縮小」になるため)
    - **後方互換 regression**: fable を含まない 3-family 配列の fixture を **1 本だけ意図的に残し**、
      crash せず fable がゼロ行 pad される (renderer の find-or-default 挙動) を pin する
      (live 更新中に古い 3 行レスポンスを受ける瞬間の安全性)

**3-B. 実装**:
- `45_renderers_sessions.js:31-37` `inferModelFamily`: `if (m.indexOf('fable') !== -1) return 'fable';`
  を opus check より前に追加 (Python と同順)
- `20_load_and_render.js:326` `const FAMILIES = ['fable', 'opus', 'sonnet', 'haiku'];`
  + 322-323 行の「canonical 順 ['opus', 'sonnet', 'haiku']」コメント更新

### Phase 4 — CSS + shell.html help テキスト

**4-A. テスト先行** — `tests/test_model_distribution_template.py`:
- `TestModelDistCss` 追加:
  - `.donut-slice.s-fable` が `var(--lavender)` を使う (既存 s-opus/coral テストと同形)
  - `.leg-fable` が `var(--lavender)`、`.donut-callout.c-fable` 定義あり
  - `00_base.css` に `--lavender:` 定義がある
- `TestModelDistPanelDOM.test_help_pop_body_contains_filter_terms` (現 93-95 行) に
  `assertIn("fable", body)` を追加
- (sessions 側) `55_sessions.css` の `.model-chip.m-fable` を grep pin するテストを
  `test_dashboard_sessions_ui.py` か template テストに追加

**4-B. 実装**:
- `00_base.css:14-23` の token 群に追加 (既存 `--rose` の下):
  ```css
  --lavender: #b9a8ff;
  --lavender-soft: #5d5380;
  ```
  (#b9a8ff は coral #ff8a76 / mint #6fe3c8 / peach #ffc97a / peri #8aa6ff と判別可能な紫系。
  実装時に dark 背景上でのコントラストを目視確認し微調整可)
- `10_components.css`: 469-471 / 520-522 / 578-580 の各ブロック先頭に fable 行追加
  (例: `.donut-slice.s-fable { stroke: var(--lavender); }`)
- `55_sessions.css:132-135`: `.model-chip.m-fable { background: rgba(185, 168, 255, 0.10); color: var(--lavender); }`
  + 132 行のコメントに fable=lavender(top-tier) を追記
- `shell.html`:
  - 137 行コメント: `(opus / sonnet / haiku の…)` → `(fable / opus / sonnet / haiku の…)`
  - 146 行 help-pop: 「family (fable / opus / sonnet / haiku) にロールアップ」に更新。
    「メッセージ数では sonnet が workhorse、コストでは opus が支配的」の傾向 claim は
    fable 追加後も成り立つか実データと突き合わせて再検証 (dashboard-wording skill 準拠)。
    「未知 model は sonnet family に寄せて集計する」は方針不変のためそのまま

### Phase 5 — docs / memory

- `docs/reference/cost-calculation-design.md`: 価格表に Fable 5 / Opus 4.8 行追加 (pin 日 2026-06-11)。
  `[1m]` suffix 正規化の設計判断を追記 — 「`[...]` suffix は context-window マーカーであり
  base と同価格に解決する前提 (公式 pricing で 1M context は標準料金と確認済)。
  `[...]` 変種が premium 価格を持つモデルが現れたら再設計する」を不変条件として明文化。
  未知 model fallback 設計は変更なしであることを明記
- `docs/spec/dashboard-api.md:917-988`: `model_distribution` 契約を 4 行に更新。
  **書き換える literal 文字列を明示** (spec は実装への verbatim claim のため、総称的な
  「記述更新」で済ませず以下を個別に patch する):
  - **928-932 行 例 JSON**: fable 行を先頭に追加 (4 行構成に)
  - **945 行**: `substring match opus → haiku → sonnet の優先順` →
    `substring match fable → opus → haiku → sonnet の優先順`
  - **957 行**: `常に opus → sonnet → haiku の固定順` →
    `常に fable → opus → sonnet → haiku の固定順`
  - **968-972 行**: 「3 行」token は **3 箇所** — ① 968 行 見出し `### 常に 3 行` → `### 常に 4 行`、
    ② 970 行 `必ず 3 行` → `必ず 4 行`、③ 971 行 `完全空 events のときも 3 行` → `…のときも 4 行`
    (③ は `必ず` を含まない別フレーズのため、`必ず 3 行` の find-replace では漏れる点に注意)。
    945 行の patch 時は原文の backtick 装飾 (`` `fable` `` 等) を維持する
- `docs/spec/usage-jsonl-events.md`: model 例示のみで family 列挙なし (grep 確認済) — 変更不要。
  ただし最終確認で再 grep
- memory: 新規 gotcha「`[1m]` suffix は `_get_pricing` の `-` boundary prefix match に乗らない
  (suffix 正規化で吸収)。prefix collision (opus-4 vs opus-4-8) は新モデル登録漏れで再発する」を
  memory ファイルに追記 + `MEMORY.md` に 1 行ポインタ

## 5. 3-family → 4-family で変更が必要な既存アサーション一覧

| ファイル | テスト / 行 | 変更 |
|---|---|---|
| `tests/test_model_distribution.py` | `test_returns_three_rows_with_canonical_order` (63-66) | 4 行 + `["fable","opus","sonnet","haiku"]` に改名・書き換え |
| 〃 | `test_empty_events_returns_three_zero_rows` (112-121) | `len == 4` に改名・書き換え |
| 〃 | `TestBuildDashboardDataModelDistribution.test_shape_has_families_and_totals` (229) | `len(md["families"]) == 4` |
| 〃 | `test_empty_events_yields_three_zero_rows` (316-321) | `len == 4` |
| 〃 | module/class docstring (5, 26, 51) | 「3-row」「'opus'/'sonnet'/'haiku'」表記更新 |
| `tests/test_model_distribution_template.py` | `test_canonical_order_hardcoded` (207-212) | regex を 4 要素配列に |
| 〃 | `test_help_pop_body_contains_filter_terms` (82-98) | `assertIn("fable", body)` 追加 |
| 〃 | `test_buildLegendHtml_uses_canonical_order` (296-309) | 4-row fixture に upgrade し `i_fable < i_opus < i_sonnet < i_haiku` を positive assert |
| 〃 | Node fixtures 各所 (233 ほか) | 4 行 fixture へ更新 + 3 行入力の pad regression を**意図的に 1 本だけ**残す |
| `tests/test_cost_metrics.py` | `TestUnknownModelFallback` | 不変 (fallback 方針継続)。`[1m]` 付き未知 model の fallback テストを追加 |
| `tests/test_dashboard_sessions_ui.py` | — | 既存変更なし (fable テスト追加のみ) |

## 6. リスク / トレードオフ

| リスク | 影響 | 対策 |
|---|---|---|
| `[1m]` 正規化の副作用 (将来 `[fast]` 等の別 suffix が別価格になるケース) | suffix 一律同価格の仮定が崩れる可能性 | 現時点の公式 pricing では context-window 変種は標準料金。不変条件をコードコメント + cost-calculation-design.md に明記し、別価格 suffix が現れたら再設計する旨を残す。正規化×collision 解決の合成パスは `test_opus_4_8_1m_suffix_priced_as_opus_4_8` で pin |
| `claude-opus-4-8` 登録による**既存データのコスト表示変化** ($15 → $5 で過去 cost が下がる) | ダッシュボードの過去コスト値が変わり「数字が動いた」ように見える | raw token 保存 + 計算時価格適用の既存設計 (cost-calculation-design.md) どおりの正しい挙動。docs にこの修正で過去表示が変わることを明記 |
| Py/JS の `inferModelFamily` 優先順 drift | donut と chips で family が食い違う | 両側に同一 fixture のテスト (semantics contrast + sessions UI test) |
| canonical 順変更で donut slice / legend / API の 3 軸が非同期になる | 視覚 snapshot flaky / consumer 破壊 | `test_canonical_order_hardcoded` + legend 順テスト + dashboard-api.md 同時更新 |
| 古い API レスポンス (3 行) を新 renderer が受ける瞬間 (live 更新中など) | renderer crash | find-or-default の pad 挙動を regression テストで pin (Phase 3-A) |
| lavender が既存 `--peri` (#8aa6ff) と紛らわしい | 判読性低下 | #b9a8ff を起点に視覚 smoke で確認、必要なら彩度調整 (テストは `var(--lavender)` 参照のみ pin し hex は固定しない) |
| 価格表 pin 日が混在 (2026-05-06 / 2026-06-11) | 出典追跡が曖昧に | docstring に Fable / Opus 4.8 行の pin 日を個別注記 |
| help-pop の傾向 claim (「sonnet が workhorse」) が fable 時代に不正確化 | help テキストが実装への誤った claim になる | Phase 4 で実データと突き合わせて文言再検証 (dashboard-wording skill) |

## 7. 検証

1. **フルテストスイート**: リポジトリ root で `python3 -m pytest tests/ -x -q`
   (node 必須テストを含むため node 利用可能環境で実施)。全 GREEN を確認
2. **実データでの視覚 smoke**:
   - dashboard を起動し実 `usage.jsonl` (fable 25 件入り) を表示
   - Overview「モデル分布」: donut に lavender の fable slice、legend 先頭行が
     fable / msgs / cost (4 桁 $)、callout (5% 以上なら) が出る
   - Sessions: fable を使った session の model chips に lavender の `fable` chip
   - `?` help-pop を開き「fable / opus / sonnet / haiku」表記を目視
   - period toggle (7d / all) で fable 行が period 連動することを確認
3. **コスト spot-check**: fable event 1 件の token を取り、手計算
   (`in/1M×10 + out/1M×50 + cr/1M×1 + cc/1M×12.5`) と Sessions の estimated cost が 4 桁内で一致。
   あわせて opus-4-8 event 1 件が $5 レートで計上されることも確認
4. **ドキュメント整合**: dashboard-api.md の例 JSON と実 `/api/data` レスポンスの形を突き合わせ
5. 全ファイル末尾の空行 (blank line) 規約を確認
