"""tests/test_dashboard_sessions_ui.py — Issue #103 Sessions ページ UI 構造 + 振る舞い。

Sessions ページ (5 番目のタブ / hash route `#/sessions`) の以下を pin する:

1. Template 構造:
   - nav 5 タブ化 (Overview / Patterns / Quality / Surface / Sessions)
   - `<section data-page="sessions">` の存在 + 主要 DOM ID (`sessionsKpi` / `sessionsTable` /
     `sessionsSub`) + colgroup 12 列 + thead 2 行 (group-row + data-row)
   - help-pop: `hp-sessions` / `hp-tokens` / `hp-cost` / `hp-tier`
   - JS / CSS の concat 順 (`45_renderers_sessions.js` / `55_sessions.css`)
   - `00_router.js` の HASH_TO_PAGE 拡張
   - `20_load_and_render.js` から `renderSessions(data)` を呼び出している

2. Renderer 関数の構造 pin (45_renderers_sessions.js):
   - `function renderSessions(...)` 定義
   - `window.__sessions = { ... }` expose
   - page-scoped early-out (`activePage !== 'sessions'`)

3. Behavior round-trip (Node 経由):
   - `formatCostUsd($X.XXXX 4 桁)` / `fmtTokens (M / k notation)` / `inferModelFamily`
   - `buildModelChips` / `buildTierChips`
   - `buildSessionRow` (active session に live-pill / whale row class)
   - `computeKpi` (合計 / 中央値 / 平均 / cache 効率)

DOM 描画系 (innerHTML への注入) の最終確認は visual smoke (chrome-devtools MCP) に委譲し、
本テストでは生成 HTML 文字列の構造のみを検証する。
"""
# pylint: disable=line-too-long
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from _dashboard_template_loader import load_assembled_template

_TEMPLATE_DIR = Path(__file__).parent.parent / "dashboard" / "template"
_HELPERS_JS = _TEMPLATE_DIR / "scripts" / "10_helpers.js"
_RENDERERS_JS = _TEMPLATE_DIR / "scripts" / "45_renderers_sessions.js"
_LOAD_RENDER_JS = _TEMPLATE_DIR / "scripts" / "20_load_and_render.js"
_ROUTER_JS = _TEMPLATE_DIR / "scripts" / "00_router.js"
_SESSIONS_CSS = _TEMPLATE_DIR / "styles" / "55_sessions.css"


def _load_template() -> str:
    return load_assembled_template()


def _extract_section(template: str, page: str) -> str:
    match = re.search(rf'<section\b[^>]*data-page="{re.escape(page)}"[^>]*>', template)
    assert match is not None, f"<section data-page={page!r}> not found"
    section_open = match.start()
    end = template.index('</section>', match.end())
    return template[section_open:end + len('</section>')]


# ============================================================
#  TestSessionsPageTemplate — DOM 構造の structural pin
# ============================================================
class TestSessionsPageTemplate:
    def test_nav_has_sessions_link(self):
        """nav に Sessions リンク (#/sessions) が追加されている。"""
        template = _load_template()
        assert 'href="#/sessions"' in template, "nav に href=#/sessions が無い"
        assert 'data-page-link="sessions"' in template, "data-page-link=sessions が無い"

    def test_nav_has_five_links_in_order(self):
        """nav に 5 タブ (Overview / Patterns / Quality / Surface / Sessions) が
        この順番で並ぶ (Sessions は最後 = period toggle の前)。"""
        template = _load_template()
        positions = []
        for path in ['#/', '#/patterns', '#/quality', '#/surface', '#/sessions']:
            idx = template.index(f'href="{path}"')
            positions.append((idx, path))
        assert positions == sorted(positions), \
            f"nav 順序が overview→patterns→quality→surface→sessions でない: {positions}"

    def test_template_has_sessions_section(self):
        """<section data-page="sessions"> が存在する。"""
        template = _load_template()
        assert 'data-page="sessions"' in template, "section data-page=sessions が無い"

    def test_sessions_section_contains_main_dom_ids(self):
        """Sessions section に主要 DOM ID (sessionsKpi / sessionsTable / sessionsSub) がある。"""
        section = _extract_section(_load_template(), 'sessions')
        for el_id in ['sessionsKpi', 'sessionsTable', 'sessionsSub']:
            assert f'id="{el_id}"' in section, f"id={el_id} が Sessions section に無い"

    def test_sessions_table_has_colgroup_with_twelve_cols(self):
        """sessionsTable の colgroup に 12 col 定義が並ぶ (cost / project / tier 等)。"""
        section = _extract_section(_load_template(), 'sessions')
        idx = section.index('id="sessionsTable"')
        cg_start = section.index('<colgroup>', idx)
        cg_end = section.index('</colgroup>', cg_start)
        colgroup = section[cg_start:cg_end]
        # 12 個の <col> 要素 (<colgroup と区別するため space 付で count)
        col_count = len(re.findall(r'<col\b(?!group)', colgroup))
        assert col_count == 12, \
            f"sessionsTable colgroup に <col> が 12 個無い (got {col_count})"
        # 主要 col クラス
        for cls in ['col-start', 'col-dur', 'col-project', 'col-models',
                    'col-tok-in', 'col-tok-out', 'col-tok-cr', 'col-tok-cc',
                    'col-cost', 'col-tier', 'col-skills', 'col-sub']:
            assert f'col-{cls.split("-",1)[1]}"' in colgroup or f'col-{cls.split("-",1)[1]}' in colgroup, \
                f"col class {cls} が colgroup に無い"

    def test_sessions_table_thead_has_group_row_and_data_row(self):
        """thead に group-row (Tokens super-group) + data-row が両方ある。"""
        section = _extract_section(_load_template(), 'sessions')
        idx = section.index('id="sessionsTable"')
        thead_start = section.index('<thead>', idx)
        thead_end = section.index('</thead>', thead_start)
        thead = section[thead_start:thead_end]
        assert 'class="group-row"' in thead, "thead に class=group-row が無い"
        assert 'class="data-row"' in thead, "thead に class=data-row が無い"
        # group-row が data-row より先に現れる
        assert thead.index('class="group-row"') < thead.index('class="data-row"'), \
            "group-row は data-row より先に現れる必要がある"

    def test_sessions_table_data_row_columns(self):
        """data-row に必要列 (Project / Models / Input / Output / 推計コスト / Service tier / Skills / Subagents) がある。"""
        section = _extract_section(_load_template(), 'sessions')
        idx = section.index('class="data-row"')
        end = section.index('</tr>', idx)
        data_row = section[idx:end]
        for col in ['開始時刻', '期間', 'プロジェクト', 'Models', 'Input', 'Output',
                    'Cache R', 'Cache C', '推計コスト', 'Service tier', 'Skills', 'Subagents']:
            assert col in data_row, f"data-row 列見出し '{col}' が無い"

    def test_sessions_help_popups_present(self):
        """4 つの help-pop (hp-sessions / hp-tokens / hp-cost / hp-tier) が template に存在。"""
        template = _load_template()
        for hid in ['hp-sessions', 'hp-tokens', 'hp-cost', 'hp-tier']:
            assert f'id="{hid}"' in template, f"help-pop id={hid} が無い"

    def test_session_id_column_not_present(self):
        """Issue #103 v1 mock 決定: Session ID 列は廃止 (project + 時刻で実質一意)。"""
        section = _extract_section(_load_template(), 'sessions')
        idx = section.index('id="sessionsTable"')
        thead_start = section.index('<thead>', idx)
        thead_end = section.index('</thead>', thead_start)
        thead = section[thead_start:thead_end]
        assert 'Session ID' not in thead, \
            "Session ID 列見出しが残っている (mock 決定では廃止)"

    def test_router_hash_to_page_includes_sessions(self):
        """00_router.js の HASH_TO_PAGE に '#/sessions': 'sessions' がある。"""
        body = _ROUTER_JS.read_text(encoding='utf-8')
        assert "'#/sessions': 'sessions'" in body, \
            "HASH_TO_PAGE に '#/sessions': 'sessions' が無い"

    def test_concat_includes_renderers_sessions_js(self):
        """`_MAIN_JS_FILES` の concat 結果に 45_renderers_sessions.js の内容が含まれる。"""
        template = _load_template()
        assert 'function renderSessions' in template, \
            "_HTML_TEMPLATE に renderSessions の定義が無い (concat 漏れ?)"

    def test_concat_includes_sessions_css(self):
        """`_CSS_FILES` の concat 結果に 55_sessions.css の内容が含まれる。"""
        template = _load_template()
        # sessions-table grammar の鍵スタイル
        assert '.sessions-table' in template, \
            "_HTML_TEMPLATE に .sessions-table CSS が無い (concat 漏れ?)"
        assert '.cost-cell' in template, ".cost-cell CSS が無い"
        assert '.live-pill' in template, ".live-pill CSS が無い"
        assert '.model-chip' in template, ".model-chip CSS が無い"
        assert '.tier-chip' in template, ".tier-chip CSS が無い"

    def test_load_and_render_invokes_render_sessions(self):
        """20_load_and_render.js から renderSessions(data) が呼び出される。"""
        body = _LOAD_RENDER_JS.read_text(encoding='utf-8')
        # window.__sessions?.renderSessions?.(data) 経由 or 直接
        assert ('renderSessions(data)' in body) or \
               ('window.__sessions' in body and 'renderSessions' in body), \
            "20_load_and_render.js から renderSessions(data) 呼び出しが無い"


class TestSessionsRendererStructure:
    def test_renderers_sessions_js_exists(self):
        """45_renderers_sessions.js ファイルが存在する。"""
        assert _RENDERERS_JS.is_file(), \
            f"{_RENDERERS_JS} が存在しない"

    def test_render_sessions_function_defined(self):
        """`function renderSessions(...)` が定義されている。"""
        body = _RENDERERS_JS.read_text(encoding='utf-8')
        assert re.search(r"\bfunction\s+renderSessions\s*\(", body), \
            "function renderSessions(...) の定義が無い"

    def test_window_sessions_exposed(self):
        """`window.__sessions` で renderSessions 等が expose される。"""
        body = _RENDERERS_JS.read_text(encoding='utf-8')
        assert 'window.__sessions' in body, \
            "window.__sessions による expose が無い"
        assert 'renderSessions' in body, \
            "window.__sessions に renderSessions が含まれない"

    def test_render_sessions_page_scoped_early_out(self):
        """renderSessions は body[data-active-page="sessions"] 以外で early-out。"""
        body = _RENDERERS_JS.read_text(encoding='utf-8')
        # 関数本体の最初 500 文字以内に early-out 条件がある
        match = re.search(
            r"function\s+renderSessions\s*\([^)]*\)\s*\{",
            body,
        )
        assert match is not None, "renderSessions 関数本体が見つからない"
        head = body[match.end():match.end() + 500]
        assert "activePage !== 'sessions'" in head or 'activePage !== "sessions"' in head, \
            "renderSessions の page-scoped early-out (activePage !== 'sessions') が無い"


# ============================================================
#  TestSessionsRendererBehavior — Node 経由の振る舞い round-trip
# ============================================================
_NODE = shutil.which("node")


@unittest.skipUnless(_NODE, "node not installed; skipping behavior round-trip")
class TestSessionsRendererBehavior(unittest.TestCase):
    """`window.__sessions` に expose した pure helpers を Node で eval して検証する。

    DOM を伴う `renderSessions` は visual smoke (chrome-devtools MCP) で確認するため
    本テストは pure 関数 (formatCostUsd / buildModelChips / buildTierChips /
    buildSessionRow / computeKpi / inferModelFamily) のみを対象にする。
    """

    @staticmethod
    def _run_node(call_expr: str) -> object:
        """helpers.js + 45_renderers_sessions.js を読み込み、call_expr を JSON で返す。

        45_renderers_sessions.js は IIFE wrap されているため、最後で
        `window.__sessions` に expose したヘルパを Node で `globalThis.__sessions` 経由
        で呼び出す。`window` shim は eval 前に渡す。
        """
        helpers_src = _HELPERS_JS.read_text(encoding='utf-8')
        renderers_src = _RENDERERS_JS.read_text(encoding='utf-8')
        # `document` / `window` shim: renderSessions が触るが、本テストは pure helpers のみ呼ぶ。
        # `window` を globalThis に alias して __sessions が globalThis 経由で取れるようにする。
        shim = (
            "const __doc_dataset = { activePage: '__none__' };\n"
            "globalThis.document = { body: { dataset: __doc_dataset }, "
            "querySelector: () => null, getElementById: () => null };\n"
            "globalThis.window = globalThis;\n"
        )
        script = (
            shim
            + helpers_src
            + "\n" + renderers_src
            + "\nconst __out = " + call_expr + ";\n"
            + "process.stdout.write(JSON.stringify(__out));\n"
        )
        env = os.environ.copy()
        proc = subprocess.run(
            [_NODE, "-e", script],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"node failed (returncode={proc.returncode}): stderr={proc.stderr}"
            )
        return json.loads(proc.stdout)

    # ---------- formatCostUsd ----------
    def test_format_cost_usd_four_decimal_places(self):
        out = self._run_node("window.__sessions.formatCostUsd(0.001)")
        self.assertEqual(out, "$0.0010")

    def test_format_cost_usd_zero(self):
        out = self._run_node("window.__sessions.formatCostUsd(0)")
        self.assertEqual(out, "$0.0000")

    def test_format_cost_usd_large(self):
        out = self._run_node("window.__sessions.formatCostUsd(12.4831)")
        self.assertEqual(out, "$12.4831")

    def test_format_cost_usd_handles_non_finite(self):
        out = self._run_node("window.__sessions.formatCostUsd(NaN)")
        self.assertEqual(out, "$0.0000")

    # ---------- fmtTokens ----------
    def test_fmt_tokens_under_1k(self):
        out = self._run_node("window.__sessions.fmtTokens(123)")
        self.assertEqual(out, "123")

    def test_fmt_tokens_kilo(self):
        out = self._run_node("window.__sessions.fmtTokens(1234)")
        self.assertEqual(out, "1.2k")

    def test_fmt_tokens_mega(self):
        out = self._run_node("window.__sessions.fmtTokens(2_100_000)")
        self.assertEqual(out, "2.1M")

    def test_fmt_tokens_zero(self):
        out = self._run_node("window.__sessions.fmtTokens(0)")
        self.assertEqual(out, "0")

    # ---------- inferModelFamily ----------
    def test_infer_model_family_opus(self):
        out = self._run_node("window.__sessions.inferModelFamily('claude-opus-4-7')")
        self.assertEqual(out, "opus")

    def test_infer_model_family_sonnet(self):
        out = self._run_node("window.__sessions.inferModelFamily('claude-sonnet-4-6')")
        self.assertEqual(out, "sonnet")

    def test_infer_model_family_haiku(self):
        out = self._run_node("window.__sessions.inferModelFamily('claude-haiku-4-5-20251001')")
        self.assertEqual(out, "haiku")

    def test_infer_model_family_unknown_falls_to_sonnet(self):
        """未知 model 名は cost_metrics.py と整合させて sonnet fallback。"""
        out = self._run_node("window.__sessions.inferModelFamily('claude-future-99')")
        self.assertEqual(out, "sonnet")

    # ---------- buildModelChips ----------
    def test_build_model_chips_single(self):
        out = self._run_node(
            "window.__sessions.buildModelChips({'claude-opus-4-7': 3})"
        )
        self.assertIn('class="model-chips"', out)
        self.assertIn('class="model-chip m-opus"', out)
        self.assertIn('opus', out)
        self.assertIn('class="ct">3<', out)

    def test_build_model_chips_mixed(self):
        out = self._run_node(
            "window.__sessions.buildModelChips({'claude-opus-4-7': 8, 'claude-sonnet-4-6': 2})"
        )
        self.assertIn('m-opus', out)
        self.assertIn('m-sonnet', out)

    def test_build_model_chips_empty(self):
        out = self._run_node("window.__sessions.buildModelChips({})")
        self.assertIn('—', out)

    # ---------- buildTierChips ----------
    def test_build_tier_chips_priority(self):
        out = self._run_node(
            "window.__sessions.buildTierChips({priority: 10})"
        )
        self.assertIn('class="tier-chips"', out)
        self.assertIn('class="tier-chip t-priority"', out)
        self.assertIn('priority', out)
        self.assertIn('class="ct">10<', out)

    def test_build_tier_chips_mixed(self):
        out = self._run_node(
            "window.__sessions.buildTierChips({priority: 3, standard: 7})"
        )
        self.assertIn('t-priority', out)
        self.assertIn('t-standard', out)

    def test_build_tier_chips_empty(self):
        out = self._run_node("window.__sessions.buildTierChips({})")
        self.assertIn('—', out)

    # ---------- buildSessionRow ----------
    def test_build_session_row_active_has_live_pill(self):
        """active session (ended_at: null) は live-pill を含む。"""
        session_js = (
            "{session_id:'s1', project:'foo', "
            "started_at:'2026-05-06T09:14:00+00:00', ended_at:null, "
            "duration_seconds:null, "
            "models:{'claude-sonnet-4-6':2}, "
            "tokens:{input:1000,output:500,cache_read:0,cache_creation:0}, "
            "estimated_cost_usd:0.0234, "
            "service_tier_breakdown:{standard:2}, "
            "skill_count:1, subagent_count:0}"
        )
        out = self._run_node(
            f"window.__sessions.buildSessionRow({session_js}, 1.0)"
        )
        self.assertIn('class="live-pill"', out)
        self.assertIn('進行中', out)
        self.assertIn('is-active', out)

    def test_build_session_row_completed_has_duration(self):
        """完了 session (ended_at あり) は live-pill ではなく duration を表示。"""
        session_js = (
            "{session_id:'s1', project:'bar', "
            "started_at:'2026-05-05T20:00:00+00:00', "
            "ended_at:'2026-05-06T00:47:00+00:00', "
            "duration_seconds:17220, "
            "models:{'claude-opus-4-7':10}, "
            "tokens:{input:1000,output:500,cache_read:0,cache_creation:0}, "
            "estimated_cost_usd:5.8210, "
            "service_tier_breakdown:{priority:10}, "
            "skill_count:14, subagent_count:8}"
        )
        out = self._run_node(
            f"window.__sessions.buildSessionRow({session_js}, 5.8210)"
        )
        self.assertNotIn('live-pill', out)
        # duration_seconds=17220 = 4h 47m
        self.assertIn('4h 47m', out)
        self.assertNotIn('is-active', out)

    def test_build_session_row_whale_class(self):
        """cost が maxCost と一致する row には is-whale が付く。"""
        session_js = (
            "{session_id:'s1', project:'foo', "
            "started_at:'2026-05-05T20:00:00+00:00', "
            "ended_at:'2026-05-06T00:00:00+00:00', "
            "duration_seconds:14400, "
            "models:{'claude-opus-4-7':10}, "
            "tokens:{input:1000,output:500,cache_read:0,cache_creation:0}, "
            "estimated_cost_usd:5.0, "
            "service_tier_breakdown:{priority:10}, "
            "skill_count:14, subagent_count:8}"
        )
        out = self._run_node(
            f"window.__sessions.buildSessionRow({session_js}, 5.0)"
        )
        self.assertIn('is-whale', out)

    def test_build_session_row_cost_format(self):
        """cost セルが $X.XXXX (4 桁) で出力される。"""
        session_js = (
            "{session_id:'s1', project:'foo', "
            "started_at:'2026-05-05T20:00:00+00:00', "
            "ended_at:'2026-05-06T00:00:00+00:00', "
            "duration_seconds:14400, "
            "models:{'claude-sonnet-4-6':1}, "
            "tokens:{input:1000,output:500,cache_read:0,cache_creation:0}, "
            "estimated_cost_usd:0.1842, "
            "service_tier_breakdown:{standard:1}, "
            "skill_count:0, subagent_count:0}"
        )
        out = self._run_node(
            f"window.__sessions.buildSessionRow({session_js}, 5.0)"
        )
        self.assertIn('$0.1842', out)
        self.assertIn('class="cost-cell"', out)
        self.assertIn('--cost-pct:', out)

    # ---------- computeKpi ----------
    def test_compute_kpi_three_sessions(self):
        sessions_js = (
            "[{estimated_cost_usd:1.0, tokens:{input:1000,output:0,cache_read:9000,cache_creation:0}},"
            "{estimated_cost_usd:2.0, tokens:{input:2000,output:0,cache_read:0,cache_creation:0}},"
            "{estimated_cost_usd:3.0, tokens:{input:0,output:0,cache_read:0,cache_creation:0}}]"
        )
        out = self._run_node(f"window.__sessions.computeKpi({sessions_js})")
        # 合計 1+2+3 = 6
        self.assertAlmostEqual(out['totalCost'], 6.0, places=4)
        # 中央値 (3 件 → index 1) = 2
        self.assertAlmostEqual(out['medianCost'], 2.0, places=4)
        # 平均 = 2.0
        self.assertAlmostEqual(out['avgCost'], 2.0, places=4)
        # cache 効率 = 9000 / (1000 + 2000 + 0 + 9000) = 0.75
        self.assertAlmostEqual(out['cacheEfficiency'], 0.75, places=4)

    def test_compute_kpi_empty(self):
        out = self._run_node("window.__sessions.computeKpi([])")
        self.assertEqual(out['totalCost'], 0)
        self.assertEqual(out['medianCost'], 0)
        self.assertEqual(out['avgCost'], 0)
        self.assertEqual(out['cacheEfficiency'], 0)

    def test_compute_kpi_even_count_median_is_average_of_middle_two(self):
        """偶数件 (TOP_N_SESSIONS = 20 の常用ケース) では sorted[n/2-1] と sorted[n/2] の平均。

        codex Round 1 / P2 指摘: median 計算が偶数件のとき上位中央 1 値しか返さず、
        中央 2 値の平均にならない (= 系統的に高めに偏る)。
        """
        # 4 件 → sorted = [1,2,3,4] → median = (2+3)/2 = 2.5
        sessions_js = (
            "[{estimated_cost_usd:1.0, tokens:{input:0,output:0,cache_read:0,cache_creation:0}},"
            "{estimated_cost_usd:2.0, tokens:{input:0,output:0,cache_read:0,cache_creation:0}},"
            "{estimated_cost_usd:3.0, tokens:{input:0,output:0,cache_read:0,cache_creation:0}},"
            "{estimated_cost_usd:4.0, tokens:{input:0,output:0,cache_read:0,cache_creation:0}}]"
        )
        out = self._run_node(f"window.__sessions.computeKpi({sessions_js})")
        self.assertAlmostEqual(out['medianCost'], 2.5, places=4)

    def test_compute_kpi_top_cost_tracks_max(self):
        sessions_js = (
            "[{estimated_cost_usd:1.5, tokens:{input:0,output:0,cache_read:0,cache_creation:0}},"
            "{estimated_cost_usd:0.2, tokens:{input:0,output:0,cache_read:0,cache_creation:0}}]"
        )
        out = self._run_node(f"window.__sessions.computeKpi({sessions_js})")
        self.assertAlmostEqual(out['topCost'], 1.5, places=4)

    def test_compute_kpi_opus_share_attributes_session_to_opus(self):
        """opus が含まれる session は cost 全額が opusCost に寄与する (按分しない)。

        mock の決定: 1 枚目 KPI sub「うち opus セッション $X.XX (Y%)」は
        「opus が使われた session」単位の合計を表示する仕様 (model 内訳の按分は UX 上不要)。
        """
        # session A (opus 使用) cost=2.0、session B (sonnet のみ) cost=8.0
        # → opusCost = 2.0、totalCost = 10.0、opusShare = 0.2
        sessions_js = (
            "[{estimated_cost_usd:2.0, models:{'claude-opus-4-7':3, 'claude-sonnet-4-6':1}, "
            "tokens:{input:0,output:0,cache_read:0,cache_creation:0}},"
            "{estimated_cost_usd:8.0, models:{'claude-sonnet-4-6':5}, "
            "tokens:{input:0,output:0,cache_read:0,cache_creation:0}}]"
        )
        out = self._run_node(f"window.__sessions.computeKpi({sessions_js})")
        self.assertAlmostEqual(out['opusCost'], 2.0, places=4)
        self.assertAlmostEqual(out['opusShare'], 0.2, places=4)

    def test_compute_kpi_median_multiple_and_top_cost_share(self):
        """3 枚目 KPI sub「中央値の N× · 上位 1 件で M% 寄与」用メトリクス。

        avg / median = medianMultiple、topCost / totalCost = topCostShare。
        whale 偏りの可視化に使う (= mean が median から大きく乖離している = 上位寄り)。
        """
        # 4 件 (sorted=[1,2,3,4]): total=10, median=2.5, avg=2.5, topCost=4
        # → medianMultiple = 2.5/2.5 = 1.0、topCostShare = 4/10 = 0.4
        sessions_js = (
            "[{estimated_cost_usd:1.0, tokens:{input:0,output:0,cache_read:0,cache_creation:0}},"
            "{estimated_cost_usd:2.0, tokens:{input:0,output:0,cache_read:0,cache_creation:0}},"
            "{estimated_cost_usd:3.0, tokens:{input:0,output:0,cache_read:0,cache_creation:0}},"
            "{estimated_cost_usd:4.0, tokens:{input:0,output:0,cache_read:0,cache_creation:0}}]"
        )
        out = self._run_node(f"window.__sessions.computeKpi({sessions_js})")
        self.assertAlmostEqual(out['medianMultiple'], 1.0, places=4)
        self.assertAlmostEqual(out['topCostShare'], 0.4, places=4)
