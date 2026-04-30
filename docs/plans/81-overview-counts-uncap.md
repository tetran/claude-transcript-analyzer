# Plan: Issue #81 — Overview KPI counter uncap

## Goal

Issue #81 で指摘された Overview 上段の `Skills` / `Subagents` / `Projects` KPI counter が **TOP_N=10 ranking 配列の length** をそのまま使っているせいで `unique kinds` カウントが 10 で頭打ちする問題を、**ranking cap (top 10) は据え置きのまま、KPI counter だけ全件カウントに切替** することで解消する。スコープは Overview 上段の KPI / lede 数値のみ。ランキング表示・stack/legend・他 TOP_N 流用箇所は触らない。

OWNER の issue comment「ランク表示は対象外」を厳守し、ランキング・stack/legend は完全にノータッチ。

## Critical files

実装で実際に編集が入るファイル:

- `dashboard/server.py` — `build_dashboard_data` 戻り dict に `skill_kinds_total` / `subagent_kinds_total` / `project_total` を additive 追加
- `dashboard/template/scripts/20_load_and_render.js` — `ledeProjects` / `kpi-skills` / `kpi-subs` / `kpi-projs` の v 計算と 3 箇所の help body を新 field 参照に切替 (defensive fallback 付き)
- `dashboard/template/scripts/25_live_diff.js` — `buildLiveSnapshot` 内 `kpi-skills` / `kpi-subs` / `kpi-projs` / `lede.ledeProjects` を同じ defensive fallback で新 field 参照に揃える (loadAndRender との表示乖離防止)
- `docs/spec/dashboard-api.md` — トップレベル形に新 3 field 追記、各 field の集計仕様、`*_ranking` / `project_breakdown` は引き続き 10 件 cap である note を補足
- `tests/test_dashboard.py` — TDD 失敗テストを先に追加 (新 field 期待値 + cap 据え置き回帰防止)
- `tests/test_dashboard_live_diff.py` — Node round-trip で frontend defensive fallback 構造を pin

参照のみで触らないが理解に必要:
- `subagent_metrics.py` (`aggregate_subagent_metrics` の戻り dict キー数 = unique subagent kinds total)
- `reports/export_html.py` (`build_dashboard_data` 経由で自動的に新 field を `window.__DATA__` に乗せる — 改修不要)

## Branch strategy

**release branch + feature branch の 2 段階** (CLAUDE.md release branch model):

1. `git fetch origin && git checkout main && git pull --ff-only origin main` (clean main)
2. **Idempotency check** — `git ls-remote --heads origin v0.7.2`:
   - 出力空 → `git checkout -b v0.7.2 && git push -u origin v0.7.2` (release branch を新規作成)
   - 既に存在 → `git fetch origin v0.7.2 && git checkout v0.7.2 && git pull --ff-only origin v0.7.2` (concurrent v0.7.2 作業が先行している前提で reuse)
3. `git checkout -b feature/81-overview-counts-uncap` from `v0.7.2`
4. PR 作成時 `--base v0.7.2` を明示 (main 直 PR ではない)

ブランチ作成は **実装フェーズで行う**。Plan としては手順を明記するが、planning 中には作らない。release branch を origin に push するのは唯一の non-reversible 操作なので idempotency check で並列衝突を予防する。

PR 本文には以下を明示:
- Issue #81 を `Fixes #81` で link
- Scope: KPI counter (`kpi-skills` / `kpi-subs` / `kpi-projs` / `ledeProjects`) のみ全件カウント。Ranking list は cap 据え置き
- OWNER コメント "ランク表示は対象外" の confirmation
- **意図された UX asymmetry の note**: subtitle (`skillSub`/`subSub` の "top 10 · max ...") と `projSub` ("10 projects · Σ ...") / project stack legend は **ランキング側の表現** なので引き続き 10 件のままになる。これは OWNER の "ランク表示は対象外" 指示通り意図的。「KPI tile = 全件 unique」「ランキング・stack = top 10」の二系統表示が **Issue #81 の合意された設計**
- 静的 HTML upgrade asymmetry: 古い v0.7.1 export は新 frontend assets で開いても引き続き 10-cap を表示する (defensive fallback)。再エクスポートで全件に更新される

## Ordered steps

TDD 規約 (CLAUDE.md): 失敗テスト先 → 実装 → 通過確認 を厳守。

### Phase 1 — Backend: TDD 失敗テスト追加

`tests/test_dashboard.py` の `TestBuildDashboardData` クラス末尾に Issue #81 用テストを追記。

| # | テスト名 | 入力 | 期待値 |
|---|---|---|---|
| 1 | `test_skill_kinds_total_counts_all_unique_skills_beyond_cap` | `skill_tool` 12 種 (s01..s12) を各 1 件 | `len(data["skill_ranking"]) == 10` AND `data["skill_kinds_total"] == 12` |
| 2 | `test_skill_kinds_total_includes_user_slash_command` | `skill_tool: a` 1 件 + `user_slash_command: /b` 1 件 | `data["skill_kinds_total"] == 2` |
| 3 | `test_subagent_kinds_total_counts_all_unique_subagents_beyond_cap` | `subagent_start` 11 種 (Explore + Plan + ...) 各 1 invocation | `len(data["subagent_ranking"]) == 10` AND `data["subagent_kinds_total"] == 11` |
| 4a | `test_subagent_kinds_total_counts_type_not_invocation` | 同一 type `Explore` を 3 invocation across 3 sessions | `data["subagent_kinds_total"] == 1` (= type-level dedup pin) |
| 4b | `test_subagent_kinds_total_one_invocation_paired` | 同 (session, type) で `subagent_start` + `subagent_lifecycle_start` (ts within `INVOCATION_MERGE_WINDOW_SECONDS = 1.0` 秒) | `data["subagent_kinds_total"] == 1` (= invocation-merge window pin) |
| 5 | `test_project_total_counts_all_unique_projects_beyond_cap` | usage 系 events で 13 個の unique project | `len(data["project_breakdown"]) == 10` AND `data["project_total"] == 13` |
| 6 | `test_project_total_excludes_housekeeping_events` | `skill_tool: project=p1` 1 件 + `session_start: project=p2` 1 件 + `notification: project=p3` 1 件 | `data["project_total"] == 1` (p1 のみ) |
| 7 | `test_kinds_totals_zero_for_empty_events` | `[]` | 全 3 field が `0` |
| 8 | `test_skill_ranking_still_capped_at_top_n_after_issue_81` | fixture 1 と同じ | `len(data["skill_ranking"]) == mod.TOP_N` (= 10) |
| 9 | `test_skill_kinds_total_matches_aggregate_skills_when_below_cap` (drift guard) | unique skill 5 種 (cap 未満) | `data["skill_kinds_total"] == len(data["skill_ranking"])` — `aggregate_skills` の filter と新 set の filter が将来 drift しないことを pin |
| 10 | `test_subagent_kinds_total_matches_aggregate_subagents_when_below_cap` (drift guard) | unique subagent type 5 種 (cap 未満) 各 1 invocation | `data["subagent_kinds_total"] == len(data["subagent_ranking"])` — `aggregate_subagent_metrics` 経由の counter と `aggregate_subagents` の sort 後 list 長が一致することを pin |
| 11 | `test_project_total_matches_project_breakdown_when_below_cap` (drift guard) | unique project 5 種 (cap 未満) | `data["project_total"] == len(data["project_breakdown"])` — `_filter_usage_events` の filter 慣習が両者で揃っていることを pin |

確認: `python -m pytest tests/test_dashboard.py::TestBuildDashboardData -k "kinds_total or project_total or capped" -x` で **全件 FAIL** することを確認 (新 field 未実装 / KeyError or 期待値ミス)。

### Phase 2 — Backend: 実装

`dashboard/server.py` の `build_dashboard_data()` を修正:

```python
def build_dashboard_data(events: list[dict]) -> dict:
    usage_events = _filter_usage_events(events)
    permission_breakdowns = aggregate_permission_breakdowns(events)

    # ranking 配列は top_n=TOP_N で cap、KPI counter は全件カウント。
    # filter / dedup は aggregate_skills / aggregate_subagent_metrics /
    # aggregate_projects と一致させる (drift guard)。
    skill_kinds_set: set[str] = set()
    for ev in events:
        if ev.get("event_type") in ("skill_tool", "user_slash_command"):
            name = ev.get("skill", "")
            if name:
                skill_kinds_set.add(name)
    subagent_kinds_total = len(aggregate_subagent_metrics(events))
    project_kinds_set: set[str] = set()
    for ev in usage_events:
        project = ev.get("project", "")
        if project:
            project_kinds_set.add(project)

    return {
        "last_updated": _now_iso(),
        "total_events": len(usage_events),
        "skill_ranking":     aggregate_skills(events),
        "subagent_ranking":  aggregate_subagents(events),
        "skill_kinds_total":     len(skill_kinds_set),       # NEW
        "subagent_kinds_total":  subagent_kinds_total,        # NEW
        "project_total":         len(project_kinds_set),     # NEW
        # ... 既存 field はそのまま ...
    }
```

注意点:
- `skill_kinds_set` は `aggregate_skills` の counter キーセットと同一 event_type / 同一 skip 判定。`aggregate_skills(events, top_n=10**9)` を呼んで `len` を取る cap-bypass 形は TOP_N=10 cap の単一責任が曖昧になるので採用しない。set を直接組む (cap が変動しても counter ロジックと独立)。Phase 1 test #9 で drift guard。
- `subagent_kinds_total` は `aggregate_subagent_metrics(events)` の dict key 数を直接取る — こちらは `top_n` 引数自体を持たない関数 (= sort/cap は呼出側 `aggregate_subagents` で行う) なので、上記 skill 側の cap-bypass 問題は構造的に発生しない。`aggregate_subagent_metrics([])` は空 dict を返す前提 (Phase 1 test #7 で end-to-end pin)。Phase 1 test #10 で drift guard。
- `project_kinds_set` の入力は `usage_events` (`_filter_usage_events()` 後)。`aggregate_projects` の引数と同一。Phase 1 test #11 で drift guard。
- 戻り dict の field 順序: 新 field は `subagent_ranking` の直後に配置する (= **diff readability のため**。consumer は key で読むので semantic 的には不問)。

`docs/spec/dashboard-api.md` のトップレベル形に追記。各 field は **cap 無し** で、aggregation 数式は曖昧さを残さず明示する (Issue #81 を発生させた "length が cap で頭打ち" の confusion を仕様レベルで再発防止):

- `skill_kinds_total: int` — `|{ev.skill : ev.event_type ∈ {skill_tool, user_slash_command} ∧ ev.skill ≠ ""}|`
- `subagent_kinds_total: int` — `len(aggregate_subagent_metrics(events))` (= invocation 単位 dedup 後の unique subagent type 数)
- `project_total: int` — `|{ev.project : ev ∈ _filter_usage_events(events) ∧ ev.project ≠ ""}|`

既存 `skill_ranking` / `subagent_ranking` / `project_breakdown` の節には:
- 「**最大 `TOP_N` 件 (= 10、`dashboard/server.py:TOP_N` 定数で定義)。ランキング表示用 cap であり、全件 unique 数ではない**」を明示
- 「全件 unique 数は `skill_kinds_total` / `subagent_kinds_total` / `project_total` を参照」リンクを追加
- TOP_N 定数を変更したときの drift guard として、`docs/spec/dashboard-api.md` 内の "10" リテラルは仕様 cap (= UI 表示用) のみで、KPI counter には適用されない旨を 1 行で書く

確認: Phase 1 のテストが **すべて PASS** すること。`python -m pytest tests/ -x` で他テスト (test_dashboard_live.py / test_dashboard_sse.py / test_dashboard_template_split.py / test_export_html.py / test_dashboard_router.py 等) に regression が無いこと。

### Phase 3 — Frontend: `20_load_and_render.js` 切替

**L23** (lede project counter):

```javascript
document.getElementById('ledeProjects').textContent =
  (data.project_total != null ? data.project_total : (data.project_breakdown||[]).length);
```

**L29 (kpi-skills)**:

```javascript
{ id: 'kpi-skills', k: 'skills',
  v: (data.skill_kinds_total != null ? data.skill_kinds_total : (data.skill_ranking||[]).length),
  s: 'unique kinds', cls: '',
  helpTtl: 'スキル種別数',
  helpBody: '観測されたスキルの種類数。スキル本体（PostToolUse(Skill)）とユーザー入力のスラッシュコマンド（UserPromptExpansion / Submit）を合算してカウント。' },
```

**L31 (kpi-subs)**:

```javascript
{ id: 'kpi-subs', k: 'subagents',
  v: (data.subagent_kinds_total != null ? data.subagent_kinds_total : (data.subagent_ranking||[]).length),
  s: 'unique kinds', cls: 'c-coral',
  helpTtl: 'Subagent 種別数',
  helpBody: '観測された subagent の種類数（invocation 単位で dedup 済み）。' },
```

**L33 (kpi-projs)**:

```javascript
{ id: 'kpi-projs', k: 'projects',
  v: (data.project_total != null ? data.project_total : (data.project_breakdown||[]).length),
  s: 'distinct cwds', cls: 'c-peach',
  helpTtl: 'プロジェクト数',
  helpBody: '利用が観測されたプロジェクト（cwd 単位）。同じディレクトリ配下のセッションは同一プロジェクトとして集計。' },
```

defensive fallback (`!= null` 三項演算子) は backward-compat 目的:
- 古い `reports/export_html.py` 出力 (新 field を持たない静的 HTML) を新 frontend で見る場合
- live SSE で server だけ古いまま frontend だけ新しい一時状態 (構造保証)

`??` ではなく `!= null` 三項演算子を採用する理由 — KPI counter は `0` も valid な値で、`||` は `0` を falsy 扱いしてしまう (`Number(0) || length` で length に化ける)。`!= null` なら `undefined`/`null` のときだけ fallback、`0` は通る。

### Phase 4 — Frontend: `25_live_diff.js` 同期

`buildLiveSnapshot` を `20_load_and_render.js` と同じソース (`*_kinds_total` / `project_total`) を読むように更新:

```javascript
const kpi = {
  'kpi-total':   Number(d.total_events) || 0,
  'kpi-skills':  (d.skill_kinds_total != null ? Number(d.skill_kinds_total) : skillRanking.length),
  'kpi-subs':    (d.subagent_kinds_total != null ? Number(d.subagent_kinds_total) : subRanking.length),
  'kpi-projs':   (d.project_total != null ? Number(d.project_total) : projects.length),
  'kpi-sess':    Number(ss.total_sessions) || 0,
  'kpi-resume':  Number(ss.resume_rate) || 0,
  'kpi-compact': Number(ss.compact_count) || 0,
  'kpi-perm':    Number(ss.permission_prompt_count) || 0,
};
const lede = {
  ledeEvents:   Number(d.total_events) || 0,
  ledeDays:     localDays.length,
  ledeProjects: (d.project_total != null ? Number(d.project_total) : projects.length),
};
```

これをやらないと:
- live mode で skill が 11 種目に増えた refresh で `kpi-skills` の delta が「old=10 (cap), new=10 (cap)」のまま 0 と判定され toast が出ない、または
- loadAndRender 側 (新 field 参照) と live-diff 側 (length) で表示と toast が乖離する

defensive fallback は loadAndRender と同じく `!= null` 三項演算子。新 field は **明示的に `!= null` 判定 → Number 変換** の 2 ステップで書く (理由: `Number(d.skill_kinds_total) || 0` だと値が `0` のとき fallback の length に化けてしまい、本当に 0 種類のときの diff が壊れる)。

### Phase 5 — Frontend test pin (Node round-trip)

`tests/test_dashboard_live_diff.py` の既存 `TestBuildLiveSnapshotNode` 内に追加。番号は plan 内 reference 用 — 既存クラスの末尾位置からそのまま append すればよく、Phase 5 開始時に番号は振り直して構わない (numbering は load-bearing ではない / test 名は plan 内で unique):

| # | テスト名 | 期待 |
|---|---|---|
| L1 | `test_kpi_skills_uses_skill_kinds_total_when_provided` | `skill_ranking: [..10..], skill_kinds_total: 25` で `kpi-skills === 25` |
| L2 | `test_kpi_skills_falls_back_to_ranking_length_when_total_missing` | `skill_kinds_total` 不在で `kpi-skills === skill_ranking.length` |
| L3 | `test_kpi_subs_uses_subagent_kinds_total_when_provided` | 同様 |
| L4 | `test_kpi_subs_falls_back_to_ranking_length_when_total_missing` | 同様 |
| L5 | `test_kpi_projs_uses_project_total_when_provided` | 同様 |
| L6 | `test_kpi_projs_falls_back_to_breakdown_length_when_total_missing` | 同様 |
| L7 | `test_lede_projects_uses_project_total_when_provided` | `lede.ledeProjects === project_total` |

`_NODE` skip guard が既存にあるので CI Node 不在環境でも壊れない (既存 pattern 踏襲)。

確認: `python -m pytest tests/test_dashboard_live_diff.py -x` で全件 PASS。Node 利用可能環境では新規 7 件分も含めて PASS。

### Phase 6 — Spec doc 更新確認

`docs/spec/dashboard-api.md` のトップレベル形 sample JSON / 集計仕様節を再読し、既存 `skill_ranking` / `subagent_ranking` / `project_breakdown` 節に「上位 10 件 cap (UI 表示用)。全件 unique 数は `*_kinds_total` / `project_total` を参照」を **必ず追記**。これを忘れると後続 issue 担当者が再度同じ confusion に陥る。

### Phase 7 — 全体回帰

- `python -m pytest tests/ -x` 全件 PASS を確認
- `python -m dashboard.server` でローカル起動 → ブラウザで Overview 上段の Skills / Subagents / Projects KPI が **fixture の cap を超えた値** を表示することを目視
- `python -m reports.export_html --output /tmp/dashboard.html && open /tmp/dashboard.html` で静的 HTML も同様に新 field が表示されることを確認

## Risks / tradeoffs

| Risk | 評価 | 緩和策 |
|---|---|---|
| 古い静的 HTML export (新 field 無し) を新 frontend で読んだとき値が消える | **中** (既存 export を保管している運用者あり) | Frontend の `!= null` 三項演算子で `*_ranking.length` / `project_breakdown.length` に fallback。値は古い 10-cap のままだが KPI が "—" や `NaN` にはならない |
| **Upgrade asymmetry** (informational) — `reports/export_html.py` は frontend assets と `window.__DATA__` を **同一 HTML に inline** するので、ユーザー操作上「古い HTML を新 frontend で開く」という path は構造的に発生しない (古い HTML には古い JS が、新 HTML には新 JS が必ず付いてくる)。defensive fallback はあくまで **構造保証** — live SSE 中の prev snapshot field-version mismatch ガードと、開発時の手動 asset swap 動作確認用。PR description には「fresh export は新 KPI を表示、古い HTML は 10-cap のまま (再エクスポートで更新)」を 1 行 note するに留める |
| `25_live_diff.js` を更新し忘れ → loadAndRender と live snapshot で値が乖離 → 誤 toast | **中** | Phase 4 で同じ fallback を必ず入れる。Phase 5 で Node round-trip test を追加して構造保証 |
| 新 field 追加で既存 dashboard test が break (`test_total_events_*` 等の sample dict と不一致) | **低** | 既存 test は dict 全体一致ではなく key 単位 assert なので additive 追加で壊れない見込み。Phase 7 全件 run で確認 |
| `aggregate_subagent_metrics` を build_dashboard_data 内で **二重に呼ぶ** ことで perf 低下 | **低** (events 数 ≪ 1 万 / hot tier 180 日制限内 / 各 invocation O(1) bucket) | 早期最適化はしない。実観測で `--profile` してから判断 |
| `??` を使うと既存 ESLint / browser support が壊れる | **低** | `??` は使わず `!= null` 三項演算子で書く |
| live SSE の prev snapshot が古い field 値 (length) で next が新 field 値 → 初回 refresh で誤 toast | **低** | `__livePrev === null` ガードが既存にあるので reload 直後の 1 発目は toast 出ない。SSE 接続継続中の "サーバーだけ" upgrade は実機で発生しないシナリオ (server / frontend 同一プロセス) なので無視可 |
| Owner の "ランク表示は対象外" comment と乖離する変更が紛れる | **高** (review でリジェクト) | Phase 3 / 4 で **rank renderer (L66-104, L105-108) と project stack (L212-235) は touch しない**。PR 本文に「ランキング・stack/legend は不変」を明記し diff を最小化 |

## Out of scope (明示)

以下は **本 PR では一切触らない**:

1. `renderRank('skillBody', ...)` (L105) と `renderRank('subBody', ...)` (L106) の Skills / Subagents top 10 ランキング描画
2. `skillSub` / `subSub` の `'top ' + length + ' · max ' + max` subtitle (L107-108)
3. Overview 横幅 project stack (L217-223) と legend (L224-234) と `projSub` (L235) — `project_breakdown` (top 10 cap) を引き続き使う
4. `aggregate_skills` / `aggregate_subagents` / `aggregate_projects` の signature と `top_n=TOP_N` cap 自体 (= ranking 配列は今後も 10 件 cap)
5. `aggregate_project_skill_matrix(top_projects=TOP_N, top_skills=TOP_N)` (Patterns ページ matrix)
6. `aggregate_permission_breakdowns(top_n=TOP_N)` (Quality permission breakdown)
7. `aggregate_compact_density(top_n=TOP_N)` (Quality worst_sessions)
8. `TOP_N_SKILL_INVOCATION = 20` / `TOP_N_SKILL_LIFECYCLE = 20` (Surface page)
9. `aggregate_skill_cooccurrence(top_n=100)` (= 100 cap で別枠)
10. CSS / shell.html / 他 JS file (`30_renderers_patterns.js` 以降) — 新 KPI 値を表示する DOM は既存の `kpi-*` tile / `ledeProjects` のみで、新規 DOM 追加なし

これらの cap 据え置きを review 中に "ついでに" 動かさないこと (= scope creep 回避 = Issue #81 が release で意図せず広がるのを防ぐ)。
