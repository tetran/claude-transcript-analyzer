#!/usr/bin/env python3
"""hooks/launch_archive.py — Issue #30 Phase C: archive job の自動起動 launcher

Claude Code Hook (SessionStart) から呼ばれ、`scripts/archive_usage.py` を
fork-and-detach で起動する **べき等な薄い launcher**。usage.jsonl には書かない。

判定フロー (< 100ms 既起動経路 / codex 6th P2 で horizon ベースに刷新):
1. ``ARCHIVE_STATE_FILE`` (default ``~/.claude/transcript-analyzer/.archive_state.json``)
   を読む
2. ``last_archived_month`` が前月 (UTC) 以降ならカバー済 → skip
3. ``last_archivable_horizon`` が現在の archivable horizon と同じ or それ以降 →
   archive_usage が同じ horizon を既に観測済みで no-op だったので skip
   (R2 の「対象なし状態の毎セッション spawn 防止」を保つ)
4. それ以外 (state 不在 / 壊れた JSON / horizon 未記録 / horizon 古い) → spawn

旧 `last_run_at == this_month` skip は廃止。retention boundary が月末を跨いだ
mid-month で立つ archive 対象を次の calendar 月まで遅延させる bug があった
(codex 6th review P2)。

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


_DEFAULT_RETENTION_DAYS = 180

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
        if not 1 <= m <= 12:
            return None
        return (y, m)
    except (ValueError, AttributeError):
        return None


def _read_state_dict(state_file: Path) -> Optional[dict]:
    """state file を dict として読む。不在 / 壊れた JSON / 非 dict → None。"""
    if not state_file.exists():
        return None
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _read_state_last_archived(state_file: Path) -> Optional[tuple[int, int]]:
    """state file から ``last_archived_month`` を ``(year, month)`` で取得。

    不在 / 壊れた JSON / 期待外型 / 月文字列不正 → ``None`` (= 「未実行扱い」)。
    """
    state = _read_state_dict(state_file)
    if state is None:
        return None
    last = state.get("last_archived_month")
    if not isinstance(last, str):
        return None
    return _parse_month_string(last)


def _read_state_last_archivable_horizon(state_file: Path) -> Optional[tuple[int, int]]:
    """state file から ``last_archivable_horizon`` を ``(year, month)`` で取得。"""
    state = _read_state_dict(state_file)
    if state is None:
        return None
    raw = state.get("last_archivable_horizon")
    if not isinstance(raw, str):
        return None
    return _parse_month_string(raw)


def _calculate_archivable_horizon(now: datetime, retention_days: int) -> tuple[int, int]:
    """現在の archivable horizon (= archive 対象として立つ最大月) を返す。

    archive_usage.py:_calculate_archivable_horizon と同一規約 (= cutoff の前月)。
    ここで二重実装している理由: launcher は archive_usage を import しない
    (依存関係を最小化して < 100ms 既起動経路を死守)。式が単純 (cutoff = now -
    retention_days, return previous_month(cutoff))なので二重化のコストは小さい。
    """
    cutoff = now - timedelta(days=retention_days)
    return _previous_month(cutoff.year, cutoff.month)


def _resolve_retention_days() -> int:
    """``USAGE_RETENTION_DAYS`` env を読む。不正値は default にフォールバック。"""
    raw = os.environ.get("USAGE_RETENTION_DAYS")
    if raw is None:
        return _DEFAULT_RETENTION_DAYS
    try:
        value = int(raw)
        if value <= 0:
            return _DEFAULT_RETENTION_DAYS
        return value
    except ValueError:
        return _DEFAULT_RETENTION_DAYS


def _needs_archive(
    state_file: Path,
    now: datetime,
    retention_days: Optional[int] = None,
) -> bool:
    """state を読み、archive job 起動が必要かを判定する (codex 6th P2 で刷新)。

    判定:
    - state 不在 / 壊れ / 不正 → True (未実行扱いで spawn)
    - last_archived_month >= 前月 (UTC) → False (skip / 通常運用 fast path)
    - last_archivable_horizon が現在 horizon 以降 → False
      (archive_usage が同じ horizon を観測済みで no-op だった = R2 無限 spawn 防止)
    - 上記以外 → True (新 horizon に追従 / 旧 schema state は保守的 spawn)

    旧 `last_run_at == this_month` skip は **廃止**。retention boundary が月末を
    跨いだ mid-month で archive 対象が立つケース (codex 6th P2) を遅延させていた。
    """
    state = _read_state_dict(state_file)
    if state is None:
        return True

    if retention_days is None:
        retention_days = _resolve_retention_days()

    last_archived = _read_state_last_archived(state_file)
    if last_archived is not None:
        prev_y, prev_m = _previous_month(now.year, now.month)
        if last_archived >= (prev_y, prev_m):
            return False

    # codex 6th P2: archivable horizon 比較で "同じ horizon を既に観測済みなら skip"。
    # 旧実装の last_run_at calendar 月 skip は同月内 horizon advance を見逃していた。
    current_horizon = _calculate_archivable_horizon(now, retention_days)
    last_horizon = _read_state_last_archivable_horizon(state_file)
    if last_horizon is not None and last_horizon >= current_horizon:
        return False
    return True


def _spawn_archive_job() -> Optional[object]:
    """archive_usage.py を fork-and-detach で起動。失敗時 None。

    Issue #44: 旧実装は Windows で archive_usage.py が POSIX fcntl 不在で state を
    書かずに即 exit する仕様だったため、spawn し続けると永久 spawn ループになる
    のを構造的に skip していた。`hooks/_lock` で msvcrt.locking 経路が入ったため
    Windows でも archive_usage は state を更新できる → skip 撤廃。
    """
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
