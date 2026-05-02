"""tests/test_surface_template.py — Issue #74 Surface 3 panel template 構造テスト。

dashboard/template.html の `<section data-page="surface">` が下記 3 panel を持ち、
対応する renderer 関数 + CSS + scope-note callout (Panel 3) + 旧 ID の不在
(regression guard) を満たすことを文字列レベルで検証する。

3 panel の DOM ID:
- Panel 1 (Skill 起動経路): #surface-inv-panel / #surface-inv (table)
- Panel 2 (Skill lifecycle): #surface-life-panel / #surface-life (table)
- Panel 3 (Hibernating skills): #surface-hib-panel / #surface-hib (table)
                                 + .scope-note callout + #surface-hib-active-note
"""
# pylint: disable=line-too-long
import re

from _dashboard_template_loader import load_assembled_template


def _load_template() -> str:
    return load_assembled_template()


def _extract_section(template: str, page: str) -> str:
    match = re.search(rf'<section\b[^>]*data-page="{re.escape(page)}"[^>]*>', template)
    assert match is not None, f"<section data-page={page!r}> not found"
    section_open = match.start()
    end = template.index('</section>', match.end())
    return template[section_open:end + len('</section>')]


def _extract_function_body(template: str, fn_name: str) -> str:
    body_start = template.index(f'function {fn_name}')
    body_end = template.index('function ', body_start + len(f'function {fn_name}'))
    return template[body_start:body_end]


# ============================================================
#  TestSurfacePagePanels — 3 panel DOM / renderer 構造
# ============================================================
class TestSurfacePagePanels:
    def test_surface_section_no_longer_placeholder(self):
        section = _extract_section(_load_template(), 'surface')
        assert 'page-placeholder' not in section, \
            "surface section should no longer be a placeholder"

    # ---- Panel 1: Skill 起動経路 ----
    def test_invocation_panel_present(self):
        section = _extract_section(_load_template(), 'surface')
        for el_id in ['surface-inv-panel', 'surface-inv', 'surface-inv-sub']:
            assert f'id="{el_id}"' in section, f"id={el_id} missing from Surface section"

    def test_invocation_table_columns(self):
        section = _extract_section(_load_template(), 'surface')
        idx = section.index('id="surface-inv"')
        thead_start = section.index('<thead>', idx)
        thead_end = section.index('</thead>', thead_start)
        thead = section[thead_start:thead_end]
        # 列順: Skill / 起動モード / LLM / ユーザー / LLM率
        # (LLM / ユーザー は MODE_LABEL の値 dual / llm-only / user-only と整合)
        # 「自律率」は「自殺率」と誤読されうるため UI は「LLM率」に揃える (API field 名は autonomy_rate のまま)
        # Issue #89: Mode → 起動モード、User → ユーザー (一般語日本語化)
        positions = []
        for col in ['Skill', '起動モード', 'LLM', 'ユーザー']:
            i = thead.find(col)
            assert i >= 0, f"surface-inv thead missing column: {col}"
            positions.append((i, col))
        assert positions == sorted(positions), \
            f"surface-inv columns not in expected order: {positions}"
        # LLM率 / Autonomy のいずれか
        assert ('LLM率' in thead) or ('Autonomy' in thead), \
            "surface-inv thead missing autonomy_rate column header"

    def test_invocation_renderer_exists(self):
        template = _load_template()
        assert 'function renderSkillInvocationBreakdown' in template, \
            "renderSkillInvocationBreakdown function missing"

    def test_invocation_renderer_page_scoped_early_out(self):
        body = _extract_function_body(_load_template(), 'renderSkillInvocationBreakdown')
        assert "activePage !== 'surface'" in body[:500], \
            "renderSkillInvocationBreakdown missing page-scoped early-out"

    # ---- Panel 2: Skill lifecycle ----
    def test_lifecycle_panel_present(self):
        section = _extract_section(_load_template(), 'surface')
        for el_id in ['surface-life-panel', 'surface-life', 'surface-life-sub']:
            assert f'id="{el_id}"' in section, f"id={el_id} missing from Surface section"

    def test_lifecycle_table_columns(self):
        section = _extract_section(_load_template(), 'surface')
        idx = section.index('id="surface-life"')
        thead_start = section.index('<thead>', idx)
        thead_end = section.index('</thead>', thead_start)
        thead = section[thead_start:thead_end]
        positions = []
        for col in ['Skill', '初回', '直近']:
            i = thead.find(col)
            assert i >= 0, f"surface-life thead missing column: {col}"
            positions.append((i, col))
        assert positions == sorted(positions), \
            f"surface-life columns not in expected order: {positions}"
        # 30日件数 / 全期間 / トレンド の存在
        assert '30日' in thead, "surface-life thead missing 30日件数 column"
        assert '全期間' in thead, "surface-life thead missing 全期間 column"
        assert 'トレンド' in thead, "surface-life thead missing トレンド column"

    def test_lifecycle_renderer_exists(self):
        template = _load_template()
        assert 'function renderSkillLifecycle' in template, \
            "renderSkillLifecycle function missing"

    def test_lifecycle_renderer_page_scoped_early_out(self):
        body = _extract_function_body(_load_template(), 'renderSkillLifecycle')
        assert "activePage !== 'surface'" in body[:500], \
            "renderSkillLifecycle missing page-scoped early-out"

    # ---- Panel 3: Hibernating skills ----
    def test_hibernating_panel_present(self):
        section = _extract_section(_load_template(), 'surface')
        for el_id in ['surface-hib-panel', 'surface-hib', 'surface-hib-sub',
                      'surface-hib-active-note']:
            assert f'id="{el_id}"' in section, f"id={el_id} missing from Surface section"

    def test_hibernating_scope_note_visible(self):
        section = _extract_section(_load_template(), 'surface')
        # scope-note callout が panel-body 直上に常時可視で存在する
        assert 'class="scope-note"' in section, \
            "scope-note callout missing from Hibernating panel"
        # user-level only の文言 (日本語 or 英語のいずれか含む)
        assert ('user-level' in section.lower()) or ('User-level' in section), \
            "scope-note missing user-level only marker"

    def test_hibernating_table_columns(self):
        section = _extract_section(_load_template(), 'surface')
        idx = section.index('id="surface-hib"')
        thead_start = section.index('<thead>', idx)
        thead_end = section.index('</thead>', thead_start)
        thead = section[thead_start:thead_end]
        # Issue #89: mtime → 更新日時 (一般語日本語化)
        for col in ['Skill', '状態', '更新日時', '最終呼び出し', '経過']:
            assert col in thead, f"surface-hib thead missing column: {col}"

    def test_hibernating_renderer_exists(self):
        template = _load_template()
        assert 'function renderSkillHibernating' in template, \
            "renderSkillHibernating function missing"

    def test_hibernating_renderer_page_scoped_early_out(self):
        body = _extract_function_body(_load_template(), 'renderSkillHibernating')
        assert "activePage !== 'surface'" in body[:500], \
            "renderSkillHibernating missing page-scoped early-out"

    # ---- 統合 ----
    def test_loadAndRender_invokes_all_three_renderers(self):
        template = _load_template()
        assert 'renderSkillInvocationBreakdown(data.skill_invocation_breakdown)' in template, \
            "renderSkillInvocationBreakdown call missing in loadAndRender"
        assert 'renderSkillLifecycle(data.skill_lifecycle)' in template, \
            "renderSkillLifecycle call missing in loadAndRender"
        assert 'renderSkillHibernating(data.skill_hibernating)' in template, \
            "renderSkillHibernating call missing in loadAndRender"

    def test_help_popups_present(self):
        template = _load_template()
        for hid in ['hp-inv', 'hp-life', 'hp-hib']:
            assert f'id="{hid}"' in template, f"help-pop {hid} missing"

    def test_panel_dom_order_inv_life_hib(self):
        section = _extract_section(_load_template(), 'surface')
        i_inv = section.index('id="surface-inv-panel"')
        i_life = section.index('id="surface-life-panel"')
        i_hib = section.index('id="surface-hib-panel"')
        assert i_inv < i_life < i_hib, \
            f"Panel order should be inv -> life -> hib, got inv={i_inv}, life={i_life}, hib={i_hib}"

    # ---- regression guards: 旧 ID / 旧関数の不在 ----
    def test_old_ids_not_present(self):
        template = _load_template()
        for old_id in ['surface-source', 'surface-source-panel', 'surface-source-sub',
                       'surface-instr-panel', 'surface-instr-mt', 'surface-instr-lr',
                       'surface-instr-glob', 'surface-instr-sub']:
            assert f'id="{old_id}"' not in template, \
                f"old DOM id should be removed: {old_id}"

    def test_old_renderer_functions_not_present(self):
        template = _load_template()
        for old_fn in ['renderSlashCommandSourceBreakdown',
                       'renderInstructionsLoadedBreakdown',
                       'renderInstrBars', 'renderGlobTable']:
            assert f'function {old_fn}' not in template, \
                f"old renderer function should be removed: {old_fn}"

    def test_old_help_popups_not_present(self):
        template = _load_template()
        for old_hid in ['hp-source', 'hp-instr']:
            assert f'id="{old_hid}"' not in template, \
                f"old help-pop should be removed: {old_hid}"

    # ---- regression guards: dtipBuild が新 Surface 行の tooltip を出す ----
    def test_dtipBuild_handles_inv_life_hib_kinds(self):
        """Surface 3 panel の data-tip="inv|life|hib" 行が dtipBuild で扱われる。

        renderer 側は data-tip 属性を出すが dtipBuild に対応分岐が無いと
        hover/focus 時に tooltip が出ず regression になる。
        """
        template = _load_template()
        build_idx = template.index('function dtipBuild')
        # dtipBuild は `return null;\n  }` で閉じる単一関数。その範囲だけ切り出す。
        end_marker = 'return null;\n  }'
        body_end = template.index(end_marker, build_idx) + len(end_marker)
        body = template[build_idx:body_end]
        for kind in ('inv', 'life', 'hib'):
            assert f"kind === '{kind}'" in body, \
                f"dtipBuild に kind === '{kind}' の分岐が無い (Surface 行 tooltip regression)"

    # ---- Lifecycle 20 件 cap を反映した active note 文言 ----
    def test_hibernating_active_note_mentions_lifecycle_cap(self):
        """active_excluded_count の note が Lifecycle 20 件 cap を反映している。

        Lifecycle panel は top_n=20 で truncate されるので、
        active 除外件数全部が必ず Lifecycle で見えるとは限らない。
        誤誘導しないため文言で cap を示す。
        """
        template = _load_template()
        # active_excluded_count を表示する分岐 (activeText.textContent = ...) の周辺に
        # "20" と "Lifecycle" の両方があれば cap が示されている
        assert 'activeText.textContent' in template
        idx = template.index('activeText.textContent')
        snippet = template[idx:idx + 400]
        assert '20' in snippet, "active note should mention Lifecycle cap (20)"
        assert 'Lifecycle' in snippet, "active note should reference Lifecycle panel"
