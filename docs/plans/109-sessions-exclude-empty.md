# Plan: Issue #109 — Sessions ページ「データが 0 のセッション」除外 (assistant_usage 0 件 = empty session の aggregator-side filter)

## 📋 plan-reviewer 反映ログ

| Proposal | 内容 | 反映箇所 |
|----------|------|----------|
| (初稿) | — | — |

### 三次レビュー反映 (iteration 2 → 3)

| Proposal | 内容 | 反映箇所 |
|----------|------|----------|
| P1 (**actionable**) | §8 Help-pop 4-axis table の "filter 条件" row が iter1 で棄却した `any(...)` 形式のままだった drift を是正、`has_assistant_usage[sid]` flag verbatim 表現に揃える | §8 4-axis 表 / (整合先) §6 Phase 1 GREEN snippet |
| P2 (**actionable**) | §6 Phase 1 GREEN code snippet の `_build_session_row` 呼び出しを実 signature に揃える (`(session_id, boundary_evs, content_evs, subagent_count)` の 4 args、`boundary_evs` を `content_evs` より前に置く)。変数名も `full_by_session` / `subagent_counts` の実コード verbatim に揃える | §6 Phase 1 GREEN snippet / §9 R1 採用案 row 説明 |
| P3 (advisory) | §3 AC line "他 6 cases" → 「7 cases (test 名の前置部 + 6)」相当に整合 | §3 AC 表 / §6 Phase 1 DoD case 数 / §7 Test files pin 表 |
| P4 (advisory) | Phase 1 RED の `test_unfilter_total_sessions_unchanged_when_breakdown_excludes` を **削除** (= Phase 2 `test_session_stats_total_sessions_includes_empty` で同等カバー、Phase 1 の aggregator-isolation 原則に沿う / DoD bisect 性も保つ) | §6 Phase 1 RED 一覧 / §6 Phase 1 DoD case 数 / §7 Test files pin 表 |

### 二次レビュー反映 (iteration 1 → 2)

| Proposal | 内容 | 反映箇所 |
|----------|------|----------|
| P1 (advisory) | subagent-only fixture を Q1 verbatim 整合に rename | §6 Phase 1 RED 一覧 (`test_session_with_subagent_lifecycle_but_no_assistant_usage_excluded` に rename) / §7 Test files pin 表の cases 列 |
| P2 (advisory) | SHA256 を RED test ではなく **reconciliation step** に reframe (`TestSessionsSha256Bump` 削除、既存 sentinel `test_html_template_byte_equivalent_to_pre_split_snapshot` 経由で扱う) | §6 Phase 3 RED 一覧 / §6 Phase 3 GREEN reconciliation step / §7 Test files pin 表 |
| P3 (**actionable**) | `buildSessionsSubText(sessions)` helper を 「(任意)」 から **必須** に昇格、`window.__sessions` expose に追加 | §5 編集 list (`45_renderers_sessions.js`) / §6 Phase 3 GREEN |
| P4 (advisory) | period 適用下「pre-cutoff にだけ assistant_usage がある session」の対称 edge case を pin (6th case 追加) | §6 Phase 2 RED 一覧 / §7 Test files pin 表 (cases 5→6) |
| P5 (advisory) | `commands/` / `README.md` grep を「(任意)」 から **必須** に昇格、out-of-scope 判定 disposition rule も明記 | §6 Phase 4 DoD / §6 Phase 4 GREEN |
| Q1 (perf) | §9 R1 の `any(...)` 再走を **single-pass `has_assistant_usage` 構築** design に refine (`period_by_session` ループ内で 1 度だけ判定) | §9 R1 採用案 design / §6 Phase 1 GREEN 実装案 |
| Q2 (live smoke) | Phase 5 PR Test plan に **active session 中の transient-disappear UX 観察** step を追加 | §6 Phase 5 PR Test plan |
| Q3 (Phase 2 DoD 曖昧性) | Phase 2 DoD で `test_model_distribution.py` 走行は **既存 suite の回帰 guard 用** (新 test 追加なし) と verbatim 明記 | §6 Phase 2 DoD |

## 1. Goal

Sessions ページの table から **assistant_usage event を 1 件も持たない session (= 起動だけ / builtin command のみ / abort)** を `aggregate_session_breakdown` の row pool 段階で除外する。除外は **Python aggregator (`cost_metrics.aggregate_session_breakdown`)** で行うため、`/api/data` 経由の live SSE / `export_html` 経路 / `build_demo_fixture.py` / `build_surface_fixture.py` の **すべての消費者に自動で効く**。Sessions ページ KPI 4 枚 (`computeKpi`) は data list を素直に sum しているので、aggregator が落とした時点で自動で除外後の数字に揃う (Q3)。Sessions panel sub の `${N} sessions` 表記は新ラベル「**有効セッション ${N} · ${projCount} projects**」に差し替えて「empty session を除いた数字である」ことを UI で明示する (Q4)。footer / header の `247 sessions` (`session_stats.total_sessions` 経路 = unfilter 観測総数) は **無変更** (Q4)。除外件数の display は出さない (Q5)。

## 2. 採用 spec まとめ (前提固定 — user 確定済、動かさない)

| Q | 採用 spec | 由来 |
|---|----------|------|
| M | base branch `v0.8.0` / branch `feature/109-sessions-exclude-empty` | user 確定 |
| Q1 | (A) `assistant_usage` event が 0 件 = 除外条件 | user 確定 |
| Q2 | (a) Python aggregator (`aggregate_session_breakdown`) で落とす | user 確定 |
| Q3 | KPI 4 枚は Q2(a) 採用で自動整合 — frontend 側に追加 filter は **入れない** | user 確定 |
| Q4 | footer / header `247 sessions` は `session_stats.total_sessions` のまま (無変更)。Sessions ページ sub は **「有効セッション ${N} · ${projCount} projects」** ラベル | user 確定 |
| Q5 | 「N 件除外」のような excluded-count display は **出さない** | user 確定 |

### 2.1 「有効セッション」ラベル正本 (確定)

- panel sub 文字列正本: `有効セッション ${N} · ${projCount} projects`
  - 「有効セッション」固定 (= 助詞・送り仮名なし、漢字 4 文字)
  - 数値は半角アラビア数字 (`computeKpi` の他 KPI 数値表記と整合)
  - `·` は中黒 U+00B7 (= 既存 `${sessions.length} sessions · ${projCount} projects` と同じ separator を踏襲)
  - `projects` は半角小文字 (= 既存正本)
- 単数 / 複数の屈折分岐なし (= 日本語側で「セッション」「プロジェクト」とも数で活用しないので invariant を増やさない)
- N = 0 のとき `有効セッション 0 · 0 projects` (= 既存の `0 sessions · 0 projects` の振る舞いを継承、空表示 / "—" fallback は採らない)
- "有効" の語彙選定理由: Issue 本文「実用上は使われていない」セッションを排除する判断と整合。CLAUDE.md vocabulary discipline に従い、内部実装語 (legacy / silent skip / observation pending) ではなく **UI surface に持ち込んで意味が立つ語** として「有効」を選択

## 3. Acceptance criteria (Issue 本文 ack)

- [x] **Q1 / Q2 spec 確定** — §2 採用表で確定 (Q1=A / Q2=a)
- [ ] **`tests/test_dashboard_sessions_api.py` に「assistant_usage 0 件の session_start のみ session が session_breakdown から除外される」test 追加** — Phase 1 RED `TestSessionBreakdownExcludesEmpty` 計 7 cases (only-session_start / only-session_start+session_end / only-skill_tool / only-user_slash_command / 1 assistant_usage 包摂 / zero-token assistant_usage 包摂 / subagent lifecycle but no assistant_usage 除外)。cross-aggregator invariant (`session_stats.total_sessions` 不変) は Phase 2 `TestSessionBreakdownEmptyExcludeIntegration` で扱う
- [ ] **KPI 4 枚計算 (`computeKpi`) が除外後セッションのみで動く** — Q2(a) 採用で `data.session_breakdown` から既に除外済 → frontend 側に追加 filter は入れない (Phase 1 + Phase 3 で「KPI 4 枚 = 除外後 sessions」を Node round-trip drift guard test で pin)
- [ ] **Sessions ページ sub 表記の `N sessions` が除外後数字** — Phase 3 で sub 文字列を「有効セッション ${N} · ${projCount} projects」に差し替え、`renderSessions` が `data.session_breakdown.length` を読む (= aggregator 出力 = 除外後)
- [ ] **footer / header sessions counter は `session_stats.total_sessions` ベースで変更しない** — `aggregate_session_stats` (server.py:989) は `events` (raw, unfilter) から session_start を直接 count、本 issue で touch しない。Phase 1 RED に「unfilter `total_sessions` は empty session も含む」 invariant pin test を追加 (= drift guard / 4-axis verification)
- [x] **Q5 excluded-count 表示判断** — §2 で「表示なし」を確定。UI surface に追加要素 (badge / カウンタ) を入れない

## 4. 必読ファイル一覧 (実装者が最初に読むべきファイル)

実装に着手する前に **必ず以下を読み通す** こと (順序付き)。

1. `cost_metrics.py:241-424` — `calculate_session_cost` / `_build_session_row` / `aggregate_session_breakdown` 本体。本 issue の **編集対象** が `_build_session_row` (= 270-348) と `aggregate_session_breakdown` (= 351-424) のいずれか / 両方かを Phase 1 で決定するため、両関数の責任分担を最初に把握する
2. `dashboard/server.py:1086-1125` — `build_dashboard_data` の return dict。`session_breakdown` / `session_stats` / `model_distribution` の 3 経路がそれぞれ raw events / period_events_raw のどれを食べているかを確認 (= 本 issue が **`session_stats` を絶対 touch しない** 根拠)
3. `dashboard/server.py:989-1011` — `aggregate_session_stats`。`total_sessions` が `events` (= unfilter raw) から `session_start` event を直接 count している事実を確認 (= footer / header `247 sessions` の独立性 pin)
4. `tests/test_dashboard_sessions_api.py:54-219` — `TestSessionBreakdown` の既存 9 case。Phase 1 RED で並べる新 test (`TestSessionBreakdownExcludesEmpty`) の **fixture helper (`_au` / `_session_start` / `_session_end`)** をそのまま流用する
5. `dashboard/template/scripts/45_renderers_sessions.js:255-309` — `renderSessions` の sub 行生成箇所 (line 286)。本 issue の Phase 3 GREEN で文字列差し替えのみ
6. `dashboard/template/shell.html:534-630` — Sessions section + `id="sessionsSub"` (line 560) + `hp-sessions` help-pop body (line 556)。**help-pop 文言の更新対象** に注意
7. `tests/test_dashboard_sessions_ui.py:1-160` — Sessions DOM 構造 + concat 順 + Node round-trip pattern。本 issue の template structural test を **本ファイルに追加** する (Issue 本文の「test_dashboard_sessions_template.py」は repo 上不在で、structural pin は `_ui.py` 側に既存。新ファイル追加は drift なので **既存 ui.py に追記**)
8. `docs/spec/dashboard-api.md:808-891` — `session_breakdown` 章。Phase 5 で「session pool 定義」に「assistant_usage 0 件の session は除外」1 行を追加 (verbatim 整合)
9. `docs/spec/dashboard-runtime.md:103, 150-153` — Sessions page の 1 行説明 + period 連動 note。「有効セッション」ラベルの根拠を 1 行追記
10. `docs/plans/106-overview-model-distribution.md` (= 直近 plan の構造の正本) — phase 構成 / reflection-log / Risks / DoD の書き方を踏襲する
11. `CLAUDE.md` の vocabulary discipline + help-pop 4-axis verification の項 (= 「有効セッション」ラベル正本 / hp-sessions 文言更新の理由づけ)

## 5. Critical files (編集 / 新規一覧)

### 編集
- `cost_metrics.py` — `aggregate_session_breakdown` の `period_by_session` 構築ループに **single-pass で `has_assistant_usage` flag を立てる** 実装を追加し、row append 直前で `if row is not None and has_assistant_usage[sid]: rows.append(row)` filter を入れる。`any(...)` の再走は避ける (= §9 R1 採用案、Q1 perf 懸念 close)
- `dashboard/template/scripts/45_renderers_sessions.js` — (1) line 286 の sub 文字列を `有効セッション ${N} · ${projCount} projects` に差し替え (1 line edit)。(2) sub 文字列生成式を `buildSessionsSubText(sessions)` helper として **関数抽出**、`window.__sessions` expose block (現 line 295-308) に追加 (Node round-trip RED test を runnable にするための load-bearing extraction、§6 Phase 3 GREEN P3 反映)
- `dashboard/template/shell.html` — line 556 の `hp-sessions` help-pop body 末尾に「`assistant_usage` event を 1 件も持たない session は除外する」 1 文追加 + lede (line 538-541) の「最新 20 セッション」を「最新 20 件の有効セッション」に微調整 (Help-pop 4-axis verification 整合)
- `tests/test_dashboard_sessions_api.py` — `TestSessionBreakdownExcludesEmpty` class を追加 (§6 Phase 1 RED の 7 cases)
- `tests/test_dashboard_sessions_ui.py` — `TestSessionsSubLabel` class を追加 (§6 Phase 3 RED の 4 cases)
- `tests/test_dashboard_template_split.py` — `EXPECTED_TEMPLATE_SHA256` を bump (Phase 3 で shell.html の help-pop / lede を編集するので必須)
- `docs/spec/dashboard-api.md` — `session_breakdown` 章の「session pool 定義」に「assistant_usage 0 件の session は除外」を verbatim で追加 (§6 Phase 4)
- `docs/spec/dashboard-runtime.md` — line 103 Sessions 行の説明を「有効セッション」ラベルに整合させる微調整 (§6 Phase 4)

### 無変更 (= touch すると acceptance criteria 違反)
- `dashboard/server.py:989-1011` `aggregate_session_stats` (footer `total_sessions` 経路)
- `dashboard/template/scripts/20_load_and_render.js` line 26 / 57 の `ss.total_sessions` (= header KPI / footer meta-item)
- `dashboard/template/scripts/25_live_diff.js` line 70 の `kpi-sess` 経路
- `cost_metrics.py` の `calculate_session_cost` / `calculate_message_cost` / 価格表 (本 issue は集計の **削減** であって cost 計算は無変更)

### 新規作成
- `docs/plans/109-sessions-exclude-empty.md` — 本 plan 本体
- (test ファイル新規作成は **しない**: 既存 2 本 `test_dashboard_sessions_api.py` / `test_dashboard_sessions_ui.py` への追記)

## 6. Phase 構成 (TDD 厳守 / failing test phase 先行)

### Phase 1 — aggregator filter (cost_metrics.py)

**Goal**: `aggregate_session_breakdown` の出力 row 配列から「assistant_usage event 0 件 session」を構造的に除外する。

**関連ファイル**:
- 編集: `cost_metrics.py` (`_build_session_row` および/または `aggregate_session_breakdown`)
- 編集: `tests/test_dashboard_sessions_api.py` (`TestSessionBreakdownExcludesEmpty` 追加)
- 参照: `dashboard/server.py:989-1011` (= `session_stats.total_sessions` の独立性 invariant pin)

**RED テスト具体名** (`tests/test_dashboard_sessions_api.py` に追加):

```python
class TestSessionBreakdownExcludesEmpty(unittest.TestCase):
    def test_session_with_only_session_start_excluded(self): ...
    def test_session_with_only_session_start_and_session_end_excluded(self): ...
    def test_session_with_only_skill_tool_excluded(self): ...           # /help / /skills のみ session
    def test_session_with_only_user_slash_command_excluded(self): ...   # builtin command のみ
    def test_session_with_one_assistant_usage_included(self): ...       # 包摂 boundary
    def test_session_with_zero_token_assistant_usage_included(self): ...  # input=output=cr=cc=0 でも assistant_usage event 自体は 1 件 → 残す (Q1=A の verbatim 解釈)
    def test_session_with_subagent_lifecycle_but_no_assistant_usage_excluded(self): ...
        # subagent_start は記録されたが main session 側の assistant_usage hook が
        # 1 件も発火しなかった session (= Task 起動直後 abort 等)。Q1=A の verbatim
        # ("assistant_usage event 0 件") に従い除外する。意図は「main session が
        # subagent をホストしただけで何も応答していない」状況の pin。
```

注: iter1 で初版に含めていた `test_unfilter_total_sessions_unchanged_when_breakdown_excludes` は **Phase 2 に統合** (P4 反映)。Phase 1 は `aggregate_session_breakdown` aggregator 単独のレベルに isolate し、`session_stats.total_sessions` 等の cross-aggregator invariant は Phase 2 `test_session_stats_total_sessions_includes_empty` で扱う (= "aggregator commit 単独で git bisect 可能" な DoD と整合)。

それぞれの fixture は既存 `_session_start` / `_session_end` / `_au` / `_skill_tool` (= raw dict 直書き) を使い、events list を組む。RED 時点では aggregator が無条件で全 session を返すので、`assertEqual(len(sb), 0)` または `assertEqual(len(sb), 1)` を期待した assert が落ちる。

**GREEN 実装案** (採用案 = §9 R1 で確定):

- `aggregate_session_breakdown` の `period_by_session` 構築ループ (= 既に raw events を session ごとに分配している箇所、`cost_metrics.py:402-408`) で **同じ pass 内に `has_assistant_usage: dict[str, bool]` を構築** する:
  ```python
  period_by_session: dict[str, list[dict]] = {}
  has_assistant_usage: dict[str, bool] = {}
  for ev in period_events:
      sid = ev.get("session_id", "")
      if not sid:
          continue
      period_by_session.setdefault(sid, []).append(ev)
      if ev.get("event_type") == "assistant_usage":
          has_assistant_usage[sid] = True
  ```
- row append 直前の filter (= 実コード `cost_metrics.py:413-421` のループに condition 追加):
  ```python
  rows: list[dict] = []
  for sid in period_by_session:
      boundary_evs = full_by_session.get(sid, [])
      content_evs = period_by_session[sid]
      row = _build_session_row(
          sid, boundary_evs, content_evs, subagent_counts.get(sid, 0),
      )
      if row is not None and has_assistant_usage.get(sid, False):
          rows.append(row)
  ```
  - `_build_session_row` の signature は **`(session_id, boundary_evs, content_evs, subagent_count)`** の 4 positional args (= `cost_metrics.py:271-276`、`boundary_evs` を `content_evs` より前)。snippet ではこれを verbatim 維持
- `_build_session_row` の責任 / シグネチャは無変更 (= R1 案 A の純度を保つ)。`any(...)` の再走を避けて O(N_event) single pass で完結 (= Q1 perf 懸念 close、180 日 hot tier の N_session が大きくても安定)
- 棄却した代替案 (記録のため):
  - **案 A`** (再走): `if row is not None and any(e.get("event_type") == "assistant_usage" for e in content_evs)` — 動作正しいが再走の重複コスト
  - **案 B** (`_build_session_row` 内 None 返し): `_build_session_row` の責任が「session_start なし or assistant_usage なし」に拡張、将来 grace 期間 spec 復活時に巻き戻しが要 (R2 と regret risk)

**DoD** (Phase 1 完了条件):
- 全 RED test (`TestSessionBreakdownExcludesEmpty` **7 cases** [P4 で 8→7 に縮小] + 既存 `TestSessionBreakdown` 9 cases) が `pytest tests/test_dashboard_sessions_api.py` で GREEN
- `cost_metrics.calculate_session_cost` / `calculate_message_cost` 既存テスト (`tests/test_cost_metrics.py`) も GREEN (回帰なし)
- aggregator commit が単独で `git bisect` 可能な完全 GREEN 状態

**関連 spec-Q**: Q1 (=A) / Q2 (=a) / Q3 (= 自動整合の根拠)

### Phase 2 — build_dashboard_data 統合 + session_stats 不変性 invariant pin

**Goal**: aggregator filter が `/api/data` 経路 / export_html 経路 / SSE refresh 経路 / demo fixture 経路 すべてで透過に効くことを cross-aggregator invariant test で pin。**Phase 1 を独立 test class で書いたあと、本 phase で build_dashboard_data 統合を verbatim 検証** する分離。

**関連ファイル**:
- 編集: `tests/test_dashboard_sessions_api.py` (`TestSessionBreakdownEmptyExcludeIntegration` 追加)
- 参照: `dashboard/server.py:1086-1125` / `dashboard/server.py:989-1011`

**RED テスト具体名**:

```python
class TestSessionBreakdownEmptyExcludeIntegration(unittest.TestCase):
    def test_build_dashboard_data_excludes_empty_session(self): ...
        # build_dashboard_data 経由で session_breakdown が "1 valid 1 empty" 入力に対し len==1 を返す
    def test_session_stats_total_sessions_includes_empty(self): ...
        # 同じ入力で session_stats.total_sessions == 2 (footer 247 sessions 経路 invariant)
    def test_period_filter_and_empty_exclude_compose(self): ...
        # period="7d" + empty session 混在で「period 内 valid + period 内 empty + period 外 valid」 → len==1 (period 内 valid のみ)
    def test_cross_cutoff_session_with_in_period_assistant_usage_kept(self): ...
        # session_start が pre-cutoff、in-period に assistant_usage 1 件 → 残る (= 既存 test_cross_cutoff_session_keeps_in_period_costs の振る舞いを破壊しない drift guard)
    def test_subagent_only_session_excluded_at_build_dashboard_data_level(self): ...
        # subagent_start のみ session (= main の assistant_usage 無し) は build_dashboard_data 経由でも除外
    def test_session_with_only_pre_cutoff_assistant_usage_excluded_under_period(self): ...
        # session_start in-period (例: 7d window 内)、assistant_usage は全て period 外 (>7d 前)。
        # period 適用後の content_evs に assistant_usage が 0 件 → 除外。
        # = 「period 内の意味あるアクティビティ」が exclusion の単位であることを pin
        # (P4 反映、`test_cross_cutoff_session_with_in_period_assistant_usage_kept` の対称形)
```

**GREEN 実装**: Phase 1 の aggregator 修正で自動 GREEN になる (= aggregator が呼ばれるすべての経路に効くため、本 phase は「呼び出し経路の verbatim 確認」のみで実装は伴わない)。

**DoD**:
- 全 6 case (P4 で 5→6 に拡大) GREEN
- `pytest tests/test_dashboard_sessions_api.py tests/test_cost_metrics.py tests/test_model_distribution.py` 全 GREEN — **本 Phase で `test_model_distribution.py` に新 test は追加しない**。これは既存 `test_session_breakdown_total_matches_model_distribution_total` 等の cross-aggregator invariant が **empty session 除外でも壊れないことを既存 suite の回帰 guard として確認** する目的での走行 (= `aggregate_model_distribution` は `period_events_raw` から直接集計するので empty session の有無に依存しない、`session_breakdown` 側だけが行数減 → cost_total は一致のまま)。新 test は §7 表通り `test_dashboard_sessions_api.py` 側に閉じる

**関連 spec-Q**: Q4 (= footer 不変) / Q3 (= KPI 自動整合の系として証明)

### Phase 3 — Sessions ページ sub label + lede + help-pop verbatim 更新 (DOM + JS + SHA256)

**Goal**: aggregator が除外後 list を返す事実を **UI surface に正しく文書化** する。Q4 採用「有効セッション」ラベルへの差し替えと、help-pop 4-axis verification 整合 (assistant_usage 0 件除外 fact が UI 文言と一致)。

**関連ファイル**:
- 編集: `dashboard/template/scripts/45_renderers_sessions.js` (line 286)
- 編集: `dashboard/template/shell.html` (line 538-541 lede / line 556 help-pop body)
- 編集: `tests/test_dashboard_sessions_ui.py` (`TestSessionsSubLabel` + `TestSessionsHelpPopVerbatim` 追加)
- 編集: `tests/test_dashboard_template_split.py` (= `EXPECTED_TEMPLATE_SHA256` bump)

**RED テスト具体名** (`tests/test_dashboard_sessions_ui.py` に追加):

```python
class TestSessionsSubLabel:
    def test_renderers_sessions_js_uses_yuko_session_label(self):
        # 45_renderers_sessions.js を string read → '有効セッション ' を verbatim 含む
    def test_renderers_sessions_js_does_not_emit_legacy_sessions_label(self):
        # ' sessions · ' (= 旧正本 ` ${sessions.length} sessions · `) が **消滅** している
    def test_render_sessions_sub_via_node_with_one_session(self):
        # Node round-trip: data.session_breakdown=[{project:'p', ...}] (1 row, valid)
        # → 描画後の #sessionsSub.textContent が "有効セッション 1 · 1 projects"
        # (jsdom 不使用なので renderSessions の sub 行生成箇所を直接 stub するか、
        #  computeKpi 同等に sub 行 builder を expose する判断を Phase 3 GREEN で確定する)
    def test_render_sessions_sub_via_node_with_zero_sessions(self):
        # 同上、空配列 → "有効セッション 0 · 0 projects"

class TestSessionsHelpPopVerbatim:
    def test_lede_uses_yuko_session_phrase(self):
        # shell.html assembled template に '最新 20 件の有効セッション' verbatim 含む (Sessions section 内)
    def test_hp_sessions_body_mentions_assistant_usage_zero_exclusion(self):
        # hp-sessions の pop-body に 'assistant_usage' / '除外' verbatim 含む
        # (Help-pop 4-axis verification: 集計フィルタの fact = aggregator filter 条件 と verbatim 整合)
    def test_hp_sessions_body_does_not_claim_all_session_starts_displayed(self):
        # 旧 body に「session_start イベントを起点に...20 件」だけが残ってはいけない
        # (= 「全 session を表示」claim の残存禁止、Help-pop 4-axis verification)

```

**SHA256 reconciliation step (sentinel、新 RED test ではない)** — P2 反映:
- 既存 `tests/test_dashboard_template_split.py::test_html_template_byte_equivalent_to_pre_split_snapshot` (`test_dashboard_template_split.py:45-60`) が shell.html 編集後 hash mismatch で即 RED になる
- recompute: `python3 -c "import hashlib, dashboard.server as d; print(hashlib.sha256(d._HTML_TEMPLATE.encode()).hexdigest())"`
- `tests/test_dashboard_template_split.py:28` の `EXPECTED_TEMPLATE_SHA256` を実測 hash に bump、Phase 3 GREEN commit に **同梱** (sentinel reconciliation = behavioral RED ではないため、新 test class は追加しない)

**GREEN 実装**:
- `45_renderers_sessions.js:286` の sub 文字列生成式を **`buildSessionsSubText(sessions)` helper として関数抽出** し `renderSessions` 内から呼び出す (P3 反映、必須)。helper の return は `'有効セッション ' + sessions.length + ' · ' + projCount + ' projects'` 形式。`projCount` は helper 内で `new Set(sessions.map(s => s.project)).size` 等で算出 (= 既存 line 285-286 の演算ロジックを保つ)
- `window.__sessions` expose block (現 line 295-308) に `buildSessionsSubText` を追加 (= 既存 `computeKpi` / `buildKpiHTML` Node round-trip pattern (`tests/test_dashboard_sessions_ui.py:447-540`) と揃える)。これにより `test_render_sessions_sub_via_node_*` が jsdom 不使用で runnable
- `shell.html:538-541` lede 内「最新 20 セッション」→「最新 20 件の有効セッション」に変更
- `shell.html:556` `hp-sessions` body を **Help-pop 4-axis verification 整合形** に書き直し:
  > `<code>session_start</code> イベントを起点に、同 <code>session_id</code> 内の <code>assistant_usage</code> から model 別 token 数 / service_tier を集計。開始時刻降順で最新 20 件。<code>ended_at</code> が未観測のものは「進行中」として表示する。**<code>assistant_usage</code> event を 1 件も持たない session (起動だけ / builtin command のみ / abort) は集計対象外。**
- `tests/test_dashboard_template_split.py:28` の `EXPECTED_TEMPLATE_SHA256` を `python3 -c "import hashlib, dashboard.server as d; print(hashlib.sha256(d._HTML_TEMPLATE.encode()).hexdigest())"` で実測した新 hash に bump

**DoD**:
- 全 RED test (`TestSessionsSubLabel` 4 + `TestSessionsHelpPopVerbatim` 3 = 7 cases、P2 で `TestSessionsSha256Bump` を sentinel reconciliation に格下げしたので RED test class は 2 つ) が GREEN
- 既存 `test_html_template_byte_equivalent_to_pre_split_snapshot` (sentinel) が新 hash で reconcile 済 GREEN
- `pytest tests/test_dashboard_sessions_ui.py tests/test_dashboard_template_split.py tests/test_dashboard_sessions_api.py` 全 GREEN
- 視覚スモーク (chrome-devtools MCP): Sessions ページを開き、sub 行が「有効セッション ${N} · ${projCount} projects」で render される / hp-sessions tooltip 内に「assistant_usage event を 1 件も持たない session ... は集計対象外」が verbatim 表示される
- footer `${total_sessions} sessions` が **無変更** (Phase 3 commit に footer 経路の touch がないことを `git diff --stat` で確認)

**関連 spec-Q**: Q4 (= sub label) / Q5 (= excluded-count display なし、= UI surface に追加要素なし) / vocabulary discipline (= 「有効」漢字 4 文字) / Help-pop 4-axis verification

### Phase 4 — docs (dashboard-api.md + dashboard-runtime.md)

**Goal**: aggregator の振る舞い変更を spec doc に verbatim 反映 (= 仕様 drift 防止)。

**関連ファイル**:
- 編集: `docs/spec/dashboard-api.md` (line 808-891 `session_breakdown` 章)
- 編集: `docs/spec/dashboard-runtime.md` (line 103 Sessions 行 + 必要なら line 150-153 period 連動 note)

**RED**: 既存 docs テストには文字列レベルの spec 整合 guard が無いので、本 phase は **manual review checklist**:

- `docs/spec/dashboard-api.md:881-891` 「period 連動」セクションの後 (line 891 `## Active session の disposition` の前) に新サブセクション追加 (案):
  ```markdown
  ### empty session の除外 (Issue #109 / v0.8.0〜)

  - `assistant_usage` event を 1 件も持たない session (= 起動直後 `/exit` / builtin
    command のみで終了 / session_start 直後の abort) は **session_breakdown 配列
    から除外** する
  - 除外は `aggregate_session_breakdown` 内で row pool 構築時に行うので、
    `/api/data` / `export_html` / live SSE / `build_demo_fixture.py` /
    `build_surface_fixture.py` の **すべての消費者に透過に効く**
  - footer / header の `total_sessions` (= `session_stats.total_sessions`) は
    **unfilter 観測総数** であり empty session も含む。これは別経路 (raw events
    から `session_start` event を直接 count) で、本除外の影響を受けない
  - drift guard: `tests/test_dashboard_sessions_api.py::TestSessionBreakdownExcludesEmpty`
    + `TestSessionBreakdownEmptyExcludeIntegration::test_session_stats_total_sessions_includes_empty`
  ```
- `docs/spec/dashboard-runtime.md:103` Sessions 行説明末尾に「(empty session = assistant_usage 0 件 は除外、Issue #109)」追記 (1 行内)

**GREEN**: 上記 docs 変更を反映。

**DoD**:
- `git diff docs/spec/` で 2 ファイルの変更が verbatim 上記内容
- `pytest` 全体 GREEN (docs 変更で test 影響ないこと確認)
- **必須 grep step (P5 反映)**: `git grep -nE 'sessions ·|セッション数|N sessions' -- commands/ README.md docs/` を走らせ、各 hit に対し下記 disposition rule を適用:
  - hit が **Sessions ページ sub label** (= 除外後の cohort 表示) を指している → 「有効セッション」表記に verbatim 揃える
  - hit が **`session_stats.total_sessions` API field / footer / header の unfilter 観測総数** を指している → **触らない** (= 別経路で本 issue の rename スコープ外)
  - 判別不能な hit が出たら本 plan の §10 Out of Scope と照合し、Issue #109 の Sessions ページ table / sub 行に閉じる範囲のみ更新

**関連 spec-Q**: 全 Q (= spec doc が決定の anchor)

### Phase 5 — PR 作成

**Goal**: `feature/109-sessions-exclude-empty` を `v0.8.0` に対して PR 化。

- branch `feature/109-sessions-exclude-empty` (base `v0.8.0`) を push
- PR 本体は `gh pr create --base v0.8.0 --title "feat(dashboard): exclude empty (assistant_usage=0) sessions from session_breakdown (#109)"` 等
- PR body の Test plan に以下を含める:
  - `pytest tests/test_dashboard_sessions_api.py tests/test_dashboard_sessions_ui.py tests/test_cost_metrics.py tests/test_dashboard_template_split.py tests/test_model_distribution.py`
  - chrome-devtools MCP visual smoke (静的): Sessions ページ sub 行 / hp-sessions tooltip / footer `${N} sessions` の 3 箇所
  - **active session 中の transient-disappear 観察 (Q2 反映)**: 実 Claude Code セッションを起動した直後 (assistant_usage hook 発火前) に dashboard を開き、対象 session が一時的に Sessions table から消える振る舞いを目視確認 → その後 prompt を 1 回投げて assistant_usage 着弾後に table に再出現することを確認 (R2 受容判断の visual confirmation)
  - 既存 fixture (例: `data/usage.jsonl` の自家 dogfooding データ) で 「有効セッション数 ≤ footer total_sessions」が成立すること

**DoD**: PR が CI green、reviewer assign 済。

## 7. Test files pin

### `tests/test_dashboard_sessions_api.py` (server-side, 既存ファイルへの追記)

| Class 追加 | Cases | Phase |
|-----------|-------|-------|
| `TestSessionBreakdownExcludesEmpty` (新規) | 7 cases [iter2 P4 fold で 8→7、`unfilter_total_sessions_unchanged` を Phase 2 に移管] (only-session_start / only-session_start+end / only-skill_tool / only-user_slash_command / 1 assistant_usage 包摂 / zero-token assistant_usage 包摂 / subagent lifecycle but no assistant_usage 除外 [iter1 P1 rename]) | Phase 1 |
| `TestSessionBreakdownEmptyExcludeIntegration` (新規) | 6 cases (build_dashboard_data 経由除外 / session_stats 不変 / period+empty 合成 / cross-cutoff 不破壊 / build_dashboard_data 経由 subagent-only 除外 / period 内 session_start + pre-cutoff のみ assistant_usage 除外 [P4 追加]) | Phase 2 |
| `TestSessionBreakdown` (既存、無改変) | 9 cases | (回帰 guard) |

### `tests/test_dashboard_sessions_ui.py` (DOM + JS round-trip, 既存ファイルへの追記)

| Class 追加 | Cases | Phase |
|-----------|-------|-------|
| `TestSessionsSubLabel` (新規) | 4 cases (有効セッション verbatim / legacy sessions label 消滅 / `buildSessionsSubText` Node round-trip 1 session / `buildSessionsSubText` Node round-trip 0 session) | Phase 3 |
| `TestSessionsHelpPopVerbatim` (新規) | 3 cases (lede verbatim / help-pop assistant_usage+除外 verbatim / 旧「全 session 表示」claim 残存禁止) | Phase 3 |
| (SHA256 reconciliation, 新 test class なし — P2 反映) | 既存 `test_html_template_byte_equivalent_to_pre_split_snapshot` を sentinel として使い、`EXPECTED_TEMPLATE_SHA256` を bump して reconcile (新 test class 追加なし) | Phase 3 |

### 新規 test ファイル
- なし (既存 2 本への追記方針、Issue 本文の `tests/test_dashboard_sessions_template.py` は repo 上不在のため `_ui.py` に追記 = drift 回避)

## 8. Help-pop 4-axis verification (load-bearing)

| 軸 | help-pop 文 verbatim | 実装 verbatim |
|----|----------------------|----------------|
| filter 条件 | 「`assistant_usage` event を 1 件も持たない session ... は集計対象外」 | `aggregate_session_breakdown` の `has_assistant_usage[sid]` flag (`period_by_session` 構築ループ内で single-pass 構築 — `if ev.get("event_type") == "assistant_usage": has_assistant_usage[sid] = True`)。row append 時に `if row is not None and has_assistant_usage.get(sid, False): rows.append(row)` で除外 |
| 集計起点 | 「`session_start` イベントを起点に」 | `_build_session_row` の `starts = [e for e in boundary_evs if e.get("event_type") == "session_start"]` |
| 集計対象 | 「`assistant_usage` から model 別 token 数 / service_tier を集計」 | `_build_session_row` の `usage_evs = [e for e in content_evs if e.get("event_type") == "assistant_usage"]` |
| sort / cap | 「開始時刻降順で最新 20 件」 | `aggregate_session_breakdown` の `rows.sort(...); return rows[:top_n]` (`TOP_N_SESSIONS = 20`) |
| active session | 「`ended_at` が未観測のものは「進行中」として表示」 | `_build_session_row` の `ended_at = ends[0].get("timestamp") if ends else None` |

= 5 軸すべてが help-pop 文 ↔ aggregator 実装 で 1:1 対応する。Phase 3 の `TestSessionsHelpPopVerbatim` で structural pin。

## 9. Risks / Tradeoffs

### R1. 採用案: `aggregate_session_breakdown` 内の **single-pass `has_assistant_usage` 構築** + row append filter

| 案 | pros | cons | 採否 |
|----|------|------|------|
| (**A**) 採用案: `period_by_session` 構築ループ内で `has_assistant_usage[sid] = True` を同時 set、row append 直前に `if row is not None and has_assistant_usage.get(sid, False): rows.append(row)` | (1) `_build_session_row` の責任「session_start なし → None」のままで純度を保つ。(2) test が aggregator 入出力レベルで pin され、`_build_session_row` を独立に呼ぶ将来コードを破壊しない。(3) **single pass で flag 構築**、`any(...)` 再走なし → 180 日 hot tier の N_session が大きくても O(N_event) で安定 (Q1 perf 懸念 close、P-reviewer iter1 Q1 反映)。(4) filter を緩める将来変更が flag 1 行の修正で済む | dict 1 つ分の付加メモリ (= N_session 個の bool、無視できる) | ✅ 採用 |
| (A`) 再走版: `aggregate_session_breakdown` の row append ループ内で `if row is not None and any(e.get("event_type") == "assistant_usage" for e in content_evs)` | (1) 実装が短い (1 行) | (1) caller 側で content_evs を再走、N_session × N_events_per_session の重複 traversal、実数値は本 repo の hot tier 規模だと許容内だが single-pass 版が同等の表現性で safer | ❌ 不採用 (Q1 perf 反映、(A) を採る) |
| (B) `_build_session_row` 内で `usage_evs` 長さ 0 → None | (1) row dict に到達する前に枝刈り、メモリ最小。(2) 既存「session_start なし → None」と同じ責任体系で読みやすい | (1) `_build_session_row` の責任が「session_start なし or assistant_usage なし」に拡張、docstring 更新が増える。(2) 「session_start はあるが usage 0 = 進行中 active session」を将来サポートしたいときに `_build_session_row` の責任を巻き戻す必要 (= R2 と関連) | ❌ 不採用 (R2 と組み合わせて regret risk) |

採用: **案 (A) single-pass**。理由は (a) `_build_session_row` の責任を最小限に保つ / (b) 「進行中 active session で assistant_usage がまだ届いていない」ケースを将来別 spec で復活させたいときに aggregator の filter 1 行を緩めるだけで済む / (c) test は aggregator 入出力レベルで pin する方が drift 検知が早い / (d) single-pass 構築で perf 懸念 close。

### R2. 「進行中 active session で assistant_usage 未着」の double-counting / disappearance

- 現実: hooks は `session_start` を Stop hook 前に append、`assistant_usage` も Stop hook で append (= Issue #99 の record_assistant_usage)。"進行中" な session でも assistant メッセージが少なくとも 1 件あれば assistant_usage event は既に書き込まれているので、一般的な flow では「`session_start` だけ存在 / `assistant_usage` 未着」は (i) 起動だけで `/exit` (ii) builtin command のみ / (iii) abort のいずれか = Issue 本文の除外対象と一致。
- ただし **race condition** で「assistant_usage hook 発火前に dashboard が `/api/data` を呼ぶ」可能性は理論上ゼロではない。本 issue の filter ではこの「過渡的に empty に見える live session」も除外される (= 数秒〜数十秒の間 sessions table から消える)。
- 受容判断: Issue #109 が target にしている UX 改善 (signal-to-noise) と「数秒間の live session の消失」を比較して **除外を優先**。代替設計 (= 「session_start から 60 秒未満 + assistant_usage 0 件は残す」grace 条件) は spec を複雑化させるので採らない。
- **対策**: hp-sessions help-pop body に「集計対象外」と書く (Phase 3) + 視覚スモーク (Phase 5) で「empty session が消える振る舞いが意図通り」を確認。

### R3. cross-aggregator drift (session_breakdown vs model_distribution / vs session_stats)

- `session_breakdown` 側で empty session を除外
- `model_distribution` 側は `period_events_raw` から `assistant_usage` event を直接集計 (= empty session には assistant_usage が 0 件なので、そもそも `model_distribution` には影響を与えない、= 元から 0 寄与)
- ∴ `Σ session_breakdown.estimated_cost_usd` と `model_distribution.cost_total` の cap 内一致 invariant (= `tests/test_model_distribution.py` の `test_session_breakdown_total_matches_model_distribution_total`) は **本 issue で破壊されない**
- `session_stats.total_sessions` は raw events から `session_start` event を直接 count、`session_breakdown` の row pool とは独立経路 → empty session も `total_sessions` には含まれる (= footer 247 sessions が unfilter 観測総数のままになる、AC4 と整合)
- Phase 2 RED `test_session_stats_total_sessions_includes_empty` で structural pin

### R4. SHA256 bump 必須 (= shell.html 編集)

- Phase 3 で shell.html line 538-541 (lede) と line 556 (hp-sessions body) を編集 → assembled `_HTML_TEMPLATE` の bytes が変化 → `tests/test_dashboard_template_split.py:28` の `EXPECTED_TEMPLATE_SHA256` が即 RED
- 運用: Phase 3 GREEN commit に **`EXPECTED_TEMPLATE_SHA256` bump も同梱**。bump 値は `python3 -c "import hashlib, dashboard.server as d; print(hashlib.sha256(d._HTML_TEMPLATE.encode()).hexdigest())"` で実測
- bisect note: Phase 3 commit 単独で「shell.html 文言 + sha256 bump + JS sub label 差し替え + ui test 追加」 = 自己完結。Phase 3 を細分割 (lede only / help-pop only / sub label only) すると中間 commit が test RED で残るので **1 commit に固める**

### R5. 「有効セッション」ラベル選定の妥当性

- 候補 (採否):
  - (a) 「有効セッション」 (= 採用): 漢字 4 文字、empty を除外した cohort を直感的に表現。意味の取り違えが少ない
  - (b) 「集計対象セッション」: 9 文字で sub 行が長くなる、横幅 budget が厳しい
  - (c) 「アクティブセッション」: 「進行中 (active = ended_at null)」と意味が衝突するので不採用
  - (d) 「カウント対象」: 内部実装語感、UI surface に持ち込むと UX が悪化
- 採用 (a)。CLAUDE.md vocabulary discipline で「UI surface に持ち込んで意味が立つ語」基準。
- 補足: 国際化 (en) 化したくなった将来は `valid sessions` / `non-empty sessions` あたりの落とし所が想定されるが、本 plan は ja-only で固定 (= 既存 sub 「sessions · projects」の半英混在表記を継承)。

### R6. visual smoke の責務分担

- DOM structural pin は `tests/test_dashboard_sessions_ui.py` の文字列 grep + Node round-trip で済む
- **画面で「有効セッション ${N} · ${projCount} projects」が中黒で正しく描画される** / **hp-sessions tooltip 内文言が読める** は chrome-devtools MCP visual smoke でしか検出できない (= フォント / 中黒 U+00B7 の glyph 問題は test grep を通り抜ける可能性)
- Phase 5 PR Test plan で visual smoke 手順を明記、reviewer による screenshot 確認を担保

### R7. live SSE refresh 経路への透過性

- live SSE は `dashboard/server.py:1439` で `build_dashboard_data` を呼び直す → aggregator が呼ばれ直す → empty session は除外 list で配信される
- frontend の `25_live_diff.js` の `kpi-sess` (line 70) は **`ss.total_sessions`** = `session_stats.total_sessions` 経由なので、live 更新時も「footer = 観測総数」「Sessions ページ sub = 有効セッション数」の二段表示が両方正しく更新される
- Phase 2 の `test_period_filter_and_empty_exclude_compose` でこの compose 経路を pin

### R8. demo fixture / surface fixture の互換性

- `scripts/build_demo_fixture.py:454` / `scripts/build_surface_fixture.py:232` は `build_dashboard_data(events)` を経由するので、aggregator 修正で自動的に「demo の Sessions ページにも empty session が出ない」状態になる
- これは UX 上望ましい (= demo screenshot で `$0.0000` 行が出ない) が、**もし fixture 内で意図的に empty session を見せたい意図** があれば回帰になる → grep で `build_demo_fixture.py` / `build_surface_fixture.py` 内の expected sessions 数を確認するチェック step を Phase 5 PR の Test plan に含める

### R9. `_build_session_row` docstring の更新有無

- 案 A 採用なので `_build_session_row` のシグネチャ / 責任は無変更
- ただし `aggregate_session_breakdown` のトップ docstring (line 358-388) に「session pool 定義」が記述されているので、ここに「**かつ assistant_usage event が 1 件以上ある** session のみ render 対象」 1 文を追加 (= `_build_session_row` ではなく aggregator docstring に書く判断、case 案 A の責任分担と整合)

## 10. Out of Scope (Issue 本文 🚫 セクション継承)

- **他ページ (Overview KPI / Patterns / Quality / Surface) のセッション数表示** 改修 — 本 issue は Sessions ページ table と sub 行のみ
- **`session_stats.total_sessions` 経路の挙動変更** (= footer / header の `247 sessions`) — unfilter 観測総数として保持
- **`model_distribution` (Issue #106) の挙動変更** — 元から assistant_usage を直接集計しているので empty session は寄与なし、別 issue で独立
- **「進行中 active session で assistant_usage 未着」を grace 期間で残す spec** — R2 で受容、複雑化を避けるため採らない
- **国際化 (en label)** — ja-only sub label「有効セッション」を確定、en 対応は別 issue
- **「除外件数を表示」(Q5)** — 表示なしで確定、UI surface に追加要素なし
