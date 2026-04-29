# Issue #58 Plan — 時間帯ヒートマップ (A1)

## 🎯 Goal

ユーザーが Claude Code を **いつ** 使っているかを 7 (weekday) × 24 (hour) のヒート
マップで可視化し、深夜偏重 / 平日午前の薄さ / 特定曜日の突出 を一目で把握できる
ようにする。Issue #57 の multipage shell が用意した Patterns ページの placeholder を
実 widget で置き換え、SSE refresh / static export / 多 TZ 環境で破綻しない実装に
する。Tier A の最重要 insight (A1) を本 PR で完結させる。

## 📐 機能要件 / 構造設計

### データ schema 確定 — option (3) hour-bucketed UTC を採用

Issue 本文では (1) per-event raw timestamp と (2) UTC 7×24 pre-bin の二案だったが、
本 plan では **(3) hour-bucketed UTC bucket 列** を推奨。各案の trade-off:

| 案 | payload 上限 (180 日) | TZ 変換精度 | 実装複雑度 | 採否 |
|---|---|---|---|---|
| (1) per-event raw | 数万〜数十万件 (`skill_tool` の頻度次第) | 完全 lossless (browser が timestamp 全件再 bin) | 低 (server 側は events をそのまま流す) | **不採用** — payload が大きすぎ SSE refresh 帯域を圧迫 |
| (2) UTC 7×24 pre-bin | 168 cells 固定 | UTC 固定 → 多くの環境でユーザー体感とズレる | 低 | **不採用** — TZ ズレで「自分の深夜」が見えない |
| (3) hour-bucketed UTC | 最大 4320 entries (180 日 × 24h) | 整数 hour offset TZ では完全 lossless / 半 hour offset (India / Newfoundland / Iran) では UTC hour bucket が local 2 hours に半分ずつ振り分けられる lossiness あり | 中 (server で hour bucket 化 + browser で `Date` 再変換) | **採用** |

**recommendation: (3) hour-bucketed UTC**

**condition for switching to (1)**: payload size が 180 日でも 4320 を大きく下回る
シナリオでは (1) のほうが lossless で簡潔だが、本プロジェクトの想定 (ヘビー Skill
ユーザー) では `skill_tool` の頻度が hour bucket 数を超える日が普通にある (= (3)
のほうが確実に小さくなる)。

**condition for switching to (2)**: 半 hour offset TZ ユーザーが実利用者に複数
おり、ユーザーから「local 14:30 の使用が 14:00 と 15:00 に半分ずつ表示されて
気持ち悪い」というフィードバックがあれば、server 側を browser TZ 知識ありで
集計する方向に倒す (例: server.py で TZ 設定を受け取って 7×24 pre-bin)。
それまでは (3) のままで足りる。

#### Schema 例

```json
{
  "hourly_heatmap": {
    "timezone": "UTC",
    "buckets": [
      {"hour_utc": "2026-04-28T10:00:00+00:00", "count": 12},
      {"hour_utc": "2026-04-28T11:00:00+00:00", "count": 3}
    ]
  }
}
```

- `timezone: "UTC"` は server 側で集計したことを明示する future-proofing field。
  将来 server 側で local TZ に切り替えるときに browser がこのキーで分岐できる。
- empty events なら `buckets: []`。
- `hour_utc` は ISO 8601 完全形 (`+00:00` 含む) でフォーマット。`fromisoformat` で
  ラウンドトリップ可能。

#### Schema migration path (将来 option (2) に倒すとき / Proposal 4)

半 hour offset TZ ユーザーから lossiness のフィードバックが出た時点で server 側
pre-bin に倒す経路を、本 plan 段階で雛形だけ pin しておく:

- 新 field `weekday_hour_matrix: number[7][24]` を additive で追加 (0=Mon..6=Sun /
  0..23 hour)。`timezone` を `"Asia/Tokyo"` 等に変更
- browser 側 `renderHourlyHeatmap` は `payload.weekday_hour_matrix` があればそれを
  優先し、無ければ既存の `buckets` を `Date` 経由で bin (= 後方互換)
- `timezone === "UTC"` でも `buckets` 経路を踏襲することで dispatch 不要 / rollback
  容易
- 新クライアントを既存 server (=`buckets` のみ返す) に当てても壊れない

### 集計関数 `aggregate_hourly_heatmap()` の signature と挙動

```python
def aggregate_hourly_heatmap(usage_events: list[dict]) -> dict:
    """usage 系 events を UTC hour bucket に集計する。

    入力:
      usage_events — `_filter_usage_events()` で usage 系のみ + subagent invocation
        dedup 済みの list (= aggregate_daily / aggregate_projects と同じ慣習)
    出力:
      {
        "timezone": "UTC",
        "buckets": [
          {"hour_utc": "<ISO 8601 with +00:00>", "count": <int>},
          ...
        ]
      }

    挙動:
      - 各 event の `timestamp` を `datetime.fromisoformat()` で parse
      - 非 UTC offset (例 `+09:00`) は `astimezone(timezone.utc)` で UTC に正規化
      - `replace(minute=0, second=0, microsecond=0)` で hour 単位に truncate
      - 同一 hour bucket は `Counter` で集計 → count++
      - parse 失敗 (空文字 / not parseable / `timestamp` キー不在) は **silent skip**
        (既存 `aggregate_daily` も同様の防御スタイル)
      - bucket は `hour_utc` 昇順で出力 (browser 側でソート不要)
    """
```

**配置場所**: `dashboard/server.py` の既存 aggregator 群 (`aggregate_skills`,
`aggregate_subagents`, `aggregate_daily`, ...) の直後。`build_dashboard_data` の
return dict に 1 行追加:

```python
"hourly_heatmap": aggregate_hourly_heatmap(usage_events),
```

`usage_events` (= 既存の `_filter_usage_events(events)` の戻り値) を渡す。
`build_dashboard_data` 内で既に 1 回 filter 済みの list が用意されているので、
それを再利用する (`aggregate_daily` / `aggregate_projects` と完全に同じ呼び出し
パターン)。**慣習統一**: 関数内で再 filter する設計 (= 二度回し) は不採用。
plan-reviewer Proposal 1 反映。

### usage filter 適用方針 (再利用)

既存 `_filter_usage_events()` (`dashboard/server.py:96`) を **そのまま** 呼び出す。
これにより:
- `skill_tool` / `user_slash_command` の event は素通し
- `subagent_start` (PostToolUse 由来) と `subagent_lifecycle_start` (lifecycle 由来)
  は `usage_invocation_events()` で invocation 単位に dedup された代表 1 件のみ採用
- `session_*` / `notification` / `instructions_loaded` / `compact_*` / `subagent_stop`
  は除外

**recommendation: `_filter_usage_events` 再利用**

**condition for changing**: heatmap 専用の filter ポリシーが必要になった場合
(例: `subagent_stop` の終了時刻も heatmap に含めたい) は別 helper を切る。本 issue の
acceptance criteria では「usage 系のみ」と明示されているのでそのケースは出ない。

### Mon-Sun 変換ロジック (browser 側)

JS `Date.getDay()` は `0=Sun..6=Sat`。Issue の acceptance criteria は **Mon-Sun**
並びを明示しているので:

```javascript
const weekdayIdx = (d.getDay() + 6) % 7;  // 0=Mon, 6=Sun
```

を使い、`matrix[weekdayIdx][hour]` で 7×24 を組み立てる。row label は
`['Mon','Tue','Wed','Thu','Fri','Sat','Sun']`。

### 色スケール戦略 (browser 側)

| 戦略 | 説明 | 採否 |
|---|---|---|
| (a) max 正規化 | `intensity = count / max(count) ` で 0..1 に正規化 → 単一 outlier に引っ張られる | **本 PR 採用** |
| (b) percentile (P95) 正規化 | `intensity = count / p95(count)` で clip → outlier に強い | 採用条件付き |
| (c) log scale | `intensity = log(count+1) / log(max+1)` | 不採用 |

**recommendation: (a) max 正規化** で本 PR は着地。色は CSS variable
`--mint` (Overview の主色) のアルファブレンド経路 (例: `rgba(120, 220, 180, intensity)`)
で 0 cell を完全透明、max を不透明に。**0 cell は CSS で薄い枠線のみ表示**
(acceptance criteria の「0 cell も表示」を担保)。

**condition for switching to (b)**: 自分のデータで実機確認時に「特定の 1 cell だけ
が真っ濃で他全部が真っ白」になる場合は P95 正規化に切り替える (renderer の
`max` 計算を `percentile(counts, 95)` に差し替える 1-3 行の局所変更)。

### legend (色強度 ↔ 件数)

heatmap 下に水平 legend を出す:
```
0  ▓▓▓▓▓▓▓▓▓▓  max
   (薄)        (濃)
```
左端ラベル `0`、右端ラベル `max` (実数値)、中央バーは 8〜10 セグメントの
グラデーション。CSS gradient or 8 個の `<span>` で組む。

## 🏛 DOM / CSS / JS 設計

### Patterns section の DOM 雛形

既存 placeholder を **置換** (Issue #59 の cross-tab は別 widget として後で section
末尾に追加されるので、heatmap は section 冒頭に置く):

```html
<section data-page="patterns" class="page" aria-labelledby="page-patterns-title" hidden>
  <header class="header">
    <h1 id="page-patterns-title">Patterns</h1>
    <p class="lede">利用パターンを可視化します。</p>
  </header>

  <div class="panel" id="patterns-heatmap-panel">
    <div class="panel-head c-peri">
      <div class="ttl-wrap">
        <span class="ttl"><span class="dot"></span>時間帯ヒートマップ</span>
        <span class="help-host">
          <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-heatmap" data-help-id="hp-heatmap">?</button>
          <span class="help-pop" id="hp-heatmap" role="tooltip" data-place="right">
            <span class="pop-ttl">時間帯ヒートマップ</span>
            <span class="pop-body">usage 系イベント (skill_tool / user_slash_command / subagent invocation) を曜日 × 時間帯のグリッドで集計。色が濃いほど件数が多い。タイムゾーンはブラウザ local。subagent は invocation 単位に dedup 済み。</span>
          </span>
        </span>
      </div>
      <span class="sub" id="patterns-heatmap-sub"></span>
    </div>
    <div class="panel-body">
      <div class="heatmap" id="patterns-heatmap" role="img" aria-label="時間帯ヒートマップ"></div>
      <div class="heatmap-legend" id="patterns-heatmap-legend"></div>
    </div>
  </div>

  <!-- (B1 / B2 cross-skill / project×skill は Issue #59 で本セクションに追加予定) -->
  <p class="placeholder-body" style="text-align:center; margin-top:24px; color:var(--ink-faint); font-size:11.5px;">
    cross-skill 共起 / project × skill (<code>#59</code>) は今後追加予定。
  </p>
</section>
```

placeholder 文言は「heatmap が主役 + #59 の言及だけ短く残す」スタイル。
`page-placeholder` クラスは外し、通常の `class="page"` に戻す (Issue #57 router の
hidden 切替は `class="page"` でも動く)。

### CSS class naming

Issue #57 で確立した `<page>-<widget>` 命名規約を踏襲:
- panel root id: `patterns-heatmap-panel`
- grid container id: `patterns-heatmap`
- legend container id: `patterns-heatmap-legend`
- sub label id: `patterns-heatmap-sub`

generic class:
- `.heatmap` — grid wrapper (CSS grid 7×25 = 1 列の row label + 24 hour cell)
- `.heatmap-row` — 1 weekday 行
- `.heatmap-row-label` — Mon..Sun ラベル
- `.heatmap-cell` — hour cell (count 0 含む)
- `.heatmap-col-axis` — top の hour ラベル軸 (00..23)
- `.heatmap-legend` — bottom の凡例
- `.heatmap-legend-bar` / `.heatmap-legend-label`

### CSS sketch

```css
.heatmap {
  display: grid;
  grid-template-columns: 32px repeat(24, 1fr);
  gap: 2px;
  font-size: 10.5px;
  margin-top: 4px;
}
.heatmap-col-axis {
  display: contents;
}
.heatmap-col-axis > span {
  color: var(--ink-faint);
  text-align: center;
  font-family: var(--ff-mono);
  padding-bottom: 4px;
}
.heatmap-row-label {
  color: var(--ink-soft);
  text-align: right;
  padding-right: 6px;
  font-family: var(--ff-mono);
  align-self: center;
}
.heatmap-cell {
  aspect-ratio: 1;
  min-height: 18px;
  border-radius: 2px;
  border: 1px solid var(--line);
  background: transparent;  /* count=0 cell */
  cursor: default;
}
.heatmap-cell[data-c="0"] { background: transparent; }
/* count > 0 は inline style="background: rgba(...)" で個別塗り (intensity に比例) */

.heatmap-legend {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  font-size: 10.5px;
  color: var(--ink-faint);
  font-family: var(--ff-mono);
}
.heatmap-legend-bar {
  flex: 0 0 200px;
  height: 8px;
  border-radius: var(--r-sm);
  background: linear-gradient(to right, rgba(120,220,180,0.05), rgba(120,220,180,1));
  border: 1px solid var(--line);
}

/* 色 stripe: heatmap は peri 系色 (Issue #57 の daily_trend と被らないよう mint 系を採用) */
.data-tip[data-kind="heatmap"] { border-left-color: var(--mint); }
```

### JS renderer (template.html `<script>` 内)

`loadAndRender()` の最後に heatmap renderer を挿入。**page-scoped early-out は
原則どおり `body[data-active-page]` で判定するが、本 widget は描画コストが軽量
(168 cells) なので Patterns ページが非表示でも常時描画する** という選択肢も
合理的。本 PR では「常時描画」で着地し、後続の plan で必要なら early-out 化。

```javascript
function renderHourlyHeatmap(payload) {
  // page-scoped early-out (Q2): 後続 Issue #59〜#62 の widget が同じ規範に乗れるよう
  // 本 PR でも 1 行入れておく。Patterns 表示中以外は skip。
  if (document.body.dataset.activePage !== 'patterns') return;
  const root = document.getElementById('patterns-heatmap');
  if (!root) return;  // shell が存在しない環境向け defensive
  const buckets = (payload && payload.buckets) || [];
  const matrix = Array.from({length: 7}, () => Array(24).fill(0));
  let max = 0;
  for (const b of buckets) {
    const d = new Date(b.hour_utc);
    if (isNaN(d.getTime())) continue;  // 不正 timestamp は silent skip
    const wd = (d.getDay() + 6) % 7;  // Mon=0..Sun=6
    const h = d.getHours();
    matrix[wd][h] += b.count;
    if (matrix[wd][h] > max) max = matrix[wd][h];
  }
  const labels = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  let html = '<div class="heatmap-col-axis"><span></span>';
  for (let h = 0; h < 24; h++) html += '<span>' + pad(h,2) + '</span>';
  html += '</div>';
  for (let wd = 0; wd < 7; wd++) {
    html += '<div class="heatmap-row-label">' + labels[wd] + '</div>';
    for (let h = 0; h < 24; h++) {
      const c = matrix[wd][h];
      const intensity = max ? c / max : 0;
      const bg = c ? 'background: rgba(120,220,180,' + (0.05 + intensity * 0.95).toFixed(3) + ')' : '';
      html += '<div class="heatmap-cell" style="' + bg + '"' +
        ' data-tip="heatmap" data-wd="' + labels[wd] + '" data-h="' + pad(h,2) +
        '" data-c="' + c + '" tabindex="0" role="img"' +
        ' aria-label="' + labels[wd] + ' ' + pad(h,2) + ':00 ' + c + ' events"></div>';
    }
  }
  root.innerHTML = html;
  // legend
  const legend = document.getElementById('patterns-heatmap-legend');
  if (legend) {
    legend.innerHTML = '<span>0</span><span class="heatmap-legend-bar"></span><span>' + fmtN(max) + '</span>';
  }
  const sub = document.getElementById('patterns-heatmap-sub');
  if (sub) {
    const total = buckets.reduce((s,b) => s + b.count, 0);
    sub.textContent = fmtN(total) + ' events / ' + fmtN(buckets.length) + ' hour buckets';
  }
}
```

`loadAndRender()` の末尾で:
```javascript
renderHourlyHeatmap(data.hourly_heatmap);
```

#### page-scoped early-out と hashchange 連携 (Q2 実装の前提)

early-out (`activePage !== 'patterns'` で return) を入れると、**初期表示が Overview の
状態でユーザーが `#/patterns` に navigate した瞬間に heatmap が空のまま**になる。
これを防ぐため、main IIFE 側で hashchange listener を追加して `loadAndRender()` を
再呼び出しする (router IIFE とは独立したリスナー):

```javascript
// main IIFE 内 (loadAndRender 定義後)
window.addEventListener('hashchange', () => {
  loadAndRender().catch(err => console.error('route change render 失敗', err));
});
```

**順序の保証**: 既存 router IIFE の `<script>` ブロックは main IIFE より **前**
に定義されているため、ブラウザは router IIFE の hashchange listener を先に登録
する。同一 target の listener は登録順に発火 → router が `body.dataset.activePage`
を更新してから main IIFE が `loadAndRender()` を呼ぶので、early-out 判定は新 page
で正しく動く。

**rule of consistency for #59〜#62**:
> page-scoped early-out (`if (activePage !== '<page>') return;`) は **render コスト
> > 5ms** な widget で採用する。本 #58 heatmap (168 cells, ~1ms) は厳密には
> 不要だが、後続 widget が同じ pattern に乗れるよう **本 PR で先に入れて pin**
> しておく。hashchange 連携 (上記 listener) は本 PR で 1 度入れれば全 page widget
> が共有する基盤になる。

### tooltip 拡張 (`dtipBuild()` 分岐追加)

template.html:1229 の `dtipBuild(el)` に以下の分岐を追加:

```javascript
if (kind === 'heatmap') {
  const wd = el.getAttribute('data-wd');
  const h = el.getAttribute('data-h');
  const c = el.getAttribute('data-c');
  return {
    kind: 'heatmap',
    html: '<span class="ttl">' + esc(wd) + ' ' + esc(h) + ':00</span>' +
          '<span class="lbl">events</span><span class="val">' + fmtN(c) + '</span>'
  };
}
```

`.data-tip[data-kind="heatmap"]` の border-left は `--mint` で stripe (上記 CSS 参照)。

### placeholder 文言の扱い

- 旧 placeholder の `<h2>Patterns — Coming soon</h2>` は **削除** し、heatmap panel
  が主役。
- Issue #59 への言及は section 末尾に薄く `<p class="placeholder-body">...</p>` で
  残す (heatmap が #58、cross-tab が #59 という親子関係を明示)。
- `class="page-placeholder"` は section から外す (heatmap が描画される実コンテンツに
  なるため)。Issue #57 の test_dashboard_router.py で `assert 'page-placeholder'
  in section` を確認しているテストは Patterns について **失敗するようになる** ので、
  そのテストを更新する必要がある (後述「既存テストへの影響」)。

## 🧪 TDD テスト計画

### 新規 server 側 unit tests (`tests/test_dashboard_heatmap.py`)

既存 `tests/test_dashboard.py:18 load_dashboard_module()` と同じパターンで
`USAGE_JSONL` env をパッチして dashboard モジュールを読み込み、
`build_dashboard_data` および `aggregate_hourly_heatmap` を直接呼ぶ。

```python
class TestAggregateHourlyHeatmap:
    def test_empty_events_returns_empty_buckets(self, tmp_path):
        # → {"timezone": "UTC", "buckets": []}

    def test_single_event_creates_one_bucket(self, tmp_path):
        # skill_tool 1 件 → buckets=[{"hour_utc":"...10:00:00+00:00","count":1}]

    def test_same_hour_multiple_events_increment_count(self, tmp_path):
        # 同一 hour に skill_tool 3 件 → 1 bucket / count=3

    def test_different_hours_create_separate_buckets(self, tmp_path):
        # 10:00 / 11:00 各 1 件 → 2 buckets

    def test_non_utc_timestamp_normalizes_to_utc(self, tmp_path):
        # +09:00 の 19:00 → UTC 10:00 bucket に入る

    def test_microsecond_timestamp_parses(self, tmp_path):
        # "2026-04-28T10:30:45.123456+00:00" → "2026-04-28T10:00:00+00:00" bucket

    def test_minute_truncation_to_hour(self, tmp_path):
        # 10:00 / 10:30 / 10:59 の 3 件 → 1 bucket / count=3

    def test_filters_session_start_and_notification(self, tmp_path):
        # session_start / notification は heatmap に含まれない (usage filter)

    def test_subagent_invocation_dedup(self, tmp_path):
        # subagent_start + subagent_lifecycle_start (1 sec 以内 / 同 session+type)
        # → 1 invocation = 1 count

    def test_subagent_lifecycle_only_invocation_counted(self, tmp_path):
        # PostToolUse 不在 lifecycle のみのケースも 1 count

    def test_malformed_timestamp_silently_skipped(self, tmp_path):
        # "" / "not-a-date" / 欠損キー → 該当 event は skip、他 event は集計に残る

    def test_week_boundary_separate_buckets(self, tmp_path):
        # 日曜 23:00 と月曜 00:00 が別 bucket (時刻 truncate 確認)

    def test_buckets_sorted_ascending(self, tmp_path):
        # 入力が時刻順でなくても buckets は hour_utc 昇順

    def test_timezone_field_is_utc(self, tmp_path):
        # 任意の入力で payload.timezone == "UTC"

    # ---- Proposal 2: 境界・カバレッジ強化 ----
    def test_dst_spring_forward_no_bucket_skipped(self, tmp_path):
        # 2026-03-08 (US DST 切替日) の UTC 境界 hour に events 並べ
        # → server は UTC bucket なので連続 hour_utc に skip なくバケットされる
        # (browser 側 DST 影響と切り分け確認)

    def test_full_168_coverage_synthetic(self, tmp_path):
        # 7×24=168 の hour すべてに usage event を 1 件ずつ投入
        # (異なる UTC hour なので buckets は 168 件、各 count=1)
        # 重複なし / 取りこぼしなし / 168 ≤ 4320 (180 日 cap) を確認

    def test_hour_zero_and_twenty_three_truncate(self, tmp_path):
        # 00:00 / 00:59 / 23:00 / 23:59 の 4 件
        # → 2 buckets (00:00 count=2 / 23:00 count=2) で hour 端 truncate 動作 pin

class TestBuildDashboardDataWithHeatmap:
    def test_build_dashboard_data_includes_hourly_heatmap_key(self, tmp_path):
        data = mod.build_dashboard_data([])
        assert "hourly_heatmap" in data
        assert data["hourly_heatmap"] == {"timezone": "UTC", "buckets": []}

    def test_hourly_heatmap_count_matches_total_events_in_simple_case(self, tmp_path):
        # 同一 hour に usage 3 件 → total_events == sum(b.count for b in buckets) == 3
        # (regression guard for filter consistency)
```

### template.html 構造テスト (新規 `tests/test_dashboard_heatmap_template.py`)

既存 `test_dashboard_router.py` のパターンを踏襲:

```python
class TestPatternsHeatmapDOM:
    def test_patterns_section_has_heatmap_panel(self):
        section = _extract_section(template, 'patterns')
        assert 'id="patterns-heatmap"' in section
        assert 'id="patterns-heatmap-panel"' in section
        assert 'id="patterns-heatmap-legend"' in section

    def test_patterns_section_no_longer_pure_placeholder(self):
        # heatmap が描画されるので page-placeholder クラスは外れる
        section = _extract_section(template, 'patterns')
        # section 開始タグに page-placeholder が無いこと (内部の <p> 文言は許容)
        opening = section.split('>', 1)[0]
        assert 'page-placeholder' not in opening

    def test_template_has_heatmap_renderer_function(self):
        assert 'function renderHourlyHeatmap' in template

    def test_template_has_heatmap_data_tip_kind(self):
        assert 'data-tip="heatmap"' in template
        # dtipBuild にも heatmap kind 分岐がある
        assert "kind === 'heatmap'" in template

    def test_template_mon_sun_weekday_conversion_present(self):
        # (d.getDay() + 6) % 7 で Mon=0..Sun=6 変換
        assert "(d.getDay() + 6) % 7" in template

    def test_loadAndRender_invokes_heatmap_renderer(self):
        assert 'renderHourlyHeatmap(data.hourly_heatmap)' in template

    def test_patterns_section_keeps_issue_59_reference(self):
        section = _extract_section(template, 'patterns')
        assert '#59' in section
```

### 既存テストへの影響 (regression)

- `tests/test_dashboard_router.py:test_non_overview_pages_are_placeholders` は
  Patterns section について `'page-placeholder' in section` を assert しているため
  本 PR で更新が必要。**Patterns は実 widget が入った状態に変わったので**、
  Quality / Surface のみを placeholder として check する形に loop の対象を絞る:
  ```python
  for page in ['quality', 'surface']:  # patterns は #58 で実 widget 化
  ```
  この変更は `test_dashboard_router.py` の同テストで 1 行修正。

- `tests/test_dashboard.py:TestBuildDashboardData` の各既存テスト
  (`test_total_events_excludes_*` / `test_total_events_includes_lifecycle_only_invocations`
  ほか) は `total_events` / `daily_trend` / `project_breakdown` のみを assert
  しているため、`hourly_heatmap` キー追加で破壊されない。`test_empty_events_returns_valid_structure` は
  存在キーを限定列挙していないので問題なし。

- `tests/test_export_html.py` (static export) は `window.__DATA__` 注入経路を
  チェックしているのみで追加 field の存在は assert しない → 影響なし。

- 全体 565 tests の現状から、本 PR で **追加 ~17 (server 14 + Proposal 2 の 3) +
  template ~7 + 修正 1 = ~590 tests / 全 pass** を目標に。

## 📦 実装ステップ (TDD red→green→refactor)

各 phase 完了時にローカルで `pytest -q tests/` を回し、grep で構造的検証を確認。

### Phase 1: server-side aggregation (RED → GREEN)

1. **RED**: `tests/test_dashboard_heatmap.py` を新規作成し
   `TestAggregateHourlyHeatmap` (~14 テスト) を書く。`aggregate_hourly_heatmap` は
   未実装なので `AttributeError`。
2. **GREEN**: `dashboard/server.py` に `aggregate_hourly_heatmap()` を実装。
   `_filter_usage_events` を呼び、`datetime.fromisoformat` + `astimezone(timezone.utc)`
   + `replace(minute=0, second=0, microsecond=0)` + `Counter` で集計。

   **trade-off — timestamp parse 失敗の扱い**:
   - (a) silent skip (既存 `aggregate_daily` 流) — **採用**
   - (b) 例外で fail — 不採用 (1 件の壊れた event で dashboard 全体が落ちる)
   - **condition for switching to (b)**: 集計の正確性が法令・契約レベルで要求
     される場面なら fail-fast に倒す。本ツール (個人の usage 自己観察) では
     defensive で十分。

3. **REFACTOR**: `_parse_ts` を `subagent_metrics._parse_ts` から再利用するか別途
   切るか検討。**結論**: 既存 `subagent_metrics._parse_ts` は private 命名で
   shape も微妙に違う (None 返し)。`aggregate_hourly_heatmap` 内に inline で書く
   ほうが読みやすい。条件: 第三 aggregator が `fromisoformat` 防御的 parse を
   必要とする時点で共通 helper に昇格。

### Phase 2: build_dashboard_data 統合 (1 行追加)

1. **RED**: `TestBuildDashboardDataWithHeatmap` の 2 テストが fail。
2. **GREEN**: `build_dashboard_data` の return dict に
   `"hourly_heatmap": aggregate_hourly_heatmap(events),` を 1 行追加。

### Phase 3: template.html DOM (RED template test → GREEN)

1. **RED**: `tests/test_dashboard_heatmap_template.py` を新規作成。Patterns section
   構造テスト (~7 テスト) と既存 `test_non_overview_pages_are_placeholders` の
   修正で fail。
2. **GREEN**: `dashboard/template.html` の `<section data-page="patterns">` を
   実 widget DOM に置換。class="page-placeholder" を外す。`<header><h1>Patterns</h1></header>`
   と `<div class="panel" id="patterns-heatmap-panel">` を入れる。Issue #59 言及の
   `<p>` も末尾に残す。
3. 既存 `test_dashboard_router.py` の Patterns 用 assertion を更新。

### Phase 4: CSS / JS renderer (visual smoke test)

1. CSS 追加: `.heatmap` / `.heatmap-cell` / `.heatmap-row-label` / `.heatmap-col-axis`
   / `.heatmap-legend` を template.html `<style>` 内、`/* multipage shell (Issue #57) */`
   セクションの後ろに追加 (`/* hourly heatmap (Issue #58) */` ラベル)。
2. JS: `loadAndRender()` の末尾に `renderHourlyHeatmap(data.hourly_heatmap);` を呼ぶ
   call を追加。`renderHourlyHeatmap` 関数は loadAndRender の後 / dtipBuild の前
   に定義 (関数宣言の hoisting 上はどこでも OK だが、視認性のため近傍に置く)。
3. **実機 smoke**:
   - `python3 dashboard/server.py` 起動
   - 自分の usage.jsonl で `#/patterns` を開き heatmap が描画されること
   - 全 0 件のテストデータで `0 events / 0 hour buckets` のサブラベル + 全透明 cell
   - hover で tooltip (`Wed 14:00 — 12 events`) が出ること
   - keyboard tab で各 cell に focus が来て tooltip 出ること
   - SSE refresh (ファイル変更) で heatmap が再描画されること
   - `#/` 起動 → `#/patterns` に navigate して heatmap が即時描画されること
     (hashchange listener 連携の検証 / Q2 早出し pattern 動作確認)
   - `python3 reports/export_html.py --output /tmp/static.html` で static export
     にも heatmap が乗ること (static export では window.__DATA__ 経路 + 静的 HTML
     なので hashchange なし → 初期 hash に応じて表示される確認)
   - **多 TZ probe (Proposal 3 合格判定)**: 以下を **1 件 probe で pin**
     1. `TZ=Asia/Tokyo` (UTC+9): UTC 01:00 (= 火 10:00 JST) の event 1 件投入 →
        ブラウザで **(Tue, 10:00) cell に count=1** が立つこと
     2. `TZ=America/New_York` (UTC-5/-4 / DST 影響あり): UTC 13:00 の event 1 件 →
        ブラウザで **DST 期間は (X, 09:00) / 非 DST 期間は (X, 08:00) cell** に
        count=1 が立つこと (DST shift を browser 側で吸収する確認)
     3. `TZ=Asia/Kolkata` (UTC+5:30): server に local 14:30 (= UTC 09:00) の event
        1 件投入 → ブラウザで **(Tue, 14:00) cell に count=1** が立つ (15:00 cell
        には立たない)。「IST の 30 分後ろ寄せ表示は仕様」として許容判定 OK
     上記 3 probe いずれも `cell に count=1 が立つ位置` が pin できれば DoD 満たす。
     主観的な「眺めて妥当」ではなく、`document.querySelector('[data-wd=...][data-h=...]').dataset.c === '1'`
     を DevTools console で直接確認できる手順に統一。

### Phase 5: tooltip 拡張 (`dtipBuild()` 分岐追加)

1. template.html:1229 `dtipBuild(el)` 内に `if (kind === 'heatmap') { ... }` 分岐を追加
   (上記「tooltip 拡張」セクションの実装)。
2. `.data-tip[data-kind="heatmap"]` の `border-left-color` を `--mint` に。
3. テスト: `test_template_has_heatmap_data_tip_kind` で `kind === 'heatmap'` 文字列の
   存在を確認 (Phase 3 と並走)。

### Phase 6: docs (docs/spec/dashboard-api.md 新設 / CLAUDE.md は behavior convention のみ)

> **重要**: schema 詳細は CLAUDE.md ではなく **`docs/spec/dashboard-api.md`** に
> 切り出す (user 指示 / "仕様に関するものは CLAUDE.md じゃなくて docs に")。
> CLAUDE.md には page-scoped early-out + hashchange listener convention 段落と、
> 詳細への 1 行 pointer (`schema 詳細は docs/spec/dashboard-api.md を参照`) のみ残す。

2. `CLAUDE.md` の「Router の動作仕様」末尾に **page-scoped early-out + hashchange
   listener convention** の段落を追加 (これは spec ではなく behavior convention
   なので CLAUDE.md 適切)。schema 詳細は **書かず**、`docs/spec/dashboard-api.md`
   への 1 行 pointer のみ。

3. `~/.claude/projects/-Users-kkoichi-Developer-personal-claude-transcript-analyzer/memory/MEMORY.md`
   に Issue #58 セクション追加 (要点と pointer。詳細は `docs/spec/dashboard-api.md`)。

### Phase 7: PR

ブランチ名: `feature/58-hourly-heatmap` (Issue #57 命名規則踏襲)
PR タイトル候補: `feat(dashboard): hourly heatmap on Patterns page (#58)`

#### PR 粒度判断 (Proposal 5)

**recommendation: Phase 1〜7 一括 PR**

**condition for splitting**: Phase 1 (server-side aggregation) の review で 2 round
以上のシグニチャ・schema 名・filter 慣習に関する往復が発生したら、**Phase 3+
(UI / template.html) を別 PR `feature/58b-hourly-heatmap-ui`** に切る。判断は
Phase 1 RED+GREEN merge の review 完了時点で行う。

PR 本文:
- 親 issue #48 / 当該 issue #58 / shell PR #57 を参照
- schema 例 (option 3 採用 + 半 hour TZ lossiness 注記 + migration path)
- 実機スクショ: 自分のデータの heatmap / tooltip / legend
- multi-TZ probe 結果 3 件 (Tokyo / NY / Kolkata, 各 cell 位置 pin 済み)

## 🚫 Out of Scope

issue 本文記載に加え、以下も本 PR では扱わない:

- **期間絞り込み UI**: hot tier 全期間 (180 日) で集計。範囲フィルタは将来 issue。
- **per-skill / per-project / per-subagent breakdown**: 全 event 合算のみ。breakdown は
  Issue #59 の cross-tab 系で扱う。
- **DST 補正の高度処理**: browser `Date` constructor の local rule 任せ。半 hour
  offset TZ の lossiness は schema 注記で許容を明示する以上のことはしない。
- **archive 込みの集計**: dashboard は仕様で hot tier のみ (CLAUDE.md と整合)。
- **page-scoped early-out 化**: heatmap renderer は 168 cells と軽量なので Patterns
  非表示中も描画 (常時 update)。後続で他 page の widget が増え renderer 総コストが
  問題化したら別 issue で page-scoped 化。
- **heatmap 上の onClick (該当時間帯の event 詳細ドリルダウン)**: tooltip 表示のみ。
- **時間帯と曜日の集計順序入れ替え (24 行 × 7 列)**: 視覚的に Mon-Sun 7 行が
  読みやすいので固定。
- **percentile 正規化への切り替え**: max 正規化で着地。実機で outlier 問題が出れば
  別 issue で対応。

## 🧷 リスクと不確実性

| リスク | 影響 | 対策 |
|---|---|---|
| 半 hour offset TZ (India / Newfoundland / Iran) で local 時刻 X:30 の使用が UTC bucket 経由で X:00 と (X+1):00 に半分ずつ振り分けられる | 当該 TZ ユーザーで heatmap が体感とズレる | schema 注記で lossiness を明記。実機検証で問題が顕在化したら server 側で TZ 受け取り → pre-bin に変更 (option 2 への移行 plan を残す) |
| max 正規化が 1 個の outlier に支配される | 大半 cell が透明、1 cell だけ濃い色になり情報量が下がる | 実機確認 (Phase 4) で観測したら P95 正規化に変更 (renderer の `max` 計算 1 行差し替え) |
| SSE refresh で 7×24 全描画コストが許容範囲か | 168 DOM nodes × 数十回/min の再描画で UI もたつき | 実測: 168 cells × innerHTML 一発書き換えは < 1ms 想定 (既存 spark sparkline と同等)。実機 Phase 4 で stutter なしを確認 |
| `Date` constructor の ISO 8601 with `+00:00` 解釈ブラウザ差 | Safari の旧版で parse 失敗 | `isNaN(d.getTime())` チェックで silent skip。最新 Safari/Chrome/Firefox は ISO 8601 完全形を仕様通り扱う |
| Issue #57 の `test_non_overview_pages_are_placeholders` が壊れる | CI fail | Phase 3 で同テストを更新 (loop 対象から `patterns` を外す) |
| `aggregate_hourly_heatmap` の bucket 数が 4320 cap を超える状況 | 今後 hot tier の 180 日制約が緩和されたとき | `_DEFAULT_PATH` の hot tier 仕様 (180 日固定) を CLAUDE.md で再確認。緩和時は再 plan |
| page-scoped early-out 採用 + `#/` で起動 → `#/patterns` navigate 時に heatmap が空 | navigate 直後の表示が壊れる | main IIFE で hashchange listener を追加して `loadAndRender()` 再実行。順序は router IIFE が先に登録されるため `body.dataset.activePage` 更新後に loadAndRender が走り正しく描画される (詳細は「page-scoped early-out と hashchange 連携」セクション参照) |

## ✔️ Definition of Done

- [ ] `tests/test_dashboard_heatmap.py` の新規 ~17 unit tests 全 pass (基本 14 +
  Proposal 2 の 3 件 = DST 境界 / 168 cell 全埋まり / hour 0/23 端)
- [ ] `tests/test_dashboard_heatmap_template.py` の新規 ~7 構造テスト全 pass
- [ ] `tests/test_dashboard_router.py` の Patterns 関連 1 テスト修正、全 16 tests pass
- [ ] `tests/test_dashboard.py` の既存 `test_total_events_excludes_*` /
  `test_total_events_includes_lifecycle_only_invocations` 全 pass (regression)
- [ ] `tests/test_export_html.py` 全 pass (window.__DATA__ 注入経路 regression)
- [ ] **全 ~590 tests pass** (565 ベースから + 約 25、- 0 / + 1 修正)
- [ ] 実機: 自分の usage.jsonl で heatmap が描画され、hover tooltip が出る、
  legend が表示される、0 cell も border のみで存在が見える
- [ ] keyboard tab で各 cell に focus し tooltip が出る
- [ ] SSE refresh (`echo '...' >> usage.jsonl`) で heatmap が再描画される
- [ ] **`#/` 起動 → `#/patterns` navigate で heatmap が即時描画される** (Q2
  hashchange 連携の動作確認)
- [ ] `python3 reports/export_html.py --output /tmp/static.html` の static export
  でも heatmap が描画される
- [ ] **multi-TZ probe pin (Proposal 3)**:
  - Tokyo: UTC 01:00 event → ブラウザで `[data-wd="Tue"][data-h="10"]` cell の
    `data-c="1"` を DevTools 確認
  - NY: UTC 13:00 event → DST 期間は `[data-h="09"]` / 非 DST 期間は
    `[data-h="08"]` cell に count=1 が立つ
  - Kolkata: UTC 09:00 event → `[data-wd="Tue"][data-h="14"]` cell に count=1
    (15:00 cell には立たないことも確認 = 30 分後ろ寄せ仕様 OK)
- [ ] CLAUDE.md の「ダッシュボード複数ページ構成」直後に schema subsection 追記
  (Q3 = (a) 採用)
- [ ] MEMORY.md に 1 行 index 追加
- [ ] PR `feature/58-hourly-heatmap` を `v0.7.0` ブランチ向けに作成 / レビュー承認

## 📦 変更ファイル一覧 (見込み)

- `dashboard/server.py` — `aggregate_hourly_heatmap()` 追加 + `build_dashboard_data` に
  1 行統合 (~30 行追加)
- `dashboard/template.html` — Patterns section の DOM 置換 / CSS 追加 / JS renderer +
  dtipBuild 分岐追加 (~80 行追加)
- `tests/test_dashboard_heatmap.py` (新規) — server 側 unit tests
- `tests/test_dashboard_heatmap_template.py` (新規) — template 構造テスト
- `tests/test_dashboard_router.py` — Patterns placeholder assertion を 1 行修正
- `CLAUDE.md` — schema 追記
- `~/.claude/projects/-Users-kkoichi-Developer-personal-claude-transcript-analyzer/memory/MEMORY.md` — 1 行 index

`subagent_metrics.py` は触らない (`_filter_usage_events` 経由で
`usage_invocation_events` を再利用するのみ)。

## 📨 後続 PR への申し送り (#59)

本 widget は `<section data-page="patterns">` の **冒頭** に panel を置き、末尾に
Issue #59 言及の `<p class="placeholder-body">` を残す。Issue #59 (cross-skill 共起 /
project × skill) は:
- 本 widget の panel の **後ろ** に新しい panel を 1〜2 個追加
- placeholder-body の `<p>` を削除
- DOM ID は `<page>-<widget>` 規約 (例: `patterns-cross-skill`, `patterns-project-skill`)

heatmap の DOM / CSS / JS とは独立して動かせる (id 衝突なし)。
