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
