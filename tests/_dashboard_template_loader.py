"""tests/_dashboard_template_loader.py — 分割後 (Issue #67) の組立済 template を返す helper。

分割前は各 template テストが `dashboard/template.html` を直接 read_text していたが、
分割後は `dashboard/server.py` の `_build_html_template()` が起動時 concat する文字列が
真の template になる。本 helper はその文字列を 1 度だけ build してキャッシュし、
構造アサーション系テストから共有する。

実装は dashboard モジュールを importlib で load して `_HTML_TEMPLATE` を読むだけ。
これにより manifest (CSS / JS の連結順) が dashboard 本体と単一の source of truth に保たれる。
"""
from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path

_DASHBOARD_PATH = Path(__file__).parent.parent / "dashboard" / "server.py"


@lru_cache(maxsize=1)
def load_assembled_template() -> str:
    """dashboard モジュールを load し、その `_HTML_TEMPLATE` を返す。

    dashboard 側の top-level は env 変数からパス定数を組み立てるだけで、
    USAGE_JSONL 等の実体ファイルは触らない。よって env 未設定でも import 可能。
    別 module 名で load することで、テスト本体が要求する env をパッチした
    `dashboard_server` モジュールと衝突しない。
    """
    # dashboard を import すると _HTML_TEMPLATE = _build_html_template() が走る
    spec = importlib.util.spec_from_file_location("_dashboard_for_template_load", _DASHBOARD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_dashboard_for_template_load"] = mod
    spec.loader.exec_module(mod)
    return mod._HTML_TEMPLATE  # pylint: disable=protected-access
