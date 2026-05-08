# Plan: Issue #114 — Overview KPI 右 4 枚 (sessions / resume rate / compactions / permission gate) を period toggle に連動させる

## 🎯 Goal

Overview KPI 行 8 枚のうち、現在 period toggle (`7d` / `30d` / `90d` / `all`) で全く変わらない右 4 枚 (`kpi-sess` / `kpi-resume` / `kpi-compact` / `kpi-perm`) を period 連動させ、KPI 行 8 枚全体を「選択中 period の単位で読む」一貫した tile 群に揃える。

具体的には:

1. **server**: `dashboard/server.py:1104` で `aggregate_session_stats(events)` を `aggregate_session_stats(period_events_raw)` に差し替え、`session_stats` の **KPI 表示用 4 sub-field** (`total_sessions` / `resume_rate` / `compact_count` / `permission_prompt_count`) を Period 適用 scope に移動する。**注**: `aggregate_session_stats` の戻り値 dict は内部的には `resume_count` を含む 5 key (verified `dashboard/server.py:1005-1011`)、本 plan で「4 sub-field」と呼ぶのは UI に出す KPI tile 数を指す。
2. **footer**: footer 末尾の `<span class="meta-item"><span class="k">sessions</span><span class="v" id="sessVal">—</span></span>` を **DOM ごと削除** し、`kpi-sess` を sessions の信頼できる唯一の表示にする (ユーザー Q1 採用)。
3. **resume_rate semantics**: period 内 `resume_count / total_sessions` の単純比率をそのまま採用 (ユーザー Q2 採用)。少数件揺れの閾値処理 (`--%` 倒し) は導入しない。
4. **spec / reference doc**: `docs/spec/dashboard-api.md` および `docs/reference/dashboard-aggregation.md` の数値整合を `session_stats` 移動分のみ補正 (= 「period 適用 11→12」「全期間 8→7」「heading の 12→13」)。**注**: dashboard-api.md heading `(12)` は Issue #99 後に 1 度 bump 済 / Issue #106 で未 bump、bullet 実数は 13、`dashboard-aggregation.md` の 22-field 表は `session_breakdown` / `model_distribution` 行自体が未追加。これら **prior #99/#106 drift は本 PR では touch しない** (= scope-out)、別 issue で defer (Risks §3 で disposition 明記)。`docs/spec/dashboard-runtime.md` の「共通 footer に `sessVal`」言及削除と、`.claude/skills/dashboard-wording/SKILL.md` の footer サンプル行削除も併せて実施。
5. **drift guard test**: `tests/test_dashboard_period_toggle.py::TestBuildDashboardDataPeriodApplication::test_full_period_fields_unchanged_across_periods` の `full_period_fields` リストから `session_stats` を外し、新たに「period=7d で `session_stats.total_sessions` が `period=all` より小さくなる」並びに `_filter_events_by_period` の二段 filter (timestamp 第一段 + subagent pair-straddling 第二段、`dashboard/server.py:126` docstring 用語に準拠) が `session_start` / `notification` / `compact_start` 単発 event を timestamp 第一段でだけで正しく cut するという period 連動 drift guard test を加える。
6. **dogfooding**: 本リポジトリの `~/.claude/transcript-analyzer/usage.jsonl` で `7d` / `30d` / `90d` / `all` 切替時に右 4 枚が変動することを目視確認する (Issue #114 AC 最終項)。

非ゴール:

- `aggregate_session_stats(events)` 単体ヘルパーの signature / 内部 logic は無変更 (= pure function を継続、入力 events の意味だけが call site で変わる)。
- KPI tile の help-pop 文言は (現状の文言が period 不変前提を明示していないので) 原則無修正。Phase 5 の 4-axis verification step で念のため確認する。
- Quality / Surface / `subagent_failure_trend` / `compact_density` 等の他の 8 → 7 field は引き続き全期間集計 (period 不変)。

---

## 📋 plan-reviewer 反映ログ

| Proposal | 内容 | 反映箇所 |
| --- | --- | --- |
| (初稿) | — | — |

### 二次レビュー反映 (round 1)

| Proposal | 内容 | 反映箇所 |
| --- | --- | --- |
| P1 (actionable) | 数値整合の base が現状 spec と既に drift。`docs/spec/dashboard-api.md:16,18` prose は `11/8`、`:28` heading は `(12)` (Issue #99 で 1 度 bump、#106 で未 bump)、bullet enumeration は実 13/8。`dashboard-aggregation.md` table は `(11)/(8)/(3)=22` で内部整合だが `session_breakdown` / `model_distribution` の行自体が未追加 (Issue #99/#106 drift)。Issue #114 の改訂数値を 「11→12, 12→13, 8→7」 に補正、prior drift は scope-out で defer (本 PR では touch しない) を明示。 | Goal 項 4 / Critical files §9 §10 / Phase 6 step 0 (grep 確認) / Risks §3 (drift disposition 明記) |
| P2 (actionable) | sentinel bump 手順で test 名 `test_template_sha256_sentinel` が confabulation。実テスト名は `tests/test_dashboard_template_split.py::test_html_template_byte_equivalent_to_pre_split_snapshot` (verified `:48`)、`EXPECTED_TEMPLATE_SHA256` at `:31`、bump history `:29-31` の `# - <hash>: <desc>` 形式。collect 0 (exit 5) を pass と誤読しない warning も追記。 | Critical files §8 / Phase 4 step 9 |
| P3 (actionable) | Issue #115 は PR #116 (`4951fbb`) 経由で `origin/v0.8.1` に **merge 済**。Phase 7 step 0 の rebase 戦略を「fresh checkout from origin/v0.8.1」recipe に確定。`tests/test_dashboard_period_toggle.py:528` docstring (`"11 field 期間適用 / 8 field 不変"`) も改訂対象に追加 (= prior drift と Issue #114 の semantics の両方が絡む)。 | Critical files §7 / Phase 7 step 0 |
| P4 (advisory) | TDD-first の triangulation rhythm を厳守。Phase 1 RED step 1 を「`test_session_stats_total_sessions_shrinks_with_7d` 1 test のみ書いて RED 確認」に絞り、残り 7 test は GREEN 後に additive で追加。`full_period_fields` リストの `session_stats` 削除は spec 改訂 diff なので Phase 6 (Docs) に移動。 | Phase 1 RED 構成 / Phase 6 docs step |
| P5 (advisory) | 4-axis verification の axis 4 (help text vs impl) と footer meta-item 数を **drift guard test 化**。footer の `class="meta-item"` 出現回数 = 1 (lastRx のみ) を assert する test、4 KPI tile help-body に `lifetime` / `期間不変` / `全期間` wording が混入しないことを assert する test、を additive に追加。 | Phase 4 GREEN step 10 / Phase 5 step 0 (新設) |

### 三次レビュー反映 (round 2)

round 2 reviewer は **3 actionable findings 全解消**を verify、verdict は `proceed with minor revisions` (= ready)。新規 actionable はゼロ、advisory 4 + Question 2 が flag。skill workflow の loop exit 条件 (zero actionable) を満たしたが、factual 誤りに起因する advisory (P1 / P2) と plan 実行可能性に直結する Question 1 は同 round で fold して handoff する。

| Proposal | 内容 | 反映箇所 |
| --- | --- | --- |
| P1 (advisory, fold) | `aggregate_session_stats` の戻り値 dict は内部的に `resume_count` を含む **5 key** (verified `dashboard/server.py:1005-1011`)。plan で「4 sub-field」と呼ぶのは UI に出す KPI tile 数のこと。混乱を避けるため "KPI 表示用 4 sub-field" と限定し、内部 5 key は注記で明示。 | Goal 項 1 |
| P2 (advisory, fold) | plan 内の「`_filter_events_by_period` の三段 filter」は helper docstring 用語 (`dashboard/server.py:126` `二段 filter`) と乖離。helper 自体は二段 (timestamp 第一段 + subagent pair 第二段)、call-site の combo (`_filter_events_by_period` + `_filter_usage_events(period_cutoff=)`) のみ三段表現 (`:1046,1048`)。plan / test 内では helper docstring 用語に揃えて「二段」を使い、混乱注記を Risks §8 に追加。 | Goal 項 5 / Risks §8 |
| P3 (advisory, defer to impl) | `_extract_block_after` ヘルパーが現存せず、4 tile help-body は単純な single-line 文字列なので「`id: 'kpi-XXX'` 後の `}` まで substring 抽出」の方が brace-balance parse より robust。実装時に judgement で確定。plan は inline 記載のままで impl 時に最善 form を選択。 | (実装時 fold、plan 改訂なし) |
| P4 (advisory, fold) | Phase 7 step 0 fresh-checkout を `git checkout origin/v0.8.1 && git checkout -b ...` の 2 段から `git checkout -b feature/114-... origin/v0.8.1` の 1 段に consolidate (detached HEAD warning 回避)。 | Phase 7 step 0 |
| Q1 (advisory, fold) | `cfc6111` plan-only commit の処遇を Phase 7 step 0 で明文化。本 plan ファイル (`docs/plans/114-overview-kpi-period-sync.md`) は `feature/115-...` HEAD のみに存在するので、fresh checkout 後に (1) cherry-pick (推奨) または (2) Phase 1 RED 最初の commit に同梱、で持ち込む。 | Phase 7 step 0 |
| Q2 (already-handled) | `dashboard-api.md` heading-bullet mismatch が 1 → 2 に拡大する件。plan disposition (Risks §3) で「Issue #99/#106 prior drift は本 PR で touch しない」を明示済 = 既知の tradeoff。本 PR では heading 12→13 / bullet 13→14 で +1 mismatch 拡大を許容、後続 issue で根本解消。改訂なし。 | Risks §3 (既存) |

---

## 🌿 Branch / base

- ブランチ名: `feature/114-overview-kpi-period-sync`
- ベース: `v0.8.1` (release-branch model に従う、ユーザー Q4 確定)
- 親 milestone: `v0.8.1`

Phase 7 step 0 で `git ls-remote --heads origin v0.8.1` により v0.8.1 ブランチの存在を verify し、無ければ CLAUDE.md `## Branching workflow` / `docs/reference/branching-workflow.md` に従って main から `v0.8.1` を切ってから本ブランチを派生させる (Issue #115 plan の Phase 7 step 0 と同じ pattern)。

PR base は `v0.8.1`。

---

## 📂 Critical files

### 変更対象 (production)

1. **`dashboard/server.py`**
   - `:1104` — `"session_stats": aggregate_session_stats(events)` → `aggregate_session_stats(period_events_raw)` に差し替え。これが本 issue のコア 1 行。
   - `:1042` — `build_dashboard_data` docstring の「`Quality / Surface / session_stats の 8 field は **常に全期間** で集計する (period 不変)`」を「`Quality / Surface の 7 field は **常に全期間**`」に改訂し、`session_stats` を `period 適用` 行 (現「11 field」を「12 field」に改訂) に移動する。
   - `:99-100` — コメント (`session_*, notification, instructions_loaded, compact_*, subagent_stop は session_stats / health_alerts 等に分かれて表示される`) は logic 説明として正しい (= `_SKILL_USAGE_EVENT_TYPES` の除外説明) ので無修正。`session_stats` の period 連動化と直接関係しない。
   - `:121` — `_filter_events_by_period` docstring 内の「`全期間 8 field (Quality 4 + Surface 3 + session_stats) には未 filter events を渡す`」を「`全期間 7 field (Quality 4 + Surface 3) には未 filter events を渡す`」に改訂。
   - `:116` — `Overview / Patterns aggregator にのみ渡す view を返す。Quality / Surface aggregator は unfiltered events を受ける` の文脈は変わらないが、もし `session_stats` への言及があれば併せて改訂。

2. **`dashboard/template/shell.html:638`** — footer の `<span class="meta-item"><span class="k">sessions</span><span class="v" id="sessVal">—</span></span>` の DOM 行を **削除**。

3. **`dashboard/template/scripts/20_load_and_render.js:25-26`** — `document.getElementById('sessVal').textContent = ss.total_sessions || 0;` の行とその上のコメント (`// footer の <span class="k">sessions</span> (Issue #89) と重複しないよう数字単独で書き出す。`) を削除。`const ss = data.session_stats || {};` 自体は kpi-sess / kpi-resume / kpi-compact / kpi-perm が引き続き読むので残す。

4. **`dashboard/template/styles/30_pages.css`** — `.app-footer` 上の `/* 全ページ共通の app-footer。conn-status / lastRx / sessVal を集約。 */` コメント (該当箇所を grep で特定) から `/ sessVal` を除去。CSS rule 本体 (flex 余白等) は無改変。`gap: 16px` の余白は item 数が 3 → 2 になっても見栄えに問題が出ない (gap は item 間 padding なので、item が 1 個減る分余白が消えるだけで、左寄せ/右寄せの flex は崩れない)。Phase 5 視覚スモークで確認する。

### 変更対象 (test)

5. **`tests/test_dashboard_router.py`**
   - `:6` — モジュール docstring の `lastRx / sessVal` → `lastRx`。
   - `:127` — `assert 'id="sessVal"' in footer, "sessVal should be in app-footer"` → `assert 'id="sessVal"' not in footer, "sessVal should be removed (Issue #114)"` に反転。
   - `:150` — `test_existing_widget_ids_preserved` の expected ID 配列から `'sessVal'` を削除。

6. **`tests/test_dashboard_template_split.py:84`** — `test_html_template_contains_critical_dom_anchors` の expected ID リストから `"sessVal"` を削除。

7. **`tests/test_dashboard_period_toggle.py`**
   - `:528` — `class TestBuildDashboardDataWithPeriod` docstring の `"11 field 期間適用 / 8 field 不変"` を `"12 field 期間適用 / 7 field 不変"` に書き換え (= Issue #114 の semantics 反映)。これは **prior #99/#106 drift とは独立** に session_stats 移動の影響だけを反映する diff (heading numbers と同じく `+1/-1` 補正)。
   - `:614` — `test_full_period_fields_unchanged_across_periods` の `full_period_fields` リストから `"session_stats"` を削除し、リストが 8 → 7 要素になる。**この diff は spec 改訂を test に反映する diff (= server 側変更前後で test 自体は pass 維持) なので Phase 6 (Docs) に移動**。
   - `:1237` — Node round-trip stub の固定 fixture `"session_stats": {"total_sessions": 1, ...}` 自体は signature 不変 (Node-side template render を試すだけのスタブ data) なので無修正で OK。
   - **新規 test class** (Phase 1 RED): `TestSessionStatsPeriodApplied` を追加。**first failing test は `test_session_stats_total_sessions_shrinks_with_7d` 1 つのみ書いて RED 確認** → Phase 1 GREEN 完了 → 残り 7 test を triangulation で additive 追加。

8. **`tests/test_dashboard_template_split.py:31` `EXPECTED_TEMPLATE_SHA256`** — sentinel sha bump (shell.html の 1 行削除に伴う bytes 変化を反映)。Phase 4 GREEN step で AssertionError 出力 (test 名 `test_html_template_byte_equivalent_to_pre_split_snapshot`, `:48`) から actual hash を抽出 → `:31` の定数差し替え + `:29-31` bump history block (`# Bump history (1 行 / issue):`) に `# - <new-sha>: Issue #114 footer sessVal 削除` を 1 行追記 (verified format: `# - 6db5eea86656...: Issue #115 sessions period toggle slot ...`)。

### 変更対象 (docs)

> **数値整合の base note** (本 §の前提): `dashboard-api.md` の現状は drift しており、prose `:16,18` は `11/8` (= pre-#99/#106 base)、heading `:28` は `(12)` (= Issue #99 で 1 度 bump 済 / #106 で未 bump)、bullet 実数は `13/8`。`dashboard-aggregation.md` の 22-field 表は `(11)/(8)/(3)` で内部整合だが `session_breakdown` / `model_distribution` の行自体が未追加。**Issue #114 では session_stats の +1/-1 移動分のみ touch** し、prior drift の根本解消は別 issue に defer する (Risks §3 disposition)。具体的な数値書き換えは「現状リテラル + 1 / -1」で機械的に決める。

9. **`docs/spec/dashboard-api.md`**
   - `:16-19` (prose) — `Overview / Patterns / KPI counter の **11 field**` → `**12 field**`、`Quality / Surface / session_stats の **8 field**` → `Quality / Surface の **7 field**`。
   - `:28` heading — `### Period 適用 scope (12 field)` → `(13 field)`。
   - `:29-34` bullet list — 末尾に新 bullet `- Sessions: session_stats (Issue #114 / v0.8.1〜)` を追記 (構成 sub-field 4 つ: `total_sessions`, `resume_rate`, `compact_count`, `permission_prompt_count`)。実 bullet 数は 13 → 14 になるが、heading は `(13)` のまま (= Issue #99/#106 prior drift を解消しない、defer)。
   - `:36` heading — `### 全期間 (period 不変) scope (8 field)` → `(7 field)`。
   - `:38-41` — `- session_stats (lifetime metric)` 行を削除。実 bullet 数は 8 → 7 (heading と整合)。
   - `:73` 周辺 — `## トップレベル形` の JSON 例の `"session_stats": { ... }` 行は plain JSON literal なので無修正。

10. **`docs/reference/dashboard-aggregation.md:204-208`** — 22-field 分類マップ表:
    - `:206` `Period-applicable (11)` → `(12)`、行末に ` / session_stats (Sessions, Issue #114)` を追記 (注: cell 内に enumerate されている fields の数は実は 11、Issue #99 / #106 で `session_breakdown` / `model_distribution` が未追加 = prior drift。`(12)` は「pre-#114 base 11 + 1」を意味し、prior drift は本 PR では touch しない)。
    - `:207` `Full-period (8)` → `(7)`、行末の `/ session_stats (resume/compact-count は lifetime 不変)` を削除し、`session_stats` への言及を完全に消す (lifetime 不変ではなくなるため)。「lifetime」コメンタリも削除。
    - `:200,202` の `~22 field` / `22-field 分類マップ` literal は `~` でぼかしてあるので無修正 (= prior drift を本 PR で解消しない方針と整合)。

11. **`docs/spec/dashboard-runtime.md:108`** — 「共通 footer (`<footer class="app-footer">`): conn-status / lastRx / sessVal / クレジット行」の `sessVal /` を削除し、「conn-status / lastRx / クレジット行」に。

12. **`.claude/skills/dashboard-wording/SKILL.md:151-156`** — Footer サンプルブロックから `<span class="meta-item"><span class="k">sessions</span><span class="v" id="sessVal">—</span></span>` 行を削除。前後の説明 (「`最終更新` は日本語、`sessions` は英語 (Claude-spec)」) も `sessions` 言及を削るか、サンプル削除に伴って「Claude-spec か一般語かで分ける」抽象 rule だけ残す形に整える。

### 参照対象 (Read のみ)

- `dashboard/server.py:989-1011` — `aggregate_session_stats` の pure function 定義 (signature 不変であることの裏付け)。
- `dashboard/server.py:107-201` — `_filter_events_by_period` の二段 filter (timestamp 第一段 + subagent pair 第二段)。`session_start` / `compact_start` / `notification` は subagent 系 pair に属さないので **第一段 timestamp filter のみが効き、第二段 pair-straddling は no-op**。これが「pair-straddling filter で session boundary が漏れない」根拠 (= straddling の対象外なので漏れる経路自体が無い)。
- `dashboard/template/scripts/25_live_diff.js:52-73` — `buildLiveSnapshot` の `kpi-sess` / `kpi-resume` / `kpi-compact` / `kpi-perm` 読み出し。`data.session_stats` の読み出しは無変更 (server から来る 4 sub-field の意味が period 連動になるだけで、読み出し側の logic は同一)。
- `tests/test_dashboard.py:674-728` — `aggregate_session_stats` 単体テスト。helper の signature が不変なので無変更。

---

## 🧪 TDD test plan (RED first)

CLAUDE.md TDD-first 原則に従い、**failing test (RED) を先に書いてから実装 (GREEN)** の順序を厳守する。

### 新規 test class (RED → GREEN を経る)

#### 1. `tests/test_dashboard_period_toggle.py::TestSessionStatsPeriodApplied`

新規。`session_stats` の 4 sub-field が period 連動になる drift guard。

| Test | 内容 | RED が示す必要がある failure |
| --- | --- | --- |
| `test_session_stats_total_sessions_shrinks_with_7d` | period=`all` で 3 件、period=`7d` で 1 件 (= cutoff 外 2 件) になる events を作り、`build_dashboard_data(events, period="7d", now=_FIXED_NOW)["session_stats"]["total_sessions"]` < `period="all"` の同 field を assert | 現実装は `session_stats` を未 filter events で集計するので `total_sessions` が period 不変。RED は「period=7d でも total_sessions=3 のまま」で失敗する |
| `test_session_stats_resume_rate_uses_period_internal_ratio` | period 内に `session_start (source=resume)` 1 件 + `session_start (source=startup)` 1 件、period 外に `session_start (source=resume)` 5 件を置き、period=`7d` で `resume_rate == 0.5` (= 1/2) を assert (= ユーザー Q2 採用の period 内 ratio semantics) | 現実装は全期間の resume_count=6 / total=7 ≈ 0.857 になり、period 内 0.5 と乖離 |
| `test_session_stats_compact_count_period_applied` | period 外 `compact_start` 3 件、period 内 1 件で `period="7d"` の `compact_count == 1` を assert | 現実装は 4 件のまま |
| `test_session_stats_permission_prompt_count_period_applied` | period 外 `notification(notification_type=permission_prompt)` 2 件、period 内 1 件で `period="7d"` の `permission_prompt_count == 1` を assert | 現実装は 3 件のまま |
| `test_session_stats_period_all_unchanged_from_pre_change` | period=`all` での `session_stats` が「全 events を直接 `aggregate_session_stats` に渡した結果」と一致することを assert (= `period=all` パスでの後方互換性 pin) | 同一の値を返すべきなので RED は出ないが (additive 検証)、RED 段階では一旦書いて、GREEN diff 適用後に value が破壊されないかの drift guard として機能 |
| `test_session_stats_session_start_at_period_boundary_kept` | `session_start.timestamp == now - 7days` (`<=` 境界) を持つ event が period=`7d` で kept されることを assert (`_filter_events_by_period` の `cutoff <= ts <= now` 包含境界) | 既存 timestamp filter の包含境界が `session_start` event にも効くことの構造的保証。境界 off-by-one regression 検出 |
| `test_session_stats_session_start_just_outside_period_dropped` | `session_start.timestamp == now - (7days + 1s)` が period=`7d` で drop されることを assert | 同上、negative 側 |
| `test_session_stats_no_pair_straddling_for_session_start` | `session_start` 単体は subagent pair の構成要素ではないので、`_filter_events_by_period` の第二段で pull-back されないことを assert (= cutoff より過去の `session_start` が timestamp 第一段で drop されたら最終結果からも drop されたまま、第二段で「pair-straddling」と誤認されない) | 第二段 logic は `_bucket_events` で subagent_type key を作るので `session_start` (event_type ≠ subagent_*) は対象外。境界 off-by-one regression や future logic drift の構造的 pin |

`_FIXED_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)` は既存 fixture と共通使用 (line 30)。`_ts(now, days=N)` ヘルパー (line 33) も流用。

### 既存 test の更新

#### 2. `tests/test_dashboard_period_toggle.py:602-621::test_full_period_fields_unchanged_across_periods`

`full_period_fields` リストから `"session_stats"` を削除する。RED 段階ではこのリスト変更を test の "spec 側" 修正として扱う (= 失敗 test を fix するのではなく、現契約の 8 → 7 改訂に追従)。

```python
# Before:
full_period_fields = [
    "subagent_failure_trend", ..., "compact_density",
    "session_stats",   # ← 削除
    "skill_invocation_breakdown", ...
]
# After: 7 elements (session_stats を Period-applied 群に移動)
```

#### 3. `tests/test_dashboard_router.py:111-127::TestCommonShell::test_session_value_in_app_footer`

footer に `sessVal` が **存在しないこと** に反転する (Issue #114 で削除されるため)。test 名も `test_session_value_removed_from_app_footer` に rename する。

#### 4. `tests/test_dashboard_router.py:140-156::test_existing_widget_ids_preserved`

`'sessVal'` を expected list から削除。残り 17 個に。

#### 5. `tests/test_dashboard_template_split.py:80-89::test_html_template_contains_critical_dom_anchors`

`"sessVal"` を expected DOM ID list から削除。

#### 6. `tests/test_dashboard_template_split.py` の `EXPECTED_TEMPLATE_SHA256`

sentinel sha bump。Phase 4 GREEN step で AssertionError から actual hash を抽出して定数を更新し、`# - <new-sha>: Issue #114 footer sessVal 削除` の bump history コメントを 1 行追記する (Issue #115 plan で確立した慣習に準拠)。

### 既存 test (無変更で動作確認のみ)

- `tests/test_dashboard.py::test_aggregate_session_stats_*` — `aggregate_session_stats` ヘルパーは pure function で signature 不変なので無修正で pass する。
- `tests/test_dashboard_live_diff.py` — `buildLiveSnapshot` は `data.session_stats.total_sessions` 等を読むだけで、period 連動の影響を受けない。

---

## 📐 Phased steps

CLAUDE.md の dashboard 6-phase plan-driven TDD rhythm に従う。本 issue は 「server 1 行差し替え + footer 削除 + spec doc 更新」のスコープなので、Phase 2 (secondary aggregator) は該当無しでスキップ。

### Phase 1 — Server unit test RED → GREEN (helper signature 不変 + build_dashboard_data 統合)

> **TDD-first triangulation rhythm** (Proposal 4 反映): 最初に **1 test だけ** RED で pin、GREEN 確認後に残りの drift guard test を additive で追加する。`full_period_fields` リスト編集 / docstring 数値書き換え等の spec 改訂 diff は Phase 6 (Docs) に集約 (= Phase 1 の中で test と spec を並行に動かさない)。

**RED (first failing test pin)**:

1. `tests/test_dashboard_period_toggle.py` に `TestSessionStatsPeriodApplied` class 骨組みを作り、最初に **`test_session_stats_total_sessions_shrinks_with_7d` 1 test のみ** を書く。fixture: period=`all` で 3 件 / period=`7d` で 1 件 (= cutoff 外 2 件) になる events を構築し、`build_dashboard_data(events, period="7d", now=_FIXED_NOW)["session_stats"]["total_sessions"] < period="all"` を assert。
2. `pytest tests/test_dashboard_period_toggle.py::TestSessionStatsPeriodApplied::test_session_stats_total_sessions_shrinks_with_7d -x` で RED を確認 (= 現実装は `aggregate_session_stats(events)` で全期間集計するので period=7d でも total_sessions=3 のまま failing)。

**GREEN (single 1-line diff to flip RED → GREEN)**:

3. `dashboard/server.py:1104` — `"session_stats": aggregate_session_stats(events)` → `aggregate_session_stats(period_events_raw)` に差し替え。
4. step 2 と同じ pytest コマンドで GREEN を確認。
5. `pytest tests/test_dashboard.py::TestAggregateSessionStats` も pass (helper signature 不変)。

**Triangulation (additive drift guard tests)**:

6. RED → GREEN 確認後、残り 7 test を `TestSessionStatsPeriodApplied` に additive 追加:
   - `test_session_stats_resume_rate_uses_period_internal_ratio` (Q2 採用 semantics)
   - `test_session_stats_compact_count_period_applied`
   - `test_session_stats_permission_prompt_count_period_applied`
   - `test_session_stats_period_all_unchanged_from_pre_change` (period=`all` の後方互換性 pin)
   - `test_session_stats_session_start_at_period_boundary_kept` (`now - 7days` 包含境界)
   - `test_session_stats_session_start_just_outside_period_dropped` (`now - 7days - 1s` 除外境界)
   - `test_session_stats_no_pair_straddling_for_session_start` (第二段 pair-straddling no-op の構造的 pin)
7. `pytest tests/test_dashboard_period_toggle.py::TestSessionStatsPeriodApplied -v` 全 8 test pass を確認。

> 注: server.py docstring (`:120-121` / `:1041-1042`) の数値書き換えと `tests/test_dashboard_period_toggle.py:528,614` の docstring / `full_period_fields` リスト編集は **Phase 6 (Docs) に集約**。Phase 1 では server logic 1 行差し替えだけが production diff。

### Phase 2 — Secondary aggregator (該当無し、スキップ)

`session_stats` のソースとなる helper (`aggregate_session_stats`) は無変更。`build_dashboard_data` 内の単独 1 行差し替えのみなので secondary aggregator は無い。

### Phase 3 — `build_dashboard_data` 統合 (Phase 1 GREEN と一体化、追加作業無し)

Phase 1 GREEN step #4 で完了。additive key の追加は無く、既存 key の入力 view が変わるだけ (= field 名 / 形 / 型は不変)。

### Phase 4 — Template DOM RED → GREEN (footer 削除 + sentinel bump)

**RED**:

1. `tests/test_dashboard_router.py:123-127` の `test_session_value_in_app_footer` を反転 (`assert 'id="sessVal"' not in footer, "sessVal should be removed (Issue #114)"`) し、test 名を `test_session_value_removed_from_app_footer` に rename。
2. `tests/test_dashboard_router.py:148-154` の expected ID list から `'sessVal'` を削除。
3. `tests/test_dashboard_template_split.py:80-89` の expected ID list から `"sessVal"` を削除。
4. `tests/test_dashboard_router.py:6` の docstring 内 `lastRx / sessVal` → `lastRx` に修正。
5. `pytest tests/test_dashboard_router.py tests/test_dashboard_template_split.py -x` で RED を確認 (= 反転した assertion が現 template の `sessVal` 存在で失敗する想定)。

**GREEN**:

6. `dashboard/template/shell.html:638` — `<span class="meta-item"><span class="k">sessions</span><span class="v" id="sessVal">—</span></span>` 行を削除。
7. `dashboard/template/scripts/20_load_and_render.js:25-26` — `// footer の <span class="k">sessions</span> ...` コメント行と `document.getElementById('sessVal').textContent = ss.total_sessions || 0;` 行の **2 行を削除**。`const ss = data.session_stats || {};` (line 16) は kpi-* で読まれるため残す。
8. `dashboard/template/styles/30_pages.css` — `app-footer` 上のコメント `/* 全ページ共通の app-footer。conn-status / lastRx / sessVal を集約。 */` から `/ sessVal` を削除。CSS rule body は無改変。
9. **Sentinel bump** (P2 反映 / 実テスト名 verified):
   ```bash
   pytest tests/test_dashboard_template_split.py::test_html_template_byte_equivalent_to_pre_split_snapshot -x 2>&1 | grep -E "actual:|expected:"
   ```
   - **collect 0 (exit 5) を pass と誤読しない warning**: 出力に `actual:   <hex>` 行があることを確認してから定数差し替えに進む。`pytest -x` が `collected 0 items` で exit 5 を返した場合 (= test 名 typo)、grep の出力は空で「sentinel が動いた」と勘違いする trap。
   - 実 sha が抽出できたら `tests/test_dashboard_template_split.py:31` の `EXPECTED_TEMPLATE_SHA256` を新値に更新し、`:29-31` の `Bump history` block に `# - <new-sha>: Issue #114 footer sessVal 削除` を 1 行 (verified format: `# - 6db5eea86656...: Issue #115 sessions period toggle slot ...`) で追記。

`pytest tests/test_dashboard_router.py tests/test_dashboard_template_split.py -v` で全 pass を確認。

**GREEN (additive drift guard / Proposal 5 反映)**:

10. `tests/test_dashboard_router.py::TestCommonShell` に新規 test 追加 (footer meta-item 数の構造的 pin):
    ```python
    def test_app_footer_meta_items_count_after_sessVal_removal(self):
        """Issue #114: footer .meta 内の class="meta-item" は exactly 1 (lastRx のみ)。conn-status は別 class なので除外。"""
        template = _load_template()
        footer = _extract_footer(template)
        count = footer.count('class="meta-item"')
        assert count == 1, \
            f"Footer should have 1 meta-item (lastRx only) post-#114; conn-status uses class=conn-status. Got {count}"
    ```
    - `_extract_footer` ヘルパー (= `<footer class="app-footer">` から `</footer>` までを切り出す) は同 module 内 (or test_dashboard_router.py の既存 helper) を流用。無ければ inline でも可。
    - 実装時に shell.html 内の `<span class="conn-status">` が同じ `meta` 内 sibling であることを再確認 (= count に交じらない確認)。

### Phase 5 — CSS / JS renderer + 視覚スモーク (footer 余白 + 4-axis verification)

**Step 0 — 4-axis axis 4 (help text vs impl) を test 化** (Proposal 5 反映):

`tests/test_dashboard_period_toggle.py` (or 新規 `tests/test_dashboard_help_text_period_consistency.py`) に新規 test 追加:

```python
def test_kpi_help_body_period_neutral_wording_post_114(self):
    """Issue #114 で kpi-sess/-resume/-compact/-perm が period 連動になったので、
    対応 help-body に period 不変前提の wording (`lifetime` / `期間不変` / `全期間` / `lifetime metric`) が
    入り込まないことを assert (= future drift catcher)。"""
    js_path = Path(__file__).parent.parent / "dashboard" / "template" / "scripts" / "20_load_and_render.js"
    js = js_path.read_text(encoding="utf-8")
    # 4 tile の helpBody は kpi-sess / kpi-resume / kpi-compact / kpi-perm のブロック内
    # 簡易: 4 tile の id 行を含むブロック (= line 57-66 周辺) に上記 wording が現れないこと
    sess_block = _extract_block_after(js, "id: 'kpi-sess'", until_close_brace=True)
    resume_block = _extract_block_after(js, "id: 'kpi-resume'", until_close_brace=True)
    compact_block = _extract_block_after(js, "id: 'kpi-compact'", until_close_brace=True)
    perm_block = _extract_block_after(js, "id: 'kpi-perm'", until_close_brace=True)
    forbidden = ["lifetime", "期間不変", "全期間"]
    for block_name, block in (("kpi-sess", sess_block), ("kpi-resume", resume_block),
                               ("kpi-compact", compact_block), ("kpi-perm", perm_block)):
        for word in forbidden:
            assert word not in block, \
                f"{block_name} helpBody contains forbidden period-invariant wording '{word}' (Issue #114)"
```

`_extract_block_after` ヘルパーは inline で書ける範囲 (= `find` + `{` `}` バランス簡易 parse)。実装時に既存 test_dashboard_router の helper があれば流用。

**Step 1 — 視覚スモーク**:

1. `python -m dashboard.server` 経由でローカル起動 (README の `### /claude-transcript-analyzer:usage-dashboard — ブラウザダッシュボード` slash command 参照)、`http://localhost:<port>` を開いて以下を目視:
   - footer 余白: `conn-status` ・ `最終更新` の 2 item が左寄せ (もしくは元 layout)、右クレジットとの間隔が `gap: 16px` で潰れていないこと。
   - `kpi-sess` / `kpi-resume` / `kpi-compact` / `kpi-perm` の 4 tile が period toggle (`7d` / `30d` / `90d` / `all`) 切替で値変動すること。

**Step 2 — 4-axis verification 実施記録** (CLAUDE.md dashboard panel discipline):

4 KPI tile の `helpBody` (`20_load_and_render.js:57-66`) を読み直し、現状文言が period 連動と矛盾しないか確認:
- `kpi-sess` `helpBody`: `SessionStart hook で観測された Claude Code セッションの開始回数。同じ session_id の startup と resume は別セッションとして数える。` — 「lifetime」等の表現無し → 無修正 OK。
- `kpi-resume` `helpBody`: `セッション開始のうち <code>--resume</code> での再開（source="resume"）が占める割合。新規 startup と区別される。` — 比率説明のみ、period 不変前提 wording 無し → 無修正 OK。
- `kpi-compact` `helpBody`: `コンテキスト自動圧縮（PreCompact hook）の発生回数。auto / manual の両方を合算。` — 「lifetime」「全期間」無し → 無修正 OK。
- `kpi-perm` `helpBody`: `承認依頼（Notification の type=<code>permission</code> / <code>permission_prompt</code>）の発生回数。多いと作業中の中断が増えていることを示す。` — 無修正 OK。

4 tile すべて axis 4 OK の判定を plan-reviewer 反映ログに残す。Step 0 の test がこの判定を構造的に future-proof にする。

**Step 3 — dogfooding** (Issue #114 AC 最終項): 本リポジトリ自身の `~/.claude/transcript-analyzer/usage.jsonl` で `7d` / `30d` / `90d` / `all` 切替時に右 4 枚が変動することを目視確認。**screenshot 2 枚以上保存** (= `period=all` / `period=7d` の比較) → Phase 7 step 4 で PR body に貼る。

### Phase 6 — Docs (spec 数値整合 + footer 言及削除 + memory file 不要判定)

**Step 0 — 現状リテラル grep で base 確認** (Proposal 1 反映):

```bash
grep -n "11 field\|12 field\|13 field\|14 field\|7 field\|8 field\|22 field\|22-field\|24 field" \
  docs/spec/dashboard-api.md \
  docs/reference/dashboard-aggregation.md \
  dashboard/server.py \
  tests/test_dashboard_period_toggle.py
```

実行結果は plan-reviewer 反映ログに verify 済 (`docs/spec/dashboard-api.md:16,18,28,36` / `docs/reference/dashboard-aggregation.md:200,202,206,207,208` / `dashboard/server.py:120,121,1041,1042` / `tests/test_dashboard_period_toggle.py:528`)。Issue #99/#106 の prior drift は本 PR では touch しない (= scope-out)、各 hit に「session_stats 移動分のみ +1/-1 補正」を機械的に適用する。

**Step 1 — `dashboard/server.py` docstring**:

- `:120` `period 適用 11 field (KPI 4 + Overview 4 + Patterns 3)` → `period 適用 12 field (KPI 4 + Overview 4 + Patterns 3 + session_stats 1)` (= 11 + 1)
- `:121` `全期間 8 field (Quality 4 + Surface 3 + session_stats)` → `全期間 7 field (Quality 4 + Surface 3)` (= 8 - 1)
- `:1041` `Overview / Patterns / KPI counter の 11 field` → `12 field` (= 同 +1)
- `:1042` `Quality / Surface / session_stats の 8 field` → `Quality / Surface の 7 field` (= 同 -1)

**Step 2 — `docs/spec/dashboard-api.md`**:

- `:16` prose `Overview / Patterns / KPI counter の **11 field**` → `**12 field**`
- `:18` prose `Quality / Surface / session_stats の **8 field**` → `Quality / Surface の **7 field**`
- `:28` heading `### Period 適用 scope (12 field)` → `(13 field)` (= 現 12 から +1)
- `:29-34` bullet 末尾に新 bullet `- Sessions: session_stats (Issue #114 / v0.8.1〜)` を追記 (4 sub-field: `total_sessions`, `resume_rate`, `compact_count`, `permission_prompt_count`)。**注**: bullet 実数は 13 → 14 になり heading `(13)` と 1 ずれが残るが、これは Issue #99/#106 prior drift で本 PR scope 外。Risks §3 で disposition 明記。
- `:36` heading `### 全期間 (period 不変) scope (8 field)` → `(7 field)`
- `:38-41` の `- session_stats (lifetime metric)` 行を削除。bullet 実数 8 → 7 (heading 整合)。
- JSON 例 `## トップレベル形` (`:73`) は plain literal なので無修正。

**Step 3 — `docs/reference/dashboard-aggregation.md`**:

- `:206` `**Period-applicable (11)** — KPI / Overview / Patterns` → `**Period-applicable (12)** — KPI / Overview / Patterns / Sessions`、cell 末尾 `(Patterns)` の後に ` / session_stats (Sessions, Issue #114)` を追記。
- `:207` `**Full-period (8)** — Quality / Surface / lifetime` → `**Full-period (7)** — Quality / Surface`、cell 末尾の ` / session_stats (resume/compact-count は lifetime 不変)` および「lifetime 必須」「lifetime」言及を `session_stats` 関連箇所のみ削除 (`skill_lifecycle (first_seen/last_seen は lifetime 必須)` の lifetime コメンタリは無修正 = 別 field の生 lifetime は引き続き lifetime metric)。
- `:200,202` `~22 field` / `22-field 分類マップ` literal は `~` でぼかしてあるので無修正 (= prior drift を本 PR で解消しない方針と整合)。

**Step 4 — `tests/test_dashboard_period_toggle.py` の spec 改訂 diff** (Phase 1 から移動):

- `:528` `class TestBuildDashboardDataWithPeriod` docstring の `"11 field 期間適用 / 8 field 不変"` → `"12 field 期間適用 / 7 field 不変"`
- `:614` `test_full_period_fields_unchanged_across_periods` の `full_period_fields` リストから `"session_stats"` を削除し、リストが 8 → 7 要素に。

**Step 5 — `docs/spec/dashboard-runtime.md:108`**:

- `共通 footer (...): conn-status / lastRx / sessVal / クレジット行` から `sessVal /` を削除し、`conn-status / lastRx / クレジット行` に。

**Step 6 — `.claude/skills/dashboard-wording/SKILL.md:151-156`**:

- Footer サンプルブロックから `<span class="meta-item"><span class="k">sessions</span><span class="v" id="sessVal">—</span></span>` 行を削除。続く文言 (`最終更新` は日本語、`sessions` は英語 (Claude-spec)) は `sessions` 言及を削るか、抽象 rule 形 (`Claude-spec か一般語かで分ける`) だけ残すよう書き換え。

**Step 7 — memory file 判定**:

本変更は「既存 field の入力 view 変更 + footer 削除 + spec 数値の整合」のみで新規概念は導入しないため、`docs/reference/` 配下の新規 memory file は不要。Issue #115 と同じ判定 (= 既存 reference 文書の局所改訂のみ)。

### Phase 7 — PR

**Step 0 — 前提 verify (P3 反映 / Issue #115 merge 済 を反映)**:

Issue #115 PR は **`origin/v0.8.1` に既に merge 済** (`4951fbb Merge pull request #116 from tetran/feature/115-sessions-period-toggle-slot`、commit `73f449d` + `1c77008` 包含、verified)。したがって本 branch は **fresh checkout from `origin/v0.8.1`** で派生する:

```bash
git fetch origin
git ls-remote --heads origin v0.8.1   # 存在 verify
gh pr list --search "issue:114 in:body" --state all   # 重複 PR 防止
git checkout -b feature/114-overview-kpi-period-sync origin/v0.8.1   # detached HEAD を経由しない一発 branch 派生
```

**重要**: 現在の HEAD (`feature/115-sessions-period-toggle-slot` branch、commit `cfc6111` で **本 plan ファイル (`docs/plans/114-overview-kpi-period-sync.md`) を含む plan-only commit** を持つ) を base にしないこと。Issue #115 の merge commit (`4951fbb`) は `origin/v0.8.1` にあるが `cfc6111` plan-only commit は `origin/v0.8.1` には未反映なので、本 branch は混入を避けるため fresh から派生する。

**`cfc6111` plan-only commit の処遇** (Round 2 reviewer Q1 反映): fresh checkout 直後の `feature/114-overview-kpi-period-sync` には Issue #114 の plan ファイルが**存在しない**ので、以下のいずれかで持ち込む:

1. **Cherry-pick** (推奨 — git 履歴を 1 commit として保つ):
   ```bash
   git cherry-pick cfc6111 -- docs/plans/114-overview-kpi-period-sync.md
   ```
   ※ `cfc6111` は Issue #115 plan も含むので、`-- <path>` 指定で Issue #114 plan のみ取り出す (or `git show cfc6111 -- docs/plans/114-overview-kpi-period-sync.md > docs/plans/114-overview-kpi-period-sync.md` → `git add ... && git commit`)。
2. **First commit に同梱** (代替): Phase 1 RED の最初の commit に plan ファイルも含める (= "docs(plan) + RED tests" 1 commit)。

どちらでも構わないが、後続の git history が読みやすいのは (1)。

`tests/test_dashboard_period_toggle.py` の周辺は Issue #115 で既に編集済 (verified `:528` docstring 含む)。本 plan は Phase 1 RED で同 file 末尾に `TestSessionStatsPeriodApplied` class を **append-only** で追加し、既存 class の前後への挿入は行わない (= line 番号が動かないので Issue #115 後の他 PR とも conflict しにくい構造)。

**Step 1 — PR 作成**:

- base `v0.8.1`, head `feature/114-overview-kpi-period-sync`
- タイトル: `feat(dashboard): sync Overview right-4 KPI with period toggle (#114)`

**Step 2 — PR body**:

- Goal / Critical files / dogfooding 結果 / TDD test 一覧 / spec 改訂サマリ
- 「`session_stats` 4 sub-field を Period-applied scope に移動 + footer `sessVal` 削除」を明示
- 「Issue #99/#106 prior drift は本 PR では touch しない」disposition も明記 (Risks §3 と同期)

**Step 3 — dogfooding screenshot 添付**:

Phase 5 step 3 で取得した `period=all` / `period=7d` 比較 screenshot を 2 枚以上 PR body に貼付。右 4 枚 (`kpi-sess` / `kpi-resume` / `kpi-compact` / `kpi-perm`) の値変動を視覚的に証拠化。

**Step 4 — merge**:

CI green を確認後 merge。

---

## ⚠️ Risks / tradeoffs

1. **Resume rate の少数件揺れ (Q2 採用の帰結)**: period=`7d` で `total_sessions` が小さいと `resume_rate` の分母が小さくなり、`1/2 = 50%` のような少数件アーティファクトが出やすい。ユーザーの判断は「period 内 ratio をそのまま採用」なので閾値倒し (`--%`) は導入しない。Phase 5 dogfooding で実値を確認し、もし実利用上 UX が悪すぎる場合は別 issue として `--%` 導入を検討する (= 本 issue scope 外)。
2. **footer 余白の見栄え劣化**: footer の `meta-item` が 2 個 (`最終更新` + `conn-status` 経由) になることで、`gap: 16px` の間隔が item 数減少で「ぽっかり余る」見た目になりうる。`flex-wrap: wrap` + `space-between` の layout は item 数に robust なので構造的には壊れないが、Phase 5 視覚スモークで確認する。問題があれば `.app-footer .meta { gap: 12px }` 程度の微調整を本 PR 内で吸収する。
3. **spec doc の prior drift (Issue #99/#106) を本 PR scope OUT で defer** (Proposal 1 反映 / disposition 明記):

   現状確認 (Phase 6 step 0 の grep で verified):
   - `docs/spec/dashboard-api.md:16,18` prose: `11 field / 8 field` (= pre-#99/#106 base)
   - `docs/spec/dashboard-api.md:28` heading: `(12 field)` (= Issue #99 で 1 度 bump 済 / #106 で未 bump)
   - `docs/spec/dashboard-api.md:29-34` bullet enumeration: 13 件 (= heading とのズレ 1)
   - `docs/spec/dashboard-api.md:36,38-41` heading + bullet: `(8 field)` / 8 entries (内部整合)
   - `docs/reference/dashboard-aggregation.md:206,207,208` table: `(11)/(8)/(3)=22` (内部整合だが `session_breakdown` / `model_distribution` 行自体が **未追加**)
   - `dashboard/server.py:120,121,1041,1042` docstring: `11 field / 8 field` (= pre-#99/#106 base、prose と整合だが heading とは不整合)

   **Issue #114 disposition** (CLAUDE.md `Plan writing — defer 時の disposition 明記` に従う):
   - **In scope**: `session_stats` 移動分の +1/-1 補正のみ。各 hit の現状リテラルに +1/-1 を機械的に適用 (`11→12`, `8→7`, heading `12→13`)。Phase 6 step 1-3 で網羅的に。
   - **Out of scope**: dashboard-api.md の prose-vs-heading-vs-bullet 不整合 (1 ずれ)、dashboard-aggregation.md table の `session_breakdown` / `model_distribution` 行追加。**現状の drift は本 PR では touch しない、別 issue (TBD) で defer**。
   - **理由**: ユーザー spec-Q Q3 は「`dashboard-api.md` + `dashboard-aggregation.md` 両方更新」だが、prior drift の根本解消には `~22 field` の総数 bump や bullet 追加が必要で本 issue scope を逸脱する (= CLAUDE.md `scope discipline` 違反)。Issue #114 後も heading と bullet 数の 1 ずれは残るが、これは Issue #114 が引き起こした drift ではなく Issue #99/#106 由来。本 PR body / Risks §3 でこの disposition を明記し、後続 issue 化を提案する。
   - **後続 issue case**: 「dashboard-api.md / dashboard-aggregation.md / server.py docstring の field 総数を `session_breakdown` / `model_distribution` 行追加 + 数値整合 (24 = 14+7+3) で正規化」を別 issue に切る。

4. **spec doc の数値整合 drift (本 PR で生む drift がないかの safety)**: 上記 disposition に従えば本 PR は `+1/-1` 補正だけ。Phase 6 step 0 の grep を**改訂前後 2 回**実行し、想定外箇所 (= 上の hit リストに無い場所) で `11`/`12`/`8` リテラルが新規追加 / 既存 hit の改訂漏れがないかを diff で verify する。
5. **既存 fixture (Node round-trip stub) の `session_stats` 値**: `tests/test_dashboard_period_toggle.py:1237` の固定 fixture は Node-side template render を試すスタブ data であり、period filter logic を経由しない。`session_stats` が period-applied になっても fixture 値は不変で OK (= 経路が違う test なので無修正)。
6. **EXPECTED_TEMPLATE_SHA256 sentinel bump**: shell.html の 1 行削除で sha が変わる。Issue #115 で確立した patch-release skill 流の手順 (AssertionError から actual hash 抽出 → 定数差し替え + bump history コメント) で吸収。sentinel は `test_html_template_contains_critical_dom_anchors` (5 page enum) / `test_html_template_tag_balance` の構造的安全網が残るので、sha bump 単独に頼る危うさは限定的。
7. **Live diff (25_live_diff.js) の `kpi-sess` 等 4 KPI 読み出し**: `buildLiveSnapshot` は `data.session_stats.total_sessions` 等を読むだけで、server から来る値の意味 (= 全期間 → period 内) が変わっても client logic は同一。SSE refresh 時に新値が tile に反映されることを Phase 5 視覚スモークで確認 (= 直前の period toggle 操作が SSE refresh とレースしないこと、これは Issue #85 で確立済の race-free pattern を踏襲)。
8. **`_filter_events_by_period` の二段 filter (`dashboard/server.py:126` 用語、timestamp 第一段 + subagent pair-straddling 第二段) が `session_start` / `compact_start` / `notification` 単発 event に対しては実質「timestamp 第一段のみ」**: 第二段は subagent_type key を持つ pair 専用なので、`event_type ∈ {session_start, compact_start, notification, instructions_loaded}` の event は `_bucket_events` の対象外で第一段で確定。これが「pair-straddling filter で session boundary が漏れない」根拠。`test_session_stats_no_pair_straddling_for_session_start` test で構造的に pin する。**注**: `dashboard/server.py:1046,1048` の call-site comment は `_filter_events_by_period` + `_filter_usage_events(period_cutoff=)` の combo を「三段 pair-straddling filter」と呼んでいるが、helper 自体は二段。本 plan / test は helper docstring 用語に揃えて「二段」を使う。

---

## ✅ Acceptance criteria mapping

Issue #114 本文の AC 5 項目それぞれをどの phase / step が満たすかの写像:

| AC | 内容 | 担当 phase / step |
| --- | --- | --- |
| AC1 | `dashboard/server.py:1104` で `aggregate_session_stats` に `period_events_raw` を渡すように変更 | Phase 1 GREEN step #3 |
| AC2 | `tests/test_dashboard_period_toggle.py` (もしくは新規) に「period=7d で `session_stats.total_sessions` が全期間より小さくなる」 drift guard test を追加 | Phase 1 RED step #1 (`TestSessionStatsPeriodApplied::test_session_stats_total_sessions_shrinks_with_7d`) — first failing test として pin、残り 7 test は Phase 1 Triangulation step #6 で additive 追加 |
| AC3 | `docs/spec/dashboard-api.md` の 8 field / 12 field 区分を 7 / 13 (追記分含む) に改訂、`session_stats` を period 適用 scope に移動 | Phase 6 step #2 (`docs/spec/dashboard-api.md` の prose / heading / bullet) + step #3 (`docs/reference/dashboard-aggregation.md` の 22-field 表も同期)。**注: Issue #99/#106 prior drift は scope OUT で defer** (Risks §3) |
| AC4 | Q1 / Q2 を確定し、結論を spec doc / 実装に反映 | **Q1 (footer 削除)**: Phase 4 GREEN step #6-9 (HTML / JS / CSS 削除 + sentinel bump) + Phase 6 step #5-6 (docs / skill). Phase 4 GREEN step #10 で footer meta-item 数 drift guard test も追加.<br>**Q2 (period 内 ratio)**: Phase 1 GREEN step #3 (`aggregate_session_stats(period_events_raw)` の自然な帰結として period 内 `resume_count / total_sessions` が出る) + Phase 1 Triangulation step #6 の `test_session_stats_resume_rate_uses_period_internal_ratio` で pin |
| AC5 | dogfooding (= 本リポジトリ自身の `usage.jsonl`) で `7d` / `30d` / `90d` / `all` 切替時に右 4 枚が変動することを目視確認 | Phase 5 step #3 (dogfooding) — screenshot を Phase 7 step #3 で PR body に添付 |

