"""hooks/_append.py

usage.jsonl への lock 付き append (Issue #30 Phase A1 + codex 5th review P1)。

並列耐性:
- archive job が LOCK_EX 中の場合、append 側は **blocking LOCK_SH** で release を待つ
- archive_usage.py は context manager で必ず LOCK_EX を release するため、待機は
  archive 実行時間で bounded (典型サブ秒、worst case で gzip rewrite 時間)
- fcntl OSError (signal 起因等の異常系) のみ silent drop + alert で observability 確保
- fcntl 不在環境 (Windows) では lock なしで append (POSIX O_APPEND の atomic 性に依拠)

設計判断 (codex 5th review P1):
- 旧実装は LOCK_SH | LOCK_NB × 5 retry × 100ms = 500ms upper-bound で、それを
  超えると `_record_drop_alert` 経由で event を silent drop していた。これは
  launch_archive auto-launcher (Phase C) が SessionStart で archive_usage を
  起動するようになったあと、長期運用された大きな usage.jsonl の gzip rewrite
  が 500ms を超える現実的なケースで append-only 不変条件を破っていた。
- 設計判断: hook latency vs data loss のトレードオフで data loss を回避する側を
  選択。非競合時はマイクロ秒、競合時のみ archive 完了まで blocking 待ちで bounded。
- reports/_archive_loader.py の blocking LOCK_SH (codex 4th P2 #1) と意味論統一。

設計上の不変条件:
- 非競合 hot path の overhead は ~µs オーダー (lock acquire + write + release)
- 競合時の wait は archive_usage.py の LOCK_EX hold duration で bounded
- record_*.py 全てがこのモジュール経由で append することで lock の取りこぼしを防ぐ
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import fcntl  # type: ignore[import]
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover (Windows のみ)
    fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False


_DEFAULT_ALERTS_PATH = (
    Path.home() / ".claude" / "transcript-analyzer" / "health_alerts.jsonl"
)


def _resolve_lock_path(data_file: Path, lock_path: Optional[Path] = None) -> Path:
    """lock file のパスを解決する。

    優先度: 明示引数 > USAGE_JSONL_LOCK env > <data_file>.lock fallback。
    """
    if lock_path is not None:
        return lock_path
    env_value = os.environ.get("USAGE_JSONL_LOCK")
    if env_value:
        return Path(env_value)
    return Path(str(data_file) + ".lock")


def _resolve_alerts_path() -> Path:
    """health_alerts.jsonl のパスを解決する (HEALTH_ALERTS_JSONL env or default)。"""
    env_value = os.environ.get("HEALTH_ALERTS_JSONL")
    if env_value:
        return Path(env_value)
    return _DEFAULT_ALERTS_PATH


def _record_drop_alert(event: dict) -> None:
    """archive 中で append が drop された event を health_alerts.jsonl に 1 行記録。

    記録自体の失敗は silent (元の hook を破壊しない)。
    """
    try:
        alerts_path = _resolve_alerts_path()
        alerts_path.parent.mkdir(parents=True, exist_ok=True)
        alert = {
            "alert": "append_skipped_due_to_archive_lock",
            "event_type": event.get("event_type", ""),
            "session_id": event.get("session_id", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with alerts_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(alert, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _write_event_line(data_file: Path, event: dict) -> None:
    # newline="\n" 固定で Windows text mode の \r\n 変換を抑止 (Issue #24 踏襲)。
    with data_file.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def append_event(
    data_file: Path,
    event: dict,
    *,
    lock_path: Optional[Path] = None,
) -> None:
    """usage.jsonl に event を 1 行追記する (lock 越し)。

    archive job (LOCK_EX) と並行時は **blocking LOCK_SH** で release を待ってから
    append する。archive_usage.py は context manager で必ず EX を release するため
    待機は archive 実行時間で bounded。

    flock 自体が OSError で失敗した場合 (signal 起因等の異常系) のみ
    health_alerts.jsonl に drop alert を 1 行記録して silent return する。
    """
    data_file.parent.mkdir(parents=True, exist_ok=True)

    if not _HAS_FCNTL:
        # Windows: lock なしで append (POSIX O_APPEND の atomic 性に依拠できない代わり、
        # fcntl 経路と挙動を揃えるため try/except で OSError は silent drop)
        try:
            _write_event_line(data_file, event)
        except OSError:
            pass
        return

    lock_file = _resolve_lock_path(data_file, lock_path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_file, "a") as lock_fp:
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_SH)  # blocking — EX release を待つ
        except OSError:
            # 異常系: signal 起因等。silent drop よりは alert で観測可能にする。
            _record_drop_alert(event)
            return

        try:
            _write_event_line(data_file, event)
        finally:
            try:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
