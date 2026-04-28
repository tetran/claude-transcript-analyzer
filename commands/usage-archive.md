Manually archive old usage events into monthly `.jsonl.gz` files.

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/scripts/archive_usage.py
```

This walks `~/.claude/transcript-analyzer/usage.jsonl`, moves any event whose
calendar month ended **before `now - 180 days`** (UTC) into
`~/.claude/transcript-analyzer/archive/YYYY-MM.jsonl.gz`, and rewrites the
hot tier to keep only the recent window. The job is idempotent — running it
twice produces the same archive and hot-tier state.

By default the archive job runs automatically on `SessionStart` via
`hooks/launch_archive.py`, so manual invocation is only needed for forced
runs (e.g. after `scripts/rescan_transcripts.py --append` reintroduces old
events) or for verification.

Override the retention window for testing:

```bash
USAGE_RETENTION_DAYS=1 "$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/scripts/archive_usage.py
```

The job log is appended to `~/.claude/transcript-analyzer/archive.log` when
launched via `--log auto` (the default for the auto-launcher); CLI invocations
log to stderr by default.
