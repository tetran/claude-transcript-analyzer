# Issue #61 Plan — Friction signals (A2 permission/skill 紐付け + A3 compact 密度)

## 📋 plan-reviewer 反映ログ

| Proposal | 内容 | 反映箇所 |
|---|---|---|
| P1 | A2 擬似コードの subagent 代表 ev 解釈ねじれを解消。`usage_invocation_events()` は **両 hook 発火 invocation で `subagent_start` を代表に選ぶ** ため `ev.timestamp = 終了時刻` になる事実を擬似コードコメントで明示。`subagent_lifecycle_start` 例外分岐は **lifecycle-only invocation のみ** に発火する。test 2 件追加 (`test_subagent_lifecycle_only_invocation_uses_start_timestamp_as_interval_start` / `test_subagent_both_hooks_invocation_uses_end_timestamp`) | A2 帰属 algorithm 擬似コード + `TestPermissionLinkAlgorithm` |
| P2 | `usage_invocation_events` (帰属候補生成) と `aggregate_subagent_metrics` (count 取得) の **二重経路 drift guard**。test 1 件追加 (`test_subagent_attribution_count_matches_metrics_count` = type 単位 invocation_count 合計が `aggregate_subagent_metrics[name].count` と常に一致) | TDD test 計画 (`TestPermissionBreakdownsAggregate`) / Risk 表 |
| P3 | A3 worst_sessions の `project=""` を UI で **`(unknown)` ラベル** で出す (空セルだと「データ欠損」と「project が空文字」が見分けつかなくなる)。renderer 内 `proj === '' ? '<span class="dim">(unknown)</span>' : esc(proj)`、test 1 件追加 (`test_worst_session_unknown_project_shown_as_unknown_label`) | A3 renderer + `TestQualityPagePermissionPanels` |
| P4 | Phase 5 実機 smoke の edge case に「`permission_rate > 1.0` (1 invocation で 2+ permission) で `200%` 等が表示され、`rate-warn` peach 色強調が出ること、cell 幅で改行しないこと」を追加 | Phase 5 実機 smoke / DoD edge case |
| Q1 | `memory/friction_signals.md` に **定数値の決定根拠 + fine-tune 観測指標** を pin。具体的には「`PERMISSION_LINK_WINDOW_SECONDS = 30` の根拠 (Issue #61 本文) / 実機で orphan ratio (= attribution 失敗 permission の割合) を見て fine-tune の trigger とする」を 3 行追加 | Phase 6 (memory file 内容) |
| Q2 | A2 algorithm の **subagent semantics 部分だけ** `subagent_metrics.py` に切り出す中間策を採用。`subagent_invocation_interval(ev) -> (start_ts, end_ts)` helper を `subagent_metrics.py` に新設し、`dashboard/server.py:aggregate_permission_breakdowns` は import して使うだけにする。後続 `reports/summary.py` への移管時の blast radius を最小化 (#60 2-Q1 教訓踏襲) | Phase 1 GREEN 配置先 / 関数 signature / 変更ファイル一覧 |
| Q3 | Phase 5 実機 smoke に **perf 確認 1 行**: `time python3 dashboard/server.py` 起動時間 + `/api/data` レスポンス時間が #60 merge 時点と比べて顕著に伸びていないこと | Phase 5 実機 smoke |

### 二次レビュー反映 (2nd round)

| 二次 Proposal | 内容 | 反映箇所 |
|---|---|---|
| 2-P1 | 申し送りの「`subagent_metrics.py` 切り出し」記述が Q2 反映前 (= 全部移管 deferred) のまま読める表現になっていた点を訂正。Q2 で **subagent invocation の interval 解釈は本 PR で `subagent_metrics.subagent_invocation_interval` に既に切り出し済み**。残り deferred = `_skill_event_interval` / `_attribute_permission` policy / `aggregate_permission_breakdowns` 公開関数の 3 つ — を明示し、後続 reports/summary.py 移管時の判断対象を絞り込む | 申し送り L (移管 deferred 記述) |
| 2-P2 | Q2 で subagent 側 interval helper だけ subagent_metrics.py に出した結果、skill_tool 側 `_skill_event_interval` を dashboard ローカルに残す **非対称性の根拠** が暗黙だった点を明示。skill_tool は subagent_metrics.py の責務スコープ外なので残置が正解。共有化圧力 (= reports/summary.py の skill 集計が同 algorithm を必要とする) が来たら改めて判断 | 関数 signature 節 (Q2 反映ブロック内) |
| 2-P3 | Phase 1 GREEN-A の `TestSubagentInvocationInterval` (~5 tests) が Phase 順序リストには宣言されているが TDD テスト計画節の中に test list が無く、実装時に「何 5 件か」が暗黙だった点を解消。test 名を pin: `test_subagent_start_uses_end_timestamp_interval` / `test_subagent_lifecycle_only_uses_start_timestamp_interval` / `test_no_duration_returns_point_interval` / `test_both_hooks_invocation_uses_subagent_start_representative` / `test_invalid_timestamp_returns_zero_or_skipped_interval` | TDD テスト計画 (新 class `TestSubagentInvocationInterval`) + テスト数見込み更新 |
| 2-Q1 | 「skill / subagent disjoint」の **合算 invariant test** を追加 (= `sum(skill[i].prompt_count) + sum(subagent[j].prompt_count) <= permission_notification_count`)。`test_skill_and_subagent_disjoint_attribution` は単一帰属ポリシーの直接 test だが、issue 本文の "table 合算可能性" は invariant として別 test を立てる方が合算利用に対する保証が強い。test 名: `test_skill_and_subagent_prompt_counts_sum_le_total_notifications` | TDD テスト計画 (`TestPermissionBreakdownsAggregate`) |
| 2-Q2 | `docs/spec/dashboard-api.md` の追記方針として「**spec doc は API 形状のみ**、interval helper の責務分担は `memory/friction_signals.md` と `subagent_metrics.py` docstring に集約」を Phase 6.1 末尾に明記。spec doc の関心事を schema 規約に閉じ、実装責務の話を混ぜない | Phase 6.1 末尾 (1 行追記) |

## 🎯 Goal

Quality ページ (#57 shell + #60 で percentile/trend 2 panel 既設) に **摩擦シグナル**
2 件を追加し、ユーザーが「どの skill / どの session で permission / compact が起きて
いるか」を見て具体的アクションに繋げられる状態を作る。

- **A2. Permission/skill 紐付け**: `notification(permission|permission_prompt)` を
  直前の `skill_tool` / subagent invocation に session 内時系列リンクで帰属させ、
  skill / subagent ごとに `permission_rate = 帰属 prompt 数 / 総起動数` を算出
- **A3. Compact 密度**: `compact_start` を session 単位で集計し、回数の histogram
  (0 / 1 / 2 / 3+) と worst session top 10 を返す

両 viz は同じ Quality ページ (`<section data-page="quality">`) に追加する。
panel 配置順は (1) 既存 percentile, (2) 既存 trend, (3) A2-skill, (4) A2-subagent,
(5) A3 — 「subagent 観点」→「skill/subagent permission」→「session 観点」と
焦点を順番にズームアウト。

## 📐 機能要件 / 構造設計

### A2. Permission/skill 紐付け

#### 集計仕様 — 帰属 algorithm

permission notification の attribution は **execution-window 優先 + 直前 backward
fallback** の 2 段階。**v1 simple な「timestamp backward window のみ」だと、長時間
subagent の途中で発火した permission が `subagent_start.timestamp` (= 終了時刻)
より前に来てしまい構造的にミスする**ため、execution interval を併用する。

```
PERMISSION_LINK_WINDOW_SECONDS = 30  # backward fallback 窓 (秒)

for each notification N (permission|permission_prompt) in session S:
  candidates = []
  # skill_tool: event.timestamp = PostToolUse 発火時刻 = ツール終了時刻
  for ev in skill_tool events of session S:
    end_ts = ev.timestamp
    start_ts = end_ts - (ev.duration_ms / 1000) if ev.duration_ms else end_ts
    if start_ts <= N.ts <= end_ts:
      candidates.append((ev, "covers", start_ts))   # interval 内
    elif end_ts <= N.ts <= end_ts + WINDOW:
      candidates.append((ev, "after", end_ts))      # backward window (end ≤ notif)
  # subagent: usage_invocation_events で dedup 済み 1 invocation 1 event
  # NOTE (P1 反映): usage_invocation_events() は両 hook 発火 invocation で
  # `subagent_start` を代表に選ぶ (subagent_metrics.py:84-85)。つまり代表 ev の
  # timestamp は 終了時刻 になり、間接的に [end - duration, end] interval が出る。
  # `subagent_lifecycle_start` 例外分岐は lifecycle-only invocation でのみ発火し、
  # その場合は ev.timestamp = 開始時刻 として [start, start + duration] interval を使う。
  for ev in usage_invocation_events(session S):
    # Q2 反映: interval 計算は subagent_metrics.subagent_invocation_interval(ev)
    # に委譲。dashboard 側は domain logic を持たない。
    start_ts, end_ts = subagent_invocation_interval(ev)
    if start_ts <= N.ts <= end_ts:
      candidates.append((ev, "covers", start_ts))
    elif end_ts <= N.ts <= end_ts + WINDOW:
      candidates.append((ev, "after", end_ts))
  # attribution: covers > after, 直近 (= 最新 start_ts/end_ts)
  if any covers: pick most recent start_ts
  elif any after: pick most recent end_ts
  else: drop (= no attribution / orphan permission)
```

**帰属ポリシー (= 単一帰属)**:
- 1 notification は **skill OR subagent の 1 候補にのみ** 帰属する (= skill table
  と subagent table の prompt_count は disjoint で合算可能)
- 候補が両方 (skill_tool 1 + subagent 1) ある場合は「直近 1 個」 — `start_ts`
  または `end_ts` がより新しい (= notification に近い) 方を採用
- 帰属できない notification は `orphan_count` として metadata に集計するが
  schema トップに出さない (本 PR では捨てる、申し送りで観測値を見て判断)

**user_slash_command の扱い**:
- `user_slash_command` は **対象外**。Issue 本文「直前の `skill_tool` / `subagent_start`」
  に従い skill_tool のみリンク対象とする。slash command は permission の起因にならない
  (slash command 自体はモデル発話の prefix であり、ツール実行を伴わない)

**notification_type の同一視**:
- `frozenset({"permission", "permission_prompt"})` で同一視する既存定数
  `_PERMISSION_NOTIFICATION_TYPES` (dashboard/server.py:89) を再利用

**subagent invocation 同定**:
- `usage_invocation_events(events)` (subagent_metrics.py:53) で dedup 済み
  invocation 列を取得 → `aggregate_subagent_metrics` の count と一致
- ただし PostToolUse 由来 (`subagent_start`) は終了時刻、SubagentStart 由来
  (`subagent_lifecycle_start`) は開始時刻という意味の違いを algorithm で
  考慮する (上記擬似コード参照)

#### 集計仕様 — rate 算出

```
skill_breakdown[name] = {
  prompt_count: 帰属された permission 数,
  invocation_count: skill_tool events 数 (success/failure 区別なし),
  permission_rate: prompt_count / invocation_count,
}
subagent_breakdown[name] = {
  prompt_count: 帰属された permission 数,
  invocation_count: aggregate_subagent_metrics(events)[name].count,
  permission_rate: prompt_count / invocation_count,
}
```

- **invocation_count = 0 の skill / subagent**: prompt_count > 0 になり得ない
  (帰属には skill/subagent invocation の存在が前提) ので構造的にゼロ除算しない
- **prompt_count = 0** で invocation_count > 0 の skill/subagent は
  `permission_rate = 0.0`、出力配列に **含めない** (top 10 は prompt_count 降順)

#### 集計仕様 — sort / top-N

- `prompt_count` 降順 → 同点は `name` 昇順 (lexicographic) で stable sort
- 上位 **10 件** を返す (Issue 明記)
- 11 位以下の合計は出さない (#59 project_skill_matrix の "other" 不採用慣習を踏襲)

#### Schema

```json
{
  "permission_prompt_skill_breakdown": [
    {"skill": "user-story-creation", "prompt_count": 3, "invocation_count": 12, "permission_rate": 0.25},
    {"skill": "rails-restful-controllers", "prompt_count": 2, "invocation_count": 8, "permission_rate": 0.25}
  ],
  "permission_prompt_subagent_breakdown": [
    {"subagent_type": "Explore", "prompt_count": 5, "invocation_count": 10, "permission_rate": 0.5}
  ]
}
```

- field 名: `skill` / `subagent_type` (既存 ranking と同じ key 名)
- 空配列ならば `[]` を返す (key 自体は常に存在 / browser 側 defensive 不要を維持)
- `permission_rate` は Python `float` (browser で `Math.round(x*100)`)
- `permission_rate > 1.0` は normal な状態 (1 invocation で複数回 permission を
  聞かれるケース) なので **clamp しない**。help-pop に注釈

#### 関数 signature

```python
# Q2 反映: subagent invocation の interval 解釈責務だけ subagent_metrics.py 側に置く。
# dashboard/server.py は domain logic を持たず、helper を import するだけ。
# 後続 reports/summary.py が permission breakdown を出す issue 来たときの
# 移管 blast radius を最小化する (#60 2-Q1 教訓: responsibility purity 採用)。

# === subagent_metrics.py 側 (新規 helper) ===
def subagent_invocation_interval(ev: dict) -> tuple[float, float]:
    """subagent invocation の代表 event から (start_epoch, end_epoch) を返す。

    `usage_invocation_events()` は両 hook 発火 invocation で `subagent_start` を
    代表に選ぶため、通常は ev.timestamp が終了時刻 → [end - duration, end] を返す。
    `event_type == "subagent_lifecycle_start"` (lifecycle-only invocation) のみ
    例外分岐: ev.timestamp が開始時刻 → [start, start + duration] を返す。
    duration_ms が無いときは start == end (point timestamp 扱い)。

    本 PR では permission attribution の interval 判定でしか使わないが、
    後続で reports/summary.py 等が同 algorithm を必要としたら同じ helper を共有する。
    """

# 2-P2 反映: skill_tool 側の `_skill_event_interval` は dashboard ローカル helper の
# まま残置する。理由: skill_tool は subagent_metrics.py の責務スコープ外なので、
# 移管すると逆に責務分担が乱れる。共有化圧力 = reports/summary.py の skill 集計が
# 同 algorithm を必要とする — が来たときに、`_skill_event_interval` の置き場所
# (= 別の共有 module 切り出し / dashboard 残置) を改めて判断する

# === dashboard/server.py 側 (aggregator) ===
PERMISSION_LINK_WINDOW_SECONDS = 30  # constant


def aggregate_permission_breakdowns(events: list[dict], top_n: int = TOP_N) -> dict:
    """notification(permission) を直前 skill_tool / subagent invocation に帰属。

    interval 計算 (subagent 側) は subagent_metrics.subagent_invocation_interval()
    に委譲する。skill_tool 側の interval は本関数内で `[end - duration_ms/1000, end]`
    として計算 (skill_tool は終了時刻 timestamp 慣習で固定)。

    返り値: {
      "skill": [{"skill": str, "prompt_count": int,
                 "invocation_count": int, "permission_rate": float}, ...],
      "subagent": [{"subagent_type": str, ...同}, ...],
    }
    """
```

build_dashboard_data でこれを呼んで return dict に 2 キーを spread:

```python
b = aggregate_permission_breakdowns(events)
return {
    ...
    "permission_prompt_skill_breakdown": b["skill"],
    "permission_prompt_subagent_breakdown": b["subagent"],
    ...
}
```

### A3. Compact 密度

#### 集計仕様

- **データ源**: `compact_start` events を `session_id` で groupby
- **histogram**:
  - `0` bucket: 「session_start を持つ session」のうち compact_start 0 件のもの
  - `1` bucket: compact_start 1 件
  - `2` bucket: compact_start 2 件
  - `3+` bucket: compact_start 3 件以上
- **session pool**: `session_start` event の `session_id` 集合を「session pool」と
  する。compact_start のみで session_start が無い orphan session_id は
  histogram には含めず (= 0 bucket への混入を避ける) `worst_sessions` には載せる
  - **理由**: histogram の 0 bucket 分母を「実観測 session 数」に揃えるため。
    orphan は計算ノイズ
- **worst_sessions**: 全 compact_start を session_id で groupby し count 降順で
  top 10。同点は session_id 昇順で安定 sort
  - 各要素: `{"session_id": str, "count": int, "project": str}`
  - `project` は当該 session の **最後に観測した** compact_start.project (空なら
    `""`)

#### Schema

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

- `histogram` キー名は string (`"0" / "1" / "2" / "3+"`) で固定 4 キー
- empty events なら `{"histogram": {"0": 0, "1": 0, "2": 0, "3+": 0}, "worst_sessions": []}`
- `histogram["0"]` は session_start を持つ session のうち compact 0 件の数
- `worst_sessions` は最大 10 件、count 降順 / session_id 昇順 sort

#### 関数 signature

```python
def aggregate_compact_density(events: list[dict], top_n: int = TOP_N) -> dict:
    """session 単位 compact_start 集計。histogram (0/1/2/3+) + worst_sessions top-10。"""
```

### Quality ページの DOM 追加 (3 panel)

既存 2 panel (`#quality-percentile-panel`, `#quality-trend-panel`) の **後** に
3 panel を追加。HTML 全体は `<section data-page="quality">` の中に閉じる。
`page-placeholder` class は #60 で既に外れている。

```html
<!-- (3) A2-skill: Permission per skill ranking (Issue #61) -->
<div class="panel" id="quality-perm-skill-panel">
  <div class="panel-head c-mint">
    <div class="ttl-wrap">
      <span class="ttl"><span class="dot"></span>Permission prompt × skill (top 10)</span>
      <span class="help-host">
        <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-perm-skill" data-help-id="hp-perm-skill">?</button>
        <span class="help-pop" id="hp-perm-skill" role="tooltip" data-place="right">
          <span class="pop-ttl">Permission per skill</span>
          <span class="pop-body">permission notification の <strong>直前 30 秒以内 (or 実行中)</strong> に発火していた <code>skill_tool</code> に帰属。1 prompt は 1 候補にのみ帰属 (skill / subagent disjoint)。<code>permission_rate</code> = 帰属 prompt 数 / 総 skill_tool 数。<strong>1 invocation で複数回 permission を聞かれる skill</strong> は rate &gt; 1.0 になる (clamp しない)。<code>fewer-permission-prompts</code> skill / settings.json allowlist 整理のヒントとして使う。</span>
        </span>
      </span>
    </div>
    <span class="sub" id="quality-perm-skill-sub"></span>
  </div>
  <div class="panel-body">
    <table class="perm-table" id="quality-perm-skill">
      <thead>
        <tr>
          <th>Skill</th>
          <th class="num">Prompts</th>
          <th class="num">Invocations</th>
          <th class="num">Rate</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  </div>
</div>

<!-- (4) A2-subagent: Permission per subagent ranking (Issue #61) -->
<div class="panel" id="quality-perm-subagent-panel">
  <div class="panel-head c-coral">
    <div class="ttl-wrap">
      <span class="ttl"><span class="dot"></span>Permission prompt × subagent (top 10)</span>
      <span class="help-host">
        <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-perm-sub" data-help-id="hp-perm-sub">?</button>
        <span class="help-pop" id="hp-perm-sub" role="tooltip" data-place="right">
          <span class="pop-ttl">Permission per subagent</span>
          <span class="pop-body">permission notification を <strong>subagent invocation の execution interval</strong> に帰属。長時間 subagent の途中で発火した prompt も interval-cover で正しく拾う。skill table と disjoint (= 1 prompt は 1 candidate のみ)。</span>
        </span>
      </span>
    </div>
    <span class="sub" id="quality-perm-subagent-sub"></span>
  </div>
  <div class="panel-body">
    <table class="perm-table" id="quality-perm-subagent">
      <thead>
        <tr>
          <th>Subagent</th>
          <th class="num">Prompts</th>
          <th class="num">Invocations</th>
          <th class="num">Rate</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  </div>
</div>

<!-- (5) A3: Compact density (Issue #61) -->
<div class="panel" id="quality-compact-panel">
  <div class="panel-head c-peach">
    <div class="ttl-wrap">
      <span class="ttl"><span class="dot"></span>Compact 発生密度 (per session)</span>
      <span class="help-host">
        <button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="hp-compact" data-help-id="hp-compact">?</button>
        <span class="help-pop" id="hp-compact" role="tooltip" data-place="right">
          <span class="pop-ttl">Compact 密度</span>
          <span class="pop-body">session ごとの <code>compact_start</code> 回数 histogram (0 / 1 / 2 / 3+)。<strong>3+ session が多い</strong> = タスク粒度過大 / <code>/clear</code> タイミングを逃した signal。worst session に載った session_id は session 切り出し / 早期リスタートの示唆。</span>
        </span>
      </span>
    </div>
    <span class="sub" id="quality-compact-sub"></span>
  </div>
  <div class="panel-body">
    <div class="compact-grid">
      <div class="compact-hist" id="quality-compact-hist"></div>
      <table class="worst-table" id="quality-compact-worst">
        <thead>
          <tr>
            <th>Session</th>
            <th>Project</th>
            <th class="num">Compacts</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
</div>
```

### A3 描画方式 — inline SVG histogram

#60 trend chart と同じ inline SVG 慣習を踏襲。

- viewBox: `0 0 360 180` (固定 / responsive 親 div で max-width)
- bars: 4 本 (`0` / `1` / `2` / `3+`)、height = count に比例
- 軸: x 軸ラベル (bucket 名) のみ。y 軸数値は bar 上の数字で代替 (簡素)
- color: `c-peach` palette (`var(--peach)`) で塗る (compact = 注意系を peach で)
- hover: `data-tip="histogram"` で「`0 compacts`: 50 sessions」を出す

### CSS 設計 (template.html `<style>`)

`/* subagent failure weekly trend (Issue #60 / B3) */` ブロック直後に追加。

```css
/* permission breakdown table (Issue #61 / A2) */
.perm-table {
  width: 100%; border-collapse: collapse; font-size: 12px;
  font-family: var(--ff-mono);
}
.perm-table th {
  text-align: left; color: var(--ink-faint); font-weight: 500;
  padding: 6px 8px; border-bottom: 1px solid var(--line);
}
.perm-table th.num, .perm-table td.num {
  text-align: right; font-variant-numeric: tabular-nums;
}
.perm-table tbody tr { border-bottom: 1px solid var(--line-faint, rgba(255,255,255,0.04)); }
.perm-table tbody tr:hover { background: var(--bg-panel-2); }
.perm-table td { padding: 5px 8px; color: var(--ink); }
.perm-table td.name { color: var(--mint); }   /* skill = mint */
.perm-table#quality-perm-subagent td.name { color: var(--coral); }  /* subagent = coral */
.perm-table td.dim { color: var(--ink-faint); }
.perm-table td.rate-warn { color: var(--peach); font-weight: 500; }
.perm-table .empty { text-align: center; color: var(--ink-faint); padding: 24px 0; }
.data-tip[data-kind="perm-skill"] { border-left-color: var(--mint); }
.data-tip[data-kind="perm-subagent"] { border-left-color: var(--coral); }

/* compact density (Issue #61 / A3) */
.compact-grid {
  display: grid; grid-template-columns: minmax(280px, 1fr) minmax(320px, 2fr);
  gap: 24px; align-items: start;
}
@media (max-width: 720px) {
  .compact-grid { grid-template-columns: 1fr; }
}
.compact-hist {
  width: 100%; max-width: 360px; aspect-ratio: 360 / 180;
}
.compact-hist svg { width: 100%; height: 100%; display: block; }
.compact-hist .bar { fill: var(--peach); }
.compact-hist .bar:hover, .compact-hist .bar:focus-visible { fill: var(--peach-bright, var(--peach)); }
.compact-hist .axis-label { fill: var(--ink-faint); font-size: 10px; font-family: var(--ff-mono); }
.compact-hist .bar-num { fill: var(--ink); font-size: 10px; font-family: var(--ff-mono); text-anchor: middle; }
.worst-table { width: 100%; border-collapse: collapse; font-size: 12px; font-family: var(--ff-mono); }
.worst-table th {
  text-align: left; color: var(--ink-faint); font-weight: 500;
  padding: 6px 8px; border-bottom: 1px solid var(--line);
}
.worst-table th.num, .worst-table td.num {
  text-align: right; font-variant-numeric: tabular-nums;
}
.worst-table tbody tr { border-bottom: 1px solid var(--line-faint, rgba(255,255,255,0.04)); }
.worst-table tbody tr:hover { background: var(--bg-panel-2); }
.worst-table td { padding: 5px 8px; color: var(--ink); }
.worst-table td.sid { color: var(--peach); font-family: var(--ff-mono); }
.worst-table td.proj { color: var(--ink-faint); }
.worst-table .empty { text-align: center; color: var(--ink-faint); padding: 16px 0; }
.data-tip[data-kind="histogram"] { border-left-color: var(--peach); }
.data-tip[data-kind="worst-session"] { border-left-color: var(--peach); }
```

### JS renderers

`renderSubagentFailureTrend` の **直後** に並べる。`loadAndRender()` 末尾に
3 行 call:

```javascript
// ---- A2 permission breakdowns (Issue #61) ----
renderPermissionSkillBreakdown(data.permission_prompt_skill_breakdown);
renderPermissionSubagentBreakdown(data.permission_prompt_subagent_breakdown);
// ---- A3 compact density (Issue #61) ----
renderCompactDensity(data.compact_density);
```

3 renderer すべて **page-scoped early-out** (`activePage !== 'quality'` で no-op)。

```javascript
function renderPermissionSkillBreakdown(items) {
  if (document.body.dataset.activePage !== 'quality') return;
  const tbody = document.querySelector('#quality-perm-skill tbody');
  const sub = document.getElementById('quality-perm-skill-sub');
  if (!tbody) return;
  const list = Array.isArray(items) ? items : [];
  if (list.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty">permission prompt なし</td></tr>';
  } else {
    tbody.innerHTML = list.map(it => {
      const c = it.prompt_count || 0;
      const inv = it.invocation_count || 0;
      const rate = it.permission_rate || 0;
      const rateClass = rate >= 0.5 ? 'num rate-warn' : 'num';
      const al = it.skill + ': ' + c + ' prompts / ' + inv + ' invocations (' + Math.round(rate * 100) + '%)';
      return '<tr data-tip="perm-skill" data-name="' + esc(it.skill) +
        '" data-c="' + c + '" data-inv="' + inv + '" data-rate="' + rate +
        '" tabindex="0" role="row" aria-label="' + esc(al) + '">' +
        '<td class="name">' + esc(it.skill) + '</td>' +
        '<td class="num">' + fmtN(c) + '</td>' +
        '<td class="num dim">' + fmtN(inv) + '</td>' +
        '<td class="' + rateClass + '">' + Math.round(rate * 100) + '%</td>' +
        '</tr>';
    }).join('');
  }
  if (sub) sub.textContent = list.length + ' skill(s)';
}

function renderPermissionSubagentBreakdown(items) {
  if (document.body.dataset.activePage !== 'quality') return;
  const tbody = document.querySelector('#quality-perm-subagent tbody');
  const sub = document.getElementById('quality-perm-subagent-sub');
  if (!tbody) return;
  const list = Array.isArray(items) ? items : [];
  if (list.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty">permission prompt なし</td></tr>';
  } else {
    tbody.innerHTML = list.map(it => {
      const c = it.prompt_count || 0;
      const inv = it.invocation_count || 0;
      const rate = it.permission_rate || 0;
      const rateClass = rate >= 0.5 ? 'num rate-warn' : 'num';
      const al = it.subagent_type + ': ' + c + ' prompts / ' + inv + ' invocations (' + Math.round(rate * 100) + '%)';
      return '<tr data-tip="perm-subagent" data-name="' + esc(it.subagent_type) +
        '" data-c="' + c + '" data-inv="' + inv + '" data-rate="' + rate +
        '" tabindex="0" role="row" aria-label="' + esc(al) + '">' +
        '<td class="name">' + esc(it.subagent_type) + '</td>' +
        '<td class="num">' + fmtN(c) + '</td>' +
        '<td class="num dim">' + fmtN(inv) + '</td>' +
        '<td class="' + rateClass + '">' + Math.round(rate * 100) + '%</td>' +
        '</tr>';
    }).join('');
  }
  if (sub) sub.textContent = list.length + ' subagent type(s)';
}

function renderCompactDensity(payload) {
  if (document.body.dataset.activePage !== 'quality') return;
  const histRoot = document.getElementById('quality-compact-hist');
  const worstTbody = document.querySelector('#quality-compact-worst tbody');
  const sub = document.getElementById('quality-compact-sub');
  const data = (payload && typeof payload === 'object') ? payload : {};
  const hist = (data.histogram && typeof data.histogram === 'object') ? data.histogram : {};
  const worst = Array.isArray(data.worst_sessions) ? data.worst_sessions : [];

  // histogram SVG (4 bars)
  const buckets = ['0', '1', '2', '3+'];
  const counts = buckets.map(b => Number(hist[b] || 0));
  const maxC = Math.max(1, ...counts);
  const W = 360, H = 180, padL = 28, padR = 12, padT = 18, padB = 30;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const barW = innerW / buckets.length * 0.6;
  const gap = (innerW - barW * buckets.length) / (buckets.length + 1);

  if (histRoot) {
    let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Compact 回数 histogram (per session)">';
    buckets.forEach((b, i) => {
      const c = counts[i];
      const h = (c / maxC) * innerH;
      const x = padL + gap + i * (barW + gap);
      const y = padT + innerH - h;
      const al = b + ' compact(s): ' + c + ' session(s)';
      svg += '<rect class="bar" x="' + x + '" y="' + y + '" width="' + barW +
        '" height="' + h + '" data-tip="histogram" data-bucket="' + esc(b) +
        '" data-c="' + c + '" tabindex="0" role="img" aria-label="' + esc(al) + '"/>';
      svg += '<text class="bar-num" x="' + (x + barW / 2) + '" y="' + (y - 4) + '">' + c + '</text>';
      svg += '<text class="axis-label" x="' + (x + barW / 2) + '" y="' + (H - 10) +
        '" text-anchor="middle">' + esc(b) + '</text>';
    });
    svg += '</svg>';
    histRoot.innerHTML = svg;
  }

  // worst sessions table
  if (worstTbody) {
    if (worst.length === 0) {
      worstTbody.innerHTML = '<tr><td colspan="3" class="empty">compact なし</td></tr>';
    } else {
      worstTbody.innerHTML = worst.map(w => {
        const sid = w.session_id || '';
        const sidShort = sid.length > 8 ? sid.slice(0, 8) : sid;  // 短縮表示
        const proj = w.project || '';
        const c = w.count || 0;
        // P3 反映: 空 project は (unknown) ラベルで明示。空セルだと「データ欠損」と
        // 「project が空文字」が見分けつかなくなる UX 問題を回避。
        const projCell = proj === '' ? '<span class="dim">(unknown)</span>' : esc(proj);
        const projForLabel = proj === '' ? 'unknown' : proj;
        const al = sidShort + ' (' + projForLabel + '): ' + c + ' compacts';
        return '<tr data-tip="worst-session" data-sid="' + esc(sid) + '" data-proj="' + esc(proj) +
          '" data-c="' + c + '" tabindex="0" role="row" aria-label="' + esc(al) + '">' +
          '<td class="sid" title="' + esc(sid) + '">' + esc(sidShort) + '</td>' +
          '<td class="proj">' + projCell + '</td>' +
          '<td class="num">' + fmtN(c) + '</td>' +
          '</tr>';
      }).join('');
    }
  }

  if (sub) {
    const total = counts.reduce((a, b) => a + b, 0);
    sub.textContent = total + ' session(s) tracked';
  }
}
```

### tooltip 拡張 (`dtipBuild()` 分岐 4 件追加)

```javascript
if (kind === 'perm-skill' || kind === 'perm-subagent') {
  const name = el.getAttribute('data-name') || '';
  const c = el.getAttribute('data-c') || '0';
  const inv = el.getAttribute('data-inv') || '0';
  const rate = parseFloat(el.getAttribute('data-rate') || '0');
  return {
    kind,
    html: '<span class="ttl">' + esc(name) + '</span>' +
          '<span class="lbl">prompts</span><span class="val">' + c + '</span>' +
          '<span class="lbl">invocations</span><span class="val">' + inv + '</span>' +
          '<span class="lbl">rate</span><span class="val">' + Math.round(rate * 100) + '%</span>'
  };
}
if (kind === 'histogram') {
  const bucket = el.getAttribute('data-bucket') || '';
  const c = el.getAttribute('data-c') || '0';
  return {
    kind: 'histogram',
    html: '<span class="ttl">' + esc(bucket) + ' compact(s)</span>' +
          '<span class="lbl">sessions</span><span class="val">' + c + '</span>'
  };
}
if (kind === 'worst-session') {
  const sid = el.getAttribute('data-sid') || '';
  const proj = el.getAttribute('data-proj') || '';
  const c = el.getAttribute('data-c') || '0';
  return {
    kind: 'worst-session',
    html: '<span class="ttl">' + esc(sid) + '</span>' +
          '<span class="lbl">project</span><span class="val">' + esc(proj) + '</span>' +
          '<span class="lbl">compacts</span><span class="val">' + c + '</span>'
  };
}
```

### Page-scoped early-out + hashchange 連携

#58/#59/#60 で確立済み (`body[data-active-page]` 判定 + main IIFE の hashchange
listener)。本 PR で追加実装不要。3 renderer に early-out を入れるだけ。

## 🧪 TDD テスト計画

### 新規 server unit tests (`tests/test_friction_signals.py`)

```python
class TestSubagentInvocationInterval:
    # 2-P3 反映: subagent_metrics.subagent_invocation_interval(ev) の単体テスト
    def test_subagent_start_uses_end_timestamp_interval(self):
        # event_type="subagent_start", duration_ms=10_000, ts=T
        # → (T - 10s, T) interval を返す
        pass
    def test_subagent_lifecycle_only_uses_start_timestamp_interval(self):
        # event_type="subagent_lifecycle_start", duration_ms=10_000, ts=T
        # → (T, T + 10s) interval を返す (lifecycle-only invocation 用)
        pass
    def test_no_duration_returns_point_interval(self):
        # duration_ms 不在 → (T, T) point interval (start == end)
        pass
    def test_both_hooks_invocation_uses_subagent_start_representative(self):
        # usage_invocation_events() 経由で代表 ev = subagent_start のとき、
        # interval は [end - duration, end] になる (ev.timestamp = 終了時刻)
        pass
    def test_invalid_timestamp_returns_zero_or_skipped_interval(self):
        # timestamp="" or 不正な ISO → (0.0, 0.0) を返す or 呼び出し側が skip する
        # 仕様 (どちらにしても caller 側の attribution loop で broken candidate にならないこと)
        pass


class TestPermissionLinkAlgorithm:
    def test_no_notification_returns_empty_breakdowns(self): pass
    def test_notification_without_candidates_is_dropped(self): pass
    def test_skill_in_backward_window_attributed(self):
        # skill_tool ts=t-10s (within 30s) + permission ts=t → skill に帰属
        pass
    def test_skill_outside_window_not_attributed(self):
        # skill_tool ts=t-31s (just outside) + permission ts=t → 帰属しない
        pass
    def test_skill_in_execution_interval_attributed(self):
        # skill_tool with duration_ms=10s, ts=t+5s (= start at t-5s, end at t+5s)
        # permission ts=t → interval covers, 帰属
        pass
    def test_subagent_lifecycle_start_in_interval_attributed(self):
        # subagent_lifecycle_start ts=t-300s, duration_ms=600_000 (10 min subagent)
        # permission ts=t → interval [t-300, t+300] covers
        pass
    def test_subagent_postooluse_end_after_notif_via_interval(self):
        # subagent_start (PostToolUse 由来) ts=t+100s, duration_ms=200_000 (200s 実行)
        # virtual_start = t-100s. permission ts=t → [t-100, t+100] covers → 帰属
        pass
    def test_multiple_candidates_attribute_to_most_recent(self):
        # skill_tool A ts=t-20s, skill_tool B ts=t-5s, permission ts=t
        # → B に帰属 (直近 1 個)
        pass
    def test_skill_and_subagent_disjoint_attribution(self):
        # skill_tool ts=t-10s + subagent ts=t-3s + permission ts=t
        # → subagent に帰属 (直近)、skill には帰属しない
        pass
    def test_permission_and_permission_prompt_unified(self):
        # 1 notif type=permission + 1 notif type=permission_prompt
        # 両方 同じ skill に帰属するパターンで prompt_count=2
        pass
    def test_user_slash_command_not_a_candidate(self):
        # user_slash_command ts=t-5s (only candidate) + permission ts=t
        # → 帰属しない (slash command は対象外)
        pass
    def test_different_session_not_linked(self):
        # skill_tool session=A + permission session=B
        # → 帰属しない (cross-session 結合しない)
        pass
    def test_subagent_lifecycle_only_invocation_uses_start_timestamp_as_interval_start(self):
        # P1 反映: lifecycle のみ発火 invocation (subagent_start 不在) で
        # interval が [lifecycle.ts, lifecycle.ts + duration_ms/1000] になることを pin。
        # permission ts=lifecycle.ts + 60s, duration_ms=120_000 → covers (帰属)
        pass
    def test_subagent_both_hooks_invocation_uses_end_timestamp(self):
        # P1 反映: 両 hook 発火 invocation (1 秒以内 merge) で usage_invocation_events
        # は subagent_start を代表に選ぶため ev.timestamp=終了時刻、interval が
        # [end - duration, end] になることを pin。permission ts=end-30s で covers
        pass

class TestPermissionBreakdownsAggregate:
    def test_invocation_count_matches_total_skill_tool(self):
        # skill X が 5 回呼ばれて 2 回 permission → invocation_count=5, prompt_count=2
        pass
    def test_invocation_count_matches_aggregate_subagent_metrics(self):
        # subagent Y の invocation_count が aggregate_subagent_metrics(events)[Y].count と一致
        pass
    def test_permission_rate_calculation(self):
        # prompt=2, invocation=8 → rate=0.25
        pass
    def test_rate_can_exceed_one(self):
        # 1 invocation で 2 permission → rate=2.0 (clamp しない)
        pass
    def test_top_n_cap(self):
        # 12 skill が prompt を持つとき返り値は 10 件
        pass
    def test_sort_by_prompt_count_desc_then_name_asc(self):
        # prompt=3,3,2 / name=alpha,beta,gamma → [3,alpha], [3,beta], [2,gamma]
        pass
    def test_zero_prompt_skill_not_in_output(self):
        # skill X: invocation=10, prompt=0 → 出力に含まれない
        pass
    def test_subagent_attribution_count_matches_metrics_count(self):
        # P2 反映: drift guard。type 単位の `invocation_count` 合計が
        # `aggregate_subagent_metrics(events)[name].count` と常に一致。
        # `usage_invocation_events` (帰属候補) と `aggregate_subagent_metrics`
        # (count 取得) の二重経路 drift を pin
        pass
    def test_skill_and_subagent_prompt_counts_sum_le_total_notifications(self):
        # 2-Q1 反映: 合算 invariant。1 notification は skill OR subagent の
        # 1 候補にのみ帰属 (= disjoint) なので、全 type の合算は notification 数を
        # 超えない。orphan permission (= 帰属候補なし) があると等号未満。
        # `sum(skill[i].prompt_count) + sum(subagent[j].prompt_count) <=
        #  count(notification with type ∈ _PERMISSION_NOTIFICATION_TYPES)` を pin。
        # Issue 本文の "table 合算可能性" を invariant として保証する
        pass

class TestCompactDensity:
    def test_empty_events_returns_zero_buckets(self): pass
    def test_session_with_zero_compacts_in_bucket_zero(self):
        # session_start のみの session → histogram["0"] += 1
        pass
    def test_session_with_one_compact_in_bucket_one(self): pass
    def test_session_with_two_compacts_in_bucket_two(self): pass
    def test_session_with_three_compacts_in_bucket_3plus(self): pass
    def test_session_with_five_compacts_in_bucket_3plus(self):
        # boundary: 3 / 4 / 5 すべて "3+"
        pass
    def test_orphan_session_excluded_from_histogram(self):
        # compact_start のみで session_start が無い session → histogram に含まれない
        pass
    def test_orphan_session_included_in_worst_sessions(self):
        # 上の orphan が worst_sessions に lifecycle 数で乗る
        pass
    def test_worst_sessions_sorted_count_desc_sid_asc(self): pass
    def test_worst_sessions_top_n_cap(self):
        # 11 session が compact 持つとき worst_sessions は 10 件
        pass
    def test_worst_session_uses_last_seen_project(self):
        # 同 session で project="A" → "B" の順に compact_start → worst の project は "B"
        pass
    def test_histogram_keys_are_strings(self):
        # "0" / "1" / "2" / "3+" が string であることを pin
        pass
    def test_histogram_keys_always_present(self):
        # 観測 0 でも 4 キーすべて 0 で出力 (browser 側 defensive 不要)
        pass

class TestBuildDashboardDataIncludesFrictionFields:
    def test_permission_prompt_skill_breakdown_key_present(self): pass
    def test_permission_prompt_subagent_breakdown_key_present(self): pass
    def test_compact_density_key_present(self): pass
    def test_empty_events_returns_safe_defaults(self): pass
    def test_constant_PERMISSION_LINK_WINDOW_SECONDS_value(self):
        # PERMISSION_LINK_WINDOW_SECONDS = 30 を pin (将来変更したら明示的に test 更新)
        pass
```

### 新規 template tests (`tests/test_friction_template.py`)

```python
class TestQualityPagePermissionPanels:
    def test_quality_section_has_perm_skill_panel(self): pass
    def test_quality_section_has_perm_subagent_panel(self): pass
    def test_quality_section_has_compact_panel(self): pass
    def test_perm_skill_table_has_thead_columns(self):
        # Skill / Prompts / Invocations / Rate
        pass
    def test_perm_subagent_table_has_thead_columns(self):
        # Subagent / Prompts / Invocations / Rate
        pass
    def test_compact_grid_has_hist_and_worst_table(self): pass
    def test_template_has_permission_skill_renderer(self): pass
    def test_template_has_permission_subagent_renderer(self): pass
    def test_template_has_compact_density_renderer(self): pass
    def test_loadAndRender_invokes_friction_renderers(self):
        # 3 関数すべてが loadAndRender 末尾で呼ばれている
        pass
    def test_perm_skill_renderer_has_page_scoped_early_out(self): pass
    def test_perm_subagent_renderer_has_page_scoped_early_out(self): pass
    def test_compact_density_renderer_has_page_scoped_early_out(self): pass
    def test_perm_skill_panel_uses_mint(self): pass
    def test_perm_subagent_panel_uses_coral(self): pass
    def test_compact_panel_uses_peach(self): pass
    def test_compact_hist_uses_svg(self):
        # renderCompactDensity 内に <svg viewBox= / <rect class="bar" が含まれる
        pass
    def test_dtipbuild_has_perm_skill_branch(self): pass
    def test_dtipbuild_has_perm_subagent_branch(self): pass
    def test_dtipbuild_has_histogram_branch(self): pass
    def test_dtipbuild_has_worst_session_branch(self): pass
    def test_worst_session_unknown_project_shown_as_unknown_label(self):
        # P3 反映: project="" の worst_session が UI で `(unknown)` と表示される。
        # renderer body 内に `(unknown)` の literal が含まれることを grep で確認。
        # 実 render テストは fixture 依存になるため、template 構造テストとして
        # `proj === ''` 分岐の存在を pin する形で代替
        pass
```

### 既存テストへの影響 (regression)

- `tests/test_dashboard.py:TestBuildDashboardData` 系 — return dict キー集合
  assert (もしあれば) に 3 キー追加
- `tests/test_quality_template.py` (#60 で導入) — 既存 percentile/trend test は
  影響なし。新 panel は additive
- `tests/test_dashboard_router.py:test_non_overview_pages_are_placeholders` —
  #60 で `quality` は既に loop から外れている (`['surface']` のみ)。本 PR で
  追加変更なし
- `tests/test_export_html.py` — `window.__DATA__` round-trip のみ → 影響なし

### テスト数の見込み

- 新規 server unit: subagent_invocation_interval (~5 / 2-P3 反映) +
  link algorithm (~14 / +P1 で 2 件) + breakdown aggregate (~9 / +P2 +2-Q1 で
  2 件) + compact density (~13) + integration (~5) = **~46 テスト**
- 新規 template 構造: **~21 テスト** (+P3 で 1 件)
- 既存 test の小修正: 1〜2 件想定 (dict キー集合系)
- **合計: 現状 ~702 + 約 67 = ~769 tests / 全 pass 想定**

## 📦 実装ステップ (TDD red→green→refactor)

> **並行可ノート**: `memory/MEMORY.md` index entry の更新と
> `docs/spec/dashboard-api.md` 追記は **Phase 1 RED と並行で書き始めて OK**。
> 設計は本 plan で確定済み。CLAUDE.md "Dogfood workflow doc changes" 観点で
> ドキュメント整備を本 PR 完結に揃えたい。

### Phase 1: subagent interval helper + 帰属 algorithm + skill/subagent breakdown (RED → GREEN)

1. **RED**: `tests/test_friction_signals.py` 新規 + `TestPermissionLinkAlgorithm`
   (~14 tests / +P1 で 2 件) + `TestPermissionBreakdownsAggregate`
   (~8 tests / +P2 で 1 件) + `TestSubagentInvocationInterval` (~5 tests, Q2 で
   subagent_metrics.py 側 helper 単体テスト) を書く
2. **GREEN-A** (Q2 反映): `subagent_metrics.py` に
   `subagent_invocation_interval(ev) -> (start_ts, end_ts)` 追加
3. **GREEN-B**: `dashboard/server.py` に
   - `PERMISSION_LINK_WINDOW_SECONDS = 30` 定数
   - `_skill_event_interval(ev)` private helper (skill_tool 用)
   - `_attribute_permission(notif, skill_candidates, subagent_candidates)` private helper
   - `aggregate_permission_breakdowns(events, top_n)` 公開関数
   - `from subagent_metrics import subagent_invocation_interval` import 追加
4. **REFACTOR**: 帰属 algorithm の private helper を整理 (subagent semantics は
   subagent_metrics 側、skill semantics + attribution policy は server 側)

### Phase 2: compact density (RED → GREEN)

1. **RED**: `TestCompactDensity` を書く (~13 tests)
2. **GREEN**: `dashboard/server.py` に `aggregate_compact_density(events, top_n)`
   実装。session pool は session_start.session_id 集合、worst_sessions は
   全 compact_start から groupby

### Phase 3: build_dashboard_data 統合 (RED → GREEN)

1. **RED**: `TestBuildDashboardDataIncludesFrictionFields` (~5 tests)
2. **GREEN**: `build_dashboard_data` の return dict に 3 キー追加:
   - `"permission_prompt_skill_breakdown": b["skill"]`
   - `"permission_prompt_subagent_breakdown": b["subagent"]`
   - `"compact_density": aggregate_compact_density(events)`

### Phase 4: Quality ページ DOM (RED template tests → GREEN)

1. **RED**: `tests/test_friction_template.py` 新規 (~20 tests)
2. **GREEN**: `<section data-page="quality">` の trend panel 後に 3 panel 追加
3. **REFACTOR**: panel 順序確認 (percentile → trend → A2-skill → A2-subagent → A3)

### Phase 5: CSS / JS renderer (visual smoke)

1. CSS 追加 (perm-table / compact-grid / compact-hist / worst-table の 4 block)
2. JS: 3 renderer + dtipBuild 4 分岐 を追加。`loadAndRender()` 末尾で 3 行 call
3. **実機 smoke**:
   - `python3 dashboard/server.py` 起動 + 自分の usage.jsonl
   - `#/quality` で 5 panel (percentile / trend / perm-skill / perm-subagent /
     compact) が縦に並ぶこと
   - perm tables: `permission_rate >= 50%` で peach 色強調
   - compact histogram: 4 bar + 数値ラベル + axis label が見える
   - worst sessions: session_id 8 文字短縮 + project + count
   - hover で tooltip (4 種すべて)
   - keyboard tab で行 / bar / cell に focus し tooltip が出る
   - SSE refresh で再描画
   - `#/` 起動 → `#/quality` navigate で即時描画 (hashchange 連携)
   - `python3 reports/export_html.py --output /tmp/static.html` で static export
     にも反映
   - **edge case 確認**:
     - permission 0 件のデータ → empty state ("permission prompt なし")
     - compact 0 件のデータ → histogram 4 bar すべて 0 で描画 / worst empty state
     - 長時間 subagent (5+ 分) で内部 permission が出ているデータ → interval-cover で
       attributed されること
     - **P4 反映**: `permission_rate > 1.0` (1 invocation で 2+ permission) を
       含むデータ → rate cell に `200%` 等が表示され、`rate-warn` peach 色強調が
       出ること、cell 幅で改行しないこと
     - **P3 反映**: orphan session (compact_start のみで session_start 無い) の
       worst_session 行で project セルに `(unknown)` ラベルが peach-faint で出ること
   - mobile width (< 720px) で `.compact-grid` が縦並びに崩れること
   - **Q3 反映 perf smoke**: `time python3 dashboard/server.py` の起動時間 +
     `curl -s http://localhost:<port>/api/data | wc -c` のレスポンス時間 / バイト数が
     #60 merge 時点 (= `git checkout 538a194 -- dashboard/server.py dashboard/template.html`
     等で計測) と比べて **顕著に伸びていない** こと (= 線形コストの想定が破れていない)

### Phase 6: docs

1. **`docs/spec/dashboard-api.md`** に 2 セクション additive:
   - `## permission_prompt_*_breakdown (Issue #61, v0.7.0〜)` —
     skill / subagent の 2 schema、`PERMISSION_LINK_WINDOW_SECONDS = 30` 定数、
     execution-window + backward-fallback の 2 段階 algorithm 説明、
     単一帰属ポリシー、user_slash_command 対象外を明記
   - `## compact_density (Issue #61, v0.7.0〜)` — histogram bucket 仕様 (`0/1/2/3+`)、
     session pool 定義 (session_start ベース)、orphan 扱い、worst_sessions 仕様
   - **2-Q2 反映**: spec doc は **API 形状のみ** に閉じる。`subagent_invocation_interval`
     helper の責務分担 (= subagent_metrics.py 側に置いた根拠) は spec doc では
     言及せず、`memory/friction_signals.md` と subagent_metrics.py の docstring に
     集約する。これは spec doc の関心事 = schema 規約 / 実装責務分担は実装ドキュメント
     という分離を維持するため
2. **`CLAUDE.md`** — 「ダッシュボード複数ページ構成」表の Quality 行は
   「A2 / A3 / A5 / B3」のまま (本 PR で A2 + A3 を追加 = 表に変更不要)。
   `data/usage.jsonl のイベント形式` セクションは notification の例既存のため変更不要
3. **`MEMORY.md` 1 行 pointer 追加** + `memory/friction_signals.md` 新規:
   - 命名規約 (`permission_prompt_<X>_breakdown` / `compact_density`)
   - `PERMISSION_LINK_WINDOW_SECONDS = 30` の根拠 (Issue 本文)
   - **Q1 反映 fine-tune 観測指標**: 実機で **orphan ratio** (= attribution 失敗
     permission の割合 = どの skill/subagent にも帰属できなかった prompt 数 /
     全 permission 数) を観測し、定常的に > 30% なら window が短すぎ (= candidate
     event が観測されていない経路を見落としている) を疑い拡大、< 5% なら過剰に
     広く取りすぎていないかを検討。実機運用で記録するメトリクスは別途 ad-hoc 集計
   - execution-window + backward-fallback の 2 段階 algorithm の意図
     (long-running subagent への構造防御)
   - 単一帰属 (skill / subagent disjoint) の根拠
   - histogram bucket 境界 (3 以上は "3+" / orphan 除外)
   - **Q2 反映**: `subagent_invocation_interval` を `subagent_metrics.py` 側に置く
     責務分担。dashboard/server.py は domain logic を持たず import するだけ
     (#60 2-Q1 教訓踏襲)
4. **`dashboard/server.py`** — 各新関数の docstring に意図を pin:
   - `aggregate_permission_breakdowns`: execution-window vs backward 経路の
     順序 + 単一帰属 + user_slash_command 対象外 + rate clamp なし
   - `aggregate_compact_density`: session pool 定義 + orphan 扱い + bucket 境界

### Phase 7: PR

ブランチ: `feature/61-friction-signals` (#57/#58/#59/#60 命名規則踏襲)
PR タイトル候補: `feat(dashboard): friction signals — permission/skill + compact density (#61)`

#### PR 粒度判断

**recommendation: A2 + A3 一括 PR**

両 viz は同じ Quality ページにマウントされ、テストファイル (`test_friction_signals.py` /
`test_friction_template.py`) も共通、build_dashboard_data 統合 phase も合流。
分割 review 価値は低い。

**condition for splitting** (Issue #59 P6 / #60 と同型のトリガー):
- `PERMISSION_LINK_WINDOW_SECONDS` の値変更要求
- 帰属 algorithm の変更要求 (execution-window やめて backward-only にする等)
- histogram bucket 境界の変更要求 (`3+` を `4+` にする等)
- session pool 定義の変更要求 (orphan を 0 bucket に含める等)

これらは下流 (template / docs / test) を全 trigger するため、回数を待たず即分割
が経済的。逆に CSS / DOM / 文言など stylistic な指摘は何 round 入っても一括 PR を
継続。

PR 本文:
- 親 issue #48 / 当該 issue #61 / 前提 PR #57 (shell), #60 (percentile/trend)
- A2 / A3 schema 例 + 帰属 algorithm の決定背景
- execution-window + backward-fallback の根拠 (long-running subagent 対応)
- 単一帰属ポリシーの根拠 (table 合算可能性)
- 実機スクショ: 5 panel quality page / 4 種 tooltip / mobile 縦並び

base branch: **`v0.7.0`** (Issue #57/#58/#59/#60 と同じ)

## 🚫 Out of Scope

issue 本文記載に加え、以下も本 PR では扱わない:

- **session 単位 permission_rate aggregate** (issue 明記)
- **compact trigger (`auto` vs `manual`) 別集計** (issue 明記)
- **`fewer-permission-prompts` skill 自体の自動連携** (issue 明記)
- **session timeline 全表示インタラクティブビュー** (issue 明記)
- **archive 込みの集計** (issue 明記)
- **`PERMISSION_LINK_WINDOW_SECONDS` の調整 UI** (= URL param / settings)
- **`reports/summary.py` への friction signals 反映** (terminal レポートは avg /
  failure_rate のままで足りる。後続 issue 候補)
- **`reports/export_html.py` で window 調整 UI** (本 PR では schema 固定)
- **orphan_count の schema 露出** (本 PR は捨てる。実観測値を見て後続判断)
- **permission notification の重複 dedup** (Notification は通常 1 prompt 1 件で
  発火するので構造的に必要なし。観測値で重複が出たら別 issue)
- **rate >= 100% の clamp / cap** (clamp しない方針を help-pop に明記)

## 🧷 リスクと不確実性

| リスク | 影響 | 対策 |
|---|---|---|
| 帰属 algorithm が long-running subagent を取りこぼす | data 精度 | execution-window で interval-cover を判定。`subagent_lifecycle_start` (= 開始時刻) と `subagent_start` (= 終了時刻) の意味差を algorithm で明示分岐 (擬似コード参照) |
| `PERMISSION_LINK_WINDOW_SECONDS = 30` が短すぎ / 長すぎ | data 精度 | 定数 export して将来 fine-tune 可能。本 PR では実機データで qualitative 評価。test で値を pin (`test_constant_PERMISSION_LINK_WINDOW_SECONDS_value`) し、変更時は明示的に test 更新を強制 |
| 単一帰属で「両方原因」のケースを 1 票しか帰属できない | data 精度 | 直近 1 個ポリシーは Issue 本文「安全側」明記。skill table と subagent table の合算で session 全体 prompt 数が出せる (= disjoint で合算可能) のがメリット。両方カウントすると合算で重複 |
| `user_slash_command` を対象外にすることで skill table の prompt が漏れる | data 精度 | Issue 本文「`skill_tool` / `subagent_start`」明記。slash command は permission 起因にならない (slash 入力は LLM への prefix で tool 実行を伴わない) ので構造的に OK |
| compact orphan session を histogram から除外するロジックが直感的でない | UX | help-pop に「session_start ベース」明記しない (UI 詰めすぎ)。spec doc / docstring に詳細記述 |
| histogram bucket "3+" の境界感覚 (3 / 5 / 10 を区別したい) | UX | worst_sessions table が補完 (top 10 で具体 count を見せる)。histogram は分布の山を見るのが目的で、heavy hitter の絶対数は worst_sessions で確認 |
| Quality ページが 5 panel で縦長 | scroll 負荷 | 各 panel は collapsed help-pop で密度抑え目。本 PR で出る情報は全て Quality 主題に整合しているので panel 数増は許容。後続 issue で panel collapse / pin UI を検討する余地はあるが本 PR scope 外 |
| permission_rate > 1.0 の clamp なしで「100% 超え」が読者を混乱させる | UX | help-pop に「1 invocation で複数 prompt は normal」と明記。peach 色強調 (rate-warn) で目立たせる方針 |
| worst_sessions の `session_id` がブラウザ表示で長く改行する | UI | 8 文字短縮表示 + `title` 属性で full session_id を tooltip 表示 |
| `aggregate_permission_breakdowns` が events を 2 周してしまう (notification + skill_tool で 2 周) | perf | 180 日 hot tier で events 数は数万オーダー。2 周しても線形コスト内、許容範囲 |
| 既存 `test_dashboard.py:TestBuildDashboardData` の dict キー集合 assert に 3 キー追加で破壊 | regression | grep で事前確認、必要なら additive にキーを追加するだけ |

## ✔️ Definition of Done

- [ ] `tests/test_friction_signals.py` の新規 ~46 unit tests 全 pass
- [ ] `tests/test_friction_template.py` の新規 ~21 構造テスト全 pass
- [ ] 既存 `tests/test_dashboard*.py` / `test_quality_template.py` 全 pass (regression)
- [ ] **全 ~769 tests pass**
- [ ] 実機: 自分の usage.jsonl で Quality に 5 panel が並び、A2 2 table と A3
      histogram + worst table が描画される
- [ ] hover tooltip が 4 種すべて (perm-skill / perm-subagent / histogram /
      worst-session) で正しく出る
- [ ] keyboard tab で行 / bar / cell に focus + tooltip
- [ ] SSE refresh / `#/` → `#/quality` navigate / static export いずれでも描画
- [ ] mobile width (< 720px) で `.compact-grid` が縦並びになる
- [ ] **edge case 確認**:
  - [ ] permission 0 件で empty state
  - [ ] compact 0 件で histogram 4 bar すべて 0 / worst empty state
  - [ ] 長時間 subagent の interval-cover 帰属が正しく動く
  - [ ] cross-session 帰属がない (skill A session=X / notif session=Y で帰属しない)
- [ ] `PERMISSION_LINK_WINDOW_SECONDS = 30` 定数が dashboard/server.py に export
      され test で pin
- [ ] `docs/spec/dashboard-api.md` に 2 セクション (permission breakdowns +
      compact density) 追加
- [ ] `memory/friction_signals.md` 新規作成 (~25 行)
- [ ] `MEMORY.md` 1 行 pointer index 追加
- [ ] PR `feature/61-friction-signals` を `v0.7.0` ブランチ向けに作成

## 📦 変更ファイル一覧 (見込み)

- `subagent_metrics.py` — `subagent_invocation_interval(ev)` helper 追加
  (Q2 反映、~25 行)。aggregate API は破壊しない・additive のみ
- `dashboard/server.py` — `PERMISSION_LINK_WINDOW_SECONDS` 定数 +
  `_skill_event_interval()` + `_attribute_permission()` +
  `aggregate_permission_breakdowns()` + `aggregate_compact_density()` +
  `build_dashboard_data()` に 3 キー追加 + `subagent_invocation_interval` import
  (~140 行追加 / Q2 で 10 行ほど subagent_metrics.py に移管)
- `dashboard/template.html` — Quality section に 3 panel 追加 / CSS 追加 /
  JS renderer 3 個 + dtipBuild 4 分岐 (~290 行追加)
- `tests/test_friction_signals.py` (新規) — server / aggregate unit tests
- `tests/test_friction_template.py` (新規) — template 構造テスト
- `tests/test_dashboard.py` — dict キー集合 assert の追従修正 (数件想定)
- `docs/spec/dashboard-api.md` — `permission_prompt_*_breakdown` +
  `compact_density` セクション (~80 行追加)
- `~/.claude/projects/.../memory/friction_signals.md` (新規) — 帰属 algorithm /
  bucket 境界 / orphan 扱い / 命名規約 (~25 行)
- `~/.claude/projects/.../memory/MEMORY.md` — 1 行 pointer index (topic file への参照)

`reports/summary.py` / `reports/export_html.py` / `subagent_metrics.py` /
`hooks/*.py` / archive 系は触らない。

## 📨 後続 PR への申し送り

- **#62 (Surface)** は別 page (`#/surface`)。干渉しない
- **`reports/summary.py` の friction signals 反映** は別 issue 候補 (terminal で
  permission breakdown / compact density を出す)
- **`PERMISSION_LINK_WINDOW_SECONDS` の fine-tune** — 実機データで permission の
  attribution 率 (= orphan ratio) を見て、30s が短い / 長いか qualitative 評価。
  必要なら別 issue で値変更 + URL param / settings 経路を検討
- **session 単位 permission_rate aggregate** (issue 明記の Out of Scope) は
  「session ごとに何回 permission 聞かれたか」の table に拡張可能。本 PR の
  schema (`permission_prompt_skill_breakdown`) に session_id grouping を追加する
  のではなく、新 schema (`permission_prompt_session_breakdown`) として additive
- **execution-window algorithm の subagent_metrics.py 移管** —
  **2-P1 反映の現状**: subagent invocation の interval 解釈 (=
  `subagent_invocation_interval`) は本 PR で **既に subagent_metrics.py に切り出し
  済み** (Q2)。残り deferred は以下の 3 件:
  - (a) skill_tool 側 `_skill_event_interval(ev)` (dashboard ローカル helper /
    2-P2 反映で「subagent_metrics.py の責務スコープ外なので残置」決定済み)
  - (b) `_attribute_permission(notif, skill_candidates, subagent_candidates)`
    policy (skill / subagent 双方の cross-cutting なので置き場所判断は移管時に必要)
  - (c) `aggregate_permission_breakdowns` 公開関数 (build_dashboard_data の入口)
  
  後続で `reports/summary.py` が permission breakdown を必要としたら、これら 3 つの
  移管先 (= `subagent_metrics.py` に統合 / 新規 `friction_metrics.py` 切り出し /
  dashboard ローカル維持で reports 側にも import) を改めて判断する
- **orphan_count の schema 露出** — 本 PR は捨てているが、実観測値で
  「permission の何 % が attribution 失敗するか」を見たくなったら schema に
  `permission_prompt_orphan_count` を additive で追加
- **forward window / next-gated-invocation algorithm** (codex round 2 反映) —
  実機 `usage.jsonl` の qualitative 観測では、permission notification は
  `Notification(permission) → user approves → tool 実行 → PostToolUse timestamp`
  という時系列を取るため、permission の "直前" 30 秒以内には帰属候補がまだ
  存在しないケースが構造的に多い (本 PR で ship した backward window だけでは
  attribution 率が低い)。Issue #61 本文 spec が「直前 N 秒以内」明記なので本 PR は
  spec 通り v1 ship で確定。後続 issue で **forward window**
  (`[notif_ts, notif_ts + N]`) または **next-gated-invocation algorithm** (window
  不採用、同 session 内 permission 以降の最初の invocation に帰属) を検討。
  schema 互換 (field 名 / 形を維持) で additive に algorithm を進化できる。
  spec doc の "Known limitation" 節 + `memory/friction_signals.md` の "v1 ship
  時点の Known Limitation" 節を参照
