Manually launch the claude-transcript-analyzer dashboard server (idempotent).

> **v0.3 以降の通常運用ではこのコマンドは不要**: `hooks/launch_dashboard.py` が
> SessionStart / UserPromptExpansion / UserPromptSubmit / PostToolUse hook で
> **べき等に自動起動**する。
> このスラッシュコマンドは、明示的に手動で立ち上げたい場合の併存パスとして残す。

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/hooks/launch_dashboard.py
```

Hook 経由と同じ launcher を呼ぶため **べき等**: 既起動なら何もせず、未起動なら
fork-and-detach で起動する。多重起動・ポート競合は起きない。

URL は起動時 stderr に `Dashboard available: http://localhost:<port>` として 1 行出力される。
また `~/.claude/transcript-analyzer/server.json` の `url` フィールドからも取得できる：

```bash
cat ~/.claude/transcript-analyzer/server.json
# → {"pid": ..., "port": ..., "url": "http://localhost:...", "started_at": "..."}
```

## 環境変数

| 変数 | デフォルト | 意味 |
|------|-----------|------|
| `DASHBOARD_PORT` | `0`（OS 任せ・空きポート） | 具体ポート指定可 |
| `DASHBOARD_IDLE_SECONDS` | `600`（10 分） | idle 自動停止の閾値秒。`0` で無効化 |
| `DASHBOARD_POLL_INTERVAL` | `1.0` | usage.jsonl 変更検知の polling 周期 (秒) |

```bash
DASHBOARD_PORT=9090 "$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/hooks/launch_dashboard.py
```

## 停止

- idle 自動停止: 最後の HTTP リクエストから 10 分経過で graceful shutdown
- 手動停止: `kill $(jq -r .pid ~/.claude/transcript-analyzer/server.json)`

idle 停止後は次の Claude Code 操作で hook 経由で **自動復活** する。

## 再起動 (UI 変更を反映したいとき)

`/plugin update` で `dashboard/template.html` 等の UI ファイルが更新されても、
launcher は idempotent な spawn なので既存サーバーは古い HTML をメモリに保持し続ける。
明示的に再起動するには `/restart-dashboard` を使う:

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/scripts/restart_dashboard.py
```

## デバッグ用 fg 起動 (上級ユーザー向け)

サーバーログを foreground で見たい場合は `dashboard/server.py` を直接叩ける：

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/dashboard/server.py
```

⚠️ ただしこの経路は **launcher を経由しないため二重起動チェックを行わない**。
事前に `cat ~/.claude/transcript-analyzer/server.json` で既起動を確認するか、
既存サーバーを kill してから叩くこと。

