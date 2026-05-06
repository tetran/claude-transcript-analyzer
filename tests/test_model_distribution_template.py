"""tests/test_model_distribution_template.py — Issue #106 / Overview モデル分布パネル

Phase 3 (DOM) / Phase 4 (Renderer JS, Node round-trip) / Phase 5 (CSS additive +
新ファイル禁止 invariant) を 1 ファイルにまとめる。
"""
# pylint: disable=line-too-long
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _dashboard_template_loader import load_assembled_template  # noqa: E402

_TEMPLATE_DIR = ROOT / "dashboard" / "template"


def _load_template() -> str:
    return load_assembled_template()


def _extract_section(template: str, page: str) -> str:
    """`<section data-page="<page>">` 〜 対応する `</section>` を返す。"""
    match = re.search(rf'<section\b[^>]*data-page="{re.escape(page)}"[^>]*>', template)
    assert match is not None, f"<section data-page={page!r}> not found"
    section_open = match.start()
    end = template.index('</section>', match.end())
    return template[section_open:end + len('</section>')]


def _read_script(name: str) -> str:
    return (_TEMPLATE_DIR / "scripts" / name).read_text(encoding="utf-8")


def _read_style(name: str) -> str:
    return (_TEMPLATE_DIR / "styles" / name).read_text(encoding="utf-8")


# =============================================================================
# Phase 3 — Template DOM
# =============================================================================


class TestModelDistPanelDOM(unittest.TestCase):
    """shell.html の overview セクションに panel が組み込まれていることを pin."""

    def setUp(self):
        self.template = _load_template()
        self.overview = _extract_section(self.template, "overview")

    def test_panel_exists_with_id(self):
        self.assertIn('id="model-dist-panel"', self.template)

    def test_panel_inside_overview_section(self):
        self.assertIn('id="model-dist-panel"', self.overview)

    def test_panel_after_project_distribution_panel(self):
        # AC「プロジェクト分布の直後」: 文字列 index で stack → model-dist の順
        idx_stack = self.overview.index('id="stack"')
        idx_model = self.overview.index('id="model-dist-panel"')
        self.assertLess(idx_stack, idx_model)

    def test_panel_head_uses_c_rose(self):
        # panel-head に c-rose class (= 新 token を Sessions/Overview の dot 系に追加)
        m = re.search(r'<div[^>]+class="[^"]*panel-head[^"]*c-rose[^"]*"', self.overview)
        self.assertIsNotNone(m, "panel-head with c-rose class not found inside #model-dist-panel")

    def test_panel_title_is_モデル分布(self):
        m = re.search(
            r'<div[^>]+id="model-dist-panel"[\s\S]*?<span class="ttl">[^<]*<span class="dot"></span>モデル分布',
            self.template,
        )
        self.assertIsNotNone(m, "<span class='ttl'>...モデル分布 not found inside #model-dist-panel")

    def test_help_pop_id_is_hp_model_dist(self):
        self.assertIn('id="hp-model-dist"', self.overview)

    def test_help_pop_body_contains_filter_terms(self):
        # 4-axis verification: help-pop body は集計ロジックの正本 verbatim を含む
        m = re.search(
            r'<span class="help-pop"[^>]+id="hp-model-dist"[^>]*>[\s\S]*?</span>\s*</span>',
            self.overview,
        )
        self.assertIsNotNone(m, "help-pop block for hp-model-dist not found")
        body = m.group(0)
        self.assertIn("assistant_usage", body)  # filter 条件
        self.assertIn("model", body)  # 集計 source field
        self.assertIn("family", body)  # rollup 軸
        self.assertIn("opus", body)
        self.assertIn("sonnet", body)
        self.assertIn("haiku", body)
        # plan §7 の判断: 「opus 5x」削除 — verbatim 数値は help-pop に書かない
        self.assertNotIn("5x", body)
        self.assertNotIn("5 倍", body)

    def test_panel_body_has_axis_pair_grid(self):
        self.assertIn("axis-pair", self.overview)

    def test_panel_body_has_two_axes(self):
        self.assertIn('data-axis="messages"', self.overview)
        self.assertIn('data-axis="cost"', self.overview)

    def test_each_axis_has_donut_svg(self):
        # 2 axis それぞれ <svg class="donut"> を持つ
        m = re.search(
            r'data-axis="messages"[\s\S]*?<svg[^>]*class="[^"]*\bdonut\b[^"]*"',
            self.overview,
        )
        self.assertIsNotNone(m, "donut svg under messages axis not found")
        m = re.search(
            r'data-axis="cost"[\s\S]*?<svg[^>]*class="[^"]*\bdonut\b[^"]*"',
            self.overview,
        )
        self.assertIsNotNone(m, "donut svg under cost axis not found")

    def test_each_axis_has_center_label(self):
        m = re.search(r'data-axis="messages"[\s\S]*?class="[^"]*\baxis-center\b', self.overview)
        self.assertIsNotNone(m, "axis-center under messages axis not found")
        m = re.search(r'data-axis="cost"[\s\S]*?class="[^"]*\baxis-center\b', self.overview)
        self.assertIsNotNone(m, "axis-center under cost axis not found")

    def test_panel_has_shared_legend(self):
        self.assertIn("model-legend", self.overview)

    def test_legend_header_uses_lowercase_msgs_cost(self):
        # plan §2 採用: lowercase mono header (Q4)
        m = re.search(
            r'class="[^"]*\bmodel-legend\b[^"]*"[\s\S]{0,1500}',
            self.overview,
        )
        self.assertIsNotNone(m)
        legend_block = m.group(0)
        self.assertIn("msgs", legend_block)
        self.assertIn("cost", legend_block)


# =============================================================================
# Phase 4 — Renderer JS (Node round-trip)
# =============================================================================


_NODE_TIMEOUT = 30


def _node_eval(js_body: str) -> str:
    """Node を spawn して js_body を実行し stdout を返す (cross-OS portability 規約準拠).

    `window` / `document` shim を入れて 20_load_and_render.js 末尾の IIFE が
    `window.__modelDist` を expose するように (= Sessions test と同じ pattern)。
    """
    shim = (
        "globalThis.document = { body: { dataset: { activePage: '__none__' } }, "
        "querySelector: () => null, querySelectorAll: () => [], "
        "getElementById: () => null };\n"
        "globalThis.window = globalThis;\n"
    )
    code = shim + _read_script("20_load_and_render.js") + "\n" + js_body
    # outer async IIFE wrap を simulate して loadAndRender 関数宣言の評価エラーを避ける。
    # 実 dashboard では shell.html の `(async function(){ __INCLUDE_MAIN_JS__ })();` 内で
    # 評価される (= 20_load_and_render.js は async IIFE body の一部で、未解決 ref は
    # 同 IIFE 内の他 file が定義する関数 / 変数)。本 test は IIFE 末尾の
    # `(function(){ ... window.__modelDist = {...}; })();` までだけ走らせれば足りる。
    proc = subprocess.run(
        ["node", "-e", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_NODE_TIMEOUT,
        check=False,
    )
    out = proc.stdout
    err = proc.stderr
    if proc.returncode != 0:
        raise AssertionError(f"node exit {proc.returncode}\nstdout: {out}\nstderr: {err}")
    return out


class TestModelDistRendererSource(unittest.TestCase):
    """ソース文字列 grep で Phase 4 の class hook / canonical order / 閾値を pin."""

    def setUp(self):
        self.src = _read_script("20_load_and_render.js")

    def test_render_model_distribution_function_defined(self):
        self.assertIn("function renderModelDistribution(", self.src)

    def test_window_modeldist_exposed(self):
        self.assertIn("window.__modelDist = {", self.src)

    def test_load_and_render_calls_render_model_distribution(self):
        # loadAndRender 関数本体内 (= __modelDist 経由 hook) からの dispatch
        self.assertRegex(
            self.src,
            r"window\.__modelDist[\s\S]{0,80}renderModelDistribution\(\s*data\s*\)",
        )

    def test_overview_page_scoped_early_out(self):
        self.assertRegex(
            self.src,
            r"dataset\.activePage\s*!==\s*['\"]overview['\"]",
        )

    def test_canonical_order_hardcoded(self):
        # plan §2 / §3: opus → sonnet → haiku を 3 軸同期
        self.assertRegex(
            self.src,
            r"\[\s*['\"]opus['\"]\s*,\s*['\"]sonnet['\"]\s*,\s*['\"]haiku['\"]\s*\]",
        )

    def test_callout_threshold_5_percent(self):
        # plan §2 採用: callout 閾値 = 5%
        self.assertIn("0.05", self.src)


class TestModelDistRendererNode(unittest.TestCase):
    """Node round-trip で Phase 4 の振る舞いを検証."""

    @classmethod
    def setUpClass(cls):
        # node が無い環境では skip
        try:
            subprocess.run(
                ["node", "--version"], capture_output=True, timeout=5, check=True,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            raise unittest.SkipTest("node binary not available")

    def test_buildDonutSvg_emits_class_hooks(self):
        out = _node_eval("""
            const families = [
                {family: 'opus', messages: 6, messages_pct: 0.6, cost_usd: 0, cost_pct: 0},
                {family: 'sonnet', messages: 3, messages_pct: 0.3, cost_usd: 0, cost_pct: 0},
                {family: 'haiku', messages: 1, messages_pct: 0.1, cost_usd: 0, cost_pct: 0},
            ];
            const svg = window.__modelDist.buildDonutSvg(families, 'messages');
            console.log(svg);
        """)
        self.assertIn('class="donut-slice s-opus"', out)
        self.assertIn('class="donut-slice s-sonnet"', out)
        self.assertIn('class="donut-slice s-haiku"', out)
        self.assertIn("stroke-dasharray", out)
        self.assertIn("stroke-dashoffset", out)

    def test_buildDonutSvg_handles_zero_total(self):
        out = _node_eval("""
            const families = [
                {family: 'opus', messages: 0, messages_pct: 0, cost_usd: 0, cost_pct: 0},
                {family: 'sonnet', messages: 0, messages_pct: 0, cost_usd: 0, cost_pct: 0},
                {family: 'haiku', messages: 0, messages_pct: 0, cost_usd: 0, cost_pct: 0},
            ];
            const svg = window.__modelDist.buildDonutSvg(families, 'messages');
            console.log(svg);
        """)
        self.assertIn("donut-empty", out)

    def test_buildLegendHtml_includes_msgs_and_cost_columns(self):
        out = _node_eval("""
            const families = [
                {family: 'opus', messages: 6, messages_pct: 0.6, cost_usd: 0, cost_pct: 0},
                {family: 'sonnet', messages: 3, messages_pct: 0.3, cost_usd: 0, cost_pct: 0},
                {family: 'haiku', messages: 1, messages_pct: 0.1, cost_usd: 0, cost_pct: 0},
            ];
            console.log(window.__modelDist.buildLegendHtml(families));
        """)
        self.assertIn("msgs", out)
        self.assertIn("cost", out)
        # uppercase 不採用
        self.assertNotIn(">MSGS<", out)
        self.assertNotIn(">COST<", out)

    def test_buildLegendHtml_header_has_4_cells_aligned_with_body(self):
        # codex review round 2 / P3 + ユーザー指摘: header と body の grid 列数が
        # 揃っていないと msgs/cost ラベルが family-name 列の上に出てしまう。
        # CSS は 4 列 grid (dot/family/msgs/cost) なので header も 4 cell 必要。
        out = _node_eval("""
            const families = [
                {family: 'opus', messages: 6, messages_pct: 0.6, cost_usd: 1.0, cost_pct: 0.6},
                {family: 'sonnet', messages: 3, messages_pct: 0.3, cost_usd: 0.5, cost_pct: 0.3},
                {family: 'haiku', messages: 1, messages_pct: 0.1, cost_usd: 0.1, cost_pct: 0.1},
            ];
            console.log(window.__modelDist.buildLegendHtml(families));
        """)
        # header に dot 用の lh-dot cell が出ている
        self.assertIn("lh-dot", out)
        # 4 種類の cell class が header に揃っている
        for cls in ["lh-dot", "lh-fam", "lh-msgs", "lh-cost"]:
            self.assertIn(cls, out)
        # body の leg-row も 4 cell (dot / family / msgs / cost)
        for cls in ["leg-dot", "leg-fam", "leg-msgs", "leg-cost"]:
            self.assertIn(cls, out)

    def test_buildLegendHtml_uses_canonical_order(self):
        out = _node_eval("""
            const families = [
                {family: 'opus', messages: 6, messages_pct: 0.6, cost_usd: 0, cost_pct: 0},
                {family: 'sonnet', messages: 3, messages_pct: 0.3, cost_usd: 0, cost_pct: 0},
                {family: 'haiku', messages: 1, messages_pct: 0.1, cost_usd: 0, cost_pct: 0},
            ];
            console.log(window.__modelDist.buildLegendHtml(families));
        """)
        i_opus = out.index("leg-opus")
        i_sonnet = out.index("leg-sonnet")
        i_haiku = out.index("leg-haiku")
        self.assertLess(i_opus, i_sonnet)
        self.assertLess(i_sonnet, i_haiku)

    def test_buildLegendHtml_uses_leg_class(self):
        out = _node_eval("""
            const families = [
                {family: 'opus', messages: 6, messages_pct: 0.6, cost_usd: 0, cost_pct: 0},
                {family: 'sonnet', messages: 3, messages_pct: 0.3, cost_usd: 0, cost_pct: 0},
                {family: 'haiku', messages: 1, messages_pct: 0.1, cost_usd: 0, cost_pct: 0},
            ];
            console.log(window.__modelDist.buildLegendHtml(families));
        """)
        self.assertIn("leg-opus", out)
        self.assertIn("leg-sonnet", out)
        self.assertIn("leg-haiku", out)

    def test_buildCalloutHtml_filters_below_5pct(self):
        # 4% slice → callout 出ない / 5% slice → callout 出る
        out_4 = _node_eval("""
            const families = [
                {family: 'opus', messages: 96, messages_pct: 0.96, cost_usd: 0, cost_pct: 0},
                {family: 'sonnet', messages: 4, messages_pct: 0.04, cost_usd: 0, cost_pct: 0},
                {family: 'haiku', messages: 0, messages_pct: 0, cost_usd: 0, cost_pct: 0},
            ];
            console.log(window.__modelDist.buildCalloutHtml(families, 'messages'));
        """)
        # 4% sonnet は callout 対象外
        self.assertNotRegex(out_4, r'class="[^"]*\bdonut-callout\b[^"]*"[^>]*data-family="sonnet"')

        out_5 = _node_eval("""
            const families = [
                {family: 'opus', messages: 95, messages_pct: 0.95, cost_usd: 0, cost_pct: 0},
                {family: 'sonnet', messages: 5, messages_pct: 0.05, cost_usd: 0, cost_pct: 0},
                {family: 'haiku', messages: 0, messages_pct: 0, cost_usd: 0, cost_pct: 0},
            ];
            console.log(window.__modelDist.buildCalloutHtml(families, 'messages'));
        """)
        # 5% sonnet は包括 (>= 0.05) — callout 出る
        self.assertIn("donut-callout", out_5)

    def test_center_label_has_no_unit_suffix(self):
        out = _node_eval("""
            console.log(window.__modelDist.buildCenterLabel({eyebrow: 'MESSAGES', value: 5432}));
        """)
        # plan §2 採用 (Q5): 単位 suffix 行なし → 値だけ
        self.assertNotIn("msgs", out.split("MESSAGES")[-1].lower().split("5432")[-1])
        # USD / $ も後置しない (cost 軸でも eyebrow + value のみ)
        self.assertNotIn("USD", out)

    def test_render_emits_period_badge_when_period_applied(self):
        # codex review round 1 / P3: __periodBadge が IIFE scope で見えない bug の guard.
        # data.period_applied="7d" を渡したら sub に "7d 集計 · " prefix が出ること。
        out = _node_eval("""
            globalThis.document.body.dataset.activePage = 'overview';
            let _modelDistSubText = '';
            const __subEl = { textContent: '' };
            Object.defineProperty(__subEl, 'textContent', {
              set(v) { _modelDistSubText = v; },
              get() { return _modelDistSubText; },
            });
            const __panelEl = {
              querySelectorAll: () => [],
              querySelector: () => null,
            };
            globalThis.document.getElementById = (id) => {
              if (id === 'model-dist-panel') return __panelEl;
              if (id === 'modelDistSub') return __subEl;
              return null;
            };
            window.__modelDist.renderModelDistribution({
              period_applied: '7d',
              model_distribution: {
                families: [
                  {family:'opus', messages:10, messages_pct:1.0, cost_usd:1.0, cost_pct:1.0},
                  {family:'sonnet', messages:0, messages_pct:0, cost_usd:0, cost_pct:0},
                  {family:'haiku', messages:0, messages_pct:0, cost_usd:0, cost_pct:0},
                ],
                messages_total: 10,
                cost_total: 1.0,
              },
            });
            console.log(_modelDistSubText);
        """)
        self.assertIn("7d 集計 · ", out)

    def test_render_omits_period_badge_when_period_is_all(self):
        out = _node_eval("""
            globalThis.document.body.dataset.activePage = 'overview';
            let _modelDistSubText = '';
            const __subEl = { textContent: '' };
            Object.defineProperty(__subEl, 'textContent', {
              set(v) { _modelDistSubText = v; },
              get() { return _modelDistSubText; },
            });
            const __panelEl = {
              querySelectorAll: () => [],
              querySelector: () => null,
            };
            globalThis.document.getElementById = (id) => {
              if (id === 'model-dist-panel') return __panelEl;
              if (id === 'modelDistSub') return __subEl;
              return null;
            };
            window.__modelDist.renderModelDistribution({
              period_applied: 'all',
              model_distribution: {
                families: [
                  {family:'opus', messages:10, messages_pct:1.0, cost_usd:1.0, cost_pct:1.0},
                  {family:'sonnet', messages:0, messages_pct:0, cost_usd:0, cost_pct:0},
                  {family:'haiku', messages:0, messages_pct:0, cost_usd:0, cost_pct:0},
                ],
                messages_total: 10,
                cost_total: 1.0,
              },
            });
            console.log(_modelDistSubText);
        """)
        # all のときは prefix なし
        self.assertNotIn("集計 · ", out)
        self.assertIn("Σ", out)

    def test_single_family_renders_full_circle(self):
        out = _node_eval("""
            const families = [
                {family: 'opus', messages: 10, messages_pct: 1.0, cost_usd: 0, cost_pct: 0},
                {family: 'sonnet', messages: 0, messages_pct: 0, cost_usd: 0, cost_pct: 0},
                {family: 'haiku', messages: 0, messages_pct: 0, cost_usd: 0, cost_pct: 0},
            ];
            console.log(window.__modelDist.buildDonutSvg(families, 'messages'));
        """)
        # opus slice が full circle (= dasharray 第 1 値が円周一周 = 2πr) になる
        self.assertIn('class="donut-slice s-opus"', out)
        # 100% slice は donut-empty fallback ではない (= 単一 family は full circle)
        self.assertNotIn("donut-empty", out)


# =============================================================================
# Phase 5 — CSS additive + 新ファイル禁止 invariant
# =============================================================================


# server.py:_CSS_FILES / _MAIN_JS_FILES を verbatim copy。Phase 5 RED 開始時点の
# dashboard/server.py:1156-1181 の tuple と一致することを `test_*_files_unchanged`
# で structural pin (= round 1 reviewer P1 反映)。意図的 file 追加 / 削除があれば
# 本 literal も更新する。`00_router.js` は `_MAIN_JS_FILES` に **含まれない**:
# `dashboard/server.py:1207` で `__INCLUDE_ROUTER_JS__` sentinel 経由で別経路 load
# される (Round 2 reviewer P1 反映)。
EXPECTED_CSS_FILES = (
    "00_base.css", "10_components.css", "15_heartbeat.css", "20_help_tooltip.css",
    "30_pages.css", "40_patterns.css", "50_quality.css", "55_sessions.css", "60_surface.css",
)
EXPECTED_MAIN_JS_FILES = (
    "05_period.js", "10_helpers.js", "15_heartbeat.js",
    "20_load_and_render.js", "25_live_diff.js", "30_renderers_patterns.js",
    "40_renderers_quality.js", "45_renderers_sessions.js", "50_renderers_surface.js",
    "60_hashchange_listener.js", "70_init_eventsource.js", "80_help_popup.js", "90_data_tooltip.js",
)


class TestModelDistCss(unittest.TestCase):

    def setUp(self):
        self.css = _read_style("10_components.css")

    def test_panel_head_c_rose_dot_color(self):
        self.assertIn(".panel-head.c-rose .ttl .dot", self.css)
        # var(--rose) を使う
        m = re.search(
            r"\.panel-head\.c-rose\s+\.ttl\s+\.dot\s*\{[^}]*background:\s*var\(--rose\)",
            self.css,
        )
        self.assertIsNotNone(m, "panel-head.c-rose .ttl .dot { background: var(--rose); } not found")

    def test_donut_class_defined(self):
        self.assertRegex(self.css, r"\.donut\s*\{")

    def test_donut_slice_color_tokens_match_phase4_hooks(self):
        # Phase 4 で確定した s-opus / s-sonnet / s-haiku に対して Sessions ページと
        # 整合する color token (--coral / --mint / --peach) を打つ
        self.assertRegex(self.css, r"\.donut-slice\.s-opus[^{]*\{[^}]*var\(--coral\)")
        self.assertRegex(self.css, r"\.donut-slice\.s-sonnet[^{]*\{[^}]*var\(--mint\)")
        self.assertRegex(self.css, r"\.donut-slice\.s-haiku[^{]*\{[^}]*var\(--peach\)")

    def test_axis_pair_grid_defined(self):
        self.assertRegex(self.css, r"\.axis-pair\s*\{[^}]*display:\s*grid")

    def test_axis_center_eyebrow_defined(self):
        self.assertRegex(self.css, r"\.axis-head\s*\{")
        self.assertRegex(self.css, r"\.axis-center\s*\{")

    def test_donut_callout_defined(self):
        self.assertRegex(self.css, r"\.donut-callout\s*\{")

    def test_model_legend_uses_canonical_color_tokens(self):
        # leg-opus / leg-sonnet / leg-haiku に Sessions 整合 color token
        self.assertRegex(self.css, r"\.leg-opus[^{]*\{[^}]*var\(--coral\)")
        self.assertRegex(self.css, r"\.leg-sonnet[^{]*\{[^}]*var\(--mint\)")
        self.assertRegex(self.css, r"\.leg-haiku[^{]*\{[^}]*var\(--peach\)")

    def test_donut_empty_class_defined(self):
        self.assertRegex(self.css, r"\.donut-empty\s*\{")

    def test_main_js_files_unchanged(self):
        # 新ファイル禁止 invariant の structural guard (round 1 reviewer P1 反映)
        from dashboard.server import _MAIN_JS_FILES
        self.assertEqual(_MAIN_JS_FILES, EXPECTED_MAIN_JS_FILES)

    def test_css_files_unchanged(self):
        from dashboard.server import _CSS_FILES
        self.assertEqual(_CSS_FILES, EXPECTED_CSS_FILES)


if __name__ == "__main__":
    unittest.main()
