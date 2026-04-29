"""tests/test_dashboard_heatmap_template.py — Issue #58 Patterns ページ heatmap widget 構造テスト。

dashboard/template.html の <section data-page="patterns"> に heatmap widget の
DOM / 周辺 JS が入ったかを文字列レベルで検証する。JS 実行を伴う動作 (実 cell 配置 /
SSE refresh / TZ 変換結果) は実機 smoke で担保するが、template の **構造的前提** は
CI で守る。`test_dashboard_router.py` のパターンを踏襲。
"""
# pylint: disable=line-too-long
import re

from _dashboard_template_loader import load_assembled_template


def _load_template() -> str:
    return load_assembled_template()


def _extract_section(template: str, page: str) -> str:
    """`<section data-page="<page>">` 〜 対応する `</section>` を返す。

    `data-page="X"` は CSS の attribute selector にも現れるので、
    必ず `<section ...>` の開始タグから始まる本物の section だけを拾う。
    """
    match = re.search(rf'<section\b[^>]*data-page="{re.escape(page)}"[^>]*>', template)
    assert match is not None, f"<section data-page={page!r}> not found"
    section_open = match.start()
    end = template.index('</section>', match.end())
    return template[section_open:end + len('</section>')]


# ============================================================
#  TestPatternsHeatmapDOM (7 tests)
# ============================================================
class TestPatternsHeatmapDOM:
    def test_patterns_section_has_heatmap_panel(self):
        """heatmap widget の root / grid / legend / sub の DOM ID 4 つが Patterns
        section 内に存在する。"""
        template = _load_template()
        section = _extract_section(template, 'patterns')
        for el_id in [
            'patterns-heatmap-panel',
            'patterns-heatmap',
            'patterns-heatmap-legend',
            'patterns-heatmap-sub',
        ]:
            assert f'id="{el_id}"' in section, f"id={el_id} missing from Patterns section"

    def test_patterns_section_no_longer_pure_placeholder(self):
        """heatmap が描画されるので section 開始タグの class から page-placeholder が外れる。
        section 内部に <p class="placeholder-body"> として #59 言及を残すのは許容。"""
        template = _load_template()
        section = _extract_section(template, 'patterns')
        opening = section.split('>', 1)[0]
        assert 'page-placeholder' not in opening, \
            f"<section> opening should not have page-placeholder class anymore: {opening!r}"

    def test_template_has_heatmap_renderer_function(self):
        template = _load_template()
        assert 'function renderHourlyHeatmap' in template, \
            "renderHourlyHeatmap function definition missing"

    def test_template_has_heatmap_data_tip_kind(self):
        """data-tip="heatmap" 属性 + dtipBuild の heatmap 分岐の両方が存在。"""
        template = _load_template()
        assert 'data-tip="heatmap"' in template, "data-tip=heatmap attribute missing"
        assert "kind === 'heatmap'" in template, "dtipBuild kind=heatmap branch missing"

    def test_template_mon_sun_weekday_conversion_present(self):
        """(d.getDay() + 6) % 7 で Mon=0..Sun=6 変換するコードが含まれる。"""
        template = _load_template()
        assert "(d.getDay() + 6) % 7" in template, \
            "Mon-Sun conversion `(d.getDay() + 6) % 7` missing"

    def test_loadAndRender_invokes_heatmap_renderer(self):
        """loadAndRender 経路から renderHourlyHeatmap が呼ばれる。"""
        template = _load_template()
        assert 'renderHourlyHeatmap(data.hourly_heatmap)' in template, \
            "renderHourlyHeatmap call from loadAndRender missing"

# ============================================================
#  TestPatternsRouterIntegration (2 tests)
# ============================================================
class TestPatternsRouterIntegration:
    def test_main_iife_has_hashchange_loadandrender_listener(self):
        """page-scoped early-out のための hashchange → loadAndRender 再実行リスナーが
        main IIFE 側にある (Q2 連携の前提)。"""
        template = _load_template()
        # router IIFE は applyRoute(location.hash) を呼ぶ既存リスナーを持つ。
        # main IIFE は loadAndRender() を再呼び出しするリスナーを持つ。両者は別経路。
        hashchange_count = template.count("addEventListener('hashchange'")
        assert hashchange_count >= 2, (
            f"expected >=2 hashchange listeners (router IIFE + main IIFE), "
            f"got {hashchange_count}"
        )
        # main IIFE の listener は loadAndRender を呼ぶ
        assert 'loadAndRender()' in template, "main IIFE should re-invoke loadAndRender"

    def test_renderer_has_page_scoped_early_out(self):
        """renderHourlyHeatmap は activePage !== 'patterns' で early-out する
        (Q2 規範: #59〜#62 が同じ pattern に乗れる)。"""
        template = _load_template()
        # 'patterns' の対比は activePage 判定で行う
        assert "document.body.dataset.activePage !== 'patterns'" in template, \
            "renderHourlyHeatmap should early-out when activePage is not patterns"
