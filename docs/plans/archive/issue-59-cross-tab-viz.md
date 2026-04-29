# Issue #59 Plan — Cross-tab viz (B1: Skill 共起 + B2: Project × Skill heatmap)

## 📋 plan-reviewer 反映ログ

| Proposal | 内容 | 反映箇所 |
|---|---|---|
| P1 | B1 `count` 単位を「session 数」と明文化、tooltip / table header / help-pop / docstring すべて統一 | 集計仕様 / Schema 例 / docstring / DOM / renderer / tooltip / test |
| P2 | B2 schema に `covered_count` / `total_count` を additive 追加、sub label にカバー率初版から表示 | Schema 例 / docstring / renderer sub / test / DoD |
| P3 | B1 sort を `sorted(items, key=(-count, pair))` で明示、逆順入力 test を具体化 | 集計仕様 / Phase 1 GREEN / test |
| P4 | B2 非正方 (3×7) test 追加で dimension invariant を pin | test |
| P5 | 両 renderer の page-scoped early-out 存在を grep test で pin | template test |
| P6 | PR splitting trigger を「往復回数」→「schema/単位/other 採用 1 回」へ変更 | Phase 8 splitting condition |
| Q2 | Patterns 縦長閾値「overview を超えたら sub-tab 化検討」を申し送りに追加 | リスク表 |

Q1 (count 単位 session vs invocation) は P1 で session 単位採用 + 将来 additive
で `skill_cooccurrence_invocations` を増やす逃げ道 pin で解決。Q3 (top_n_share の
責任分界) は P2 で server 側計算採用で解決。

### 二次レビュー反映 (2nd round)

| 二次 Proposal | 内容 | 反映箇所 |
|---|---|---|
| 2-P1 | Phase 5 smoke checklist の tooltip 文言を `co-occurrences` → `sessions` に統一 | Phase 5 smoke step |
| 2-P2 | B2 help-pop body にカバー率案内を追記 | DOM 雛形 |
| 2-P3 | page-scoped early-out test の grep window を 400 chars に絞る (locality 強化) | template test |
| 2-Q1 | skill 系 inline filter の定数化 trigger 閾値を申し送り | 後続 PR への申し送り |
| 2-Q2 | subagent only events で `total_count=0` になる契約を test で pin | server unit test |
| 2-Q3 | aggregator purity (再呼出し等価 + events 非破壊) を test で pin | server unit test |

## 🎯 Goal

skill / project の総量だけでは見えない **組合せパターン** を可視化する。Issue #58
で実装した時間帯ヒートマップが「いつ」の軸を埋めたのに対し、本 issue は「**何と
何が一緒に**」「**どの project でどの skill が**」という cross 軸を Patterns
ページに追加する。

- **B1. Skill 共起マトリクス**: 同一 session 内で一緒に使われた skill の pair
  を集計 → workflow 化のヒント
- **B2. Project × Skill heatmap**: project ごとの skill 利用偏り → 共通化 /
  プロジェクト固有カスタマイズの判断材料

両 widget とも Patterns ページ (`<section data-page="patterns">`) の **hourly
heatmap panel の後ろ** に追加配置し、`#58` で残された placeholder
(`<p class="placeholder-body"> ... #59 ... </p>`) を **削除** する。

## 📐 機能要件 / 構造設計

### B1. Skill 共起マトリクス

#### 集計仕様

- **入力**: 全 events の中から `skill_tool` / `user_slash_command` のみ抽出
  (= 既存 `aggregate_skills` と同じ filter 慣習)。**subagent は対象外**
  (issue 本文 "B1: usage 系 event のみ対象 (subagent は別)" を厳格採用)
- **session 単位 unique skill 集合**: `session_id` ごとにグルーピングし、
  `{ev.skill for ev in group if ev.skill}` で unique 化
- **pair 列挙**: `itertools.combinations(sorted(skills), 2)` で全 pair を生成。
  これにより `(a, b)` は常に `a < b` で正規化され、self-pair (a == a) は
  combinations の性質上 自然に除外される
- **全 session 合算**: `Counter` で pair → 出現 session 数 を蓄積
- **count の単位 (重要 / Proposal 1 反映)**: `count` は **「両 skill が両方登場した
  session 数」**。同 session 内で同じ pair が複数回トリガされても 1 として数える
  (unique 化の必然)。**invocation 単位 (回数) ではない**。tooltip / sub label / spec
  doc / docstring すべてで `sessions` 単位を明示する
- **明示 sort (Proposal 3 反映)**: `Counter.most_common(top_n)` ではなく
  `sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]` を使う。
  `most_common` は同 count 内で insertion order を保つだけで lexicographic 順
  は保証されないため、明示 sort で順序を pin する
- **top N**: `top_n=100` で切る (issue 本文の cap)

#### 計算量と現実的なサイズ感

- 1 session 内の unique skill が `k` のとき pair 数は `C(k, 2) = k(k-1)/2`
- issue 本文の試算 "30 unique skill = 435 pair" が **1 session の上限近似**
- 実際の usage.jsonl は 1 session で 5〜10 unique skill が中心 → 10〜45 pair
- session 数 × 平均 unique skill² で爆発しうるが、Counter のメモリは
  `O(distinct pairs)` で抑えられ、`most_common(100)` が `O(M log K)` (M=distinct
  pairs, K=top N)。180 日 hot tier なら distinct pair が 10K オーダーになっても
  問題なし

#### Schema 例

```json
{
  "skill_cooccurrence": [
    {"pair": ["frontend-design", "webapp-testing"], "count": 12},
    {"pair": ["codex-review", "verify-bot-review"], "count": 9}
  ]
}
```

- `count` の単位は **両 skill が両方登場した session 数** (= unique
  `session_id` の数)。invocation 数ではない (Proposal 1)
- 配列要素は `count` 降順、同 count 内では `pair[0]`, `pair[1]` の lexicographic
  昇順 (明示 sort で順序を pin)
- 空入力 (events なし / pair なし) なら `[]`
- pair の中身は **常にソート済み** (`pair[0] <= pair[1]`)。browser 側は順序を
  仮定して OK
- **将来の単位拡張**: invocation 単位の共起が必要になったら additive で
  `skill_cooccurrence_invocations` field を新設する (本 PR は session 単位 only)。
  field 名に単位を含めない (`skill_cooccurrence`) 判断は YAGNI 採用 — 現状
  workflow 発見目的では session 単位で十分

#### 関数 signature

```python
def aggregate_skill_cooccurrence(
    events: list[dict],
    top_n: int = 100,
) -> list[dict]:
    """同一 session 内の skill pair を集計し top_n 件返す (Issue #59 / B1)。

    入力 events は **未 filter** (build_dashboard_data からは raw events を渡す)。
    内部で `skill_tool` / `user_slash_command` のみに絞り、subagent は除外。
    aggregate_skills と同じ filter 慣習。

    挙動:
      - session_id ごとに skill 名を unique 集合化 (空 session_id は skip)
      - 各 session の skill 集合に対して itertools.combinations で 2-pair 列挙
      - Counter で全 session 合算
      - count 降順 + pair lexicographic 昇順で **明示 sort**
        (`sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]`)
        → `most_common` の暗黙順序 (insertion order 依存) を避ける
      - top_n で切る (default 100)

    出力 list[{"pair": [a, b], "count": N}]
      - pair は a <= b で正規化済み
      - **count は session 数** (両 skill が両方登場した unique session_id 数)。
        同 session 内の重複呼び出しは 1 回扱い
    """
```

#### B1 の filter 慣習論点 (recommendation)

**recommendation: `aggregate_skills` と完全に同じ filter (skill_tool +
user_slash_command, subagent 除外)**

**condition for changing**: subagent ↔ skill の共起 (例: 「Explore subagent と
codex-review skill が同じ session で共に使われている」) を見たくなったら、別
field `subagent_skill_cooccurrence` を additive で追加する。本 issue 本文の
Out of Scope に "subagent との共起" が明記されているので本 PR では扱わない。

### B2. Project × Skill heatmap

#### 集計仕様

- **入力**: 全 events の中から `skill_tool` / `user_slash_command` のみ抽出
  (B1 と同じ filter)
- **project 軸**: count 降順 top 10。それ以外は **drop** (`"other" 集約は不採用`、
  下記議論)
- **skill 軸**: count 降順 top 10。それ以外は drop
- **counts**: 2D dense matrix (10 × 10 max)。`counts[i][j]` = project[i] × skill[j]
  の event count。ゼロも含めて **dense** で出す (heatmap 描画に dense が自然)

#### "other" 集約の採否 (議論ポイント)

**recommendation: "other" 集約は採用しない (本 PR では top 10 × top 10 sparse 100 cells のみ)**

trade-off:

| 案 | pros | cons |
|---|---|---|
| (a) drop (本 PR 採用) | 実装最小、heatmap が読みやすい、他 widget (skill_ranking / project_breakdown) との整合 (どちらも top 10 で打ち切りで "other" 集約しない) | top 10 から漏れた skill / project の存在感が消える |
| (b) "other" 行/列 を追加 | truncate された usage 量が見える | dense matrix のサイズが 11×11 になり、"other" の中身ドリルダウンを期待されると応答できない |

**condition for switching to (b)**: 実機確認時に「top 10 × top 10 で全体の何 %
カバーされているか」が判らない不便を感じたら、`top_n_share` (= top 10 が全体
events 中で占める %) を sub label に出すだけで OK。"other" 行/列を足すまでも
ない。判断は実機 smoke 後。

**recommendation 補強**: 既存 `project_breakdown` / `skill_ranking` も top 10
打ち切りで "other" を出していない (sub label に "10 projects · Σ N" と出すだけ)。
本 widget も同じ慣習に揃えるのが UI consistency 観点で正しい。

**カバー率 sub label を初版から出す (Proposal 2 反映)**:
"other" を出さない代わりに **`covered_count` / `total_count` を schema に出し、
sub label に top 10×10 が全体の何 % をカバーしているかを表示する**。これにより
「top 漏れがどれだけあるか」が `data` 上から判断でき、`condition for switching
to (b)` で挙げた懸念を初版で解決できる。aggregator は project / skill 別合計
Counter を **既に内部で持つ** (top 10 抽出に必要) ので、合計値を返り値に含める
だけで実装コストは僅少。

#### Schema 例

```json
{
  "project_skill_matrix": {
    "projects": ["chirper", "claude-transcript-analyzer", "..."],
    "skills":   ["frontend-design", "codex-review", "..."],
    "counts": [
      [12, 3, 0, 8, ...],
      [5, 0, 7, 2, ...],
      ...
    ],
    "covered_count": 234,
    "total_count": 312
  }
}
```

- `projects` / `skills`: それぞれ **count 降順** で並んだ name 配列。max 10 件
- `counts`: rows = `projects`, cols = `skills` の 2D int array。
  `len(counts) == len(projects)` / `len(counts[i]) == len(skills)` を invariant
  として保証
- `covered_count`: matrix に乗っている events 数 (= `sum(sum(row) for row in counts)`)。
  Proposal 2 反映で **本 PR から出す**
- `total_count`: filter 後 (skill_tool + user_slash_command) の全体 events 数 (top
  漏れ含む)。`covered_count <= total_count`
- 空入力なら `{"projects": [], "skills": [], "counts": [], "covered_count": 0, "total_count": 0}`
- top 10 軸より少ない場合 (例: project が 3 種類しかない) は短い配列のまま返す
  (例: 3×N matrix)
- カバー率 = `covered_count / total_count` (`total_count > 0` のとき)。browser 側で
  パーセント整数化して sub label 表示

#### 関数 signature

```python
def aggregate_project_skill_matrix(
    events: list[dict],
    top_projects: int = TOP_N,
    top_skills: int = TOP_N,
) -> dict:
    """project × skill の dense 2D matrix を返す (Issue #59 / B2)。

    入力 events は **未 filter** (build_dashboard_data からは raw events を渡す)。
    内部で `skill_tool` / `user_slash_command` のみに絞る。

    挙動:
      - skill_tool / user_slash_command のみ対象、空 project / 空 skill の event は skip
      - project / skill それぞれ count 降順で top_projects / top_skills に切る
      - 残った (project, skill) ペアの count を 2D matrix で組み立てる (cell 0 含む)
      - "other" 集約は採用しない (top 漏れは drop) が、`covered_count` /
        `total_count` を返してカバー率の可視化を可能にする (Proposal 2)

    出力: {
      "projects": [...],         # count 降順
      "skills": [...],           # count 降順
      "counts": [[int]],         # rows=projects, cols=skills (dense)
      "covered_count": int,      # matrix に乗っている events 数
      "total_count": int,        # filter 後の全体 events 数 (top 漏れ含む)
    }
    """
```

### `_filter_usage_events` を使わない理由

両 aggregator とも `aggregate_skills` と同じく **subagent を除外** したいので、
`_filter_usage_events()` (subagent invocation を含む) ではなく
`event_type in ("skill_tool", "user_slash_command")` の inline filter を使う。
これは既存 `aggregate_skills` と完全に同一パターン:

```python
for ev in events:
    et = ev.get("event_type")
    if et not in ("skill_tool", "user_slash_command"):
        continue
    skill = ev.get("skill", "")
    if not skill:
        continue
    ...
```

`build_dashboard_data` から見ると以下のように整理される:

| aggregator | input | filter |
|---|---|---|
| `aggregate_skills` | `events` (raw) | inline: skill_tool + user_slash_command |
| `aggregate_subagents` | `events` (raw) | inline (subagent_metrics 経由) |
| `aggregate_daily` | `usage_events` (filtered) | usage 系 + subagent invocation |
| `aggregate_projects` | `usage_events` (filtered) | usage 系 + subagent invocation |
| `aggregate_hourly_heatmap` | `usage_events` (filtered) | usage 系 + subagent invocation |
| **`aggregate_skill_cooccurrence`** (新) | `events` (raw) | inline: skill_tool + user_slash_command |
| **`aggregate_project_skill_matrix`** (新) | `events` (raw) | inline: skill_tool + user_slash_command |

「skill 軸を持つ widget は raw events + inline filter / project 全体の量を扱う
widget は usage_events filter」という整理が自然に成立する。

## 🏛 DOM / CSS / JS 設計

### Patterns section の新 DOM 構造

`#58` で残した placeholder `<p class="placeholder-body">...#59...</p>` を
**削除**し、その位置に B1 / B2 の panel を 2 個追加する。

```html
<section data-page="patterns" class="page" aria-labelledby="page-patterns-title" hidden>
  <header class="header">
    <div>
      <h1 id="page-patterns-title"><span class="accent">Patterns</span></h1>
      <p class="lede">利用パターンを可視化します。</p>
    </div>
  </header>

  <!-- (1) hourly heatmap (#58 で実装済み・既存) -->
  <div class="panel" id="patterns-heatmap-panel">...</div>

  <!-- (2) Skill 共起 (#59 / B1 新規) -->
  <div class="panel" id="patterns-cooccurrence-panel">
    <div class="panel-head c-coral">
      <div class="ttl-wrap">
        <span class="ttl"><span class="dot"></span>スキル共起マトリクス</span>
        <span class="help-host">
          <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-cooc" data-help-id="hp-cooc">?</button>
          <span class="help-pop" id="hp-cooc" role="tooltip" data-place="right">
            <span class="pop-ttl">スキル共起</span>
            <span class="pop-body">同じセッション内で一緒に使われた skill / slash command のペアを集計。<strong>カウント単位はセッション数</strong> (同一セッション内の重複呼び出しは 1 とみなす)。subagent は対象外。多い順に上位 100 ペアを表示。</span>
          </span>
        </span>
      </div>
      <span class="sub" id="patterns-cooccurrence-sub"></span>
    </div>
    <div class="panel-body">
      <table class="cooc-table" id="patterns-cooccurrence">
        <thead>
          <tr><th>Skill A</th><th>Skill B</th><th class="num">Sessions</th></tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <!-- (3) Project × Skill heatmap (#59 / B2 新規) -->
  <div class="panel" id="patterns-projskill-panel">
    <div class="panel-head c-peach">
      <div class="ttl-wrap">
        <span class="ttl"><span class="dot"></span>プロジェクト × スキル</span>
        <span class="help-host">
          <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-projskill" data-help-id="hp-projskill">?</button>
          <span class="help-pop" id="hp-projskill" role="tooltip" data-place="right">
            <span class="pop-ttl">プロジェクト × スキル</span>
            <span class="pop-body">プロジェクト (上位 10) × スキル (上位 10) のクロス利用件数。色が濃いほど件数が多い。subagent は対象外。上位漏れは表示しないが、サブラベルに上位 10×10 の<strong>カバー率</strong>を表示。</span>
          </span>
        </span>
      </div>
      <span class="sub" id="patterns-projskill-sub"></span>
    </div>
    <div class="panel-body">
      <div class="projskill" id="patterns-projskill" role="img" aria-label="プロジェクト × スキル ヒートマップ"></div>
      <div class="projskill-legend" id="patterns-projskill-legend"></div>
    </div>
  </div>
</section>
```

#### 色 stripe pin (panel-head と data-tip border-left)

| widget | panel-head class | data-tip stripe | 衝突確認 |
|---|---|---|---|
| hourly heatmap (#58 既存) | `c-peri` | `--mint` (`data-kind="heatmap"`) | — |
| **B1 cooccurrence (新)** | `c-coral` | `--coral` (`data-kind="cooc"`) | rank-subagent も coral だが kind が違うため OK |
| **B2 project × skill (新)** | `c-peach` | `--peach` (`data-kind="projskill"`) | proj (project_breakdown) も peach。**意図的に揃える** (project 系は peach の慣習) |

panel-head 色は **3 panel が並ぶ視覚的な差別化** が目的なので、coral / peri /
peach の 3 色で chrome を分ける。data-tip stripe は既存ボキャブラリと整合させ
る（B2 は project 系なので peach、B1 は新カテゴリなので coral）。

### CSS 設計

template.html の `<style>` 内、`/* hourly heatmap (Issue #58) */` セクション
直後に追加。

#### B1 cooccurrence table

```css
/* skill cooccurrence (Issue #59 / B1) */
.cooc-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  font-family: var(--ff-mono);
}
.cooc-table th {
  text-align: left;
  color: var(--ink-faint);
  font-weight: 500;
  padding: 6px 8px;
  border-bottom: 1px solid var(--line);
}
.cooc-table th.num,
.cooc-table td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.cooc-table tbody tr {
  border-bottom: 1px solid var(--line-faint, rgba(255,255,255,0.04));
}
.cooc-table tbody tr:hover {
  background: var(--bg-panel-2);
}
.cooc-table td {
  padding: 5px 8px;
  color: var(--ink);
}
.cooc-table td.skill { color: var(--mint); }
.cooc-table .empty {
  text-align: center;
  color: var(--ink-faint);
  padding: 24px 0;
}
.data-tip[data-kind="cooc"] { border-left-color: var(--coral); }
```

table 全体に **scroll 制限はかけない**（CSS overflow なし）。 100 行は固定列幅で
そのまま縦長に出る。これは既存 `daily_trend` / `project_breakdown` も縦長を
許容している慣習。

#### B2 project × skill heatmap

```css
/* project x skill heatmap (Issue #59 / B2) */
.projskill {
  display: grid;
  /* row-label 列 + cell 1fr × N skill。動的に grid-template-columns を JS から設定 */
  gap: 2px;
  font-size: 10.5px;
  margin-top: 4px;
  overflow-x: auto;  /* skill 名が長いと横スクロール許容 */
}
.projskill-col-axis {
  display: contents;
}
.projskill-col-axis > span {
  color: var(--ink-faint);
  font-family: var(--ff-mono);
  text-align: center;
  padding-bottom: 4px;
  white-space: nowrap;
  /* skill 名が長すぎる場合の縦書きは採用しない (実装複雑度 vs 価値で見送り)。
     代わりに max-width + ellipsis */
  max-width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.projskill-row-label {
  color: var(--ink-soft);
  font-family: var(--ff-mono);
  text-align: right;
  padding-right: 6px;
  align-self: center;
  white-space: nowrap;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.projskill-cell {
  aspect-ratio: 1;
  min-height: 22px;
  border-radius: 2px;
  border: 1px solid var(--line);
  background: transparent;
  cursor: default;
}
.projskill-cell[data-c="0"] { background: transparent; }
/* count > 0 は inline style で peach 系の rgba 塗り */

.projskill-legend {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  font-size: 10.5px;
  color: var(--ink-faint);
  font-family: var(--ff-mono);
}
.projskill-legend-bar {
  flex: 0 0 200px;
  height: 8px;
  border-radius: var(--r-sm);
  background: linear-gradient(to right, rgba(255,201,122,0.05), rgba(255,201,122,1));
  border: 1px solid var(--line);
}

.data-tip[data-kind="projskill"] { border-left-color: var(--peach); }
```

**color rationale**: B2 の cell 色は `rgba(255, 201, 122, alpha)` (peach の RGB)。
hourly heatmap が mint 系 `rgba(111, 227, 200, alpha)` なので、視覚的に区別される。

### JS renderer (template.html `<script>` 内)

`loadAndRender()` の末尾、既存 `renderHourlyHeatmap(data.hourly_heatmap);` の
**直後** に 2 行追加:

```javascript
// ---- skill cooccurrence (Issue #59 / B1) ----
renderSkillCooccurrence(data.skill_cooccurrence);
// ---- project × skill heatmap (Issue #59 / B2) ----
renderProjectSkillMatrix(data.project_skill_matrix);
```

renderer は `renderHourlyHeatmap` の関数定義の **直後** に並べる (近傍配置で
読みやすさ重視)。

#### B1 renderer

```javascript
function renderSkillCooccurrence(items) {
  // page-scoped early-out (Patterns 非表示中は描画スキップ / Issue #58 規範)
  if (document.body.dataset.activePage !== 'patterns') return;
  const tbody = document.querySelector('#patterns-cooccurrence tbody');
  if (!tbody) return;
  const list = Array.isArray(items) ? items : [];
  if (list.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" class="empty">共起データなし</td></tr>';
  } else {
    tbody.innerHTML = list.map((it) => {
      const a = (it.pair && it.pair[0]) || '';
      const b = (it.pair && it.pair[1]) || '';
      const c = it.count || 0;
      const al = a + ' ⨉ ' + b + ': ' + c + ' sessions';
      return '<tr data-tip="cooc" data-a="' + esc(a) + '" data-b="' + esc(b) + '"' +
        ' data-c="' + c + '" tabindex="0" role="row" aria-label="' + esc(al) + '">' +
        '<td class="skill">' + esc(a) + '</td>' +
        '<td class="skill">' + esc(b) + '</td>' +
        '<td class="num">' + fmtN(c) + '</td>' +
        '</tr>';
    }).join('');
  }
  const sub = document.getElementById('patterns-cooccurrence-sub');
  if (sub) {
    sub.textContent = list.length + ' pairs (top 100)';
  }
}
```

#### B2 renderer

```javascript
function renderProjectSkillMatrix(payload) {
  if (document.body.dataset.activePage !== 'patterns') return;
  const root = document.getElementById('patterns-projskill');
  if (!root) return;
  const projects = (payload && Array.isArray(payload.projects)) ? payload.projects : [];
  const skills = (payload && Array.isArray(payload.skills)) ? payload.skills : [];
  const counts = (payload && Array.isArray(payload.counts)) ? payload.counts : [];

  if (projects.length === 0 || skills.length === 0) {
    root.innerHTML = '<div class="empty" style="padding:24px;text-align:center;color:var(--ink-faint)">データなし</div>';
    const lg = document.getElementById('patterns-projskill-legend');
    if (lg) lg.innerHTML = '';
    const sb = document.getElementById('patterns-projskill-sub');
    if (sb) sb.textContent = '';
    return;
  }

  let max = 0;
  for (const row of counts) for (const c of row) if (c > max) max = c;

  // dynamic grid-template-columns: row label 列 + N skill 列
  root.style.gridTemplateColumns = '140px repeat(' + skills.length + ', minmax(40px, 1fr))';

  let html = '<div class="projskill-col-axis"><span></span>';
  for (const s of skills) html += '<span title="' + esc(s) + '">' + esc(s) + '</span>';
  html += '</div>';
  for (let i = 0; i < projects.length; i++) {
    const p = projects[i];
    html += '<div class="projskill-row-label" title="' + esc(p) + '">' + esc(p) + '</div>';
    const row = counts[i] || [];
    for (let j = 0; j < skills.length; j++) {
      const c = row[j] || 0;
      const intensity = max ? c / max : 0;
      const bg = c
        ? 'background: rgba(255, 201, 122, ' + (0.08 + intensity * 0.92).toFixed(3) + ')'
        : '';
      const al = p + ' × ' + skills[j] + ': ' + c + ' events';
      html += '<div class="projskill-cell" style="' + bg + '"' +
        ' data-tip="projskill" data-p="' + esc(p) + '" data-s="' + esc(skills[j]) +
        '" data-c="' + c + '" tabindex="0" role="img" aria-label="' + esc(al) + '"></div>';
    }
  }
  root.innerHTML = html;

  const legend = document.getElementById('patterns-projskill-legend');
  if (legend) {
    legend.innerHTML =
      '<span>0</span><span class="projskill-legend-bar" aria-hidden="true"></span>' +
      '<span>peak ' + fmtN(max) + '</span>';
  }
  const sub = document.getElementById('patterns-projskill-sub');
  if (sub) {
    const covered = (payload && payload.covered_count) || 0;
    const total = (payload && payload.total_count) || 0;
    let s = projects.length + ' projects × ' + skills.length + ' skills';
    if (total > 0) {
      const pct = Math.round((covered / total) * 100);
      s += ' · ' + pct + '% covered (' + fmtN(covered) + '/' + fmtN(total) + ')';
    }
    sub.textContent = s;
  }
}
```

#### Page-scoped early-out + hashchange 連携

`#58` で導入済みの `body[data-active-page]` 判定 + main IIFE の hashchange
listener が **そのまま機能する** ので、本 PR で追加実装不要。両 renderer に
`if (document.body.dataset.activePage !== 'patterns') return;` を入れるだけ。

### tooltip 拡張 (`dtipBuild()` 分岐 2 件追加)

template.html:1374 `dtipBuild(el)` 内、既存 `kind === 'heatmap'` 分岐の **後**
に追加:

```javascript
if (kind === 'cooc') {
  const a = el.getAttribute('data-a') || '';
  const b = el.getAttribute('data-b') || '';
  const c = el.getAttribute('data-c') || '0';
  return {
    kind: 'cooc',
    html: '<span class="ttl">' + esc(a) + ' ⨉ ' + esc(b) + '</span>' +
          '<span class="lbl">sessions</span><span class="val">' + fmtN(c) + '</span>'
  };
}
if (kind === 'projskill') {
  const p = el.getAttribute('data-p') || '';
  const s = el.getAttribute('data-s') || '';
  const c = el.getAttribute('data-c') || '0';
  return {
    kind: 'projskill',
    html: '<span class="ttl">' + esc(p) + ' × ' + esc(s) + '</span>' +
          '<span class="lbl">events</span><span class="val">' + fmtN(c) + '</span>'
  };
}
```

## 🧪 TDD テスト計画

### 新規 server 側 unit tests (`tests/test_dashboard_cross_tabs.py`)

`test_dashboard_heatmap.py` のパターンを踏襲し、`USAGE_JSONL` env をパッチして
モジュールをロード、aggregator を直接呼ぶ。

```python
class TestAggregateSkillCooccurrence:
    def test_empty_events_returns_empty_list(self, tmp_path):
        # → []

    def test_single_session_single_skill_no_pair(self, tmp_path):
        # 1 session 内で 1 unique skill のみ → pair 0 件
        # (acceptance: unique skill が 0/1 件のとき pair 数は 0)

    def test_single_session_two_skills_one_pair(self, tmp_path):
        # 1 session 内で 2 unique skill → 1 pair
        # pair=[a, b] (sorted) / count=1

    def test_single_session_three_skills_three_pairs(self, tmp_path):
        # 1 session 内で 3 unique skill → C(3,2)=3 pair / 各 count=1
        # acceptance criteria の C(N, 2) 検証

    def test_pair_normalized_to_sorted_order(self, tmp_path):
        # session A: [foo, bar] / session B: [bar, foo] → 同じ pair=[bar, foo](sorted) で count=2
        # 異 session の同一 skill ペアが正しく合算 (acceptance criteria)

    def test_self_pair_excluded(self, tmp_path):
        # 1 session 内で同じ skill が 5 回 → unique 化で 1 → pair 0 件
        # combinations は (a, a) を出さない

    def test_user_slash_command_counted(self, tmp_path):
        # skill_tool 1 件 + user_slash_command 1 件 → 1 pair
        # (filter が両 event_type を拾っている確認)

    def test_subagent_excluded(self, tmp_path):
        # subagent_start 1 件 + skill_tool 1 件 → pair 0 件 (1 session 1 unique skill)

    def test_session_start_notification_excluded(self, tmp_path):
        # session_start / notification は filter で除外され pair に影響しない

    def test_empty_session_id_skipped(self, tmp_path):
        # session_id="" の events はグルーピングできないので skip

    def test_empty_skill_name_skipped(self, tmp_path):
        # skill="" の events は skip (空 skill で pair に紛れ込まない)

    def test_top_n_cap_at_100_default(self, tmp_path):
        # 101 distinct pair を投入 → 返り値 100 件
        # 101 個目 (count 最小) が捨てられている

    def test_top_n_cap_custom(self, tmp_path):
        # top_n=5 で呼ぶと 5 件で打ち切り

    def test_count_descending_order(self, tmp_path):
        # 異なる count の pair を並べると返り値が count 降順

    def test_lexicographic_sort_within_same_count_reverse_input(self, tmp_path):
        # Proposal 3 反映: 逆順入力でも sort で正規化される
        # 入力 events を pair=[("b","c"), ("a","c"), ("a","b")] の順で投入 →
        # 全 count=1 同点 → 返り値は [(a,b), (a,c), (b,c)] の lexicographic 昇順
        # `Counter.most_common` の insertion order 依存を避けたことの regression guard

    def test_count_unit_is_session_not_invocation(self, tmp_path):
        # Proposal 1 反映: 同 session で同じ pair を 5 回トリガしても count=1
        # session_id=A で skill X / skill Y を交互に 5 回ずつ呼ぶ events を投入 →
        # pair=("X","Y") の count は 1 (session 単位 unique 化の確認)


class TestAggregateProjectSkillMatrix:
    def test_empty_events_returns_empty_structure(self, tmp_path):
        # → {"projects": [], "skills": [], "counts": []}

    def test_single_event_creates_1x1_matrix(self, tmp_path):
        # skill_tool 1 件 → projects=[p] / skills=[s] / counts=[[1]]

    def test_top_n_projects_cut(self, tmp_path):
        # 11 distinct project → projects は count 降順 top 10
        # 11 個目の project の events は drop (counts 列に現れない)

    def test_top_n_skills_cut(self, tmp_path):
        # 11 distinct skill → skills は top 10、11 個目は drop

    def test_other_aggregation_not_applied(self, tmp_path):
        # top 漏れは "other" 行/列に集約されないことを invariant 化
        # (将来 (b) 案に倒すときの regression 検出用)

    def test_counts_dimensions_match_axes(self, tmp_path):
        # len(counts) == len(projects) and all(len(row) == len(skills) for row in counts)

    def test_zero_cell_present_when_project_skill_no_overlap(self, tmp_path):
        # P1 では skill A のみ / P2 では skill B のみ → 2x2 matrix で対角だけ非ゼロ

    def test_subagent_excluded(self, tmp_path):
        # subagent_start のみの events → empty structure

    def test_user_slash_command_counted(self, tmp_path):
        # user_slash_command のみで 1x1 matrix

    def test_empty_project_skipped(self, tmp_path):
        # project="" の event は drop

    def test_empty_skill_name_skipped(self, tmp_path):
        # skill="" の event は drop

    def test_projects_skills_descending_by_total_count(self, tmp_path):
        # P1=10, P2=20, P3=15 → projects=[P2, P3, P1] (count 降順)

    def test_custom_top_args(self, tmp_path):
        # top_projects=2, top_skills=3 で軸が打ち切られる

    def test_asymmetric_axes_dimensions(self, tmp_path):
        # Proposal 4 反映: 3 project × 7 skill の非正方 matrix
        # → len(counts) == 3, all(len(row) == 7 for row in counts)
        # B2 renderer の gridTemplateColumns 計算は skills.length に依存するので
        # 行ごとに列数がブレない invariant を test で pin する

    def test_covered_count_equals_sum_of_matrix(self, tmp_path):
        # Proposal 2 反映: covered_count == sum(sum(row) for row in counts)
        # の invariant 確認 (top 漏れを除いた matrix 内総量)

    def test_total_count_includes_top_dropped_events(self, tmp_path):
        # Proposal 2 反映: top 11 個目の project の events も total_count に
        # は含まれる (covered_count < total_count の関係)
        # 11 project + 5 skill, 1 project あたり events 1 件ずつ → covered=10, total=11

    def test_total_count_zero_for_empty_input(self, tmp_path):
        # 空入力時に total_count=0, covered_count=0 (ZeroDivision 防止 invariant)

    def test_total_count_zero_when_only_subagent_events(self, tmp_path):
        # 二次レビュー Q2 反映: subagent_start のみで skill_tool/user_slash_command が 0
        # → total_count=0 / covered_count=0。renderer 側 if (total > 0) で
        # 0% カバー表示が省略される契約を pin

    def test_aggregator_pure_no_input_mutation(self, tmp_path):
        # 二次レビュー Q3 反映: aggregator は events に対して pure。
        # 同じ events を 2 回連続で aggregator にかけ、両回の戻り値が等しいこと、
        # かつ events の中身が変化していないことを assert (in-place mutation 防止)


class TestBuildDashboardDataIncludesCrossTabs:
    def test_skill_cooccurrence_key_present(self, tmp_path):
        data = mod.build_dashboard_data([])
        assert "skill_cooccurrence" in data
        assert data["skill_cooccurrence"] == []

    def test_project_skill_matrix_key_present(self, tmp_path):
        data = mod.build_dashboard_data([])
        assert "project_skill_matrix" in data
        assert data["project_skill_matrix"] == {
            "projects": [], "skills": [], "counts": [],
            "covered_count": 0, "total_count": 0,
        }

    def test_skill_cooccurrence_consistent_with_skill_ranking_filter(self, tmp_path):
        # skill_ranking が pickup する skill のみが pair 候補になる (subagent 混入なし)
        # regression guard
```

### template.html 構造テスト (新規 `tests/test_dashboard_cross_tabs_template.py`)

`test_dashboard_heatmap_template.py` パターン踏襲:

```python
class TestPatternsCrossTabsDOM:
    def test_patterns_section_has_cooccurrence_panel(self):
        section = _extract_section(template, 'patterns')
        assert 'id="patterns-cooccurrence"' in section
        assert 'id="patterns-cooccurrence-panel"' in section
        assert 'id="patterns-cooccurrence-sub"' in section

    def test_patterns_section_has_projskill_panel(self):
        section = _extract_section(template, 'patterns')
        assert 'id="patterns-projskill"' in section
        assert 'id="patterns-projskill-panel"' in section
        assert 'id="patterns-projskill-legend"' in section
        assert 'id="patterns-projskill-sub"' in section

    def test_patterns_section_no_longer_has_issue_59_placeholder(self):
        # #58 で残した <p class="placeholder-body">...#59...</p> が削除されている
        section = _extract_section(template, 'patterns')
        # placeholder 行 (issue 59 言及テキスト) が消えていること
        assert '今後追加予定' not in section
        # ただし他の widget の help-pop 等の説明文には影響しない (具体的な
        # placeholder marker 文言で確認)

    def test_template_has_cooccurrence_renderer_function(self):
        assert 'function renderSkillCooccurrence' in template

    def test_template_has_projskill_renderer_function(self):
        assert 'function renderProjectSkillMatrix' in template

    def test_template_has_cooc_data_tip_kind(self):
        assert 'data-tip="cooc"' in template
        assert "kind === 'cooc'" in template

    def test_template_has_projskill_data_tip_kind(self):
        assert 'data-tip="projskill"' in template
        assert "kind === 'projskill'" in template

    def test_loadAndRender_invokes_cross_tab_renderers(self):
        assert 'renderSkillCooccurrence(data.skill_cooccurrence)' in template
        assert 'renderProjectSkillMatrix(data.project_skill_matrix)' in template

    def test_cooccurrence_table_has_thead(self):
        # 見出し行が存在 (Proposal 1: count 単位は sessions)
        assert '<thead>' in template
        assert 'Skill A' in template
        assert 'Sessions' in template

    def test_cooccurrence_renderer_has_page_scoped_early_out(self):
        # Proposal 5 反映: renderSkillCooccurrence の関数定義直後 (= 第 1 文)
        # に page-scoped early-out が入っていることを grep で確認。
        # window=400 chars に絞る (二次レビュー Proposal 3): early-out は関数 body
        # 冒頭の guard なので 400 chars 内に必ず収まる。広い window だと別 renderer の
        # 本体が含まれて assertion locality が緩むため絞る
        idx = template.index('function renderSkillCooccurrence')
        body = template[idx : idx + 400]
        assert "document.body.dataset.activePage !== 'patterns'" in body

    def test_projskill_renderer_has_page_scoped_early_out(self):
        # 同上 (B2) — 関数冒頭 400 chars に絞る
        idx = template.index('function renderProjectSkillMatrix')
        body = template[idx : idx + 400]
        assert "document.body.dataset.activePage !== 'patterns'" in body

    def test_cooccurrence_tooltip_uses_sessions_label(self):
        # Proposal 1 反映: tooltip の lbl が 'sessions' (旧 'co-occurrences' ではない)
        assert ">sessions<" in template or "'sessions'" in template
        # 旧ボキャブラリが残っていないこと
        assert 'co-occurrences' not in template

    def test_projskill_sub_label_includes_covered_count(self):
        # Proposal 2 反映: sub label に covered/total のカバー率表示が組まれている
        # window=2500 chars: renderProjectSkillMatrix は本体ロジック (matrix 構築 +
        # legend + sub) が長く、sub label 部は関数末尾近く。3000 だと近隣関数が
        # 紛れ込みうるが、2500 は実装サイズに対してタイト
        idx = template.index('function renderProjectSkillMatrix')
        body = template[idx : idx + 2500]
        assert 'covered_count' in body or 'covered' in body
        assert 'total_count' in body or '% covered' in body

    def test_projskill_panel_uses_peach_color(self):
        # panel-head c-peach が project × skill panel に付いている
        section = _extract_section(template, 'patterns')
        # cooccurrence panel と projskill panel の panel-head class を抽出して確認
        # (精密な regex までは不要、文字列存在確認で十分)
        assert 'patterns-projskill-panel' in section
        # 直近の panel-head c-peach 出現が projskill であることを order で確認:
        cooc_idx = section.index('patterns-cooccurrence-panel')
        proj_idx = section.index('patterns-projskill-panel')
        assert cooc_idx < proj_idx  # 順序: heatmap → cooc → projskill
```

### 既存テストへの影響 (regression)

- `tests/test_dashboard.py:TestBuildDashboardData` 系: 既存テストは
  `total_events` / `daily_trend` / `project_breakdown` / `subagent_ranking`
  のみを assert しているので、新 field 追加で破壊されない (`test_empty_events_returns_valid_structure`
  も存在キー限定列挙していない)
- `tests/test_export_html.py`: `window.__DATA__` 注入の round-trip 確認のみ → 影響なし
- `tests/test_dashboard_heatmap_template.py:test_patterns_section_keeps_issue_59_reference`:
  **破壊される** (`#59` 言及が削除されるため)。本テストは `#58` 完了時点での申し送り
  ガードだったので、本 PR では **削除** する (issue #59 が完了した時点で役割を終える)
- `tests/test_dashboard_router.py:test_non_overview_pages_are_placeholders`: Patterns
  はすでに `#58` で実 widget 化されており、本 PR でも Quality / Surface のみが
  placeholder として check 対象 → 影響なし

### テスト数の見込み

- 新規 server 側 unit: B1 ~16 (Proposal 1/3 反映で +2) + B2 ~18 (Proposal 2/4 + 二次 Q2/Q3 反映で +6)
  + integration ~3 = **~37 テスト**
- 新規 template 構造: **~14 テスト** (Proposal 1/2/5 反映で +4)
- 削除: `test_patterns_section_keeps_issue_59_reference` 1 件
- **合計: ~593 + 51 - 1 ≈ ~643 tests / 全 pass** (現状 593 pass + 1 skip)

## 📦 実装ステップ (TDD red→green→refactor)

### Phase 1: B1 server-side aggregation (RED → GREEN)

1. **RED**: `tests/test_dashboard_cross_tabs.py` 新規作成 +
   `TestAggregateSkillCooccurrence` (~14 tests) を書く。
   `aggregate_skill_cooccurrence` 未実装 → `AttributeError`
2. **GREEN**: `dashboard/server.py` の `aggregate_projects` の直後に
   `aggregate_skill_cooccurrence` を実装。`itertools.combinations` で pair 列挙、
   `Counter` で集計、**明示 sort**
   `sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]` で順序を pin
   (Proposal 3 反映 — `Counter.most_common` の insertion order 依存を避ける)。
   pair の中身は tuple 内で sorted、出力 dict では list[a, b] に変換
3. **REFACTOR**: `(session_id → set[skill])` のグルーピングが
   `aggregate_project_skill_matrix` でも使えるか検討 → **共通 helper 化しない**
   (B2 は session_id を使わない / 集計対象が違う)。

### Phase 2: B2 server-side aggregation (RED → GREEN)

1. **RED**: 同ファイルに `TestAggregateProjectSkillMatrix` (~12 tests) 追加。
   未実装で fail
2. **GREEN**: `aggregate_skill_cooccurrence` の直後に
   `aggregate_project_skill_matrix` を実装。
   - 1 pass で `(project, skill) → count` の Counter を作成
   - project 別 / skill 別の合計 Counter を **同時に** 作る (再 walk 不要)
   - `total_count` = filter 後 events 総数 (top 漏れ含む) を集計しながら蓄積
   - top 10 ずつ抽出 → dense 2D matrix を組み立て
   - `covered_count` = `sum(sum(row) for row in counts)` で計算 (matrix 構築後)
   - 出力 dict に `covered_count` / `total_count` を含める (Proposal 2 反映)

### Phase 3: build_dashboard_data 統合 (2 行追加)

1. **RED**: `TestBuildDashboardDataIncludesCrossTabs` の 3 tests が fail
2. **GREEN**: `build_dashboard_data` の return dict に
   ```python
   "skill_cooccurrence": aggregate_skill_cooccurrence(events),
   "project_skill_matrix": aggregate_project_skill_matrix(events),
   ```
   の 2 行追加 (raw events を渡す / `usage_events` ではない、上記「filter 慣習」
   セクション参照)。

### Phase 4: template.html DOM (RED template tests → GREEN)

1. **RED**: `tests/test_dashboard_cross_tabs_template.py` 新規 (~10 tests)
   + 既存 `test_patterns_section_keeps_issue_59_reference` 削除
2. **GREEN**: `<section data-page="patterns">` の `<p class="placeholder-body">`
   を 2 個の panel (cooccurrence / projskill) に置換

### Phase 5: CSS / JS renderer (visual smoke)

1. CSS 追加 (`/* skill cooccurrence (Issue #59 / B1) */` と
   `/* project x skill heatmap (Issue #59 / B2) */` のラベル付き)
2. JS: `renderSkillCooccurrence` と `renderProjectSkillMatrix` を
   `renderHourlyHeatmap` の後に並べる。`loadAndRender()` 末尾で 2 行 call
3. **実機 smoke**:
   - `python3 dashboard/server.py` 起動 + 自分の usage.jsonl
   - `#/patterns` で 3 panel (heatmap / cooc / projskill) が縦に並ぶこと
   - cooc table: top pair が件数降順で並び、空 skill/subagent が混入しない
   - projskill heatmap: top 10 × top 10 の dense matrix、cell 0 も border のみで存在感
   - hover で tooltip (`A ⨉ B: 12 sessions` / `proj × skill: 8 events`)
   - keyboard tab で row / cell に focus し tooltip
   - SSE refresh で再描画
   - `#/` 起動 → `#/patterns` navigate で即時描画 (hashchange 連携)
   - `python3 reports/export_html.py --output /tmp/static.html` で static export
     にも反映

### Phase 6: tooltip 拡張 (`dtipBuild()` に 2 分岐追加)

1. `dtipBuild(el)` 内、heatmap 分岐の後ろに `cooc` / `projskill` 分岐を追加
2. `.data-tip[data-kind="cooc"]` / `.data-tip[data-kind="projskill"]` の
   `border-left-color` を CSS に追加 (Phase 5 と並走)
3. テスト: `test_template_has_cooc_data_tip_kind` / `..._projskill_data_tip_kind`
   で grep ベース確認

### Phase 7: docs (docs/spec/dashboard-api.md / CLAUDE.md / MEMORY.md)

1. **`docs/spec/dashboard-api.md`** に `## skill_cooccurrence` と
   `## project_skill_matrix` セクションを additive で追加。
   - 各セクションに schema 例 / 集計仕様 / filter 慣習 / 設計判断 ("other" 集約
     不採用の議論) を載せる
   - hourly_heatmap セクションのフォーマットに揃える
2. **`CLAUDE.md`**: 既存 "ダッシュボード複数ページ構成" セクションの schema
   詳細は `docs/spec/dashboard-api.md` を参照、と既に書かれている。**追加変更
   なし** (CLAUDE.md は behavior convention のみで、schema 詳細は spec doc に
   寄せる規範を `#58` で確立済み)
3. **MEMORY.md**: 1 行 index 追加 (本 issue の核要点 + spec doc pointer)

### Phase 8: PR

ブランチ: `feature/59-cross-tab-viz` (Issue #57/#58 命名規則踏襲)
PR タイトル候補: `feat(dashboard): cross-tab viz — skill cooccurrence + project×skill (#59)`

#### PR 粒度判断

**recommendation: B1 + B2 一括 PR**

両 widget は filter 慣習 / DOM placement / CSS naming / TDD 流れがほぼ同型なので、
分割すると review コストが嵩む割に並行価値が低い。

**condition for splitting (Proposal 6 反映)**: 「往復回数」ではなく
「**変更面の質**」をトリガーにする。具体的には Phase 1 (B1 server) または
Phase 2 (B2 server) の review で **以下のいずれかが 1 回でも入った時点**で
分割する:

- schema field 名の変更要求 (例: `skill_cooccurrence` → `skill_pair_sessions`)
- count 単位の変更要求 (session 単位 → invocation 単位)
- B2 "other" 集約の採用要求

これらは下流 (template / docs / test) を全 trigger するため、回数を待たず即分割
が経済的。逆に CSS / DOM / lede 文言など stylistic な指摘は何 round 入っても
一括 PR を継続する。分割発動時は Phase 2+ を別 PR `feature/59b-project-skill-matrix`
に切る。

PR 本文:
- 親 issue #48 / 当該 issue #59 / 前提 PR #57 (shell), #58 (heatmap) を参照
- B1/B2 schema 例 + filter 慣習表
- "other" 集約不採用の議論サマリ
- 実機スクショ: 自分のデータでの cooc table / projskill heatmap / tooltip

## 🚫 Out of Scope

issue 本文記載に加え、以下も本 PR では扱わない:

- **時系列共起 (Sequential pattern)**: 「A の後 B が来る」順序付き共起。
- **Subagent との共起 (skill ↔ subagent / subagent ↔ subagent)**: filter 慣習を
  揃えれば後続で additive に拡張可能 (`subagent_skill_cooccurrence` field 追加)
- **Project × subagent_type heatmap**: 本 issue の OOS。subagent 系の cross は別 issue
- **archive 込みの集計**: dashboard 仕様で hot tier のみ
- **共起 chord diagram / network graph**: 描画コスト過大なので table で着地
- **B2 "other" 集約**: 上記議論の通り不採用 (top 10 × top 10 sparse のみ)
- **B2 軸の手動切替 (project × skill ↔ skill × project)**: 行/列固定 (project が行)
- **drill-down (cell click → 該当 events 一覧)**: tooltip のみ
- **共起件数の表示単位 (% / 比率)**: count のみ。ratio 系は後続 issue
- **page-scoped early-out のさらなる最適化**: heatmap (#58) と同じ規範踏襲

## 🧷 リスクと不確実性

| リスク | 影響 | 対策 |
|---|---|---|
| 1 session 内 unique skill が極端に多い (e.g. 50+) と pair 数が C(50,2)=1225 個に膨らみ Counter サイズが大きくなる | memory / sort コスト | top 100 cap で中間 Counter は最終的に絞られる。 distinct pair が 100K オーダーまでは余裕 (Counter 内部 dict)。実測 Phase 5 で確認 |
| 同じ skill が同 session で複数回呼ばれても unique 化されるが、それを「使用度」と取り違えやすい | UX 解釈ズレ | help-pop に「同じセッションで両方が一度でも登場すれば 1 件」を明記 (上記 DOM 雛形) |
| top 10 × top 10 で全体カバー率が低いと heatmap が誤解を招く | 実利用判断ミス | sub label に `N projects × M skills` を出すのみ。カバー率の sub 表示が必要なら後続 issue |
| skill 名が長くて column header が読めない | 視認性低下 | CSS で `max-width: 80px; ellipsis;` + `title=` 属性で hover full 名表示。実機で短縮文字数を調整 |
| project 名 (cwd encode) が長すぎて row label が読めない | 同上 | row label `max-width: 140px; ellipsis;` + `title=` で full 表示 |
| Patterns ページに panel が 3 個並んで縦長になる | scroll 負荷 | overview ページも縦長慣習 (6 panel)。本 PR では受容。**閾値 (Q2 反映)**: Patterns の合計 scroll 高が overview を超えたら sub-tab 化を再検討。後続 #60/#62 で Quality / Surface に panel が増えた時点で再判断する |
| `test_patterns_section_keeps_issue_59_reference` を削除すると `#58` の DoD ガードが消える | 後追いトラッキング不能 | 本 PR が `#59` 完了 PR なのでガード消去は妥当 (役目終了)。代わりに `#59` 自身の test (cooc / projskill panel 存在) が新たな構造ガードになる |
| B1 が空 result (1 session も pair を持たない) の場合の UI が寂しい | UX | empty state テキスト「共起データなし」を明示表示 |

## ✔️ Definition of Done

- [ ] `tests/test_dashboard_cross_tabs.py` の新規 ~37 unit tests 全 pass
- [ ] `tests/test_dashboard_cross_tabs_template.py` の新規 ~14 構造テスト全 pass
- [ ] 既存 `test_patterns_section_keeps_issue_59_reference` を削除して全 pass
- [ ] `tests/test_dashboard.py` / `test_export_html.py` / `test_dashboard_router.py` / `test_dashboard_heatmap.py` 全 pass (regression)
- [ ] **全 ~643 tests pass** (現状 593 pass + 1 skip / + 約 51 / - 1 削除)
- [ ] 実機: 自分の usage.jsonl で Patterns に 3 panel が並び、cooc table と
  projskill heatmap が描画される
- [ ] B1 sub label に `N pairs (top 100)` / B2 sub label に
  `N projects × M skills · X% covered (covered/total)` が出る (Proposal 2)
- [ ] hover tooltip が両 widget で正しく出る (`A ⨉ B: N sessions` / `P × S: N events`)
- [ ] keyboard tab で row / cell に focus + tooltip
- [ ] SSE refresh / `#/` → `#/patterns` navigate / static export いずれでも描画
- [ ] B1 lexicographic 安定 sort: 逆順入力でも返り値の pair 順が
  `[("a","b"), ("a","c"), ("b","c")]` (Proposal 3 の test 経由で pin)
- [ ] `docs/spec/dashboard-api.md` に 2 セクション追加
- [ ] `MEMORY.md` に 1 行 index 追加
- [ ] PR `feature/59-cross-tab-viz` を `v0.7.0` ブランチ向けに作成

## 📦 変更ファイル一覧 (見込み)

- `dashboard/server.py` — `aggregate_skill_cooccurrence()` +
  `aggregate_project_skill_matrix()` 追加 + `build_dashboard_data` に 2 行統合
  (~80 行追加)
- `dashboard/template.html` — Patterns section に 2 panel 追加 (placeholder 削除) /
  CSS 追加 / JS renderer 2 個 + dtipBuild 2 分岐追加 (~180 行追加)
- `tests/test_dashboard_cross_tabs.py` (新規) — server 側 unit tests
- `tests/test_dashboard_cross_tabs_template.py` (新規) — template 構造テスト
- `tests/test_dashboard_heatmap_template.py` — `test_patterns_section_keeps_issue_59_reference`
  1 件削除
- `docs/spec/dashboard-api.md` — `skill_cooccurrence` / `project_skill_matrix`
  セクション追加 (~80 行追加)
- `~/.claude/projects/-Users-kkoichi-Developer-personal-claude-transcript-analyzer/memory/MEMORY.md` — 1 行 index

`subagent_metrics.py` / `_filter_usage_events` / 既存 aggregator は触らない
(本 widget は raw events + inline filter で完結)。

## 📨 後続 PR への申し送り

- `#60` (Quality: subagent percentile / weekly) は別 page (`<section data-page="quality">`)
  なので干渉しない
- `#62` (Surface: skill 発見性) も別 page
- 本 PR で確立した「raw events + inline filter (skill_tool +
  user_slash_command)」パターンは subagent 系 cross widget でも踏襲できる
  (例: `aggregate_subagent_skill_cooccurrence` を `_filter_usage_events` に
  乗せず inline で書く)
- **filter membership の定数化**: 本 PR 後、skill 系 inline filter の使用箇所が
  `aggregate_skills` / `aggregate_skill_cooccurrence` /
  `aggregate_project_skill_matrix` の **3 か所** になる (二次レビュー Q1 反映)。
  もし将来 `("skill_tool", "user_slash_command")` に第 3 の event_type が加わる
  必要が出たら、3 か所同時更新が必要。**現時点での閾値**: 4 か所目が必要に
  なった or 1 種類でも追加要請が出たら、`_SKILL_USAGE_EVENT_TYPES =
  frozenset({"skill_tool", "user_slash_command"})` を module 定数として extract
  する。本 PR では 3 か所留まりかつ追加要請なしのため YAGNI で見送り。
