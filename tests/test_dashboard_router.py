"""tests/test_dashboard_router.py — Issue #57 マルチページ shell の構造テスト。

dashboard/template.html に以下が入ったかを文字列レベルで検証する:
- 4 タブ nav (Overview / Patterns / Quality / Surface) + ハッシュ router
- Overview 専用 chrome (h1 + lede + 既存 KPI / 各 panel)
- 全ページ共通の app-footer (conn-status / lastRx / sessVal)
- 後続 PR (#58〜#62) の placeholder section

JS 実行を伴う動作 (キーボード遷移 / SSE refresh 整合 / ブラウザ戻る進む) は実機で
担保するが、template の **構造的前提** は CI で守る。
"""
# pylint: disable=line-too-long
from pathlib import Path

_TEMPLATE_PATH = Path(__file__).parent.parent / "dashboard" / "template.html"


def _load_template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _extract_section(template: str, page: str) -> str:
    """`<section data-page="<page>">` 〜 対応する `</section>` を返す。

    nested section が存在しない前提 (本 issue の構造設計上の不変条件)。
    """
    marker = f'data-page="{page}"'
    start = template.index(marker)
    section_open = template.rfind('<section', 0, start)
    assert section_open != -1, f"section open tag not found before {marker}"
    end = template.index('</section>', start)
    return template[section_open:end + len('</section>')]


def _extract_footer(template: str) -> str:
    """`<footer class="app-footer">` 〜 `</footer>` を返す。"""
    start = template.index('class="app-footer"')
    footer_open = template.rfind('<footer', 0, start)
    assert footer_open != -1, "footer open tag not found"
    end = template.index('</footer>', start)
    return template[footer_open:end + len('</footer>')]


# ============================================================
#  TestRouterShellStructure (10 tests / proposal-1+4 反映)
# ============================================================
class TestRouterShellStructure:
    def test_template_has_four_page_nav_links(self):
        """nav は 4 タブ (Overview / Patterns / Quality / Surface)"""
        template = _load_template()
        for path in ['#/', '#/patterns', '#/quality', '#/surface']:
            assert f'href="{path}"' in template, f"nav link href={path} not found"

    def test_template_has_four_page_sections(self):
        """4 つの <section data-page="..."> が存在"""
        template = _load_template()
        for page in ['overview', 'patterns', 'quality', 'surface']:
            assert f'data-page="{page}"' in template, f"section data-page={page} not found"

    def test_overview_page_contains_existing_widgets(self):
        """Overview section 内に既存 widget の DOM ID が含まれる"""
        template = _load_template()
        overview = _extract_section(template, 'overview')
        for el_id in ['kpiRow', 'skillBody', 'subBody', 'spark', 'stack', 'stackLegend']:
            assert f'id="{el_id}"' in overview, f"id={el_id} missing from Overview section"

    def test_router_javascript_present(self):
        """router 中核 (hashchange listener / data-page-link / aria-current)"""
        template = _load_template()
        assert "addEventListener('hashchange'" in template, "hashchange listener missing"
        assert ("'#/patterns'" in template) or ('"#/patterns"' in template), "patterns route literal missing"
        assert 'data-page-link' in template, "data-page-link selector missing"
        assert 'aria-current' in template, "aria-current attribute missing"

    def test_router_initial_apply_route_call(self):
        """applyRoute(location.hash) は listener 内 + 初期呼び出しで 2 回以上現れる"""
        template = _load_template()
        assert template.count('applyRoute(location.hash)') >= 2, \
            f"applyRoute(location.hash) should appear ≥2 times, got {template.count('applyRoute(location.hash)')}"

    def test_router_hash_table_covers_empty_and_slash(self):
        """空 hash / '#' 単体 / '#/' の 3 経路すべてが overview に map される"""
        template = _load_template()
        assert "'': 'overview'" in template, "empty hash → overview mapping missing"
        assert "'#': 'overview'" in template, "'#' → overview mapping missing"
        assert "'#/': 'overview'" in template, "'#/' → overview mapping missing"

    def test_router_fallback_to_overview(self):
        """未知 hash は applyRoute 内で 'overview' に倒れる"""
        template = _load_template()
        assert "|| 'overview'" in template, "fallback `|| 'overview'` missing"

    def test_body_data_active_page_exposed_for_followups(self):
        """後続 PR (#58〜#62) が page-scoped early-out できる contract"""
        template = _load_template()
        assert 'document.body.dataset.activePage' in template, \
            "body.dataset.activePage assignment missing (后続 PR が触る contract)"

    def test_placeholder_pages_reference_followup_issues(self):
        """各 placeholder section に後続 issue 番号が書かれている (epic 追跡可能性)"""
        template = _load_template()
        for issue_num in ['#58', '#59', '#60', '#61', '#62']:
            assert issue_num in template, f"placeholder should reference {issue_num}"


# ============================================================
#  TestCommonShell (4 tests)
# ============================================================
class TestCommonShell:
    def test_conn_status_in_app_footer(self):
        """接続バッジは全ページ共通の app-footer に含まれる"""
        template = _load_template()
        footer = _extract_footer(template)
        assert 'id="connStatus"' in footer, "conn-status should be in app-footer"

    def test_last_rx_in_app_footer(self):
        """最終更新タイムスタンプも app-footer"""
        template = _load_template()
        footer = _extract_footer(template)
        assert 'id="lastRx"' in footer, "lastRx should be in app-footer"

    def test_session_value_in_app_footer(self):
        """セッション数表示も app-footer"""
        template = _load_template()
        footer = _extract_footer(template)
        assert 'id="sessVal"' in footer, "sessVal should be in app-footer"

    def test_page_nav_outside_all_page_sections(self):
        """nav は <section data-page=...> の外 (= 全ページ共通の頂部)"""
        template = _load_template()
        nav_pos = template.index('class="page-nav"')
        first_section_pos = template.index('data-page="overview"')
        assert nav_pos < first_section_pos, "page-nav should appear before any data-page section"


# ============================================================
#  TestBackwardCompatibility (2 tests)
# ============================================================
class TestBackwardCompatibility:
    """Overview section が従来の DOM ID を保ち、loadAndRender / static export が壊れない"""

    def test_existing_widget_ids_preserved(self):
        """v0.6.2 までの ID 18 個は破壊せず維持"""
        template = _load_template()
        # 注意: ここは Overview section に閉じ込められた ID も含む。
        # template 全体に存在することだけ確認 (DOM 階層は別テスト)。
        for el_id in [
            'kpiRow', 'skillBody', 'subBody', 'spark', 'sparkStats',
            'stack', 'stackLegend', 'connStatus', 'lastRx', 'sessVal',
            'ledeEvents', 'ledeDays', 'ledeProjects',
            'skillSub', 'subSub', 'dailySub', 'projSub',
            'dataTooltip',
        ]:
            assert f'id="{el_id}"' in template, f"existing widget id={el_id} missing (regression)"

    def test_window_data_fallback_still_works(self):
        """static export 経路: window.__DATA__ は fetch より先に参照される"""
        template = _load_template()
        window_data_pos = template.index('window.__DATA__')
        fetch_pos = template.index("fetch('/api/data'")
        assert window_data_pos < fetch_pos, \
            "window.__DATA__ must be checked before fetch('/api/data') for static export compat"
