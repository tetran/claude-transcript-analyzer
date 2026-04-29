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

