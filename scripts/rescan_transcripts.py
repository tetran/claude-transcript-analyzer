"""scripts/rescan_transcripts.py — 過去トランスクリプト遡及スキャンの CLI 起動口。

~/.claude/projects/ 以下の過去のトランスクリプト（.jsonl）を遡って解析し、
usage.jsonl を一から作り直す（または追記する）スクリプト。

コアロジックは analyzer/rescan/transcripts.py に集約されており、この leaf は
argparse と main() だけを持つ薄い起動口。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer.rescan.transcripts import (  # noqa: E402
    DATA_FILE,
    scan_all,
    write_events,
    write_events_with_dedup,
)


def main() -> None:
    default_transcripts_dir = Path.home() / ".claude" / "projects"

    parser = argparse.ArgumentParser(
        description="Rescan past Claude Code transcripts and rebuild usage.jsonl"
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="(deprecated since v0.8.0: now the default behavior)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing usage.jsonl instead of appending (BC break escape hatch)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count events without writing to file",
    )
    parser.add_argument(
        "--transcripts-dir",
        type=Path,
        default=default_transcripts_dir,
        help=(
            "Directory to scan for transcripts "
            "(default: ~/.claude/projects on POSIX, "
            "%%USERPROFILE%%\\.claude\\projects on Windows)"
        ),
    )
    args = parser.parse_args()

    events = scan_all(args.transcripts_dir)

    if args.dry_run:
        print(f"Found {len(events)} events (dry-run, not writing)")
        return

    if args.overwrite:
        write_events(events, DATA_FILE, append=False)
    else:
        write_events_with_dedup(events, DATA_FILE)
    print(f"Wrote {len(events)} events to {DATA_FILE}")


if __name__ == "__main__":
    main()
