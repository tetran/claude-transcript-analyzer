# ライブダッシュボード運用仕様

`dashboard/server.py` および `hooks/launch_dashboard.py` のランタイム挙動仕様。
v0.3 (Issue #14) で導入された「Claude Code セッションと一体化したライブビュー」
の設計。手動コマンドは不要。

`/api/data` レスポンスの schema は `docs/spec/dashboard-api.md` を参照。

## 起動条件

`hooks/launch_dashboard.py` が以下の Hook で発火し、`server.json` を見て **べき等に**
起動判定する（既起動なら何もしない、未起動なら fork-and-detach で起動）：

| Hook | 役割 |
|------|------|
| `SessionStart` | Claude Code 起動の瞬間にダッシュボードも立ち上げる |
| `UserPromptExpansion` | slash command 経路の主観測点で発火 → 即時復活 |
| `UserPromptSubmit` | idle 後にユーザーが操作再開 → 自動復活（expansion fallback としても兼用） |
| `PostToolUse` | 道中の死活サイダーガード（任意のツール使用後にも復活窓を持つ） |

launcher は **常に < 100ms で exit 0**（Claude Code をブロックしない）。

## URL 確認方法

サーバー起動時に `~/.claude/transcript-analyzer/server.json` に `{pid, port, url, started_at}`
が atomic に書かれる。手動起動時は stderr にも `Dashboard available: http://localhost:<port>`
を 1 行出力する。

```bash
# URL を取得 (fallback / 確認用)
cat ~/.claude/transcript-analyzer/server.json
```

## URL の通知タイミング (Issue #34, v0.5.2〜)

`launch_dashboard.py` が hook output の `systemMessage` 経由でユーザー UI に
`📊 Dashboard: <url>` を 1 行通知する。条件:

| 状態 \ hook | SessionStart | UserPromptExpansion | UserPromptSubmit | PostToolUse |
|------------|:------------:|:------------------:|:---------------:|:-----------:|
| **新規 spawn** | ✅ 通知 | ✅ 通知 | ✅ 通知 | ✅ 通知 |
| **既起動 (alive)** | ✅ 通知 (再表示) | ❌ silent | ❌ silent | ❌ silent |

設計判断:
- 毎ターン発火する hook (UserPromptExpansion / Submit / PostToolUse) で既起動時に
  通知すると会話画面が systemMessage で埋まるため silent
- spawn 時は「初回起動 / idle 復活」のいずれもユーザーが URL を必要とする瞬間なので
  4 hook いずれでも通知
- 既起動 + SessionStart は `claude --resume` 等のセッション開始時の親切再表示

例外時 / hook_event_name 不在 / spawn 直後 server.json 出現遅延時は **silent**
(silent exit 0 契約は維持。次回 hook での復活経路がある)。

## 停止条件

- **idle 自動停止**: 最後の HTTP リクエストから `DASHBOARD_IDLE_SECONDS`（デフォルト 600 秒 = 10 分）経過で graceful shutdown
  - SSE 接続中は idle カウンタが進まないため、ブラウザ開きっぱなしでは停止しない
- **手動停止**: `kill <pid>`（pid は server.json から取得）。SIGTERM / SIGINT で graceful shutdown
- 停止時に server.json は **compare-and-delete** で自動削除（多重インスタンス保護）

idle 停止後にユーザーが Claude Code 操作を再開すると、UserPromptExpansion /
UserPromptSubmit / PostToolUse hook が launch_dashboard を起動し直して **自動復活**
する（同 or 別ポート）。

## 環境変数

| 変数 | デフォルト | 意味 |
|------|-----------|------|
| `DASHBOARD_PORT` | `0`（OS 任せ） | 具体ポート指定時はそのポートで bind |
| `DASHBOARD_IDLE_SECONDS` | `600` | idle 停止の閾値秒。`0` で停止無効化 |
| `DASHBOARD_POLL_INTERVAL` | `1.0` | usage.jsonl 変更検知の polling 周期 (秒)。`0` で監視無効 |
| `DASHBOARD_SERVER_JSON` | `~/.claude/transcript-analyzer/server.json` | server.json のパス |

## 手動起動・停止

通常は hook 経由の自動起動で十分だが、手動でも起動可能：

```bash
# 手動起動 (launcher 経由でべき等 — 既起動なら何もしない)
python3 ${CLAUDE_PLUGIN_ROOT}/hooks/launch_dashboard.py
# /usage-dashboard スラッシュコマンドも同じ経路

# fg debug 用に直叩きする場合は事前に既起動確認 (二重起動防止)
cat ~/.claude/transcript-analyzer/server.json  # 既起動なら kill してから
python3 dashboard/server.py

# 手動停止
kill $(jq -r .pid ~/.claude/transcript-analyzer/server.json)
```

## ダッシュボード複数ページ構成

ダッシュボードは **ハッシュベース router で 4 ページ** に分割。

### ページ構成

| Path | data-page | 名前 | 主な目的 |
|------|-----------|------|----------|
| `#/` | `overview` | Overview | KPI / skill ranking / subagent ranking / project breakdown / daily trend / health alerts |
| `#/patterns` | `patterns` | Patterns | 利用パターン (時間帯 / 共起 / project×skill) |
| `#/quality` | `quality` | Quality | 実行品質と摩擦シグナル (permission / compact / percentile) |
| `#/surface` | `surface` | Surface | スキル surface (発見性 / 想起性) |

### 共通 chrome と Overview 専用 chrome の分離

- **共通頂部 nav** (`<nav class="page-nav">`): 4 タブ。`.app` 直下、全ページに表示
- **共通 footer** (`<footer class="app-footer">`): conn-status / lastRx / sessVal /
  クレジット行。全ページに表示（接続バッジは Overview 以外のページからも見える）
- **Overview 専用 header** (`<header class="header">`): h1「Claude Code Usage
  Overview」+ lede。`<section data-page="overview">` の中に閉じる

### Router の動作仕様

- **DOM 構造**: 4 つの `<section data-page="...">` を DOM に常駐させ、`hidden`
  属性切替でページ表示を切り替える（DOM tree から削除しない）
- **router IIFE**: `<script>` ブロック内に独立した IIFE で実装。`HASH_TO_PAGE`
  テーブル + `applyRoute(rawHash)` + `hashchange` listener。SSE refresh 経路と
  独立して動作（Overview 以外のページ表示中も `loadAndRender()` は走り続け、
  戻ってきたときに最新データが見える）
- **不正 hash fallback**: 未知 hash (`#/foo`, percent-encoded, `#` 単体) は
  `'overview'` に倒れる
- **`body[data-active-page="<page>"]`**: 各 widget renderer が
  `if (document.body.dataset.activePage !== '<page>') return;` で page-scoped
  early-out できるよう active page 名を expose

`loadAndRender()` は Overview 専用 renderer (absolute `getElementById` lookup)
として残す。Overview 以外のページが widget を持つ場合は page-scoped early-out
で no-op 化する設計。

page-scoped early-out (`if (document.body.dataset.activePage !== '<page>') return;`)
を持つ widget は、page 切替直後に render される必要があるため main IIFE に
`window.addEventListener('hashchange', () => loadAndRender().catch(...))` の
hashchange listener を 1 本持つ。router IIFE は先に登録されているため
`body.dataset.activePage` が更新されてから main IIFE の listener が走り、新 page
の renderer が正しく動く。

### Period toggle (Issue #85, v0.7.3〜)

- **配置**: 各 page header の右端 slot に表示。`<header class="header">` は
  `display: grid; grid-template-columns: 1fr auto;` の 2 カラム構成で、
  右側 (auto) カラムに `<div class="period-toggle-slot" data-period-slot="<page>">` を置く。
  toggle DOM (`<div id="periodToggle">`) は **1 つだけ** で、初期配置は Overview slot に居る。
  4 ボタン (`data-period="7d|30d|90d|all"`) で `aria-pressed` で active 表現。
- **Page 切替時の DOM move**: `05_period.js` の `movePeriodToggleToActivePage()` が
  hashchange listener で active page slot に DOM を `appendChild` で move する
  (1 DOM 維持 / state sync 不要)。router IIFE (`00_router.js`) の hashchange listener
  で `body.dataset.activePage` が先に更新されてから本 listener が走る (登録順 = 発火順)。
- **可視範囲**: Overview / Patterns 表示時のみ可視。Quality / Surface には slot が
  存在しないので move 先が無く、`body[data-active-page="quality"|"surface"] #periodToggle { display: none }`
  の page-scoped CSS rule で隠す (toggle DOM は前 page の slot に残る)。
- **State**: `05_period.js` の closure-private `__periodCurrent` で持ち、
  `window.__period.{getCurrentPeriod, setCurrentPeriod, wirePeriodToggle}` を expose。
  click handler で `aria-pressed` 付け替え + `setCurrentPeriod(p)` + 再 fetch を呼ぶ。
- **Fetch 経路**: `20_load_and_render.js` の fetch URL は毎呼び出し時に
  `'/api/data?period=' + encodeURIComponent(getCurrentPeriod())` で組み立てる
  (call-time lookup で SSE refresh も新 period で走る = race-free)。
- **永続化なし**: reload で `'all'` にリセットされる仕様 (URL hash 同期 / localStorage
  保存は本 issue scope 外、将来 issue で再検討)。
- **Static export**: `render_static_html` 経路 (`window.__DATA__` 既存) では
  `wirePeriodToggle()` の冒頭で toggle に `hidden` 属性を立てて click bind を skip
  する (server を経由しないので period 切り替え自体に意味がない)。
- **Badge 表示**: response の `period_applied !== 'all'` のとき Overview の
  `dailySub` / `skillSub` / `subSub` / `projSub` と Patterns 3 sub に
  `<period> 集計 · ` を additive prefix。`'all'` のときは prefix なし (現状互換)。
