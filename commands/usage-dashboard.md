Launch the claude-transcript-analyzer dashboard server.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/dashboard/server.py
```

Open http://localhost:8080 in your browser to view the Skills and Subagents usage dashboard.

You can set the `DASHBOARD_PORT` environment variable to use a different port:
```bash
DASHBOARD_PORT=9090 python3 ${CLAUDE_PLUGIN_ROOT}/dashboard/server.py
```

