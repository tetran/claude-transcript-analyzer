#!/usr/bin/env python3
"""hooks/record_assistant_usage.py — Stop hook の薄い起動口。

token / model 観測のコアロジックは analyzer/rescan/assistant_usage.py に
集約されており、この leaf は stdin を読んで handle_stop に dispatch するだけ。

silent contract: Stop hook をブロックしないため、parse error / IO error は
全て silent skip (= sys.exit(0))。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer.rescan.assistant_usage import handle_stop  # noqa: E402


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
