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
  "project_breakdown": [...],
  "hourly_heatmap":    { ... },
  "session_stats":     { ... },
  "health_alerts":     [...]
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
