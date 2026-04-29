# `/api/data` レスポンス schema

`dashboard/server.py` の `build_dashboard_data()` が返す `/api/data` JSON の
**schema 仕様**。SSE refresh / 静的エクスポート (`window.__DATA__` 注入) どちらの
経路でも同じ形が browser に届く。

> 集計のフィルタ慣習 (usage 系 only / subagent invocation 単位 dedup) は
> `dashboard/server.py:_filter_usage_events()` で一括管理。本仕様で「usage 系」と
> 書いたフィールドはすべてこの helper を経由している。

## トップレベル形

```json
{
  "last_updated":      "<ISO 8601 UTC>",
  "total_events":      <int>,
  "skill_ranking":     [...],
  "subagent_ranking":  [...],
  "daily_trend":       [...],
  "project_breakdown":       [...],
  "hourly_heatmap":          { ... },
  "skill_cooccurrence":      [...],
  "project_skill_matrix":    { ... },
  "subagent_failure_trend":  [...],
  "session_stats":           { ... },
  "health_alerts":           [...]
}
```

各フィールドは additive で増える前提 (browser 側は欠損キーに defensive)。

## `hourly_heatmap` (Issue #58, v0.7.0〜)

時間帯 × 曜日のヒートマップ用 payload。Patterns ページで描画される。

### 形

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

- `timezone`: 集計時の TZ。現状は常に `"UTC"`。将来 server pre-bin に倒すと
  `"Asia/Tokyo"` 等になる (下記 migration 節参照)
- `buckets`: hour-truncated UTC の bucket 列。`hour_utc` は ISO 8601 完全形
  (`+00:00` 含む)。`count` は int
- buckets は `hour_utc` 昇順
- empty events なら `buckets: []`

### 集計仕様

- 対象 event_type: usage 系のみ
  - `skill_tool` / `user_slash_command`
  - subagent invocation (`usage_invocation_events()` で `subagent_start` +
    `subagent_lifecycle_start` を invocation 単位に dedup した代表 1 件)
- 対象外: `session_*` / `notification` / `instructions_loaded` / `compact_*` /
  `subagent_stop` (= `total_events` / `daily_trend` / `project_breakdown` と
  完全に同じ filter 慣習)
- timestamp parse 失敗 (空文字 / not parseable / `timestamp` キー欠損) は silent
  skip。naive datetime も skip
- truncate: `astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)`

### Browser 側 binning

server は UTC のまま hour bucket を返し、browser 側で local TZ + (weekday, hour)
の 7×24 matrix に bin する設計 (option 3 hour-bucketed UTC):

```javascript
const d = new Date(b.hour_utc);              // local TZ で解釈される Date
const wd = (d.getDay() + 6) % 7;             // 0=Mon..6=Sun
const h = d.getHours();                      // 0..23 (local hour)
matrix[wd][h] += b.count;
```

- weekday index: `Date.getDay()` は 0=Sun..6=Sat。`(getDay() + 6) % 7` で
  0=Mon..6=Sun に変換 (UI は Mon-Sun の行並び)
- hour: `Date.getHours()` は integer 0..23

### 設計判断 — TZ 戦略

設計時に検討した 3 案:

| 案 | payload 上限 (180 日) | TZ 精度 | 採否 |
|---|---|---|---|
| (1) per-event raw timestamp 列 | 数万〜数十万件 | 完全 lossless | 不採用 (SSE 帯域) |
| (2) server-side UTC 7×24 pre-bin | 168 cells 固定 | UTC 固定 (体感とズレる) | 不採用 |
| (3) hour-bucketed UTC | 最大 4320 entries (180 日 × 24h) | 整数 hour offset TZ で lossless / 半 hour offset で軽微 lossy | **採用** |

### 半 hour offset TZ の lossiness

India (UTC+5:30) / Newfoundland (UTC-3:30) / Iran (UTC+3:30) など半 hour offset の
TZ では、UTC hour bucket が local の 2 hours に半分ずつ振り分けられる。`Date`
constructor + `getHours()` は integer 丸めなので、たとえば:

- UTC `09:00` 1 件 → IST 14:30 → ブラウザ上で **(Tue, 14:00) cell に count=1** が立つ
  (15:00 cell には立たない)

これは「30 分後ろ寄せ表示」として実用許容。実利用フィードバックで顕在化したら
option (2) への移行を検討する。

### Migration path — option (2) への移行

将来 server pre-bin に倒す場合の互換戦略:

1. 新 field `weekday_hour_matrix: number[7][24]` を additive で追加 (0=Mon..6=Sun /
   0..23 hour)
2. `timezone` 値を `"Asia/Tokyo"` 等に変更
3. browser 側 renderer は `payload.weekday_hour_matrix` があればそれを優先、
   無ければ既存の `buckets` 経路 (= 現クライアントは旧 field のままで動く)
4. `timezone === "UTC"` でも `buckets` 経路を踏襲することで rollback 容易

新クライアントを既存 server (`buckets` のみ返す) に当てても壊れない設計。

### Multi-TZ probe (実装検証済み)

| TZ | UTC event | 期待 cell | pin |
|---|---|---|---|
| Tokyo (UTC+9) | `2026-04-28T01:00:00+00:00` | (Tue, 10:00) | ✅ |
| NY DST (UTC-4) | `2026-04-28T13:00:00+00:00` | (Tue, 09:00) | ✅ |
| NY std (UTC-5) | `2026-01-13T13:00:00+00:00` | (Tue, 08:00) | ✅ |
| Kolkata (UTC+5:30) | `2026-04-28T09:00:00+00:00` | (Tue, 14:00) | ✅ (15:00 ではない) |

## `skill_cooccurrence` (Issue #59, v0.7.0〜)

同一 session 内で一緒に使われた skill / slash command のペア。Patterns ページの
スキル共起マトリクス table で描画される (B1)。

### 形

```json
{
  "skill_cooccurrence": [
    {"pair": ["frontend-design", "webapp-testing"], "count": 12},
    {"pair": ["codex-review", "verify-bot-review"], "count": 9}
  ]
}
```

- 配列は **count 降順、同 count 内では `pair` の lexicographic 昇順** で安定 sort
- `pair` は **常にソート済み** (`pair[0] <= pair[1]`)。browser 側は順序を仮定 OK
- `count` の単位は **両 skill が両方登場した unique session 数**。同 session 内の
  重複呼び出しは 1 回扱い (invocation 数ではない)
- 空入力なら `[]`
- 最大 100 pair (`top_n=100` cap)

### 集計仕様

- 対象 event_type: `skill_tool` / `user_slash_command` のみ (= `aggregate_skills`
  と同じ filter 慣習、subagent は対象外)
- `session_id` ごとに skill 名を unique 集合化 (空 session_id / 空 skill は skip)
- `itertools.combinations(sorted(skills), 2)` で 2-pair 列挙 (self-pair は性質上
  自然に除外される)
- 全 session を `Counter` で合算
- **明示 sort**: `sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]`
  で順序を pin (`Counter.most_common` の暗黙 insertion order を避ける)

### 設計判断 — count 単位

**recommendation: session 単位**

workflow 発見が主目的のため、session 内で同じ pair が複数回トリガされても 1 と
数える。tooltip / table header / help-pop / docstring すべてで `sessions` 単位を
明示。

### 将来拡張

invocation 単位の共起が必要になったら `skill_cooccurrence_invocations` を additive
で新設する。本フィールド名に単位を含めない選択は YAGNI 採用 — 単一の単位で十分な
うちは現名が読みやすい。

## `project_skill_matrix` (Issue #59, v0.7.0〜)

project (上位 10) × skill (上位 10) のクロス利用件数 dense matrix。Patterns
ページのプロジェクト × スキル heatmap で描画される (B2)。

### 形

```json
{
  "project_skill_matrix": {
    "projects": ["chirper", "claude-transcript-analyzer", "..."],
    "skills":   ["frontend-design", "codex-review", "..."],
    "counts": [
      [12, 3, 0, 8, ...],
      [5, 0, 7, 2, ...]
    ],
    "covered_count": 234,
    "total_count": 312
  }
}
```

- `projects` / `skills`: それぞれ **count 降順** で並んだ name 配列。最大 10 件
- `counts`: rows = `projects`, cols = `skills` の 2D int array
  - invariant: `len(counts) == len(projects)` / `len(counts[i]) == len(skills)`
  - cell 0 を含む dense 形式
- `covered_count`: matrix に乗っている events 数 (= `sum(sum(row) for row in counts)`)
- `total_count`: filter 後 (skill_tool + user_slash_command) の全体 events 数
  (top 漏れ含む)。`covered_count <= total_count`
- 空入力なら `{"projects": [], "skills": [], "counts": [], "covered_count": 0, "total_count": 0}`
- top 10 軸より少ない場合 (例: project 3 種類のみ) は短い配列のまま返す
- カバー率 = `covered_count / total_count` (`total_count > 0` のとき)。browser 側
  でパーセント整数化して sub label `"X% covered (covered/total)"` 表示

### 集計仕様

- 対象 event_type: `skill_tool` / `user_slash_command` のみ (subagent 除外)
- 空 project / 空 skill の event は skip
- 1 pass で `(project, skill) → count` の Counter + project 別合計 + skill 別
  合計 + total_count を同時集計
- top 10 ずつ抽出 → dense 2D matrix 構築
- `covered_count` は matrix 構築後に `sum(sum(row) for row in counts)` で算出

### 設計判断 — "other" 集約は採用しない

**recommendation: drop (top 漏れは表示しない、カバー率 sub label で量を可視化)**

trade-off:

| 案 | pros | cons |
|---|---|---|
| (a) drop + カバー率 sub | 既存 `skill_ranking` / `project_breakdown` と整合、heatmap が読みやすい、dense matrix が常に 10×10 max で予測可 | top 漏れの中身は見えない |
| (b) "other" 行/列 を追加 | truncate された量が可視 | matrix が 11×11 になり、"other" の中身ドリルダウンを期待されると応答できない |

カバー率 sub label が (a) の cons を実用上カバーしている。(b) への migration が
必要になったら `projects` / `skills` 配列の末尾に `"other"` 要素を additive で
足す互換戦略を取る。

## `subagent_ranking` percentile 拡張 (Issue #60, v0.7.0〜)

`subagent_ranking` 配列の各要素に **percentile + sample_count** を additive 追加。
既存フィールド (`name` / `count` / `failure_count` / `failure_rate` / `avg_duration_ms`)
は破壊しない。

### 形

```json
{
  "name": "Explore",
  "count": 12,
  "failure_count": 1,
  "failure_rate": 0.083,
  "avg_duration_ms": 8200.0,
  "p50_duration_ms": 7500.0,
  "p90_duration_ms": 18400.0,
  "p99_duration_ms": 32100.0,
  "sample_count": 11
}
```

- `p50_duration_ms` / `p90_duration_ms` / `p99_duration_ms`: **ミリ秒**。
  duration が観測できなかった invocation は計算対象外なので `sample_count <= count` の
  関係が常に成り立つ
- `sample_count`: percentile 計算に入った sample 数 (= duration が None でない invocation 数)
- 計算手法: `statistics.quantiles(data, n=100, method="inclusive")` で 99 cuts を取り
  index 49/89/98 を採用 (= **Excel `PERCENTILE.INC` 等価**、線形補間)。
  `numpy` のデフォルト (`method="linear"` exclusive) とは別物
- edge case:
  - `sample_count == 0` → 全 percentile が `null` (browser 側で `-` 表示)
  - `sample_count == 1` → 全 percentile が同値 (退化扱い)

### 集計範囲は全 consumer 対象 (Q3 反映)

これらの新キーは `subagent_ranking` 配列の **全要素** に乗るため、Quality ページの
percentile table 専用ではなく、Overview の subagent ranking や `reports/summary.py`
(将来の `reports/summary.py` percentile 拡張) からも同じ schema を参照する。

## `subagent_failure_trend` (Issue #60, v0.7.0〜)

subagent invocation の **週次 failure_rate trend**。Quality ページで折れ線描画される。

### 形

```json
[
  {"week_start": "2026-04-21", "subagent_type": "Explore",        "count": 12, "failure_count": 2, "failure_rate": 0.166},
  {"week_start": "2026-04-21", "subagent_type": "general-purpose", "count":  8, "failure_count": 0, "failure_rate": 0.0},
  {"week_start": "2026-04-28", "subagent_type": "Explore",        "count": 15, "failure_count": 1, "failure_rate": 0.066}
]
```

- `week_start`: ISO date string `"YYYY-MM-DD"`。**月曜 00:00 UTC 起算**
  (= Sun 23:59 UTC と Mon 00:00 UTC は別 week)
- `subagent_type`: invocation の type 名
- `count`: 当該 (week, type) bucket の invocation 数
- `failure_count`: そのうち `start.success=False OR stop.success=False` だった invocation 数
- `failure_rate`: `failure_count / count` (Python float)。`count==0` bucket は構造的に
  発生しないが、安全網として「もし 0 ならば 0.0」
- 配列は `(week_start, subagent_type)` lexicographic 昇順で **明示 sort**
  (`Counter.most_common` の insertion order 依存を避ける慣習を踏襲)
- 空入力 / observed なし subagent は配列に含まれない (`[]` または該当 type のみ欠落)

### 集計仕様

- データ源: `subagent_metrics.invocation_records()` 経由で `_build_invocations` +
  `_process_bucket` と同じ invocation 同定ロジックを使う (両 hook 並列発火 / lifecycle のみ /
  flaky 経路すべてを 1 invocation にまとめる)
- failure 判定: 各 invocation について `start.success=False OR stop.success=False` のとき failure
- 週境界: `datetime.fromisoformat(timestamp)` を UTC aware に正規化 → `dt.weekday()` (Mon=0..Sun=6)
  → `(dt.date() - timedelta(days=weekday))`
- naive timestamp safety belt: TZ 情報なしの ISO は **UTC として扱う**
  (Python 3.11+ で local TZ shift する非対称への構造防御)

### server は top-N で切らない (P2 反映)

server は **観測された全 (week, subagent_type)** を返す。クライアント側 (Quality ページ)
は count 上位 5 type に絞って描画するが、それは **UI affordance** であり schema 仕様では
ない。`/api/data` の programmatic な consumer は全 type の trend を受け取る前提で読む。
client 側 top-5 と sync させたい consumer は `subagent_ranking` の `count` で再現可能
(= `aggregate_subagents` の sort key と同一)。

### 命名規約 (P7 反映)

- **`subagent_<metric>_trend`** for **weekly time-series**: 後続で `subagent_duration_trend`
  などを additive で追加できる足場。週境界は本仕様 (monday-UTC start) を踏襲する
- 月次 / 日次 trend が必要になった場合は別キー (`subagent_<metric>_daily_trend` 等) を
  起こす。粒度を明示する命名で混在を避ける
