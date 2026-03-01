"""hooks/verify_session.py

Stop hook: セッション終了時にトランスクリプトと usage.jsonl を照合し、
差分があれば data/health_alerts.jsonl に記録する。

差分はイベント種別ごとの件数で比較する（timestamp は除外）。
同一セッションで同一の差分が既にアラートとして記録済みの場合は重複記録しない。
"""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# scripts/ ディレクトリをパスに追加して rescan_transcripts を利用
_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from rescan_transcripts import _scan_transcript_file  # noqa: E402

_DEFAULT_DATA_FILE = Path(__file__).parent.parent / "data" / "usage.jsonl"
_DEFAULT_ALERTS_FILE = Path(__file__).parent.parent / "data" / "health_alerts.jsonl"
_DEFAULT_CLAUDE_HOME = Path.home() / ".claude"

DATA_FILE = Path(os.environ.get("USAGE_JSONL", str(_DEFAULT_DATA_FILE)))
ALERTS_FILE = Path(os.environ.get("HEALTH_ALERTS_JSONL", str(_DEFAULT_ALERTS_FILE)))
CLAUDE_HOME = Path(os.environ.get("CLAUDE_HOME", str(_DEFAULT_CLAUDE_HOME)))


def _transcript_path(claude_home: Path, cwd: str, session_id: str) -> Path:
    """トランスクリプトのパスを返す。

    cwd の '/' を '-' に変換する（先頭の '-' はそのまま残す）。
    例: /Users/foo/myapp → -Users-foo-myapp
    """
    cwd_encoded = cwd.replace("/", "-")
    return claude_home / "projects" / cwd_encoded / f"{session_id}.jsonl"


def _load_usage_events_for_session(usage_file: Path, session_id: str) -> list[dict]:
    """usage.jsonl から指定 session_id のイベントを読み込む。"""
    if not usage_file.exists():
        return []
    events = []
    for line in usage_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("session_id") == session_id:
            events.append(ev)
    return events


def _alert_fingerprint(session_id: str, missing_types: list[str]) -> str:
    """重複アラート抑止用のフィンガープリントを返す。"""
    return session_id + ":" + ",".join(sorted(missing_types))


def _existing_fingerprints(alerts_file: Path) -> set[str]:
    """既存アラートのフィンガープリント集合を返す。"""
    if not alerts_file.exists():
        return set()
    fps = set()
    for line in alerts_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            alert = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = alert.get("session_id", "")
        mtypes = alert.get("missing_types", [])
        fps.add(_alert_fingerprint(sid, mtypes))
    return fps


def handle_stop(
    session_id: str,
    cwd: str,
    claude_home: Path | None = None,
    usage_file: Path | None = None,
    alerts_file: Path | None = None,
) -> None:
    """Stop hook の処理本体。トランスクリプトと usage.jsonl を照合してアラートを記録する。"""
    if claude_home is None:
        claude_home = CLAUDE_HOME
    if usage_file is None:
        usage_file = DATA_FILE
    if alerts_file is None:
        alerts_file = ALERTS_FILE

    transcript_path = _transcript_path(claude_home, cwd, session_id)
    if not transcript_path.exists():
        return

    transcript_events = _scan_transcript_file(transcript_path)
    usage_events = _load_usage_events_for_session(usage_file, session_id)

    # timestamp を除いたイベント種別ごとの件数で照合
    transcript_counter = Counter(ev.get("event_type", "") for ev in transcript_events)
    usage_counter = Counter(ev.get("event_type", "") for ev in usage_events)
    missing = transcript_counter - usage_counter

    if not missing:
        return

    missing_types = sorted(missing.keys())
    missing_count = sum(missing.values())

    # 同一セッション・同一差分のアラートが既にあれば重複記録しない
    fingerprint = _alert_fingerprint(session_id, missing_types)
    if fingerprint in _existing_fingerprints(alerts_file):
        return

    alert = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "missing_count": missing_count,
        "missing_types": missing_types,
    }
    alerts_file.parent.mkdir(parents=True, exist_ok=True)
    with alerts_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(alert, ensure_ascii=False) + "\n")


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError) as e:
        print(f"WARN: verify_session: invalid JSON: {e}", file=sys.stderr)
        return

    session_id = payload.get("session_id", "")
    cwd = payload.get("cwd", "")

    if not session_id:
        print("WARN: verify_session: session_id missing in payload", file=sys.stderr)
        return

    handle_stop(session_id=session_id, cwd=cwd)


if __name__ == "__main__":
    main()
