Manually launch the claude-transcript-analyzer dashboard server (idempotent).

> **Not needed in normal operation since v0.3**: `hooks/launch_dashboard.py`
> is **auto-launched idempotently** on the SessionStart / UserPromptExpansion
> / UserPromptSubmit / PostToolUse hooks.
> This slash command remains as a coexisting path for explicit manual launch.

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/hooks/launch_dashboard.py
```

It calls the same launcher as the hook path, so it is **idempotent**: a
no-op if already running, fork-and-detach spawn otherwise. Double-launch
and port collision cannot occur.

The URL is printed to stderr at startup as a single line `Dashboard
available: http://localhost:<port>`. It is also retrievable from the `url`
field of `~/.claude/transcript-analyzer/server.json`:

```bash
cat ~/.claude/transcript-analyzer/server.json
# → {"pid": ..., "port": ..., "url": "http://localhost:...", "started_at": "..."}
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `DASHBOARD_PORT` | `0` (OS-assigned free port) | Specify an explicit port |
| `DASHBOARD_IDLE_SECONDS` | `600` (10 min) | Idle auto-stop threshold in seconds. `0` disables |
| `DASHBOARD_POLL_INTERVAL` | `1.0` | usage.jsonl change-detection polling interval (seconds) |

```bash
DASHBOARD_PORT=9090 "$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/hooks/launch_dashboard.py
```

## Stopping

- Idle auto-stop: graceful shutdown after 10 minutes without an HTTP request
- Manual stop: `kill $(jq -r .pid ~/.claude/transcript-analyzer/server.json)`

After an idle stop, the next Claude Code action **revives** the dashboard
automatically through the hook.

## Restart (to pick up UI changes)

When `/plugin update` refreshes UI files like `dashboard/template/shell.html`
(split into shell + styles + scripts under `dashboard/template/` in Issue
#67), the launcher's idempotent spawn keeps the existing server, which holds
the old HTML in memory. To force an explicit restart, use `/restart-dashboard`:

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/scripts/restart_dashboard.py
```

## Debug foreground launch (advanced users)

To watch server logs in the foreground, invoke `dashboard/server.py` directly:

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/dashboard/server.py
```

WARNING: this path **bypasses the launcher and therefore the double-launch
check**. Verify there is no live server beforehand
(`cat ~/.claude/transcript-analyzer/server.json`) or kill the existing server
first.
