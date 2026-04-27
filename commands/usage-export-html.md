Generate a standalone static HTML report of Claude Code Skills and Subagents usage.

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/reports/export_html.py
```

This creates a self-contained HTML file (no server required) at the default path
(`$HOME/.claude/transcript-analyzer/report.html`) and prints the absolute output path.

To specify a custom output path:

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/reports/export_html.py --output /path/to/report.html
```

To include archived events (`~/.claude/transcript-analyzer/archive/*.jsonl.gz`)
in the report — by default only the hot tier (last 180 days) is rendered:

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/reports/export_html.py --include-archive
```

Open the generated file directly in a browser — it works offline and can be
shared or archived without running a local server.

