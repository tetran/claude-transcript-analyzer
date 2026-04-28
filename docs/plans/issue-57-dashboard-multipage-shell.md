# Issue #57 Plan — ダッシュボード複数ページ化 shell (router / nav / layout)

## 🎯 Goal

v0.7.0 の Tier A+B 計 9 insights をマウントするための **空の page shell** を先に
入れる。本 issue では「ページ枠 + ナビ + 空 placeholder」までで停止し、各ページの
中身 widget は後続 issue (#58〜#62) で本実装する。

最初に merge する基盤 PR なので、後続 5 issue が並列で「ページにマウントするだけ」の
状態を提供することが価値の中心。コードの構造判断（特に router の置き場所と
ページ section の DOM 形）は後続 5 PR を直撃するため慎重に決める。

## 📐 ページ構成

| Path | data-page | 名前 | 内包 widget (将来) |
|------|-----------|------|--------------------|
| `#/` | `overview` | Overview | KPI / skill / subagent ranking / project / daily / health alerts (現状互換) |
| `#/patterns` | `patterns` | Patterns | A1 時間帯ヒートマップ / B1 共起 / B2 project×skill |
| `#/quality` | `quality` | Quality | A2 permission/skill / A3 compact / A5 percentile / B3 weekly |
| `#/surface` | `surface` | Surface | A4 expansion比率 / B4 instructions_loaded |

本 issue では Overview 以外は「Coming soon (#58 等で実装予定)」placeholder。

## 🏛 構造設計

### DOM レイアウト (template.html)

```html
<body>
  <div class="app">
    <!-- 共通: ページ切替 nav (4 タブ) -->
    <nav class="page-nav" role="navigation" aria-label="ダッシュボードページ">
      <a href="#/" data-page-link="overview" class="active" tabindex="0">Overview</a>
      <a href="#/patterns" data-page-link="patterns" tabindex="0">Patterns</a>
      <a href="#/quality" data-page-link="quality" tabindex="0">Quality</a>
      <a href="#/surface" data-page-link="surface" tabindex="0">Surface</a>
    </nav>

    <!-- ページごとの section -->
    <section data-page="overview" class="page" aria-labelledby="page-overview-title">
      <header class="header">
        <!-- h1 + lede ... ここは Overview 専用 -->
      </header>
      <div class="kpi-row" id="kpiRow"></div>
      <!-- 既存 panel 群 (skill / subagent / daily / projects) -->
    </section>

    <section data-page="patterns" class="page page-placeholder" aria-labelledby="page-patterns-title" hidden>
      <h2 id="page-patterns-title">Patterns — Coming soon</h2>
      <p class="placeholder-body">本ページは Issue #58 / #59 で実装予定です。</p>
    </section>

    <section data-page="quality" class="page page-placeholder" hidden>...</section>
    <section data-page="surface" class="page page-placeholder" hidden>...</section>

    <!-- 共通: 接続バッジ + 最終更新 + セッション情報 + クレジット -->
    <footer class="app-footer">
      <div class="meta">
        <span class="conn-status" id="connStatus" data-state="reconnect" role="status" aria-live="polite">○ 接続準備中</span>
        <span class="meta-item"><span class="k">最終更新</span><span class="v" id="lastRx">—</span></span>
        <span class="meta-item"><span class="k">セッション</span><span class="v" id="sessVal">—</span></span>
      </div>
      <div class="credits">
        <span class="accent">claude-transcript-analyzer</span> · v0.6
        <span class="sep">·</span> stdlib only · no third-party js
      </div>
    </footer>
  </div>
  ...
</body>
```

**設計判断**:

- `<a href="#/x">` (ハッシュリンク) を採用 → ブラウザ戻る/進む / 直 URL ブックマーク /
  右クリック「タブで開く」が **追加コードゼロで動く**。`<button onClick>` 経路は
  この機能の自前実装が必要になるため不採用。
- `hidden` attribute (HTML5) で非表示 → CSS なしでも `display:none` 相当が効く。
  `<section>` を残す方式 (DOM tree から削除しない) を選ぶ理由:
    - SSE refresh 時に Overview 以外のページがロード/アンロードされるとパフォーマンス
      ブレが起きる
    - 後続 #58〜 で各ページが独自に DOM を保持するときも `hidden` 切替で済む
    - `loadAndRender()` は今のまま「Overview の DOM 全部書き換え」で動かしておく
- header (h1 + lede) は **Overview section の中** に移動 (= Overview 専用)。
  acceptance criteria の「header (現状の KPI badge 帯) は Overview ページ専用に移動」
  に直接対応。
- conn-status / 最終更新 / セッション情報は **footer に移動 → 全ページ共通**。
  acceptance criteria の「全ページ共通の頂部 nav と footer (接続バッジ等) は維持」
  に対応。SSE 接続バッジは Overview 以外のページからも見える位置にあるべき。

### Router (JavaScript / template.html 内)

```javascript
const PAGES = ['overview', 'patterns', 'quality', 'surface'];
const HASH_TO_PAGE = {
  '': 'overview', '#': 'overview', '#/': 'overview',
  '#/patterns': 'patterns', '#/quality': 'quality', '#/surface': 'surface',
};

function applyRoute(rawHash) {
  const page = HASH_TO_PAGE[rawHash] || 'overview';
  document.querySelectorAll('.page').forEach(el => {
    el.hidden = (el.dataset.page !== page);
  });
  document.querySelectorAll('[data-page-link]').forEach(a => {
    a.classList.toggle('active', a.dataset.pageLink === page);
    a.setAttribute('aria-current', a.dataset.pageLink === page ? 'page' : 'false');
  });
  // 後続 PR が `body[data-active-page]` を読んで page-scoped early-out できる
  document.body.dataset.activePage = page;
}

window.addEventListener('hashchange', () => applyRoute(location.hash));
applyRoute(location.hash);  // 初期描画時
```

**設計判断**:

- 既存 `loadAndRender()` の中身は **一切変更しない**。ルーターは独立した IIFE として
  `<script>` の上部 (loadAndRender 定義前) で実行。Overview の中身は今と同じ ID
  群 (`kpiRow`, `skillBody`, ...) を使い続ける。
- `applyRoute` は **副作用が `hidden` / `aria-current` / `body[data-active-page]`
  のみ** → SSE refresh 経路と独立。Overview 以外のページ表示中も `loadAndRender()`
  は走り続け、戻ってきたときに最新データが見える。これが本 issue の最大の互換性ポイント。
- 不正な hash (`#/foo`, `#bar`, `#` 単体, percent-encoded `#/%E3%83%91...` 等) は
  overview にフォールバック → 「直 URL ブックマーク可能」の acceptance criteria の
  安全側。`HASH_TO_PAGE` で空文字 / `#` / `#/` を 3 つとも明示的に overview に map
  し、それ以外は `||` 演算子で fallback。
- `<a href="#/x">` をクリックすると `hashchange` イベントが発火 → 自動で `applyRoute`
  が走るので、`<a>` の `click` を hijack する必要はない。Enter 押下時も同様に動作
  (ブラウザネイティブ挙動)。
- `body[data-active-page="<page>"]` を expose するのは **後続 PR (#58〜#62) が
  page-scoped early-out 判定**に使えるようにするため。例: `if
  (document.body.dataset.activePage !== 'patterns') return;` で patterns 専用
  renderer を no-op にできる。

### CSS の追加 (最小限)

```css
.page-nav {
  display: flex;
  gap: 4px;
  padding: 0 0 18px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 22px;
}
.page-nav a {
  padding: 8px 14px;
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-soft);
  text-decoration: none;
  border-radius: var(--r-md) var(--r-md) 0 0;
  transition: color 120ms, background 120ms;
}
.page-nav a:hover { color: var(--ink); background: var(--bg-panel); }
.page-nav a.active {
  color: var(--mint);
  background: var(--bg-panel);
  border-bottom: 2px solid var(--mint);
  margin-bottom: -1px;  /* nav 下線と重ねる */
}
.page-nav a:focus-visible { outline: 2px solid var(--peri); outline-offset: 2px; }

.page[hidden] { display: none; }

.page-placeholder {
  padding: 60px 20px;
  text-align: center;
  color: var(--ink-soft);
}
.page-placeholder h2 {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 8px;
  color: var(--ink);
}
.page-placeholder .placeholder-body {
  font-size: 13px;
  color: var(--ink-faint);
  margin: 0;
}

.app-footer {
  margin-top: 32px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 11.5px;
  color: var(--ink-faint);
}
.app-footer .meta { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
.app-footer .meta-item .k { color: var(--ink-faint); margin-right: 4px; }
.app-footer .meta-item .v { color: var(--ink-soft); font-family: var(--ff-mono); }
.app-footer .credits { color: var(--ink-faint); }
.app-footer .credits .accent { color: var(--mint); }
```

既存 `.header` / `.kpi-row` / `.panel` / `<footer>` の旧スタイルは Overview の
中身がそのまま使うので残す。`<footer>` (旧クレジット行) ルールは `.app-footer`
に置き換え。

## ✅ 機能要件への対応マッピング

| Acceptance Criteria | 実装ポイント |
|---|---|
| ハッシュベース router (`#/`, `#/patterns`, `#/quality`, `#/surface`) | `applyRoute(location.hash)` + `HASH_TO_PAGE` テーブル |
| full page reload 無し SPA 風遷移 | `<a href="#/x">` + `hashchange` listener |
| 直 URL でブックマーク可能 | 初期 `applyRoute(location.hash)` 実行 |
| 横並び nav 4 タブ + active state | `.page-nav` + `applyRoute` の `classList.toggle('active', ...)` |
| キーボードアクセス (Enter で遷移) | `<a href>` のネイティブ挙動 + `:focus-visible` outline |
| Overview 以外は「Coming soon」 placeholder | `.page-placeholder` セクション 3 つ |
| header は Overview 専用 | `<section data-page="overview">` の中に移動 |
| 全ページ共通の頂部 nav / footer | `.page-nav` / `.app-footer` を `.app` 直下に配置 |
| 既存 SSE / loadAndRender 機能を壊さない | `loadAndRender` の DOM ID は不変、router は独立 IIFE |
| `/api/data` レスポンス互換 (additive のみ) | サーバー側 `build_dashboard_data` には触らない |
| `window.__DATA__` 注入経路互換 | `render_static_html` の `</head>` 注入位置は不変 |

## 🧪 テスト戦略 (TDD)

外部ライブラリ不使用 / Python stdlib のみという制約のため、JS 実行を伴う E2E は
できない。既存の `test_dashboard.py` 群と同じ **template 文字列の構造検査** で
Acceptance Criteria を検証する。

### 新規追加テスト (`tests/test_dashboard_router.py`)

```python
class TestRouterShellStructure:
    def test_template_has_four_page_nav_links(self):
        # <a href="#/">..</a> 〜 <a href="#/surface">..</a>
        for path in ['#/', '#/patterns', '#/quality', '#/surface']:
            assert f'href="{path}"' in template

    def test_template_has_four_page_sections(self):
        for page in ['overview', 'patterns', 'quality', 'surface']:
            assert f'data-page="{page}"' in template

    def test_overview_page_contains_existing_widgets(self):
        # Overview section の中に kpi-row / skillBody / subBody / spark / stack が含まれる
        overview_section = _extract_section(template, 'overview')
        assert 'id="kpiRow"' in overview_section
        assert 'id="skillBody"' in overview_section
        assert 'id="spark"' in overview_section
        assert 'id="stack"' in overview_section

    def test_non_overview_pages_are_placeholders(self):
        for page in ['patterns', 'quality', 'surface']:
            section = _extract_section(template, page)
            assert 'page-placeholder' in section
            assert 'Coming soon' in section or '実装予定' in section
            # 既存ウィジェットの ID は含まれない
            assert 'id="kpiRow"' not in section

    def test_router_javascript_present(self):
        assert "addEventListener('hashchange'" in template
        assert "'#/patterns'" in template or '"#/patterns"' in template
        assert 'data-page-link' in template
        assert 'aria-current' in template

    def test_router_initial_apply_route_call(self):
        # listener 内 + 初期呼び出しで applyRoute(location.hash) が 2 回以上現れる
        assert template.count('applyRoute(location.hash)') >= 2

    def test_router_hash_table_covers_empty_and_slash(self):
        """空 hash / '#' 単体 / '#/' の 3 経路すべてが overview に map される"""
        # HASH_TO_PAGE テーブルに 3 つの空-ish key が並ぶことを文字列で確認
        # (順序依存しないよう個別に in 検査)
        assert "'': 'overview'" in template
        assert "'#': 'overview'" in template
        assert "'#/': 'overview'" in template

    def test_router_fallback_to_overview(self):
        """未知 hash は applyRoute 内で 'overview' に倒れる"""
        assert "|| 'overview'" in template

    def test_body_data_active_page_exposed_for_followups(self):
        """後続 PR が page-scoped early-out できる contract"""
        assert 'document.body.dataset.activePage' in template

    def test_placeholder_pages_reference_followup_issues(self):
        """各 placeholder section に後続 issue 番号が書かれている (epic 追跡可能性)"""
        assert '#58' in template  # patterns ページ (heatmap)
        assert '#59' in template  # patterns ページ (cross-tab)
        assert '#60' in template  # quality ページ (subagent percentile)
        assert '#61' in template  # quality ページ (friction)
        assert '#62' in template  # surface ページ
```

### 共通要素のテスト (footer / nav)

```python
class TestCommonShell:
    def test_conn_status_in_footer_not_in_overview_only(self):
        """接続バッジは全ページ共通の app-footer に含まれる"""
        footer = _extract_section_by_class(template, 'app-footer')
        assert 'id="connStatus"' in footer

    def test_last_rx_in_footer(self):
        footer = _extract_section_by_class(template, 'app-footer')
        assert 'id="lastRx"' in footer

    def test_session_value_in_footer(self):
        footer = _extract_section_by_class(template, 'app-footer')
        assert 'id="sessVal"' in footer

    def test_page_nav_outside_all_page_sections(self):
        """nav は <section data-page=...> の外 (= 全ページ共通)"""
        nav_pos = template.index('class="page-nav"')
        first_section_pos = template.index('data-page="overview"')
        assert nav_pos < first_section_pos
```

### 互換性テスト

```python
class TestBackwardCompatibility:
    def test_existing_widget_ids_preserved(self):
        """v0.6.2 までの ID は破壊せず維持 (loadAndRender 互換)"""
        for el_id in ['kpiRow', 'skillBody', 'subBody', 'spark', 'sparkStats',
                      'stack', 'stackLegend', 'connStatus', 'lastRx', 'sessVal',
                      'ledeEvents', 'ledeDays', 'ledeProjects',
                      'skillSub', 'subSub', 'dailySub', 'projSub']:
            assert f'id="{el_id}"' in template

    def test_window_data_fallback_still_works(self):
        """static export 経路: window.__DATA__ は fetch より先に参照"""
        window_data_pos = template.index('window.__DATA__')
        fetch_pos = template.index("fetch('/api/data'")
        assert window_data_pos < fetch_pos

    def test_existing_dashboard_tests_pass(self):
        """既存テストの XSS escape / gauge layout / 集計ロジックを破壊しない"""
        # 既存 test_dashboard.py / test_export_html.py / test_dashboard_live.py
        # / test_dashboard_sse.py がすべて pass することで担保 (本 plan の DoD)
```

### 既存テストの保護 (regression)

`tests/test_dashboard.py` の以下テストは Overview セクションが従来の DOM ID を
保ったまま動くことを担保する:
- `test_html_template_has_xss_escape_for_user_strings` (esc 経由が template に存在)
- `test_ranking_uses_inline_gauge_layout` (`gauge-bar` / `rank-row` が存在)

`tests/test_export_html.py` の以下テストは `window.__DATA__` 注入経路を担保:
- `window.__DATA__` を含む全テスト群

`tests/test_dashboard_live.py` / `tests/test_dashboard_sse.py` は HTTP / SSE
レイヤーで、本 issue では一切変更しない (SSE が壊れていないことを担保)。

すべて既存通り pass することを最終 CI で確認。

## 📋 実装ステップ (TDD 順)

各 Phase 完了時にレビューを挟むかは **実装着手時に再相談**。RED → GREEN → REFACTOR
の単位で進める。

### Phase 1: テスト追加 (RED)

`tests/test_dashboard_router.py` を新規作成:
- `TestRouterShellStructure` (10 tests / Router proposal-1+4 反映済)
- `TestCommonShell` (4 tests)
- `TestBackwardCompatibility` (2 tests)

**期待 RED 状態の精度**:
- 計 16 tests 中 **15 tests fail / 1 test pass** が期待値
- pass する 1 件: `test_existing_widget_ids_preserved` (現 template に既存 ID あり)
- fail する 15 件の **fail reason** を Phase 1 完了時に記録 (assertion error の
  message を 1 行ずつ plan 末尾に追記)。Phase 2 完了時に「予想 reason 通りに
  GREEN 化されたか」を 16 件すべて re-run して objective に判定する

### Phase 2: template.html 構造変更 (GREEN)

順序:
1. `<nav class="page-nav">` を `.app` 直下の先頭に追加
2. 既存 `<header class="header">` 〜 `</footer>` を `<section data-page="overview">`
   で wrap
3. `conn-status` / `lastRx` / `sessVal` を Overview 内 `<header>` から削除し、
   新規 `<footer class="app-footer">` 内に移動
4. h1 + lede は Overview の `<header>` に残す
5. `<section data-page="patterns" hidden>` 〜 surface の placeholder section
   を Overview の後ろに 3 つ追加
6. CSS 追加 (`.page-nav` / `.page` / `.page-placeholder` / `.app-footer`)
7. 既存の `<footer>` クレジット行ルールは `.app-footer .credits` に変更

このタイミングで全 Router 関連テストが pass、既存テストも全部 pass する状態
を作る。

### Phase 3: Router JS 追加 (GREEN)

`<script>` 先頭 (loadAndRender 定義より前) に router IIFE を挿入:
- `HASH_TO_PAGE` テーブル
- `applyRoute(rawHash)` 関数
- `hashchange` listener
- 初期 `applyRoute(location.hash)` 呼び出し

`loadAndRender` の中身は完全に touch しない。

### Phase 4: 実機動作確認

1. `python3 dashboard/server.py` 起動
2. ブラウザで `http://localhost:<port>/` → Overview 表示確認
3. nav タブ 4 つクリック → 各ページ active state + hidden 切替
4. `#/patterns` を直接アクセス → Patterns active で開く
5. ブラウザの戻る/進むで Overview / Patterns 行き来
6. Overview 表示中に `data/usage.jsonl` を変更 → SSE refresh で再描画
7. Patterns 表示中に SSE refresh が走っても (loadAndRender 完走しても)
   Patterns view が壊れないこと
8. キーボード: Tab で nav タブにフォーカス → Enter で遷移
9. `python3 reports/export_html.py --output /tmp/static.html` → ブラウザで開く
10. 静的 HTML でも nav が動く / `static` バッジが footer で見える

### Phase 5: ドキュメント更新

- `CLAUDE.md`: 「ライブダッシュボードの運用仕様 (v0.3, Issue #14)」セクションの
  下に **「ダッシュボード複数ページ構成 (v0.7.0, Issue #57)」** を追加。内容:
    - 4 ページ表 / hash → page id 対応
    - Overview に共通 chrome (footer with conn-status / lastRx / sessVal) あり
    - `loadAndRender` は Overview 専用 renderer (DOM ID は Overview section 内に
      閉じる) / 後続 PR が他ページ widget を追加するときは page-scoped early-out
      推奨
    - **ID 命名規約** (#58〜#62 が踏む側のインターフェース):
      - 各ページ独自 widget の ID: `<page>-<widget>` 形式
        (例: `patterns-heatmap`, `quality-percentile`, `surface-expansion-rate`)
      - 既存 Overview の ID (`kpiRow`, `skillBody`, ...) は historical naming
        として残す (移行コスト > 一貫性のメリット)
      - 各ページ widget の data-tip 種別も `<page>-<widget>` を踏襲
    - **ページ section 触り方の契約** (並列 PR 想定): 各 PR は自分の `<section
      data-page="<page>">` 内のみを変更し、`<nav class="page-nav">` /
      `<footer class="app-footer">` / 他ページ section には触らない
    - **page-scoped early-out** の標準パターン: 各 widget renderer は
      `if (document.body.dataset.activePage !== '<page>') return;` で no-op 化
- `MEMORY.md`: 1 行 index で「ダッシュボード複数ページ構成 (Issue #57)」を追加

### Phase 6: PR

ブランチ名: `feature/57-dashboard-multipage-shell` (既存 命名規則に整合)
PR タイトル候補: `feat(dashboard): multipage router/nav shell (#57)`

PR 本文に書くこと:
- 親 issue #48 / 当該 issue #57 を参照
- 「shell のみ / 各ページ中身は #58〜#62 で本実装」を明示
- 実機動作スクショ: Overview / Patterns placeholder / 直 URL ブックマーク
- 後続 PR がぶら下がる base であることを記載

## 🚫 Out of Scope (本 plan で扱わないもの)

issue 本体に書かれている out of scope に加え、以下も扱わない。各項目は
「本 PR の disposition / 将来の扱い」の二元情報で記述 (CLAUDE.md "Plan writing"
規約への self-apply):

- **`loadAndRender()` のリファクタ (Overview 描画と他ページ描画の分離)**:
  本 PR では一切 touch せず、Overview の DOM 全書き換え動作を維持。後続 PR で
  page-scoped renderer に分離する案は「後続 PR への申し送り」セクション参照
- **Overview 以外のページの SSE 自動 reload 制御**: 本 PR では Overview 以外が
  空 placeholder のため `loadAndRender` が走っても副作用なし (absolute
  `getElementById` lookup が当たる先は全部 Overview section 内)。#58 以降で
  各 widget が live data を必要とした時点で page-scoped renderer 化を検討
- **nav タブの順序変更 / カスタマイズ**: 本 PR では Overview / Patterns /
  Quality / Surface 固定。将来の追加ページは plan で個別判断
- **ページ遷移アニメーション (CSS transition)**: 本 PR では `hidden` 切替のみで
  瞬時遷移。アニメーション化は別 issue
- **mobile 向けレスポンシブ**: 本 PR では `.page-nav` の `flex` デフォルト wrap に
  任せ、明示的なメディアクエリは追加しない。将来 issue で対応
- **ダーク/ライトテーマ切替**: issue 本体の out of scope (本 PR は構造のみ)

## 📨 後続 PR への申し送り (#58〜#62)

本 shell PR は「後続 5 PR が並走できる契約」を提供する。後続 PR の作業者
(将来の自分含む) が plan 段階で同じ調査を繰り返さないよう、shell の API
contract と既知の地雷を以下に明示する。

### `loadAndRender()` の page-scoping 戦略

現状 `loadAndRender()` は **Overview 専用 renderer**。`document.getElementById('kpiRow')`
のような absolute lookup で動く。後続 PR で他ページ widget に live data を流すときは
以下の選択肢から選ぶ:

| 戦略 | 説明 | 推奨度 |
|---|---|---|
| (a) page-scoped renderer 別関数化 | `renderOverview(data)` / `renderPatterns(data)` に分離し、`applyRoute` から active page の renderer のみ起動 | 中長期推奨 |
| (b) `body[data-active-page]` ガードで no-op | 各 widget の renderer 冒頭で `if (document.body.dataset.activePage !== '<page>') return;` | **#58 短期推奨** |
| (c) widget 単位で SSE 個別購読 | 各 widget が `EventSource` を独立に接続 | 過剰 (複雑性 > 価値) |

**推奨フロー**: #58 では (b) で着地、#58 がマージされた後に renderer の数が増えて
きた段階で (a) への移行を別 issue で検討。本 PR で expose する `body[data-active-page]`
は (b) を即時可能にする contract。

### widget ID 命名規約

各ページ独自 widget の ID は `<page>-<widget>` 形式で命名する:
- `patterns-heatmap-svg` / `patterns-cross-tab-table`
- `quality-percentile-chart` / `quality-friction-list`
- `surface-expansion-rate` / `surface-instructions-loaded`

既存 Overview の ID (`kpiRow`, `skillBody`, `subBody`, `spark`, `stack`, ...) は
historical naming としてそのまま残す (移行コスト > 一貫性のメリット)。

### 触ってはいけない共通 chrome

各 PR は以下に **触らない契約**:
- `<nav class="page-nav">` (Overview / Patterns / Quality / Surface のリンク 4 つ)
- `<footer class="app-footer">` (conn-status / lastRx / sessVal / クレジット行)
- `applyRoute()` IIFE と `HASH_TO_PAGE` テーブル
- 他ページの `<section data-page="<other>">` の中身

各 PR が触ってよいのは自分の `<section data-page="<self>">` の中身のみ。
nav / footer / router を変更する必要が出たら別 issue で先行して固める。

### `/api/data` レスポンスの拡張ルール

本 PR では `/api/data` を一切変更しない。後続 PR で新しい集計フィールドを追加する
ときは **必ず additive** (既存フィールドの key / 値型を変えない / 削除しない) で、
新フィールド名は widget ID と揃える (例: `patterns_heatmap`, `quality_percentile`)。
これにより `window.__DATA__` 注入経路 (`render_static_html` / export_html.py) が
壊れない。

## ⚠️ リスクと対策

| リスク | 影響 | 対策 |
|---|---|---|
| `loadAndRender()` が Overview の DOM 取得時 `null` で crash | SSE refresh 後に descrendant ページで JS エラー | section を DOM tree に残す方式 (`hidden` で非表示) で対応済 |
| `<a href="#/x">` クリックでブラウザがスクロール | UX が壊れる | 名前付きアンカーが存在しない `#/...` 形式なのでスクロールは発生しない (検証済仕様) |
| 静的 export で初期 hash が空 | Overview が選択されない | `applyRoute('')` → fallback で `'overview'` を返す `HASH_TO_PAGE` 設計 |
| 後続 5 issue が DOM ID 衝突する | #58〜 で同じ ID を使うと壊れる | 各ページ section の中で ID を出す慣習を CLAUDE.md に明示 (Phase 5) |
| nav の active state が SSE refresh で消える | Patterns 表示中に Overview に戻る視覚バグ | `applyRoute` は SSE 経路と独立 IIFE で動作。`loadAndRender` は nav に触らない |
| 既存 `<header class="header">` の grid layout (1fr auto) が Overview 内で崩れる | 視覚 regression | Overview section の中でも grid は機能する。実機確認 (Phase 4) で担保 |

## 📦 変更ファイル一覧 (見込み)

- `dashboard/template.html` (本体構造 + CSS + router JS)
- `tests/test_dashboard_router.py` (新規)
- `CLAUDE.md` (新セクション追加)
- `~/.claude/projects/-Users-kkoichi-Developer-personal-claude-transcript-analyzer/memory/MEMORY.md` (1 行 index)

`dashboard/server.py` は **触らない**。`/api/data` も `render_static_html` も
`build_dashboard_data` も互換維持。

## ✔️ Definition of Done (本 plan)

- [ ] `tests/test_dashboard_router.py` の新規 16 tests 全 pass
- [ ] `tests/test_dashboard*.py` / `tests/test_export_html*.py` 全 pass (regression)
- [ ] 実機 4 ページ navigation 動作確認 / 直 URL ブックマーク確認 / SSE refresh が
      Overview 描画継続を妨げない / キーボード Tab→Enter 遷移確認 / ブラウザ戻る進む確認
- [ ] `python3 reports/export_html.py` 経由の静的 HTML でも nav が動き、conn-status が
      footer で `static` バッジ表示される
- [ ] CLAUDE.md / MEMORY.md 更新済み (ID 命名規約 / 触ってはいけない共通 chrome /
      page-scoped early-out 標準パターン を含む)
- [ ] PR `feature/57-dashboard-multipage-shell` を `main` 向けに作成 / レビュー承認

