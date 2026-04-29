"""tests/test_quality_template.py — Issue #60 Quality page widget 構造テスト。

dashboard/template.html の `<section data-page="quality">` に
A5 (subagent percentile table) と B3 (subagent failure weekly trend chart) の
2 panel が並び、対応する renderer 関数 / CSS / tooltip 分岐が入ったかを
文字列レベルで検証する (`tests/test_dashboard_cross_tabs_template.py` と同型)。
"""
# pylint: disable=line-too-long
from pathlib import Path

_TEMPLATE_PATH = Path(__file__).parent.parent / "dashboard" / "template.html"


def _load_template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _extract_section(template: str, page: str) -> str:
    marker = f'data-page="{page}"'
    start = template.index(marker)
    section_open = template.rfind('<section', 0, start)
    assert section_open != -1
    end = template.index('</section>', start)
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
        """P5 反映: 列順は Subagent / Count / Samples / avg / p50 / p90 / p99
        (Samples は Count 直後)"""
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

    def test_trend_single_week_renders_circles_no_polyline(self):
        """P4 反映: weeks.length === 1 の degenerate path で polyline 0 / circle のみ。
        renderer body 内で `pts.length >= 2` の guard が存在することを pin。"""
        template = _load_template()
        body_start = template.index('function renderSubagentFailureTrend')
        body_end = template.index('function ', body_start + 10)
        body = template[body_start:body_end]
        assert 'pts.length >= 2' in body, "polyline guard `pts.length >= 2` missing"

    def test_trend_polyline_splits_at_gaps_no_bridging(self):
        """Codex Round 1 / P3 反映: type 別に観測なし週 (gap) を跨ぐ単一 polyline は描かず、
        連続 run ごとに分割して polyline を出す (= consecutive index で run を組む)。
        renderer body 内で run/segment 概念を実装している痕跡 (i 比較で連続性判定) を pin。"""
        template = _load_template()
        body_start = template.index('function renderSubagentFailureTrend')
        body_end = template.index('function ', body_start + 10)
        body = template[body_start:body_end]
        # 連続 run ごとに polyline を吐く実装の signature: 「consecutive」「run」「segment」のいずれか + 連続性比較
        assert ('p.i - prev' in body) or ('current.i + 1' in body) or ('runs.push' in body), \
            "gap-aware polyline split (consecutive index check) が未実装"

    def test_trend_xaxis_densified_for_empty_calendar_weeks(self):
        """Codex Round 2 / P2#2 反映: server が観測なし週を返さない仕様のため、
        renderer 側で weekSet を 7-day 増分で densify して空週も x-axis に表示する。
        renderer body 内で week 増分 (Date 操作 / setUTCDate / 7-day 加算) を実装している
        痕跡を pin。これにより inactivity 期間が timeline 上に可視化される。"""
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
