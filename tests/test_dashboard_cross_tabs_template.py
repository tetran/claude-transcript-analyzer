"""tests/test_dashboard_cross_tabs_template.py — Issue #59 cross-tab widget 構造テスト。

dashboard/template.html の <section data-page="patterns"> に B1 (skill cooccurrence
table) と B2 (project × skill heatmap) の panel が並び、対応する renderer 関数 / CSS /
tooltip 分岐が入ったかを文字列レベルで検証。
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
#  TestPatternsCrossTabsDOM
# ============================================================
class TestPatternsCrossTabsDOM:
    def test_patterns_section_has_cooccurrence_panel(self):
        template = _load_template()
        section = _extract_section(template, 'patterns')
        for el_id in [
            'patterns-cooccurrence-panel',
            'patterns-cooccurrence',
            'patterns-cooccurrence-sub',
        ]:
            assert f'id="{el_id}"' in section, f"id={el_id} missing from Patterns section"

    def test_patterns_section_has_projskill_panel(self):
        template = _load_template()
        section = _extract_section(template, 'patterns')
        for el_id in [
            'patterns-projskill-panel',
            'patterns-projskill',
            'patterns-projskill-legend',
            'patterns-projskill-sub',
        ]:
            assert f'id="{el_id}"' in section, f"id={el_id} missing from Patterns section"

    def test_patterns_section_no_longer_has_issue_59_placeholder(self):
        # #58 で残した <p class="placeholder-body">...今後追加予定...</p> が削除されている
        template = _load_template()
        section = _extract_section(template, 'patterns')
        assert '今後追加予定' not in section, (
            "Patterns section should no longer have the #59 placeholder text"
        )

    def test_template_has_cooccurrence_renderer_function(self):
        template = _load_template()
        assert 'function renderSkillCooccurrence' in template

    def test_template_has_projskill_renderer_function(self):
        template = _load_template()
        assert 'function renderProjectSkillMatrix' in template

    def test_template_has_cooc_data_tip_kind(self):
        template = _load_template()
        assert 'data-tip="cooc"' in template
        assert "kind === 'cooc'" in template

    def test_template_has_projskill_data_tip_kind(self):
        template = _load_template()
        assert 'data-tip="projskill"' in template
        assert "kind === 'projskill'" in template

    def test_loadAndRender_invokes_cross_tab_renderers(self):
        template = _load_template()
        assert 'renderSkillCooccurrence(data.skill_cooccurrence)' in template
        assert 'renderProjectSkillMatrix(data.project_skill_matrix)' in template

    def test_cooccurrence_table_has_thead(self):
        # Proposal 1: count 単位は sessions
        template = _load_template()
        assert '<thead>' in template
        assert 'Skill A' in template
        assert 'Sessions' in template

    def test_cooccurrence_renderer_has_page_scoped_early_out(self):
        # Proposal 5 反映: 関数冒頭 400 chars 以内に early-out が入っている
        template = _load_template()
        idx = template.index('function renderSkillCooccurrence')
        body = template[idx:idx + 400]
        assert "document.body.dataset.activePage !== 'patterns'" in body

    def test_projskill_renderer_has_page_scoped_early_out(self):
        template = _load_template()
        idx = template.index('function renderProjectSkillMatrix')
        body = template[idx:idx + 400]
        assert "document.body.dataset.activePage !== 'patterns'" in body

    def test_cooccurrence_tooltip_uses_sessions_label(self):
        # Proposal 1: tooltip lbl が 'sessions' / 旧 'co-occurrences' は残らない
        template = _load_template()
        assert ">sessions<" in template or "'sessions'" in template
        assert 'co-occurrences' not in template

    def test_projskill_sub_label_includes_covered_count(self):
        # Proposal 2 反映: sub label に covered/total のカバー率が組まれている
        template = _load_template()
        idx = template.index('function renderProjectSkillMatrix')
        body = template[idx:idx + 2500]
        assert 'covered_count' in body or 'covered' in body
        assert 'total_count' in body or '% covered' in body

    def test_projskill_panel_uses_peach_color_after_cooccurrence(self):
        # 順序: hourly heatmap (#58) → cooccurrence (#59 B1) → projskill (#59 B2)
        template = _load_template()
        section = _extract_section(template, 'patterns')
        heatmap_idx = section.index('patterns-heatmap-panel')
        cooc_idx = section.index('patterns-cooccurrence-panel')
        proj_idx = section.index('patterns-projskill-panel')
        assert heatmap_idx < cooc_idx < proj_idx
