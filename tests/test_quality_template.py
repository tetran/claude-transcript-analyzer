"""tests/test_quality_template.py — Issue #60 Quality page widget 構造テスト。

dashboard/template.html の `<section data-page="quality">` に
A5 (subagent percentile table) と B3 (subagent failure weekly trend chart) の
2 panel が並び、対応する renderer 関数 / CSS / tooltip 分岐が入ったかを
文字列レベルで検証する (`tests/test_dashboard_cross_tabs_template.py` と同型)。
"""
# pylint: disable=line-too-long
import re
from pathlib import Path

_TEMPLATE_PATH = Path(__file__).parent.parent / "dashboard" / "template.html"


def _load_template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _extract_section(template: str, page: str) -> str:
    # `data-page="X"` は CSS の attribute selector にも現れるので、
    # 必ず `<section ...>` の開始タグから始まる本物の section だけを拾う。
    match = re.search(rf'<section\b[^>]*data-page="{re.escape(page)}"[^>]*>', template)
    assert match is not None, f"<section data-page={page!r}> not found"
    section_open = match.start()
    end = template.index('</section>', match.end())
    return template[section_open:end + len('</section>')]


# ============================================================
#  TestQualityPageDOM
# ============================================================

class TestQualityPageDOM:
    def test_quality_section_is_no_longer_placeholder(self):
        section = _extract_section(_load_template(), 'quality')
        assert 'page-placeholder' not in section, "Quality should no longer be placeholder"
        assert 'Coming soon' not in section, "Quality should no longer say 'Coming soon'"

    def test_quality_section_has_percentile_panel(self):
        section = _extract_section(_load_template(), 'quality')
        for el_id in [
            'quality-percentile-panel',
            'quality-percentile',
            'quality-percentile-sub',
        ]:
            assert f'id="{el_id}"' in section, f"id={el_id} missing from Quality section"

    def test_quality_section_has_trend_panel(self):
        section = _extract_section(_load_template(), 'quality')
        for el_id in [
            'quality-trend-panel',
            'quality-trend',
            'quality-trend-legend',
            'quality-trend-sub',
        ]:
            assert f'id="{el_id}"' in section, f"id={el_id} missing from Quality section"

    def test_quality_section_has_no_overview_widgets(self):
        """kpiRow / skillBody / subBody / spark など Overview 専用 ID が混入しない"""
        section = _extract_section(_load_template(), 'quality')
        for el_id in ['kpiRow', 'skillBody', 'subBody', 'spark', 'stack', 'stackLegend']:
            assert f'id="{el_id}"' not in section, f"Quality should not contain Overview id={el_id}"

    def test_template_has_percentile_renderer_function(self):
        template = _load_template()
        assert 'function renderSubagentPercentile' in template, \
            "renderSubagentPercentile function missing"

    def test_template_has_trend_renderer_function(self):
        template = _load_template()
        assert 'function renderSubagentFailureTrend' in template, \
            "renderSubagentFailureTrend function missing"

    def test_loadAndRender_invokes_quality_renderers(self):
        template = _load_template()
        assert 'renderSubagentPercentile(data.subagent_ranking)' in template, \
            "renderSubagentPercentile(data.subagent_ranking) call missing"
        assert 'renderSubagentFailureTrend(data.subagent_failure_trend)' in template, \
            "renderSubagentFailureTrend(data.subagent_failure_trend) call missing"

    def test_percentile_table_has_thead_columns(self):
        """列順は Subagent / Count / Samples / avg / p50 / p90 / p99 (Samples を Count
        直後に置くことで、percentile の信頼度を最初に整理させる読み順を pin する。
        sample_count <= count の関係も並びで読み取れる)。"""
        section = _extract_section(_load_template(), 'quality')
        # thead 部分を抜き出して列順を確認
        thead_start = section.index('<thead>')
        thead_end = section.index('</thead>')
        thead = section[thead_start:thead_end]
        positions = []
        for col in ['Subagent', 'Count', 'Samples', 'avg', 'p50', 'p90', 'p99']:
            idx = thead.find(col)
            assert idx >= 0, f"thead missing column: {col}"
            positions.append((idx, col))
        assert positions == sorted(positions), \
            f"thead 列順が想定と違う: {[c for _, c in positions]} (期待: Subagent, Count, Samples, avg, p50, p90, p99)"

    def test_trend_chart_uses_svg(self):
        """renderSubagentFailureTrend 内に <svg / <polyline / <circle が出る"""
        template = _load_template()
        body_start = template.index('function renderSubagentFailureTrend')
        body_end = template.index('function ', body_start + 10)
        body = template[body_start:body_end]
        for tag in ['<svg ', '<polyline', '<circle']:
            assert tag in body, f"renderSubagentFailureTrend に {tag!r} がない"

    def test_trend_single_point_renders_circle_no_polyline(self):
        """単一データ点 (= 1 週分しかない / 連続 run が長さ 1) では polyline を描かず
        circle のみ残る: renderer 内で長さ 2 以上の polyline guard が存在することを pin。
        gap-bridging 修正後は run 単位で guard するため `run.length >= 2` を許容する。"""
        template = _load_template()
        body_start = template.index('function renderSubagentFailureTrend')
        body_end = template.index('function ', body_start + 10)
        body = template[body_start:body_end]
        assert ('pts.length >= 2' in body) or ('run.length >= 2' in body), \
            "polyline length guard (>= 2) missing"

    def test_trend_polyline_splits_at_gaps_no_bridging(self):
        """type 別に観測なし週 (gap) を跨ぐ単一 polyline を描かない: weeks 軸上で連続
        index ごとに run を組み、run 単位で polyline を吐く実装になっていること。
        renderer body 内で連続性判定 (consecutive index check) を pin する。"""
        template = _load_template()
        body_start = template.index('function renderSubagentFailureTrend')
        body_end = template.index('function ', body_start + 10)
        body = template[body_start:body_end]
        # 連続 run ごとに polyline を吐く実装の signature: 「consecutive」「run」「segment」のいずれか + 連続性比較
        assert ('p.i - prev' in body) or ('current.i + 1' in body) or ('runs.push' in body), \
            "gap-aware polyline split (consecutive index check) が未実装"

    def test_trend_xaxis_densified_for_empty_calendar_weeks(self):
        """server が観測なし週を返さない仕様 (sparse axis) のため、renderer 側で
        observedWeeks を 7-day 増分で densify して空週も x-axis に展開する。
        これにより全 type で観測 0 だった週も timeline 上に inactivity 期間として
        可視化される (xOf(i) が暦週位置に揃う)。
        renderer body 内で週増分 (setUTCDate + 7) を実装している痕跡を pin する。"""
        template = _load_template()
        body_start = template.index('function renderSubagentFailureTrend')
        body_end = template.index('function ', body_start + 10)
        body = template[body_start:body_end]
        # 7-day 増分の densify 実装: setUTCDate + 7 / Date('T00:00:00Z') 経由のいずれか
        assert ('setUTCDate' in body) and ('+ 7' in body or '+7' in body or 'getUTCDate() + 7' in body), \
            "x-axis densify (7-day step via Date.setUTCDate) が未実装"

    def test_percentile_renderer_has_page_scoped_early_out(self):
        template = _load_template()
        body_start = template.index('function renderSubagentPercentile')
        body_first_500 = template[body_start:body_start + 500]
        assert "activePage !== 'quality'" in body_first_500, \
            "page-scoped early-out (activePage !== 'quality') が冒頭にない"

    def test_trend_renderer_has_page_scoped_early_out(self):
        template = _load_template()
        body_start = template.index('function renderSubagentFailureTrend')
        body_first_500 = template[body_start:body_start + 500]
        assert "activePage !== 'quality'" in body_first_500, \
            "page-scoped early-out (activePage !== 'quality') が冒頭にない"

    def test_percentile_data_tip_kind_present(self):
        template = _load_template()
        assert 'data-tip="percentile"' in template, "data-tip=\"percentile\" missing"
        assert "kind === 'percentile'" in template, "dtipBuild kind === 'percentile' branch missing"

    def test_trend_data_tip_kind_present(self):
        template = _load_template()
        assert 'data-tip="trend"' in template, "data-tip=\"trend\" missing"
        assert "kind === 'trend'" in template, "dtipBuild kind === 'trend' branch missing"

    def test_quality_panel_uses_coral_for_percentile(self):
        section = _extract_section(_load_template(), 'quality')
        # percentile panel-head に c-coral クラス
        idx = section.index('id="quality-percentile-panel"')
        head_idx = section.index('panel-head', idx - 200)
        head_chunk = section[head_idx:head_idx + 100]
        assert 'c-coral' in head_chunk, "percentile panel uses c-coral missing"

    def test_quality_panel_uses_mint_for_trend(self):
        section = _extract_section(_load_template(), 'quality')
        idx = section.index('id="quality-trend-panel"')
        head_idx = section.index('panel-head', idx - 200)
        head_chunk = section[head_idx:head_idx + 100]
        assert 'c-mint' in head_chunk, "trend panel uses c-mint missing"


# ============================================================
#  TestPagePanelSpacing — Issue #71: Patterns/Quality/Surface タブの
#  panel 間マージンを CSS で確保する (Overview 以外の 3 ページが
#  対象。Overview は inline style + .two-up gap で既に間隔がある)。
# ============================================================

class TestPagePanelSpacing:
    def test_page_panel_adjacent_sibling_has_margin_top(self):
        """`.page > .panel + .panel { margin-top: ... }` 相当の rule が存在する。

        Patterns / Quality / Surface 各ページで連続する .panel 要素間にマージンを
        入れるための CSS rule。selector 形式は `.page > .panel + .panel` か
        同等の adjacent-sibling combinator を含めばよい。
        """
        template = _load_template()
        # CSS ブロックだけを切り出して selector を検索
        style_start = template.index('<style>')
        style_end = template.index('</style>', style_start)
        css = template[style_start:style_end]
        assert '.page > .panel + .panel' in css, \
            "Patterns/Quality/Surface ページの .panel 間マージン CSS rule が未定義 (Issue #71)"
