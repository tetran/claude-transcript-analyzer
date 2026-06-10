"""dashboard/render.py — テンプレート組み立て + 静的 HTML レンダリング (Issue #123 Phase 1).

dashboard/server.py から区画 D を切り出した。既存の `dashboard/template/`
ディレクトリとモジュール名が衝突するため `render.py` とする (`template.py` 不可)。
`_HTML_TEMPLATE` は import 時に `template/` 配下を concat する副作用を持つ。
"""
import json
from pathlib import Path


def render_static_html(data: dict) -> str:
    """データをインライン埋め込みしたスタンドアロン HTML を返す。

    `<script>` ブロック内の JSON literal は HTML script-data-state パーサーから見て
    `</` で始まるシーケンスを含むと早期終了させられうる (`</script>` 直接抜けの他、
    `<!--` で script-data-escaped state に入った後 `</script>` で抜けるパスもある)。
    `</` 全般を `<\\/` に escape することで両方のパスを構造的に塞ぐ。
    `\\/` は RFC 8259 で許可された JSON escape のため `JSON.parse` ラウンドトリップ
    でブラウザには元の `</` として復元される。

    `<!--` 単体は escape 不要: `</` 全般 escape の時点で script-data-escaped 経路の
    `</script>` 抜け道は塞がれており、`<!--` 自体は構造化データの一部として
    そのまま通せる (claude[bot] PR#27 review #1 / 再レビュー対応)。
    """
    json_str = json.dumps(data, ensure_ascii=False).replace("</", r"<\/")
    inline = f'<script>window.__DATA__ = {json_str};</script>\n'
    return _HTML_TEMPLATE.replace('</head>', inline + '</head>', 1)


# `template/` 配下に分割した shell + styles + scripts を起動時に concat する (Issue #67)。
# `_HTML_TEMPLATE` は外部から見たら従来通り「単一の HTML 文字列」契約を維持しているので、
# `render_static_html` の `</head>` replace 戦略や export_html 経路は無改修で動く。
#
# 連結順は CSS のカスケード順 / JS の closure 内での宣言順を再現するため厳密に決まっている。
# 個別ファイルへの分割境界は元 template.html のセクションコメント (例: `/* ---------- KPI ---------- */`)
# を踏襲。新しいセクションを追加するときはこの list に追記し、
# 既存セクション内へ追記するときは該当ファイルを開いて編集する。
_TEMPLATE_DIR = Path(__file__).resolve().parent / "template"
_CSS_FILES = (
    "00_base.css",          # root vars / reset / body / .app
    "10_components.css",    # header / live badge / KPI / panel / two-up / ranking / spark / projects / footer
    "15_heartbeat.css",     # live heartbeat sparkline (Issue #83)
    "20_help_tooltip.css",  # help button + data tooltip (graph data points)
    "30_pages.css",         # multipage shell (Issue #57)
    "40_patterns.css",      # hourly heatmap + skill cooccurrence + project×skill (Issue #58/59)
    "50_quality.css",       # subagent percentile/failure + permission breakdown + compact density (Issue #60/61)
    "55_sessions.css",      # Sessions table + KPI 4 枚 + cost meter / model chip / tier chip (Issue #103)
    "60_surface.css",       # Surface 3 panel + tooltip border colors (Issue #74)
)
_MAIN_JS_FILES = (
    "05_period.js",               # period toggle closure + getCurrentPeriod / wirePeriodToggle (Issue #85)
    "10_helpers.js",              # esc / fmtN / pad / STATUS_LABEL / setConnStatus
    "15_heartbeat.js",            # live heartbeat sparkline (Issue #83)
    "20_load_and_render.js",      # async loadAndRender (KPI / ranking / sparkline / projects)
    "25_live_diff.js",            # live mode 差分 highlight + toast (Issue #69)
    "30_renderers_patterns.js",   # heatmap / cooccurrence / project×skill matrix renderers
    "40_renderers_quality.js",    # subagent percentile / failure / permission / compact renderers
    "45_renderers_sessions.js",   # Sessions table renderer + KPI 4 cards (Issue #103)
    "50_renderers_surface.js",    # Surface invocation / lifecycle / hibernating + fmtDur
    "60_hashchange_listener.js",  # hashchange → loadAndRender 再実行 (Issue #58 Q2)
    "70_init_eventsource.js",     # 初回描画 + EventSource (live refresh)
    "80_help_popup.js",           # help popover behavior (click / Escape / resize)
    "90_data_tooltip.js",         # data tooltip ([data-tip] elements)
)


def _concat_main_js() -> str:
    """`_MAIN_JS_FILES` を順に読んで 1 つの JS bundle 文字列に連結する.

    `_concat_main_js()` is a test seam exposed for `tests/test_dashboard_period_toggle.py`;
    not a public API.

    `"".join(...)` (= no separator) で連結する: byte-identical to pre-refactor `_HTML_TEMPLATE`;
    do not introduce separators (改行など) — assembled `_HTML_TEMPLATE` の bytes が変わって
    `EXPECTED_TEMPLATE_SHA256` の drift detection が壊れる。
    """
    return "".join(
        (_TEMPLATE_DIR / "scripts" / name).read_text(encoding="utf-8")
        for name in _MAIN_JS_FILES
    )


def _build_html_template() -> str:
    """`template/` 配下を起動時に 1 度だけ concat して `_HTML_TEMPLATE` を作る。

    shell.html に置いた `__INCLUDE_*\\n` センチネルを、styles / scripts の concat 結果で
    line-aligned に置換する (置換は trailing `\\n` ごと吸収するので前後の改行が二重化しない)。
    """
    styles = "".join((_TEMPLATE_DIR / "styles" / name).read_text(encoding="utf-8") for name in _CSS_FILES)
    router_js = (_TEMPLATE_DIR / "scripts" / "00_router.js").read_text(encoding="utf-8")
    main_js = _concat_main_js()
    shell = (_TEMPLATE_DIR / "shell.html").read_text(encoding="utf-8")
    return (shell
            .replace("__INCLUDE_STYLES__\n", styles)
            .replace("__INCLUDE_ROUTER_JS__\n", router_js)
            .replace("__INCLUDE_MAIN_JS__\n", main_js))


_HTML_TEMPLATE = _build_html_template()
