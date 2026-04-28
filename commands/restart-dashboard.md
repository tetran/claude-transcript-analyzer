Restart the running claude-transcript-analyzer dashboard server.

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/scripts/restart_dashboard.py
```

`hooks/launch_dashboard.py` は **idempotent な spawn** で動いているため、`/plugin update`
で `dashboard/template.html` などの UI ファイルが更新されても、既存のダッシュボード
プロセスがメモリに古い HTML を保持し続けて変更が反映されない (Issue #52)。

このコマンドは **明示的に再起動** する経路:

1. `~/.claude/transcript-analyzer/server.json` から pid を読む
2. SIGTERM を送って graceful shutdown を依頼し、最大 5 秒待つ
3. 5 秒経っても死なない場合は SIGKILL で強制終了 (POSIX のみ)
4. 残骸の `server.json` を compare-and-delete でクリーンアップ
5. `hooks/launch_dashboard.py` を直叩きして新規 spawn

サーバーが動いていない状態で叩いても **冪等** に動作する (= 起動経路として兼用可能)。

## 出力

- 状態は stderr に 1 行ずつ出力される (例: `[restart] sending SIGTERM to dashboard pid=12345`)
- 新サーバーの URL は launcher の systemMessage 経由で表示される

## 失敗時

既存プロセスを止められなかった場合 (PermissionError / SIGTERM/SIGKILL でも生存) は
**新規 spawn を行わずに exit 1** で抜ける。二重起動を構造的に避けるための安全側挙動。
その場合は `kill -9 $(jq -r .pid ~/.claude/transcript-analyzer/server.json)` 等で
手動 cleanup してから再実行する。

## 自動化のヒント

`/plugin update` フック等で自動再起動したい場合は同じスクリプトを呼べばよい:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/restart_dashboard.py
```
