# Dashboard アグリゲータ契約 — JSON 順序 / retention cap / drift-guard / period filter

`dashboard/server.py:build_dashboard_data` と `aggregate_*` 群が API consumer に対して負っている契約をまとめたリファレンス。dict iteration order を JSON contract として保つための 3 層保証、retention-aware aggregator における defensive cap の罠、capped ranking 配列と兄弟 `_total` field の drift-guard test pattern、period filter での field 分類・3-stage 包含 filter・wall-clock 注入チェーン。

サーバー側 runtime (SSE / template / IIFE 規約) は `dashboard-server.md`、フロントエンド実装 (TZ / SPA / fetch / UI label) は `dashboard-client.md` を参照。

---

## §1. Dict iteration order を JSON contract として保つ

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

## §2. Retention-aware aggregator — defensive cap が trend を歪める罠

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

---

## §3. Capped ranking 配列 + 兄弟 `_total` field と drift-guard test pattern

`aggregate_skills()` / `aggregate_subagents()` / `aggregate_projects()` は ranking 表示用に `TOP_N=10` で配列を **truncate** して返す。frontend が `data.skill_ranking.length` を「unique kinds の総数」KPI として読むと、cardinality が cap を超えるまで silent に正しく見え、production 投入後に「いくら使っても 10 で頭打ち」のバグが顕在化する (Issue #81)。

### Antipattern

```js
// ❌ NG — capped 配列の length は total ではない
ttlEl.textContent = (data.skill_ranking || []).length;
```

### 正しい設計: ranking + 兄弟 `_total`

```python
return {
    "skill_ranking": top_n_list,           # 表示用 top-N (cap)
    "skill_kinds_total": len(all_kinds),   # 兄弟 field: full count
    ...
}
```

API spec doc には capped 配列に対して **「これは display-truncated subset であって full count ではない、`<X>_total` を使え」** を必ず明記する。spec が cap だけ書いて total との分別を pin しないと、frontend が `length` を total と誤読する正規ループが再生される。

### Drift-guard test (paired aggregation 共通)

ranking と total は **同じ filter logic** (`event_type` 判定 / 空 skill 名 skip 等) を共有しているため、片側の filter を将来 PR が evolve させて他方を忘れると silent に desync する。**below-cap fixture** で `<X>_total == len(<X>_ranking)` を assert する drift-guard test が cheap insurance。

```python
def test_skill_kinds_total_matches_aggregate_skills_when_below_cap(self):
    """Drift guard: filter logic divergence between paired aggregations."""
    fixture = make_n_unique_skills(n=5)  # cap=10 に対して well below
    data = build_dashboard_data(fixture)
    self.assertEqual(data["skill_kinds_total"], len(data["skill_ranking"]))
```

ポイント:
- **below-cap が必須**: above-cap fixture では ranking が truncate されるので `total > len(ranking)` が正しく、何も pin できない
- N は cap よりだいぶ小さい数 (cap=10 に対して 5 など) で、cap 値変更にも壊れない headroom を残す
- test 名は `test_<new_field>_matches_<existing>_when_below_cap` で grep 可能に
- docstring に **「drift guard」** を書く — cap-test との重複と誤解されて削除されないように

### 適用範囲

- **paired aggregation** (skill / subagent / project) には **対称的に** 適用する。半分だけ guard すると未 guard の dimension で drift が起きる
- 一般化: 「同じデータの second view を追加したとき」 (uncapped count next to top-N、daily total next to weekly、unique-by-X next to all-by-X、fast path next to slow path、cached next to recomputed 等) は below-cap の overlapping subset で structural equivalence を assert する
- **例外**: 新 field の logic が **意図的に異なる** (異 filter / 異 dedup) なら drift guard は legitimate divergence をブロックするので **書かない**

### `top_n=10**9` 経由で cap 回避は反対

`aggregate_skills(events, top_n=10**9)` で「cap-bypass = total」を取る案は single-responsibility 違反。`10**9` を「infinity sentinel」として乱用しない。**fresh set 構築で cap-bypass concern を分離** する (Issue #81 plan で reject 済)。

---

## §4. Period filter — field 分類・3-stage 包含 filter・wall-clock 注入

`/api/data` に period toggle (例: 7d / 30d / all) を入れるとき、`build_dashboard_data` の ~22 field を **「filter していい / だめ / N/A」の3群に分類** してから入れる (Issue #85)。pre-filter and hope は silent under-counting を生む。

### 22-field 分類マップ

| 群 | field | 判定基準 |
|---|---|---|
| **Period-applicable (12)** — KPI / Overview / Patterns / Sessions | `total_events`, `skill_kinds_total`, `subagent_kinds_total`, `project_total` (KPI) / `skill_ranking`, `subagent_ranking`, `daily_trend`, `project_breakdown` (Overview) / `hourly_heatmap`, `skill_cooccurrence`, `project_skill_matrix` (Patterns) / `session_stats` (Sessions, Issue #114) | usage event の集約で、窓を狭めても意味が壊れない |
| **Full-period (7)** — Quality / Surface | `subagent_failure_trend` (週バケッ: 7d だと 1 bucket になり trend として無意味) / `permission_prompt_skill_breakdown`, `permission_prompt_subagent_breakdown`, `compact_density` (Quality) / `skill_invocation_breakdown`, `skill_lifecycle` (`first_seen`/`last_seen` は lifetime 必須), `skill_hibernating` (Surface) | 観測窓不変。filter すると意味が壊れる |
| **Filtering-N/A (3)** — metadata | `last_updated`, `health_alerts`, `period_applied` (echo 用追加 field) | response metadata、filter とは独立 |

判定ルール: **集計の cardinal 単位が period より粗いものは full-period 群**。週バケッを 7d 窓で出すと 1 bucket だけで trend 表示が壊れる、が典型。

### Two-flavor 規約: `_raw` と `_usage` を別変数に

`dashboard/server.py:884` の既存 convention は `events` (raw) + `usage_events = _filter_usage_events(events)` の 2 系統。period filter は **両方とも narrow** する:

```python
period_events_raw = _filter_events_by_period(events, period, now=now)
period_events_usage = _filter_usage_events(period_events_raw)
```

`_raw` を読む aggregator は `period_events_raw`、`_usage` を読む aggregator は `period_events_usage`。**変数名を spec のように扱う** (plan doc だけでなく function body で grep 可能に: `period_events_raw` / `period_events_usage` をそれぞれ呼ぶ aggregator が 1 行で見える)。

### KPI と panel を上下整合させる

KPI tile (`*_total`) と直下の panel (sparkline / heatmap) の period 群を **必ず揃える**。panel が period-applicable で KPI が full-period だと UX が自己矛盾する。

### `period_applied` echo field

response に `period_applied` を加えて frontend badge (`"7d 集計"` 等) と server-side fallback 通知 (invalid input → 何が effective だったか) の両方に使う。

### Rolling vs calendar window

cutoff には `now - timedelta(days=N)` (rolling) を使う。既存 `cutoff_30d = now - timedelta(days=30)` (`aggregate_skill_lifecycle` 内) との整合が取れる。**calendar window** (local-TZ 真夜中境界) は理論的には sparkline の boundary が綺麗だが、server が client TZ を確実に知る経路が無いので採用しない。

### 3-stage inclusive filter — paired event の境界取りこぼし防止

`subagent_start ↔ subagent_lifecycle_start` (`INVOCATION_MERGE_WINDOW_SECONDS = 1.0` 内) や `subagent_start ↔ subagent_stop` (`start_ts ≤ stop_ts < next_start_ts` semantics) のような pair-windowed event は、**naive timestamp filter だけ** では片側が cutoff の外側に落ちて pair が壊れ、`_build_invocations` が lifecycle-only invocation と誤判定して `failure_rate` / `avg_duration_ms` / kinds-total が silent に drift する。

```
Stage 1: timestamp filter (cutoff <= ts <= now)
Stage 2: start↔lifecycle pair re-include — 同 (session_id, type) bucket の sibling を
         INVOCATION_MERGE_WINDOW_SECONDS=1.0 以内なら Stage 1 で落ちていても再包含
Stage 3: start↔stop pair re-include — bidirectional
         (kept start → next paired stop / kept stop → directly preceding paired start)
         start_ts ≤ stop_ts AND no other start in between
         (= subagent_metrics._pair_invocations_with_stops semantics)
```

#### Boundary fixture が必須

```
start_A @ now-7d-2.0s   (cutoff 外、Stage 1 で drop)
stop_A  @ now-7d-1.5s   (cutoff 外、Stage 1 で drop)
start_B @ now-7d+0.3s   (Stage 1 keep)
stop_B  @ now-7d+0.8s   (Stage 1 keep)
```

Stage 3 が `stop_A` を `start_B` に **誤帰属させない** (it belongs to invocation A) を assert。`stop_A` は `start_A` も Stage 1 で kept だった場合だけ pull-back される。包含の test は **対称に**: `test_filter_period_includes_subagent_stop_paired_with_kept_start` AND `test_filter_period_includes_subagent_start_paired_with_kept_stop` の両方向。

#### Mirror 警告 — `_pair_invocations_with_stops` を canonical に

`_filter_events_by_period` の Stage 3 は `subagent_metrics._pair_invocations_with_stops` の semantics を mirror している。**両側に `# Mirrors X. Keep in sync.` コメント** を入れて bidirectional grep で発見できるようにする。Mirror 実装は boundary case で必ず drift するので、可能な範囲で **canonical helper を直接 import して再利用する** (`_pair_invocations_with_stops` 自体を mirror から呼ぶ)。詳細 lesson は `subagent-invocation-pairing.md`「Pair-with-stop helper §教訓」(Issue #85 で codex review 5 round で 6 件の boundary-case drift を再発見した実績)。

### Wall-clock 注入チェーン — drift-guard test の deterministic 化

drift-guard test (例: `build_dashboard_data(events, period="all") == build_dashboard_data(events, period="7d")[period-applicable-fields]`) を 2 回連続で呼ぶと、`datetime.now()` を内部で読む aggregator が μs 単位で違う wall-clock を見て、`last_seen` が 「29 days, 23:59:59」 → 「30 days, 00:00:00」 で trend label が flip する day-boundary flake が出る。

**stdlib-only な freezegun 等価**: `now: Optional[datetime] = None` keyword を `build_dashboard_data` に追加し、chain 全体に伝播:

```
build_dashboard_data(events, period, now=fixed_now)
  ├─ _filter_events_by_period(..., now=now)
  ├─ aggregate_skill_lifecycle(..., now=now)        # cutoff_30d/14d, trend bucketing
  ├─ aggregate_skill_hibernating(..., now=now)      # active_cutoff, days_since_use
  └─ last_updated = now.isoformat() if now else _now_iso()
```

production 経路 (`_serve_api()`) は `now=None` のまま real-time 動作 (`None`-then-fallback)。test だけが固定 `now` を渡す。

#### Audit checklist

新 path を `build_dashboard_data` に通す前に必ず:

1. `grep -nE 'datetime\.now|time\.time|time\.monotonic' dashboard/` で wall-clock site を全列挙
2. それぞれが `now=` keyword を受けるか確認、受けないものを extend
3. **chain の途中で止めない**: top-level だけ受けても aggregator まで伝播しないと false confidence (test seems deterministic but flakes intermittently)

#### 残余 wall-clock gap の document

`subagent_metrics.py` 内部の `datetime.now()` は plumb されていない (= ungated)。これは intentional な gap: 該当 field (`subagent_failure_trend` 等) は **full-period 群** なので、`period="all"` と `period="7d"` 両方とも同じ wall-clock instant (μs 内) を見て同 bucket に入る → equality は成立する。**deliberate な gap を doc に残す** ことで future maintainer が「why isn't this plumbed?」で迷わない。

#### `last_updated` も `now=` 経由

`last_updated = now.isoformat()` を `now=` 注入下に置かないと、drift-guard test が `del result["last_updated"]` で 1 field 削ってから equal を比較する hack になる。**full-equality の structural property** を保つには response metadata も同じ `now=` を読ませる。

