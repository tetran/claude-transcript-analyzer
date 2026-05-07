# Plan: Issue #115 — Sessions タブに period toggle slot を追加 (HTML 1 行 + 閉じコメント 1 つ + allow-list 1 行)

## Goal

Sessions タブを開いている間も **既存の period toggle DOM** をそのタブの header 右端で操作できるようにする。`session_breakdown` は既に `period_events_raw` 経由で period 連動済み (`dashboard/server.py:1115-1117`) なので、UI 側に「タブを抜けずに period を切り替えられる」動線を追加するだけのスコープに閉じる。**方針 A** (Sessions header に `data-period-slot="sessions"` を 1 つ足し、`05_period.js` の許容リストに `"sessions"` を 1 つ足し、`30_pages.css` の Quality / Surface 用 `display: none` rule から `sessions` を外す) を採用。Quality / Surface はスコープ外 (touch しない、display:none も維持)。サーバー側 aggregation / API field は無改変。

## 📋 plan-reviewer 反映ログ

| Proposal | 内容 | 反映箇所 |
| --- | --- | --- |
| (初稿) | — | — |

### 二次レビュー反映 (2026-05-07)

| Proposal | 内容 | 反映箇所 |
| --- | --- | --- |
| P1 (actionable) | `EXPECTED_TEMPLATE_SHA256` sentinel が live (`tests/test_dashboard_template_split.py:28`) → snapshot bump を Risk から Phase 4 必須 step に昇格 | Phase 4 GREEN step #3 (新設) / Risk #6 文言更新 / TDD test plan 表に sentinel bump 行追加 |
| P2 (actionable) | Sessions 閉じタグに `<!-- /data-page="sessions" -->` 追加 (option b 採用) → Overview pattern と対称化、positional pin test を簡素に | タイトル / Critical files §変更対象 #1 / Phase 4 GREEN step #2 / TDD test plan 表 (positional pin の anchor 説明) |
| P3 (actionable) | タイトルの "CSS 1 行" を削除 (body と矛盾) | タイトル / Goal 段落の「CSS 1 行」表記削除 |
| P4 (advisory) | Phase 6 で spec/code 既存 drift (spec が sessions を display:none に列挙、code は無し) の correction も同時に行う旨を明示 | Phase 6 冒頭 + PR body §関連で言及 |
| P5 (advisory) | Phase 7 step 0 として `git ls-remote --heads origin v0.8.1` で v0.8.1 ブランチ存在を verify、無ければ main から作成する prerequisite step | Phase 7 step 0 (新設) |
| P6 (advisory) | Issue #114 が `05_period.js:77` を触る claim を soft 化 ("if #114 ends up touching" 表現) | Risk #3 / Phase 4 特記文言 |

### 三次レビュー反映 (2026-05-07)

| Proposal | 内容 | 反映箇所 |
| --- | --- | --- |
| P1 (actionable) | sentinel bump コマンドの import path が誤り (`_build_html_template` は `tests/test_dashboard_template_split.py` に存在しない、実体は `dashboard/server.py:1200`)。pytest AssertionError 経由で actual hash を抽出する patch-release skill 流の手順に置換 | Phase 4 step #3 / Risk #6 |
| P2 (actionable) | `patch-release` skill が release-cycle-start をスコープ外と SKILL.md 冒頭で宣言 → 引用先を CLAUDE.md `## Branching workflow` / `docs/reference/branching-workflow.md` に修正 | Phase 7 step 0 |
| P3 (advisory) | Sentinel bump 時の履歴コメント慣習 (`# - <new-sha>...: Issue #115 ...` 1 行) を明文化 | Phase 4 step #3 末尾 |
| P4 (advisory) | sha bump 単独に頼らず `test_html_template_contains_critical_dom_anchors` (5 page enum) / `test_html_template_tag_balance` の構造的安全網が残ることを Risk に明記 | Risk #6 末尾 |
| P5 (advisory) | Phase 5 起動コマンドを README 参照に soft 化 (`python3 -m dashboard --port 18585` の妥当性未検証) | Phase 5 step #1 / Rollout §ローカル起動 |
| P6 (advisory) | Phase 7 step 0 に `gh pr list --search "issue:114"` を 1 行 checklist 化 (forget 防止) | Phase 7 step 0 |
| Q1 (advisory) | Sessions section closing marker は本 issue 後 Overview + Sessions の 2 section だけが marker を持つ非対称構造になる。5 section 統一は別 issue scope と Out of scope に明記 | Out of scope |

### 四次レビュー反映 (2026-05-07)

| Proposal | 内容 | 反映箇所 |
| --- | --- | --- |
| P1 (advisory) | 履歴コメントを既存 5 行 prose ブロックの中に挿入すると将来 bump で読みにくくなる → labeled section (`# Bump history (1 行 / issue):`) + bullet 形式 (header + `# - <sha>: ...`) に変更、既存 prose の後 / 定数の前に配置 | Phase 4 step #3 履歴コメント例 |
| P2 (advisory) | README に「Dashboard 起動」という見出しは存在しない (実際は `### /claude-transcript-analyzer:usage-dashboard — ブラウザダッシュボード` line 110) → slash command 直接参照に置換 (verbatim heading 引用より actionable) | Phase 5 step #1 / Rollout §ローカル起動 |
| P3 (advisory) | Phase 7 step 0 の #114 conflict 判定を decision tree (open PR 無し / 開いてるが line 77 を触らない / 開いて line 77 を触る) に展開、衝突時の default は本 PR が後 rebase | Phase 7 step 0 |
| 自主判断 | reviewer 最終判断: actionable 0 / advisory 3 → exit loop へ進む。advisory はすべて反映済 | 全 advisory 反映後 handoff |

## Critical files

### 変更対象 (production)

- `dashboard/template/shell.html:534-630` — Sessions section の編集は **2 箇所**:
  1. **slot 追加** (header 内): Sessions section の `<header class="header">` の `</header>` 直前に `<div class="period-toggle-slot" data-period-slot="sessions"></div>` を 1 行追加。`.header` は既に `display: grid; grid-template-columns: 1fr auto; align-items: end` (`10_components.css:2-10`) なので、2 個目の grid child が右カラム auto に自動配置される (Q2 「Overview / Patterns と同じ flex 右寄せの 2-column header」を踏襲)。
  2. **closing comment marker 追加** (Sessions section 閉じタグ): line 630 の bare `</section>` を `</section><!-- /data-page="sessions" -->` に置換。これは Overview section (line 183) の既存 pattern と対称化するための変更で、positional pin test の anchor として機能する (P2 反映)。
- `dashboard/template/scripts/05_period.js:77` — `movePeriodToggleToActivePage()` 内の早期 return:
  - 現状: `if (activePage !== "overview" && activePage !== "patterns") return;`
  - 変更後: `if (activePage !== "overview" && activePage !== "patterns" && activePage !== "sessions") return;`
  - これだけで hashchange listener (`window.addEventListener("hashchange", movePeriodToggleToActivePage)`) が走った時点で toggle DOM が Sessions slot に `appendChild` で move する。
- `tests/test_dashboard_template_split.py:28` — `EXPECTED_TEMPLATE_SHA256` の **必須 bump** (P1 反映)。`shell.html` の bytes が変わるので live sentinel を再計算した sha256 で置換し、PR body にも「sentinel snapshot bump (Issue #115 slot 追加に伴う想定 diff)」と明記する。
- **CSS 編集なし**: `dashboard/template/styles/30_pages.css:79-82` の `body[data-active-page="quality"] #periodToggle, body[data-active-page="surface"] #periodToggle { display: none; }` rule は **無改変**。Sessions は元々この rule に含まれておらず、含めないこと自体が本 issue の目的。タイトルから「CSS 1 行」は削除済 (P3 反映)。spec/code 既存 drift (`docs/spec/dashboard-runtime.md:149-150` が sessions を display:none rule に列挙、code は無し) は Phase 6 で spec 側を更新して整合させる (P4 反映)。

### テスト対象

- `tests/test_dashboard_period_toggle.py:722-1080` — 既存 `TestPeriodToggleTemplate` class。Sessions 関連の structural test を**この既存 class に追加** (新規ファイルは作らない、Issue #109 で確立済の「Sessions 関連 UI test は既存 ui ファイルに追記する」慣習に準拠)。Node round-trip 用の minimal stub pattern (lines 833-930 / 977-1035) を再利用。

### 参照対象 (Read のみ)

- `dashboard/server.py:1115-1117` — `session_breakdown` が `period_events_raw` で集計されている既存契約 (= UI 側 toggle 追加で server に手を入れる必要が無いことの裏付け)。
- `dashboard/template/styles/10_components.css:1-10` — `.header` の `grid-template-columns: 1fr auto` (slot を `<div>` 1 個追加するだけで右寄せが効く根拠)。
- `dashboard/template/shell.html:43-55` (Overview slot) / `:192-194` (Patterns 空 slot) — 既存 slot pattern を踏襲する元ネタ。
- `docs/spec/dashboard-runtime.md:138-168` — Period toggle spec。lines 149-154 に「Sessions ページから period を切り替える UI は持たない」と明記されているため、本 issue では**ここを更新する** (Phase 6)。

## Phases

本 issue は server-side aggregation を一切いじらず、template HTML + JS + CSS の最小編集 + spec 文言更新に閉じるため、6-phase plan-driven TDD rhythm の Phase 1-3 (aggregator unit / secondary / build_dashboard_data 統合) は省略可。残り **Phase 4 (template DOM test RED → GREEN) / Phase 5 (JS allow-list 拡張 + 視覚スモーク) / Phase 6 (docs) / Phase 7 (PR)** の 4 phase 構成。

### Phase 4 — Template DOM RED → GREEN (slot 配置 + allow-list 構造 + sentinel bump)

RED 先行 (TDD 必須):

1. `tests/test_dashboard_period_toggle.py` の `TestPeriodToggleTemplate` に以下を追加:
   - `test_sessions_header_has_empty_period_slot` — 既存 `test_patterns_header_has_empty_period_slot` (line 781) の Sessions 版。`'data-period-slot="sessions"' in template` を assert。さらに **Sessions section 内** に居ること (= `<section data-page="sessions"` 開始位置 < slot 位置 < `</section><!-- /data-page="sessions" -->` の出現位置) も pin。Overview の既存 anchor pattern (`</section><!-- /data-page="overview" -->`, line 183) と対称になるよう、本 plan の GREEN diff で Sessions 側にも closing comment marker を導入することを前提にする (P2 反映)。
   - `test_sessions_slot_inside_sessions_header` — Sessions section 内の `<header class="header">` の開始 〜 直近の `</header>` の間に `data-period-slot="sessions"` が居る (= header 外の panel-body に紛れ込んでいない) ことを substring 順序で pin。
   - `test_period_toggle_allows_sessions_active_page` — `_concat_main_js()` 結果に `'activePage !== "sessions"'` (or 等価表現) が含まれる、つまり「allow-list に sessions が居る」ことを substring で pin。**かつ** 既存の `test_period_toggle_moved_via_hashchange_listener` (line 788) と同じ Node round-trip pattern を踏襲して、`document.body.dataset.activePage = "sessions"` をセットしてから `window.__period.movePeriodToggleToActivePage()` を呼び、Sessions slot の `appendChild` が呼ばれる (= move された) ことを behavioral に assert する (substring pin + behavioral test の二重 belt)。
2. RED 確認 (3 test 追加すべて failing) → GREEN 実装 (production diff、3 箇所):
   - **`shell.html` slot 追加**: Sessions section `<header class="header">` 内の `</header>` 直前に 1 行追加: `<div class="period-toggle-slot" data-period-slot="sessions"></div>`
   - **`shell.html` closing comment marker 追加** (P2 反映): line 630 付近の Sessions section 閉じタグを `</section>` → `</section><!-- /data-page="sessions" -->` に置換。Overview pattern と対称化、positional pin test の anchor として機能。
   - **`05_period.js:77`** の早期 return 条件に `&& activePage !== "sessions"` を追加。
3. **Sentinel snapshot bump** (P1 反映、必須 step):
   - **手順 (patch-release skill 流)**: production diff を当てた上で
     ```bash
     python3 -m pytest tests/test_dashboard_template_split.py::test_html_template_byte_equivalent_to_pre_split_snapshot -v
     ```
     を 1 度走らせ、AssertionError の `actual:` 行 (もしくは equivalently 出力される hash) から新 sha256 をコピー。`_build_html_template` は `tests/` 側に export されておらず、実体は `dashboard/server.py:1200` 配下 + `tests/_dashboard_template_loader.py:35` の `load_assembled_template()` 経由でしか組み立てられないため、one-liner ではなく test 失敗経由が現実的 (三次レビュー P1 反映)。
   - `tests/test_dashboard_template_split.py:28` の `EXPECTED_TEMPLATE_SHA256 = "dd215897..."` を新値に置換。
   - **履歴コメント追記** (三次レビュー P3 / 四次レビュー P1 反映): 同ファイルの既存 5 行 prose ブロック (line 23-27) の **後**、定数定義の **前** に labeled section + bullet 形式で履歴を蓄積。既存 prose を改変せず append point を作る。例:
     ```python
     # 意図的な template 変更時は新 hash に更新する (docstring 参照)。
     #
     # Bump history (1 行 / issue):
     # - <new-sha-prefix-12>: Issue #115 sessions period toggle slot + closing comment marker
     EXPECTED_TEMPLATE_SHA256 = "<new-sha>..."
     ```
     既存ファイルに履歴コメント枠が無いため、本 issue で labeled section 慣習を導入する形になる。将来 bump 時は同 section に 1 行追記するだけで済む。
   - PR body §「sentinel snapshot bump」で「Issue #115 slot 追加 + closing comment marker 追加に伴う想定 diff、reviewer は hash 自体ではなく shell.html の diff を見て判定」と注記。
4. Phase 4 完了条件: 既存 test 全 GREEN + 新規 3 test GREEN + `tests/test_dashboard_template_split.py` 内の `test_html_template_byte_equivalent_to_pre_split_snapshot` も新 hash で GREEN。

特記:

- Issue #114 (Overview KPI 4 枚 period 連動) も in-flight。同 PR が **もし** `05_period.js:77` の allow-list を触っていれば merge conflict が起き得るが、これは未確認の hypothesis (P6 反映) — 実際の #114 PR diff を Phase 7 step 0 と一緒に確認し、conflict を見つけた側が rebase する一般原則で対応する。本 plan は branch を **v0.8.1** から切る (後述 §Branch / PR target)。

### Phase 5 — 視覚スモーク + 静的 / 動的 path 確認

JS allow-list 拡張は Phase 4 GREEN 内で済んでいるので、Phase 5 は**動作確認に閉じる**:

1. ローカル dashboard server を起動: `/claude-transcript-analyzer:usage-dashboard` を実行 → stderr / 起動 message に表示される `http://localhost:<port>` URL にブラウザでアクセス (四次レビュー P2 反映、README §「ブラウザダッシュボード」 line 110 周辺の slash command を直接参照)。
2. **目視 checklist** (acceptance criteria 直訳):
   - [ ] Overview タブで `7d` を click → period toggle が `aria-pressed="true"` 状態で `7d` に乗る。
   - [ ] Sessions タブへ遷移 → toggle DOM が **Sessions header 右端 slot に move** している (DevTools Inspector で `data-period-slot="sessions"` の中に `#periodToggle` が居ることを確認)。
   - [ ] Sessions タブで `30d` を click → Network タブで `GET /api/data?period=30d` が fire し、KPI 4 枚 / `sessionsTable` / `sessionsSub` の値が更新される (= `__liveDiff.scheduleLoadAndRender()` 経由で再 render)。
   - [ ] Quality / Surface タブへ遷移 → toggle が **見えない** (`30_pages.css:79-82` の rule で `display:none`、現状維持)。
   - [ ] `aria-pressed` の active button が他タブ間遷移を跨いでも保たれている (Q1=A: `__periodCurrent` 引き継ぎ)。
3. **Static export スモーク**: `python3 reports/export_html.py --output /tmp/r.html` → ブラウザで開く → Sessions タブで toggle が `hidden` 属性で非表示 (既存 `wirePeriodToggle()` 冒頭の `window.__DATA__` 経路、変更なし) を確認。`tests/test_dashboard_period_toggle.py:977 test_static_export_hides_toggle` の regression が無いことが test suite 側で担保されているはず。
4. **chrome-devtools MCP** (任意、目視補強): live server 経由で Sessions タブの screenshot を取り、Overview / Patterns と同じ右寄せ位置で違和感が無いことを確認。

### Phase 6 — Docs 更新 (spec の食い違い解消 + 既存 drift 訂正) + memory file 不要確認

**この Phase は 2 つの修正を同時に行う** (P4 反映):

1. **Issue #115 narrative 更新**: Sessions タブから period 切替できるようになったことを spec に反映
2. **既存 spec/code drift の訂正**: `docs/spec/dashboard-runtime.md:149-150` が `body[data-active-page="quality"|"surface"|"sessions"] #periodToggle { display: none }` と sessions を含む rule を記述しているが、code 実体 `30_pages.css:79-82` には sessions 行が無い (= spec が以前から間違っていた)。本 issue で Sessions が toggle 表示タブに昇格するため、spec を code 側に合わせる方向で同時に直す。

`docs/spec/dashboard-runtime.md:138-168` の Period toggle 節を更新:

- line 149-150: `display: none` rule の対象 page list から `sessions` を削除し、`quality` / `surface` の 2 page のみに絞る。これは (a) 本 issue で Sessions に slot を追加した narrative 更新、かつ (b) 既存 drift (CSS 実体は元々 sessions を含んでいなかった) の訂正、を同時に行う両用途。
- line 151-154 周辺: 「Sessions ページの `session_breakdown` 自体は Overview/Patterns で設定した period が継続適用される ... が、Sessions ページから period を切り替える UI は持たない」の最後の but 節を削除し、「Sessions ページからも period を切り替えられる (Issue #115)」に書き換え。
- PR body §関連 で「spec/code drift correction も同時に行っている」旨を 1 行明記 — reviewer が spec diff の意図 (narrative update + drift fix の 2 用途) を読み取りやすくするため。
- `docs/spec/dashboard-api.md` — `period` query / `session_breakdown` の period 連動契約は変更なし (line 11 / line 33 / line 881 はそのまま)。**touch しない**。
- `docs/reference/dashboard-client.md` — period toggle の言及は薄い (grep hit 無し) → 更新不要。
- **Memory file 更新は不要**: 本変更は public spec として `dashboard-runtime.md` に集約されており、CLAUDE.md / AGENTS.md レベルの project rule は影響しない。
- **help-pop の追加・変更なし** = 4-axis verification (spec match / data smoke / live smoke / help-text vs impl) は本 issue では**不要**。Sessions help-pop (`hp-sessions` / `hp-tokens` / 他) の文言は無改変。
- **新 API field 追加なし** = 「API field 名は安定 / UI 表示は flex」の境界 conventions に該当する変更なし。

### Phase 7 — PR

0. **v0.8.1 release branch prerequisite verify** (P5 反映、三次レビュー P2 / P6 反映):
   - `git fetch origin --prune && git ls-remote --heads origin v0.8.1` を確認。
   - 空 (= まだ origin に存在しない) の場合: `git checkout main && git pull origin main && git checkout -b v0.8.1 origin/main && git push -u origin v0.8.1` で main 最新から v0.8.1 を新規作成。手順は **CLAUDE.md `## Branching workflow` および `docs/reference/branching-workflow.md`** の release branch 切り出し節に準拠 (`patch-release` skill は release-cycle-start をスコープ外と宣言しているため引用しない、三次レビュー P2 反映)。
   - 既に存在する場合: そのまま base として使用。
   - **Issue #114 PR 状態の確認** (三次レビュー P6 / 四次レビュー P3 反映、decision tree 形式):
     - `gh pr list --search "issue:114"` を 1 度叩いて状態確認。
     - **(a)** 該当 PR が **存在しない** → そのまま進行 (no overlap)。
     - **(b)** 該当 PR が **存在するが `dashboard/template/scripts/05_period.js:77` の allow-list を触っていない** → そのまま進行 (independent diffs)。
     - **(c)** 該当 PR が **存在し line 77 allow-list を触っている** → user に gh PR URL + 該当 line diff を escalate。**default**: 本 PR (#115) が後 rebase 側 (= #114 が先 merge、#115 は #114 の allow-list 形に合わせて再編集 + sentinel sha 再 bump) — #115 の allow-list 拡張は #114 の改変上に additive で乗せられるため。
   - feature branch (`feature/115-sessions-period-toggle-slot`) は v0.8.1 確定後に切る。
1. Branch: `feature/115-sessions-period-toggle-slot` を **v0.8.1** から切る (Step 0 で確定)。
2. PR title (≤70 chars): `feat(dashboard): add period toggle slot to Sessions tab (#115)`
3. PR body: Summary / Test plan checklist / before-after screenshot (Sessions タブで toggle が右端に出ている図) / **「sentinel snapshot bump (Issue #115 slot 追加に伴う想定 diff)」section** / **「spec/code drift correction も同時に実施」section** / Issue #103 で漏れていた slot を再付与した位置づけ説明 / `gh pr create --base v0.8.1`。
4. CI 確認: macOS / Linux / Windows 3 OS matrix で `pytest tests/test_dashboard_period_toggle.py tests/test_dashboard_template_split.py` 通過。

## TDD test plan (failing-test-first)

**追加先**: 新規ファイルは作らず、`tests/test_dashboard_period_toggle.py` の既存 `TestPeriodToggleTemplate` class に 3 cases 追加 (既存 sessions 関連は `tests/test_dashboard_sessions_ui.py` 側にあるが、period toggle structural pin は `_period_toggle.py` 側に集中している既存慣習を踏襲)。

| Test name | 何を assert するか | パターン |
| --- | --- | --- |
| `test_sessions_header_has_empty_period_slot` | `_build_html_template()` 結果に `data-period-slot="sessions"` が存在し、かつ Sessions section (`<section data-page="sessions"`) と次の `</section>` の間に居る | substring + 位置順序 (既存 line 781 `test_patterns_header_has_empty_period_slot` の Sessions 版) |
| `test_sessions_slot_inside_sessions_header` | Sessions section 内で `<header class="header">` の開始 〜 直近の `</header>` の間に slot が居る (panel-body 紛れ込み防止) | substring index 範囲 |
| `test_period_toggle_moves_to_sessions_slot_via_hashchange` | Node round-trip stub で `document.body.dataset.activePage = "sessions"` をセット → `window.__period.movePeriodToggleToActivePage()` を invoke → Sessions slot の `appendChild` が toggle を引き取る (= allow-list に sessions が含まれているからこそ早期 return しなかった証跡) | 既存 `test_period_toggle_moved_via_hashchange_listener` (line 788) の Node stub pattern を流用、stub の `querySelector('[data-period-slot="sessions"]')` が `appendChild` 受信スパイを返すように仕込む |
| `test_html_template_byte_equivalent_to_pre_split_snapshot` (既存) | `tests/test_dashboard_template_split.py:45` 既存 sentinel test。`EXPECTED_TEMPLATE_SHA256` (line 28) を Phase 4 GREEN 後に再計算した値で update する必要あり (P1 反映) | sentinel update は production 側の HTML diff が確定した時点で 1 度だけ実施。test code は無改変、定数だけ置換。|

**regression pin (touch せず)**:

- 既存 `test_period_toggle_hidden_on_quality_and_surface_pages_via_css` (line 796) — Quality / Surface の `display:none` rule が CSS に残っていることを保証。本 issue は CSS rule に Sessions を**追加しない**ため、この test の assertion 文字列に `sessions` を入れない (= 現状維持) ことで「Quality / Surface 側に regression が無い」を構造的に pin。
- 既存 `test_static_export_hides_toggle` (line 977) — `window.__DATA__` 経路の `hidden` 属性ロジックは無変更。
- 既存 `test_load_and_render_uses_period_query` (line 1088) — fetch URL に `?period=` が含まれる契約は無変更。

`docs/spec/dashboard-runtime.md` の文言整合 RED は **入れない**: spec 文書 grep test (`test_issue_85_daily_trend_sentinel` 系) は本 issue scope 外。docs 更新は Phase 6 の手作業 review に閉じる。

## Risks / tradeoffs

1. **Header design 崩れ (Q2 起因)**: Sessions header の lede が長め (2 行 = "最新 20 件の有効セッションのトークン消費・推計コスト・モデル構成を一覧。実測トークン × モデル別公開価格表の掛け算による参考値。" `shell.html:539-541`) で、右カラム auto の toggle (高さ ~28px) と vertical alignment が `align-items: end` (= `10_components.css:6`) で揃う。Overview header の lede (1 行) と比べて行数差があるが、grid `align-items: end` で底辺が揃うので違和感は少ないと予測。Phase 5 の chrome-devtools MCP screenshot で目視確認。
2. **Cross-tab period state consistency**: Q1=A 採用なので Sessions タブ初訪問時も `__periodCurrent` (closure-private、initial = "all") を引き継ぐ。Overview で 7d を選んでから Sessions に来ると、Sessions も 7d で render される (= server 側 `session_breakdown` が `period_events_raw` で既に period 連動しているので fetch 結果も整合)。**Sessions タブ独自 state を持たない**ことの確認は acceptance criteria の「`aria-pressed="true"` が `7d` に乗っている」目視で済む。
3. **Issue #114 (Overview KPI 4 枚 period 連動) との semantic 整合**: #114 は KPI render 経路を period 連動化する話で、本 #115 は UI 動線追加。両者は独立。**もし** #114 が `05_period.js:77` の allow-list 周辺も触るなら merge conflict が起き得る (P6 反映、未確認 hypothesis) — Phase 7 step 0 で v0.8.1 確認するついでに `gh pr list --search "issue:114"` で #114 PR の有無 / diff を見て確定する。**回避策一般則**: PR base を v0.8.1 に揃え、conflict が出れば後 merge 側が rebase する。
4. **Live diff loop (`__liveDiff.scheduleLoadAndRender`) 干渉**: Sessions タブで toggle click → `setCurrentPeriod(p)` → `resetLiveSnapshot()` → `scheduleLoadAndRender()` が走る既存経路 (`05_period.js:50-61`)。Sessions タブ active 時に走っても、既存 `25_live_diff.js` の `__livePrev = null` reset で false-burst が抑止される (`tests/test_dashboard_period_toggle.py:932 test_period_resets_live_snapshot_before_load_and_render` の規約)。Sessions 専用に何かを bypass する必要は**ない**。
5. **Cross-platform / browser regression リスク**: 本変更は HTML 1 行 + JS 条件式 1 個 + CSS 0 行 = 動的 layout / フォーカス遷移の変更なし。Windows / macOS / Linux + Chromium / Firefox / Safari の差は出にくい。`tests/test_dashboard_period_toggle.py` は Node 必須の test がいくつかあるが、CI ではスキップ条件付き (`shutil.which("node") is None` で skip)。
6. **Sessions section の HTML 行数増加 → `EXPECTED_TEMPLATE_SHA256` sentinel が確実に red になる** (P1 反映、verified live): `tests/test_dashboard_template_split.py:45` の `test_html_template_byte_equivalent_to_pre_split_snapshot` は live で、line 28 に `EXPECTED_TEMPLATE_SHA256 = "dd215897..."` が pin されている。本 issue で `shell.html` の bytes は確実に変わる (slot 1 行 + 閉じ comment marker 1 つ追加) ため snapshot bump は **必須 step** (Phase 4 step #3 として明記済)。reviewer が hash diff を「想定外の regression」と読み違えないよう PR body に「sentinel snapshot bump」section を 1 つ立てる。**dual exposure 補足** (三次レビュー P4 反映): sha bump で byte-equivalence test だけ通せば「何でも通せる」訳ではなく、`tests/test_dashboard_template_split.py:63` 周辺の `test_html_template_contains_critical_dom_anchors` (5 page `data-page` enum loop) / `test_html_template_tag_balance` 等の構造的安全網が並走しているため、sha 単独に頼らずとも slot DOM 不在・タグ balance 崩れは別 test で検知される。

## Out of scope

- Quality / Surface への slot 追加 — 仕様上 period 不変 scope なので toggle が UI 上あっても押す意味が無い (defer to 別 issue if needed)。
- Period toggle の semantic 拡張 — Issue #114 (Overview KPI 4 枚 period 連動) の話。本 #115 は UI 動線追加に閉じる。
- 新 field / API 変更 — `session_breakdown` schema は無変更。`period_applied` echo も既存契約のまま。
- Period state の URL hash / localStorage 永続化 — `dashboard-runtime.md:161-162` で「将来 issue」と明記済、本 issue でも踏襲。
- Sessions タブ独自の period state — Q1=A 採用で却下。
- Period toggle button の文言・並び替え (`7d / 30d / 90d / 全期間`) — 無改変。
- **Section closing comment marker の 5 section 統一** (三次レビュー Q1 反映): 本 issue では Sessions section にのみ `<!-- /data-page="sessions" -->` を追加する (positional pin の anchor 用)。Patterns / Quality / Surface section も同 marker を持つよう揃える方針は別 issue scope。本 issue 後は Overview + Sessions の 2 section だけが marker を持つ非対称構造になるが、test 側は section 名を直接 anchor にしているため動作上問題なし。

## Rollout / verification

### Test suite

```bash
python3 -m pytest tests/test_dashboard_period_toggle.py -v
```

期待: 既存 test 全 PASS + 新規 3 test PASS。Node 不在環境では node-依存 test が skip するのは既存挙動どおり。

### ローカル dashboard 起動 + 目視 checklist (Phase 5 と重複、最終 verify として再列挙)

- [ ] `/claude-transcript-analyzer:usage-dashboard` slash command でローカル dashboard server を起動 → 表示された `http://localhost:<port>` にアクセス (四次レビュー P2 反映)
- [ ] Overview → `7d` click → toggle `aria-pressed="true"` が `7d` に乗る
- [ ] `#/sessions` へ遷移 → toggle DOM が Sessions header 右端 slot (`data-period-slot="sessions"`) に move されている (DevTools で確認)
- [ ] Sessions タブで `30d` click → Network tab に `GET /api/data?period=30d` → KPI 4 枚 / table / sub 行が新値で再 render
- [ ] Quality / Surface → toggle 非表示 (regression check)
- [ ] reload → toggle 初期値 `全期間 (all)` に戻る (永続化なし spec 維持)

### Static export verify

```bash
python3 reports/export_html.py --output /tmp/r.html
```

→ ブラウザで開いて Sessions タブで toggle が **hidden** (既存 `window.__DATA__` 経路維持) を目視。

## Branch / PR target

- **Branch**: `feature/115-sessions-period-toggle-slot`
- **Base**: **v0.8.1** (main から新規作成予定の release branch)
- **PR target**: v0.8.1
- **Merge order with #114**: 本 PR と #114 が同じ `05_period.js:77` 行を触るため、先に merge された側に合わせて rebase。両 issue 並行進行時は v0.8.1 base で順次取り込み、conflict は早期 lookup で解消。
