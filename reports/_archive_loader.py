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
from typing import Iterator, Optional

try:
    import fcntl  # type: ignore[import]
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover (Windows のみ)
    fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False

_DEFAULT_DATA_DIR = Path.home() / ".claude" / "transcript-analyzer"
_DEFAULT_DATA_FILE = _DEFAULT_DATA_DIR / "usage.jsonl"
_DEFAULT_ARCHIVE_DIR = _DEFAULT_DATA_DIR / "archive"


def resolve_archive_dir() -> Path:
    """ARCHIVE_DIR env / USAGE_JSONL parent / default を返す。

    archive_usage.py:_resolve_paths と同じ規約:
    1. ARCHIVE_DIR が明示されていればそれ
    2. USAGE_JSONL が指定されていれば <parent>/archive
    3. どちらも未設定 → ~/.claude/transcript-analyzer/archive
    """
    env_archive = os.environ.get("ARCHIVE_DIR")
    if env_archive:
        return Path(env_archive)
    env_usage = os.environ.get("USAGE_JSONL")
    if env_usage:
        return Path(env_usage).parent / "archive"
    return _DEFAULT_ARCHIVE_DIR


def _resolve_lock_path() -> Path:
    """archive lock file path を archive_usage.py と同じ規約で返す。

    1. USAGE_JSONL_LOCK 明示 → そのまま
    2. USAGE_JSONL 指定 → <USAGE_JSONL>.lock
    3. どちらも未設定 → <DEFAULT_DATA_FILE>.lock
    """
    env_lock = os.environ.get("USAGE_JSONL_LOCK")
    if env_lock:
        return Path(env_lock)
    env_usage = os.environ.get("USAGE_JSONL")
    if env_usage:
        return Path(env_usage + ".lock")
    return Path(str(_DEFAULT_DATA_FILE) + ".lock")


def _try_acquire_archive_read_lock() -> tuple[bool, Optional[int]]:
    """archive 読み取り用 LOCK_SH | LOCK_NB を non-blocking で取得試行。

    codex P3 対策: archive_usage.py の run_archive 中は archive .gz が
    rewrite される前に既に書かれており、reader が割り込むと hot tier と
    archive の両方に同 event が見える二重カウント window がある。
    reader 側で LOCK_SH を試み、取れない (= archive job が LOCK_EX 保持中) なら
    archive を読まずスキップする。

    Returns:
        (should_read, fd_to_release)
        - (True, None): lock 不要 (fcntl 不在 / lock file 不在 / open 失敗) → 普通に読む
        - (True, fd): SH 取得成功 → 読み終わったら fd を release/close
        - (False, None): LOCK_EX 保持中 → archive を読まない
    """
    if not _HAS_FCNTL:
        return True, None  # Windows / fcntl 不在 — 保護できないが進める
    lock_path = _resolve_lock_path()
    if not lock_path.exists():
        # archive_usage.py が一度も走ってない → 競合相手不在で読んで OK
        return True, None
    try:
        fd = os.open(str(lock_path), os.O_RDONLY)
    except OSError:
        return True, None  # 保護できないが進める
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        os.close(fd)
        return False, None  # archive job が LOCK_EX 保持中 → archive を読まない
    return True, fd


def _release_archive_read_lock(fd: Optional[int]) -> None:
    if fd is None or not _HAS_FCNTL:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def load_archive_events(archive_dir: Path | None = None) -> Iterator[dict]:
    """archive_dir/*.jsonl.gz を順に iter して event を yield。

    - `.tmp` 系は glob pattern で自動除外される (`*.jsonl.gz` が拾うのは完成形のみ)
    - archive_dir 不在時は空 iterator
    - JSON parse error 行は silent skip (人手修復に委ねる、人間の jq で読める前提を維持)
    - codex P3: archive job が LOCK_EX 保持中なら archive を読まずスキップ
      (hot tier との二重カウントを構造的に防ぐ)
    """
    if archive_dir is None:
        archive_dir = resolve_archive_dir()
    if not archive_dir.exists():
        return
    should_read, lock_fd = _try_acquire_archive_read_lock()
    if not should_read:
        return
    try:
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
    finally:
        _release_archive_read_lock(lock_fd)
