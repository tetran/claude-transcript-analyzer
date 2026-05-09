Restart the running claude-transcript-analyzer dashboard server.

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/scripts/restart_dashboard.py
```

`hooks/launch_dashboard.py` runs as an **idempotent spawn**, so even when a
`/plugin update` refreshes UI files like `dashboard/template/shell.html`
(split under `dashboard/template/` in Issue #67), the existing dashboard
process keeps the old HTML in memory and the change is not reflected
(Issue #52).

This command is the **explicit restart** path:

1. Read pid from `~/.claude/transcript-analyzer/server.json`
2. Send SIGTERM to request a graceful shutdown, wait up to 5 seconds
3. If still alive after 5 seconds, force-kill with SIGKILL (POSIX only)
4. Clean up the leftover `server.json` via compare-and-delete
5. Invoke `hooks/launch_dashboard.py` directly to spawn a fresh process

Running this command when no server is alive is **idempotent** (= it can
double as the launch path).

## Output

All progress is written one line at a time to stderr:

- Progress: `[restart] sending SIGTERM to dashboard pid=12345`
- Ready URL: `[restart] dashboard available at http://localhost:9999`

If the URL cannot be obtained within the timeout (spawn failure, etc.) the
command stays silent. In that case `cat ~/.claude/transcript-analyzer/server.json`
also reveals the URL.

## Failure

If the existing process cannot be stopped (PermissionError, or it survives
both SIGTERM and SIGKILL), the command **exits 1 without spawning a new
server** — a structural safeguard against double-launch. Clean up manually
with e.g. `kill -9 $(jq -r .pid ~/.claude/transcript-analyzer/server.json)`
and re-run.

## Automation hint

To auto-restart from a `/plugin update` hook or similar, invoke the same
script:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/restart_dashboard.py
```
