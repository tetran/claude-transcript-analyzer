"""tests/test_analyzer_package_imports.py — analyzer/ パッケージ構造のリグレッションガード (Issue #121)。

- analyzer/ 配下の全モジュールが副作用なく import できる (絶対 import の健全性)
- analyzer/ 配下に sys.path ハック (sys.path.insert / sys.path.append) が 1 つも無い
- repo root から共有モジュール (cost_metrics / server_registry / subagent_metrics) が
  消えている (移設完了の検証)
"""
import importlib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ANALYZER_DIR = _REPO_ROOT / "analyzer"


def _analyzer_module_names() -> list[str]:
    """analyzer/ 配下の全 .py を dotted module 名に変換して返す。"""
    names = []
    for py in sorted(_ANALYZER_DIR.rglob("*.py")):
        rel = py.relative_to(_REPO_ROOT).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(parts))
    return names


class TestAnalyzerPackageImports:
    def test_all_analyzer_modules_import(self):
        """analyzer/ 配下の全モジュールが import エラー無く読める。"""
        names = _analyzer_module_names()
        assert "analyzer" in names and len(names) >= 8, f"想定モジュール数に満たない: {names}"
        for name in names:
            importlib.import_module(name)

    def test_no_syspath_hack_in_analyzer(self):
        """analyzer/ パッケージ内部に sys.path 操作が無い (絶対 import のみ)。"""
        for py in _ANALYZER_DIR.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            assert "sys.path.insert" not in text, f"{py} に sys.path.insert がある"
            assert "sys.path.append" not in text, f"{py} に sys.path.append がある"

    def test_root_shared_modules_removed(self):
        """repo root 直下から共有 .py モジュールが一掃されている。"""
        for name in ("cost_metrics.py", "server_registry.py", "subagent_metrics.py"):
            assert not (_REPO_ROOT / name).exists(), f"repo root に {name} が残っている"
