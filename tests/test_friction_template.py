"""tests/test_friction_template.py — Issue #61 friction signals (A2 + A3)
template 構造テスト。

dashboard/template.html の `<section data-page="quality">` に
A2 (permission/skill 紐付け 2 panel) と A3 (compact density 1 panel) の合計
3 panel が並び、対応する renderer 関数 / CSS / tooltip 分岐が入ったかを
文字列レベルで検証する (`tests/test_quality_template.py` と同型)。
"""
# pylint: disable=line-too-long
import re

from _dashboard_template_loader import load_assembled_template


def _load_template() -> str:
    return load_assembled_template()


def _extract_section(template: str, page: str) -> str:
    # `data-page="X"` は CSS の attribute selector にも現れるので、
    # 必ず `<section ...>` の開始タグから始まる本物の section だけを拾う。
    match = re.search(rf'<section\b[^>]*data-page="{re.escape(page)}"[^>]*>', template)
    assert match is not None, f"<section data-page={page!r}> not found"
    section_open = match.start()
    end = template.index('</section>', match.end())
    return template[section_open:end + len('</section>')]


def _extract_function_body(template: str, fn_name: str) -> str:
    """`function <fn_name>` から次の `function ` までの body を返す (簡易抽出)."""
    body_start = template.index(f'function {fn_name}')
    body_end = template.index('function ', body_start + len(f'function {fn_name}'))
    return template[body_start:body_end]


# ============================================================
#  TestQualityPagePermissionPanels — A2 + A3 DOM / renderer 構造
# ============================================================

class TestQualityPagePermissionPanels:
    def test_quality_section_has_perm_skill_panel(self):
        section = _extract_section(_load_template(), 'quality')
        for el_id in [
            'quality-perm-skill-panel',
            'quality-perm-skill',
            'quality-perm-skill-sub',
        ]:
            assert f'id="{el_id}"' in section, f"id={el_id} missing from Quality section"

    def test_quality_section_has_perm_subagent_panel(self):
        section = _extract_section(_load_template(), 'quality')
        for el_id in [
            'quality-perm-subagent-panel',
            'quality-perm-subagent',
            'quality-perm-subagent-sub',
        ]:
            assert f'id="{el_id}"' in section, f"id={el_id} missing from Quality section"

    def test_quality_section_has_compact_panel(self):
        section = _extract_section(_load_template(), 'quality')
        for el_id in [
            'quality-compact-panel',
            'quality-compact-hist',
            'quality-compact-worst',
            'quality-compact-sub',
        ]:
            assert f'id="{el_id}"' in section, f"id={el_id} missing from Quality section"

    def test_perm_skill_table_has_thead_columns(self):
        section = _extract_section(_load_template(), 'quality')
        # perm-skill table の thead 部分から列順を抽出
        # Issue #89: Prompts / Invocations / Rate は一般語日本語化 (列順契約は維持)
        idx = section.index('id="quality-perm-skill"')
        thead_start = section.index('<thead>', idx)
        thead_end = section.index('</thead>', thead_start)
        thead = section[thead_start:thead_end]
        positions = []
        for col in ['Skill', 'プロンプト数', '呼び出し回数', '比率']:
            i = thead.find(col)
            assert i >= 0, f"perm-skill thead missing column: {col}"
            positions.append((i, col))
        assert positions == sorted(positions), \
            f"perm-skill thead 列順が想定と違う: {[c for _, c in positions]}"

    def test_perm_subagent_table_has_thead_columns(self):
        section = _extract_section(_load_template(), 'quality')
        # Issue #89: Prompts / Invocations / Rate は一般語日本語化 (列順契約は維持)
        idx = section.index('id="quality-perm-subagent"')
        thead_start = section.index('<thead>', idx)
        thead_end = section.index('</thead>', thead_start)
        thead = section[thead_start:thead_end]
        positions = []
        for col in ['Subagent', 'プロンプト数', '呼び出し回数', '比率']:
            i = thead.find(col)
            assert i >= 0, f"perm-subagent thead missing column: {col}"
            positions.append((i, col))
        assert positions == sorted(positions), \
            f"perm-subagent thead 列順が想定と違う: {[c for _, c in positions]}"

    def test_compact_grid_has_hist_and_worst_table(self):
        section = _extract_section(_load_template(), 'quality')
        idx = section.index('id="quality-compact-panel"')
        # panel 内に compact-grid + hist + worst-table が並ぶ
        panel_chunk = section[idx:idx + 2000]
        assert 'compact-grid' in panel_chunk
        assert 'id="quality-compact-hist"' in panel_chunk
        assert 'id="quality-compact-worst"' in panel_chunk

    def test_template_has_permission_skill_renderer(self):
        template = _load_template()
        assert 'function renderPermissionSkillBreakdown' in template, \
            "renderPermissionSkillBreakdown function missing"

    def test_template_has_permission_subagent_renderer(self):
        template = _load_template()
        assert 'function renderPermissionSubagentBreakdown' in template, \
            "renderPermissionSubagentBreakdown function missing"

    def test_template_has_compact_density_renderer(self):
        template = _load_template()
        assert 'function renderCompactDensity' in template, \
            "renderCompactDensity function missing"

    def test_loadAndRender_invokes_friction_renderers(self):
        template = _load_template()
        assert 'renderPermissionSkillBreakdown(data.permission_prompt_skill_breakdown)' in template, \
            "renderPermissionSkillBreakdown(data.permission_prompt_skill_breakdown) call missing"
        assert 'renderPermissionSubagentBreakdown(data.permission_prompt_subagent_breakdown)' in template, \
            "renderPermissionSubagentBreakdown(data.permission_prompt_subagent_breakdown) call missing"
        assert 'renderCompactDensity(data.compact_density)' in template, \
            "renderCompactDensity(data.compact_density) call missing"

    def test_perm_skill_renderer_has_page_scoped_early_out(self):
        template = _load_template()
        body = _extract_function_body(template, 'renderPermissionSkillBreakdown')
        assert "activePage !== 'quality'" in body[:500], \
            "renderPermissionSkillBreakdown missing page-scoped early-out"

    def test_perm_subagent_renderer_has_page_scoped_early_out(self):
        template = _load_template()
        body = _extract_function_body(template, 'renderPermissionSubagentBreakdown')
        assert "activePage !== 'quality'" in body[:500], \
            "renderPermissionSubagentBreakdown missing page-scoped early-out"

    def test_compact_density_renderer_has_page_scoped_early_out(self):
        template = _load_template()
        body = _extract_function_body(template, 'renderCompactDensity')
        assert "activePage !== 'quality'" in body[:500], \
            "renderCompactDensity missing page-scoped early-out"

    def test_perm_skill_panel_uses_mint(self):
        section = _extract_section(_load_template(), 'quality')
        idx = section.index('id="quality-perm-skill-panel"')
        head_idx = section.index('panel-head', idx)
        head_chunk = section[head_idx:head_idx + 100]
        assert 'c-mint' in head_chunk, "perm-skill panel uses c-mint missing"

    def test_perm_subagent_panel_uses_coral(self):
        section = _extract_section(_load_template(), 'quality')
        idx = section.index('id="quality-perm-subagent-panel"')
        head_idx = section.index('panel-head', idx)
        head_chunk = section[head_idx:head_idx + 100]
        assert 'c-coral' in head_chunk, "perm-subagent panel uses c-coral missing"

    def test_compact_panel_uses_peach(self):
        section = _extract_section(_load_template(), 'quality')
        idx = section.index('id="quality-compact-panel"')
        head_idx = section.index('panel-head', idx)
        head_chunk = section[head_idx:head_idx + 100]
        assert 'c-peach' in head_chunk, "compact panel uses c-peach missing"

    def test_compact_hist_uses_svg(self):
        template = _load_template()
        body = _extract_function_body(template, 'renderCompactDensity')
        for tag in ['<svg ', '<rect class="bar"']:
            assert tag in body, f"renderCompactDensity に {tag!r} がない"

    def test_dtipbuild_has_perm_skill_branch(self):
        template = _load_template()
        assert 'data-tip="perm-skill"' in template, "data-tip=\"perm-skill\" missing"
        assert "kind === 'perm-skill'" in template, "dtipBuild kind === 'perm-skill' branch missing"

    def test_dtipbuild_has_perm_subagent_branch(self):
        template = _load_template()
        assert 'data-tip="perm-subagent"' in template, "data-tip=\"perm-subagent\" missing"
        assert "kind === 'perm-subagent'" in template, "dtipBuild kind === 'perm-subagent' branch missing"

    def test_dtipbuild_has_histogram_branch(self):
        template = _load_template()
        assert 'data-tip="histogram"' in template, "data-tip=\"histogram\" missing"
        assert "kind === 'histogram'" in template, "dtipBuild kind === 'histogram' branch missing"

    def test_dtipbuild_has_worst_session_branch(self):
        template = _load_template()
        assert 'data-tip="worst-session"' in template, "data-tip=\"worst-session\" missing"
        assert "kind === 'worst-session'" in template, "dtipBuild kind === 'worst-session' branch missing"

    def test_worst_session_unknown_project_shown_as_unknown_label(self):
        # P3 反映: project="" の worst_session を `(unknown)` literal で表示する分岐が
        # renderCompactDensity 内に存在することを pin。空セルだと「データ欠損」と
        # 「project が空文字」が見分けつかなくなる UX 問題を回避。
        template = _load_template()
        body = _extract_function_body(template, 'renderCompactDensity')
        assert '(unknown)' in body, \
            "renderCompactDensity 内に '(unknown)' literal が無い (空 project の判別不能を回避できていない)"
        assert "proj === ''" in body, \
            "renderCompactDensity 内に proj === '' 分岐が無い"
