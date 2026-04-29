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

## 関連 source

| 概念 | 実装場所 |
|---|---|
| invocation 同定 | `subagent_metrics._build_invocations()` / `_process_bucket()` |
| failure / duration ペアリング | `subagent_metrics.aggregate_subagent_metrics()` |
| ヘッドライン用代表イベント | `subagent_metrics.usage_invocation_events()` |
| マージウィンドウ定数 | `subagent_metrics.INVOCATION_MERGE_WINDOW_SECONDS = 1.0` |
