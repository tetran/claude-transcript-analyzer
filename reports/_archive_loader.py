"""reports/_archive_loader.py

archive ディレクトリ (`~/.claude/transcript-analyzer/archive/*.jsonl.gz`) から
event を opt-in で読み込むための共通 loader (Issue #30 Phase B)。

`reports/summary.py` と `reports/export_html.py` の両方が import し、
`--include-archive` flag 経由で archive を merge して集計する。
dashboard/server.py は archive を読まない (仕様で 180 日固定) ため
このモジュールを import しない。
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Iterator

_DEFAULT_ARCHIVE_DIR = (
    Path.home() / ".claude" / "transcript-analyzer" / "archive"
)


def resolve_archive_dir() -> Path:
    """ARCHIVE_DIR env or default を返す (archive_usage.py と同じ規約)。"""
    env_value = os.environ.get("ARCHIVE_DIR")
    if env_value:
        return Path(env_value)
    return _DEFAULT_ARCHIVE_DIR


def load_archive_events(archive_dir: Path | None = None) -> Iterator[dict]:
    """archive_dir/*.jsonl.gz を順に iter して event を yield。

    - `.tmp` 系は glob pattern で自動除外される (`*.jsonl.gz` が拾うのは完成形のみ)
    - archive_dir 不在時は空 iterator
    - JSON parse error 行は silent skip (人手修復に委ねる、人間の jq で読める前提を維持)
    """
    if archive_dir is None:
        archive_dir = resolve_archive_dir()
    if not archive_dir.exists():
        return
    for path in sorted(archive_dir.glob("*.jsonl.gz")):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
