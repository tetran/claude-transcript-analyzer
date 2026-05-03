# Dashboard クライアント実装 — TZ / SPA / SSE 受信 / fetch / UI label

`dashboard/template/` 配下のフロントエンドが背負う実装契約をまとめたリファレンス。stdlib http.server から渡された UTC データを local TZ で見せる、4 page SPA を hash router で切替、SSE で live update し、fetch overlap を coalesce-while-busy で防ぎ、cross-surface UI label の整合性を audit する経緯。

サーバー側 runtime (SSE / template / IIFE 規約) は `dashboard-server.md`、aggregator 契約 (dict 順序 / retention cap / drift-guard / period filter) は `dashboard-aggregation.md` を参照。

---

## §1. Client-side TZ 変換 — UTC で送り local で見せる (Issue #58, #65)

dashboard frontend は **server から UTC で受け取り、client 側で local TZ に変換**
する分担。「server に client TZ を確実に教える経路が無い」(cookie / header /
query は SSE と相性が悪い) のと、「DST 境界は `Date` の native methods が正しく
扱える」のが採用理由。

### 該当箇所と入力

| 場所 | server output | frontend 変換 |
|---|---|---|
| Patterns hourly heatmap | `hourly_heatmap.buckets` (UTC hour) | `getDay()` / `getHours()` で local の `(weekday, hour)` 7×24 matrix に bin (`30_renderers_patterns.js`) |
| Overview sparkline | `hourly_heatmap.buckets` (= 同上) | `localDailyFromHourly(buckets)` (10_helpers.js) で local 日付集約 → `[{date, count}]` |
| header「最終更新」 | `last_updated` (ISO 8601 with `+00:00`) | `formatLocalTimestamp(iso)` (10_helpers.js) で `"YYYY-MM-DD HH:mm <TZ>"` |

`subagent_failure_trend` は **Mon 00:00 UTC 起算** で固定 (= server pre-bin 済み
の week_start を使う)。Issue #65 の射程外。

### 実装の罠 — `toISOString` を使わない

`toISOString().slice(0, 10)` は **UTC 日付** を返すため、local TZ 集約には
**使ってはいけない**。`localDailyFromHourly` / sparkline densify は
`getFullYear()` / `getMonth()` / `getDate()` を手組みで連結して key を作る:

```js
const key = dt.getFullYear() + '-' + pad(dt.getMonth()+1, 2) + '-' + pad(dt.getDate(), 2);
```

densify (観測 0 の中間日も x 軸に並べる) も同様に `new Date(y, m-1, d)` で
local cursor を作り `setDate(+1)` で進める。`new Date(date+'T00:00:00Z')` +
`setUTCDate(+1)` 経路は UTC 日付を吐くので避ける。`Date` constructor は月 / 年
またぎを自動補正するため `setDate(32)` 等でも正しく wrap する。

### TZ 短縮名は環境依存

`Intl.DateTimeFormat(undefined, { timeZoneName: 'short' })` の出力は
**ブラウザ / OS / locale 依存**。本リポジトリで実観測済みの組み合わせ:

- Node v24 + macOS: `"GMT+9"` (Issue #65 検証時)

ブラウザ実機 (Safari / Edge / Firefox / 旧 Chromium) は未検証。`"JST"` を返す
環境がある報告は古くから一般的だが、自リポジトリでの pin は持たない。仕様
としては固定しない (= test pin は正規表現
`^\d{4}-\d{2}-\d{2} \d{2}:\d{2} \S+$` で吸収)。ユーザー報告で表記が揺れる場合は
"環境依存" として説明する。

### export_html を別ホストで開いた場合

`reports/export_html.py` は `<script>window.__DATA__ = ...</script>` でデータを
inline するが、`Date` の TZ 変換は **閲覧ホスト** で実行される。生成ホスト ≠
閲覧ホスト の場合 (例: JST マシンで生成 → CET マシンで閲覧) は **閲覧ホスト**
の TZ で表示される。これは仕様 (受け手の体感に合わせるほうが自然)。

---

## §2. Multi-page SPA shell — router/SSE 直交設計

dashboard は単一 HTML ページの SPA で、4 つの page (`#/`, `#/patterns`,
`#/quality`, `#/surface`) を **DOM-resident sections + `hidden` toggle** で
切り替える (Issue #57)。route 切替と SSE refresh が **完全に直交** している
のが設計の肝。

### 直交の方法 — DOM 残置 + hidden toggle

```html
<section data-page="overview">  ...全 widget...  </section>
<section data-page="patterns" hidden>  ...全 widget...  </section>
<section data-page="quality"  hidden>  ...全 widget...  </section>
<section data-page="surface"  hidden>  ...全 widget...  </section>
```

- すべての `<section>` が **常に DOM 内にいる**。route 切替は `hidden`
  attribute と `aria-current` / `body[data-active-page]` の更新のみ
- 既存 renderer (`loadAndRender()`) は `getElementById('kpiRow')` 系の
  **絶対 lookup** を使い続けられる (要素がまだ document に存在するため)
- SSE refresh が hidden page も含む全 section を upserts → 戻ってきた時に
  最新

### Router の責務分離

router IIFE (`00_router.js`) は

- `HASH_TO_PAGE` テーブル (`#/` / `#/patterns` / `#/quality` / `#/surface`
  → page 名)
- `applyRoute()`: hidden toggle + dataset 更新
- `hashchange` listener (router 自身の)

の 3 機能 **だけ**。データフローを知らない。Main IIFE
(`70_init_eventsource.js` 等) が SSE / `loadAndRender` を担当。

### Page-scoped 早期 return + 独立 hashchange listener (Issue #58)

重い renderer (例: `renderHourlyHeatmap`) は cost-control で
**page-scoped early-out** を入れる:

```javascript
function renderHourlyHeatmap(data) {
  if (document.body.dataset.activePage !== 'patterns') return;
  // heavy render only when patterns is active
}
```

問題: `#/` 起動 → `#/patterns` 遷移 で section の hidden は解けるが、
widget の DOM は空のまま (前回の `loadAndRender` 時点では
`activePage='overview'` で early-out していた)。

解決: **main IIFE 側に独立 hashchange listener** を持たせ、route 切替で
`loadAndRender()` を再実行:

```javascript
window.addEventListener('hashchange', () => {
  loadAndRender().catch((err) => console.error('route change render 失敗', err));
});
```

### Listener 順序の保証

`addEventListener` callback は **登録順** で発火する。HTML template で
`00_router.js` を `70_init_eventsource.js` より前に置いてあるので:

1. router IIFE listener が先に走り `body.dataset.activePage = 'patterns'`
2. main IIFE listener が後で走り `loadAndRender()` 再実行 → renderer の
   early-out が pass する

この順序保証は **template の concat 順** に依存する (`server.py` の
`_MAIN_JS_FILES` tuple)。順序が崩れると early-out が pass せず空 widget の
ままになる。

### 設計コア

- **router の job = state**, **SSE の job = side-effect**, **renderer は
  両者の交差点**
- 「mount/unmount per route」より「DOM 残置 + visibility toggle」が
  既存 renderer の絶対 lookup を壊さないため有利
- 新しい page-scoped widget を足す時:
  1. `loadAndRender()` の中に renderer 呼び出しを追加 (SSE refresh + 初回
     boot 両方で発火させる)
  2. renderer 冒頭に `if (body.dataset.activePage !== '<page>') return;`
  3. **新しい hashchange listener は足さない** — main IIFE に既存

### 直交のテスト

「Page B に遷移しても Page A の元 DOM は live」を assert:
chrome-devtools MCP で hidden Page A の content を取り、SSE で usage.jsonl
に append → reread して update を確認。**static export (export_html)** は
`loadAndRender` を起動時 1 回しか呼ばないので、初期 hash で決まる first
paint がそのまま固定。

### 罠

- **`href="#/x"` で済むものを `click` ハイジャックしない** — browser back/
  forward / direct URL bookmark / keyboard Enter は native で動く
- **空 hash の fallback table** は `""` / `"#"` / `"#/"` の 3 形を全て
  default route にマップ。`||` fallback は unknown key しか catch しない
- **`window.dispatchEvent` 等の cross-IIFE pub/sub に頼らない** — 同 event
  に独立 listener 2 つで十分

---

## §3. Sparse server / dense client — time-series axis contract

時系列 chart で **server は zero buckets を意図的に omit** して dense 表現
を返さない (API minimal)。client が **calendar gap を可視化するために axis
densify** する責務を持つ — 怠ると無観測週が消えて時間軸が嘘をつく。

### Bug 例 (Issue #60 で踏んだ)

```javascript
// ❌ NG — 観測のみで axis を作る
const weeks = [...new Set(items.map(r => r.week_start))].sort();
// weeks = ['2026-04-13', '2026-04-27']  // W2=04-20 が消える
const xOf = (i) => padL + innerW * i / (weeks.length - 1);
// → W1 と W3 が隣接 x 位置にレンダされ、無観測週が見えない
```

```javascript
// ✅ OK — 観測区間を densify
const observed = [...new Set(items.map(r => r.week_start))].sort();
const weeks = [];
const cursor = new Date(observed[0] + 'T00:00:00Z');
const end = new Date(observed[observed.length - 1] + 'T00:00:00Z');
while (cursor <= end) {
  weeks.push(cursor.toISOString().slice(0, 10));
  cursor.setUTCDate(cursor.getUTCDate() + 7);
}
// weeks = ['2026-04-13', '2026-04-20', '2026-04-27']
```

### 直交する 2 つの failure mode

両方 fix が必要:

| Failure mode | 例 | Fix |
|---|---|---|
| **Per-type polyline 橋渡し** | type X: i=0, i=2 観測 → polyline が i=1 を直線で結ぶ (型 Y は i=1 観測あり) | gap で polyline を **run に分割** |
| **Global axis 崩壊** | 全 type で i=1 が無観測 → server が omit → axis から消える | 観測区間の **union timeline で densify** |

「2 つ揃ってるか?」を sparse-data viz の review チェック項目に。

### API spec の書き方

server-side sparseness と client-side densify 責務を **両側 document**
する。spec doc が「観測なし bucket は配列に含まれない」だけだと、次の
consumer は同じバグを再構築する。`dashboard-api.md` 側に
「**client は axis densify を実装する責務**」を明記。

### 実装メモ

- `Date.setUTCDate(getUTCDate() + 7)` が UTC 週進めの cheap で native な
  方法。DST edge case を回避 (UTC は DST 無し)
- 不正入力に備えて safety cap (e.g. 1040 週 ≒ 20 年) を入れる
- 該当箇所: `30_renderers_patterns.js` の week-bin 構築 / `10_helpers.js`
  の `localDailyFromHourly` (sparkline densify はこちらで)

---

## §4. EventSource error path ≠ `loadAndRender()` の try/catch path

dashboard live update には **2 つの独立した error 経路** がある。user 視点では「ダッシュボードが反応しない」に見えても JS runtime では決して交わらない。これを混同すると「catch path を verify する」と称した PR checklist が **catch path を一度も exercise しない** まま通過する。

| 起こした事象 | 担当 | 復旧経路 | `loadAndRender()` の catch policy |
|---|---|---|---|
| **Server kill / SSE channel break** | `EventSource` 内蔵の error/reconnect (`70_init_eventsource.js` の `addEventListener('error', ...)`) | `setConnStatus('reconnect' \| 'offline')` → 次の `message`/`refresh` で silent 復旧 | ❌ 呼ばれない |
| **`/api/data` の 500 (SSE は alive)** | `loadAndRender()` の `try { await fetch(...) } catch (e) { console.error(...); return; }` | 直接 catch block | ✅ ここで足した policy だけが発火 |

### 検証 recipe を error path に合わせる

`loadAndRender()` の catch policy (例: 「catch 時に `__livePrev` を更新しない」) を verify する re-pro は **server-kill ではない** — `/api/data` handler に一行 `raise RuntimeError("forced 500")` を inject して revert する形にする。

逆に **EventSource resilience** (reconnect badge / offline indicator) の re-pro は server-kill で、`/api/data` 500 では発火しない。

**「両方を1シナリオで verify」は不可能** — state を共有しない。checklist が両方を claim しているなら 2 項目に分割する。`USAGE_DASHBOARD_FORCE_500=1` 系の dev-only env-var を入れたくなるが scope 拡大なので、ad-hoc 検証は patch+revert で済ませるのが軽い。

### 関連 source

- `dashboard/template/scripts/70_init_eventsource.js` — EventSource error / `OFFLINE_AFTER_MS=30000` semantics の権威
- `dashboard/template/scripts/20_load_and_render.js` — `loadAndRender()` の try/catch

---

## §5. Fetch overlap stale-snapshot race と coalesce-while-busy

dashboard で `fetch('/api/data')` を **複数の async source** (SSE refresh / hashchange / polling) から fire すると、ThreadingHTTPServer + browser の **同一 origin 6 並列接続** で実観測レベルで out-of-order に response が返る (server-side processing time variance だけで arrival 順は flip する)。HTTP/1.1 keep-alive は同一 TCP 接続内でしか order を保証しない。

### Damage は二重 — request-id at commit-site fix では半分しか防げない

| 損害 | 発生箇所 | request-id at commit-site で守れる? |
|---|---|---|
| (a) DOM `innerHTML = ...` で stale data を repaint | render の途中 | ❌ commit-site より早い |
| (b) `commitSnapshot(next)` が stale baseline を `__prev` に書き、次の diff が regressed delta を over-count | render の終端 | ✅ commit-site で防げる |

外部 reviewer (Codex) が指摘するのは大抵 (b) だけ。同じ fetch overlap が (a) も起こすので **entry-point で serialize しないと (a) は放置** される。「**bot のレース指摘を verify するときは function 内の work cascade 全体を trace** する」が教訓。

### Coalesce-while-busy recipe

```js
let __active = null;
let __pending = false;
function schedule() {
  if (__active) { __pending = true; return __active; }
  __active = Promise.resolve()
    .then(() => doWork())
    .finally(() => {
      __active = null;
      if (__pending) { __pending = false; schedule(); }
    });
  return __active;
}
```

性質:
- in-flight は **常に 1 個まで**
- queue cap = 1 (caller が何個来ても pending 1 個に coalesce)
- 完了後に pending があれば 1 回だけ追走
- coalesced caller には同じ promise を返す

### 適用範囲 — fire-and-forget な loadAndRender 呼び出しは全部 `schedule()` 経由

SSE message handler / hashchange listener / polling / 手動 refresh / **起動時の初回 `await loadAndRender()` 自身**まで全部 `schedule()` 経由にする。最後を入れると hashchange-during-init も coalesce されて uniformly defended。

### Test 戦略

serialize は **wrapper 関数 (`schedule()`)** で test する。模擬 overlap で `['start1','end1','start2','end2']` の strict serial 順序を assert + 3+ 同時呼びが pending 1 個に coalesce することも assert。Node `-e` で mock した `loadAndRender` で十分、real DOM 不要。

### State の置き場所

`__active` / `__pending` は `__livePrev` などの cross-cutting render state と同じファイル (`25_live_diff.js`) に置く。**TDZ 順序問題を起こさず**、IIFE 直下の宣言ブロックを discoverable に保つ (`dashboard-server.md` §4「Single async IIFE 直下の shared closure 規約」)。

### 実用性 vs cost

localhost (<50ms typical) + file-watcher poll 1s coalescing で reachability は低い。しかし fix は ~15行 + test 数本で **race 全クラスを structurally 消せる**。diff/highlight semantics を入れる same-PR で同時に入れる価値はある。

---

## §6. Cross-surface UI label 整合性監査 (helpTtl / k / th / tooltip)

dashboard で同一概念 (例: 「compaction」) は **3+ 面に同時に出現** する:

| surface | 例 (Issue #89 で踏んだ 3-form drift) |
|---|---|
| KPI tile **`helpTtl`** (popup タイトル) | `"Compact 数"` |
| KPI tile **`k`** (tile 内の小さい label) | `"コンテキスト圧縮"` |
| 表 **`<th>`** (列見出し) | `"圧縮回数"` |
| `[data-tip] lbl` (chip caption) | `"Compact"` |

surface ごとに孤立して翻訳ポリシーを適用すると、**同じ概念が 3 種類の表記で同時に画面に出る**。Issue #89 plan-reviewer iter 4 がこの 3-form 不整合を catch した。単一 surface review では各々が局所最適に見えるので、**cross-surface 比較しないと出ない**。

### 整合性 table を plan の §2.x に置く

「翻訳すべき word の list」だけでは不十分。**識別子 × surface 軸の table** を plan に明記する:

| id | k | helpTtl | s | th | tooltip lbl | 決定 |
|---|---|---|---|---|---|---|
| compact (NG) | コンテキスト圧縮 | Compact 数 | (none) | 圧縮回数 | Compact | ❌ 3 形 |
| compact (OK) | Compact | Compact 数 | (none) | Compact 回数 | Compact | ✅ 統一 |

各 cell に **default rule に頼らず実際の最終文字列** を書く。「default で英語維持」のような policy 名を cell に書くと意思決定の grain が粗すぎて 3-form drift を再生する。**concept-level resolution を強制する** ことが table の役割。

### 「ファミリー全体整合性」sub-section

高頻度な識別子は plan に明示的な見出しを切る:

> **Compact ファミリー**: helpTtl + k + th + tooltip lbl 全て英語維持 (`Compact`, `Compact 数`, `Compact 回数`, `Compact`)

### Test 5-style assertion との相互作用

「全 `helpTtl` が non-ASCII であること」を `not re.fullmatch(r"[\x20-\x7E]+", ttl)` で hard-fail させる test は、未触の英語 `helpTtl` を 1 個ぶつけるだけで silent な side-fix を強制する。**整合性 table が catch しないと test が「side-fix は是」のように振る舞う**。plan を書く段で table → mental dry-run → 通らない row があれば disposition を明示 (translate するか、test を scope-down するか) する。

### Forbidden / required test list は per-surface で書く

「`compact` の th が `圧縮` を含まない」「`compact` の helpTtl が `Compact 数` で始まる」のように **surface ごとに pin**。1 識別子 = 1 entry にせず、その識別子が触れる surface の数だけ entry を立てる。

### Bug-deferral pattern との相互作用

key mismatch を残しつつ display string だけ揃える bug-deferral pattern (chip-tooltip parity 等) も cross-surface 整合性契約。**display 揃えと key 残しは別レイヤの decision** なので table も別軸として併記する (key axis vs display axis を混ぜない)。

