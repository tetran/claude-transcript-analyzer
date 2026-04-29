# Issue #69 — Live ダッシュボード差分ハイライト + 更新概要 toast

## 🎯 Goal & Scope

Issue 本文（Light variant）の 2 intent:

1. ライブ更新時、**変化した数字（KPI tile / lede / ranking row count）を視覚的に目立たせる**
2. **更新概要を toast** で短時間表示する

→ ユーザーが画面を見ているときに「いま何が伸びたか」が一瞥でわかり、長いセッション中の「いつの間にか数字が動いていた」状態を解消する。

### Scope

- **対象数字**: Overview ページのみ。具体的には KPI tiles 8 種 (`kpi-total` / `kpi-skills` / `kpi-subs` / `kpi-projs` / `kpi-sess` / `kpi-resume` / `kpi-compact` / `kpi-perm`) + lede 数字 3 種 (`ledeEvents` / `ledeDays` / `ledeProjects`) + ranking rows の name 単位 count (skill_ranking / subagent_ranking)
- **動作経路**: live mode (`EventSource` 経由 `/events` refresh) のみ。静的 export (`window.__DATA__` 経由) では highlight も toast も発火しない（diff 比較相手が無い + UX 上 noise）
- **Toast 内容**: 1 行 aggregate (`+5 events · +1 skill · +2 subagent invocations`)。複数 SSE refresh が連発したときは最新 toast で置換 (queue しない)
- **First-render**: 初回 `loadAndRender()` は `prevSnapshot === null` の状態を構造的に保ち、highlight も toast も**出さない**

### Out of scope (本 PR の境界)

- Patterns / Quality / Surface タブの数字 highlight（タブ非表示で highlight しても見えず、見える化された toast に「裏で +10 events」と書いても scope が膨れて UX 検証が難しい）
- Toast の click-to-dismiss / hover で持続 / 履歴 / 通知音
- Sparkline / hourly heatmap / cooccurrence / failure trend / compact density / percentile / permission breakdown / surface 3 panel の cell-level 強調（まずは「視野 1 で見える数字」に focus し、視覚 noise を最小化）
- **減少**の強調（KPI 数値が減るケースは retention や archive 化のタイミング以外ほぼ無く、本番 UX ノイズになる）。`delta > 0` のみ highlight。delta=0 (= 不変) も highlight しない
- 静的 export での再現

## 📐 設計概要 — 主要設計判断

### D1. Diff の単位 — 「key → 前回値」 Map を closure 内に保持

選択: **closure 内の `prevSnapshot` Map** を `loadAndRender()` の外側に置き、毎回の render 末尾で「次回比較用の next snapshot」に置き換える。

理由: render 関数は副作用 (DOM 完全置換) を持つ既存設計。**diff を render 内に閉じ込める**設計だと「render → DOM 置換 → diff 取り → highlight class 付与」を同期で書ける。「前回値を返す flow」設計より既存コードへの侵襲が小さい。`window.__DATA__` 経由 (static export) では `prevSnapshot` を初期化しない or 常に `null` にし、highlight 経路に入らない。

snapshot schema (内部 JS object):
```js
{
  kpi: { 'kpi-total': 1234, 'kpi-skills': 8, ... },   // 8 entries
  lede: { ledeEvents: 1234, ledeDays: 12, ledeProjects: 3 },
  rankSkill:  Map<name, count>,    // skill_ranking name 単位
  rankSub:    Map<name, count>,    // subagent_ranking name 単位
}
```

#### concat 順序 / TDZ 安全性根拠

shell.html は全 main_js を `(async function(){ __INCLUDE_MAIN_JS__ })();` で wrap している (= **単一の async IIFE**)。ES module ではないため hoisting/TDZ は **同一 IIFE 内の lexical scoping** に基づく。

`_MAIN_JS_FILES` 並びは `00 → 10 → 20 → 25 → 30 → ...`。`let __livePrev = null` は **25 番ファイル冒頭 (IIFE 直下)** に置き、20 番ファイル内の `loadAndRender()` の **関数 body** からのみ参照する。

- 20 番の IIFE 直下 (関数定義の外側) には `__livePrev` の **宣言文 (`let / var / const __livePrev`) を置かない** (declaration-only check で grep 可能)
- `loadAndRender()` は async 関数で実行時に呼ばれるため、その時点では 25 番の `let` は評価済み → ReferenceError は構造的に発生しない
- Phase 1-a の literal pin で 2 つを保証:
  - `test_load_render_does_not_redeclare_liveprev` — 20 番に `let __livePrev` / `var __livePrev` / `const __livePrev` の **宣言文が無い** (grep で簡潔に確認可能 — JS parser 不要)
  - `test_25_declares_liveprev_at_iife_top` — 25 番冒頭 (関数定義より前) に `let __livePrev` 宣言が存在

### D2. Highlight 表現 — 背景色 pulse (1.5s, ease-out, fade)

選択: **CSS `@keyframes pulse-bg`** で 1.5 秒で fade out する mint-tinted 背景パルス。ranking row は同 keyframe を `.rank-row.bumped` で適用、KPI tile は `.kpi.bumped`、lede 数字は `.num.bumped`。

理由:
- border flash は KPI tile の `::before` (top accent line) と競合して見にくい
- scale bump (transform) は隣接 KPI を押しのけて layout shift を生む。dashboard の 8-col grid は密で許容できない
- 背景 pulse は `box-shadow: inset 0 0 0 transparent → mint @ 0.18 → transparent` の単一プロパティアニメで layout-safe
- **`prefers-reduced-motion: reduce`** では keyframe を無効化し、代わりに 1.5s 後に消える静的 outline のみ残す（toast の slide-in も同条件で無効化）

class 付与 → 1500ms 後に `classList.remove('bumped')` する `setTimeout`。複数連続の SSE refresh で同要素が再 bump するときは `void el.offsetWidth` で animation を再起動する。

### D3. Toast 表現 — 1 行 aggregate / top-right / 4s 自動消滅

選択: **画面右上に固定 (position: fixed; top: 14px; right: 14px)** な単一 toast 要素。SSE refresh ごとに**置換** (queue しない)。

形式（例）:
```
+12 events · +1 skill · +2 subagent · +3 sessions
```

ルール:
- delta > 0 のフィールドだけ列挙、最大 4 セグメント
- 順序固定: events → skills → subagent → sessions → projects → compact → permission（KPI 並び順から `kpi-resume` を除外し、後述 LABEL テーブルで toast 対象を絞る）。**この順序が priority order を兼ねる** — 5 種以上 delta が同時発火したときは先頭 4 セグメントが残る (Phase 1-b の `test_format_toast_summary_caps_at_four_segments` が LABEL テーブル順を前提に pin)
- 全 delta = 0 のときは toast を出さない（無音 refresh = ファイル mtime touch だけ等）
- `prefers-reduced-motion: reduce` のときは slide-in を opacity fade のみに簡略化
- `aria-live="polite"` + `role="status"` でスクリーンリーダーにも届ける（ただし日本語化は既存 dashboard の慣習に従い formatter で英語アグリゲートのまま提示）

要素は shell.html に **`<div class="toast" id="liveToast" role="status" aria-live="polite" aria-atomic="true" hidden></div>`** を追加（footer の直前に置き z-order top）。

#### Toast LABEL テーブル — 唯一の真実源

各 KPI に対する toast セグメントの**ラベル文言（singular / plural）**、**delta 単位**、**toast 対象に含めるか**を pin する。実装と test の唯一の真実源。

| KPI id | label (singular / plural) | delta 単位 | toast 対象? | 備考 |
|---|---|---|---|---|
| `kpi-total` | event / events | int | ○ | events 増分 |
| `kpi-skills` | skill / skills | int | ○ | skill kind 数の増分 (新登場 skill 観測時のみ立つ) |
| `kpi-subs` | subagent invocation / subagent invocations | int | ○ | 新 subagent kind 数の増分 |
| `kpi-sess` | session / sessions | int | ○ | session 数の増分 |
| `kpi-projs` | project / projects | int | ○ | project (cwd 単位) 数の増分 |
| `kpi-resume` | — | ratio | × | ratio 値で加算 delta が UX 上意味不明 → toast 除外 (highlight のみ) |
| `kpi-compact` | compaction / compactions | int | ○ | PreCompact 増分 |
| `kpi-perm` | permission / permissions | int | ○ | permission prompt 増分 |

ranking row (`rankSkill` / `rankSub`) と lede (`ledeEvents` / `ledeDays` / `ledeProjects`) は**toast には載せず highlight のみ**（toast に「+5 lede events」「+1 codex-review use」を入れると KPI 数値と二重カウントで読みにくい）。

`prefers-reduced-motion` 配慮や aria 属性は toast 表示の有無にかかわらず適用。LABEL テーブルから外れた KPI (`kpi-resume`) も**highlight 経路は走る** (= 数字が動いたらパルスは出る)。

### D4. Diff 比較 scope — Overview のみ（minimal 推奨）

選択: KPI 8 + lede 3 + ranking rows (skill / subagent) のみ。

理由:
- Patterns / Quality / Surface はタブ非アクティブ時に `hidden` で見えない。highlight 付与しても無意味
- Toast に「+1 hourly bucket」「+2 cooccurrence pairs」を入れても understanding が伴わず UX noise
- 後続 Issue で「Patterns タブにいるときは Patterns の数字も拾う」拡張は active page 検知で素直に追加可能（`document.querySelector('section.page:not([hidden])').dataset.page`）。今回は構造を「key → value Map」設計にして将来拡張余地だけ残す

### D5. First-render / reconnect 直後の扱い

選択: `prevSnapshot = null` のときは highlight / toast 経路を完全 skip。`loadAndRender()` の末尾で `prevSnapshot = computeNextSnapshot(data)` を必ず実行することで、**初回 render が「次回の prev」になる**。

reconnect (SSE 再接続) は EventSource の自動再接続で `loadAndRender()` 自体が呼ばれないため特別扱い不要。ユーザーが page reload したときは module 評価から始まるので `prevSnapshot = null` に自然に戻り、reload 直後の SSE refresh 1 発目は diff 無し（= toast 無し）になる。これは「ユーザーが意図的に reload した直後の noise」を構造的に防ぐ。

#### Fetch 失敗時の `__livePrev` 取扱いポリシー

現行 `loadAndRender()` は `data` 取得失敗時 `console.error` + early-return する catch 経路を持つ。本機能は **catch 経路で `__livePrev` を更新しない** (= 前回成功時の baseline を保持)。

意図:
- 「初回成功 (snapshot1 保存) → 2 回目失敗 (catch return / prev は snapshot1 のまま) → 3 回目成功」のとき、3 回目で `snapshot3 vs snapshot1` の **2 回分累積 delta** が toast に出る
- これは「fetch が間欠的に失敗していた間も裏で何か動いていた」signal として **意図的**。逆に catch で `__livePrev = null` にリセットすると 3 回目の toast が出なくなり、「失敗をまたいだ」観測が抜ける

**構造的強制 — `commitLiveSnapshot(next)` helper の導入**:

`__livePrev` への代入を `25_live_diff.js` の `commitLiveSnapshot(next)` helper に**閉じ込める**。`20_load_and_render.js` 側は `commitLiveSnapshot(__liveNext)` を success path の末尾でだけ呼ぶ。これにより:
- 20 番から `__livePrev = ...` の直接代入を**禁止**できる (literal pin で grep 可能)
- catch 経路で `commitLiveSnapshot()` を呼ばない契約を構造的に保証
- `commitLiveSnapshot` 単体は pure (引数を `__livePrev` に代入し読み出すだけ) なので Node round-trip でも pin 可能

Phase 1 で pin する 2 本セット:
- Phase 1-a literal pin: `test_load_render_does_not_directly_assign_liveprev` — `20_load_and_render.js` 内に `__livePrev =` (代入) が**存在しない** (commit helper 経由のみであることを構造的に保証)
- Phase 1-b Node round-trip: `test_commit_live_snapshot_then_diff_accumulates_across_skipped_commit` — `commitLiveSnapshot(snap1)` → `diffLiveSnapshot(__livePrev, snap3)` で snap1 vs snap3 の累積 delta が出ることを直接 pin (catch 経路の擬似化 = 「commit を呼ばない」シナリオ)

### D6. Ranking row の identity = name

選択: rank index ではなく **name 単位**で前回 count を保持。

理由: 同じ rank position でも name が入れ替わるケース (e.g. skill A 11→12 で skill B 10→11 を抜き 1 位浮上) は実機で頻発。rank index で diff を取ると「動いていない skill」も highlight されてしまう。name 単位 Map で diff し、新登場の skill (前回 Map に key 無し) も `delta = current - 0 > 0` で highlight する。

### D7. 高頻度 SSE refresh への対処

`server.py` の polling は default 1.0s。連発する SSE refresh で:
- highlight: `bumped` class を再適用するときに animation 再起動 (`void el.offsetWidth` reflow trick)
- highlight の **per-element timer cleanup**: `applyHighlights()` は要素ごとに「remove `bumped` する setTimeout id」を **`WeakMap<Element, number>`** で保持し、再 bump 時に `clearTimeout(prev)` してから新 timer を貼る。これにより「最初の bump 1500ms 後の `setTimeout` が **2 回目 animation の真ん中**で `classList.remove('bumped')` を実行してしまう race」を構造的に防ぐ
- **WeakMap 必須**: rank row は `loadAndRender()` ごとに `#skillBody` / `#subBody` の innerHTML 完全置換で element 参照が detach する。`Map` だと detach 済 DOM を pin して slow leak になるため、必ず `WeakMap` を使う。Phase 1-a に `test_apply_highlights_uses_weakmap_for_timer_state` (`new WeakMap(` の存在 + `new Map(` を timer 用途で使っていないこと) を pin する
- toast: 同要素の textContent 置換 + `void el.offsetWidth` で slide-in 再起動。前 toast の dismiss timer (`toastTimer`) は `clearTimeout` してから新規 timer を貼る

debounce は**入れない**。Issue は「更新があったら知らせて」が intent で、debounce はその逆方向。

## 📁 Critical files for Implementation

新規 + 既存:

- `dashboard/template/scripts/25_live_diff.js` — **新規**。`buildLiveSnapshot(data)` / `diffLiveSnapshot(prev, next)` / `applyHighlights(diff)` / `formatToastSummary(diff)` / `showLiveToast(msg)` の 5 関数。closure-private state も 25 番に置く（router/helpers が 00/10、本体 render が 20、その直後 25 で diff を「render の隣接シビング」として扱う）
- `dashboard/template/scripts/20_load_and_render.js` — **改修**。`loadAndRender()` 末尾で `const next = buildLiveSnapshot(data); if (prev) { applyHighlights(diffLiveSnapshot(prev, next)); showLiveToast(formatToastSummary(...)); } prev = next;` を追加。`prev` は 25_live_diff.js の closure-private (export せず top-level let で IIFE 内に保持)
- `dashboard/template/styles/10_components.css` — **改修**。`@keyframes pulse-bg` + `.bumped` 修飾 + `.toast` block + `prefers-reduced-motion` メディアクエリを追加。新ファイルを切らないのは「現状 7 ファイル運用 / 70_live_pulse.css を増やすほどの量ではない」判断
- `dashboard/template/shell.html` — **改修**。footer 直前に `<div class="toast" id="liveToast" ...>` を追加。kpi tile span に `data-kpi-id` (既存 id を持っているので追加不要かも → 既存 `kpi-*` id をそのまま data ref に使う) / lede `<span class="num">` には既存 id がある。ranking row には `data-name` 既存 (rank renderer 出力済) があるので利用
- `dashboard/server.py` — **改修**。`_MAIN_JS_FILES` tuple に `25_live_diff.js` を追加（`20_load_and_render.js` と `30_renderers_patterns.js` の間）。`test_dashboard_template_split.py` の `EXPECTED_TEMPLATE_SHA256` を更新（plan-reviewer に意図的更新だと示すため、コミットメッセージに sha 履歴を追加）
- `tests/test_dashboard_live_diff.py` — **新規**。Node round-trip + static-export grep + sha256 整合の混合
- `scripts/build_live_diff_fixture.py` — **新規** (オプショナル)。手動視覚スモーク用。2 段階の usage.jsonl を切り替えて diff 発火を再現する fixture

## 🛠 Step-by-step 実装 (TDD ordering)

> Phase 0 prep / Phase 1 RED (helper 単位) / Phase 2 GREEN / Phase 3 RED (renderer 統合) / Phase 4 GREEN / Phase 5 sha256 + visual smoke。各 Phase は独立 commit。

### Phase 0 — preparation (commit 1)

- 0.1 `tests/test_dashboard_template_split.py::EXPECTED_TEMPLATE_SHA256` の運用ルール再確認（コメントの「履歴」リストに今回 entry を追加する余地を確保）
- 0.2 既存 dashboard JS / CSS への侵襲箇所を読んで、`prefers-reduced-motion` 判定 helper が無いことを確認 (新規導入しても既存 CSS と衝突しない)

### Phase 1 — RED (helper 単位の失敗テストを書く / commit 2)

新規 `tests/test_dashboard_live_diff.py` に **literal pin + Node round-trip** で以下を失敗化する。

#### 1-a. literal pin (静的解析)

- `test_25_live_diff_js_file_exists` — `dashboard/template/scripts/25_live_diff.js` が存在
- `test_build_live_snapshot_function_defined` — `function buildLiveSnapshot(` が含まれる
- `test_diff_live_snapshot_function_defined` — `function diffLiveSnapshot(` が含まれる
- `test_apply_highlights_function_defined` — `function applyHighlights(` が含まれる
- `test_format_toast_summary_function_defined` — `function formatToastSummary(` が含まれる
- `test_show_live_toast_function_defined` — `function showLiveToast(` が含まれる
- `test_25_listed_in_main_js_files_tuple` — `dashboard/server.py` の `_MAIN_JS_FILES` に `25_live_diff.js` が `20_load_and_render.js` と `30_renderers_patterns.js` の間に挟まる
- `test_load_and_render_calls_diff_helpers` — `20_load_and_render.js` 末尾に `buildLiveSnapshot(` と `diffLiveSnapshot(` の呼び出しが新たに追加される
- `test_assembled_template_contains_toast_element` — concat 後の `_HTML_TEMPLATE` に `id="liveToast"` が含まれる
- `test_assembled_template_contains_pulse_keyframe` — `@keyframes pulse-bg` が含まれる
- `test_pulse_keyframe_respects_reduced_motion` — `@media (prefers-reduced-motion: reduce)` ブロックが pulse-bg を override (e.g. `animation: none` か `animation-duration: 0.01ms`)
- `test_load_render_does_not_redeclare_liveprev` — `20_load_and_render.js` に `let __livePrev` / `var __livePrev` / `const __livePrev` の宣言文が**存在しない** (declaration-only grep)
- `test_25_declares_liveprev_at_iife_top` — `25_live_diff.js` の冒頭 (function 定義より前) に `let __livePrev = null` 宣言が存在
- `test_load_render_does_not_directly_assign_liveprev` — `20_load_and_render.js` に `__livePrev =` (代入) が**存在しない** (commit helper 経由のみ強制)
- `test_apply_highlights_uses_weakmap_for_timer_state` — `25_live_diff.js` に `new WeakMap(` が存在し、timer 用途で `new Map(` を使っていない (WeakMap 必須規約の pin)

#### 1-b. Node round-trip (behavior pin)

`tests/test_dashboard_local_tz.py::TestLocalDailyFromHourlyNode` と同 pattern で `_run_node` を再利用。新規 `TestBuildLiveSnapshot` / `TestDiffLiveSnapshot` / `TestFormatToastSummary` クラス:

- `test_build_live_snapshot_extracts_kpi_keys` — `data = {total_events: 100, skill_ranking: [...], ...}` から `snapshot.kpi['kpi-total'] === 100` 等
- `test_build_live_snapshot_handles_missing_data` — `data = {}` で `snapshot.kpi['kpi-total'] === 0` (defensive default)
- `test_diff_returns_empty_when_first_render` — `prev === null` のとき diff 結果も「変化なし」を表現する empty 形を返す（toast 抑制経路）
- `test_diff_kpi_increment_only` — KPI 値が増えたフィールドだけ delta > 0 の entry を持つ。`delta === 0` / `delta < 0` は出力に含まれない
- `test_diff_lede_increment` — lede `ledeEvents` の delta も別 bucket に出る
- `test_diff_ranking_new_name_treated_as_zero_baseline` — 新登場 skill (前回 Map 未登録) は `delta = current - 0` で出る
- `test_diff_ranking_existing_name_count_growth` — 既存 skill の count 増分が正しく出る
- `test_diff_ranking_name_disappeared_does_not_appear` — 前回 top10 にいたが今回消えた skill は diff 出力に出ない（toast のために**増分のみ**追う）
- `test_format_toast_summary_aggregates_by_label` — diff から `+12 events · +1 skill · +2 subagent invocations` を生成
- `test_format_toast_summary_returns_empty_when_no_growth` — delta 全 0 のとき空文字を返す（toast 表示抑制）
- `test_format_toast_summary_skips_zero_delta_segments` — events 増えたが skills 不変なら skills セグメントは出ない
- `test_format_toast_summary_caps_at_four_segments` — 5 種以上の delta があっても先頭 4 セグメントに切る (省略 `...` 等は付けない / segment 数 cap 規則をシンプルに)
- `test_format_toast_summary_uses_singular_for_delta_one` — `+1 event` (events ではなく event)、`+1 skill`、`+1 subagent invocation` を pin (LABEL テーブル準拠)
- `test_format_toast_summary_excludes_resume_rate` — `kpi-resume` の delta があっても toast には出ない (highlight のみ)
- `test_format_toast_summary_excludes_lede_buckets` — lede 数字 (`ledeEvents` / `ledeDays` / `ledeProjects`) は toast に出ない (KPI と二重カウント防止)
- `test_format_toast_summary_excludes_ranking_rows` — ranking row delta も toast に出ない (highlight のみ)
- `test_commit_live_snapshot_then_diff_accumulates_across_skipped_commit` — `commitLiveSnapshot(snap1)` → `diffLiveSnapshot(getLivePrev(), snap3)` で累積 delta が出ることを Node round-trip で pin。catch 経路の擬似化 = 「`commitLiveSnapshot` を呼ばないシナリオ」を直接表現する (DOM-heavy な `loadAndRender()` は Node では走らないため、helper 単位で behavior を保証する)

### Phase 2 — GREEN (helper 実装 / commit 3)

- 2.1 `dashboard/template/scripts/25_live_diff.js` を新規作成
  - `function buildLiveSnapshot(data)`: data から KPI / lede / ranking name→count Map を作る pure function
  - `function diffLiveSnapshot(prev, next)`: `{kpi: [{id, delta}], lede: [{id, delta}], rankSkill: [{name, delta}], rankSub: [{name, delta}]}` を返す。delta ≤ 0 entry は除外。`prev === null` のときは all-empty を返す
  - `function applyHighlights(diff)`: KPI / lede は `getElementById(id)` で `.bumped` を付ける → 1500ms で remove。ranking row は `querySelector('.rank-row[data-name="..."][data-kind="skill"]')` で取得。timer state は `WeakMap<Element, number>` (= 上記 D7)
  - `function formatToastSummary(diff)`: aggregate label 集合を順序固定で生成
  - `function showLiveToast(msg)`: msg 空ならば `el.hidden = true` で抑制、空でないとき textContent 置換 + animation restart + 4s 後に再 hidden
  - `function commitLiveSnapshot(next)`: `__livePrev = next` を行う**唯一の writer**。20 番からはこの helper 経由でしか prev を更新しない (= catch 経路で呼ばないことが構造的に保証される)
  - closure-private state (`let __livePrev = null` / `let toastTimer = null`) を **25 番ファイル冒頭 (IIFE 直下)** に配置し、`window.__liveDiff = { buildLiveSnapshot, diffLiveSnapshot, formatToastSummary, commitLiveSnapshot, getLivePrev: () => __livePrev }` で test から呼べるよう公開（Node round-trip でフックする）。**`getLivePrev` は test fixture 専用の read-only probe** で production code path では呼ばない（production 経路は `diffLiveSnapshot` の第一引数として 20 番から渡す path のみ通る）
- 2.2 `dashboard/template/scripts/20_load_and_render.js` 末尾に diff 統合
  ```js
  const __liveNext = buildLiveSnapshot(data);
  if (typeof window.__DATA__ === 'undefined') {  // live mode のみ
    const __liveDiff = diffLiveSnapshot(__livePrev, __liveNext);
    if (__livePrev !== null) {
      applyHighlights(__liveDiff);
      showLiveToast(formatToastSummary(__liveDiff));
    }
    commitLiveSnapshot(__liveNext);  // ← 直接代入は禁止。helper 経由のみ
  }
  ```
  `__livePrev` は 25_live_diff.js 側に `let __livePrev = null` で宣言。20 番は `__livePrev` を**読み参照のみ** (`diffLiveSnapshot` の第一引数) で使い、**代入は `commitLiveSnapshot` 経由でしか行わない**
- 2.3 `dashboard/template/styles/10_components.css` に追記
  - `@keyframes pulse-bg`
  - `.kpi.bumped` / `.kpi .v.bumped` / `.rank-row.bumped` / `.rank-row .rv.bumped` / `.lede .num.bumped`
  - `.toast` (position fixed top right, padding, border-radius, font-family mono, opacity transition, slide-in keyframe)
  - `@media (prefers-reduced-motion: reduce)` で `animation: none` + 静的 outline only
- 2.4 `dashboard/template/shell.html` の footer 直前に toast div 追加
- 2.5 `dashboard/server.py` の `_MAIN_JS_FILES` tuple に `25_live_diff.js` を `20_load_and_render.js` の直後に追加
- 2.6 Phase 1 のテストが**全部 GREEN** になることを確認

### Phase 3 — RED (renderer 統合の DOM 整合 / commit 4)

statics export 経路と live 経路の両方で DOM が壊れていない構造的確認を Pin。

- `test_static_export_does_not_show_toast` — `render_static_html(data)` の出力に `id="liveToast"` 要素は存在するが `hidden` 属性 (or `[hidden]` selector ヒット) が付いている。誤って toast を出していないことを HTML 文字列レベルで確認
- `test_static_export_does_not_apply_bumped_class` — 出力 HTML に `class="... bumped"` が含まれない（render_static_html は 1 ショットなので diff 不能 → 当然 highlight 無し）
- `test_kpi_id_attributes_persist_after_render` — kpiRow は完全置換されるが、`id="kpi-total"` 等の id 属性が `loadAndRender()` 後の HTML 文字列にも残っている（applyHighlights が getElementById で参照する前提を壊さない）
- `test_rank_row_data_name_attribute_persists` — `data-name="..."` が rank renderer 出力に必ず含まれる（diff 統合後も既存属性が壊れていない）
- `test_first_refresh_after_reload_does_not_emit_toast` — Node round-trip で `prevSnapshot=null` → `loadAndRender(dataA)` → toast textContent が空のままを pin

### Phase 4 — GREEN + sha256 更新 (commit 5)

- 4.1 Phase 3 で発見した不整合 (もしあれば) を修正
- 4.2 `tests/test_dashboard_template_split.py::EXPECTED_TEMPLATE_SHA256` を新値に**上書き**更新（期待値は最新 sha のみ。コメントの履歴 list は documentation で assertion には使われない）。コメント履歴に Issue #69 entry を 1 行追加。fix-up commit で sha が再変動したら期待値を再上書きし履歴に追加 entry を append
- 4.3 全テストグリーン確認 (`python3 -m pytest tests/`)

### Phase 5 — visual smoke (commit 6 / **mandatory**)

`prefers-reduced-motion` の効きと pulse / toast の見た目は Node round-trip では検証できず、実機ブラウザでの確認が唯一の検証手段。**accessibility 配慮は手動チェック必須**として Phase 5 を mandatory 扱いに格上げ (オプショナルではない)。

- 5.1 `scripts/build_live_diff_fixture.py` を新規作成。2 段階の `usage.jsonl` (snapshot A / snapshot B) を生成し、`render_static_html` で順番に書き出す代わりに、live dashboard を起動して**手動で append** する手順を `print()` で説明する fixture
- 5.2 動作確認シナリオ (commit message + PR description に記録):
  1. fixture を起動 → A snapshot で初回描画 (toast 出ない / highlight 出ない)
  2. usage.jsonl に skill_tool 5 件 + subagent 1 件を append
  3. ~1 秒後に SSE refresh が来て KPI tiles の `kpi-total` / `kpi-skills` (新登場時) が pulse + toast `+5 events · +1 subagent invocation`
  4. macOS のシステム設定「視差効果を減らす (Reduce motion)」を ON にして再確認 → pulse は静止 outline のみ / toast は fade のみ
- 5.3 **PR description チェックリスト**（merge 前に必ずチェック）:
  - [ ] live mode で highlight pulse 確認 (KPI / lede / ranking row)
  - [ ] live mode で toast 表示確認 (1 行 aggregate / 4s 自動消滅)
  - [ ] static export (HTML を `python3 reports/export_html.py` で生成) で toast / highlight が**出ない**ことを確認
  - [ ] OS 設定で reduced motion ON にしたとき pulse が静止 outline / toast が opacity fade のみであることを確認
  - [ ] **catch 経路の累積 delta**: SSE は alive のまま `/api/data` 単体が失敗するシナリオを再現し、復活後の累積 delta toast を確認。具体的には `dashboard/server.py` の `/api/data` ハンドラに一時的に `raise RuntimeError("forced 500")` を仕込み (revert 容易な単行) → usage.jsonl に skill_tool 1 件 append → toast 出ない (catch return) → revert → 再 append → 復活 refresh で **2 回分の累積 delta** が toast に出ることを確認。これが catch 経路の policy (D5) の唯一の正しい再現で、server kill では SSE channel ごと止まるため再現にはならない（`70_init_eventsource.js` の error handler は `loadAndRender()` を呼ばない）

> 注: 「fetch 失敗 → 復活」は `loadAndRender()` の **try/catch 内**で発生したときだけ catch path policy が走る。SSE channel 自体が切れる server kill は EventSource の error 経路 (`setConnStatus('reconnect' | 'offline')`) であり、`loadAndRender` を呼ばない別経路。この区別を PR reviewer / 後続改修者にも明示しておくため、上記注釈を Phase 5 に残す。

## 🧪 Test plan — 配置詳細

| テストファイル | テスト対象 | pin 手法 |
|---|---|---|
| `tests/test_dashboard_live_diff.py` (新規) | `25_live_diff.js` の関数定義 / `_MAIN_JS_FILES` への登録 / shell.html の toast 要素 / CSS keyframe / `prefers-reduced-motion` | literal pin (regex / substring) |
| `tests/test_dashboard_live_diff.py::TestBuildLiveSnapshotNode` | snapshot 抽出 / defensive default | Node round-trip (skipUnless) |
| `tests/test_dashboard_live_diff.py::TestDiffLiveSnapshotNode` | KPI / lede / ranking name 単位 diff 規則 | Node round-trip |
| `tests/test_dashboard_live_diff.py::TestFormatToastSummaryNode` | aggregate 文字列生成 / 順序 / cap | Node round-trip |
| `tests/test_dashboard_live_diff.py::TestStaticExportNoLiveBehavior` | static export で toast 非表示 / bumped class 無 | `render_static_html(data)` を呼んで HTML 文字列 grep |
| `tests/test_dashboard_template_split.py` | `EXPECTED_TEMPLATE_SHA256` 更新 + DOM anchor (`liveToast` 追加) | sha256 + substring |
| `tests/test_dashboard_local_tz.py` の構造踏襲 | Node `_run_node` ヘルパパターン | コピペ + 拡張（fixture import） |
| 手動 visual smoke | pulse / toast の見た目 / reduced-motion | `scripts/build_live_diff_fixture.py` + ブラウザ |

> **Node round-trip の TZ 依存無し**: 本機能の関数群は時刻に依存しない pure function（snapshot extract / diff / formatter）なので `TZ` を `Asia/Tokyo` 固定にせずデフォルトで動かして問題ない。`_run_node` の env override は省略してよい。

> **DOM 依存関数の Node 取扱い**: `applyHighlights(diff)` と `showLiveToast(msg)` は `getElementById` / `document` を触るため Node round-trip の対象外。`buildLiveSnapshot` / `diffLiveSnapshot` / `formatToastSummary` の 3 つだけが pure function として `_run_node` で pin される。DOM 依存ロジックは Phase 5 の visual smoke でのみ検証。

### ファイル分離ポリシー

`tests/test_dashboard_live.py` (既存) と `tests/test_dashboard_live_diff.py` (新規) の責務を明示分離:

- `test_dashboard_live.py` = **server-side**: ThreadingHTTPServer / server.json lifecycle / idle watchdog / `/healthz` / SSE 経路のテスト
- `test_dashboard_live_diff.py` = **client-side JS**: `buildLiveSnapshot` / `diffLiveSnapshot` / `formatToastSummary` の Node round-trip + template anchor (toast 要素 / pulse keyframe / `prefers-reduced-motion`) の literal pin

両者は責務が直交するため統合しない。後続 issue で「次の live 機能」を追加する人が「どっちに書くか」を 0 秒で判断できるようにする。

## ⚠️ Risks / tradeoffs / accessibility

| Risk | 対策 |
|---|---|
| **`prefers-reduced-motion` 配慮抜け** | CSS で `@media (prefers-reduced-motion: reduce) { .kpi.bumped, .rank-row.bumped, .lede .num.bumped { animation: none !important; outline: 1px solid var(--mint); } .toast { animation: none !important; transition: opacity 200ms ease; } }` を必ず追加。Phase 1-a の `test_pulse_keyframe_respects_reduced_motion` で構造的に pin |
| **高頻度 SSE refresh で toast がチカチカする** | toast は queue せず置換のみ。`clearTimeout` で前回 dismiss timer をキャンセルし、最新 toast の textContent をそのまま 4s 表示。delta 全 0 の refresh は toast を出さない (= ファイル touch のみのケースで silent) |
| **静的 export で誤って toast / highlight が出る** | `if (typeof window.__DATA__ === 'undefined') { ... }` ガードで diff 経路自体に入らない。Phase 3 の `test_static_export_does_not_show_toast` で構造的に pin |
| **ranking row の z-order と pulse の互換** | `.rank-row` の hover gauge / data-tip との pseudo-element 競合を避けるため、pulse は `box-shadow: inset` でなく **背景色 transition** + `outline` の組み合わせで実装。`.rank-row` 自体に `position: relative` は既に効いており追加不要 |
| **Toast が固定 right-top で hover help-popover (右配置の `data-place="right"`) と競合** | help-popover は KPI 内側に出る短期 popover で z-index も低い。toast は z-index: 1000 で完全 overlay。視覚衝突は両方 visible でも文脈的に明確（toast = 自動表示 / popover = ユーザー操作）なので妥協可 |
| **EXPECTED_TEMPLATE_SHA256 を意図的に更新する PR の review 負荷** | コミットメッセージで「Issue #69 で shell.html + 10_components.css + _MAIN_JS_FILES に変更があるため意図的に更新」を明記。`test_html_template_byte_equivalent_to_pre_split_snapshot` は変更後 sha で再 GREEN。**fix-up commit で template が再変更された場合**は、履歴 list に **追加** entry を 1 行ずつ append する（既存 Issue #65 fix-up エントリ慣習を踏襲 / `tests/test_dashboard_template_split.py` のコメント履歴を参照）|
| **減少フィールドを silent に流す UX 妥当性** | 減少は実機ではほぼ起きない (retention で 180 日超データが落ちるとき位)。「+ aggregate のみ」の方が toast が「成長 signal」として機能する。減少強調は別 Issue で再考 |
| **toast の文言 i18n** | dashboard の既存タイトルは日本語、KPI label は英語（"events" / "skills" 等）。toast も英語 aggregate (`+5 events · +1 skill`) で揃える方が既存 KPI ラベル文言と整合 |
| **`box-shadow` animation の GPU 負荷** | 1.5s pulse × 同時最大 ~13 要素 (KPI 8 + lede 3 + rank 2) で safari/firefox とも問題なし (composite layer)。低スペック端末でも `prefers-reduced-motion` で off できる |

## 🚫 Out of Scope

明示的に「将来 Issue で再考」とするもの:

- Patterns / Quality / Surface タブ内の数字の highlight / toast への取り込み（active page 検知で素直に拡張可能。本 PR の構造（key Map）にすでに将来拡張余地はある）
- Toast の click-to-dismiss / hover で持続 / queue 表示 / 履歴 / 通知音
- 数値**減少**の visual signal (retention 動作可視化など)
- Sparkline / heatmap 等の cell-level 強調（cell 数が多く、視覚 noise が支配的になるリスク）
- 静的 export での「過去 export 同士の diff」表示（diff 比較相手を生成する必要があり scope が爆発）
- toast 内容の日本語化（既存 KPI label が英語のため整合性で英語維持。i18n 化は別 issue）

## 📝 申し送り (post-merge memory file 候補)

- `docs/reference/dashboard-server.md` に「**live diff & toast 統合の構造**」節を追記する余地。`__livePrev` closure-private state / live-only ガード / name 単位 ranking diff の理由 / sha256 更新の運用 を 1 ブロックで残しておくと、次の renderer 改修者が踏み抜かない
- 将来 Patterns / Quality タブの数字を取り込むときは `buildLiveSnapshot(data)` に `patterns: { ... }` バケットを追加して、`active page` を `document.querySelector('section.page:not([hidden])').dataset.page` で取り、active page のみ highlight 適用する形に拡張する（toast は active 関係なく常に最新 aggregate を出してよい）
- toast 文字列フォーマット (`+N events · +N skill`) の precision/i18n は将来 i18n layer 導入トリガーで `_UI_LABELS = {...}` 定数化する余地（#62 / #74 で踏襲済の hardcoded 慣習を本 PR でも踏襲）
