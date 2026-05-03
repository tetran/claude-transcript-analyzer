# Subagent invocation 単位の同定とペアリング — 設計判断

`subagent_metrics.aggregate_subagent_metrics()` および
`subagent_metrics.usage_invocation_events()` が依拠する **invocation 単位
同定アルゴリズム** の設計根拠と、過去に踏み抜いた DRY trap の教訓。

event の正規 schema (`subagent_start` / `subagent_lifecycle_start` /
`subagent_stop` のフィールド形) は `docs/spec/usage-jsonl-events.md` を参照。

## なぜ正規/補助の二重観測か

Subagent 起動 hook は **`PostToolUse(Task|Agent)`** を **正規観測点** とし
`event_type: subagent_start` で記録する。`SubagentStart` hook 由来は
**補助観測** として `event_type: subagent_lifecycle_start` で別 event_type に
分けて記録する。

二重観測する理由は、**どちらか片方だけだと flaky モードで取りこぼす**から:

| 発火モード | PostToolUse 単独 | SubagentStart 単独 | 二重観測 |
|---|:---:|:---:|:---:|
| 両方発火 (通常) | ✅ | ✅ | ✅ (1 invocation に統合) |
| PostToolUse のみ (lifecycle 落ち) | ✅ | ❌ | ✅ |
| lifecycle のみ (PostToolUse 落ち) | ❌ | ✅ | ✅ |
| 起動失敗 (start fail で stop 来ない) | ✅ | — | ✅ |

count に入れる正規観測点を 1 本に固定しないと double-count するし、
かといって PostToolUse だけにすると lifecycle のみで来る invocation を
取りこぼす。**event_type を分けて両方残し、aggregator 側で invocation
同定を担う** ことで取りこぼし・二重計上のどちらも避ける。

## Invocation 同定 — `(session, type)` バケット内の timestamp 順マージ

`(session_id, subagent_type)` バケットごとに `subagent_start` と
`subagent_lifecycle_start` を **timestamp 順マージ** して invocation を
構築する。判定ルール:

```
INVOCATION_MERGE_WINDOW_SECONDS = 1.0

for each pair (start_ev, lifecycle_ev) in time-sorted bucket:
    if abs(start_ev.ts - lifecycle_ev.ts) <= 1s:
        → 同一 invocation の重複発火 (1 件にまとめる)
    else:
        → 別 invocation
```

- 1 秒以内に発火した start と lifecycle は **同一 invocation の重複扱い**
- それ以上離れていれば **別 invocation**
- これにより両方発火・lifecycle のみ・start のみ・disjoint な flaky
  パターンすべてを取りこぼさず・二重計上もせず数える

`tool_use_id ↔ agent_id` の直接紐付け手段が無いため時系列近似だが、
**同 type の並行実行は実機では稀** で v0.2 の精度要件を満たす。

## failure / duration 集計 — invocation 単位ペアリング

`subagent_metrics.aggregate_subagent_metrics()` に統一。
`(session_id, subagent_type)` でグルーピングし時系列順に start↔stop を
**invocation 単位でペアリング** したうえで:

- 各 invocation について `start.success=False OR stop.success=False` のとき
  1 failure として計上 (同 invocation の重複発火は構造的に 1 件に)
- 起動失敗 (start fail) で stop が来ないケースは「starts と stops の件数
  不一致」を見て stop プールを消費しないペアリングに切り替える
- duration は **invocation ごと** に `stop.duration_ms` (end-to-end) を優先、
  無ければ `start.duration_ms` (起動オーバーヘッド) を fallback とする
  (type 単位 fallback はバイアスになるため不採用)

## 設計教訓 — `frozenset(event_types)` フィルタは dedup を担えない

過去に `subagent_ranking` と `total_events` で件数が食い違った
(PR #12 codex review round 9-10 P2)。原因は前者が `aggregate_subagent_metrics`
の invocation 同定経由、後者が `frozenset(event_types) ⊂ {whitelist}` の
**生イベントフィルタ経由** で、後者に dedup 知識が無かった。

`frozenset(event_types)` フィルタは **「include / exclude」しか表現できず
dedup semantics を持てない**。同じイベントログから 2 つ以上の view を
出していて片方が dedup する場合、もう一方も同じ helper を経由させなければ
silent な UI inconsistency が出る。

### DRY 圧の発生源

`aggregate_*_metrics()` がドメインに既にあるなら、ヘッドライン / total 系の
メトリクスは **必ずそれ経由** で計算する。convention で揃える、ではなく
shared helper を passing through することで構造的に揃える。

ヘッドラインメトリクス (`total_events` / `daily_trend` / `project_breakdown`) も
`subagent_metrics.usage_invocation_events()` 経由で同じ invocation 同定を使い、
各 invocation の代表イベント (start を優先、無ければ lifecycle) 1 件だけを
反映する。これで lifecycle-only invocation も headline に現れ、
`subagent_ranking` と数字が必ず一致する。

### 回帰防止テスト

同一 fixture に対して `headline_count == ranking_count` を assert する
**cross-aggregator invariant test** を、dual-hook の各 flake モード
(start-only / lifecycle-only / both-merged-within-1s / both-disjoint)
について書く。per-aggregator の単体テストは pass しても cross が壊れている
ケースを catch する形。

## Pair-with-stop helper + cross-bucket attribution

start↔stop ペアリングが mismatched count (`len(starts) != len(stops)`) の時、
**naive 順次消費** は time bucket をまたぐ誤帰属を起こす:

```
2 successful starts (W1=2026-04-22, W2=2026-04-29) + 1 stop(success=False) on W2.

sequential pairing:
  start[0] (W1) ← stop[0] (W2 failed)  → W1 が failure を貰う (誤)
  start[1] (W2) ← (no stop)            → W2 に failure 無し (誤)
```

合計 failure_count は保存されるが、`(week_start, type)` トレンド表示で
**時間バケット帰属が壊れる**。Codex Round 2 (Issue #60) で発覚。

### Timestamp-window pairing で fix

`start[i]` を `[start[i].ts, start[i+1].ts)` の窓内にある未消費 stop と
ペアリング (最後の start は `[start[i].ts, +∞)`)。窓外の orphan stop は
自然に落ちる。

### 共有 helper `_pair_invocations_with_stops`

`_process_bucket()` と `_bucket_invocation_records()` の **両方** が
start↔stop ペアリングを必要とする。Round 1 では「drift guard test
(`test_failure_count_matches_metrics_failure_count`)」で 2 関数の出力一致を
固定していたが、**両方が同じバグを抱えていた** ため drift guard は何も
catch しなかった (drift guard は drift を検出するが drift を防がない)。

Refactor: `_pair_invocations_with_stops(invocations, stops)` を抽出し
`[(invocation, paired_stop_or_None), ...]` を返す single source of truth に。
両 consumer がこれを呼ぶ構造にして、drift 自体を不可能にした。

### 教訓

- **「件数保存」≠「帰属正確性」**。time bucket 集計の後では distribution
  ズレが見える。totals だけ assert する test では通り抜ける。
- **Drift guard tests は drift を検出するが防がない**。並行する 2 関数が
  「同じ入力に対して同じ derived value で合意すべき」なら、合意の shape を
  抽出する (pair-up / bucketing / predicate)。`[(item, partner_or_None), ...]`
  は pair-with-optional の典型 shape。
- **対称ヒューリスティクスの未対称見落し**: `if failed and not paired:` を
  書いたら `if succeeded and not paired:` も書く (or 起き得ない理由を証明)。
  片方だけ書くと残った分岐がバグの抜け穴になる。
- **「共通化は別 PR で」コメントは技術負債の前兆**。次の review で出るバグが
  まさにその refactor を要求する。≤30 行で済む helper extraction は
  defer しない。
- **Cross-bucket test fixture を入れる**: 週境界をまたぐ start/stop を
  test fixture に明示的に置き、bucket ごとの count を assert (totals だけ
  ではなく)。

## 代表イベントの timing semantics — start preferred, end-time trap

`usage_invocation_events()` は invocation あたり 1 件の **代表イベント** に
dedup する。選択ロジック (`subagent_metrics.py:84-85`):

```python
result.append(inv.get("start") or inv["lifecycle"])
```

**両 hook 発火時 (通常)**: 代表は `subagent_start` (PostToolUse 由来)。
**lifecycle のみ発火時 (rare)**: 代表は `subagent_lifecycle_start`。

### Timing semantic の罠 — `subagent_start.timestamp == END time`

`subagent_start` は `PostToolUse(Task|Agent)` hook で書かれる。
**PostToolUse は invocation 完了時に発火** するので、`_now_iso()` で書かれる
`timestamp` は **終了時刻**。`subagent_lifecycle_start` (SubagentStart hook
由来) のみが「真の開始時刻」。

代表イベント選択の含意:

| ケース | 代表 event_type | timestamp 意味 | 区間復元 |
|---|---|---|---|
| 両 hook 発火 (通常) | `subagent_start` | END | `[end - duration_ms/1000, end]` |
| lifecycle のみ (rare) | `subagent_lifecycle_start` | START | `[start, start + duration_ms/1000]` |

`event_type == "subagent_lifecycle_start"` を条件に start-time 処理を分岐
させると、**通常パスは never-fires**。本番データの大半は両 hook 発火 (= 終了
時刻パス) であることに留意。

### Week-bucketing は lifecycle 優先

時刻バケッティング (週境界、時刻ヒートマップ) では `lifecycle.timestamp`
の方が「開始時刻」として正しい。`_bucket_invocation_records` の優先順位は
`lifecycle or start` (Codex 5th P? で flip)。**週境界をまたぐ pair**
(Sun 23:59 lifecycle + Mon 00:00 PostToolUse) は週 N に bucket されるべきで、
`start` 優先だと週 N+1 に誤分類される。

### `subagent_lifecycle_start` は `duration_ms` を持たない

`hooks/record_subagent.py:_handle_subagent_start` (SubagentStart hook handler)
は `duration_ms` を **書かない**。`subagent_stop` のみが書く。Lifecycle-only
invocation で `ev.duration_ms` を直読みすると `None` で、interval が
`(base, base)` の点区間に潰れる — Issue #61 で長時間 subagent 中の permission
notification を取り逃した実バグ。

### Helper の使い分け

| Helper | 入力 | 出力 | 使う場面 |
|---|---|---|---|
| `subagent_invocation_interval(ev)` | rep event 単体 | `(start_ts, end_ts)` | rep event が start-with-duration を持っていることが既知の時 |
| `usage_invocation_intervals(events)` | events list | `[(start_ts, end_ts, rep_ev), ...]` | lifecycle-only invocation を含む可能性がある時 (= 通常はこちら)。stop pairing で duration を補完 |

新しい timing analytics を書く時は **lifecycle-only branch の test を別に
書く**。fixture が `duration_ms=N_000` を強制する scaffolding は本番 shape
と乖離するので、`duration_ms` を omit した regression test を 1 本足す。

### 教訓

- `PostToolUse(<tool>)` の `_now_iso()` は **終了時刻**。event_type に
  `start` が入っていても発火時刻は完了時。field 名だけでなく hook の発火
  タイミングを audit する。
- 代表イベントの選択 1 行 (`a or b`) に巨大な timing semantic が乗る。
  downstream attribution が「両 hook = ?」branch を持つ時はその branch が
  fire する fixture を意識的に作る。
- 区間 helper を 2 層に分ける: per-event interval helper (event type の
  timing 意味を解釈) + attribution function (interval を消費して帰属判定)。
  「event type の意味」と「帰属アルゴリズム」が分離されて test しやすい。

## Permission attribution — execution-window primary + backward fallback

Permission notification (event A) を「直前の何の cause (event B) が起こした
のか」温度感で帰属させる時、backward-only window や symmetric window 単体
だと **長時間 cause** を取りこぼす。

### Two-stage cover strategy

1. **Primary — execution-window cover**: 各 cause B に
   `[B.start_ts, B.end_ts]` (= duration_ms 利用) を計算。`A.ts` がこの区間内
   に入ったら帰属 (高信頼: A が B のライフタイム内に発生)。
2. **Fallback — backward window**: 区間 cover で当たらない A は
   `B.end_ts ∈ [A.ts - W, A.ts]` の B (= 直近 W 秒以内に終わった B) を探し、
   最直近を採用。
3. **Tiebreaker**: 同 tier 内では「最直近の start_ts (cover tier) / 最直近の
   end_ts (after tier)」が勝つ。「最近の cause に帰属」semantic。

### なぜ 2 段必要か

- **backward-only**: B が in-flight (B.ts は END を記録) のとき、A < B.end_ts
  だと B.ts が見えず取り逃す。長時間 subagent では致命的。
- **symmetric window** (`B.ts ∈ [A.ts - W, A.ts + W]`): B の duration < W なら
  catch するが、B.duration > W で取り逃す。さらに「abs delta 最小」基準だと
  本当に A の原因でない future B に誤帰属しうる。

cause 間に **duration の orders-of-magnitude 差** (skill_tool ~秒 vs subagent
~分) があれば execution-window が必須。

### Single-attribution invariant

「1 A → 1 B」policy を採用すると、category (skill / subagent) ごとの table
を sum して double-count しない。`sum(skill_attrib) + sum(subagent_attrib) ≤
total_A_count` の disjoint 不変条件は test 可能で、将来読者に policy を
pin する。

### Orphan は意図的に落とす

window 内に B が無い A は **orphan** として attribution しない。
`orphan_count` を観測値として記録し、ratio が高い (>30%) なら window が
狭い、低い (<5%) なら over-attribution の疑い、というチューニング指標に。
**window 定数を URL param / settings に最初から expose しない** — 観測前の
premature configurability は害が多い。

### 4 boundary fixture

test fixture には: (a) cause が notif を cover、(b) cause が window 内
直前に終了 (backward)、(c) cause が window 外 (drop)、(d) 異 tier の複数
candidate (cover が after に勝つ) の 4 ケースを入れる。

## Module responsibility boundary

本リポは **subagent 関連ロジックの owner module を type で分割** している。

| module | owns | imports / consumes |
|---|---|---|
| `subagent_metrics.py` | invocation 同定 (`(session, type)` バケット, merge window), pair-with-stop, failure rules, percentiles, weekly trend, interval semantics — 「subagent invocation 意味論」全部 | (lower-level: 他の module から import される側) |
| `dashboard/server.py` | dashboard 固有 filter, API response shape 構築, skill_tool side helper (`_skill_event_interval`) | `subagent_metrics` を import |

### 規律: cross-cutting helper は **lower module** に置く

`subagent_metrics.py` は `dashboard/server.py` と `reports/summary.py` の
**両方** に既に依存される位置。新 helper を `dashboard/server.py` 側に
書くと、後で `reports/` でも要るとなった時に migration コスト (rewrite +
import 配線) が発生する。

判断ルール: 「`reports/summary.py` がいつかこの helper を必要とする可能性
があるか?」 yes なら最初から `subagent_metrics.py` に置いて dashboard が
import する。

### Skill_tool helper の非対称配置は意図的

`_skill_event_interval()` は `dashboard/server.py` 側に残す — skill_tool は
**`subagent_metrics.py` の type scope 外**。「機能で co-locate」(permission
attribution は両 type を扱うから 1 module に統合) ではなく **「ドメイン型
で co-locate」** (subagent 側 interval は subagent_metrics、skill 側 interval
は dashboard) という規律。

将来 `reports/summary.py` で skill_tool interval が必要になれば skill helper
は別途 migrate する。subagent helper は **すでに lowest dependency 層に
いる**。

### 表面対称性で「揃える」reflex に注意

「subagent 側だけ抽出されてるのは不揃いだから skill 側も上げよう」型の
リファクタは、**skill_tool が `subagent_metrics.py` の責務範囲外** という
契約を破壊する。

ルール: shared logic は **すべての consumer の dependency 層に既にある最低位
module** に流れ落ちる。それ以上の hoisting は責務拡大であって対称性回復では
ない。

## 関連 source

| 概念 | 実装場所 |
|---|---|
| invocation 同定 | `subagent_metrics._build_invocations()` / `_process_bucket()` |
| failure / duration ペアリング | `subagent_metrics.aggregate_subagent_metrics()` |
| pair-with-stop (cross-bucket attribution) | `subagent_metrics._pair_invocations_with_stops()` |
| ヘッドライン用代表イベント | `subagent_metrics.usage_invocation_events()` |
| 区間 helper (rep event 単体) | `subagent_metrics.subagent_invocation_interval()` |
| 区間 helper (lifecycle-only 補完) | `subagent_metrics.usage_invocation_intervals()` |
| マージウィンドウ定数 | `subagent_metrics.INVOCATION_MERGE_WINDOW_SECONDS = 1.0` |
| skill 側 interval (boundary 外) | `dashboard/server.py:_skill_event_interval()` |
