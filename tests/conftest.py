"""tests/conftest.py

pytest 共有 fixture 定義。
"""
import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


@pytest.fixture(name="archive_module")
def _archive_module_fixture(monkeypatch, tmp_path):
    """archive_usage モジュールを env 隔離した状態で reload。

    test_archive_usage.py / test_archive_state.py で共有 (Issue #30 / PR #43)。
    """
    monkeypatch.setenv("USAGE_JSONL", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("ARCHIVE_STATE_FILE", str(tmp_path / ".archive_state.json"))
    monkeypatch.setenv("USAGE_JSONL_LOCK", str(tmp_path / "usage.jsonl.lock"))
    monkeypatch.setenv("HEALTH_ALERTS_JSONL", str(tmp_path / "health_alerts.jsonl"))
    sys.modules.pop("archive_usage", None)
    import archive_usage
    importlib.reload(archive_usage)
    return archive_usage
