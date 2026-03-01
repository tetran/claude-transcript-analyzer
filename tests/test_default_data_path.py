"""tests/test_default_data_path.py

各スクリプトのデフォルトデータパスが
~/.claude/transcript-analyzer/ を指しとることを検証するテスト。

USAGE_JSONL / HEALTH_ALERTS_JSONL 環境変数を設定せずに
_DEFAULT_PATH 等を検査する。
"""
import importlib
import os
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
EXPECTED_DIR = Path.home() / ".claude" / "transcript-analyzer"


def _reload_module(module_name: str, module_path: str) -> types.ModuleType:
    """環境変数なしでモジュールを再ロードして返す。"""
    # 既存キャッシュを削除
    for key in list(sys.modules.keys()):
        if key == module_name or key.startswith(module_name + "."):
            del sys.modules[key]

    env_backup = {}
    for var in ("USAGE_JSONL", "HEALTH_ALERTS_JSONL"):
        if var in os.environ:
            env_backup[var] = os.environ.pop(var)

    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        os.environ.update(env_backup)


class TestRecordSkillDefaultPath:
    def test_default_path_points_to_transcript_analyzer(self):
        mod = _reload_module(
            "record_skill",
            str(PROJECT_ROOT / "hooks" / "record_skill.py"),
        )
        assert mod._DEFAULT_PATH == EXPECTED_DIR / "usage.jsonl", (
            f"record_skill._DEFAULT_PATH が期待値と違う: {mod._DEFAULT_PATH}"
        )


class TestRecordSubagentDefaultPath:
    def test_default_path_points_to_transcript_analyzer(self):
        mod = _reload_module(
            "record_subagent",
            str(PROJECT_ROOT / "hooks" / "record_subagent.py"),
        )
        assert mod._DEFAULT_PATH == EXPECTED_DIR / "usage.jsonl", (
            f"record_subagent._DEFAULT_PATH が期待値と違う: {mod._DEFAULT_PATH}"
        )


class TestVerifySessionDefaultPaths:
    def test_default_data_file_points_to_transcript_analyzer(self):
        mod = _reload_module(
            "verify_session",
            str(PROJECT_ROOT / "hooks" / "verify_session.py"),
        )
        assert mod._DEFAULT_DATA_FILE == EXPECTED_DIR / "usage.jsonl", (
            f"verify_session._DEFAULT_DATA_FILE が期待値と違う: {mod._DEFAULT_DATA_FILE}"
        )

    def test_default_alerts_file_points_to_transcript_analyzer(self):
        mod = _reload_module(
            "verify_session",
            str(PROJECT_ROOT / "hooks" / "verify_session.py"),
        )
        assert mod._DEFAULT_ALERTS_FILE == EXPECTED_DIR / "health_alerts.jsonl", (
            f"verify_session._DEFAULT_ALERTS_FILE が期待値と違う: {mod._DEFAULT_ALERTS_FILE}"
        )


class TestDashboardDefaultPaths:
    def test_default_path_points_to_transcript_analyzer(self):
        mod = _reload_module(
            "server",
            str(PROJECT_ROOT / "dashboard" / "server.py"),
        )
        assert mod._DEFAULT_PATH == EXPECTED_DIR / "usage.jsonl", (
            f"dashboard/server._DEFAULT_PATH が期待値と違う: {mod._DEFAULT_PATH}"
        )

    def test_default_alerts_path_points_to_transcript_analyzer(self):
        mod = _reload_module(
            "server",
            str(PROJECT_ROOT / "dashboard" / "server.py"),
        )
        assert mod._DEFAULT_ALERTS_PATH == EXPECTED_DIR / "health_alerts.jsonl", (
            f"dashboard/server._DEFAULT_ALERTS_PATH が期待値と違う: {mod._DEFAULT_ALERTS_PATH}"
        )


class TestSummaryDefaultPath:
    def test_default_path_points_to_transcript_analyzer(self):
        mod = _reload_module(
            "summary",
            str(PROJECT_ROOT / "reports" / "summary.py"),
        )
        assert mod._DEFAULT_PATH == EXPECTED_DIR / "usage.jsonl", (
            f"reports/summary._DEFAULT_PATH が期待値と違う: {mod._DEFAULT_PATH}"
        )


class TestRescanTranscriptsDefaultPath:
    def test_default_data_file_points_to_transcript_analyzer(self):
        mod = _reload_module(
            "rescan_transcripts",
            str(PROJECT_ROOT / "scripts" / "rescan_transcripts.py"),
        )
        assert mod._DEFAULT_DATA_FILE == EXPECTED_DIR / "usage.jsonl", (
            f"rescan_transcripts._DEFAULT_DATA_FILE が期待値と違う: {mod._DEFAULT_DATA_FILE}"
        )

