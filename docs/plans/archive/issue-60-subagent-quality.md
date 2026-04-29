# Issue #60 Plan — Subagent quality 強化 (A5: duration percentile + B3: failure weekly trend)

## 📋 plan-reviewer 反映ログ

| Proposal | 内容 | 反映箇所 |
|---|---|---|
| P1 | percentile method を「numpy default 等価」と誤記していた点を訂正。`statistics.quantiles(..., method="inclusive")` は **Excel `PERCENTILE.INC` 等価** であり、numpy のデフォルト (`method="linear"` exclusive) とは別物。help-pop / rationale / risk 表を統一 | A5 集計仕様 / help-pop / Risk 表 |
| P2 | `subagent_failure_trend` は server で top-N に切らず **全 (week, type) を返す** schema を明記。client 側 top-5 はあくまで UI affordance であることを docstring と spec doc に書く | B3 集計仕様 / docstring / Phase 8 (spec doc) |
| P3 | `datetime.fromisoformat()` 結果が naive のときは UTC として扱う safety belt (`if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)`)。test 1 件追加 | B3 集計仕様 / 新規 test `test_naive_timestamp_treated_as_utc` |
| P4 | `weeks.length === 1` の degenerate path で line が描けない件をサブラベルに「1 week only」を出して明示。template test 1 件追加 (polyline=0 / circle=top.length) | B3 renderer / 新規 test `test_trend_single_week_renders_circles_no_polyline` |
| P5 | percentile table の列順を `Subagent / Count / Samples / avg / p50 / p90 / p99` に変更 (`Samples` を `Count` 直後に置いて信頼度評価を最初に整理) | DOM 雛形 / renderer / `test_percentile_table_has_thead_columns` |
| P6 | invariant test 2 件追加: (a) `p50 <= p90 <= p99` (cut index 49/89/98 の off-by-one 検出)、(b) `sample_count <= count` (durations が None の invocation で count > sample_count になることを許容しつつ上界を pin) | 新規 test 2 件 / 想定 test 数を ~53 に更新 |
| P7 | schema 命名規約「`subagent_<metric>_trend` for weekly time-series」を `docs/spec/dashboard-api.md` に明文化。本 PR は `subagent_failure_trend` のみだが、後続で `subagent_duration_trend` 等を additive で増やせる足場を作る | Phase 8 (spec doc) / 申し送り |
| Q1 | `_process_bucket` ↔ `_bucket_invocation_records` drift を二重ガードする `test_failure_count_matches_metrics_failure_count` を追加 (count だけでなく failure_count でも一致を pin) | 新規 test 1 件 |
| Q2 | MEMORY.md は現状 260 行で 200 行 limit 超過。dense な percentile method / 週境界 / `_process_bucket` 関係は `memory/subagent_quality.md` topic file に分離。MEMORY.md には 1 行 pointer のみ | Phase 8 (memory) / 変更ファイル一覧 |
| Q3 | `subagent_ranking` の percentile キーが Overview 含む全 consumer に流れる事実を `docs/spec/dashboard-api.md` に明記 (Quality 専用 field と誤読されないようにする) | Phase 8 (spec doc) |

### 二次レビュー反映 (2nd round)

| 二次 Proposal | 内容 | 反映箇所 |
|---|---|---|
| 2-P1 | `_bucket_invocation_records` 擬似コード直後に「余った `stops_sorted[len(invocations):]` は record 化しない (= invocation 単位の集計なので stop 単独は trend に寄与しない)。`_process_bucket` も余り stop の failure はカウントしないため両者の failure_count は一致する」を 2 行明記。Q1 の `test_failure_count_matches_metrics_failure_count` の合理性を読む人に保証 | B3 集計仕様 (`_bucket_invocation_records` 擬似コード末尾) |
| 2-P2 / 2-Q1 | `aggregate_subagent_failure_trend` を **`subagent_metrics.py`** 側に置く (responsibility purity 採用)。Issue #59 慣習 (`dashboard/server.py` 集約) より、後続 `reports/summary.py` への trend/percentile 反映 issue が現実に来る想定で **依存方向の綺麗さ** を優先。`dashboard/server.py` は import して `build_dashboard_data` で呼ぶだけ | B3 関数 signature コメント / Phase 3 GREEN 配置先 / 変更ファイル一覧 |
| 2-P3 / 2-Q2 | SVG accessibility 多重化を解消: (a) `<div class="trend-chart">` の `role="img" aria-label` を削除、(b) `<svg>` 側のみ `role="img" aria-label="..."` を残す、(c) 各 `<circle>` の `aria-label` は keyboard focus 用に維持。実機 smoke に **Safari での `<circle tabindex="0">` focus** 確認項目追加 | DOM 雛形 / B3 renderer SVG 組立 / Phase 6 実機 smoke |
| 2-P4 | 本文中 inline `(P1 反映)` 等のマーカーは **コードコメント / docstring に転記** する旨を Phase 8 に明記。`_percentiles` / `aggregate_subagent_failure_trend` の docstring 内に `# Excel PERCENTILE.INC 等価 (Issue #60 / P1)` のような形で意図を残し、PR 後に意図が薄れない | Phase 8.4 (`subagent_metrics.py` docstring 更新) |
| 2-Q3 | `memory/subagent_quality.md` の作成は Phase 8 を待たず **Phase 1 RED と並行** で開始 (dogfooding "Dogfood workflow doc changes" 観点で新規 topic file を本 PR 完結に揃える) | 実装ステップ整理 (Phase 1 と Phase 8.3 が並行可と明記) |

## 🎯 Goal

`subagent_ranking` の現状 (count / failure_rate 累計 / avg_duration_ms) では見えない
**長尾分布** と **時系列 trend** を Quality ページに乗せる。

- **A5. Subagent duration percentile**: subagent_type ごとに p50 / p90 / p99 +
  sample count を可視化 → 「Explore は avg 速いが p99 で 5 分超」のような skewness を
  発見できる
- **B3. Subagent failure rate weekly trend**: monday-start UTC で週次 failure_rate を
  折れ線描画 → 「最近劣化したのか / ずっと不安定か」を切り分け

両 viz は **Quality ページ** (`<section data-page="quality">`) に配置する。Issue #57
shell で placeholder のままだったので、本 PR で初の本 widget 化となる。

## 📐 機能要件 / 構造設計

### A5. Percentile

#### 集計仕様

- **データ源**: 既存 invocation 単位 `duration_ms`。
  `subagent_metrics.aggregate_subagent_metrics()` が内部で持っている
  `invocation_durations[name]: list[float]` (= invocation 1 件 1 値) をそのまま percentile
  入力にする
- **計算**: stdlib `statistics.quantiles(data, n=100, method="inclusive")` で 99 個の
  cut を得て、index 49/89/98 を p50/p90/p99 として採る。`numpy` 不使用方針。
  - `method="inclusive"` は **Excel `PERCENTILE.INC` 等価** (端点を含めた線形補間)。
    numpy のデフォルト `method="linear"` (exclusive endpoints) とは **別物** なので
    「numpy default 等価」という雑な言い方はしない。本 PR の test では `[1,2,3,4]`
    のような既知サンプルに対する固定値 (`p50=2.5 / p90=3.7 / p99=3.97`) を pin して、
    method 切替えによる回帰を検出できるようにする (P1 反映)
  - **edge case**: `len(data) < 2` のとき `statistics.quantiles` は
    `StatisticsError` を投げる → 自前で扱う
    - `len(data) == 0` → p50/p90/p99 すべて `None`
    - `len(data) == 1` → p50/p90/p99 すべて `data[0]` (退化扱い)
    - `len(data) >= 2` → `statistics.quantiles(...)` を呼ぶ
- **sample_count**: `len(durations)` (= duration が観測できた invocation 数)。
  count (= invocation 数) と一致しない場合がある (start.duration_ms も stop.duration_ms
  も無い invocation は `_invocation_duration` が None を返し durations に積まれない)。
  UI 側で「percentile は N サンプルから」と explicit にできるよう別フィールドとして
  出す

#### 関数 signature 拡張

`subagent_metrics._build_metrics()` の中で metrics dict に 4 フィールド追加する。
**aggregator API は破壊しない・additive のみ**。

```python
def _build_metrics(...) -> dict[str, dict]:
    metrics: dict[str, dict] = {}
    for name, count in type_count.items():
        durations = invocation_durations.get(name, [])
        p50, p90, p99 = _percentiles(durations)
        metrics[name] = {
            "count": count,
            "failure_count": failure,
            "failure_rate": (failure / count) if count else 0.0,
            "avg_duration_ms": (sum(durations) / len(durations)) if durations else None,
            # ── 新規 (Issue #60 / A5) ──
            "p50_duration_ms": p50,
            "p90_duration_ms": p90,
            "p99_duration_ms": p99,
            "sample_count": len(durations),
        }
    return metrics


def _percentiles(durations: list[float]) -> tuple[float | None, float | None, float | None]:
    """duration list から (p50, p90, p99) を返す。空 → (None, None, None)。
    1 件 → 全値同一。2 件以上 → statistics.quantiles(n=100, inclusive)。"""
```

`dashboard/server.py:aggregate_subagents()` は metrics dict を spread しているだけ
(`{"name": name, **m}`) なので、追加フィールドは自動的に `subagent_ranking` 配列の
各要素に流れる。**broker コードの変更不要**。

#### 計算量

- N invocation の percentile = sort `O(N log N)` + `quantiles` `O(N)`
- 180 日 hot tier で subagent invocation は数千オーダー = 完全に許容範囲
- type ごとに sort なので type 数 K で全体 `O(Σ N_k log N_k)` ≈ `O(N log(N/K))`

#### `avg_duration_ms` との重複論点

**recommendation: avg は維持して percentile を additive 追加**。avg は既存 UI
(Overview の subagent ranking meta `avg 1.2s`) で使われており、削除すると Overview に
regression が出る。schema は API 互換 + UI は **Quality ページの新パネルに p50/p90/p99
列だけ追加** で済む。

### B3. Weekly trend

#### 集計仕様

- **データ源**: invocation 単位の (timestamp, subagent_type, success)。
  `subagent_metrics` の内部状態は `name → metrics dict` までしか expose していないので、
  本 PR で **invocation list を export する API** を 1 個足す
  (`subagent_metrics.invocation_records()`)。 既存 aggregator と同じ
  `_bucket_events` + `_build_invocations` を使う薄い wrapper
- **week boundary**: `monday-start UTC`。
  - `dt = datetime.fromisoformat(timestamp)`
  - **naive safety belt** (P3 反映): `if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)`
    を **必ず** 挟む。usage.jsonl は通常 `+00:00` 付き ISO を書くが、Stop hook 経由
    `_merge_stop_hook_list` や `rescan_transcripts.py --append` 由来で naive ISO が
    紛れ込んだ場合に「local TZ として `astimezone(utc)` され週境界が ±数時間ずれる」
    silent corruption を構造的に塞ぐ。Python 3.10 では naive 値の `astimezone()` は
    `ValueError` だが 3.11+ では local TZ 解釈で silent shift する非対称があるため
    特に厳格に
  - `dt = dt.astimezone(timezone.utc)` (これで必ず UTC aware)
  - `week_start_date = (dt.date() - timedelta(days=dt.weekday()))` (`weekday()`:
    Mon=0..Sun=6)
  - 出力 schema 上は ISO date string (`"2026-04-21"`)
  - **境界 invariant**: 日曜 23:59 UTC と 月曜 00:00 UTC は別週に分かれる
- **failure 判定**: 各 invocation について `start.success=False OR stop.success=False`
  (= `_process_bucket` と同じロジックの結果を invocation 単位 list に展開)
- **集計単位**: `(week_start, subagent_type) → (count, failure_count)`
- **failure_rate**: `failure_count / count`。`count==0` ケースは構造的に発生しない
  (count=0 の bucket は出力しない)。安全網として「もし 0 ならば 0.0」を明示
- **空入力 / observed なし subagent**: 当該 subagent_type の trend には 1 件も
  含まれない (空の line を描かない)

#### Schema 例

```json
{
  "subagent_failure_trend": [
    {"week_start": "2026-04-21", "subagent_type": "Explore",        "count": 12, "failure_count": 2, "failure_rate": 0.166},
    {"week_start": "2026-04-21", "subagent_type": "general-purpose", "count":  8, "failure_count": 0, "failure_rate": 0.0},
    {"week_start": "2026-04-28", "subagent_type": "Explore",        "count": 15, "failure_count": 1, "failure_rate": 0.066}
  ]
}
```

- 配列要素は `(week_start, subagent_type)` の lexicographic 昇順で **明示 sort**
  (Counter.most_common の insertion order 依存を避ける慣習を踏襲。Issue #59 P3 と同型)
- `week_start` は ISO date `"YYYY-MM-DD"` (UTC monday)
- `failure_rate` は浮動小数 (Python `float`)。browser 側で `Math.round(x*100) + "%"`
- 空入力なら `[]`
- **server は top-N に切らず観測された全 `(week, subagent_type)` を返す** (P2 反映)。
  本 PR の chart は browser 側で count 上位 5 type に絞って描画するが、それは UI の
  affordance であり schema 仕様ではない。`/api/data` の programmatic な consumer は
  全 type の trend を受け取る前提で読む。client 側 top-5 と sync させたい consumer は
  `subagent_ranking` の `count` で再現可能 (= aggregate_subagents の sort key と同一)

#### 関数 signature

```python
# subagent_metrics.py (2-Q1 反映: dashboard/server.py から移動)
# 配置根拠: invocation_records と同じファイルに置くことで domain logic を
# 一箇所に集約。dashboard/server.py は build_dashboard_data から import + 呼出すだけ。
# 後続 reports/summary.py が将来 trend を出すときも同じ関数を再 import すれば済む。
def aggregate_subagent_failure_trend(events: list[dict]) -> list[dict]:
    """subagent invocation を (monday-UTC week, subagent_type) で bucket して trend を返す。

    監視しているのは end-to-end 成功 (start.success=False OR stop.success=False を 1 failure)。
    sort: (week_start, subagent_type) lexicographic 昇順。
    **top-N で切らない** (P2 反映): 観測された全 (week, subagent_type) を返す。
    UI 側の top-5 描画はあくまで affordance であり schema には現れない。
    naive datetime は UTC として扱う (P3 反映)。

    出力: list[{"week_start": "YYYY-MM-DD", "subagent_type": str,
               "count": int, "failure_count": int, "failure_rate": float}]
    """
```

実装は `subagent_metrics.invocation_records(events)` (新規 helper) を呼んで
invocation list を得て、week_start で bucket してカウントするだけ。

```python
# subagent_metrics.py に追加
def invocation_records(events: list[dict]) -> list[dict]:
    """各 invocation を `{"timestamp": str, "subagent_type": str, "failed": bool}` で返す。

    `aggregate_subagent_metrics` と同じ invocation 同定 (`_bucket_events` +
    `_build_invocations` + start↔stop pairing) を使い、各 invocation の
    `failed` flag (start.success=False OR stop.success=False) を計算する。
    timestamp は invocation の代表時刻 = `start.timestamp` 優先 / 無ければ `lifecycle.timestamp`。
    """
```

`_process_bucket` は今 `(failures, durations)` を返す内部関数。これを **拡張せずに**、
trend 用に invocation 単位の `(timestamp, failed)` を返す姉妹 helper を新設する方が
既存テストへの blast radius が小さい:

```python
# subagent_metrics.py に追加
def _bucket_invocation_records(invocations, stops_sorted, name):
    """1 バケット分の invocation 単位 [(timestamp, name, failed)] list を返す。"""
    paired_stops = len(invocations) == len(stops_sorted)
    stop_idx = 0
    records = []
    for inv in invocations:
        start = inv.get("start")
        lifecycle = inv.get("lifecycle")
        rep = start or lifecycle  # timestamp の代表
        ts = rep.get("timestamp", "") if rep else ""
        start_failed = bool(start) and start.get("success") is False
        if start_failed and not paired_stops:
            failed = True
        else:
            stop = stops_sorted[stop_idx] if stop_idx < len(stops_sorted) else None
            if stop is not None:
                stop_idx += 1
            stop_failed = bool(stop) and stop.get("success") is False
            failed = start_failed or stop_failed
        records.append({"timestamp": ts, "subagent_type": name, "failed": failed})
    return records
```

> **drift guard 注記 (2-P1 反映)**: ループ後に残る `stops_sorted[stop_idx:]`
> (= invocation 数より stops が多い余り) は **record 化しない**。invocation 単位
> 集計なので stop 単独イベントは trend に寄与しないのが正解。`_process_bucket`
> 側も L145-148 で余り stop の `duration_ms` を durations に積むだけで failure
> としてはカウントしないため、両者の failure_count は **構造的に一致** する。
> Q1 `test_failure_count_matches_metrics_failure_count` がそのまま通ることを
> この設計で保証する。

#### top-N 描画フィルタ (UI 側論点だが schema にも影響)

- B3 raw schema は **全 subagent_type の trend** を返す (top-N で切らない / server)
- browser 側で **count 上位 5 type** に絞って描画する (default top-5、固定)
- 「Top-N selector を出す」のは out of scope (本 PR は固定 top-5。issue 本文の
  "top N subagent_type を選んで描画 (画面が線で埋まらないように)" を、selector では
  なく "default top-5 で line を絞る" 解釈で着地)
- `top-N` selector は後続 issue 候補

**recommendation (top-5 fix)**: 折れ線が 5 本までなら palette / 凡例どちらも管理可能。
selector を入れるとイベントハンドラ + 状態管理 + URL persistence などコストが嵩む割に
本 PR の価値 (= percentile + trend を見える化) を超えない。

### Quality ページの DOM 全置換

`<section data-page="quality" class="page page-placeholder">` の中身を **全置換** する。
`page-placeholder` class は外す (`<section data-page="quality" class="page" ...>`)。

```html
<section data-page="quality" class="page" aria-labelledby="page-quality-title" hidden>
  <header class="header">
    <div>
      <h1 id="page-quality-title"><span class="accent">Quality</span></h1>
      <p class="lede">実行品質と摩擦シグナルを可視化します。</p>
    </div>
  </header>

  <!-- (1) A5: Subagent percentile table -->
  <div class="panel" id="quality-percentile-panel">
    <div class="panel-head c-coral">
      <div class="ttl-wrap">
        <span class="ttl"><span class="dot"></span>Subagent 所要時間 (percentile)</span>
        <span class="help-host">
          <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-percentile" data-help-id="hp-percentile">?</button>
          <span class="help-pop" id="hp-percentile" role="tooltip" data-place="right">
            <span class="pop-ttl">所要時間 (percentile)</span>
            <span class="pop-body">subagent_type ごとの invocation 所要時間を p50 / p90 / p99 で集計。<strong>p99 は最遅 1% の閾値</strong>。avg 平均値だけでは見えない長尾分布を確認できる。計算手法は <code>statistics.quantiles(method="inclusive")</code> = Excel <code>PERCENTILE.INC</code> 等価 (線形補間)。所要時間サンプル数が 1 のときは全 percentile が同値、0 のときは「-」。</span>
          </span>
        </span>
      </div>
      <span class="sub" id="quality-percentile-sub"></span>
    </div>
    <div class="panel-body">
      <table class="percentile-table" id="quality-percentile">
        <thead>
          <!-- 列順 (P5 反映): Subagent / Count / Samples / avg / p50 / p90 / p99
               Samples を Count 直後に置くことで、percentile の信頼度を最初に整理させる。
               sample_count <= count (durations 欠損 invocation で count > samples) の
               関係性が並びで読み取れる。 -->
          <tr>
            <th>Subagent</th>
            <th class="num">Count</th>
            <th class="num">Samples</th>
            <th class="num">avg</th>
            <th class="num">p50</th>
            <th class="num">p90</th>
            <th class="num">p99</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <!-- (2) B3: Subagent failure weekly trend (line chart) -->
  <div class="panel" id="quality-trend-panel">
    <div class="panel-head c-mint">
      <div class="ttl-wrap">
        <span class="ttl"><span class="dot"></span>Subagent 失敗率 (週次)</span>
        <span class="help-host">
          <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-trend" data-help-id="hp-trend">?</button>
          <span class="help-pop" id="hp-trend" role="tooltip" data-place="right">
            <span class="pop-ttl">失敗率 (週次)</span>
            <span class="pop-body">subagent invocation の <strong>週ごとの失敗率</strong> を折れ線で表示。週は <strong>月曜 00:00 UTC 起算</strong>。default で count 上位 5 type に絞って描画 (画面の混雑回避)。observed 0 の週はライン上に点が打たれない。</span>
          </span>
        </span>
      </div>
      <span class="sub" id="quality-trend-sub"></span>
    </div>
    <div class="panel-body">
      <!-- 2-Q2 反映: outer div の role="img" / aria-label を削除。
           内側の <svg> が role="img" + aria-label を持つので screen reader は
           svg 1 回読み上げる (重複読み上げ防止)。 -->
      <div class="trend-chart" id="quality-trend"></div>
      <div class="trend-legend" id="quality-trend-legend"></div>
    </div>
  </div>
</section>
```

### B3 描画方式 — inline SVG

外部 lib 不使用。`<svg>` を JS で組み立てる。

- viewBox: `0 0 600 220` (固定 px / responsive 親 div で max-width)
- 軸: y は 0..1 (失敗率)、x は週インデックス (0..N-1)
- gridline: y 軸 0% / 50% / 100% に horizontal line + label
- 線: `<polyline>` 1 本 / subagent_type
- 点: `<circle r=2.5>` を week データ点に打つ。マウスホバーで tooltip
  (data-tip="trend") 経由で「Explore week 2026-04-21: 16% (2/12)」を出す
- 色: 既存 palette (palette[i % palette.length]) を流用 (line 5 本以下なので衝突なし)
- 凡例: legend container に色マーカー + 名前

### CSS 設計 (template.html `<style>`)

`/* skill cooccurrence (Issue #59 / B1) */` セクション直後に追加。

```css
/* subagent percentile table (Issue #60 / A5) */
.percentile-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  font-family: var(--ff-mono);
}
.percentile-table th {
  text-align: left; color: var(--ink-faint); font-weight: 500;
  padding: 6px 8px; border-bottom: 1px solid var(--line);
}
.percentile-table th.num,
.percentile-table td.num {
  text-align: right; font-variant-numeric: tabular-nums;
}
.percentile-table tbody tr { border-bottom: 1px solid var(--line-faint, rgba(255,255,255,0.04)); }
.percentile-table tbody tr:hover { background: var(--bg-panel-2); }
.percentile-table td { padding: 5px 8px; color: var(--ink); }
.percentile-table td.name { color: var(--coral); }
.percentile-table td.dim { color: var(--ink-faint); }
.percentile-table .empty { text-align: center; color: var(--ink-faint); padding: 24px 0; }
.data-tip[data-kind="percentile"] { border-left-color: var(--coral); }

/* subagent failure weekly trend (Issue #60 / B3) */
.trend-chart {
  width: 100%; max-width: 600px; aspect-ratio: 600 / 220;
  margin-top: 4px;
}
.trend-chart svg { width: 100%; height: 100%; display: block; }
.trend-chart .grid { stroke: var(--line); stroke-dasharray: 2,3; }
.trend-chart .axis-label { fill: var(--ink-faint); font-size: 9px; font-family: var(--ff-mono); }
.trend-chart .line { fill: none; stroke-width: 1.5; }
.trend-chart .pt { stroke-width: 1; }
.trend-legend {
  display: flex; flex-wrap: wrap; gap: 12px;
  margin-top: 12px; font-size: 10.5px; color: var(--ink-faint);
  font-family: var(--ff-mono);
}
.trend-legend .marker {
  display: inline-block; width: 10px; height: 10px; border-radius: 2px;
  margin-right: 4px; vertical-align: middle;
}
.data-tip[data-kind="trend"] { border-left-color: var(--mint); }
```

**color rationale**:
- A5 percentile: subagent 系 = `c-coral` (既存 subagent_ranking と同 palette)
- B3 trend: 「品質 / 健康」を mint で表現 (hourly heatmap とは別 page なので衝突 OK)

### JS renderer

`renderProjectSkillMatrix` の **直後** に並べる。`loadAndRender()` 末尾に 2 行 call:

```javascript
// ---- subagent percentile table (Issue #60 / A5) ----
renderSubagentPercentile(data.subagent_ranking);
// ---- subagent failure weekly trend (Issue #60 / B3) ----
renderSubagentFailureTrend(data.subagent_failure_trend);
```

両 renderer は **page-scoped early-out** を持つ (`activePage !== 'quality'` ならスキップ)。

```javascript
function renderSubagentPercentile(items) {
  if (document.body.dataset.activePage !== 'quality') return;
  const tbody = document.querySelector('#quality-percentile tbody');
  if (!tbody) return;
  const list = Array.isArray(items) ? items : [];
  if (list.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">subagent データなし</td></tr>';
  } else {
    tbody.innerHTML = list.map((it) => {
      const c = it.count || 0;
      const samples = it.sample_count || 0;
      const avg = it.avg_duration_ms;
      const p50 = it.p50_duration_ms;
      const p90 = it.p90_duration_ms;
      const p99 = it.p99_duration_ms;
      const al = it.name + ': p50 ' + fmtDur(p50) + ' / p90 ' + fmtDur(p90) + ' / p99 ' + fmtDur(p99);
      // P5 反映: 列順は Subagent / Count / Samples / avg / p50 / p90 / p99
      return '<tr data-tip="percentile" data-name="' + esc(it.name) +
        '" data-c="' + c + '" data-p50="' + (p50 != null ? p50 : '') +
        '" data-p90="' + (p90 != null ? p90 : '') +
        '" data-p99="' + (p99 != null ? p99 : '') + '" tabindex="0" role="row" ' +
        'aria-label="' + esc(al) + '">' +
        '<td class="name">' + esc(it.name) + '</td>' +
        '<td class="num">' + fmtN(c) + '</td>' +
        '<td class="num dim">' + fmtN(samples) + '</td>' +
        '<td class="num dim">' + fmtDur(avg) + '</td>' +
        '<td class="num">' + fmtDur(p50) + '</td>' +
        '<td class="num">' + fmtDur(p90) + '</td>' +
        '<td class="num">' + fmtDur(p99) + '</td>' +
        '</tr>';
    }).join('');
  }
  const sub = document.getElementById('quality-percentile-sub');
  if (sub) sub.textContent = list.length + ' subagent types';
}

function renderSubagentFailureTrend(items) {
  if (document.body.dataset.activePage !== 'quality') return;
  const root = document.getElementById('quality-trend');
  const legend = document.getElementById('quality-trend-legend');
  const sub = document.getElementById('quality-trend-sub');
  if (!root) return;

  const list = Array.isArray(items) ? items : [];
  if (list.length === 0) {
    root.innerHTML = '<div class="empty" style="padding:24px;text-align:center;color:var(--ink-faint)">trend データなし</div>';
    if (legend) legend.innerHTML = '';
    if (sub) sub.textContent = '';
    return;
  }

  // (week_start ASC, type) → series 化 + 上位 5 type 抽出
  const byType = new Map();
  const weekSet = new Set();
  for (const r of list) {
    if (!byType.has(r.subagent_type)) byType.set(r.subagent_type, { total: 0, byWeek: new Map() });
    const e = byType.get(r.subagent_type);
    e.total += r.count;
    e.byWeek.set(r.week_start, r);
    weekSet.add(r.week_start);
  }
  const weeks = [...weekSet].sort();
  const top = [...byType.entries()].sort((a, b) => b[1].total - a[1].total).slice(0, 5);

  // SVG geometry
  const W = 600, H = 220, padL = 36, padR = 12, padT = 14, padB = 28;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const xOf = (i) => padL + (weeks.length === 1 ? innerW / 2 : (innerW * i / (weeks.length - 1)));
  const yOf = (rate) => padT + innerH - innerH * rate; // 0..1

  const palette = ['#FF6E70','#FFC97A','#6FE3C8','#9AB3FF','#D6A6FF'];
  // 2-Q2 反映: aria-label に top.length を埋め込んで「(top 5)」の subscope を SR に伝える
  let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg"' +
    ' role="img" aria-label="Subagent 失敗率 週次トレンド (top ' + top.length + ')">';
  // grid
  for (const [r, lbl] of [[0, '0%'], [0.5, '50%'], [1.0, '100%']]) {
    const y = yOf(r);
    svg += '<line class="grid" x1="' + padL + '" x2="' + (W - padR) + '" y1="' + y + '" y2="' + y + '"/>';
    svg += '<text class="axis-label" x="' + (padL - 6) + '" y="' + (y + 3) + '" text-anchor="end">' + lbl + '</text>';
  }
  // x-axis labels (first / mid / last)
  const tickIdx = weeks.length === 1 ? [0] : [0, Math.floor((weeks.length - 1) / 2), weeks.length - 1];
  for (const i of tickIdx) {
    const x = xOf(i);
    svg += '<text class="axis-label" x="' + x + '" y="' + (H - 10) + '" text-anchor="middle">' + esc(weeks[i]) + '</text>';
  }
  // lines
  top.forEach(([name, e], idx) => {
    const color = palette[idx % palette.length];
    const pts = [];
    weeks.forEach((w, i) => {
      const r = e.byWeek.get(w);
      if (r) pts.push({ i, r });
    });
    if (pts.length >= 2) {
      svg += '<polyline class="line" stroke="' + color + '" points="' +
        pts.map(p => xOf(p.i) + ',' + yOf(p.r.failure_rate)).join(' ') + '"/>';
    }
    pts.forEach(p => {
      const al = name + ' ' + p.r.week_start + ': ' + Math.round(p.r.failure_rate * 100) + '% (' + p.r.failure_count + '/' + p.r.count + ')';
      svg += '<circle class="pt" stroke="' + color + '" fill="' + color + '" cx="' + xOf(p.i) +
        '" cy="' + yOf(p.r.failure_rate) + '" r="2.5" data-tip="trend"' +
        ' data-name="' + esc(name) + '" data-w="' + esc(p.r.week_start) +
        '" data-rate="' + p.r.failure_rate + '" data-fc="' + p.r.failure_count +
        '" data-c="' + p.r.count + '" tabindex="0" role="img" aria-label="' + esc(al) + '"/>';
    });
  });
  svg += '</svg>';
  root.innerHTML = svg;

  if (legend) {
    legend.innerHTML = top.map(([name, e], i) =>
      '<span><span class="marker" style="background:' + palette[i % palette.length] + '"></span>' + esc(name) + '</span>'
    ).join('');
  }
  if (sub) {
    // P4 反映: weeks.length === 1 のときは line が描けないため明示注記
    const weekLabel = weeks.length === 1 ? '1 week only' : (weeks.length + ' weeks');
    sub.textContent = weekLabel + ' · top ' + top.length + ' / ' + byType.size + ' types';
  }
}

function fmtDur(ms) {
  if (ms == null) return '-';
  if (ms >= 1000) return (ms / 1000).toFixed(1) + 's';
  return Math.round(ms) + 'ms';
}
```

### tooltip 拡張 (`dtipBuild()` 分岐 2 件追加)

```javascript
if (kind === 'percentile') {
  const name = el.getAttribute('data-name') || '';
  const p50 = el.getAttribute('data-p50') || '';
  const p90 = el.getAttribute('data-p90') || '';
  const p99 = el.getAttribute('data-p99') || '';
  const fmt = (v) => v === '' ? '-' : (Number(v) >= 1000 ? (Number(v)/1000).toFixed(1) + 's' : Math.round(Number(v)) + 'ms');
  return {
    kind: 'percentile',
    html: '<span class="ttl">' + esc(name) + '</span>' +
          '<span class="lbl">p50</span><span class="val">' + fmt(p50) + '</span>' +
          '<span class="lbl">p90</span><span class="val">' + fmt(p90) + '</span>' +
          '<span class="lbl">p99</span><span class="val">' + fmt(p99) + '</span>'
  };
}
if (kind === 'trend') {
  const name = el.getAttribute('data-name') || '';
  const w = el.getAttribute('data-w') || '';
  const rate = parseFloat(el.getAttribute('data-rate') || '0');
  const fc = el.getAttribute('data-fc') || '0';
  const c = el.getAttribute('data-c') || '0';
  return {
    kind: 'trend',
    html: '<span class="ttl">' + esc(name) + '</span>' +
          '<span class="lbl">' + esc(w) + '</span>' +
          '<span class="val">' + Math.round(rate * 100) + '% (' + fc + '/' + c + ')</span>'
  };
}
```

### Page-scoped early-out + hashchange 連携

`#58/#59` で確立済みの `body[data-active-page]` 判定 + main IIFE の hashchange
listener が **そのまま機能する** ので、本 PR で追加実装不要。両 renderer に
`activePage !== 'quality'` の早期 return を入れるだけ。

## 🧪 TDD テスト計画

### 新規 server 側 unit tests (`tests/test_subagent_quality.py`)

```python
class TestPercentileEdgeCases:
    def test_empty_list_returns_none_triple(self): pass
    def test_single_value_repeated_for_all_percentiles(self): pass
    def test_two_values_inclusive_method(self): pass
    def test_odd_count_n5(self): pass
    def test_even_count_n10(self): pass
    def test_all_same_values(self): pass
    def test_known_sample_pin_excel_inclusive(self):
        # P1 反映: [1,2,3,4] に対し p50=2.5 / p90=3.7 / p99=3.97 を pin
        # method 切替え (inclusive→exclusive) による regression を検出
        pass
    def test_monotonic_p50_le_p90_le_p99(self):
        # P6(a) 反映: 任意の入力で p50 <= p90 <= p99 が成立
        # cut index 49/89/98 の off-by-one 検出
        pass

class TestSubagentMetricsAddsPercentileFields:
    def test_metrics_dict_has_percentile_keys(self): pass
    def test_p50_p90_p99_present_when_durations_present(self): pass
    def test_percentile_none_when_no_durations(self): pass
    # avg + p99 + sample_count の 3 軸が同一サンプルから出ること
    def test_avg_and_percentiles_share_sample_set(self): pass
    def test_sample_count_equals_len_durations(self): pass
    def test_sample_count_le_count_invariant(self):
        # P6(b) 反映: durations が None の invocation で count > sample_count に
        # なるケースを test fixtures で作り、sample_count <= count を pin
        pass
    # 後方互換: 既存テストが見ている count / failure_rate / avg_duration_ms は
    # そのまま動く (regression)
    def test_existing_fields_unchanged(self): pass

class TestInvocationRecords:
    def test_empty_returns_empty(self): pass
    def test_single_invocation_with_start_only(self): pass
    def test_single_invocation_with_lifecycle_only(self): pass
    def test_start_plus_stop_pair_one_invocation(self): pass
    def test_start_failed_records_failed_true(self): pass
    def test_stop_failed_records_failed_true(self): pass
    def test_both_succeeded_records_failed_false(self): pass
    def test_timestamp_uses_start_when_present(self): pass
    def test_timestamp_falls_back_to_lifecycle(self): pass
    def test_invocation_count_matches_metrics_count(self):
        # invocation_records と aggregate_subagent_metrics の type 別 count が一致
        pass

class TestAggregateSubagentFailureTrend:
    def test_empty_events_returns_empty_list(self): pass
    def test_single_invocation_creates_one_bucket(self): pass
    def test_sunday_2359_and_monday_0000_are_different_weeks(self):
        # 2026-04-26 (Sun) 23:59:59 UTC と 2026-04-27 (Mon) 00:00:00 UTC が別 week_start を持つ
        # → "2026-04-20" と "2026-04-27" の 2 bucket
        pass
    def test_monday_0000_is_new_week_boundary(self):
        # 月曜 00:00 UTC ちょうどの invocation は week_start=その月曜
        pass
    def test_weeks_normalized_to_monday_utc(self):
        # 火曜 / 水曜 / 日曜 (同週) を投げて全部同じ week_start にまとまる
        pass
    def test_naive_timestamp_treated_as_utc(self):
        # P3 反映: timestamp が naive (`+00:00` 等の TZ サフィックスなし) でも
        # UTC として解釈され、local TZ shift が起きないことを pin。
        # 同一週の日時を naive / aware 両形式で投げ、week_start が一致すること
        pass
    def test_failure_rate_when_count_is_zero_invariant(self):
        # 構造的に count=0 の bucket は出力しないが、念の為「もし 0 なら 0.0」を pin
        pass
    def test_failure_rate_calculation(self):
        # count=4, fail=1 → failure_rate=0.25
        pass
    def test_multiple_subagent_types_separated(self): pass
    def test_observed_zero_subagent_not_in_output(self):
        # subagent_type=A しか観測されていない期間に B の trend は出力されない
        pass
    def test_sort_by_week_then_type_lex(self):
        # 入力順に依存せず (week_start ASC, subagent_type ASC) で sort される
        pass
    def test_returns_all_types_no_top_n_cap(self):
        # P2 反映: server は top-N で切らず観測された全 (week, type) を返す。
        # 6 type を投入し戻り値配列に 6 type すべての trend が含まれること。
        # client 側 top-5 はあくまで UI affordance
        pass
    def test_archive_skip_documented(self):
        # 本 schema は hot tier のみ集計の前提 (build_dashboard_data 経由)
        pass

    def test_failure_count_matches_metrics_failure_count(self):
        # Q1 反映: invocation_records 経由の trend と aggregate_subagent_metrics の
        # failure_count が type 単位の合計で一致 (drift guard)。count だけでなく
        # failure_count でも pin することで _process_bucket と
        # _bucket_invocation_records の failure 判定 drift を検出
        pass

class TestBuildDashboardDataIncludesQualityFields:
    def test_subagent_failure_trend_key_present_empty(self): pass
    def test_subagent_ranking_items_have_percentile_keys(self):
        # build_dashboard_data の subagent_ranking 配列要素が p50/p90/p99/sample_count を持つ
        pass
    def test_percentile_consistency_with_avg(self):
        # avg は内部 invocation_durations の mean、p50 は同 list の median
        # → 単一 sample 入力で avg == p50 == p90 == p99 となる
        pass
```

### 新規 template 構造テスト (`tests/test_quality_template.py`)

```python
class TestQualityPageDOM:
    def test_quality_section_is_no_longer_placeholder(self):
        # page-placeholder class が外れている
        pass
    def test_quality_section_has_percentile_panel(self): pass
    def test_quality_section_has_trend_panel(self): pass
    def test_quality_section_has_no_overview_widgets(self):
        # kpiRow / skillBody / subBody 等が混入しない
        pass
    def test_template_has_percentile_renderer_function(self): pass
    def test_template_has_trend_renderer_function(self): pass
    def test_loadAndRender_invokes_quality_renderers(self): pass
    def test_percentile_table_has_thead_columns(self):
        # P5 反映: 列順は Subagent / Count / Samples / avg / p50 / p90 / p99
        # (Samples が Count 直後)。順序を pin する文字列インデックス比較で確認
        pass
    def test_trend_chart_uses_svg(self):
        # renderSubagentFailureTrend 内に <svg viewBox= / <polyline / <circle が出る
        pass
    def test_trend_single_week_renders_circles_no_polyline(self):
        # P4 反映: weeks.length === 1 の degenerate path で、renderer が
        # polyline を 0 本、circle を top.length 本だけ出すロジック (= pts.length >= 2 の guard)
        # を template grep で確認。renderer body 内で `pts.length >= 2` の条件分岐が
        # 存在することを pin
        pass
    def test_percentile_renderer_has_page_scoped_early_out(self):
        # 関数冒頭 400 chars 以内に "activePage !== 'quality'" が出る
        pass
    def test_trend_renderer_has_page_scoped_early_out(self): pass
    def test_percentile_data_tip_kind_present(self):
        # data-tip="percentile" + "kind === 'percentile'" の grep
        pass
    def test_trend_data_tip_kind_present(self): pass
    def test_quality_panel_uses_coral_for_percentile(self):
        # panel-head c-coral が percentile panel に付いている
        pass
    def test_quality_panel_uses_mint_for_trend(self):
        # panel-head c-mint が trend panel に付いている
        pass
```

### 既存テストへの影響 (regression)

- `tests/test_dashboard_router.py:test_non_overview_pages_are_placeholders` —
  loop が `['quality', 'surface']` を回している。本 PR で **`quality` を loop から
  外し `['surface']` のみに縮小**。以後 `surface` が #62 で実装されたとき同じ操作
- `tests/test_dashboard.py:TestBuildDashboardData` 系 — `subagent_ranking` を
  count 等で assert しているが、新 percentile キーは additive なので破壊なし。
  ただし「dict キーが完全一致」を assert している test があれば書き換え要 (要 grep で
  事前確認)
- `tests/test_subagent_metrics_*.py` (= subagent_metrics 直接テスト)
  — metrics dict の キー集合を assert している test があれば 4 キー追加で書き換え。
  既存 test の出力 dict を更新すれば pass する想定 (regression 修正であって新仕様 test ではない)
- `tests/test_export_html.py` — `window.__DATA__` round-trip のみ → 影響なし

### テスト数の見込み

- 新規 server unit: percentile (~8 / +P1+P6a) + metrics 拡張 (~7 / +P6b) +
  invocation_records (~10) + trend (~14 / +P2+P3+Q1) + integration (~3) = **~42 テスト**
- 新規 template 構造: **~15 テスト** (+P4)
- 既存 test の小修正: 1〜3 件想定 (dict キー集合系 + router placeholder loop 縮小)
- **合計: ~645 + 57 ≈ ~702 tests / 全 pass** (現状 645 pass + 1 skip)

## 📦 実装ステップ (TDD red→green→refactor)

> **並行可ノート (2-Q3 反映)**: `memory/subagent_quality.md` 新規作成 (Phase 8.3)
> は **Phase 1 RED と並行で書き始めて OK**。決定根拠 (percentile method 選択 /
> 週境界 / `_process_bucket` ↔ `_bucket_invocation_records` の役割分担 /
> `subagent_<metric>_trend` 命名規約) は本 PR で確定済みなので、実装中に振れる
> 余地は小さい。CLAUDE.md "Dogfood workflow doc changes" の観点でも、新規
> topic file は本 PR 完結に揃えたい。

### Phase 1: percentile helper + metrics 拡張 (RED → GREEN)

1. **RED**: `tests/test_subagent_quality.py` 新規 + `TestPercentileEdgeCases` +
   `TestSubagentMetricsAddsPercentileFields` を書く。`statistics.quantiles` の
   inclusive method の挙動を test に焼き込む
2. **GREEN**: `subagent_metrics.py` に `_percentiles()` helper を追加。
   `_build_metrics()` を拡張して 4 キー追加
3. **REFACTOR**: 既存テストの metrics dict キー集合 assert があれば追従修正

### Phase 2: invocation_records helper (RED → GREEN)

1. **RED**: `TestInvocationRecords` を書く (~10 tests)
2. **GREEN**: `subagent_metrics.py` に `invocation_records()` + 内部 helper
   `_bucket_invocation_records()` を追加。`_process_bucket` のロジックを **複製しない**
   (= 同じ `_build_invocations` を使い、failure 判定だけ records 形式に整形)。共通
   helper 化はこの段階では deferred — 1 共通 helper で `(failures, durations, records)`
   の 3 値を返す変更は blast radius が大きいので別 PR
3. **REFACTOR**: 必要に応じて `_process_bucket` と `_bucket_invocation_records` の重複
   を共通化検討 (本 PR では見送り、申し送り)

### Phase 3: aggregate_subagent_failure_trend (RED → GREEN)

1. **RED**: `TestAggregateSubagentFailureTrend` (~14 tests)。週境界の boundary
   test を厳密に書く (Sun 23:59 / Mon 00:00 / Mon 23:59 / Tue 00:00)。P3 の
   naive timestamp / P2 の top-N 切らない / Q1 の failure_count drift guard も含む
2. **GREEN** (2-Q1 反映): `subagent_metrics.py` の `invocation_records` の **直後** に
   `aggregate_subagent_failure_trend()` 実装。`dashboard/server.py` 側は import
   1 行追加 (`from subagent_metrics import ..., aggregate_subagent_failure_trend`)

### Phase 4: build_dashboard_data 統合

1. **RED**: `TestBuildDashboardDataIncludesQualityFields` 3 tests
2. **GREEN**: `build_dashboard_data` の return dict に
   ```python
   "subagent_failure_trend": aggregate_subagent_failure_trend(events),
   ```
   1 行追加 (raw events を渡す)

### Phase 5: Quality ページ DOM (RED template tests → GREEN)

1. **RED**: `tests/test_quality_template.py` 新規 (~14 tests) +
   `tests/test_dashboard_router.py:test_non_overview_pages_are_placeholders` の
   loop から `quality` を除外する変更を **テスト先行で書く** (= test を直す → red →
   template 修正で green)
2. **GREEN**: `<section data-page="quality">` の中身を全置換、`page-placeholder` class
   を削除

### Phase 6: CSS / JS renderer (visual smoke)

1. CSS 追加 (`/* subagent percentile table (Issue #60 / A5) */` + B3 SVG 用)
2. JS: `renderSubagentPercentile` + `renderSubagentFailureTrend` を
   `renderProjectSkillMatrix` の後に並べる。`loadAndRender()` 末尾で 2 行 call
3. **実機 smoke**:
   - `python3 dashboard/server.py` 起動 + 自分の usage.jsonl
   - `#/quality` で 2 panel が縦に並ぶこと
   - percentile table: 行が count 降順 / `-` を None セルに正しく出すこと
   - trend chart: 上位 5 type の line が描画され、grid line / 軸ラベルが見えること
   - hover で tooltip (`Explore p50: 1.2s / p90: 4.5s / p99: 12s` /
     `Explore 2026-04-21: 16% (2/12)`)
   - keyboard tab で行 / 点に focus し tooltip が出ること
   - **Safari でも `<circle tabindex="0">` に Tab focus が入ること** (2-P3 反映)。
     SVG2 では tabindex が効くが Safari 旧版で focus 取れない既知問題があるため
     明示確認。Chrome / Firefox / Safari 全部で focus + tooltip 動作を smoke
   - SSE refresh で再描画
   - `#/` 起動 → `#/quality` navigate で即時描画 (hashchange 連携)
   - `python3 reports/export_html.py --output /tmp/static.html` で static export
     にも反映
   - 単一週しか観測されていないデータ (= weeks.length === 1) でも chart が描けること
     (xOf の degenerate path)

### Phase 7: tooltip 拡張 + dtipBuild() 分岐

1. `dtipBuild(el)` 内、cooc/projskill 分岐の後に `percentile` / `trend` 分岐追加
2. CSS の `.data-tip[data-kind="percentile"]` / `[data-kind="trend"]` border-left

### Phase 8: docs

1. **`docs/spec/dashboard-api.md`** に 2 セクション additive:
   - `## subagent_ranking 拡張 (Issue #60, v0.7.0〜)` — percentile キー
     (`p50_duration_ms / p90_duration_ms / p99_duration_ms / sample_count`) の追記。
     **Q3 反映**: これらは `subagent_ranking` 配列の **全要素** に乗るので、Quality
     ページ専用ではなく Overview ranking や `reports/summary.py` (将来) からも
     アクセス可能、と明記
   - `## subagent_failure_trend (Issue #60, v0.7.0〜)` — 新 schema。
     **P2 反映**: 「server は top-N で切らず観測された全 (week, type) を返す」を
     prominent に記述。client 側 top-5 は UI affordance であることを明示。
     **P7 反映**: 命名規約「`subagent_<metric>_trend` for weekly time-series」を
     セクション末に明文化し、後続で `subagent_duration_trend` 等を additive で
     追加できる足場を pin。
     **P1 反映**: 計算手法は `statistics.quantiles(method="inclusive")` =
     Excel `PERCENTILE.INC` 等価、と明記 (numpy default 等価とは書かない)
2. **`CLAUDE.md`** — 「ダッシュボード複数ページ構成」表の Quality 行が現状
   「A2 / A3 / A5 / B3」となっており、本 PR で **A5 + B3 のみ** が実装される
   (A2 / A3 は #61 で予定)。表の文言は変更不要 (= 計画は同じ)。spec doc 詳細
   への pointer も `docs/spec/dashboard-api.md` に既出。**追加変更なし**
3. **MEMORY.md / 新規 `memory/subagent_quality.md` (Q2 反映)** — MEMORY.md は
   現状 260 行で 200 行 limit 超過。dense な内容 (percentile method の選択根拠 /
   週境界 monday-UTC + naive safety belt / `_process_bucket` ↔
   `_bucket_invocation_records` の役割分担と drift guard / `subagent_<metric>_trend`
   命名規約) を `memory/subagent_quality.md` に **新規 topic file** として作成
   (~30 行想定)。 MEMORY.md には 1 行 pointer entry
   (例: `- [Subagent quality (#60)](subagent_quality.md) — percentile method / week boundary / drift guard`)
   のみ追加し、行数増加を最小化
4. **`subagent_metrics.py`** — `_build_metrics` / `invocation_records` /
   `aggregate_subagent_failure_trend` の docstring を percentile キー / week 仕様
   含めて更新 (本 PR の API 拡張部分)。**2-P4 反映**: 本文 inline マーカー
   (`(P1 反映)` / `(P3 反映)` 等) を docstring 内コメントに転記する。例:
   `_percentiles` docstring 末尾に `# Excel PERCENTILE.INC 等価 (Issue #60 / P1)`、
   `aggregate_subagent_failure_trend` docstring 末尾に `# naive datetime は UTC として扱う (Issue #60 / P3)`、
   `# server は top-N で切らない・client 側 top-5 は UI affordance (Issue #60 / P2)`。
   これにより plan 削除後も実装者がコードから根拠 issue を辿れる

### Phase 9: PR

ブランチ: `feature/60-subagent-quality` (#57/#58/#59 命名規則踏襲)
PR タイトル候補: `feat(dashboard): subagent quality — percentile + weekly trend (#60)`

#### PR 粒度判断

**recommendation: A5 + B3 一括 PR**

両 viz は同じ Quality ページにマウントされ、A5 は `subagent_ranking` 拡張、
B3 は新 field。filter 慣習・page-scoped early-out・TDD 流れがほぼ同型なので、
分割 review 価値は低い。

**condition for splitting** (Issue #59 P6 と同型のトリガー):

- schema field 名の変更要求 (例: `subagent_failure_trend` → 別名)
- 週境界の変更要求 (monday-UTC → ISO week → calendar week 等)
- percentile method の変更要求 (inclusive → exclusive / nearest-rank)
- 「percentile は subagent_metrics ではなく dashboard 側で計算してほしい」要求

これらは下流 (template / docs / test) を全 trigger するため、回数を待たず即分割
が経済的。逆に CSS / DOM / 文言など stylistic な指摘は何 round 入っても一括 PR を
継続する。

PR 本文:
- 親 issue #48 / 当該 issue #60 / 前提 PR #57 (shell), #58 (heatmap), #59 (cross-tab)
- A5 / B3 schema 例 + percentile method の決定背景
- 週境界 (monday-UTC) の根拠
- 実機スクショ: percentile table / failure trend chart / tooltip

base branch: **`v0.7.0`** (Issue #57/#58/#59 と同じ)

## 🚫 Out of Scope

issue 本文記載に加え、以下も本 PR では扱わない:

- **skill_tool 側の percentile** (issue 本文明記 / 別 issue 候補)
- **月次 / 日次 trend** (週次のみ。粒度を 1 つに絞る — issue 本文明記)
- **archive 込みの trend** (dashboard 仕様で hot tier のみ — issue 本文明記)
- **subagent invocation の token / cost 系** (= 観測してない)
- **Top-N selector** (default top-5 固定で着地。selector は後続 issue 候補)
- **percentile method の selector** (inclusive method 固定)
- **trend chart のズーム / pan / クリックで詳細** (静的 SVG のみ)
- **failure_count gradient / heatmap 化** (line chart のみ)
- **A5 を Overview ranking テーブルにも反映** (Quality 専用 panel に閉じる。
  Overview の subBody は既存表示を維持して情報密度爆発を避ける)
- **reports/summary.py の percentile 反映** (issue 本文「terminal 出力にも percentile
  反映するなら追加」だが、terminal は avg + failure_rate でも足りているので
  本 PR は dashboard 側に閉じる。後続 issue 候補)

## 🧷 リスクと不確実性

| リスク | 影響 | 対策 |
|---|---|---|
| `statistics.quantiles(method="inclusive")` 結果が読者の期待 (numpy デフォルト等) と一致しないことの解釈ズレ | UX | help-pop に「Excel `PERCENTILE.INC` 等価 (線形補間)」と明記。numpy default (`method="linear"` exclusive) との等価性は **主張しない**。test では `[1,2,3,4]` 等の既知サンプルで p50=2.5 / p90=3.7 / p99=3.97 を pin して method 切替えによる regression を検出 (P1 反映) |
| sample_count が極小 (1〜2) の subagent で percentile が誤解を招く | UX | sample_count 列を percentile と同じ行に出し、件数で読み手が信頼度判断できる UI 設計 |
| invocation_records が `_process_bucket` と二重実装になり挙動 drift | 回帰リスク | invocation_count == metrics.count を test で pin (`test_invocation_count_matches_metrics_count`) + failure_count 一致も pin (Q1: `test_failure_count_matches_metrics_failure_count`) / 余り stops 経路の構造的一致を 2-P1 drift guard 注記で保証。共通化は別 PR の申し送り |
| 週境界のタイムゾーン依存 (local TZ vs UTC) で読者が混乱 | UX | help-pop に「月曜 00:00 UTC 起算」明記。ISO date 表記でブラウザ TZ に依存しない |
| 単一週しか観測されていない場合 (weeks.length === 1) で line chart が描けない | 描画失敗 | renderer 側 degenerate path: 中央 1 点に circle のみ + line skip (pts.length >= 2 条件) |
| line chart 5 本以上に伸びると palette 衝突 / 視認低下 | 視認性 | top-5 cap で上限を抑える (default)。後続 issue で selector 化 |
| Quality ページが縦長になりすぎる (#61 で A2/A3 が更に増える前提) | scroll 負荷 | 本 PR は 2 panel に留めるので問題なし。#61 時点で再評価。#59 plan の Q2 と同じスタンス |
| `test_non_overview_pages_are_placeholders` を `quality` 除外で書き換え | router 規範のガード弱化 | 代わりに `test_quality_template.py:test_quality_section_is_no_longer_placeholder` が新たな構造ガードになる |
| `subagent_metrics` のモジュール公開 API に `invocation_records` / metrics dict 4 キー追加 → 外部利用 (reports/summary.py 等) への影響 | 後方互換 | 全て **additive**。既存利用箇所 (`aggregate_subagent_metrics`) のキーアクセスは破壊しない |

## ✔️ Definition of Done

- [ ] `tests/test_subagent_quality.py` の新規 ~42 unit tests 全 pass
- [ ] `tests/test_quality_template.py` の新規 ~15 構造テスト全 pass
- [ ] `tests/test_dashboard_router.py:test_non_overview_pages_are_placeholders` が
      `['surface']` のみを loop し pass
- [ ] 既存 `tests/test_dashboard*.py` / `test_subagent_metrics*.py` 全 pass (regression)
- [ ] **全 ~702 tests pass** (現状 645 pass + 1 skip / + 約 57 / dict キー assert 修正
      数件)
- [ ] 実機: 自分の usage.jsonl で Quality に 2 panel が並び、percentile table と
      failure trend chart が描画される
- [ ] A5 sub label に `N subagent types` / B3 sub label に
      `N weeks · top M / K types` が出る
- [ ] hover tooltip が両 widget で正しく出る
- [ ] keyboard tab で行 / 点に focus + tooltip
- [ ] SSE refresh / `#/` → `#/quality` navigate / static export いずれでも描画
- [ ] week boundary: 日曜 23:59 UTC と月曜 00:00 UTC が別 week_start に分かれる
      ことが test で pin
- [ ] failure_rate count=0 で NaN にならない (= bucket そもそも出力しない / 構造不変)
- [ ] B3: 観測なし subagent は trend に含まれない (= 空 line を描かない)
- [ ] `docs/spec/dashboard-api.md` に 2 セクション (or 1 拡張 + 1 新規) 追加。
      P2 (top-N UI affordance) / P7 (命名規約) / Q3 (全 consumer 対象) を反映
- [ ] `memory/subagent_quality.md` を新規作成 (Q2 反映、~30 行)
- [ ] `MEMORY.md` に 1 行 pointer index 追加 (topic file への参照のみ)
- [ ] `aggregate_subagent_failure_trend` が **`subagent_metrics.py`** に配置されている
      (`dashboard/server.py` 側ではない / 2-Q1 反映)。`grep -n
      "def aggregate_subagent_failure_trend" subagent_metrics.py` で hit すること
- [ ] PR `feature/60-subagent-quality` を `v0.7.0` ブランチ向けに作成

## 📦 変更ファイル一覧 (見込み)

- `subagent_metrics.py` — `_percentiles()` helper + `_build_metrics()` 拡張 +
  `invocation_records()` + `_bucket_invocation_records()` +
  **`aggregate_subagent_failure_trend()`** 追加 (2-Q1 反映で `dashboard/server.py`
  から移動) (~110 行追加)
- `dashboard/server.py` — `aggregate_subagent_failure_trend` を import +
  `build_dashboard_data` に 1 行統合 (~5 行追加)
- `dashboard/template.html` — Quality section 全置換 (placeholder → 2 panel) /
  CSS 追加 / JS renderer 2 個 + dtipBuild 2 分岐 + `fmtDur()` helper 追加
  (~280 行追加 / ~10 行削除)
- `tests/test_subagent_quality.py` (新規) — server / metrics 側 unit tests
- `tests/test_quality_template.py` (新規) — template 構造テスト
- `tests/test_dashboard_router.py` — 1 loop 修正 (`['quality', 'surface']` →
  `['surface']`)
- `tests/test_dashboard.py` / `test_subagent_metrics*.py` — dict キー assert 系の
  追従修正 (数件想定)
- `docs/spec/dashboard-api.md` — `subagent_ranking` percentile 拡張 (Q3: 全 consumer
  対象であることを明記) + `subagent_failure_trend` セクション (P2: top-N UI affordance /
  P7: 命名規約) (~70 行追加)
- `~/.claude/projects/.../memory/subagent_quality.md` (新規 / Q2 反映) — percentile
  method / 週境界 + naive safety belt / `_process_bucket` 役割分担 / 命名規約 (~30 行)
- `~/.claude/projects/.../memory/MEMORY.md` — 1 行 pointer index (topic file への参照)

`reports/summary.py` / `reports/export_html.py` / archive 系は触らない。

## 📨 後続 PR への申し送り

- **#61 (Quality: A2 / A3 = permission/skill / compact 密度)** は同 Quality ページに
  追加 panel として乗る。本 PR で `data-page="quality"` の page-scoped early-out
  パターンを確立しているので、新 panel renderer は同パターンを踏襲するだけで OK
- **#62 (Surface)** は別 page。干渉しない
- **`reports/summary.py` の percentile 反映** は別 issue 候補 (terminal レポートで
  p50/p90/p99 を出す価値はあるが、本 PR は dashboard 視覚化に集中)
- **`_process_bucket` と `_bucket_invocation_records` の共通化** は本 PR で deferred。
  両方が `(start, stop, lifecycle)` から 3 種の出力 (failures, durations, records)
  を生むので、1 helper に集約する余地あり。本 PR で blast radius 大のため見送り、
  共通化が必要になるトリガー (= 4 つ目の出力種が必要になったとき) で別 PR
- **Top-N selector / percentile method selector** は UX 価値が見えてから別 issue 化
- **archive 込みの trend** が必要になったら、`reports/_archive_loader` 経由で
  events を読む別 endpoint (`/api/data?include_archive=1`) を新設する設計が自然。
  本 PR はその余地を塞がない (`build_dashboard_data` は events を引数で受けるので
  archive を contextually 注入できる構造のまま)
