"""tests/test_export_html_include_archive.py

reports/export_html.py の `--include-archive` flag (Issue #30 Phase B) のテスト。

カバー範囲:
- flag なしで生成された HTML の window.__DATA__ は hot tier のみ
- flag ありで生成された HTML の window.__DATA__ は archive 込み
"""
import gzip
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
EXPORT_HTML = PROJECT_ROOT / "reports" / "export_html.py"


def _write_hot(tmp_path: Path, events: list[dict]) -> None:
    p = tmp_path / "usage.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _write_archive(tmp_path: Path, month: str, events: list[dict]) -> None:
    d = tmp_path / "archive"
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / f"{month}.jsonl.gz", "wt", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _extract_total_events(html: str) -> int:
    """window.__DATA__ の total_events を取り出す."""
    m = re.search(r"window\.__DATA__\s*=\s*(\{.*?\});", html, re.DOTALL)
    assert m, "window.__DATA__ が HTML に埋め込まれていない"
    raw = m.group(1).replace(r"<\/", "</")
    data = json.loads(raw)
    return data["total_events"]


def _run_export(tmp_path: Path, *, include_archive: bool) -> Path:
    output = tmp_path / "report.html"
    env = os.environ.copy()
    env.update(
        {
            "USAGE_JSONL": str(tmp_path / "usage.jsonl"),
            "ARCHIVE_DIR": str(tmp_path / "archive"),
            "HEALTH_ALERTS_JSONL": str(tmp_path / "health_alerts.jsonl"),
        }
    )
    args = [sys.executable, str(EXPORT_HTML), "--output", str(output)]
    if include_archive:
        args.append("--include-archive")
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    return output


class TestExportHtmlIncludeArchive:
    def test_default_excludes_archive(self, tmp_path):
        _write_hot(
            tmp_path,
            [{"event_type": "skill_tool", "skill": "/recent", "timestamp": "2026-04-20T00:00:00+00:00", "session_id": "s", "tool_use_id": "t1"}],
        )
        _write_archive(
            tmp_path,
            "2025-08",
            [{"event_type": "skill_tool", "skill": "/old", "timestamp": "2025-08-01T00:00:00+00:00", "session_id": "s", "tool_use_id": "t_old"}],
        )

        output = _run_export(tmp_path, include_archive=False)
        total = _extract_total_events(output.read_text())
        assert total == 1

    def test_include_archive_flag_merges_both(self, tmp_path):
        _write_hot(
            tmp_path,
            [{"event_type": "skill_tool", "skill": "/recent", "timestamp": "2026-04-20T00:00:00+00:00", "session_id": "s", "tool_use_id": "t1"}],
        )
        _write_archive(
            tmp_path,
            "2025-08",
            [{"event_type": "skill_tool", "skill": "/old", "timestamp": "2025-08-01T00:00:00+00:00", "session_id": "s", "tool_use_id": "t_old"}],
        )

        output = _run_export(tmp_path, include_archive=True)
        total = _extract_total_events(output.read_text())
        assert total == 2
