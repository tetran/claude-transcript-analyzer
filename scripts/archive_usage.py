#!/usr/bin/env python3
"""scripts/archive_usage.py — usage.jsonl アーカイブの CLI 起動口。

180 日より古い usage イベントを月次 .jsonl.gz にアーカイブする。
コアロジックは analyzer/archive/usage.py に集約されており、この leaf は
repo root を sys.path に載せて main() に dispatch するだけ。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer.archive.usage import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
