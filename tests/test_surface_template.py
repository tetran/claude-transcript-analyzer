"""tests/test_surface_template.py — Issue #62 Surface ページ template 構造テスト。

dashboard/template.html の `<section data-page="surface">` から placeholder が外れ、
A4 (slash command source breakdown) と B4 (instructions_loaded breakdown) の
2 panel が並び、対応する renderer 関数 / CSS / tooltip 分岐が入ったかを文字列
レベルで検証する (`tests/test_friction_template.py` と同型)。
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


def _extract_function_body(template: str, fn_name: str) -> str:
    body_start = template.index(f'function {fn_name}')
    body_end = template.index('function ', body_start + len(f'function {fn_name}'))
    return template[body_start:body_end]


# ============================================================
#  TestSurfacePagePanels — A4 + B4 DOM / renderer 構造
# ============================================================
class TestSurfacePagePanels:
    def test_surface_section_no_longer_placeholder(self):
        section = _extract_section(_load_template(), 'surface')
        assert 'page-placeholder' not in section, \
            "surface section should no longer be a placeholder"

    def test_surface_section_has_source_panel(self):
        section = _extract_section(_load_template(), 'surface')
        for el_id in [
            'surface-source-panel',
            'surface-source',
            'surface-source-sub',
        ]:
            assert f'id="{el_id}"' in section, f"id={el_id} missing from Surface section"

    def test_surface_section_has_instr_panel(self):
        section = _extract_section(_load_template(), 'surface')
        for el_id in [
            'surface-instr-panel',
            'surface-instr-mt',
            'surface-instr-lr',
            'surface-instr-glob',
            'surface-instr-sub',
        ]:
            assert f'id="{el_id}"' in section, f"id={el_id} missing from Surface section"

    def test_source_table_has_thead_columns(self):
        # 列順 Skill / Expansion / Submit / Rate (legacy 列は user 判断で削除)
        section = _extract_section(_load_template(), 'surface')
        idx = section.index('id="surface-source"')
        thead_start = section.index('<thead>', idx)
        thead_end = section.index('</thead>', thead_start)
        thead = section[thead_start:thead_end]
        positions = []
        for col in ['Skill', 'Expansion', 'Submit', 'Rate']:
            i = thead.find(col)
            assert i >= 0, f"surface-source thead missing column: {col}"
            positions.append((i, col))
        assert positions == sorted(positions), \
            f"surface-source columns not in expected order: {positions}"
        assert 'Legacy' not in thead, "Legacy column should be removed"

    def test_glob_table_has_thead_columns(self):
        section = _extract_section(_load_template(), 'surface')
        idx = section.index('id="surface-instr-glob"')
        thead_start = section.index('<thead>', idx)
        thead_end = section.index('</thead>', thead_start)
        thead = section[thead_start:thead_end]
        positions = []
        for col in ['File path', 'Count']:
            i = thead.find(col)
            assert i >= 0, f"glob-table thead missing column: {col}"
            positions.append((i, col))
        assert positions == sorted(positions), \
            f"glob-table columns not in expected order: {positions}"

    def test_instr_grid_has_three_cols(self):
        section = _extract_section(_load_template(), 'surface')
        idx = section.index('id="surface-instr-panel"')
        panel_chunk = section[idx:idx + 4000]
        assert 'instr-grid' in panel_chunk
        assert 'id="surface-instr-mt"' in panel_chunk
        assert 'id="surface-instr-lr"' in panel_chunk
        assert 'id="surface-instr-glob"' in panel_chunk

    def test_template_has_source_renderer(self):
        template = _load_template()
        assert 'function renderSlashCommandSourceBreakdown' in template, \
            "renderSlashCommandSourceBreakdown function missing"

    def test_template_has_instr_renderer(self):
        template = _load_template()
        for fn in ['renderInstructionsLoadedBreakdown', 'renderInstrBars', 'renderGlobTable']:
            assert f'function {fn}' in template, f"{fn} function missing"

    def test_loadAndRender_invokes_surface_renderers(self):
        template = _load_template()
        assert 'renderSlashCommandSourceBreakdown(data.slash_command_source_breakdown)' in template, \
            "renderSlashCommandSourceBreakdown call missing"
        assert 'renderInstructionsLoadedBreakdown(data.instructions_loaded_breakdown)' in template, \
            "renderInstructionsLoadedBreakdown call missing"

    def test_source_renderer_has_page_scoped_early_out(self):
        body = _extract_function_body(_load_template(), 'renderSlashCommandSourceBreakdown')
        assert "activePage !== 'surface'" in body[:500], \
            "renderSlashCommandSourceBreakdown missing page-scoped early-out"

    def test_instr_renderer_has_page_scoped_early_out(self):
        body = _extract_function_body(_load_template(), 'renderInstructionsLoadedBreakdown')
        assert "activePage !== 'surface'" in body[:500], \
            "renderInstructionsLoadedBreakdown missing page-scoped early-out"

    def test_help_popups_present(self):
        template = _load_template()
        for hid in ['hp-source', 'hp-instr']:
            assert f'id="{hid}"' in template, f"help-pop {hid} missing"
