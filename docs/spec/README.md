# `docs/spec/` — 現行仕様 (contract)

このディレクトリは **「何が正しいか / どんな形であるべきか」** を定義する
contract docs を集める場所。実装はここに書かれた契約を満たさなければならない。

## 仕分け基準

| spec/ に置く | reference/ に置かない |
|---|---|
| API レスポンスのフィールド契約 | 実装の非自明ポイント (gotcha) |
| 観測対象 event の schema | なぜその設計を選んだか |
| 動作の挙動契約 (起動条件 / 停止条件 / 環境変数) | 過去に踏み抜いた defects |
| authoring / formatting の規約 | パターン・教訓 |

判断ルール: 「これに違反すると **バグ**」なら spec / 「これを知らないと
**踏み抜く**」なら reference。

## ファイル一覧

| File | 概要 |
|---|---|
| `dashboard-api.md` | dashboard backend `/api/data` のレスポンス schema |
| `dashboard-runtime.md` | ライブダッシュボードの起動 / URL 通知 / idle 停止 / 複数ページ router の挙動契約 |
| `usage-jsonl-events.md` | `usage.jsonl` (収集後 event log) の event_type ごとの schema |
| `archive-runtime.md` | retention + 月次 archive の自動起動 / 環境変数 / 手動コマンド |
| `issue-authoring.md` | GitHub Issue authoring 規約 (Heavy / Light variant) |
| `legacy/` | v0.1 時代の直接 parse 手順 (履歴アーカイブ / 現行仕様ではない) |

## 関連リファレンス

設計プリミティブ・gotcha・教訓は `docs/reference/` を参照。
生 transcript フォーマットと Hook 入力 JSON schema は
`docs/transcript-format.md` (ルート直下) に集約されている。
