# GitHub Issue authoring 規約

このプロジェクトの Issue は **日本語 + セクション絵文字 ToC** の構造的フォーマット。issue の規模で **Heavy / Light** 二バリアント。

---

## Title format

```
[vX.Y.Z カテゴリ] 主目的 — 副タイトル
```

- `vX.Y.Z` セグメントは release への帰属を示す（同一 release の issue 群を並べたい）
- 観測されたカテゴリ: `機能追加`, `UI改善`, `UX改善`
- 例: `[v0.5.0 機能追加] ライブダッシュボード自動起動 — fork-and-detach launcher`

---

## Heavy variant — 大型機能 / spec（例: #30）

```markdown
## 🎯 Why
<motivation + design-decision rationale>

## 👤 User Stories
**Primary:**
As a <role>, I want <capability>, so that <outcome>.

**Supporting:**
- As a <role>, I want ...
- As a <role>, I want ...

## ✅ Acceptance Criteria

### 機能要件
- [ ] ...

### 非機能要件
- [ ] ...

### テスト要件
- [ ] ...

### ドキュメント要件
- [ ] ...

## 🎬 User Scenarios

### Happy path
1. ...
2. ...

### Edge cases
- ...

## 🚫 Out of Scope
- macOS notification (OS 依存・うるさい・Linux 切り捨てになるため不採用)
- ...

## ✔️ Definition of Done
- [ ] テストグリーン
- [ ] ドキュメント更新
- [ ] 実機確認
  - [ ] ...
  - [ ] ...
```

### Heavy variant のお約束

- **セクション絵文字（🎯 / 👤 / ✅ / 🎬 / 🚫 / ✔️）は visual ToC**。Heavy variant 全部で同じ並びを使い、検索性を保つ
- **Out of Scope の各項目は必ず `(理由)` を付ける**。剥き身の「out of scope」は様式に外れる
- **Acceptance Criteria はチェックボックスの羅列**（narrative paragraph ではない）。プロジェクトは「spec-as-checklist」志向
- **Definition of Done に `実機確認` サブチェックリスト** を入れる。自動テストでは捕まらない視覚 / 挙動の確認をここで明示
- 第三者ツールの仕様に依存する issue では `## 🔍 公式仕様の確認結果` セクションを追加し、doc anchor を pin する

---

## Light variant — UI/UX 小改善（例: #17）

```markdown
## 現状

| 表面 | 現在の挙動 |
|---|---|
| ... | ... |

## 採用方針
<chosen approach>

## 受け入れ条件
- [ ] ...
- [ ] ...

## スコープ外
- ... (理由)

## 参考
関連 Issue: #14, #21
関連コード: `dashboard/server.py:_handler()`
```

### Light variant のお約束

- **User Stories セクションを丸ごと省く**。1 ファイル微修正で primary/supporting 分解の価値がない場合に Light を選ぶ
- 開幕は「現状」テーブルでサーフェスごとの現在挙動を summarise する
- Acceptance criteria は Light でもチェックボックス
- 「参考」ブロックは末尾。関連 Issue / 該当コード / 仕様 URL をここに集約

---

## バリアント選択の目安

| 条件 | Heavy | Light |
|---|---|---|
| Acceptance criteria が > 10 項目 | ✅ | ❌ |
| 新ファイル / 新コンポーネント追加 | ✅ | ❌ |
| 1 ファイル polish / UI tweak | ❌ | ✅ |
| Spec / 設計判断を含む | ✅ | ❌ |

迷ったら Heavy 寄りで書き、不要セクションを刈り込んだほうが後から読みやすい。

---

## 関連 issue（参考実装）

| Issue | バリアント |
|---|---|
| #30 | Heavy（spec 系） |
| #17 | Light（UI 改善） |
| #20, #14, #21 | Heavy（機能追加） |

---

## Issue 粒度の決め方 — 共通集計コード OR 共通改善観点で束ねる

リリースに含めたい N 件の改善候補を Issue 化するとき、「1 候補 = 1 Issue」(過分割) でも「1 ページ = 1 Issue」(各 PR が肥大化) でもなく、**共通の集計コード OR 共通の改善観点** で束ねる。目安は **1 PR ≈ 300〜500 行**。複数ページ UI を追加するリリースでは、別途「shell / foundation」Issue を先頭に立てる。

bundling ルールは非対称: 集計コードのみ共通でも OK、改善観点のみ共通でも OK、しかし **両方とも共通点が無いペアは Issue 数削減のために強引にまとめてはいけない**。

### 実務ルール

- 候補ごとに **共通集計コード** と **共通改善観点** の 2 属性を抽出。どちらか一方でも一致するペアを束ね、両方一致しないペアは reject
- bundling 後、各 bundle の予測 diff 量を sanity check: ~500 行を超えそうなら更に分割。~200 行未満で隣接観点があれば統合検討
- 複数ページ UI 変更では shell/foundation Issue を先に切り、子 Issue 本文に「depends on shell #X」を明記
- bundling 判断は **Issue 作成前にユーザーへ surface する**: 提案する N→M 分割を 1 行で告げ、override 余地 (「override 必要なら止めて」) を添えて、Auto Mode 中なら待たずに進める

---

## Umbrella / tracking issue — 親 Issue を捨てない

planning 段階で親 Issue が複数子 Issue に分割された場合、親をすぐに close せず **umbrella / tracking issue に転用** する。子は親を `## 📎 関連` で逆参照、親は子のチェックボックスリストを持つ双方向リンク。

本文 1 行のみの親 Issue は情報量ゼロ。close すると分割に至った判断履歴が失われる。

### 親 Issue 本文 skeleton（再構成順）

1. `## 🎯 vX.Y.Z epic: <name>` — epic スコープを 1 段落
2. `## 💬 判断履歴` — 日付付き決定トレイル + コメントリンク（チャットでユーザーが何を言ったか / planning 中に何を決めたか / 何を defer したか）
3. `## 📐 ページ構成` — テーブル（multi-page UI 案件のみ。それ以外は省略）
4. `## ✅ サブ Issues` — チェックボックスリスト + 短いタイトル要約
5. `## 🛠 進行ガイド` — 順序ガイド（shell first, then parallel）
6. `## 🚫 vX.Y.Z で扱わないもの` — Tier C / out-of-scope（scope-creep 防止 — 「検討したが今回は含めない」記録）
7. `## 📎 関連` — リンク: planning notes / 関連旧 Issue / 現バージョン → 目標バージョン

### Timing rules

- 子 Issue 作成 **直後** に親を tracking issue に転用（cross-reference を live に保つ）
- 全子が close するまで umbrella を close しない → release tag の自然な anchor になる
- ユーザーが頼まない限り title は変えない — 本文の役割転換だけで足りる

---

## 削除許容のリファクタ Issue — 3 案ラダー

ユーザーが「削除も検討」とシグナルを出した refactor / cleanup Issue では、本文に **3 案ラダー** を提示する（1 案単独 recommendation ではなく）。レビュアーに tradeoff を再導出させない。

### 必須要素

- **3 案**: 削除（推奨, lowest LoC）/ refactor / 維持・手動同期（非推奨。比較対象として併記し、レビュアーが「検討した」と分かるように）
- **削除案の LoC delta を定量化**: `impl + entry-point + tests` に分解。具体数値で削除を実感可能にする（見積もりではなく）
- **Why-now を具体的なプロジェクト状態シフトに根付かせる**（canonicalization / surface expansion / deprecation labelling 等）— 抽象 DRY 議論ではなく
- **Acceptance criteria チェックリスト**: テストファイル（`tests/test_<module>.py` は失念しがちな最重要 checkbox）+ README / CLAUDE.md / `docs/*` の参照漏れ drift 監査
- **Label**: GitHub 標準セットには `refactor` / `cleanup` が無いため、`enhancement` を「単純化としての削除」用に流用

---

## PR / Issue コメント運用 — 仕様拡張・実装時判断・AC 逸脱の記録

実装が Issue 本文と乖離したとき（探索中に見つけたバグ、defer された判断の解決、SHOULD を MUST として ship、AC の曖昧解釈を一方向に解決など）、diff だけでは rationale が残らない。Issue 本文は古いスナップショットになり、レビュアー / 未来の自分は「これは意図した変更か？」を git history を漁らないと判断できない。**構造化コメントを残す**。

### コメントを残すべきカテゴリ

- **(A) 仕様拡張** — 実装中に発見したバグ / 改善で Issue 本文に無いもの。justification なしだと scope creep に読まれるリスク
- **(B) 実装時判断** — Issue が「実装時に決定」と明記した項目（例「1 PR or 分割は着手時に決定」）。決定を書き戻す
- **(C) SHOULD → MUST 昇格** — SHOULD 項目を MUST として ship した場合。昇格理由が大事
- **(D) Acceptance criteria 解釈** — 曖昧な criteria を一方向に解決した場合（例「Windows verification」を「CI Windows green」と解釈）

### Workflow — PR review 依頼前

1. Issue 本文 vs diff 監査: 全変更カテゴリを on-spec / off-spec に分類
2. off-spec 項目を **Issue へ単一コメント** で post（本文 amend は不要）
3. このコメントは PR review コンテキストにもなる（「Issue scope 外のファイルを触っている理由」を先回り回答）

### コメントテンプレート

```markdown
## 実装報告: 方針レベルの追記事項 (PR #N)

### Issue 本文外で追加対応した項目
- **(A) 新規 finding**: ...
- **(B) 追加で踏んだ latent bug**: ...
- **(C) 想定外の wider scope 検出**: ...

### 実装着手時判断項目の決定
- **(D) [decision area]**: [chosen option] — [reason]

### SHOULD の処遇
- [SHOULD item] → ✅ done / ❌ deferred (rationale)
```

コメントを省略するのは「決定を private で行う」のに等しい。solo 作業ならまだしも collab repo では有害。

### AC 逸脱の 3 サイト記録

(A) / (D) の特殊ケース: plan-reviewer の P-level finding が原因で、plan が元 Issue 本文の AC から逸脱した場合（例 AC が「X で十分」と書いていたが、実データで signal-death が判明し、review 経由で逸脱を決定）。レビュアーは「AC が実装と一致するか」を見ているのであって「逸脱の rationale が plan history で議論されたか」は見ない — 未記録の逸脱は再 litigate される。

逸脱は **同じ編集パスで 3 サイトに記録** する:

1. **Reflection log の行**: AC 文言と逸脱の双方を引用（plan re-review 時の素早い scan 用）
2. **Risk table の行**: 「AC deviation」とタグ付け、mitigation を「PR description 明示」とする（逸脱そのものが管理対象の design risk である）
3. **PR description**: 「AC との差分」セクションを短く立て、Issue 本文 AC 引用 / plan 逸脱 / rationale (1 文) / plan reflection log 行へのリンクを書く。**ここが load-bearing site** — merge 時のレビュアーと merge 後の読者は plan ファイルではなく PR を見る

変更は「review 経由で AC を更新」とフレームし、「AC を override」とは言わない（collaborative refinement, not authority-flip）。Issue 本文を plan に合わせて silent に書き換えない — 元 AC を可視のまま残し、rationale をコメントで添える。逸脱が minor に見えてもこのパターンを守る — minor-now が major-later になるのは「半分のチームが議論を忘れた」とき。

