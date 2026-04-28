"""reports/export_html.py — 静的スタンドアロン HTML レポートを生成する。

使い方:
    python3 reports/export_html.py [--output PATH]

オプション:
    --output PATH   出力先ファイルパス（省略時はデフォルトパス）

デフォルト出力先: ~/.claude/transcript-analyzer/report.html
USAGE_JSONL 環境変数でデータパスをオーバーライド可能。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.server import build_dashboard_data, render_static_html
from reports.summary import load_events

_DEFAULT_OUTPUT = (
    Path.home() / ".claude" / "transcript-analyzer" / "report.html"
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="静的 HTML レポートを生成する")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="出力先ファイルパス（省略時はデフォルトパス）",
    )
    parser.add_argument(
        "--include-archive",
        action="store_true",
        help="archive/*.jsonl.gz を読み込んで集計に含める (default: hot tier のみ)",
    )
    args = parser.parse_args(argv)

    output: Path = args.output if args.output is not None else _DEFAULT_OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)

    events = load_events(include_archive=args.include_archive)
    data = build_dashboard_data(events)
    html = render_static_html(data)

    output.write_text(html, encoding="utf-8")
    print(str(output.expanduser().resolve()))


if __name__ == "__main__":
    main()

