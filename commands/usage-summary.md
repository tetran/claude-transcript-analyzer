Show a summary report of Claude Code Skills and Subagents usage.

```bash
$(command -v python3 || command -v python) ${CLAUDE_PLUGIN_ROOT}/reports/summary.py
```

This prints a terminal report aggregating all recorded events from
`~/.claude/transcript-analyzer/usage.jsonl`.

