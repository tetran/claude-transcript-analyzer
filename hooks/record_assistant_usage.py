#!/usr/bin/env python3
"""hooks/record_assistant_usage.py — Stop hook で transcript から token / model
観測を集めて usage.jsonl に `assistant_usage` event として追記する (Issue #99)。

収集経路:
- メイン session transcript (hook 入力 `transcript_path`) ← `source="main"`
- per-subagent transcript (`<session_dir>/subagents/agent-<agent_id>.jsonl`) ←
  `source="subagent"`。Issue #93 で確定した `subagent_type == ""` filter rule を
  適用し、type 入り invocation のみ対象とする。

dedup:
- `(session_id, message_id)` first-wins。既存 `usage.jsonl` を line scan して
  set 化、新規分のみ append。同 message_id を main / subagent 経路で二重観測
  しても 1 件に収束。

silent contract: Stop hook をブロックしないため、parse error / IO error は
全て silent skip (= sys.exit(0))。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _append import append_event  # noqa: E402

_DEFAULT_PATH = Path.home() / ".claude" / "transcript-analyzer" / "usage.jsonl"
DATA_FILE = Path(os.environ.get("USAGE_JSONL", str(_DEFAULT_PATH)))


def _project_from_cwd(cwd: str) -> str:
    if not cwd:
        return ""
    return Path(cwd).name


def _parse_iso(ts: str) -> datetime | None:
    """ISO 8601 を parse し、tz-aware のみを valid とする (naive は drop / spec)。

    Python 3.11+ は `Z` suffix を fromisoformat で受けるが、それ以前のバージョン
    が混ざる環境でも動くよう Z → +00:00 fallback を持つ。
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        if ts.endswith("Z"):
            try:
                dt = datetime.fromisoformat(ts[:-1] + "+00:00")
            except ValueError:
                return None
        else:
            return None
    if dt.tzinfo is None:
        return None  # naive は drop
    return dt


def _extract_assistant_usage(
    transcript_path: Path,
    *,
    session_id: str,
    project: str,
    source: str,
) -> Iterator[dict]:
    """transcript の各 line から assistant_usage event 候補を yield する。

    skip 規律:
    - JSON parse 失敗 → skip
    - `message.role != "assistant"` → skip
    - `message.usage` 欠損 → skip
    - `message.id` 欠損 → skip (dedup key を作れない)
    - timestamp parse 失敗 / naive → skip
    """
    try:
        text = transcript_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        msg_id = msg.get("id") or ""
        if not msg_id:
            continue
        dt = _parse_iso(rec.get("timestamp", ""))
        if dt is None:
            continue
        ts_iso = dt.astimezone(timezone.utc).isoformat()

        ev = {
            "event_type": "assistant_usage",
            "project": project,
            "session_id": session_id,
            "timestamp": ts_iso,
            "model": msg.get("model", "") or "",
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            # transcript の `_input_tokens` サフィックスを event field では `_tokens` に統一
            "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
            "cache_creation_tokens": int(usage.get("cache_creation_input_tokens") or 0),
            "message_id": msg_id,
            "service_tier": usage.get("service_tier"),
            "inference_geo": usage.get("inference_geo"),
            "source": source,
        }
        yield ev


def _scan_existing_state(data_file: Path, session_id: str) -> tuple[set, set]:
    """既存 `usage.jsonl` を 1 pass scan して dedup set + valid agent_ids を作る。

    - existing_keys: `(session_id, message_id)` の set。assistant_usage event 全期間。
    - valid_agent_ids: 当該 session 内で `subagent_type != ""` の subagent_stop に
      紐づく agent_id 集合。Issue #93 filter rule を per-subagent transcript の
      対象絞り込みに使う。
    """
    existing_keys: set = set()
    valid_agent_ids: set = set()
    if not data_file.exists():
        return existing_keys, valid_agent_ids
    try:
        text = data_file.read_text(encoding="utf-8")
    except OSError:
        return existing_keys, valid_agent_ids
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        et = ev.get("event_type")
        if et == "assistant_usage":
            sid = ev.get("session_id", "") or ""
            mid = ev.get("message_id", "") or ""
            if sid and mid:
                existing_keys.add((sid, mid))
        elif et == "subagent_stop":
            if ev.get("session_id", "") != session_id:
                continue
            agent_id = ev.get("subagent_id", "") or ""
            sub_type = ev.get("subagent_type", "") or ""
            if agent_id and sub_type:
                valid_agent_ids.add(agent_id)
    return existing_keys, valid_agent_ids


def _subagent_dir(transcript_path: Path) -> Path:
    """`<encoded-cwd>/<session_id>.jsonl` → `<encoded-cwd>/<session_id>/subagents/`."""
    return transcript_path.with_suffix("") / "subagents"


def _agent_id_from_filename(file_path: Path) -> str:
    """`agent-<agent_id>.jsonl` → `<agent_id>`. prefix が違えば空文字。"""
    stem = file_path.stem
    if stem.startswith("agent-"):
        return stem[len("agent-"):]
    return ""


def handle_stop(payload: dict, *, data_file: Path | None = None) -> None:
    """Stop hook の処理本体。transcript を読んで新規 assistant_usage を append。"""
    if data_file is None:
        data_file = DATA_FILE

    transcript_path_str = payload.get("transcript_path", "")
    if not transcript_path_str:
        return
    transcript_path = Path(transcript_path_str)

    session_id = payload.get("session_id", "") or ""
    if not session_id:
        return
    cwd = payload.get("cwd", "") or ""
    project = _project_from_cwd(cwd)

    existing_keys, valid_agent_ids = _scan_existing_state(data_file, session_id)

    new_events: list[dict] = []

    # main session transcript (source="main")
    if transcript_path.exists():
        for ev in _extract_assistant_usage(
            transcript_path,
            session_id=session_id,
            project=project,
            source="main",
        ):
            key = (ev["session_id"], ev["message_id"])
            if key in existing_keys:
                continue
            existing_keys.add(key)  # 同 transcript 内重複 + main↔subagent 二重観測の dedup
            new_events.append(ev)

    # per-subagent transcripts (source="subagent")
    sa_dir = _subagent_dir(transcript_path)
    if sa_dir.is_dir():
        for sa_file in sorted(sa_dir.glob("agent-*.jsonl")):
            agent_id = _agent_id_from_filename(sa_file)
            if not agent_id or agent_id not in valid_agent_ids:
                # Issue #93 filter rule: type 入り invocation のみ
                continue
            for ev in _extract_assistant_usage(
                sa_file,
                session_id=session_id,
                project=project,
                source="subagent",
            ):
                key = (ev["session_id"], ev["message_id"])
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                new_events.append(ev)

    for ev in new_events:
        append_event(data_file, ev)


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    try:
        handle_stop(payload)
    except Exception:
        # Stop hook は silent: 何があっても exit 0
        sys.exit(0)


if __name__ == "__main__":
    main()
