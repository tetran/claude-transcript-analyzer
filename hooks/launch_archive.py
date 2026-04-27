#!/usr/bin/env python3
"""hooks/launch_archive.py — Issue #30 Phase C: archive job の自動起動 launcher

Claude Code Hook (SessionStart) から呼ばれ、`scripts/archive_usage.py` を
fork-and-detach で起動する **べき等な薄い launcher**。usage.jsonl には書かない。

判定フロー (< 100ms 既起動経路):
1. ``ARCHIVE_STATE_FILE`` (default ``~/.claude/transcript-analyzer/.archive_state.json``)
   を読み、``last_archived_month`` を確認
2. ``last_archived_month`` が **前月 (UTC) 以前** なら archive job 起動が必要 → spawn
3. それ以外 (state 不在 / 壊れた JSON / 当月 / 前月) → silent exit 0

設計上の不変条件:
- どんな例外でも **silent exit 0** (Claude Code をブロックしない)
- 既起動検出経路は **< 100ms** (state file 1 個 read のみ)
- ``hooks/_launcher_common.spawn_detached()`` 経由で OS 別 detach (POSIX
  ``start_new_session=True`` / Windows ``DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP``)
- 子の archive_usage の log は子側で ``--log auto`` 経由で
  ``~/.claude/transcript-analyzer/archive.log`` に append (子のみが log を所有)
- 親 launcher の stdin/stdout/stderr は DEVNULL を子に渡し、親自身は何も書かない
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# `_launcher_common.spawn_detached` を import するため hooks/ を sys.path に追加
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _launcher_common import spawn_detached  # noqa: E402


_DEFAULT_STATE_FILE = (
    Path.home() / ".claude" / "transcript-analyzer" / ".archive_state.json"
)
STATE_FILE = Path(
    os.environ.get("ARCHIVE_STATE_FILE", str(_DEFAULT_STATE_FILE))
)

_ARCHIVE_USAGE_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "archive_usage.py"
)


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return (year - 1, 12)
    return (year, month - 1)


def _parse_month_string(s: str) -> Optional[tuple[int, int]]:
    """``YYYY-MM`` 形式を (year, month) に。形式不正は None。"""
    try:
        y_str, m_str = s.split("-")
        y, m = int(y_str), int(m_str)
        if not (1 <= m <= 12):
            return None
        return (y, m)
    except (ValueError, AttributeError):
        return None


def _read_state_last_archived(state_file: Path) -> Optional[tuple[int, int]]:
    """state file から ``last_archived_month`` を ``(year, month)`` で取得。

    不在 / 壊れた JSON / 期待外型 / 月文字列不正 → ``None`` (= 「未実行扱い」)。
    """
    if not state_file.exists():
        return None
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    last = raw.get("last_archived_month")
    if not isinstance(last, str):
        return None
    return _parse_month_string(last)


def _needs_archive(state_file: Path, now: datetime) -> bool:
    """state を読み、archive job 起動が必要かを判定する。

    - state 不在 / 壊れ / 不正 → True (未実行扱いで spawn)
    - last_archived_month >= 前月 (UTC) → False (skip)
    - last_archived_month < 前月 → True (古いので spawn)
    """
    last = _read_state_last_archived(state_file)
    if last is None:
        return True
    last_year, last_month = last

    prev_y, prev_m = _previous_month(now.year, now.month)
    # last >= 前月 なら skip
    return (last_year, last_month) < (prev_y, prev_m)


def _spawn_archive_job() -> Optional[object]:
    """archive_usage.py を fork-and-detach で起動。失敗時 None。"""
    if not _ARCHIVE_USAGE_SCRIPT.exists():
        return None
    return spawn_detached(
        [sys.executable, str(_ARCHIVE_USAGE_SCRIPT), "--log", "auto"],
    )


def main() -> int:
    """launcher のエントリポイント。常に 0 を返す (silent fail)。"""
    try:
        now = datetime.now(timezone.utc)
        if _needs_archive(STATE_FILE, now):
            _spawn_archive_job()
    except Exception:  # pylint: disable=broad-except
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
