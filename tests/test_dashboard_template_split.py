"""tests/test_dashboard_template_split.py — Issue #67: テンプレート分割のための smoke test

`dashboard/template.html` 一枚岩を `dashboard/template/` 配下の shell.html + styles/
+ scripts/ に分割した後も、サーバーが起動時に concat する `_HTML_TEMPLATE`
文字列が **byte 単位で同一** であることを保証する。

byte 等価のチェックは sha256 で行う。期待値は分割前の `dashboard/template.html`
の hash を 1 度キャプチャして固定した値。分割後の build_template() が
同一 hash を再現できれば、CSS/JS の損失や順序ミスを完全に検知できる。

副次的に、`_HTML_TEMPLATE` が運用上必須とする DOM ID / セクション構造が
保たれていることも assert する（hash が将来意図的に更新されるときの
セーフティネット）。
"""
# pylint: disable=line-too-long
import hashlib
import importlib.util
import os
from pathlib import Path

_DASHBOARD_PATH = Path(__file__).parent.parent / "dashboard" / "server.py"

# 分割前 (v0.7.0 時点) の dashboard/template.html の sha256。
# 分割後の `_HTML_TEMPLATE` がこれと一致することで、CSS/JS の concat 順や
# 改行の取り扱いを含めた byte 等価性を保証する。
#
# 意図的な template 変更時は新 hash に更新する (docstring 参照)。
# 履歴:
#   - 39ad755c...: v0.7.0 / Issue #67 split 直後
#   - 7538e22b...: Issue #65 / local TZ 化で 10_helpers.js + 20_load_and_render.js 改修
#   - f27e07c7...: Issue #65 fix-up / formatLocalTimestamp に falsy ガード追加
#   - e7440528...: Issue #69 / live diff highlight + toast (25_live_diff.js 追加, shell.html / 10_components.css / 20_load_and_render.js / _MAIN_JS_FILES 改修)
#   - 4feb318f...: Issue #69 fix-up / KPI tile に id="' + g.id + '" を追加 (applyHighlights getElementById 命中)
#   - f53d0dab...: Issue #69 UX 調整 / toast を横方向中央寄せ + coral 系 color に変更
#   - a4885a23...: Issue #69 UX 調整 / toast 表示時間を 4s → 6s に延長
#   - f7bcb6b0...: Issue #69 UX 調整 / toast fade-out transition を 240ms 完走させる二段 timer 設計
#   - 42d23915...: Issue #69 UX 調整 / 表示中上書き時の slide-in 再生 + 表示時間を 6s → 4s に戻す
#   - 2964f3e9...: Issue #69 UX 調整 / requestAnimationFrame で frame 分割 (style 変更 collapse 回避)
#   - e787b78b...: Issue #69 UX 調整 / CSS animation (@keyframes toast-in/out) に切替 (CSS transition 方式は実機で再生されない問題への対処)
EXPECTED_TEMPLATE_SHA256 = "e787b78b9d4bdf106972f9675dc3f033bf55dbca2c0370e1def44a9119c6b4be"


def _load_dashboard_module(tmp_path: Path):
    """テスト用の minimal env で dashboard モジュールを読み込む。"""
    usage_jsonl = tmp_path / "usage.jsonl"
    usage_jsonl.write_text("", encoding="utf-8")
    os.environ["USAGE_JSONL"] = str(usage_jsonl)
    try:
        spec = importlib.util.spec_from_file_location("dashboard_server_split", _DASHBOARD_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del os.environ["USAGE_JSONL"]
    return mod


def test_html_template_byte_equivalent_to_pre_split_snapshot(tmp_path):
    """`_HTML_TEMPLATE` の sha256 が分割前と一致することを assert する。

    分割は loss-less であるべき。誤って改行を増減したり concat 順を間違えたら
    この assertion で即座に検知される。意図的な template 変更の際は
    `EXPECTED_TEMPLATE_SHA256` を新しい hash に更新する。
    """
    mod = _load_dashboard_module(tmp_path)
    actual_sha = hashlib.sha256(mod._HTML_TEMPLATE.encode("utf-8")).hexdigest()  # pylint: disable=protected-access
    assert actual_sha == EXPECTED_TEMPLATE_SHA256, (
        f"_HTML_TEMPLATE の sha256 が期待値と異なる。"
        f"\n  expected: {EXPECTED_TEMPLATE_SHA256}"
        f"\n  actual:   {actual_sha}"
        f"\n分割ファイルの concat 順 / 改行 / 末尾の \\n 取り扱いを確認すること。"
        f"\n意図的な変更なら EXPECTED_TEMPLATE_SHA256 を更新する。"
    )


def test_html_template_contains_critical_dom_anchors(tmp_path):
    """JS が依存する DOM ID / セクション anchor が抜け落ちていないことを確認する。

    sha256 等価チェックの方が強いが、hash 更新時にも構造的不変条件を
    別レイヤーで持っておく安全網。
    """
    mod = _load_dashboard_module(tmp_path)
    html = mod._HTML_TEMPLATE  # pylint: disable=protected-access

    # 4 ページ section
    for page in ("overview", "patterns", "quality", "surface"):
        assert f'data-page="{page}"' in html, f"page section '{page}' が消えている"

    # JS の getElementById / querySelector が参照する主要 ID
    for dom_id in (
        "kpiRow", "skillBody", "subBody", "skillSub", "subSub",
        "ledeEvents", "ledeDays", "ledeProjects",
        "stack", "stackLegend", "projSub",
        "lastRx", "sessVal", "connStatus",
        "dataTooltip",
    ):
        assert f'id="{dom_id}"' in html, f"DOM id '{dom_id}' が消えている"

    # Hash router の HASH_TO_PAGE と data-page-link の整合
    assert 'data-page-link="overview"' in html
    assert 'data-page-link="patterns"' in html
    assert 'data-page-link="quality"' in html
    assert 'data-page-link="surface"' in html


def test_html_template_tag_balance(tmp_path):
    """`<style>` / `</style>` / `<script>` / `</script>` が偶数 (= 開閉対) で揃っていること。

    分割→concat の過程でタグを取りこぼしていないかを構造的に確認する。
    """
    mod = _load_dashboard_module(tmp_path)
    html = mod._HTML_TEMPLATE  # pylint: disable=protected-access

    # 元 template は <style> 1 ペア + <script> 2 ペア
    assert html.count("<style>") == 1
    assert html.count("</style>") == 1
    assert html.count("<script>") == 2
    assert html.count("</script>") == 2

    # IIFE wrapper が main script で 1 つだけ存在
    assert html.count("(async function(){") == 1
    assert html.count("})();") >= 2  # router IIFE + main IIFE
