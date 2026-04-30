# Issue #83 Implementation Plan — Live Heartbeat Sparkline

## 1. Goal

Idle 中も「動き続けてる」感を出すため、ダッシュボード上部に常時左→右へ流れる ECG 風 heartbeat sparkline を新設し、SSE refresh メッセージ受信を契機に山を一発立てる。既存の conn-status / lastRx / liveToast / `.bumped` highlight とは責務を分けて重複させない。

## 2. Acceptance Criteria

- [ ] Live mode で dashboard を開くと、画面上部 (page-nav 帯) の右端に幅 ~140px / 高さ ~22px の heartbeat sparkline が常時アニメーションする
- [ ] アイドル 30 秒以上でも line が左→右に流れ続ける (= 「凍ってない」)
- [ ] EventSource `message` (data に `refresh`) 受信時、現在の波形位置に山 (amplitude ~10px、~700ms で減衰) が 1 発立つ
- [ ] `setConnStatus('reconnect')` 中は line 色が peach 系に薄く、山立ては抑制 (薄い反応のみ)
- [ ] `setConnStatus('offline')` 中は流れを停止し flat line が静止
- [ ] 静的 export (`window.__DATA__`) では sparkline 要素は DOM に存在するが `hidden` 属性で表示されない
- [ ] `prefers-reduced-motion: reduce` 環境では line のスクロール / 山立てを停止し、視覚要素は静的 baseline のみ。受信時は `aria-live="polite"` の SR 通知に縮退
- [ ] 全 4 ページ (Overview / Patterns / Quality / Surface) で同じ heartbeat が同位置に表示される
- [ ] `pytest tests/` 全件 green。新規 `tests/test_dashboard_heartbeat.py` が template / concat / static export / reduced-motion / sentinel 整合を pin
- [ ] `EXPECTED_TEMPLATE_SHA256` が新 hash に更新され、`test_html_template_byte_equivalent_to_pre_split_snapshot` が green

## 3. Out of Scope (User explicitly rejected)

- relative time auto-tick (`lastRx` を "X秒前" + 1 秒 tick) — 案 C
- activity sparkline (受信頻度の mini graph) — 案 D
- top-edge progress beam — Q3 D 案
- 全画面 breathing animation — Q3 G 案
- conn-status badge を上にも複製する案
- Patterns / Quality / Surface の page-specific heartbeat バリエーション (共通要素として 1 個)
- 複数本同時に走る波形 / multi-channel ECG
- 受信頻度をグラフ化する経時 plot
- **Footer version bump (`v0.7.1` → `v0.7.2`)**: stays at `v0.7.1` in this PR。bump は `v0.7.2 → main` の release PR で行う (`patch-release` skill convention)。理由: feature PR cycle と release cycle の責務分離 / release タイミング slip 時の merge 競合回避

## 4. Decisions Made in This Plan

### 配置 — 推奨: **(i) `nav.page-nav` の右端**

選定: `nav.page-nav` (4 タブの flex 行) の **右端** に `<svg id="heartbeat">` を置き、`margin-left: auto` で右寄せする。

理由:
- 「画面の上の方」というユーザー要件を満たす最上位の永続 chrome
- 4 ページ全てに既に存在する共通要素 → router 切替で消えない (Overview limited な case (iii) 不要)
- 新規 chrome バンドを増やさず縦スペース消費がゼロ (案 (ii) は新規 band で密度アップ → page-nav の `border-bottom: 1px solid var(--line)` の下に余計な分割線が 1 本増える)
- `.page-nav` は既に `flex-wrap: wrap` なので、狭幅で改行しても破綻しない

代替条件:
- **案 (ii) を採るべきとき**: heartbeat の幅を 240px+ に伸ばしたい / amplitude を 24px+ に上げたい場合。page-nav の縦幅 (~37px) 内に収まらなくなる
- **案 (iii) を採るべきとき**: ユーザーが Overview 限定の "演出" として欲しいと方針転換した場合のみ。共通要素として不適なので **plan としては非推奨**

### 残り未決の仕様の決定

| # | 項目 | 決定 |
|---|---|---|
| 1 | flat line の演出 | **ECG 風に右へスクロールする静的 baseline**。SVG `<polyline>` の `points` 配列を `requestAnimationFrame` で 1 frame ごとに 1 サンプル左シフト + 末尾に baseline (y=0) サンプル追加。速度は `~30 px/s` (= 60fps 環境で frame あたり 0.5px) |
| 2 | 山の形・amplitude・持続時間 | **PQRS 風の単一スパイク** (上に -10px → 下に +4px → 0px) を ~0.7s で `points` に書き込む。`refresh` 受信時に `__hbSpikeRemain = SPIKE_SHAPE.length (= 10 frames)` をセットし、tick で sample 値を `SPIKE_SHAPE[i] * __hbSpikeAmp` で消費。**duration (10 frames) は固定**、強弱は `__hbSpikeAmp` で別軸制御 |
| 3 | 接続切れ挙動 | `online` → 通常 (mint, `__hbSpikeAmp = 1.0`)。`reconnect` → 流速半分・色 peach・**spike 振幅を 0.3 倍** (`__hbSpikeAmp = 0.3`、duration は 10 frames で online と同じ)。`offline` → tick 停止 + line を flat にクリア (色 coral)。`static` → `hidden` 属性で完全非表示 |
| 4 | 静的 export 経路 | **完全非表示** (`hidden` 属性)。10_helpers.js の `setConnStatus('static')` 経由で確実に隠す。理由: 静的 export は受信契機が無いので「動き続け感」自体が無意味、視覚 noise になる |
| 5 | `prefers-reduced-motion: reduce` | tick を起動せず、`<polyline>` は flat line 1 本を SVG 描画のみ。受信時は `points` を変えず、`aria-live="polite"` 領域 (新規 `<span class="sr-only" id="heartbeatSr">` を sparkline の隣に置き、refresh 時に `"更新を受信しました"` を 1 度書き込む) で SR 通知に縮退 |
| 6 | アクセシビリティ | SVG 要素に `role="img"` + `aria-label="ライブ更新インジケータ"`。隣に visually-hidden な `<span id="heartbeatSr" aria-live="polite"></span>` を追加。conn-status (`role="status"`) との重複は避け、heartbeat SR は受信時のみ短文。**reduced-motion 環境でのみ実際に書き込む** (通常環境では noisy / 数秒に 1 度発火する aria-live は逆効果)。 通常 SR ユーザーには conn-status の `aria-live="polite"` で接続生存が伝わるので heartbeat の SR 補完は副次的。Reduced-motion 時のみ視覚 spike が消える分の補完として作動 |
| 7 | 実装手段 | **inline SVG `<polyline>` + `requestAnimationFrame`**。理由: stdlib + vanilla JS 規約準拠 / Canvas は state 管理 + DPR 対応が増える / CSS-only は `refresh` イベント駆動の山立てが書けない / SVG なら既存 `.spark-svg` (overview sparkline) と同じ pattern で `viewBox` resize に強い。Trade-off: 60fps × 60 sample で 1 frame あたり ~60 polyline point の文字列再構築だがブラウザの SVG path diff は CPU 数 % で済む。**closure-private state は既存の単一 shared IIFE 内で宣言** (`25_live_diff.js` 冒頭コメント参照: 全 main_js は単一 IIFE で wrap される)。Per-file IIFE 新設は既存パターンと不一致になるので **しない**。代わりに 全識別子を `__hb` prefix で名前空間隔離し、Step 1 の literal pin で再宣言禁止を test レベルで保証 |
| 8 | データ source | **EventSource `message` の onmessage で発火**。`70_init_eventsource.js` の `es.addEventListener('message', ...)` 内で `scheduleLoadAndRender()` の前に `bumpHeartbeat()` を呼ぶ。`/api/data` のメタデータは使わない (受信契機 = 鼓動の意味的整合がそのまま取れる) |

## 5. Critical Files

### New

| Path | 役割 |
|---|---|
| `dashboard/template/scripts/15_heartbeat.js` | heartbeat tick state + `bumpHeartbeat()` + `startHeartbeat()` / `stopHeartbeat()` API。10 (helpers) と 20 (load_and_render) の間に置き、20 番から見えるように先に評価させる |
| `dashboard/template/styles/15_heartbeat.css` | `.heartbeat` SVG / line stroke 色 (state 別) / `prefers-reduced-motion` 縮退 |
| `tests/test_dashboard_heartbeat.py` | template smoke / concat 順 / static export hidden / reduced-motion CSS pin / `setConnStatus` 経路の `bumpHeartbeat` 呼び出し pin (Node round-trip) |

### Changed

| Path | 変更内容 |
|---|---|
| `dashboard/template/shell.html` | `nav.page-nav` の最後 (4 タブの後) に `<svg id="heartbeat">` + 隣の SR span を追加。footer version は `v0.7.1` のまま据え置き (release PR で bump) |
| `dashboard/template/scripts/10_helpers.js` | `setConnStatus(state)` の中で heartbeat 状態 (`startHeartbeat` / `stopHeartbeat` / amplitude scaling) も呼ぶ。**heartbeat sync は `if (!el) return` ガードより前** に書く (connStatus DOM 削除 / 不在時にも heartbeat sync を維持する防衛措置)。`STATUS_LABEL` は据え置き。**verified**: `connStatus` は `shell.html:466` の `footer.app-footer` 内 shared chrome に居住するので 4 ページ全てで存在 (per-page header 仮説は誤り) |
| `dashboard/template/scripts/70_init_eventsource.js` | `es.addEventListener('message', ...)` 内の `scheduleLoadAndRender()` 呼び出し前に `bumpHeartbeat()` を fire。static 経路では heartbeat 起動しない (`setConnStatus('static')` 後は何もしない) |
| `dashboard/server.py` | `_CSS_FILES` に `15_heartbeat.css`、`_MAIN_JS_FILES` に `15_heartbeat.js` を **20_load_and_render.js の直前** に追加 |
| `tests/test_dashboard_template_split.py` | `EXPECTED_TEMPLATE_SHA256` を新 hash に更新 + 履歴コメント追記。`test_html_template_contains_critical_dom_anchors` の id list に `heartbeat` / `heartbeatSr` を追加 |

### Deleted

なし。

## 6. Ordered Implementation Steps (TDD: failing test 先 → impl → green)

### Step 0 — Branch / version 準備

- `git checkout v0.7.2 -b feature/83-live-heartbeat` (本作業ブランチ)。**実装ステップ外、PM 操作**

### Step 1 — Failing test: server template smoke (concat / sentinel 整合)

**先**: `tests/test_dashboard_heartbeat.py::TestTemplateConcat`

- `_HTML_TEMPLATE` に `id="heartbeat"` SVG が含まれる
- `_HTML_TEMPLATE` に `id="heartbeatSr"` aria-live span が含まれる
- `_CSS_FILES` tuple に `"15_heartbeat.css"` が `"10_components.css"` の後 / `"20_help_tooltip.css"` の前にある
- `_MAIN_JS_FILES` tuple に `"15_heartbeat.js"` が `"10_helpers.js"` の後 / `"20_load_and_render.js"` の前にある
- **literal pin** `test_hb_state_declared_only_in_15_heartbeat_js`: 全 main_js を concat した string で `let __hbState` の出現が **1 回のみ** (= 既存の `25_live_diff.js` 等の closure と shared IIFE 内で名前競合しないことを grep ベースで保証)。同様に `__hbBuf` / `__hbSpikeRemain` / `__hbSpikeAmp` / `__hbRafId` も 1 回ずつ
- **state contract pin** `test_setHeartbeatState_accepts_status_label_keys`: `STATUS_LABEL` の keys (= `online` / `reconnect` / `offline` / `static`) と `setHeartbeatState` の switch case が **1:1 対応** していることを grep ベースで pin。一方の追加が他方に伝搬しないと test red になる。これにより `setConnStatus` 内の heartbeat sync 順序 (heartbeat sync → connStatus DOM ガード) で渡される state value の domain を test 化
- 期待: 全部 fail (まだファイル無い)

### Step 2 — Failing test: static export 縮退 + reduced-motion CSS pin

**先**: `tests/test_dashboard_heartbeat.py::TestStaticExportHidden` + `TestReducedMotionCss`

- `render_static_html(data)` の出力で `<svg id="heartbeat"` を含む tag に `hidden` boolean attribute がついている (regex で確認)
- `15_heartbeat.css` に `@media (prefers-reduced-motion: reduce)` ブロックがあり、その中で `.heartbeat` の `animation: none` または tick 停止 marker (`--heartbeat-paused: 1`) のいずれかが立つ
- 期待: ファイル無いので fail

### Step 3 — Failing test: Node round-trip behavior (`requestAnimationFrame` mock)

**先**: `tests/test_dashboard_heartbeat.py::TestHeartbeatTickNode` (`@unittest.skipUnless(_NODE, ...)`)

- **ロード対象**: `15_heartbeat.js` を **単体ファイル** で `_node_eval` する (concat 後ではない、ファイル単位 unit test として完結)
- **完全な stub list** (Node に存在しない browser API 全部):
  ```js
  // Node 環境では window / document が undefined。`15_heartbeat.js` 単体ロード時に
  // `window.matchMedia(...)` / `window.requestAnimationFrame(...)` への参照が global 上で
  // 解決されるよう先に bind する。
  globalThis.window = globalThis;
  globalThis.document = globalThis.document || {};
  let _fakePoly = { setAttribute() {}, getAttribute: () => '' };
  let _fakeSvg = { dataset: {}, setAttribute() {}, getAttribute: () => '', querySelector: () => _fakePoly, hidden: false };
  let _fakeSr = { textContent: '' };
  document.getElementById = (id) => id === 'heartbeat' ? _fakeSvg : id === 'heartbeatSr' ? _fakeSr : null;
  let _rafQueue = []; let _rafId = 0;
  window.requestAnimationFrame = (fn) => { _rafQueue.push({id: ++_rafId, fn}); return _rafId; };
  window.cancelAnimationFrame = (id) => { _rafQueue = _rafQueue.filter(x => x.id !== id); };
  window.matchMedia = (q) => ({ matches: false });
  function flushFrames(n) { for (let i = 0; i < n; i++) { const item = _rafQueue.shift(); if (item) item.fn(); } }
  function flushMicrotasks() { return Promise.resolve().then(() => Promise.resolve()); }  // microtask 2 段 drain
  ```
- **Pre-flight assertion** (= "test ran but tested nothing" 防止): file load 直後に `assert typeof window.__heartbeat === 'object'` / `assert typeof window.__heartbeat.bump === 'function'`。`15_heartbeat.js` の module 末尾の `window.__heartbeat = {...}` 代入が成功したかを最初に確認 → stub 不足時に loud fail
- **同期的 frame drain** で確定: `setImmediate` 経由ではなく `flushFrames(N)` で N 回同期 dequeue → 微小 task の event loop ordering に依存しない
- **Online assertion**: `__heartbeat.start()` → `__heartbeat.bump()` → `flushFrames(15)` 後、`__heartbeat._buf()` の `Math.min(...buf) < -5` (= 山が立った)
- **Reconnect assertion (Proposal 4 整合)**: `__heartbeat.setState('reconnect')` → `__heartbeat.bump()` → `flushFrames(15)` 後、`Math.max(...buf.map(Math.abs)) < 5` (= online と同じ duration / 振幅 0.3 倍 で薄い反応)。online との対比で振幅 scaling が duration scaling ではないことが pin される
- **Offline assertion**: `__heartbeat.setState('offline')` 後 `__heartbeat.bump()` → `flushFrames(15)` でも `__heartbeat._buf()` が flat (`Math.max(...buf.map(Math.abs)) < 1`)
- **State transition leak assertion (Proposal 4 追補)**: `__heartbeat.setState('reconnect')` → `setState('online')` → `bump()` → `flushFrames(15)` 後、buf 振幅は **online 用 (`>= 5`)**。reconnect 時代の `__hbSpikeAmp = 0.3` が leak していないことを pin
- **Reduced-motion bump 連発 SR 通知 assertion (Proposal 5 追補, microtask drain 込み)**: テスト関数を `async` で書く。flow:
  1. `__heartbeat.stop()` (前段の online テストで設定済の rAF を停止)
  2. `window.matchMedia = () => ({matches: true})` に差し替え
  3. `__heartbeat._reset()` (= 後述する test 専用 hook を呼んで `__hbReducedMotion` を再評価)
  4. `_fakeSr` を `{ _history: [], set textContent(v) { this._history.push(v); } }` の spy に差し替え
  5. `__heartbeat.bump()` → `await flushMicrotasks()` → `__heartbeat.bump()` → `await flushMicrotasks()`
  6. `_fakeSr._history` が `['', '更新を受信しました', '', '更新を受信しました']` と並ぶことを assert (= 同一文字列でも一旦 `''` を経由するので aria-live が再発火する設計を pin)
  7. `_node_eval` の test runner は `async` 戻り値を `await` するので microtask が確実に drain される
- 期待: `15_heartbeat.js` 無いので fail

### Step 4 — Impl: 新規 CSS / JS 作成

**impl**:

- `dashboard/template/styles/15_heartbeat.css` 新設
  - `.heartbeat` SVG 既定 size (140 × 22), `display: inline-block`, `margin-left: auto`, `vertical-align: middle`
  - `.heartbeat polyline` stroke `var(--mint)` `stroke-width: 1.5px`、`fill: none`
  - state 別 stroke color: `.heartbeat[data-state="reconnect"] polyline { stroke: var(--peach); opacity: 0.7; }` / `[data-state="offline"] { stroke: var(--coral); opacity: 0.5; }` / `[data-state="static"] { display: none; }` (hidden 属性とのフォールバック二段保険)
  - `@media (prefers-reduced-motion: reduce) { .heartbeat { /* tick は JS 側で起動しない、CSS は hint */ } }` + 上記 reduced-motion ブロックで `.heartbeat polyline { animation: none; }`
  - `.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }` (heartbeatSr が visually-hidden に必要)
  - **note (Proposal 3 verify 結果)**: `dashboard/template/styles/` 全 7 ファイルに `.sr-only` / `visually-hidden` の既存定義は **無し** (grep verified)。今は heartbeat 専用 utility としてここに置く。将来別箇所で SR 用 visually-hidden を再利用する case が出たら `00_base.css` に昇格させる (deferred to that future PR)
- `dashboard/template/scripts/15_heartbeat.js` 新設
  - closure-private state は **既存の単一 shared IIFE 内** に直接宣言 (per-file IIFE では wrap しない、`25_live_diff.js` と同じ慣習)。全識別子を `__hb` prefix で名前空間隔離:
    ```js
    const SAMPLES = 60;
    const FRAME_PX_PER_SAMPLE = 1;
    const SPIKE_SHAPE = [-2,-5,-9,-7,-3,2,4,3,1,0];
    let __hbState = 'idle';
    let __hbBuf = new Float32Array(SAMPLES);
    let __hbSpikeRemain = 0;
    let __hbSpikeAmp = 1.0;       // online=1.0 / reconnect=0.3 / offline 等は bump 自体抑制
    let __hbRafId = null;
    let __hbReducedMotion = false;
    ```
  - `function startHeartbeat() { if (__hbRafId !== null) return; __hbReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches; if (__hbReducedMotion) { renderFlat(); return; } tick(); }`
  - `function stopHeartbeat() { if (__hbRafId !== null) { cancelAnimationFrame(__hbRafId); __hbRafId = null; } }`
  - `function setHeartbeatState(state)` — **全 state で `__hbSpikeAmp` を明示的に書き込む** (state transition race 防止 / Proposal 4):
    - `online` → `__hbSpikeAmp = 1.0`、tick 起動
    - `reconnect` → `__hbSpikeAmp = 0.3`、tick 起動
    - `offline` → `__hbSpikeAmp = 0.0`、tick 停止 + buf を全要素 0 に clear (flat)
    - `static` → `__hbSpikeAmp = 0.0`、tick 停止 + buf を全要素 0 に clear + `hidden` 属性付与 (将来 `hidden` 解除時に spike 残骸が出ないよう offline と同じく buf clear)
    - 未知 state → no-op (defensive)。`STATUS_LABEL` keys との 1:1 対応は Step 1 の literal pin で保証
    - SVG `data-state` 属性も同期書き込み
  - `function bumpHeartbeat()`:
    - reduced-motion 分岐 (Proposal 5 — 連発時の aria-live 再発火を保証):
      ```js
      if (__hbReducedMotion) {
        const sr = document.getElementById('heartbeatSr');
        if (sr) {
          sr.textContent = '';   // 一旦クリア (同一文字列の連続 set による aria-live no-op を防止)
          // microtask boundary を挟んで textContent を再書き込み — DOM diff として確実に発火させる
          Promise.resolve().then(() => { sr.textContent = '更新を受信しました'; });
        }
        return;
      }
      ```
    - `if (__hbState === 'offline' || __hbState === 'static') return;`
    - `__hbSpikeRemain = SPIKE_SHAPE.length;` // **duration 固定 10 frames**, 振幅は __hbSpikeAmp で別軸
  - `function tick()` — buf を 1 sample 左シフト、spike 残量があれば末尾に `SPIKE_SHAPE[len - remain] * __hbSpikeAmp` を書き込み (= **振幅スケール / duration スケールではない**)、なければ baseline 0 を書き込み、polyline.points を再構築。最後に `__hbRafId = requestAnimationFrame(tick);`
  - `window.__heartbeat = { bump: bumpHeartbeat, setState: setHeartbeatState, start: startHeartbeat, stop: stopHeartbeat, _buf: () => __hbBuf, _reset: function() { if (__hbRafId !== null) { cancelAnimationFrame(__hbRafId); __hbRafId = null; } __hbReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches; for (let i = 0; i < __hbBuf.length; i++) __hbBuf[i] = 0; __hbSpikeRemain = 0; } };` (`_reset` は **test 専用** hook: `start()` の re-entry guard を回避し `__hbReducedMotion` / buf / spike 残量を初期化する。production code から呼ばない)
- 期待: Step 1 / 3 の test がまだ fail (server.py / shell.html 未更新のため)

### Step 5 — Impl: shell.html / server.py / 10_helpers.js / 70_init_eventsource.js

**impl**:

- `shell.html`:
  - `<nav class="page-nav" role="navigation" aria-label="ダッシュボードページ">` の 4 リンクの後、`</nav>` 直前に:
    ```html
    <svg class="heartbeat" id="heartbeat" data-state="reconnect" viewBox="0 0 140 22" preserveAspectRatio="none" role="img" aria-label="ライブ更新インジケータ">
      <polyline points=""/>
    </svg>
    <span class="sr-only" id="heartbeatSr" aria-live="polite" aria-atomic="true"></span>
    ```
  - footer version は `v0.7.1` のまま据え置き (release PR で bump、`patch-release` skill convention)
- `server.py`:
  - `_CSS_FILES` に `"15_heartbeat.css"` を `"10_components.css"` の直後に追加 (cascade 順は components の後 / help_tooltip の前)
  - `_MAIN_JS_FILES` に `"15_heartbeat.js"` を `"10_helpers.js"` の直後・`"20_load_and_render.js"` の直前に追加
- `10_helpers.js`:
  - `setConnStatus(state)` の **冒頭** (= `getElementById('connStatus')` + `if (!el) return` ガードより前) に `if (window.__heartbeat) window.__heartbeat.setState(state);` を追加 (heartbeat sync を connStatus DOM 不在ガードから独立させる防衛措置)
- `70_init_eventsource.js`:
  - 先頭で (state init 前) `if (window.__heartbeat) window.__heartbeat.start();`
  - `es.addEventListener('message', (ev) => { ... if (refresh) { if (window.__heartbeat) window.__heartbeat.bump(); scheduleLoadAndRender()...; } })`
  - `__DATA__` 経路では `__heartbeat.start()` を呼ばず `setConnStatus('static')` (10_helpers.js 経由で hidden 化 + tick 抑制)
- 期待: Step 1 / 2 / 3 全部 green になる

### Step 6 — Failing test → green: template-split byte equivalence hash 更新

**test 更新 (cassette regenerate)**:

- `tests/test_dashboard_template_split.py` の `EXPECTED_TEMPLATE_SHA256` を新 hash に更新 + 履歴コメントに `: Issue #83 / live heartbeat sparkline` 行追記
  - workflow: Step 5 完了後に `pytest tests/test_dashboard_template_split.py::test_html_template_byte_equivalent_to_pre_split_snapshot` を実行 → mismatch error message に出る新 hash を `EXPECTED_TEMPLATE_SHA256` に貼り付け (= cassette regenerate pattern。手動 hash 計算は **しない**)
- `test_html_template_contains_critical_dom_anchors` の dom_id list に `"heartbeat"` / `"heartbeatSr"` を追加 (構造保証 二重化)
- 期待: green

### Step 7 — Manual QA (browser 実機 / chrome-devtools MCP 任意)

- ダッシュボード起動 → page-nav 右端に流れる line を 30 秒以上観察 (idle 動き続け確認)
- `touch ~/.claude/transcript-analyzer/usage.jsonl` または hook event を発火 → 山が一発立つ
- ネットワーク切断 → conn-status `reconnect` → 30s 後 `offline` → line 流れ停止確認
- ネットワーク再接続 → `online` 復帰 + 流れ再開
- export_html → 静的 HTML を別ホストで開いて heartbeat が表示されないこと
- `chrome://settings → Accessibility → Reduce motion` を ON で再 reload → flat line のみ + refresh で SR 通知 (VoiceOver / NVDA で読み上げ確認)

### Step 8 — Manual QA Checklist (PR description に貼る用)

```
[ ] live mode で page-nav 右端に heartbeat が見える
[ ] 30s 何もしなくても line が流れ続ける
[ ] hook event を 1 個発火 → 山が立つ (toast / .bumped と同時に動く)
[ ] 連続発火 (5 events / 1s) しても tick が乱れない
[ ] ネットワーク切断 30s+ で line が flat に止まる
[ ] 再接続で再開
[ ] Patterns / Quality / Surface に切替えても heartbeat が消えない
[ ] static export では heartbeat が消えている
[ ] prefers-reduced-motion で flat line + 受信時 SR 読み上げ
[ ] localhost dashboard を 5 分放置しても CPU が暴走しない (Activity Monitor で 5% 以下)
```

## 7. Risks / Tradeoffs

- **配置 (i) の改行リスク**: `nav.page-nav` は `flex-wrap: wrap` のため、極端に狭い viewport (~360px) で SVG が次行に折れる可能性。許容判断 — heartbeat は装飾要素であり、最低でも tab は切れず使える
- **狭幅 viewport での SVG 変形**: `viewBox preserveAspectRatio="none"` は狭幅で水平圧縮されスパイクが横長 blob 化する可能性。`min-width: 140px` で wrap 動作を歪めるよりは、装飾要素として変形を許容する選択 (4 タブの可読性 > heartbeat の形)。許容判断
- **`prefers-reduced-motion` 検出のタイミング**: `startHeartbeat()` で 1 度だけ snapshot し、user が後から motion 設定を切り替えても再評価しない。許容判断 — dashboard を reload する前提
- **reconnect 中の山立て抑制**: 「サーバが死んでて来てない」のに山が立つと誤情報。30% に減衰 + 色を peach にすることで「見えてはいるが live 値ではない」を視覚伝達。代替案として完全抑制も可だが、「再接続中も裏で何か聞こえてる」感を残すほうが Issue 趣旨 (= 動き続け) に近い
- **静的 export 整合**: `setConnStatus('static')` 経路と CSS `display: none` の二段保険で、JS 起動失敗時にも heartbeat が変な flat line だけ残らないよう守る (hidden 属性は SR にも非通知)
- **`requestAnimationFrame` の battery 影響**: 60fps × 60 sample で polyline reconstruct を毎 frame 行うが、SVG path diff は数 % CPU で済む。Tab 非表示時はブラウザが自動で `requestAnimationFrame` を 1Hz 以下に絞るので、background tab で battery を食わない (`hidden` document API への手動 hook は不要)
- **`__livePrev` パターンとの整合**: Issue #69 で確立した「closure 内 state を専用ファイル冒頭に置く」原則を踏襲する (`15_heartbeat.js` の冒頭で IIFE 直下宣言 → 70 番から参照)。再宣言禁止の literal pin (`test_hb_state_declared_only_in_15_heartbeat_js` — Step 1 を参照) を入れて regression を防ぐ
- **SVG `points` 文字列の length**: 60 sample × `"x.x,y.y "` で ~600 char × 60fps = 36KB/s のテキスト書換。長時間タブ放置で GC 圧。許容判断 — 既存 sparkline (overview) も同様の pattern。問題顕在化したら `requestAnimationFrame` を 30fps に absolute throttling

## 8. Branch / Milestone

- Branch: `feature/83-live-heartbeat` (`v0.7.2` から派生)
- Milestone: `v0.7.2`
- PR title: `feat(dashboard): ライブ更新インジケータ heartbeat sparkline を追加 (Issue #83)`
- 単発 PR で完結。stacked PR 不要 (Issue #69 のように multi-phase ではない)
