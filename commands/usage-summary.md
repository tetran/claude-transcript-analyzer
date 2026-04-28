Show a summary report of Claude Code Skills and Subagents usage.

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/reports/summary.py
```

This prints a terminal report aggregating all recorded events from
`~/.claude/transcript-analyzer/usage.jsonl` (the hot tier — last 180 days by default).

To include archived events from `~/.claude/transcript-analyzer/archive/*.jsonl.gz`:

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/reports/summary.py --include-archive
```

This merges every monthly `.jsonl.gz` archive with the hot tier for a full-history aggregate.

