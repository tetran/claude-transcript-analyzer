"""reports/_archive_loader.py

archive ディレクトリ (`~/.claude/transcript-analyzer/archive/*.jsonl.gz`) から
event を opt-in で読み込むための共通 loader (Issue #30 Phase B)。

`reports/summary.py` と `reports/export_html.py` の両方が import し、
`--include-archive` flag 経由で archive を merge して集計する。
dashboard/server.py は archive を読まない (仕様で 180 日固定) ため
このモジュールを import しない。

Public API:
- archive_read_lock(): context manager。block で SH を取って release まで保持。
  caller は hot tier + archive を 1 つの atomic snapshot として読みたいとき使う。
- iter_archive_events_unlocked(): lock を取らずに archive を iter。caller が
  archive_read_lock() 下で呼ぶ前提。
- load_archive_events(): backwards-compat な薄い wrapper (lock 取得 + iterate)。
"""
from __future__ import annotations

import contextlib
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


def _acquire_archive_read_lock() -> Optional[int]:
    """archive 読み取り用 LOCK_SH を **blocking** で取得する。

    設計判断 (codex P2 #1):
    旧実装は LOCK_NB で取れなければ archive を silent skip していたが、
    `--include-archive` を明示したユーザーに対して archive を 0 件として返すと
    全期間集計を silent に偽装することになる。CLI 起動の reports は hooks と
    違って `< 100ms` 制約が無く、archive job の LOCK_EX 保持はサブ秒で終わる
    (rewrite + state marker 書き込み程度) ため、blocking で **archive job 完了を
    待ってから一貫した状態で読む** 意味論に固定する。

    旧 codex P3 (archive job 中の二重カウント window) は LOCK_SH (blocking) でも
    解消される — EX 解除を待ってから読み始めるので、hot tier と archive の両方に
    同 event が transient で見える window 自体が closed になる。

    Returns:
        - None: lock 不要 / 取得不能 (fcntl 不在 / lock file 不在 / open 失敗) → そのまま読む
        - fd:  SH 取得成功 → 読み終わったら fd を release/close
    """
    if not _HAS_FCNTL:
        return None  # Windows / fcntl 不在 — 保護できないが進める
    lock_path = _resolve_lock_path()
    try:
        # codex 6th P3: O_RDWR | O_CREAT で create-on-open に統一。
        # 旧実装は `lock_path.exists()` で early return していたため、check と
        # archive_usage の lock file 作成 + LOCK_EX 取得の間に reader が unlocked
        # で archive を読む TOCTOU window があった。create-on-open でこの window を
        # 構造的に閉じる (lock file は archive_usage 側も同じ path を見るため
        # SH/EX が正しく coordinate する)。
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return None  # 保護できないが進める
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)  # blocking — EX 保持中なら release を待つ
    except OSError:
        # blocking 中の OSError は実運用ではほぼ起きない (signal 起因等の異常系)。
        # silent skip より「読みに行って失敗した」を選ぶ — fd は閉じて lock 取得を諦め、
        # 後段の glob 経路で archive を読みに行かせる (lock 無し読み出しは旧来挙動)。
        os.close(fd)
        return None
    return fd


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


@contextlib.contextmanager
def archive_read_lock() -> Iterator[None]:
    """archive 読み取り用 LOCK_SH を blocking で取得して保持する context manager。

    codex 5th review P2: caller (summary.py / export_html.py) が hot tier と
    archive を **同じ SH lock 下で読む** ことで、archive job の LOCK_EX と
    atomic snapshot semantics を実現する。

    使い方:
        with archive_read_lock():
            hot_events = read_hot_tier()
            archive_events = list(iter_archive_events_unlocked())
            # ↑ archive job がこの with の最中に走ることはないので
            #   hot と archive は consistent な snapshot
    """
    lock_fd = _acquire_archive_read_lock()
    try:
        yield
    finally:
        _release_archive_read_lock(lock_fd)


def iter_archive_events_unlocked(archive_dir: Path | None = None) -> Iterator[dict]:
    """archive_dir/*.jsonl.gz を順に iter して event を yield (**lock 取得なし**)。

    caller は `archive_read_lock()` で SH を保持中である前提。lock 無しで呼ぶと
    archive job と race して transient な部分書き込み状態を読む可能性がある。

    - `.tmp` 系は glob pattern で自動除外される (`*.jsonl.gz` が拾うのは完成形のみ)
    - archive_dir 不在時は空 iterator
    - JSON parse error 行は silent skip
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


def load_archive_events(archive_dir: Path | None = None) -> Iterator[dict]:
    """archive_dir/*.jsonl.gz を SH lock 下で iter する backwards-compat wrapper。

    archive 単体読み出しが目的の caller (旧 API) 向け。hot tier と組み合わせて
    atomic snapshot を取りたい caller は `archive_read_lock()` +
    `iter_archive_events_unlocked()` を直接使うこと (codex 5th P2)。
    """
    with archive_read_lock():
        yield from iter_archive_events_unlocked(archive_dir)
