Generate a standalone static HTML report of Claude Code Skills and Subagents usage.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/reports/export_html.py
```

This creates a self-contained HTML file (no server required) at
`~/.claude/transcript-analyzer/report.html` and prints the output path.

To specify a custom output path:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/reports/export_html.py --output /path/to/report.html
```

Open the generated file directly in a browser — it works offline and can be
shared or archived without running a local server.

