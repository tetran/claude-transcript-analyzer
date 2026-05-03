# Dashboard サーバー実装 — SSE / HTML embed / component composition

`dashboard/server.py` の実装上の非自明ポイントをまとめたリファレンス。stdlib のみで Server-Sent Events を提供し、`window.__DATA__` で初期データを注入し、pylint 違反を `.pylintrc` に逃さず合理的に解消した経緯。

---

## §1. stdlib http.server で SSE を実装する

`BaseHTTPRequestHandler` + `ThreadingHTTPServer` で `/events` を実装するときに、どの単一ドキュメントにも書かれていない 3 つの非自明要件。

### 要件 1: リクエストループの抑止

```python
def do_GET(self):
    if self.path == "/events":
        self.close_connection = True   # ← これが無いと ConnectionResetError
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        ...
```

`self.close_connection = True` を SSE handler の冒頭で立てる。これが無いと `do_GET` が return した後 `BaseHTTPRequestHandler.handle()` が次の `handle_one_request()` にループし、0 byte read で `ConnectionResetError` を stderr に吐く。

### 要件 2: ブロックしない peer disconnect 検知

```python
def _peer_disconnected(sock):
    """True if EOF/RST observed; False otherwise."""
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        if not readable:
            return False
        data = sock.recv(1, socket.MSG_PEEK)
        return data == b""  # EOF
    except (BlockingIOError, InterruptedError):
        return False
    except OSError:
        return True  # RST
```

`select.select([sock], [], [], 0)` + `sock.recv(1, MSG_PEEK)` で `self.connection` を非ブロッキング検査。EOF は `b""`、RST は `OSError`。

**書き込み失敗 (BrokenPipeError) を peer-disconnect 検知に使ってはいけない** — TCP send buffer が大量データを吸収し、`BrokenPipeError` が立つまでに数秒の遅延がある。

### 要件 3: peer-check と keepalive のケイデンス分離

```python
_SSE_PEER_CHECK_INTERVAL = 1.0  # seconds — 1 か所に集約するモジュール定数

# Naive (悪い):
while alive:
    send_keepalive()
    stop_event.wait(keepalive_seconds)   # 15s 寝る → disconnect 検知も 15s 遅れる

# Correct:
last_keepalive = time.monotonic()
while alive:
    tick = min(keepalive_seconds, _SSE_PEER_CHECK_INTERVAL)
    if stop_event.wait(tick):
        break
    if _peer_disconnected(self.connection):
        break
    now = time.monotonic()
    if 0 < keepalive_seconds <= now - last_keepalive:  # chained compare (R1716)
        send_keepalive()
        last_keepalive = now
```

ポイント：

- `stop_event.wait(tick)` でブロック時間を `_SSE_PEER_CHECK_INTERVAL=1.0` に制限
- Keepalive 送信は **時刻ベース**（`now - last_keepalive >= keepalive_seconds`）で判定
- `keepalive_seconds=0` は「無限・送らない」のセンチネル。`if 0 < keepalive <= now - last_keepalive` の **chained 比較** で扱う（pylint R1716 にも適合）

### 形式と broadcast

- SSE comment（`: ...\n\n`）は `onmessage` を発火しない。**ヘッダ flush 直後の初回送信** と **keepalive ping** に使う（`onopen` を即座に発火させる用途）
- 複数クライアント broadcast はスナップショットパターンで安全化：

```python
def broadcast(self, payload):
    with self._lock:
        snapshot = list(self._clients)
    dead = []
    for client in snapshot:
        try:
            client.send(payload)
        except (BrokenPipeError, OSError):
            dead.append(client)
    if dead:
        with self._lock:
            for c in dead:
                self._clients.discard(c)
```

ロック内でスナップショット → ロック外で送信 → ロック内で死亡クライアント reap。長い write が他の登録をブロックしない。

### スケール限界

`select.select` は数〜数十クライアントまで。100+ クライアントが現実味を帯びたら asyncio に移行。stdlib SSE は **≤10 同時接続** の dashboards に最適な pragmatic bridge。

---

## §2. JSON-in-`<script>` 埋め込みの escape

`window.__DATA__ = { ... }` で初期データを HTML に埋め込むときの **必要かつ十分な** escape は次の 1 行：

```python
script_payload = json.dumps(data).replace("</", r"<\/")
html = f"<script>window.__DATA__ = {script_payload};</script>"
```

これだけで HTML5 script-data-state の **両方の脱出経路** を閉じる。

### HTML5 script の脱出経路（仕様 § 12.2.5.4）

1. **Path 1（直接）**: `</script>` end-tag が即時に script を閉じる
2. **Path 2（comment 経由）**: `<!--` で script-data-escaped state に入り、その状態でも `</script>` を見ると閉じる

両方とも `</` を必ず含むので、`</` を `<\/` にすれば 1 発で塞がる。

### よくある間違い: 過剰 escape で JSON を壊す

`<!--` を別途エスケープしようとして `replace("<!--", r"<\!--")` を追加すると、`\!` が **JSON RFC 8259 違反** で `JSON.parse` がエラーを吐き、dashboard が white-screen する。

JSON RFC 8259 § 7 で valid な string escape は `\"`, `\\`, `\/`, `\b`, `\f`, `\n`, `\r`, `\t`, `\uXXXX` の **9 種のみ**。`\!`, `\$`, `\<` 等はすべて parse error。

しかも `<!--` 単体（`</script>` を伴わない）は実際には script context を抜けない（script-data-escaped state から出るには `</script>` が必要）。よって `<!--` の defensive escape は **冗長**。

### Round-trip 検証

defensive escape を 2 つ以上重ねたくなったら、必ず JSON.parse 往復を assert：

```python
import json
result = json.dumps(input).replace(...)  # escape チェーン
assert json.loads(result) == input  # round-trip 必須
```

文字列の中に `<\!--` が現れる」だけを assert する単体テストでは byte-level 等価性しか見ないので、`JSON.parse` 失敗を検出できない。

### `<!--` を文字列として完全保護したい場合（稀）

`replace("</", ...)` で塞がらないケースが本当に必要なら、JSON Unicode escape を使う：

```python
# JSON-valid: < で < を encode
result = json.dumps(input).replace("<", r"<")
```

これは round-trip するし、`<!--` も `<!--` になって HTML parser に届かない。

---

## §3. Component composition pattern（pylint R0902 対策）

`dashboard/server.py` の class が `>7 self.X = ...` で R0902 (`too-many-instance-attributes`) に触れたとき、**コンポーネント分解 + `@property` shim** で解消。3-tier escalation policy（refactor → local-disable → `.pylintrc` tweak）の最上位「refactor」で着地した実例。policy 自体は **global `~/.claude/CLAUDE.md` の Scope Discipline** に記載されており、本ドキュメントは project 固有の **architecture decision の記録**。

### 分解後の構造

| Component | 責務 |
|---|---|
| `_IdleTracker` | idle 時間の touch/idle_for/watchdog |
| `_SseState` | SSE clients / lock / keepalive 状態 |
| `_FileWatcher` | usage.jsonl の path / poll interval / 監視 thread |

各 component は private（`_idle`, `_sse`, `_watcher`）。public API はサーバークラス側に **1 行 delegate** メソッドを持たせて維持：

```python
class DashboardServer(...):
    def __init__(self, ...):
        # Components MUST be initialized BEFORE super().__init__()
        # (super() may call self.server_close() on bind failure;
        #  if components aren't ready, AttributeError masks the original OSError)
        self._idle = _IdleTracker(...)
        self._sse = _SseState(...)
        self._watcher = _FileWatcher(...)
        super().__init__(...)

    def touch(self):                    # 1-line delegate
        self._idle.touch()

    @property
    def idle_seconds(self):             # backward-compat shim for existing tests
        return self._idle.idle_seconds
```

### `@property` shim の効果

- 既存テストが `server.idle_seconds` / `server.poll_interval` で読んでいた public 属性を **そのまま維持** できる（テスト書き換え不要）
- `@property` getter は pylint の instance-attribute count に **入らない** ので R0902 が解ける
- CPython 3.11+ で関数呼び出し 1 回分のオーバーヘッドのみ。dashboard のスケール（≤10 req/s）では不可視

### 初期化順序の罠

```python
def __init__(self, ...):
    # ❌ NG: super() が先だと bind 失敗時に AttributeError でマスクされる
    super().__init__(...)
    self._idle = _IdleTracker(...)

    # ✅ OK: components が先
    self._idle = _IdleTracker(...)
    super().__init__(...)
```

`super().__init__()` は bind 失敗時に防御的に `self.server_close()` を呼びうる。`server_close` が component 属性を参照していると、未初期化で `AttributeError` が立ち、本来の `OSError`（ポート衝突など）の情報が失われる。**Components init → super().__init__() の順序を守るコメントを `__init__` 直上に書くこと。**

### 何故 `__getattr__` を使わないか

`__getattr__` で透過的に component メソッドを呼べそうに見えるが、

- 「どの component の何を呼んでいるのか」が読み手に見えなくなる
- typo した時の error が遅延する（`AttributeError` が `__getattr__` の中で出る）
- pylint / mypy が解析できない

明示的 1 行 delegate のほうが冗長でも保守性で勝つ。

---

## §4. Template 分割 — 起動時 concat (Issue #67)

`dashboard/template.html` (123 KB / 2886 行) を `dashboard/template/` 配下の shell + styles + scripts に分割し、`server.py` が起動時に concat して `_HTML_TEMPLATE` を組み立てる。export 経路 (`render_static_html` の `</head>` replace) や live 経路の前提は無改修。

### ディレクトリ構成

```
dashboard/template/
├── shell.html              # head + nav + 4 page sections + footer。__INCLUDE_*__ センチネル 3 つ
├── styles/
│   ├── 00_base.css         # root vars / reset / body / .app
│   ├── 10_components.css   # header / live badge / KPI / panel / two-up / ranking / spark / projects / footer
│   ├── 20_help_tooltip.css # help button + data tooltip
│   ├── 30_pages.css        # multipage shell (Issue #57)
│   ├── 40_patterns.css     # heatmap + cooccurrence + project×skill (Issue #58/59)
│   ├── 50_quality.css      # subagent percentile/failure + permission + compact (Issue #60/61)
│   └── 60_surface.css      # Surface 3 panel + tooltip border (Issue #74)
└── scripts/
    ├── 00_router.js              # hash router IIFE (独立 <script>)
    ├── 10_helpers.js             # esc / fmtN / pad / setConnStatus
    ├── 20_load_and_render.js     # async loadAndRender (KPI / ranking / sparkline / projects)
    ├── 30_renderers_patterns.js  # heatmap / cooccurrence / matrix renderers
    ├── 40_renderers_quality.js   # percentile / failure / permission / compact renderers
    ├── 50_renderers_surface.js   # Surface invocation / lifecycle / hibernating + fmtDur
    ├── 60_hashchange_listener.js # hashchange → loadAndRender 再実行
    ├── 70_init_eventsource.js    # 初回描画 + EventSource (live refresh)
    ├── 80_help_popup.js          # help popover behavior
    └── 90_data_tooltip.js        # [data-tip] graph data tooltip
```

### Sentinel 戦略

`shell.html` には 3 つの `__INCLUDE_*__` センチネルを **単独行** で配置する。`server.py` の `_build_html_template()` がそれぞれを styles / router / main の concat 結果で置換する：

```python
return (shell
        .replace("__INCLUDE_STYLES__\n", styles)
        .replace("__INCLUDE_ROUTER_JS__\n", router_js)
        .replace("__INCLUDE_MAIN_JS__\n", main_js))
```

ポイント：

- 置換対象が `__INCLUDE_*__\n` (改行込み) なので、置換結果側に重複改行が入らない
- 各 split file は元 `template.html` の **連続スライス** で、concat で改行や空行が完全に再現される (byte 等価)
- `tests/test_dashboard_template_split.py::test_html_template_byte_equivalent_to_pre_split_snapshot` が sha256 で loss-less を保証

### Where to add what

| 追加したいもの | 編集先 |
|---|---|
| 新しい `<section data-page="...">` ページ | `shell.html` (HTML 構造) + `template/styles/X_<page>.css` 新設 + `template/scripts/X_renderers_<page>.js` 新設 + `server.py` の `_CSS_FILES` / `_MAIN_JS_FILES` tuple に追加 |
| 既存ページの新 panel | 該当 page の `*.css` / `*.js` に追記 (ファイル名は据え置き、内部のみ更新) |
| 共通 helper / renderer | `10_helpers.js` (state-less util) / `20_load_and_render.js` (loadAndRender 直下に hook を追加) |
| 共通スタイル (KPI / panel / ranking など) | `10_components.css` |
| 起動 IIFE 内の subscriber (新 EventSource event 型 / 新 keydown handler) | `70_init_eventsource.js` / `80_help_popup.js` のいずれか分担に従う |

### 注意点

- **連結順は固定**。CSS のカスケード順 / JS の TDZ 配置を変えるとレイアウトや初期化順が壊れる。`_CSS_FILES` / `_MAIN_JS_FILES` tuple のコメントが分担表
- **新 split file 追加時は `server.py` 側の tuple も更新**。tuple に無いファイルは concat されない
- `(async function(){` / `})();` の IIFE wrapper は **shell.html 側に置いている**。split file は IIFE body の連続スライスのみ含み、自前で IIFE を開閉しない
- byte 等価 smoke test (sha256) は強い regression guard。意図的に template を変更したら期待値の hash を更新する

---

## §5. Client-side TZ 変換 — UTC で送り local で見せる (Issue #58, #65)

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

## §6. Multi-page SPA shell — router/SSE 直交設計

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

## §7. Sparse server / dense client — time-series axis contract

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

## §8. Dict iteration order を JSON contract として保つ

server が dict を JSON で返し、`memory_type_dist` のように
**「iteration order = count desc → key asc」** を契約として保証している
ケース (Issue #62 など)。Python 3.7+ + `json.dumps` (no `sort_keys`) +
ECMAScript spec の 3 層で order が保たれる。

### 3 層の保証チェーン

| 層 | 保証 |
|---|---|
| **(1) Python 3.7+ dict** | insertion order を保つ言語仕様 |
| **(2) `json.dumps`** | dict を iteration 順に出力 (`sort_keys=False` がデフォルト) |
| **(3) ECMAScript** | 仕様で string key の挿入順保持を規定 (`JSON.parse` で順序が保たれる) |

3 層中どれが破れても **silently wrong** に corrupt する (例外は出ない)。

### 自然敵 — `json.dumps(..., sort_keys=True)`

refactor で「deterministic 出力」「diff 読みやすさ」を理由に reflex で足
される。1 flag で server-side dict order が壊れる。**call site から見えない
契約** なので review 時に気付けない。

### Roundtrip regression test (load-bearing artifact)

```python
def test_dict_iteration_order_survives_json_roundtrip(self):
    out = aggregate_X(events)
    roundtripped = json.loads(json.dumps(out))
    assert list(roundtripped["the_dist"].keys()) == ["expected", "order"]
```

これが **持続的 guard**。docstring / spec doc / memory file は人が読むだけ
で機械的検証は無いので、test を書く。

### 一時的 guard — 実装時の grep

```bash
grep -n 'sort_keys' dashboard/server.py reports/
```

これは **実装時の 1 度限り** の確認。将来 PR への持続的 guard にはならない
(test がそれ)。

### Aggregator docstring に caveat を残す

```python
def aggregate_X(events):
    """...
    json.dumps(..., sort_keys=True) を serialize 経路に混入させると本契約が
    破壊される。test_dict_iteration_order_survives_json_roundtrip が
    regression guard。
    """
```

### List-of-dicts vs dict-with-order-contract

| 軸 | dict + contract | list of `{"key": k, ...}` |
|---|---|---|
| Schema 表現の自然さ | 高 (「観測 key → count」) | 中 (二重表現) |
| JSON サイズ | 小 | 大 |
| consumer の access pattern | keyed lookup OK | 二度 iterate が必要 |
| order 保証の強さ | 3 層依存 | 1 層 (list 自体) |
| 1 contract 追加で増える test | regression test 1 本 | なし |

**consumer 数が ≥3** なら list-of-dicts の方が安全 (explicit さが pay off)。
1–2 consumer なら dict + contract も許容。

### ECMAScript 版数の citation 注意

「ECMAScript 2020+ で …」のように年号 / 番号を citation するのは
confabulation 高リスク (CLAUDE.md "Number-shaped technical identifiers"
参照)。「ECMAScript 仕様で string key の挿入順保持が規定されている」と
書く方が安全。

---

## §9. Retention-aware aggregator — defensive cap が trend を歪める罠

`aggregate_skill_lifecycle` (Surface tab Issue #74) で `observation_days =
min(180, max(days_since_first, 1))` という defensive cap を初版で入れていた
が、plan-reviewer がバイアスを catch:

| ケース | overall_rate | recent_rate (last 30d) | ratio | trend 判定 |
|---|---|---|---|---|
| **cap=180** (200 events / 365 日 → 180) | 200/180 = 1.11/d | 1.0/d | 0.90 | `stable` |
| **cap 撤廃** (200 events / 365 日) | 200/365 = 0.55/d | 1.0/d | 1.83 | `accelerating` |

cap が **古い skill の acceleration を `decelerating` 寄りに silent mask**
していた。撤廃 (commit "Q2: cap 撤廃")。

### Bias は方向 1 で危険

denominator cap は **`overall_rate` を inflate** する → `recent / overall`
比は下がる → `decelerating` 寄り。**asymmetric bias** は real signal と
誤認しやすい (「古い skill は使われなくなる」という妥当に聞こえる story を
fake data が支える)。

### 上位 retention bound を確認するルール

`min(N, ...)` cap を denominator に入れる前に: **既に上流の retention で
N が bound されているか?**

- yes (cap 値 ≧ retention) → cap は dead code、しかし
  `--include-archive` 経由など retention bypass パスで bias を発動する
- no (cap 値 < retention) → cap が second window を作る、downstream には
  invisible

本リポは **hot tier 180 日 retention** が dashboard データを自然に bound
するので、`observation_days` への cap は redundant + 害 (archive 込み path
で bias 発動)。

### Spec wording の例

「`observation_days` に cap を置かない — 本リポの 180 日 retention が
dashboard データを自然に bound、`--include-archive` パスは意図的に広い窓を
取る」を spec に書いておくと future PR の "let's add a safety cap" reflex を
止めやすい。

### Test guard

`first_seen` / `last_seen` パターンの metric には、**N より長い span の
データ** を fixture に入れて trend 判定を assert する unit test を書く。
将来「cap を足したい」PR が test を破る → review で止まる。

### 教訓

- defensive cap の働く向きを **数値例 2 ケース (with / without)** で具体
  確認してから入れる。「feels safer」で入れない
- 「bias 方向は 1 つしか無い」cap は最も発見が遅い defect (real signal と
  区別できない)
- 上流に既に bound がある量に下流で cap を被せると、bypass path で bias を
  発動する hidden surface が増える

