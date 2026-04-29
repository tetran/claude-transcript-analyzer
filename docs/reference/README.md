# `docs/reference/` — 設計判断・gotcha・実装パターン

このディレクトリは **「なぜそうなっているか / どう踏み抜きを避けるか」** を
記録する reference docs を集める場所。仕様 (contract) ではなく、設計プリミティブ
・教訓・実装の非自明ポイントを残す。

## 仕分け基準

| reference/ に置く | spec/ に置かない |
|---|---|
| 実装の非自明ポイント (gotcha) | API レスポンス schema |
| なぜその設計を選んだか (option 比較) | event_type の field 契約 |
| 過去に踏み抜いた defects と回避策 | 環境変数の値域定義 |
| パターン (multi-OS / lock / SSE 等) の recipe | 動作契約 (起動条件等) |

判断ルール: 「これに違反すると **バグ**」なら spec / 「これを知らないと
**踏み抜く**」なら reference。

## ファイル一覧

| File | 概要 |
|---|---|
| `storage.md` | JSONL primary 採用の根拠 / dedup 規律 / archive 不変性ポリシー |
| `cross-platform.md` | Windows porting checklist + Python launcher trilemma (`python` vs `python3` vs shell-fallback chain) |
| `dashboard-server.md` | stdlib SSE の 3 要件 / JSON-in-`<script>` escape / component composition pattern |
| `subagent-invocation-pairing.md` | 二重観測点 (PostToolUse + SubagentStart) の同定アルゴリズム + DRY 圧の教訓 |

## 関連 spec

各 reference に対応する仕様 (contract) は `docs/spec/` を参照:

| reference | 関連 spec |
|---|---|
| `storage.md` | `usage-jsonl-events.md` / `archive-runtime.md` |
| `cross-platform.md` | (CLAUDE.md データフロー / `hooks/hooks.json` / `commands/*.md`) |
| `dashboard-server.md` | `dashboard-api.md` / `dashboard-runtime.md` |
| `subagent-invocation-pairing.md` | `usage-jsonl-events.md` (subagent_* event の schema) |
