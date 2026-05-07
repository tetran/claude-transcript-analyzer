# `/api/data` レスポンス schema

`dashboard/server.py` の `build_dashboard_data()` が返す `/api/data` JSON の
**schema 仕様**。SSE refresh / 静的エクスポート (`window.__DATA__` 注入) どちらの
経路でも同じ形が browser に届く。

> 集計のフィルタ慣習 (usage 系 only / subagent invocation 単位 dedup) は
> `dashboard/server.py:_filter_usage_events()` で一括管理。本仕様で「usage 系」と
> 書いたフィールドはすべてこの helper を経由している。

## Query parameters (Issue #85, v0.7.3〜)

### `period` (optional)

- 値: `"7d"` / `"30d"` / `"90d"` / `"all"` (default `"all"`)
- `"all"` 以外を渡すと、Overview / Patterns / KPI counter の **11 field** が
  rolling window (`now - timedelta(days=N) <= ts <= now`) で切られた events から
  集計される。Quality / Surface / `session_stats` の **8 field** は **常に全期間**
  (period 不変)。
- 不正値 / 空値 / 不在 → `"all"` に fallback (lenient: 400 を返さない)。
- 三段 pair-straddling filter: timestamp 第一段 cut 後、`subagent_start ↔ subagent_lifecycle_start` の
  `INVOCATION_MERGE_WINDOW_SECONDS = 1.0s` 以内 sibling、および
  `subagent_start ↔ subagent_stop` の paired 直近を再 include する。これは
  `subagent_metrics._pair_invocations_with_stops` の `start_ts <= stop_ts < next_start_ts`
  pairing semantics を尊重し、`failure_rate` / `avg_duration_ms` / pXX duration が
  period boundary 跨ぎで silent drift しないことを保証するため。

### Period 適用 scope (12 field)

- KPI counter: `total_events` / `skill_kinds_total` / `subagent_kinds_total` / `project_total`
- Overview: `skill_ranking` / `subagent_ranking` / `daily_trend` / `project_breakdown`
- Patterns: `hourly_heatmap` / `skill_cooccurrence` / `project_skill_matrix`
- Sessions: `session_breakdown` (Issue #99 / v0.8.0〜)
- Overview: `model_distribution` (Issue #106 / v0.8.0〜)

### 全期間 (period 不変) scope (8 field)

- Quality: `subagent_failure_trend` / `permission_prompt_skill_breakdown` /
  `permission_prompt_subagent_breakdown` / `compact_density`
- Surface: `skill_invocation_breakdown` / `skill_lifecycle` / `skill_hibernating`
- `session_stats` (lifetime metric)

### Filter 対象外 (3 field)

- `last_updated` (常に server clock)
- `health_alerts` (独立 log 読み出し)
- `period_applied` (echo)

### `period_applied` (response field)

- server で正規化した period 文字列を additive に echo する。frontend は
  `period_applied !== 'all'` のとき Overview/Patterns sub に `<period> 集計 · ` の
  badge prefix を出す (例: `7d 集計 · top 10 · max 42`)。
- 古い frontend (period unaware) は読まないので backward-compat。

## トップレベル形

```json
{
  "last_updated":          "<ISO 8601 UTC>",
  "total_events":          <int>,
  "skill_ranking":         [...],
  "subagent_ranking":      [...],
  "skill_kinds_total":     <int>,
  "subagent_kinds_total":  <int>,
  "project_total":         <int>,
  "daily_trend":           [...],
  "project_breakdown":     [...],
  "hourly_heatmap":          { ... },
  "skill_cooccurrence":      [...],
  "project_skill_matrix":    { ... },
  "subagent_failure_trend":  [...],
  "session_stats":           { ... },
  "health_alerts":           [...],
  "skill_invocation_breakdown":  [...],
  "skill_lifecycle":             [...],
  "skill_hibernating":           { ... },
  "session_breakdown":           [...],
  "model_distribution":          { ... },
  "period_applied":          "all" | "7d" | "30d" | "90d"
}
```

各フィールドは additive で増える前提 (browser 側は欠損キーに defensive)。

### Ranking 配列と "全件 unique 数" カウンタの分離 (Issue #81, v0.7.2〜)

`skill_ranking` / `subagent_ranking` / `project_breakdown` は **最大 `TOP_N` 件 (= 10、`dashboard/server.py:TOP_N` 定数)** で cap する **UI ランキング表示用** 配列。
全件 unique 数を取りたい consumer は新規の `skill_kinds_total` / `subagent_kinds_total` / `project_total` を参照すること
(これらは Issue #81 で「Overview KPI tile が 10 で頭打ちになる」問題を解消するために導入した cap 無しカウンタ)。

`docs/spec/dashboard-api.md` 内の "10" リテラルは **ランキング cap (= UI 表示用)** にだけ適用される。KPI counter には適用されない。

#### `skill_kinds_total: int`

- 集計式: `|{ev.skill : ev.event_type ∈ {skill_tool, user_slash_command} ∧ ev.skill ≠ ""}|`
- `aggregate_skills` の counter キーセットと同一 event_type / 同一 skip 判定 (drift guard は `tests/test_dashboard.py::TestBuildDashboardData::test_skill_kinds_total_matches_aggregate_skills_when_below_cap`)
- empty events では `0`

#### `subagent_kinds_total: int`

- 集計式: `len(aggregate_subagent_metrics(events))`
- = invocation 単位 dedup (`subagent_start` + `subagent_lifecycle_start` を `INVOCATION_MERGE_WINDOW_SECONDS = 1.0` 秒以内ペアで 1 invocation 化) 後の unique subagent type 数
- 同 type の複数 invocation は **1 kind** としてカウント (= type-level dedup)
- drift guard は `test_subagent_kinds_total_matches_aggregate_subagents_when_below_cap`
- empty events では `0`

#### `project_total: int`

- 集計式: `|{ev.project : ev ∈ _filter_usage_events(events) ∧ ev.project ≠ ""}|`
- 入力は `_filter_usage_events()` 後 (= usage 系のみ + subagent invocation dedup 済み)。`session_start` / `notification` / `instructions_loaded` 等のハウスキーピング系は除外
- drift guard は `test_project_total_matches_project_breakdown_when_below_cap`
- empty events では `0`

## `last_updated` / `daily_trend` の local TZ 表示 (Issue #65, v0.7.1〜)

dashboard frontend は **local TZ** で表示する。server は UTC のまま JSON を返す。
client が `Date` の native methods で local 化する分担。

### `last_updated`

- server は `datetime.now(timezone.utc).isoformat()` (= ISO 8601 with `+00:00`
  suffix) を返す。`Z` suffix も browser の `new Date()` で同じ instant に parse
  されるので互換
- frontend は `formatLocalTimestamp(iso)` (10_helpers.js) で
  `"YYYY-MM-DD HH:mm <TZ>"` 形式に整形して header の「最終更新」に表示
- `<TZ>` 部は `Intl.DateTimeFormat(undefined, { timeZoneName: 'short' })` の出力。
  **環境依存** (Node v24 / macOS Chromium で `"GMT+9"` を観測)。他のブラウザ /
  OS では `"JST"` / `"GMT+9"` 等になりうるが、値の具体形は仕様としない
  (= test pin は正規表現で吸収)

### `daily_trend` (server は UTC 日付で返すが frontend は読まない)

- server `aggregate_daily()` は引き続き **UTC 日付** で bucket した
  `[{date: "YYYY-MM-DD", count: int}]` を返す (= /api/data の
  backward-compat field)
- dashboard frontend は **直接読まず**、`hourly_heatmap.buckets` を
  `localDailyFromHourly()` (10_helpers.js) で local TZ 日付に再集計して
  sparkline / `ledeDays` / "N 日間の観測" KPI subtitle に表示
- 影響: JST から見ると UTC 23:00 hour bucket は翌日 JST に shift する。`count`
  合計は UTC daily_trend と JST localDays で **不変** (hour bucket → 日付集約は
  count 加算のみで保存される。DST 23h/25h 日も含めて invariant)
- `localDailyFromHourly` の DST / 月またぎ / 年またぎは
  `tests/test_dashboard_local_tz.py::TestLocalDailyFromHourlyNode` で behavior
  pin (Node 経由の round-trip test)

### export_html を別ホストにコピーした場合の TZ

`reports/export_html.py` で生成した HTML は `<script>window.__DATA__ = ...</script>`
にデータを inline しているが、`formatLocalTimestamp` / `localDailyFromHourly` は
**閲覧ホスト** の `Date` で実行されるため、生成ホストと閲覧ホストの TZ が異なる
場合は **閲覧ホスト** の local TZ で render される。これは仕様 (= 受け取った人が
自分の TZ で見える方が体感に合う)。

### Frontend 一覧 — local TZ 系の処理箇所

| 場所 | 入力 | 表示 |
|---|---|---|
| header `lastRx` | `data.last_updated` | `formatLocalTimestamp` で local 整形 |
| Overview sparkline | `data.hourly_heatmap.buckets` | `localDailyFromHourly` で local 日付 daily |
| Overview KPI "N 日間の観測" | `localDailyFromHourly` の length | local 日付の observed days 数 |
| Patterns hourly heatmap | `data.hourly_heatmap.buckets` | client 側 `(weekday, hour)` bin (既存 / Issue #58) |

`subagent_failure_trend` は **Mon 00:00 UTC 起算** で固定 (本ドキュメントの該当節
参照)。Issue #65 の射程外。

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

### server は top-N で切らない

server は **観測された全 (week, subagent_type)** を返す。クライアント側 (Quality ページ)
は count 上位 5 type に絞って描画するが、それは **UI affordance** であり schema 仕様では
ない。`/api/data` の programmatic な consumer は全 type の trend を受け取る前提で読む。
client 側 top-5 と sync させたい consumer は `subagent_ranking` の `count` で再現可能
(= `aggregate_subagents` の sort key と同一)。

### 命名規約

- **`subagent_<metric>_trend`** for **weekly time-series**: 後続で `subagent_duration_trend`
  などを additive で追加できる足場。週境界は本仕様 (monday-UTC start) を踏襲する
- 月次 / 日次 trend が必要になった場合は別キー (`subagent_<metric>_daily_trend` 等) を
  起こす。粒度を明示する命名で混在を避ける

## `permission_prompt_*_breakdown` (Issue #61, v0.7.0〜)

permission notification を直前 `skill_tool` / subagent invocation に **session 内
時系列リンク** で帰属させ、skill / subagent ごとに `permission_rate` を返す。
Quality ページの「Permission per skill / subagent (top 10)」2 panel が消費する。

```json
{
  "permission_prompt_skill_breakdown": [
    {"skill": "user-story-creation", "prompt_count": 3, "invocation_count": 12, "permission_rate": 0.25}
  ],
  "permission_prompt_subagent_breakdown": [
    {"subagent_type": "Explore", "prompt_count": 5, "invocation_count": 10, "permission_rate": 0.5}
  ]
}
```

### 帰属 algorithm — 2 段階 (execution-window + backward fallback)

`PERMISSION_LINK_WINDOW_SECONDS = 30` を backward fallback 窓に使う。
v1 simple な「timestamp backward window のみ」だと、長時間 subagent の途中で発火した
permission が `subagent_start.timestamp` (= 終了時刻) より前に来てしまい構造的にミス
する。execution interval を併用して防ぐ。

```
for each notification N (permission|permission_prompt) in session S:
  candidates = []
  for ev in skill_tool events of S:
    end_ts = ev.timestamp
    start_ts = end_ts - (ev.duration_ms / 1000) if ev.duration_ms else end_ts
    if start_ts <= N.ts <= end_ts:                 covers, start_ts
    elif end_ts <= N.ts <= end_ts + 30s:           after, end_ts
  for ev in usage_invocation_events(S):
    start_ts, end_ts = subagent_metrics.subagent_invocation_interval(ev)
    # subagent_start (= 終了時刻 ts): [end - duration, end]
    # subagent_lifecycle_start (lifecycle-only): [start, start + duration]
    if start_ts <= N.ts <= end_ts:                 covers, start_ts
    elif end_ts <= N.ts <= end_ts + 30s:           after, end_ts
  if any covers: pick most recent start_ts
  elif any after: pick most recent end_ts
  else: drop (orphan permission)
```

### 単一帰属ポリシー

- 1 notification は **skill OR subagent の 1 候補のみ** に帰属 (= disjoint)
  → skill table と subagent table の `prompt_count` は合算可能 (合算 invariant:
  `sum(skill[i].prompt_count) + sum(subagent[j].prompt_count) <= 全 permission 数`)
- 候補が両方ある場合は **直近 1 個** (start_ts または end_ts がより新しい方) を選ぶ
- 帰属できない notification は本 schema に出さない (= orphan / 本 PR では捨てる)。
  実観測値で orphan ratio を見て後続で `permission_prompt_orphan_count` を additive
  で出すかを判断 (`memory/friction_signals.md` 参照)

### user_slash_command 対象外

`user_slash_command` は **候補にしない**。slash command はモデル発話の prefix で
ツール実行を伴わず permission の起因にならないため、Issue #61 本文「直前の
`skill_tool` / `subagent_start`」明記に従い skill_tool のみリンク対象とする。

### sort / top-N

- `prompt_count` 降順 → 同点は `name` 昇順 (lexicographic) で **明示 sort**
- 上位 **10 件** を返す
- `prompt_count == 0` で `invocation_count > 0` の skill / subagent は **配列に
  含めない** (top-N は prompt_count 降順で切る慣習)
- `permission_rate > 1.0` は normal な状態 (1 invocation で複数回 permission を
  聞かれるケース) なので **clamp しない**。client 側で help-pop 注釈

### invocation_count の出処 (drift guard)

- skill: 同 session で観測された `skill_tool` events 数 (success / failure 区別なし)
- subagent: `aggregate_subagent_metrics(events)[name].count` と **完全一致**
  (= drift guard / `test_subagent_attribution_count_matches_metrics_count`)。
  type 単位の合計が必ず metrics count と一致する

### notification_type の同一視

`frozenset({"permission", "permission_prompt"})` で同一視
(`_PERMISSION_NOTIFICATION_TYPES` 既存定数を再利用)。

### Known limitation — 実機 attribution 率は低めに出ること

実機 `usage.jsonl` の qualitative 観測 (本仕様 ship 時点) では、permission
notification の **多く** は直前 30 秒以内に終了した skill_tool / subagent invocation
を持たない傾向にある。これは Claude Code の hook fire 順序が
`Notification(permission) → user approves → tool 実行 → PostToolUse(skill_tool)
/ Task(subagent) の timestamp` という時系列を取るため、permission の "直前" には
帰属候補となる invocation が **まだ存在していない** ケースが構造的に多いため。

本 PR は Issue #61 本文 spec (「直前 N 秒以内に発火していた skill_tool / subagent
invocation」) に literal に従う v1 実装で、`PERMISSION_LINK_WINDOW_SECONDS = 30`
を初期値として ship する。orphan ratio (= 帰属できなかった permission / 全 permission)
が定常的に高い場合、後続 issue で以下のいずれかを検討する:

- **forward window**: `[notif_ts, notif_ts + N]` を追加し、permission の **直後** に
  実行された invocation に帰属させる (= 現実の causal 順序に沿う)
- **next-gated-invocation algorithm**: window 不採用、同 session 内 permission
  以降の最初の skill_tool / subagent invocation に帰属
- 上記 2 つの組み合わせ + window 値の調整

algorithm 仕様変更は schema 互換 (= `permission_prompt_*_breakdown` の field 名 /
形は維持) で行えるため、後続 issue で additive に進化させる。本 PR では schema
を固定し、orphan_count は schema に出さない (実観測値での後続判断のため捨てる)。

## `compact_density` (Issue #61, v0.7.0〜)

session 単位の `compact_start` 集計。histogram (`0` / `1` / `2` / `3+`) と
worst session top 10 を返す。Quality ページの「Compact 発生密度 (per session)」
panel が消費する。

```json
{
  "compact_density": {
    "histogram": {"0": 50, "1": 12, "2": 4, "3+": 2},
    "worst_sessions": [
      {"session_id": "abc-123", "count": 5, "project": "chirper"},
      {"session_id": "def-456", "count": 4, "project": "claude-transcript-analyzer"}
    ]
  }
}
```

### histogram bucket 仕様

- key は **string で固定 4 種** (`"0" / "1" / "2" / "3+"`)。空入力でも 4 キー
  すべて 0 で出力 (browser 側 defensive 不要を維持)
- bucket 境界:
  - `"0"`: session_start を持つ session のうち compact 0 件
  - `"1"` / `"2"`: 同 1 件 / 2 件
  - `"3+"`: 3 件以上 (3 / 4 / 5 ... すべて同じ bucket)

### session pool 定義

`session_start` event の `session_id` 集合を **session pool** とする。
compact_start のみで session_start が無い orphan session_id は histogram から
**除外** (= 0 bucket への混入を避ける) するが、`worst_sessions` には載せる。
histogram の 0 bucket 分母を「実観測 session 数」に揃えるため。

### worst_sessions

- 全 `compact_start` を `session_id` で groupby し count 降順で **top 10** を返す
- 同点は `session_id` 昇順で stable sort
- 各要素 `{"session_id": str, "count": int, "project": str}`
- `project` は当該 session の **最後に観測した** `compact_start.project` (空なら `""`)
- 空入力なら `[]` を返す

## skill 名正規化 (Issue #74, v0.7.0〜)

`skill_invocation_breakdown` / `skill_lifecycle` / `skill_hibernating` の 3
aggregator は **共通の正規化規則** で skill 名を扱う:

| event_type | `skill` フィールドの形 |
|-----------|---------------------|
| `skill_tool` | `"codex-review"` (先頭 `/` なし、`tool_input.skill` 由来) |
| `user_slash_command` | `"/codex-review"` (先頭 `/` あり、`command_name` 由来) |

→ aggregator 内で `lstrip("/")` を適用し、**先頭の `/` を全て剥がして** 同じ
key にマージする (例: `"//foo"` も `"/foo"` も `"foo"` に正規化される)。
正規化結果が空文字 (`""` / `"/"` / `"  "` などの whitespace-only) の event は
silent skip。`~/.claude/skills/<name>/` のディレクトリ名 (= skill ID)
と一致する形がカノニカル。

この正規化は本 3 aggregator 内に閉じる (= `aggregate_skills` などの既存 ranking
には影響しない / 別 issue)。

## `skill_invocation_breakdown` (Issue #74, v0.7.0〜)

同一 skill に対する **LLM 自律発火 (`skill_tool`)** と **ユーザー手動発火
(`user_slash_command`)** の件数比を skill ごとに集計。Surface ページ
「Skill 起動経路」panel が消費する。

### 形

```json
{
  "skill_invocation_breakdown": [
    {"skill": "codex-review",        "mode": "dual",      "tool_count": 24, "slash_count": 1,  "autonomy_rate": 0.96},
    {"skill": "user-story-creation", "mode": "dual",      "tool_count": 2,  "slash_count": 18, "autonomy_rate": 0.10},
    {"skill": "frontend-design",     "mode": "llm-only",  "tool_count": 12, "slash_count": 0,  "autonomy_rate": null},
    {"skill": "usage-archive",       "mode": "user-only", "tool_count": 0,  "slash_count": 8,  "autonomy_rate": null}
  ]
}
```

### 形 — 各フィールド

- `skill`: 正規化済み skill 名 (先頭 `/` なし)
- `mode`: 3-way 分類
  - `"dual"`: 両方観測あり (`tool_count > 0 && slash_count > 0`)
  - `"llm-only"`: `skill_tool` のみ観測
  - `"user-only"`: `user_slash_command` のみ観測
- `tool_count`: `skill_tool` event 件数 (`success=False` も含む / B2)
- `slash_count`: `user_slash_command` event 件数 (`source` の値は無視)
- `autonomy_rate`: `dual` のみ `round(tool_count / (tool_count + slash_count), 4)` の float、`llm-only` / `user-only` では `null` (UI で `—` 表示)

### sort / top-N

- sort key: `(tool_count + slash_count)` 降順 → `skill` 昇順
- top-N: `TOP_N_SKILL_INVOCATION = 20` で cap

### 失敗 event の扱い

`skill_tool.success=False` (PostToolUseFailure 由来) も `tool_count` に含める。
LLM が呼ぼうとした事実そのものが Panel 1 の意味 (= 想起率) なので、成否は
区別しない。

## `skill_lifecycle` (Issue #74, v0.7.0〜)

各 skill の lifecycle (初回 / 直近 / 30日件数 / 全期間件数 / trend) を 1 行
集計。`skill_tool` + `user_slash_command` を skill 名正規化済み key で merge
した上で算出。Surface ページ「Skill lifecycle」panel が消費する。

### 形

```json
{
  "skill_lifecycle": [
    {
      "skill": "codex-review",
      "first_seen": "2026-02-28T10:00:00+00:00",
      "last_seen":  "2026-04-28T10:00:00+00:00",
      "count_30d":   18,
      "count_total": 24,
      "trend": "accelerating"
    }
  ]
}
```

### 形 — 各フィールド

- `skill`: 正規化済み skill 名
- `first_seen` / `last_seen`: ISO 8601 (`+00:00` 付き UTC)。`timestamp` parse
  失敗 (空 / 不正 / naive) の event は silent skip
- `count_30d`: `now - 30d <= ts <= now` を満たす event 数 (両端 inclusive / B3)
- `count_total`: 全期間 (= hot tier 180 日 + opt-in archive) の event 数
- `trend`: enum `accelerating` / `stable` / `decelerating` / `new`

### trend 判定ロジック

```
days_since_first = (now - first_seen).days

if days_since_first < 14:
    trend = "new"          # lifecycle 浅すぎて trend 判定不可 (最優先)
else:
    observation_days = max(days_since_first, 1)
    recent_rate  = count_30d / 30
    overall_rate = count_total / observation_days
    ratio = recent_rate / overall_rate
    if   ratio > 1.5: trend = "accelerating"
    elif ratio < 0.5: trend = "decelerating"
    else:             trend = "stable"
```

`observation_days` に **上限 cap は無い**。dashboard 経路の events は 180 日
hot tier で自然 bound されるが、`--include-archive` 時に古い skill が
artificially `decelerating` 寄りに歪む実害があるため cap 撤廃 (Issue #74 / Q2)。

### sort / top-N

- sort key: `last_seen` 降順 → `skill` 昇順
- top-N: `TOP_N_SKILL_LIFECYCLE = 20` で cap

### `now` 注入

aggregator は `now: datetime | None = None` キーワード引数を受け、`None` 時
`datetime.now(timezone.utc)` 既定。test 安定化のため明示注入推奨。

## `skill_hibernating` (Issue #74, v0.7.0〜)

`~/.claude/skills/*/SKILL.md` listing と `usage.jsonl` を cross-reference し、
**install してるが最近呼ばれてない user-level skill** を surface する。
Surface ページ「Hibernating skills」panel が消費する。

### 形

```json
{
  "skill_hibernating": {
    "items": [
      {"skill": "frontend-design",     "status": "warming_up", "mtime": "2026-04-26T10:00:00+00:00", "last_seen": null,                       "days_since_last_use": null},
      {"skill": "webapp-testing",      "status": "resting",    "mtime": "2026-01-29T10:00:00+00:00", "last_seen": "2026-04-15T10:00:00+00:00", "days_since_last_use": 14},
      {"skill": "ruby-gem-security",   "status": "idle",       "mtime": "2026-02-28T10:00:00+00:00", "last_seen": "2026-04-01T10:00:00+00:00", "days_since_last_use": 28}
    ],
    "scope_note": "user-level only",
    "active_excluded_count": 5
  }
}
```

### 形 — 各フィールド

- `items[].skill`: ディレクトリ名 (= skill ID, 正規化済み skill 名と同じ)
- `items[].status`: enum `warming_up` / `resting` / `idle`
- `items[].mtime`: `<skills_dir>/<skill>/SKILL.md` のファイル mtime (ISO 8601 UTC)
- `items[].last_seen`: 該当 skill の最後の usage event timestamp (使用無しは `null`)
- `items[].days_since_last_use`: `(now - last_seen).days` (使用無しは `null`)
- `scope_note`: 文字列 `"user-level only"` 固定 (UI でユーザーへの注釈)
- `active_excluded_count`: 14 日以内に 1 度でも呼ばれて **active 除外** された
  skill 数 (= panel に表示されない skill 数)。UI で「14日以内に使われた X 件は
  非表示」注記に使う

### スコープ

- 対象: `~/.claude/skills/*/SKILL.md` (user-level skills only)
- 対象外: `~/.claude/plugins/*/skills/` (plugin-bundled は本 panel に出さない)

### Active 除外ルール

`last_seen >= now - 14d` (両端 inclusive) の skill は items に含めない。
代わりに `active_excluded_count` に 1 加算。Lifecycle panel 側で見える設計。

### Status 分類

| status | 条件 |
|--------|------|
| `warming_up` | 未使用 (`last_seen=null`) かつ `mtime >= now - 14d` (新着 install) |
| `resting`    | 使用履歴あり、`14d < days_since_last_use <= 30d` |
| `idle`       | 使用履歴あり、`days_since_last_use > 30d` ／ または 未使用かつ `mtime < now - 14d` (古い install で未使用 = 死蔵) |

### sort

第一 key: status 順 (`warming_up` → `resting` → `idle`)。各 status 内 tiebreaker:

- `warming_up`: `mtime` 降順 (= 最新 install を上に)
- `resting`: `days_since_last_use` 降順
- `idle`: `max(days_since_last_use, days_since_install)` 降順 (使用ありなら使用経過、未使用なら install 経過の長い方)

### `skills_dir` resolution / env override

優先順: 引数 `skills_dir` > 環境変数 `SKILLS_DIR` > 既定 `~/.claude/skills/`。
ディレクトリ不在 / アクセス不可 (`PermissionError` 等) のとき `{"items": [],
"scope_note": "user-level only", "active_excluded_count": 0}` を silent return
(health_alert は立てない / B4)。

### Robustness

各 entry の `is_dir() / is_file() / .stat()` は壊れた symlink で `OSError` を
投げうるため、entry 単位で `try/except OSError: continue` で wrap。1 件の
壊れた entry が panel 全体を毒さない設計。

### `now` 注入

aggregator は `now: datetime | None = None` キーワード引数を受け、`None` 時
`datetime.now(timezone.utc)` 既定。

## `session_breakdown` (Issue #99 / v0.8.0〜)

session 単位の token / cost / model 内訳 / service_tier / skill 件数 /
subagent 件数を 1 行 = 1 session の row 配列で返す。Sessions ページ
(Issue #103) が消費する。

### 形

```json
{
  "session_breakdown": [
    {
      "session_id": "abc-123-def",
      "project": "chirper",
      "started_at": "2026-05-01T10:00:00+00:00",
      "ended_at":   "2026-05-01T11:00:00+00:00",
      "duration_seconds": 3600.0,
      "models": {"claude-opus-4-7": 3, "claude-haiku-4-5": 12},
      "tokens": {
        "input": 12345, "output": 6789,
        "cache_read": 89000, "cache_creation": 1200
      },
      "estimated_cost_usd": 0.4567,
      "service_tier_breakdown": {"priority": 5, "standard": 10},
      "skill_count": 4,
      "subagent_count": 2
    }
  ]
}
```

### 集計仕様

- 入力 source: `assistant_usage` event (Issue #99 / `usage-jsonl-events.md`) +
  `session_start` / `session_end` / `skill_tool` / `user_slash_command` +
  `subagent_*` event (subagent_count 算出用)。`build_dashboard_data` は
  **`period_events_raw` 経由**で渡す (= `assistant_usage` は
  `_filter_usage_events` の対象外)
- `started_at`: `session_start.timestamp` (ISO 8601 UTC `+00:00`)。`session_start`
  を持たない orphan session は配列に含めない
- `ended_at` / `duration_seconds`: `session_end` 不在の active session では
  両方とも `null` (= UI 側「進行中」pill の trigger)
- `models`: `{model_name: assistant_usage event 数}`。1 session 内の `/model`
  切替も per-message 単位で正しく count される
- `tokens`: 4 dimension の session 内合計 (`int`)
- `estimated_cost_usd`: model 別 rate 適用 → reduce 合算 (=
  `cost_metrics.calculate_session_cost()`)。**実測 token × 価格表掛け算による
  参考値** で、価格改定で過去値も動く (`docs/reference/cost-calculation-design.md`
  §4)。4 桁丸め (USD 0.0001 = 1/100 セント)
- `service_tier_breakdown`: `{tier_name: assistant_usage event 数}`。
  `service_tier` 欠損 / null の event は **breakdown に出さない** (real value
  のみ集計、real-world data quirks をそのまま見せる)
- `skill_count`: session 内 `skill_tool` + `user_slash_command` event 数
- `subagent_count`: `subagent_metrics.session_subagent_counts(events)` 経由
  (= invocation 単位 dedup 後の count)。`aggregate_subagent_metrics` の
  type 軸合計と session 軸合計が一致する drift guard あり

### sort / top-N

- sort key: `started_at` 降順 (= 最新 session が先頭)
- top-N: `TOP_N_SESSIONS = 20` (`cost_metrics.py` 定数)
- `TOP_N` (`dashboard/server.py`、ranking 系の 10) とは別の独立定数

### 価格表 / 未知 model fallback

- 価格表 (per-1M-token rate) は `cost_metrics.MODEL_PRICING` に pin
  (出典は同 module docstring 参照 — 2026-05-06 時点
  `https://platform.claude.com/docs/en/about-claude/pricing` から verbatim)
- 未知 model は **Sonnet 4.6 fallback** で計算 (silent、UI を毒さない)
- date-suffix 付き ID (`claude-haiku-4-5-20251001` 等) は token-boundary prefix
  match で base price 解決 (`claude-opus-4` $15 と `claude-opus-4-5` $5 の 3x
  差を longest-match で取り違えない)

### period 連動 (Issue #85)

- period="7d" / "30d" / "90d" / "all" に応じて **rolling window で切られた
  events から集計**。cutoff 外の `session_start` を持つ session は
  `session_breakdown` から消える
- drift guard: `tests/test_dashboard_sessions_api.py::TestSessionBreakdown::test_session_breakdown_period_split`

### Active session の disposition

`session_end` 不在 = "進行中"。`ended_at = null` / `duration_seconds = null` で
返す。timeout (X 時間経過で自動 close 扱い等) は本仕様 scope 外、将来 issue。

### empty session の除外 (Issue #109 / v0.8.0〜)

- `assistant_usage` event を 1 件も持たない session (= 起動直後 `/exit` /
  builtin command のみで終了 / session_start 直後の abort) は
  **session_breakdown 配列から除外** する
- 除外は `aggregate_session_breakdown` 内で row pool 構築時に行うので、
  `/api/data` / `export_html` / live SSE / `build_demo_fixture.py` /
  `build_surface_fixture.py` の **すべての消費者に透過に効く**
- footer / header の `total_sessions` (= `session_stats.total_sessions`) は
  **unfilter 観測総数** であり empty session も含む。これは別経路 (raw
  events から `session_start` event を直接 count) で、本除外の影響を
  受けない
- period 適用との合成: period filter 後の `period_events` 上で
  `assistant_usage` 0 件なら除外される (= 「period 内の意味あるアクティ
  ビティ」が exclusion の単位)。session_start が pre-cutoff、in-period に
  `assistant_usage` 1 件 → 残る (`test_cross_cutoff_session_with_in_period_assistant_usage_kept`)
- drift guard: `tests/test_dashboard_sessions_api.py::TestSessionBreakdownExcludesEmpty`
  + `TestSessionBreakdownEmptyExcludeIntegration::test_session_stats_total_sessions_includes_empty`

## `model_distribution` (Issue #106 / v0.8.0〜)

Overview ページの「モデル分布」パネル用に、`assistant_usage` event の `model`
フィールドを **family rollup (opus / sonnet / haiku)** で集計した messages × cost
の二軸 distribution を返す。

### 形

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

### 集計仕様

- 入力: `period_events_raw` (= `session_breakdown` と同じ period 適用済 events)
- filter: `event_type == "assistant_usage"` のみ。`session_start` / `skill_tool` /
  `subagent_start` 等は除外
- family rollup: 各 event の `model` を `cost_metrics.infer_model_family()` で
  family 文字列に解決 (substring match `opus` → `haiku` → `sonnet` の優先順、
  未知 model は sonnet fallback)。client 側の `inferModelFamily`
  (`45_renderers_sessions.js`) と semantics を 1:1 一致
- per-event cost: `cost_metrics.calculate_message_cost(model, in, out, cr, cc)`
  と同じ rate 表 (= 価格表 drift しない)
- 各 family の `messages` / `cost_usd` を sum、`messages_pct` / `cost_pct` は
  total に対する比率 (server 側で **丸めない**、UI 側で `Math.round(pct*100)`)
- `cost_usd` / `cost_total` は 4 桁丸め (`session_breakdown.estimated_cost_usd`
  と同じ regime)

### Canonical 順 (load-bearing)

`families` 配列の順は **常に `opus → sonnet → haiku` の固定順**。cost 降順や
出現順ではない。理由:

- donut chart の slice 並び (12 時起点で時計回り) を決定論化
- 共有 legend の行順 / callout 配置 / 視覚 snapshot を決定論化
- API consumer 側が family 別 lookup を index ではなく `find(r => r.family === 'opus')`
  で書ける (= 並び順に依存しない契約)

3 軸 (server 配列順 / client donut slice 順 / legend 行順) を同期させることで
random 順による flaky snapshot を防ぐ。

### 常に 3 行 / NaN guard

family 数が 0 / 1 / 2 でも `families` は **必ず 3 行** (未出現 family は
`messages=0` / `cost_usd=0` のゼロ行で埋める)。完全空 events のときも 3 行
全 zero で返す。

`messages_total == 0` のとき `messages_pct = 0.0`、`cost_total == 0` のとき
`cost_pct = 0.0` を server 側で塞ぐ (frontend に NaN を渡さない契約)。

### 未知 model の扱い

raw model 名は output に出さない (= family のみ rollup 出力)。未知 model は
`infer_model_family` の最後の `return "sonnet"` で sonnet 行に集計、cost は
`calculate_message_cost` の `DEFAULT_PRICING` (sonnet-4-6) で推計。

### Period 連動

`period_events_raw` 経由なので period toggle (Issue #85) で Overview ページ全体と
連動 (`session_breakdown` と同じ判断)。`session_breakdown.estimated_cost_usd` の
sum と `model_distribution.cost_total` は **同じ events を別軸で集計** している
ので 4 桁内一致 (drift guard: `tests/test_model_distribution.py::TestBuildDashboardDataModelDistribution::test_session_breakdown_total_matches_model_distribution_total`)。

ただし `session_breakdown` は `top_n=20` cap、`model_distribution` は cap なし
→ 21 session 以上では `Σ row.estimated_cost_usd < model_distribution.cost_total`
で発散する。これは cap 仕様の自然な帰結 (drift guard:
`test_session_breakdown_total_diverges_from_model_distribution_above_cap`)。

### Subagent assistant_usage の包含

`source = "subagent"` の `assistant_usage` event も `model` field を持つので
集計対象に含める。subagent invocation の入れ子を別軸で集計しないという意味で
"subagent token 別 model 扱い" は別 issue 送りだが、subagent main session 内で
発火する per-message event は normal な count 対象 (drift guard:
`test_subagent_assistant_usage_included`)。
