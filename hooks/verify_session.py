#!/usr/bin/env python3
"""hooks/verify_session.py

Stop hook: セッション終了時にトランスクリプトと usage.jsonl を照合し、
差分があれば ~/.claude/transcript-analyzer/health_alerts.jsonl に記録する。

差分はイベント種別ごとの件数で比較する（timestamp は除外）。
同一セッションで同一の差分が既にアラートとして記録済みの場合は重複記録しない。
"""

import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# scripts/ ディレクトリをパスに追加して rescan_transcripts を利用
_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from rescan_transcripts import _scan_transcript_file  # noqa: E402

_DEFAULT_DATA_FILE = Path.home() / ".claude" / "transcript-analyzer" / "usage.jsonl"
_DEFAULT_ALERTS_FILE = (
    Path.home() / ".claude" / "transcript-analyzer" / "health_alerts.jsonl"
)
_DEFAULT_CLAUDE_HOME = Path.home() / ".claude"

DATA_FILE = Path(os.environ.get("USAGE_JSONL", str(_DEFAULT_DATA_FILE)))
ALERTS_FILE = Path(os.environ.get("HEALTH_ALERTS_JSONL", str(_DEFAULT_ALERTS_FILE)))
CLAUDE_HOME = Path(os.environ.get("CLAUDE_HOME", str(_DEFAULT_CLAUDE_HOME)))


_CWD_ENCODE_PATTERN = re.compile(r"[/\\:.]")


def _encode_cwd(cwd: str) -> str:
    """Claude Code 本体のトランスクリプトディレクトリ命名規則に揃えてエンコード。

    `/`, `\\`, `:`, `.` を全て `-` に変換する。実機 `ls ~/.claude/projects/` で
    確認: dot 入り cwd (例: `/Users/foo/.worktrees/...`) はディレクトリ名側で
    `--worktrees-` のように両端ハイフン化されている。

    例:
      /Users/foo/myapp           → -Users-foo-myapp
      /Users/foo/.worktrees/x    → -Users-foo--worktrees-x
      /Users/foo/my.app/sub      → -Users-foo-my-app-sub
      C:\\Users\\foo\\myapp      → C--Users-foo-myapp
    """
    return _CWD_ENCODE_PATTERN.sub("-", cwd)


def _transcript_path(claude_home: Path, cwd: str, session_id: str) -> Path:
    """トランスクリプトのパスを返す (Claude Code 本体のエンコード規則準拠)。"""
    return claude_home / "projects" / _encode_cwd(cwd) / f"{session_id}.jsonl"


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


_TRANSCRIPT_MISMATCH_HINT = (
    "Inspect transcript_path to identify the missed events. "
    "User slash commands are observed via UserPromptExpansion (primary) and "
    "UserPromptSubmit (fallback); skill / subagent events via PostToolUse. "
    "A missing entry usually indicates a hook execution failure — check "
    "~/.claude/log/ for hook errors, then re-run "
    "`scripts/rescan_transcripts.py --append` if the gap needs to be backfilled."
)


def _project_from_cwd(cwd: str) -> str:
    """cwd の basename を project 名として返す (空 cwd は空文字)。"""
    if not cwd:
        return ""
    return Path(cwd).name


def _build_missing_samples(
    transcript_counter: Counter,
    usage_counter: Counter,
    missing_types: list[str],
) -> list[dict]:
    """欠損 type ごとに transcript_count / usage_count / delta を返す。

    Issue #51: 「skill_tool が 1 件欠けた」だけでなく「transcript には 3 件あったが
    usage には 2 件しかない」と分かるようにする。
    """
    samples: list[dict] = []
    for et in missing_types:
        t = transcript_counter.get(et, 0)
        u = usage_counter.get(et, 0)
        samples.append({
            "event_type": et,
            "transcript_count": t,
            "usage_count": u,
            "delta": t - u,
        })
    return samples


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

    # Issue #51: alert に actionable な情報を追加する。
    # - kind: 種別 enum (drop alert と区別)
    # - project / cwd: どこで起きたか即座に分かる
    # - transcript_path: 直接該当トランスクリプトを開ける
    # - missing_samples: 欠損 type ごとの transcript vs usage 件数比較
    # - hint: recommended action 文
    alert = {
        "kind": "transcript_mismatch",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "project": _project_from_cwd(cwd),
        "cwd": cwd,
        "transcript_path": str(transcript_path),
        "missing_count": missing_count,
        "missing_types": missing_types,
        "missing_samples": _build_missing_samples(
            transcript_counter, usage_counter, missing_types
        ),
        "hint": _TRANSCRIPT_MISMATCH_HINT,
    }
    # newline="\n" 固定で Windows text mode の \r\n 変換を抑止 (Issue #24)。
    alerts_file.parent.mkdir(parents=True, exist_ok=True)
    with alerts_file.open("a", encoding="utf-8", newline="\n") as f:
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
