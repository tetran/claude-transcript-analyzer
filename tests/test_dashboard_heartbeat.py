"""tests/test_dashboard_heartbeat.py — Issue #83: Live heartbeat sparkline tests.

template smoke / concat 順 / sentinel pin / static export hidden / reduced-motion CSS pin /
Node round-trip behavior (rAF mock 経由) を 1 ファイルで pin する。
"""
# pylint: disable=protected-access,line-too-long
import importlib.util
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
_DASHBOARD_PATH = _REPO / "dashboard" / "server.py"
_SCRIPTS_DIR = _REPO / "dashboard" / "template" / "scripts"
_STYLES_DIR = _REPO / "dashboard" / "template" / "styles"
_HEARTBEAT_JS = _SCRIPTS_DIR / "15_heartbeat.js"
_HEARTBEAT_CSS = _STYLES_DIR / "15_heartbeat.css"


def _load_dashboard_module(tmp_path: Path):
    usage_jsonl = tmp_path / "usage.jsonl"
    usage_jsonl.write_text("", encoding="utf-8")
    os.environ["USAGE_JSONL"] = str(usage_jsonl)
    try:
        spec = importlib.util.spec_from_file_location(
            "dashboard_server_heartbeat", _DASHBOARD_PATH
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del os.environ["USAGE_JSONL"]
    return mod


# ============================================================
#  Step 1: template concat / sentinel 整合 / literal pin
# ============================================================
class TestTemplateConcat:
    def test_html_template_contains_heartbeat_svg(self, tmp_path):
        mod = _load_dashboard_module(tmp_path)
        assert 'id="heartbeat"' in mod._HTML_TEMPLATE

    def test_html_template_contains_heartbeat_sr_span(self, tmp_path):
        mod = _load_dashboard_module(tmp_path)
        assert 'id="heartbeatSr"' in mod._HTML_TEMPLATE

    def test_css_files_position(self, tmp_path):
        mod = _load_dashboard_module(tmp_path)
        files = list(mod._CSS_FILES)
        assert "15_heartbeat.css" in files, f"15_heartbeat.css が _CSS_FILES に居ない: {files}"
        assert files.index("10_components.css") < files.index("15_heartbeat.css") < files.index("20_help_tooltip.css"), \
            f"15_heartbeat.css は 10_components.css の後 / 20_help_tooltip.css の前であるべき: {files}"

    def test_main_js_files_position(self, tmp_path):
        mod = _load_dashboard_module(tmp_path)
        files = list(mod._MAIN_JS_FILES)
        assert "15_heartbeat.js" in files, f"15_heartbeat.js が _MAIN_JS_FILES に居ない: {files}"
        assert files.index("10_helpers.js") < files.index("15_heartbeat.js") < files.index("20_load_and_render.js"), \
            f"15_heartbeat.js は 10_helpers.js の後 / 20_load_and_render.js の前であるべき: {files}"

    def test_hb_state_declared_only_in_15_heartbeat_js(self, tmp_path):
        """closure-private state が 15_heartbeat.js でのみ 1 回宣言されている。

        全 main_js を concat した string で `let __hbX` の出現が 1 回ずつ。
        既存の単一 shared IIFE 内で名前競合しないことを grep ベースで pin。
        """
        mod = _load_dashboard_module(tmp_path)
        all_js = "".join(
            (_SCRIPTS_DIR / name).read_text(encoding="utf-8")
            for name in mod._MAIN_JS_FILES
        )
        # __hbLastTickMs / __hbAccumMs は elapsed-time 駆動 tick の closure state (Issue #83 codex Round 1)。
        # __hbTickCount は idle baseline breathing wave の phase 入力 (Issue #83 codex Round 2 P1)。
        for var in (
            "__hbState", "__hbBuf", "__hbSpikeRemain", "__hbSpikeAmp", "__hbRafId",
            "__hbLastTickMs", "__hbAccumMs", "__hbTickCount",
        ):
            count = all_js.count("let " + var)
            assert count == 1, f"`let {var}` 出現は 1 回のみであるべきが {count} 回。再宣言禁止違反"

    def test_setHeartbeatState_accepts_status_label_keys(self):
        """STATUS_LABEL keys と setHeartbeatState の switch case が 1:1 対応。

        一方を増やしてもう一方を増やし忘れると test red になる drift guard。
        """
        helpers_src = (_SCRIPTS_DIR / "10_helpers.js").read_text(encoding="utf-8")
        heartbeat_src = _HEARTBEAT_JS.read_text(encoding="utf-8") if _HEARTBEAT_JS.exists() else ""
        m = re.search(r"const STATUS_LABEL\s*=\s*\{([^}]+)\}", helpers_src)
        assert m, "STATUS_LABEL definition が 10_helpers.js に見つからない"
        keys = set(re.findall(r"(\w+)\s*:", m.group(1)))
        # setHeartbeatState 関数本体を brace-match で抽出 (switch 内 if ブロックの
        # `}` で early-exit しないように non-greedy regex ではなく深さカウンタで取る)。
        m2 = re.search(r"function\s+setHeartbeatState\s*\([^)]*\)\s*\{", heartbeat_src)
        assert m2, "setHeartbeatState 関数が 15_heartbeat.js に見つからない"
        depth = 0
        body_end = -1
        for i in range(m2.end() - 1, len(heartbeat_src)):
            ch = heartbeat_src[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    body_end = i
                    break
        assert body_end > 0, "setHeartbeatState の閉じ波括弧が見つからない"
        body = heartbeat_src[m2.end():body_end]
        cases = set(re.findall(r"case\s+['\"](\w+)['\"]", body))
        assert keys == cases, \
            f"STATUS_LABEL keys={sorted(keys)} と setHeartbeatState case labels={sorted(cases)} が一致しない"


# ============================================================
#  Step 2: static export hidden + reduced-motion CSS pin
# ============================================================
class TestStaticExportHidden:
    def test_render_static_html_marks_heartbeat_hidden(self, tmp_path):
        """render_static_html の出力で <svg id="heartbeat"> tag に hidden 属性が立つ。

        live 経路では JS が hidden を解除するが、static export では JS が start しない
        ので shell.html 側の default `hidden` がそのまま残る設計。
        """
        mod = _load_dashboard_module(tmp_path)
        html = mod.render_static_html({"total_events": 0, "skill_ranking": []})
        m = re.search(r"<svg[^>]*id=\"heartbeat\"[^>]*>", html)
        assert m, "<svg id=\"heartbeat\"> tag が render_static_html 出力に見つからない"
        tag = m.group(0)
        # boolean attribute なので `hidden` 単体 / `hidden=""` のどちらでも OK
        assert re.search(r"\bhidden(?:=|\b)", tag), \
            f"static export で svg#heartbeat に hidden 属性が付いていない: {tag}"


class TestReducedMotionCss:
    def test_reduced_motion_block_disables_heartbeat_animation(self):
        css = _HEARTBEAT_CSS.read_text(encoding="utf-8") if _HEARTBEAT_CSS.exists() else ""
        m = re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{", css)
        assert m, "15_heartbeat.css に @media (prefers-reduced-motion: reduce) ブロックが無い"
        # ブロック内側を取り出す (brace match)
        start = m.end() - 1
        depth = 0
        end = start
        for i in range(start, len(css)):
            ch = css[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        block = css[start:end + 1]
        assert ".heartbeat" in block, \
            "reduced-motion ブロック内に .heartbeat 制御が無い"
        has_animation_none = ("animation: none" in block) or ("animation:none" in block)
        has_paused_marker = "--heartbeat-paused: 1" in block
        assert has_animation_none or has_paused_marker, \
            "reduced-motion ブロック内で animation 停止 marker (animation: none / --heartbeat-paused: 1) が無い"


class TestHeartbeatPulseCss:
    """常時明滅 (Issue #83 user follow-up): line そのものを周期的に明滅させる。"""

    def test_heartbeat_polyline_has_pulse_animation(self):
        css = _HEARTBEAT_CSS.read_text(encoding="utf-8") if _HEARTBEAT_CSS.exists() else ""
        # `.heartbeat polyline` ルールの中で animation: が `none` 以外の値で
        # 当たっていること (= 常時明滅 keyframes が wired up)。@media 内の
        # `animation: none` (reduced-motion 抑止) は対象外。
        matches = re.findall(
            r"\.heartbeat\s+polyline\s*\{[^}]*?animation:\s*([^;}]+)",
            css,
        )
        assert matches, ".heartbeat polyline ルールに animation: プロパティが無い"
        non_none = [v.strip() for v in matches if v.strip() != "none"]
        assert non_none, \
            f"常時明滅 animation が定義されていない (見つかった値は全部 none: {matches!r})"

    def test_pulse_keyframes_define_stroke_opacity_variation(self):
        css = _HEARTBEAT_CSS.read_text(encoding="utf-8") if _HEARTBEAT_CSS.exists() else ""
        m = re.search(r"@keyframes\s+([A-Za-z_][\w-]*)\s*\{", css)
        assert m, "15_heartbeat.css に @keyframes 定義が無い"
        # keyframes ブロック内に stroke-opacity (state 別 opacity と独立した axis) の
        # 変動が定義されていること。state 別の `opacity: 0.7` 等と conflict させない。
        start = m.end() - 1
        depth = 0
        end = start
        for i in range(start, len(css)):
            ch = css[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        block = css[start:end + 1]
        assert "stroke-opacity" in block, \
            "@keyframes 内で stroke-opacity を動かしていない (state 別 opacity と独立に明滅させるため stroke-opacity 軸を使う)"


# ============================================================
#  Step 3: Node round-trip behavior (rAF mock)
# ============================================================
_NODE = shutil.which("node")


def _node_eval_heartbeat(prelude: str, script: str) -> object:
    """`15_heartbeat.js` を **単体ファイル** で eval し JSON 結果を返す。

    プラン step 3 規律: heartbeat.js は単体で `window.__heartbeat` を完結させる
    設計のため concat 後ではなく単体ロードで test する (closure-private state
    の挙動が他 file と混ざらない)。

    `prelude` は heartbeat.js 評価 **前** に入れる stub (window / document /
    requestAnimationFrame 等)。`script` は heartbeat.js の **後** に入れる
    assertion / console.log。
    """
    src = _HEARTBEAT_JS.read_text(encoding="utf-8")
    full = prelude + "\n" + src + "\n" + script
    env = os.environ.copy()
    # Windows GitHub runner では Node の cold-start が 10 秒を超えるバラツキで
    # subprocess.TimeoutExpired を出して flaky になる (Issue #103 PR で観測)。
    # 30 秒まで猶予を持たせる: 同 test は通常 1〜2 秒で完了する想定なので、
    # ここで延ばしても通常 CI 時間に影響なし、Windows tail-latency にだけ効く。
    proc = subprocess.run(
        [_NODE, "-e", full],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"node failed (returncode={proc.returncode}): stderr={proc.stderr}"
        )
    return json.loads(proc.stdout.strip().splitlines()[-1])


_STUB_PRELUDE = r"""
globalThis.window = globalThis;
globalThis.document = globalThis.document || {};
let _fakePoly = { setAttribute() {}, getAttribute: () => '' };
let _fakeSvg = { dataset: {}, setAttribute() {}, getAttribute: () => '', querySelector: () => _fakePoly, hidden: false };
let _fakeSr = { textContent: '' };
document.getElementById = (id) => id === 'heartbeat' ? _fakeSvg : id === 'heartbeatSr' ? _fakeSr : null;
let _rafQueue = []; let _rafId = 0;
let _simTime = 0;
window.requestAnimationFrame = (fn) => { _rafQueue.push({id: ++_rafId, fn}); return _rafId; };
window.cancelAnimationFrame = (id) => { _rafQueue = _rafQueue.filter(x => x.id !== id); };
window.matchMedia = (q) => ({ matches: false });
// elapsed-ms 駆動の __hbTick (Issue #83 codex Round 1) に追従するため timestamp を渡す。
// 33ms / frame で進めると HB_MS_PER_SAMPLE と一致し 1 sample/frame で進む (= 元設計と同じ
// 観測粒度)。catch-up 上限 5 を踏まないようにするため frame ごとの dt は 33ms に固定。
function flushFrames(n) {
  for (let i = 0; i < n; i++) {
    const item = _rafQueue.shift();
    if (item) item.fn(_simTime);
    _simTime += 33;
  }
}
"""

_PRE_FLIGHT = r"""
if (typeof window.__heartbeat !== 'object') throw new Error('pre-flight: window.__heartbeat 不在');
if (typeof window.__heartbeat.bump !== 'function') throw new Error('pre-flight: __heartbeat.bump 不在');
if (typeof window.__heartbeat.setState !== 'function') throw new Error('pre-flight: __heartbeat.setState 不在');
if (typeof window.__heartbeat.start !== 'function') throw new Error('pre-flight: __heartbeat.start 不在');
if (typeof window.__heartbeat.stop !== 'function') throw new Error('pre-flight: __heartbeat.stop 不在');
if (typeof window.__heartbeat._buf !== 'function') throw new Error('pre-flight: __heartbeat._buf 不在');
if (typeof window.__heartbeat._reset !== 'function') throw new Error('pre-flight: __heartbeat._reset 不在');
"""


@unittest.skipUnless(_NODE, "node not installed; skipping behavior round-trip")
class TestHeartbeatTickNode(unittest.TestCase):
    def test_pre_flight_window_heartbeat_exposed(self):
        out = _node_eval_heartbeat(_STUB_PRELUDE, _PRE_FLIGHT + r"""
console.log(JSON.stringify({ok: true}));
""")
        self.assertEqual(out, {"ok": True})

    def test_online_bump_creates_spike(self):
        out = _node_eval_heartbeat(_STUB_PRELUDE, _PRE_FLIGHT + r"""
window.__heartbeat.start();
window.__heartbeat.bump();
flushFrames(15);
const buf = Array.from(window.__heartbeat._buf());
console.log(JSON.stringify({minV: Math.min.apply(null, buf), maxAbs: Math.max.apply(null, buf.map(Math.abs))}));
""")
        # online 時 __hbSpikeAmp = 1.0 で SPIKE_SHAPE 中の -9 が乗るので min < -5
        self.assertLess(out["minV"], -5)

    def test_reconnect_bump_amplitude_is_attenuated(self):
        out = _node_eval_heartbeat(_STUB_PRELUDE, _PRE_FLIGHT + r"""
window.__heartbeat.start();
window.__heartbeat.setState('reconnect');
window.__heartbeat.bump();
flushFrames(15);
const buf = Array.from(window.__heartbeat._buf());
console.log(JSON.stringify({maxAbs: Math.max.apply(null, buf.map(Math.abs))}));
""")
        # 0.3 倍 → SPIKE_SHAPE max abs 9 * 0.3 = 2.7 < 5
        self.assertLess(out["maxAbs"], 5)

    def test_offline_bump_is_suppressed(self):
        out = _node_eval_heartbeat(_STUB_PRELUDE, _PRE_FLIGHT + r"""
window.__heartbeat.start();
window.__heartbeat.setState('offline');
window.__heartbeat.bump();
flushFrames(15);
const buf = Array.from(window.__heartbeat._buf());
console.log(JSON.stringify({maxAbs: Math.max.apply(null, buf.map(Math.abs))}));
""")
        # offline では spike 抑制 + buf clear → 全要素 0
        self.assertLess(out["maxAbs"], 1)

    def test_state_transition_amplitude_no_leak(self):
        out = _node_eval_heartbeat(_STUB_PRELUDE, _PRE_FLIGHT + r"""
window.__heartbeat.start();
window.__heartbeat.setState('reconnect');
window.__heartbeat.setState('online');
window.__heartbeat.bump();
flushFrames(15);
const buf = Array.from(window.__heartbeat._buf());
console.log(JSON.stringify({maxAbs: Math.max.apply(null, buf.map(Math.abs))}));
""")
        # online に戻ったら振幅 1.0 → max abs >= 5 (SPIKE_SHAPE max abs 9)
        self.assertGreaterEqual(out["maxAbs"], 5)

    def test_idle_baseline_animates_when_no_spike(self):
        """idle 中 (spike 残量 0) で line が静止せず breathing wave で微小に動く。

        Issue #83 codex Round 2 P1: baseline を 0 固定にすると polyline が完全静止
        して acceptance criteria「アイドル 30 秒以上でも line が左→右に流れ続ける
        (= 凍ってない)」を満たさない。breathing 振幅 ~0.6px の sin wave で生存感を出す。
        offline / static (amp=0) では既存テストの flat 契約と整合させるため、
        breathing も __hbSpikeAmp スケール下に置く。
        """
        out = _node_eval_heartbeat(_STUB_PRELUDE, _PRE_FLIGHT + r"""
window.__heartbeat.start();
flushFrames(50);
const buf = Array.from(window.__heartbeat._buf());
console.log(JSON.stringify({
  maxAbs: Math.max.apply(null, buf.map(Math.abs)),
  allZero: buf.every(function (v) { return v === 0; }),
}));
""")
        self.assertFalse(out["allZero"], "idle baseline が all-0 で静止している (Round 2 P1 regression)")
        self.assertGreater(out["maxAbs"], 0.1, "idle breathing wave 振幅が小さすぎる")

    def test_resume_after_stop_does_not_compress_spike(self):
        """offline → online resume 後の bump() が spike full duration を出す。

        Issue #83 codex Round 2 P2: stopHeartbeat() で __hbLastTickMs / __hbAccumMs を
        クリアしないと、長い pause 後の resume で huge dt が catch-up cap=5 を踏んで
        spike 先頭が一気に消費されて compress 表示になる。
        """
        out = _node_eval_heartbeat(_STUB_PRELUDE, _PRE_FLIGHT + r"""
window.__heartbeat.start();
flushFrames(3);
window.__heartbeat.setState('offline');
_simTime += 10000;
window.__heartbeat.setState('online');
window.__heartbeat.bump();
flushFrames(15);
const buf = Array.from(window.__heartbeat._buf());
console.log(JSON.stringify({maxAbs: Math.max.apply(null, buf.map(Math.abs))}));
""")
        # stale timing state が漏れると先頭 5 sample が catch-up で消費されて
        # 残り 5 sample (= [2,4,3,1,0] の持続部) しか buf に残らない → max abs ~4。
        # 修正後は spike 全 10 sample が乗るので max abs = 9。
        self.assertGreaterEqual(out["maxAbs"], 8)

    def test_reduced_motion_bump_writes_sr_with_microtask_drain(self):
        """reduced-motion 環境で bump() 連発時に SR 通知が再発火する。

        textContent への代入を spy で履歴に積み、['', '更新を受信しました', '', '更新を受信しました']
        の並びを pin (= 一旦 '' を経由してから本文を書く設計を保証)。
        """
        prelude_with_sr_spy = r"""
globalThis.window = globalThis;
globalThis.document = globalThis.document || {};
let _fakePoly = { setAttribute() {}, getAttribute: () => '' };
let _fakeSvg = { dataset: {}, setAttribute() {}, getAttribute: () => '', querySelector: () => _fakePoly, hidden: false };
let _srHistory = [];
let _fakeSr = {
  set textContent(v) { _srHistory.push(v); },
  get textContent() { return _srHistory[_srHistory.length - 1] || ''; },
};
document.getElementById = (id) => id === 'heartbeat' ? _fakeSvg : id === 'heartbeatSr' ? _fakeSr : null;
let _rafQueue = []; let _rafId = 0;
window.requestAnimationFrame = (fn) => { _rafQueue.push({id: ++_rafId, fn}); return _rafId; };
window.cancelAnimationFrame = (id) => { _rafQueue = _rafQueue.filter(x => x.id !== id); };
window.matchMedia = (q) => ({ matches: true });  // reduced-motion ON
function flushFrames(n) { for (let i = 0; i < n; i++) { const item = _rafQueue.shift(); if (item) item.fn(); } }
"""
        out = _node_eval_heartbeat(prelude_with_sr_spy, _PRE_FLIGHT + r"""
(async () => {
  window.__heartbeat._reset();
  window.__heartbeat.bump();
  await Promise.resolve().then(() => Promise.resolve());
  window.__heartbeat.bump();
  await Promise.resolve().then(() => Promise.resolve());
  console.log(JSON.stringify({history: _srHistory}));
})();
""")
        self.assertEqual(
            out["history"],
            ['', '更新を受信しました', '', '更新を受信しました'],
        )
