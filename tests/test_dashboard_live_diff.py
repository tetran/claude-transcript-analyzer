"""tests/test_dashboard_live_diff.py — Issue #69: live diff highlight + toast.

Live mode (`/events` SSE refresh) のときだけ走る差分ハイライト + 更新概要 toast。

仕分け:
  1-a. literal pin: ファイル存在 / 関数定義 / `_MAIN_JS_FILES` 配置 / shell.html の
       toast 要素 / CSS keyframe / `prefers-reduced-motion` / `__livePrev` 宣言の
       一意性 / 直接代入禁止 / WeakMap 必須規約。grep で十分なものは正規表現で pin。
  1-b. Node round-trip: `buildLiveSnapshot` / `diffLiveSnapshot` / `formatToastSummary`
       / `commitLiveSnapshot` の behavior 検証。host TZ には依存しないので env override
       は不要。
  3.   static export 経路で toast / highlight が出ないことの構造 pin。

DOM 依存関数 (`applyHighlights` / `showLiveToast`) は Node round-trip では検証しない
(Phase 5 visual smoke で実機確認)。
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
_LIVE_DIFF_JS = _TEMPLATE_DIR / "scripts" / "25_live_diff.js"
_LOAD_RENDER_JS = _TEMPLATE_DIR / "scripts" / "20_load_and_render.js"
_HELPERS_JS = _TEMPLATE_DIR / "scripts" / "10_helpers.js"
_INIT_ES_JS = _TEMPLATE_DIR / "scripts" / "70_init_eventsource.js"
_HASHCHANGE_JS = _TEMPLATE_DIR / "scripts" / "60_hashchange_listener.js"
_DASHBOARD_PY = Path(__file__).parent.parent / "dashboard" / "server.py"
_COMPONENTS_CSS = _TEMPLATE_DIR / "styles" / "10_components.css"
_SHELL_HTML = _TEMPLATE_DIR / "shell.html"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ============================================================
#  1-a. Literal pin: 25_live_diff.js の関数 / 構造規約
# ============================================================
class TestLiveDiffJsStructure:
    def test_25_live_diff_js_file_exists(self):
        assert _LIVE_DIFF_JS.is_file(), \
            "dashboard/template/scripts/25_live_diff.js が存在しない"

    def test_build_live_snapshot_function_defined(self):
        body = _read(_LIVE_DIFF_JS)
        assert re.search(r"\bfunction\s+buildLiveSnapshot\s*\(", body), \
            "function buildLiveSnapshot(...) が 25_live_diff.js に定義されていない"

    def test_diff_live_snapshot_function_defined(self):
        body = _read(_LIVE_DIFF_JS)
        assert re.search(r"\bfunction\s+diffLiveSnapshot\s*\(", body), \
            "function diffLiveSnapshot(...) が 25_live_diff.js に定義されていない"

    def test_apply_highlights_function_defined(self):
        body = _read(_LIVE_DIFF_JS)
        assert re.search(r"\bfunction\s+applyHighlights\s*\(", body), \
            "function applyHighlights(...) が 25_live_diff.js に定義されていない"

    def test_format_toast_summary_function_defined(self):
        body = _read(_LIVE_DIFF_JS)
        assert re.search(r"\bfunction\s+formatToastSummary\s*\(", body), \
            "function formatToastSummary(...) が 25_live_diff.js に定義されていない"

    def test_show_live_toast_function_defined(self):
        body = _read(_LIVE_DIFF_JS)
        assert re.search(r"\bfunction\s+showLiveToast\s*\(", body), \
            "function showLiveToast(...) が 25_live_diff.js に定義されていない"

    def test_commit_live_snapshot_function_defined(self):
        body = _read(_LIVE_DIFF_JS)
        assert re.search(r"\bfunction\s+commitLiveSnapshot\s*\(", body), \
            "function commitLiveSnapshot(...) が 25_live_diff.js に定義されていない"

    def test_schedule_load_and_render_function_defined(self):
        """SSE refresh / hashchange の loadAndRender 並行発火を直列化する
        scheduleLoadAndRender が 25_live_diff.js に定義されている。defining
        ファイルを 25 にする理由: __activeRender / __pendingRefresh の
        closure-private state が 25 番冒頭の IIFE-top 宣言群と同居しているため。
        """
        body = _read(_LIVE_DIFF_JS)
        assert re.search(r"\bfunction\s+scheduleLoadAndRender\s*\(", body), \
            "function scheduleLoadAndRender(...) が 25_live_diff.js に定義されていない"

    def test_schedule_state_declared_at_iife_top(self):
        """`__activeRender` / `__pendingRefresh` が 25_live_diff.js 冒頭 (関数定義
        より前) に宣言されている。25 番冒頭に集約する理由は 25_live_diff.js の
        TDZ 安全性コメントと同じ — IIFE-top の `let` 群と一緒に評価済にする。
        """
        body = _read(_LIVE_DIFF_JS)
        first_fn_match = re.search(r"\bfunction\s+", body)
        assert first_fn_match is not None
        head = body[: first_fn_match.start()]
        assert re.search(r"\blet\s+__activeRender\s*=", head), \
            "25_live_diff.js 冒頭に let __activeRender = ... 宣言が無い"
        assert re.search(r"\blet\s+__pendingRefresh\s*=", head), \
            "25_live_diff.js 冒頭に let __pendingRefresh = ... 宣言が無い"

    def test_25_declares_liveprev_at_iife_top(self):
        """25_live_diff.js 冒頭 (関数定義より前) に `let __livePrev` 宣言が存在する。

        TDZ 安全性根拠: shell.html は全 main_js を単一 IIFE で wrap するため、
        20 番の loadAndRender 関数 body が呼び出される時点で 25 番冒頭の `let` は
        評価済 → ReferenceError は構造的に発生しない。
        """
        body = _read(_LIVE_DIFF_JS)
        first_fn_match = re.search(r"\bfunction\s+", body)
        assert first_fn_match is not None, "25_live_diff.js に関数定義が無い"
        head = body[: first_fn_match.start()]
        assert re.search(r"\blet\s+__livePrev\s*=", head), \
            "25_live_diff.js 冒頭 (関数定義より前) に let __livePrev = ... 宣言が無い"

    def test_apply_highlights_uses_weakmap_for_timer_state(self):
        """applyHighlights の per-element timer state は WeakMap で持つ。

        rank row は loadAndRender ごとに innerHTML 完全置換で element 参照が detach
        するため、Map だと detach 済 DOM を pin して slow leak になる。WeakMap 必須。
        """
        body = _read(_LIVE_DIFF_JS)
        assert "new WeakMap(" in body, \
            "25_live_diff.js に new WeakMap(...) が無い (timer state は WeakMap 必須)"

    def test_show_live_toast_replays_slide_in_on_overwrite(self):
        """表示中の toast に上書き表示が来たとき slide-in animation を再生する構造。

        CSS transition 方式では Browser が同フレーム内の連続 style 変更を collapse
        して transition を skip する問題があり、実機で「text だけ瞬時置換」になる。
        CSS animation (@keyframes toast-in) を使い、reflow trick (`.remove + offsetWidth
        + .add`) で確実に animation を再起動する classic pattern を pin する。

        pins:
          - @keyframes toast-in / toast-out が CSS に含まれる (concat 後 _HTML_TEMPLATE)
          - .toast.show に animation: toast-in が適用される
          - showLiveToast で `.show` remove → reflow → re-add の順序がある
        """
        body = _read(_LIVE_DIFF_JS)
        match = re.search(
            r"function\s+showLiveToast\s*\([^)]*\)\s*\{",
            body,
        )
        assert match is not None
        start = match.end()
        depth = 1
        i = start
        while i < len(body) and depth > 0:
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
            i += 1
        fn_body = body[start:i - 1]
        # `.show` を一度 remove してから reflow trick を経て再 add する pattern
        assert "classList.remove('show')" in fn_body, \
            "showLiveToast に classList.remove('show') が無い (animation 再起動の起点)"
        assert "void el.offsetWidth" in fn_body, \
            "showLiveToast に void el.offsetWidth が無い (CSS animation 再起動の reflow trick)"
        assert "classList.add('show')" in fn_body, \
            "showLiveToast に classList.add('show') が無い (animation 再起動の終点)"
        # 順序確認: remove('show') → offsetWidth → add('show')
        i_rem = fn_body.find("classList.remove('show')")
        i_reflow = fn_body.find("void el.offsetWidth")
        i_add = fn_body.find("classList.add('show')")
        assert i_rem < i_reflow < i_add, \
            "showLiveToast の reflow trick 順序が誤っている (remove → reflow → add でないと animation 再起動しない)"
        # CSS 側に @keyframes toast-in / toast-out が定義されている
        css_body = _read(_COMPONENTS_CSS)
        assert "@keyframes toast-in" in css_body, \
            "10_components.css に @keyframes toast-in が無い (CSS animation 不在)"
        assert "@keyframes toast-out" in css_body, \
            "10_components.css に @keyframes toast-out が無い (fade-out animation 不在)"
        # .toast.show に animation: toast-in が当たる
        assert re.search(r"\.toast\.show\s*\{[^}]*animation:\s*toast-in", css_body), \
            ".toast.show に animation: toast-in が当たっていない (slide-in 再生されない)"
        # .toast.fading に animation: toast-out が当たる
        assert re.search(r"\.toast\.fading\s*\{[^}]*animation:\s*toast-out", css_body), \
            ".toast.fading に animation: toast-out が当たっていない (fade-out 再生されない)"

    def test_show_live_toast_fade_out_completes_before_hidden(self):
        """showLiveToast の fade-out transition が display: none で打ち切られない構造。

        `.show` を remove したあと **同フレームで** `hidden = true` を当てると CSS
        `transition: opacity 240ms ease` がキャンセルされて瞬間消失する。CSS と
        同期した __TOAST_FADE_MS 経過後に hidden を当てる二段 timer 設計を強制
        する。pure DOM 副作用なので Node round-trip では検証できず、source レベル
        の structural pin で代替する。
        """
        body = _read(_LIVE_DIFF_JS)
        # __TOAST_FADE_MS 定数の存在 (CSS の 240ms と同期)
        assert re.search(r"__TOAST_FADE_MS\s*=\s*\d+", body), \
            "__TOAST_FADE_MS 定数 (CSS transition と同期) が定義されていない"
        # __toastFadeTimer (fade-out 終了 → hidden 化) と __toastTimer (display 期間終了 →
        # fade-out 開始) が両方存在
        assert "__toastFadeTimer" in body, \
            "__toastFadeTimer (fade-out 完了後 hidden 化用) が無い — fade-out が瞬間消失する"
        # showLiveToast 関数本体を抜き出して、内側に二段 setTimeout (TOAST_MS と
        # TOAST_FADE_MS) があることを構造的に確認
        match = re.search(
            r"function\s+showLiveToast\s*\([^)]*\)\s*\{",
            body,
        )
        assert match is not None
        start = match.end()
        depth = 1
        i = start
        while i < len(body) and depth > 0:
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
            i += 1
        fn_body = body[start:i - 1]
        # 二段 setTimeout 構造: 外側 __TOAST_MS / 内側 __TOAST_FADE_MS
        assert "__TOAST_MS" in fn_body and "__TOAST_FADE_MS" in fn_body, \
            "showLiveToast 内に __TOAST_MS / __TOAST_FADE_MS 両方が無い (二段 timer 設計違反)"
        # `.show` remove と `hidden = true` を同 statement / 同フレームで打っていない
        # ことを確認: `el.classList.remove('show'); el.hidden = true` のような
        # 直接連結が無い (= setTimeout を挟んでいる)
        assert not re.search(
            r"classList\.remove\(['\"]show['\"]\)\s*;\s*\n?\s*[a-zA-Z_].*?hidden\s*=\s*true",
            fn_body,
        ), "showLiveToast で .show remove 直後に hidden=true を打っている (fade-out transition がキャンセルされる)"


# ============================================================
#  1-a. Literal pin: 20_load_and_render.js 側の統合と __livePrev 規律
# ============================================================
class TestLoadRenderIntegration:
    def test_load_and_render_calls_diff_helpers(self):
        body = _read(_LOAD_RENDER_JS)
        assert "buildLiveSnapshot(" in body, \
            "20_load_and_render.js から buildLiveSnapshot(...) が呼ばれていない"
        assert "diffLiveSnapshot(" in body, \
            "20_load_and_render.js から diffLiveSnapshot(...) が呼ばれていない"

    def test_load_render_does_not_redeclare_liveprev(self):
        """20 番に `let __livePrev` / `var __livePrev` / `const __livePrev` の
        **宣言文** が無いことを pin。25 番 IIFE-top 宣言の lexical 一意性を構造保証。
        """
        body = _read(_LOAD_RENDER_JS)
        for kw in ("let", "var", "const"):
            assert not re.search(rf"\b{kw}\s+__livePrev\b", body), \
                f"20_load_and_render.js に `{kw} __livePrev` 宣言が混入している"

    def test_load_render_does_not_directly_assign_liveprev(self):
        """20 番から `__livePrev =` 直接代入を禁止 (commitLiveSnapshot 経由のみ強制)。

        catch 経路で `commitLiveSnapshot` を呼ばない契約を構造保証する。
        """
        body = _read(_LOAD_RENDER_JS)
        assert not re.search(r"__livePrev\s*=", body), \
            "20_load_and_render.js に __livePrev への直接代入が混入している" \
            " (commitLiveSnapshot(...) 経由のみ許可)"

    def test_init_eventsource_uses_schedule_wrapper_for_sse_refresh(self):
        """SSE refresh の fire-and-forget 経路は scheduleLoadAndRender 経由で呼ぶ。

        bare `loadAndRender()` を fire-and-forget すると、fetch1 / fetch2 が overlap
        した際に DOM が古い data1 で上書き → commitLiveSnapshot で __livePrev も
        snap1 に巻き戻る race が再来する。
        """
        body = _read(_INIT_ES_JS)
        # message handler 内で loadAndRender 直接呼出ではなく schedule 経由
        msg_handler = re.search(
            r"addEventListener\(['\"]message['\"][^{]*\{(.*?)\}\)",
            body, re.DOTALL,
        )
        assert msg_handler is not None, \
            "70_init_eventsource.js の message handler が見つからない"
        handler_body = msg_handler.group(1)
        assert "scheduleLoadAndRender(" in handler_body, \
            "70_init_eventsource.js の SSE message handler が " \
            "scheduleLoadAndRender(...) を呼んでいない"
        # bare loadAndRender( を message handler 内で fire-and-forget していない
        # (= "loadAndRender(" が現れたとしても scheduleLoadAndRender( の一部として)
        bare_calls = re.findall(r"(?<!schedule)\bloadAndRender\s*\(", handler_body)
        assert not bare_calls, \
            "70_init_eventsource.js の SSE message handler に bare loadAndRender(...) " \
            "の fire-and-forget が残っている (race 防止のため schedule 経由必須)"

    def test_hashchange_listener_uses_schedule_wrapper(self):
        """hashchange handler も SSE refresh と並行して走り得るので schedule 経由。"""
        body = _read(_HASHCHANGE_JS)
        assert "scheduleLoadAndRender(" in body, \
            "60_hashchange_listener.js が scheduleLoadAndRender(...) を呼んでいない"
        # bare loadAndRender( が残っていない
        bare_calls = re.findall(r"(?<!schedule)\bloadAndRender\s*\(", body)
        assert not bare_calls, \
            "60_hashchange_listener.js に bare loadAndRender(...) が残っている " \
            "(race 防止のため schedule 経由必須)"


# ============================================================
#  1-a. Literal pin: server.py の _MAIN_JS_FILES に 25 が挟まる
# ============================================================
class TestMainJsTupleOrder:
    def test_25_listed_in_main_js_files_tuple(self):
        body = _read(_DASHBOARD_PY)
        # _MAIN_JS_FILES tuple を切り出して順序を見る。
        # `[^)]*` だと tuple 内コメント (例: "(KPI / ranking / ... / projects)") の閉じ
        # 括弧で early-stop するため、開き行から閉じ行 `\n)\n` までを multi-line で取る。
        match = re.search(r"_MAIN_JS_FILES\s*=\s*\(\n(.*?)\n\)\n", body, re.DOTALL)
        assert match is not None, "dashboard/server.py に _MAIN_JS_FILES tuple が無い"
        tuple_body = match.group(1)
        # 各エントリの " ... " の中身だけを順序保ったまま抜き出す
        names = re.findall(r'"([^"]+\.js)"', tuple_body)
        assert "25_live_diff.js" in names, \
            "_MAIN_JS_FILES に 25_live_diff.js が含まれていない"
        assert "20_load_and_render.js" in names and "30_renderers_patterns.js" in names, \
            "_MAIN_JS_FILES の前提エントリ (20 / 30) が無い"
        i_20 = names.index("20_load_and_render.js")
        i_25 = names.index("25_live_diff.js")
        i_30 = names.index("30_renderers_patterns.js")
        assert i_20 < i_25 < i_30, \
            "25_live_diff.js は 20_load_and_render.js と 30_renderers_patterns.js " \
            f"の間に挟まる必要がある (got order: {names})"


# ============================================================
#  1-a. Literal pin: shell.html / 10_components.css への変更
# ============================================================
class TestShellAndComponentsAssets:
    def test_assembled_template_contains_toast_element(self):
        template = load_assembled_template()
        assert 'id="liveToast"' in template, \
            "concat 後 _HTML_TEMPLATE に id=\"liveToast\" 要素が含まれていない"

    def test_assembled_template_toast_has_role_and_aria(self):
        template = load_assembled_template()
        # liveToast 要素を含む 1 行を取り出してアトリビュートを確認
        match = re.search(r'<[^>]*id="liveToast"[^>]*>', template)
        assert match is not None, "liveToast 要素のタグが無い"
        tag = match.group(0)
        assert 'role="status"' in tag, "liveToast に role=\"status\" が無い"
        assert 'aria-live="polite"' in tag, "liveToast に aria-live=\"polite\" が無い"

    def test_assembled_template_contains_pulse_keyframe(self):
        template = load_assembled_template()
        assert "@keyframes pulse-bg" in template, \
            "concat 後 _HTML_TEMPLATE に @keyframes pulse-bg が含まれていない"

    def test_pulse_keyframe_respects_reduced_motion(self):
        """`@media (prefers-reduced-motion: reduce)` ブロックで `.bumped` の
        animation を無効化していることを構造的に pin。
        """
        css = _read(_COMPONENTS_CSS)
        assert "prefers-reduced-motion" in css, \
            "10_components.css に prefers-reduced-motion メディアクエリが無い"
        # メディアクエリ block を抜き出して .bumped の animation 無効化を確認
        match = re.search(
            r"@media\s*\([^)]*prefers-reduced-motion[^)]*\)\s*\{(.*?)\n\s*\}\s*\n",
            css, re.DOTALL,
        )
        assert match is not None, \
            "prefers-reduced-motion ブロックが閉じていない / 構造化できていない"
        block = match.group(1)
        assert ".bumped" in block, \
            "prefers-reduced-motion ブロックで .bumped の制御が見当たらない"
        assert ("animation: none" in block) or ("animation:none" in block), \
            "prefers-reduced-motion ブロックで animation: none による無効化が無い"


# ============================================================
#  1-b. Node round-trip: pure helper の behavior
#  CI に node が無いので skipUnless gate
# ============================================================
_NODE = shutil.which("node")


def _node_eval(script: str) -> object:
    """Node で script を評価し JSON 結果を返す。

    helpers.js + 25_live_diff.js を IIFE 内側で動く前提でそのまま eval する。
    Windows CI で os.environ を継承して PATH 等が消えないように env.copy する。

    encoding='utf-8' を明示する理由: subprocess.run(text=True) は Windows で
    OS デフォルト encoding (cp932 / cp1252 等) を使うため、Node stdout の UTF-8
    multibyte 文字 (本機能の toast separator " · " = U+00B7 等) が誤 decode され
    'Â·' / '·' のような mojibake になり、Windows CI 上で test 失敗する
    (Linux/macOS では `LANG=*.UTF-8` がデフォルトなので顕在化しない)。
    """
    helpers_src = _read(_HELPERS_JS)
    live_src = _read(_LIVE_DIFF_JS)
    full = helpers_src + "\n" + live_src + "\n" + script
    env = os.environ.copy()
    proc = subprocess.run(
        [_NODE, "-e", full],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=10,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"node failed (returncode={proc.returncode}): stderr={proc.stderr}"
        )
    return json.loads(proc.stdout)


@unittest.skipUnless(_NODE, "node not installed; skipping behavior round-trip")
class TestBuildLiveSnapshotNode(unittest.TestCase):
    def test_extracts_kpi_keys(self):
        """data の各種カウントが kpi bucket に対応 id で入る。"""
        data = {
            "total_events": 100,
            "skill_ranking": [{"name": "a", "count": 10}, {"name": "b", "count": 5}],
            "subagent_ranking": [{"name": "x", "count": 3}],
            "project_breakdown": [{"project": "p1", "count": 2}],
            "session_stats": {
                "total_sessions": 7,
                "resume_rate": 0.5,
                "compact_count": 4,
                "permission_prompt_count": 2,
            },
            "hourly_heatmap": {"buckets": [
                {"hour_utc": "2026-04-29T00:00:00+00:00", "count": 8},
                {"hour_utc": "2026-04-30T00:00:00+00:00", "count": 4},
            ]},
        }
        out = _node_eval(
            "const s = buildLiveSnapshot(" + json.dumps(data) + ");\n"
            "process.stdout.write(JSON.stringify({\n"
            "  kpiTotal: s.kpi['kpi-total'],\n"
            "  kpiSkills: s.kpi['kpi-skills'],\n"
            "  kpiSubs: s.kpi['kpi-subs'],\n"
            "  kpiProjs: s.kpi['kpi-projs'],\n"
            "  kpiSess: s.kpi['kpi-sess'],\n"
            "  kpiCompact: s.kpi['kpi-compact'],\n"
            "  kpiPerm: s.kpi['kpi-perm'],\n"
            "  ledeEvents: s.lede.ledeEvents,\n"
            "  ledeProjects: s.lede.ledeProjects,\n"
            "}));\n"
        )
        self.assertEqual(out["kpiTotal"], 100)
        self.assertEqual(out["kpiSkills"], 2)
        self.assertEqual(out["kpiSubs"], 1)
        self.assertEqual(out["kpiProjs"], 1)
        self.assertEqual(out["kpiSess"], 7)
        self.assertEqual(out["kpiCompact"], 4)
        self.assertEqual(out["kpiPerm"], 2)
        self.assertEqual(out["ledeEvents"], 100)
        self.assertEqual(out["ledeProjects"], 1)

    def test_handles_missing_data_with_defensive_default(self):
        """data = {} でも全 KPI が 0 で埋まる。"""
        out = _node_eval(
            "const s = buildLiveSnapshot({});\n"
            "process.stdout.write(JSON.stringify({\n"
            "  kpiTotal: s.kpi['kpi-total'],\n"
            "  kpiSkills: s.kpi['kpi-skills'],\n"
            "  kpiSubs: s.kpi['kpi-subs'],\n"
            "  ledeEvents: s.lede.ledeEvents,\n"
            "  ledeDays: s.lede.ledeDays,\n"
            "  rankSkillSize: s.rankSkill.size,\n"
            "  rankSubSize: s.rankSub.size,\n"
            "}));\n"
        )
        self.assertEqual(out["kpiTotal"], 0)
        self.assertEqual(out["kpiSkills"], 0)
        self.assertEqual(out["kpiSubs"], 0)
        self.assertEqual(out["ledeEvents"], 0)
        self.assertEqual(out["ledeDays"], 0)
        self.assertEqual(out["rankSkillSize"], 0)
        self.assertEqual(out["rankSubSize"], 0)

    def test_rank_skill_is_map_keyed_by_name(self):
        """rankSkill は name → count Map で、同 rank index でも name 単位で diff できる。"""
        data = {
            "skill_ranking": [
                {"name": "alpha", "count": 11},
                {"name": "beta", "count": 7},
            ],
        }
        out = _node_eval(
            "const s = buildLiveSnapshot(" + json.dumps(data) + ");\n"
            "process.stdout.write(JSON.stringify({\n"
            "  alpha: s.rankSkill.get('alpha'),\n"
            "  beta: s.rankSkill.get('beta'),\n"
            "}));\n"
        )
        self.assertEqual(out["alpha"], 11)
        self.assertEqual(out["beta"], 7)


@unittest.skipUnless(_NODE, "node not installed; skipping behavior round-trip")
class TestDiffLiveSnapshotNode(unittest.TestCase):
    def test_returns_empty_when_first_render(self):
        """prev === null のとき diff は all-empty を返す (toast 抑制経路)。"""
        out = _node_eval(
            "const next = buildLiveSnapshot({total_events: 1});\n"
            "const d = diffLiveSnapshot(null, next);\n"
            "process.stdout.write(JSON.stringify({\n"
            "  kpiLen: d.kpi.length,\n"
            "  ledeLen: d.lede.length,\n"
            "  rankSkillLen: d.rankSkill.length,\n"
            "  rankSubLen: d.rankSub.length,\n"
            "}));\n"
        )
        self.assertEqual(out, {"kpiLen": 0, "ledeLen": 0, "rankSkillLen": 0, "rankSubLen": 0})

    def test_kpi_increment_only(self):
        """KPI 増加フィールドだけ delta > 0 entry。delta == 0 / < 0 は出ない。"""
        out = _node_eval(
            "const prev = buildLiveSnapshot({\n"
            "  total_events: 100,\n"
            "  session_stats: {total_sessions: 5},\n"
            "});\n"
            "const next = buildLiveSnapshot({\n"
            "  total_events: 105,\n"
            "  session_stats: {total_sessions: 5},\n"
            "});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify(d.kpi));\n"
        )
        # kpi-total +5 だけ。kpi-sess は不変なので含まれない
        ids = [e["id"] for e in out]
        self.assertIn("kpi-total", ids)
        self.assertNotIn("kpi-sess", ids)
        kpi_total = next(e for e in out if e["id"] == "kpi-total")
        self.assertEqual(kpi_total["delta"], 5)

    def test_kpi_decrement_excluded(self):
        """delta < 0 は出力に含まれない (Issue #69 scope: 増分のみ)。"""
        out = _node_eval(
            "const prev = buildLiveSnapshot({total_events: 100});\n"
            "const next = buildLiveSnapshot({total_events: 90});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify(d.kpi));\n"
        )
        self.assertEqual(out, [])

    def test_lede_increment(self):
        """lede ledeEvents の delta は別 bucket に出る。"""
        out = _node_eval(
            "const prev = buildLiveSnapshot({total_events: 50});\n"
            "const next = buildLiveSnapshot({total_events: 62});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify(d.lede));\n"
        )
        ids = [e["id"] for e in out]
        self.assertIn("ledeEvents", ids)
        e = next(x for x in out if x["id"] == "ledeEvents")
        self.assertEqual(e["delta"], 12)

    def test_ranking_new_name_treated_as_zero_baseline(self):
        """前回 Map に key 無しの新登場 skill は delta = current - 0 で出る。"""
        out = _node_eval(
            "const prev = buildLiveSnapshot({skill_ranking: [{name:'a', count:5}]});\n"
            "const next = buildLiveSnapshot({skill_ranking: ["
            "  {name:'a', count:5}, {name:'b', count:3}]});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify(d.rankSkill));\n"
        )
        names = [e["name"] for e in out]
        self.assertIn("b", names)
        b = next(e for e in out if e["name"] == "b")
        self.assertEqual(b["delta"], 3)
        # a は不変なので含まれない
        self.assertNotIn("a", names)

    def test_ranking_existing_name_count_growth(self):
        out = _node_eval(
            "const prev = buildLiveSnapshot({skill_ranking: [{name:'a', count:5}]});\n"
            "const next = buildLiveSnapshot({skill_ranking: [{name:'a', count:8}]});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify(d.rankSkill));\n"
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "a")
        self.assertEqual(out[0]["delta"], 3)

    def test_ranking_name_disappeared_does_not_appear(self):
        """前回 top10 にいたが今回消えた skill は出力に出ない (toast は増分のみ)。"""
        out = _node_eval(
            "const prev = buildLiveSnapshot({skill_ranking: [{name:'a', count:5}]});\n"
            "const next = buildLiveSnapshot({skill_ranking: []});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify(d.rankSkill));\n"
        )
        self.assertEqual(out, [])


@unittest.skipUnless(_NODE, "node not installed; skipping behavior round-trip")
class TestFormatToastSummaryNode(unittest.TestCase):
    def test_aggregates_by_label(self):
        """diff から `+12 events · +1 skill · +2 subagent invocations` を生成。"""
        out = _node_eval(
            "const prev = buildLiveSnapshot({\n"
            "  total_events: 100,\n"
            "  skill_ranking: [{name:'a', count:5}],\n"
            "  subagent_ranking: [{name:'x', count:1}],\n"
            "});\n"
            "const next = buildLiveSnapshot({\n"
            "  total_events: 112,\n"
            "  skill_ranking: [{name:'a', count:5}, {name:'b', count:3}],\n"
            "  subagent_ranking: [{name:'x', count:1}, {name:'y', count:1}, {name:'z', count:1}],\n"
            "});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify(formatToastSummary(d)));\n"
        )
        # +12 events · +1 skill · +2 subagent invocations
        self.assertIn("+12 events", out)
        self.assertIn("+1 skill", out)
        self.assertIn("+2 subagent invocations", out)
        # セパレータは " · "
        self.assertIn(" · ", out)
        # 順序固定: events → skills → subagent
        i_events = out.index("+12 events")
        i_skill = out.index("+1 skill")
        i_sub = out.index("+2 subagent invocations")
        self.assertTrue(i_events < i_skill < i_sub,
                        f"順序が events → skill → subagent でない: {out!r}")

    def test_returns_empty_when_no_growth(self):
        out = _node_eval(
            "const prev = buildLiveSnapshot({total_events: 5});\n"
            "const next = buildLiveSnapshot({total_events: 5});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify(formatToastSummary(d)));\n"
        )
        self.assertEqual(out, "")

    def test_skips_zero_delta_segments(self):
        """events 増えたが skills 不変なら skills セグメントは出ない。"""
        out = _node_eval(
            "const prev = buildLiveSnapshot({\n"
            "  total_events: 10, skill_ranking: [{name:'a', count:1}]});\n"
            "const next = buildLiveSnapshot({\n"
            "  total_events: 13, skill_ranking: [{name:'a', count:1}]});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify(formatToastSummary(d)));\n"
        )
        self.assertIn("+3 events", out)
        self.assertNotIn("skill", out)

    def test_caps_at_four_segments(self):
        """5 種以上の delta があっても先頭 4 セグメントに切る (省略 ... を付けない)。

        順序固定: events → skills → subagent → sessions → projects → compact → permission
        priority 順から先頭 4 が残る。
        """
        out = _node_eval(
            "const prev = buildLiveSnapshot({\n"
            "  total_events: 0,\n"
            "  skill_ranking: [],\n"
            "  subagent_ranking: [],\n"
            "  project_breakdown: [],\n"
            "  session_stats: {total_sessions: 0, compact_count: 0, permission_prompt_count: 0},\n"
            "});\n"
            "const next = buildLiveSnapshot({\n"
            "  total_events: 10,\n"
            "  skill_ranking: [{name:'a', count:1}],\n"
            "  subagent_ranking: [{name:'x', count:1}],\n"
            "  project_breakdown: [{project:'p1', count:1}],\n"
            "  session_stats: {total_sessions: 2, compact_count: 1, permission_prompt_count: 1},\n"
            "});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify(formatToastSummary(d)));\n"
        )
        # セグメント数 = " · " で分割した数
        segments = out.split(" · ")
        self.assertEqual(len(segments), 4, f"4 セグメントを超えた: {out!r}")
        # 先頭 4: events, skill, subagent, session (priority 順)
        self.assertTrue(segments[0].startswith("+10 event"))
        self.assertIn("skill", segments[1])
        self.assertIn("subagent", segments[2])
        self.assertIn("session", segments[3])
        # cap 後ろの projects / compact / permission は出ない
        self.assertNotIn("project", out)
        self.assertNotIn("compaction", out)
        self.assertNotIn("permission", out)
        # 省略 "..." を付けない
        self.assertNotIn("...", out)

    def test_uses_singular_for_delta_one(self):
        """+1 event / +1 skill / +1 subagent invocation (singular)。"""
        out = _node_eval(
            "const prev = buildLiveSnapshot({\n"
            "  total_events: 0,\n"
            "  skill_ranking: [],\n"
            "  subagent_ranking: [],\n"
            "});\n"
            "const next = buildLiveSnapshot({\n"
            "  total_events: 1,\n"
            "  skill_ranking: [{name:'a', count:1}],\n"
            "  subagent_ranking: [{name:'x', count:1}],\n"
            "});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify(formatToastSummary(d)));\n"
        )
        self.assertIn("+1 event", out)
        self.assertIn("+1 skill", out)
        self.assertIn("+1 subagent invocation", out)
        # plural が混入していない (event(s) / skill(s) / invocation(s) を見分ける)
        self.assertNotIn("+1 events", out)
        self.assertNotIn("+1 skills", out)
        self.assertNotIn("+1 subagent invocations", out)

    def test_excludes_resume_rate(self):
        """kpi-resume の delta があっても toast には出ない (highlight のみ)。"""
        out = _node_eval(
            "const prev = buildLiveSnapshot({\n"
            "  total_events: 5,\n"
            "  session_stats: {total_sessions: 10, resume_rate: 0.1},\n"
            "});\n"
            "const next = buildLiveSnapshot({\n"
            "  total_events: 5,\n"
            "  session_stats: {total_sessions: 10, resume_rate: 0.5},\n"
            "});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify(formatToastSummary(d)));\n"
        )
        self.assertNotIn("resume", out.lower())

    def test_excludes_lede_buckets(self):
        """lede 数字 (ledeEvents/ledeDays/ledeProjects) は toast に出ない。

        KPI と二重カウント防止。total_events の lede と kpi-total は同値なので、
        toast に出るのは kpi-total 経由の "+N events" のみで、ledeEvents 経由は出ない。
        ledeDays / ledeProjects 単独で toast 出ないことを確認するには、KPI が
        全 0 で lede のみ動くシナリオを作る (kpi-projs の bump が同時に出るので
        分離しにくい → ledeDays bucket を直接見る)。
        """
        out = _node_eval(
            "const prev = buildLiveSnapshot({\n"
            "  hourly_heatmap: {buckets: ["
            "    {hour_utc: '2026-04-29T00:00:00+00:00', count: 1}]}\n"
            "});\n"
            "const next = buildLiveSnapshot({\n"
            "  hourly_heatmap: {buckets: ["
            "    {hour_utc: '2026-04-29T00:00:00+00:00', count: 1},"
            "    {hour_utc: '2026-04-30T00:00:00+00:00', count: 1}]}\n"
            "});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify({\n"
            "  ledeBucketHasDays: d.lede.some(e => e.id === 'ledeDays'),\n"
            "  toast: formatToastSummary(d),\n"
            "}));\n"
        )
        # lede bucket には ledeDays delta は出るが、toast には出ない
        self.assertTrue(out["ledeBucketHasDays"],
                        "diff の lede bucket に ledeDays delta が出ていない (前提崩れ)")
        # toast 文字列に "day" が含まれない
        self.assertNotIn("day", out["toast"].lower())

    def test_excludes_ranking_rows(self):
        """ranking row delta も toast に出ない (highlight のみ)。"""
        out = _node_eval(
            "const prev = buildLiveSnapshot({skill_ranking: [{name:'codex-review', count:5}]});\n"
            "const next = buildLiveSnapshot({skill_ranking: [{name:'codex-review', count:8}]});\n"
            "const d = diffLiveSnapshot(prev, next);\n"
            "process.stdout.write(JSON.stringify(formatToastSummary(d)));\n"
        )
        # rankSkill bucket の name は toast に出ない
        self.assertNotIn("codex-review", out)
        # 同 name の +3 を kpi-skills (kind 数 = 1 のまま) と勘違いしないこと
        self.assertNotIn("skill", out)


@unittest.skipUnless(_NODE, "node not installed; skipping behavior round-trip")
class TestFirstRefreshAfterReloadNode(unittest.TestCase):
    """page reload 直後の SSE refresh 1 発目で toast が出ない構造保証。

    module 評価から始まるので __livePrev = null で初期化される。reload 直後の
    初回 refresh は diff 不能で toast 出ない (= ユーザーが意図的に reload した
    直後の noise を構造的に防ぐ)。
    """

    def test_first_refresh_after_reload_does_not_emit_toast(self):
        out = _node_eval(
            "// reload 直後の状態 = __livePrev === null。20_load_and_render.js が\n"
            "// 末尾でやることの抜粋: __livePrev !== null をガードに toast を出す。\n"
            "// commit 前の livePrev probe = null。\n"
            "const probe = (typeof window !== 'undefined' && window.__liveDiff)\n"
            "  ? window.__liveDiff.getLivePrev()\n"
            "  : __livePrev;\n"
            "const next = buildLiveSnapshot({\n"
            "  total_events: 100,\n"
            "  skill_ranking: [{name:'a', count:5}],\n"
            "});\n"
            "// __livePrev === null のため diff = empty / toast = 空文字\n"
            "const d = diffLiveSnapshot(probe, next);\n"
            "const toast = formatToastSummary(d);\n"
            "process.stdout.write(JSON.stringify({\n"
            "  probeIsNull: probe === null,\n"
            "  kpiLen: d.kpi.length,\n"
            "  toast: toast,\n"
            "}));\n"
        )
        self.assertTrue(out["probeIsNull"], "reload 直後 __livePrev は null のはず")
        self.assertEqual(out["kpiLen"], 0)
        self.assertEqual(out["toast"], "",
                         "reload 直後の初回 refresh で toast が出てしまっている")


@unittest.skipUnless(_NODE, "node not installed; skipping behavior round-trip")
class TestCommitLiveSnapshotNode(unittest.TestCase):
    def test_commit_then_diff_accumulates_across_skipped_commit(self):
        """commitLiveSnapshot(snap1) → (skip commit for snap2) → diff(getLivePrev(), snap3)
        で snap1 vs snap3 の累積 delta が出る。catch 経路で commit を呼ばないシナリオ。

        production code path で `getLivePrev` を直接呼ぶのは禁止 (test fixture probe 専用)
        だが、Node round-trip では `__livePrev` 内部状態に依存することを直接検証する。
        """
        out = _node_eval(
            "const snap1 = buildLiveSnapshot({total_events: 10});\n"
            "const snap2 = buildLiveSnapshot({total_events: 12});\n"
            "const snap3 = buildLiveSnapshot({total_events: 15});\n"
            "commitLiveSnapshot(snap1);\n"
            "// snap2 では commit を呼ばずに skip (catch 経路の擬似化)\n"
            "// snap3 で復活: __livePrev は snap1 のままなので 15 - 10 = 5 の累積 delta\n"
            "const probe = (typeof window !== 'undefined' && window.__liveDiff)\n"
            "  ? window.__liveDiff.getLivePrev()\n"
            "  : __livePrev;\n"
            "const d = diffLiveSnapshot(probe, snap3);\n"
            "process.stdout.write(JSON.stringify(d.kpi));\n"
        )
        ids = [e["id"] for e in out]
        self.assertIn("kpi-total", ids)
        e = next(x for x in out if x["id"] == "kpi-total")
        self.assertEqual(e["delta"], 5,
                         "commit を skip した場合 累積 delta (snap1→snap3) が出るべき")


@unittest.skipUnless(_NODE, "node not installed; skipping behavior round-trip")
class TestScheduleLoadAndRenderNode(unittest.TestCase):
    """scheduleLoadAndRender が overlap を直列化し、in-flight 中の追加要求は
    coalesce して 1 件 pending に絞ることを検証する (stale-snapshot race 対策)。
    """

    def test_serializes_overlapping_calls_strictly(self):
        """scheduleLoadAndRender を 2 連続で呼んでも、2 件目の loadAndRender は
        1 件目が完了するまで開始されない (strict serial order)。

        失敗時の症状: 並行実行で start1 / start2 が先に並び end1 / end2 が後ろに
        来る ('start1','start2','end1','end2')。修正後は完全直列で
        ['start1','end1','start2','end2'] になる。
        """
        out = _node_eval(
            "let counter = 0;\n"
            "let order = [];\n"
            "let resolvers = [];\n"
            "// scheduleLoadAndRender が呼び出す lexical な loadAndRender を override する。\n"
            "// JS の関数宣言は再代入可能 (strict mode の Module ではないので)。\n"
            "loadAndRender = function () {\n"
            "  counter += 1;\n"
            "  const my = counter;\n"
            "  order.push('start' + my);\n"
            "  return new Promise(resolve => {\n"
            "    resolvers.push(function () { order.push('end' + my); resolve(); });\n"
            "  });\n"
            "};\n"
            "(async function () {\n"
            "  const p1 = scheduleLoadAndRender();\n"
            "  const p2 = scheduleLoadAndRender();\n"
            "  // microtask を流して p1 の Promise.resolve().then(...) を発火させる\n"
            "  await Promise.resolve(); await Promise.resolve();\n"
            "  const midStarted = counter;\n"
            "  const midOrder = order.slice();\n"
            "  // __pendingRefresh は live_diff.js の script-scope let なので bare name で参照\n"
            "  const midPending = __pendingRefresh;\n"
            "  // 1 件目の loadAndRender を resolve\n"
            "  resolvers[0]();\n"
            "  // finally → pending fire → 2 件目の loadAndRender 開始までの microtask を流す\n"
            "  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();\n"
            "  const afterFirst = counter;\n"
            "  const afterFirstOrder = order.slice();\n"
            "  // 2 件目を resolve\n"
            "  resolvers[1]();\n"
            "  await Promise.resolve(); await Promise.resolve();\n"
            "  process.stdout.write(JSON.stringify({\n"
            "    midStarted,            // 1 件目開始 / 2 件目はまだ → 1\n"
            "    midOrder,              // ['start1']\n"
            "    midPending,            // true (2 件目が coalesce 済)\n"
            "    afterFirst,            // 1 件目完了後に 2 件目開始 → 2\n"
            "    afterFirstOrder,       // ['start1','end1','start2']\n"
            "    finalCounter: counter, // 2 (それ以上 fire しない)\n"
            "    finalOrder: order,     // ['start1','end1','start2','end2']\n"
            "    p1eq_p2: p1 === p2,    // true (coalesce で同 promise 返却)\n"
            "  }));\n"
            "})().catch(e => { console.error(e); process.exit(1); });\n"
        )
        self.assertEqual(out["midStarted"], 1,
                         "1 件目開始時点で 2 件目が同時に走り出している (直列化されていない)")
        self.assertEqual(out["midOrder"], ["start1"])
        self.assertTrue(out["midPending"],
                        "2 件目が __pendingRefresh = true で coalesce されていない")
        self.assertEqual(out["afterFirst"], 2,
                         "1 件目完了後に 2 件目が fire していない (pending refresh が消えている)")
        self.assertEqual(out["afterFirstOrder"], ["start1", "end1", "start2"],
                         f"strict serial order が崩れている: {out['afterFirstOrder']}")
        self.assertEqual(out["finalCounter"], 2,
                         "想定外に 3 回目以降 fire している")
        self.assertEqual(out["finalOrder"], ["start1", "end1", "start2", "end2"],
                         f"final 直列順序が崩れている: {out['finalOrder']}")
        self.assertTrue(out["p1eq_p2"],
                        "scheduleLoadAndRender が in-flight 中に同じ promise を返していない")

    def test_third_call_during_first_does_not_double_pend(self):
        """1 件目 in-flight 中に 3 回連続 schedule しても pending は 1 件に
        coalesce される (queue 肥大しない)。最終的な loadAndRender 呼出回数 = 2。
        """
        out = _node_eval(
            "let counter = 0;\n"
            "let resolvers = [];\n"
            "loadAndRender = function () {\n"
            "  counter += 1;\n"
            "  return new Promise(resolve => { resolvers.push(resolve); });\n"
            "};\n"
            "(async function () {\n"
            "  scheduleLoadAndRender();\n"
            "  scheduleLoadAndRender();\n"
            "  scheduleLoadAndRender();\n"
            "  scheduleLoadAndRender();\n"
            "  await Promise.resolve(); await Promise.resolve();\n"
            "  // 1 件目だけ start している\n"
            "  const startedFirst = counter;\n"
            "  resolvers[0]();\n"
            "  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();\n"
            "  // pending 1 件だけが fire (3 件 coalesce → 1 件)\n"
            "  const startedSecond = counter;\n"
            "  resolvers[1]();\n"
            "  await Promise.resolve(); await Promise.resolve();\n"
            "  // 全完了後 pending 残らない\n"
            "  const final = counter;\n"
            "  const tailPending = __pendingRefresh;\n"
            "  process.stdout.write(JSON.stringify({\n"
            "    startedFirst, startedSecond, final, tailPending,\n"
            "  }));\n"
            "})().catch(e => { console.error(e); process.exit(1); });\n"
        )
        self.assertEqual(out["startedFirst"], 1)
        self.assertEqual(out["startedSecond"], 2,
                         "3 件以上 schedule しても pending は 1 件に coalesce される必要がある")
        self.assertEqual(out["final"], 2,
                         f"queue が肥大して 2 を超えている (got {out['final']})")
        self.assertFalse(out["tailPending"],
                         "全完了後も __pendingRefresh が立っている (cleanup 漏れ)")


# ============================================================
#  Phase 3: Static export と first-render では toast / highlight が出ない
# ============================================================
class TestStaticExportNoLiveBehavior:
    def test_static_export_does_not_show_toast(self):
        """render_static_html(data) の出力に id=\"liveToast\" 要素は存在するが
        hidden 属性が付いている (誤って toast 表示しない)。
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_dashboard_for_static_export_test", _DASHBOARD_PY
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        data = mod.build_dashboard_data([])
        html = mod.render_static_html(data)
        assert 'id="liveToast"' in html, "static export 出力に liveToast 要素が無い"
        match = re.search(r'<[^>]*id="liveToast"[^>]*>', html)
        assert match is not None
        tag = match.group(0)
        # `hidden` boolean attribute が付いている
        assert re.search(r'\bhidden\b', tag), \
            f"liveToast に hidden 属性が無い (tag={tag!r})"

    def test_static_export_does_not_apply_bumped_class(self):
        """static export では diff 不能なので bumped class が DOM 要素に付かない。

        CSS / コメント内の `bumped` 文字列は許容 (.kpi.bumped セレクタ定義は CSS に
        含まれてよい)。HTML 要素の class attribute 値として出現していないことを pin。
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_dashboard_for_static_export_test2", _DASHBOARD_PY
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        data = mod.build_dashboard_data([])
        html = mod.render_static_html(data)
        # `class="...bumped..."` のように HTML attribute 値として bumped が現れたら fail。
        bad_match = re.search(r'class="[^"]*\bbumped\b[^"]*"', html)
        assert bad_match is None, \
            f"static export 出力に bumped class が混入している (highlight は live mode 限定): {bad_match.group(0) if bad_match else ''}"

    def test_kpi_id_attributes_persist(self):
        """kpi の id 属性は loadAndRender 後の HTML 文字列にも残っている前提。

        applyHighlights が getElementById で参照する前提を壊さない。
        kpiRow.innerHTML 完全置換の出力で各 KPI tile div に id=\"kpi-...\" を
        付けて出していることを source-level に pin。
        chrome-devtools での実機確認で id 属性漏れを検出できたケースを後追いで
        構造保証する (Phase 5 visual smoke で見つけた gap)。
        """
        body = _read(_LOAD_RENDER_JS)
        # kpis array の各 entry に id: 'kpi-...' があること
        for kpi_id in ("kpi-total", "kpi-skills", "kpi-subs", "kpi-projs",
                       "kpi-sess", "kpi-resume", "kpi-compact", "kpi-perm"):
            assert f"'{kpi_id}'" in body, \
                f"20_load_and_render.js の kpis array に '{kpi_id}' が無い"
        # kpiRow.innerHTML 直前 / 内部の map() で kpi tile div に
        # `id="' + g.id + '"` を埋め込んでいること。
        # クォートのエスケープバリエーションを許容する。
        assert ("id=\"' + g.id + '\"" in body) or ("id=' + g.id + '" in body), \
            "KPI tile の HTML 出力で id=\"' + g.id + '\" を埋め込めていない " \
            "(applyHighlights の getElementById が hit しない)"

    def test_rank_row_data_name_attribute_persists(self):
        """rank renderer 出力に data-name=\"...\" が必ず含まれる。"""
        body = _read(_LOAD_RENDER_JS)
        assert "data-name=\"' + esc(it.name) + '\"" in body, \
            "rank-row の data-name 属性が rank renderer で出力されていない"
