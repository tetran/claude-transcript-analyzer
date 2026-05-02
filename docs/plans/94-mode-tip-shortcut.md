# Plan: Issue #94 — Surface inv panel の MODE_LABEL/MODE_TIP キー不整合修正

## Goal

Surface ページ **Skill 起動経路** panel の chip ラベル定義 (`MODE_LABEL`) と tooltip ラベル定義 (`MODE_TIP`) のキーを `'dual'` に統一し、`mode='dual'` 行ホバー時の tooltip lookup miss (生 `'dual'` literal 表示) を解消する。display 文字列 `'🤝 Dual'` は Issue #89 で既に paired 済のため不変、純粋に key 整合のみのデータフロー構造修正。

## Lineage (Issue #89 からの contract 反転)

Issue #89 (PR #95、v0.7.3 にマージ済) は本バグを **意図的に温存** した。具体的には `tests/test_dashboard_wording.py`:

- L172-173 docstring: 「MODE_TIP の 'mixed' キーは既存バグ温存の証拠として明示的に存在を assert」
- L184-185: invariants に `"'mixed'"` を含めて温存契約として固定
- L211: `assert "'mixed'" in mode_tip_block` (温存契約)
- L212: `assert "'dual'" not in mode_tip_block` (温存契約)

display 文字列 `'🤝 Dual'` は Issue #89 で chip ↔ tooltip 両側で paired 済 (L218-223) のため、UX には現れない構造バグとして本 issue (#94) に切り出された。**本 PR ではその温存契約を逆転させ、`'dual'`-present / `'mixed'`-absent に書き換える** (= データフロー側のバグ修正)。git log / bisect で読み戻したときに「L185 が day-1 から間違っていた」と読み違えないよう、本 lineage を明示しておく (CLAUDE.md plan-writing 規律: 「現状処理と未来計画の併記」)。

## Non-Goals

- display 文字列 (`'🤝 Dual'`、`'🤖 LLM-only'`、`'👤 User-only'`) の変更は行わない (Issue #89 で確立済の規範を維持)
- 周辺 chip/tooltip pair (`TREND_LABEL`/`TREND_TIP`、`STATUS_LABEL`/`STATUS_TIP`) は既に整合確認済のため触らない
- `MODE_LABEL` 側 (`50_renderers_surface.js`) の key・value はいずれも変更しない (`data-mode` 属性で writer 側が canonical なので、reader 側の `MODE_TIP` を合わせる方針)
- v0.7.3 milestone 内の他 issue 修正をこの PR に混ぜない

## Critical files

- `dashboard/template/scripts/90_data_tooltip.js` — `MODE_TIP` 定義 (本 issue の本体修正対象、L223-227)
- `dashboard/template/scripts/50_renderers_surface.js` — `MODE_LABEL` 定義 (canonical key の出典、変更なし、参照のみ)
- `tests/test_dashboard_wording.py` — paired-negative invariant test (L184, L211-212 反転 + 新 test 追加先)
- `tests/test_dashboard_template_split.py` — `EXPECTED_TEMPLATE_SHA256` fixture bump (L28)

## Step-by-step plan (TDD 厳守: t-wada 流 red → green → refactor)

### Step 1: feature branch を v0.7.3 から派生

- `git checkout v0.7.3` (Issue #89 PR #95 が merge 済の release branch を base にする)
- `git checkout -b feature/94-mode-tip-alignment`
- stack はしない (#95 は merge 済のため独立 PR でよい)

### Step 2 (RED-A): paired-negative invariant test を反転 (test 先行で fail を作る)

`tests/test_dashboard_wording.py` を編集する:

1. **L184 付近の invariants コメント・assertion 更新**
   - 既存「MODE_TIP key (バグ温存の証拠)」コメントを削除 or「MODE_TIP key (Issue #94 で整合化済)」に書き換え
   - `"'mixed'"` を期待する文字列を、新キー `"'dual'"` を期待するものに置換

2. **L211-212 の paired-negative ブロックを反転**
   - `assert "'mixed'" in mode_tip_block` → `assert "'dual'" in mode_tip_block` に反転
   - `assert "'dual'" not in mode_tip_block` → 行ごと削除
   - 反転後、追加で `assert "'mixed'" not in mode_tip_block` を入れて旧キーが残らないことをガード (paired-negative の対称性を保つ)

3. **L215-216 の MODE_LABEL 側 assertion (`'dual'` あり / `'mixed'` 無し) は不変** — そのまま残す
4. **L218-223 の chip ↔ tooltip display parity (`'🤝 Dual'`) は assertion 自体は不変だが、コメントは更新する**
   - L219 のコメント `// MODE_LABEL[dual] と MODE_TIP[mixed] は **同じ表示文字列** を持つ` は rename 後 stale (lookup site の key が `'dual'` に変わるため)。
   - 新コメント例: `// MODE_LABEL[dual] と MODE_TIP[dual] が同じ表示文字列 '🤝 Dual' を持つ (chip ↔ tooltip parity)`
   - assertion `assert "'🤝 Dual'" in mode_label_block and "'🤝 Dual'" in mode_tip_block` は value-based なので **そのまま残す** (新 key-set parity test とは独立した defense-in-depth)

この時点で `pytest tests/test_dashboard_wording.py` を実行 → **MODE_TIP block に `'dual'` が無いため fail** することを確認 (red state)。

### Step 3 (RED-B): MODE_LABEL/MODE_TIP key 集合一致 invariant test を追加

`tests/test_dashboard_wording.py` に新規 test を追加 (ユーザー判断 #2 で「追加する」と確定済)。

- 配置: 既存 `test_invariant_keys_unchanged()` の直後 (L223 付近) に新規関数として追加
- 関数名案: `test_mode_label_tip_key_parity()`
- 実装方針:
  - `load_assembled_template()` で得た concat 後 template 全文から `const MODE_LABEL = { ... }` / `const MODE_TIP = { ... }` の各 block を `re.search(r"const MODE_LABEL\s*=\s*\{([^}]+)\}", template)` 等で抽出 (既存 `test_invariant_keys_unchanged()` L202-208 と同パターン)
  - 各 block からキーを抽出する regex は **`'<key>':` の colon-anchored パターン** に pin する: `re.findall(r"'([a-z][a-z-]*)'\s*:", block)` (false positive 回避: value 側の `'🤝 Dual'` 等 emoji 含む文字列を拾わないことが確定する)
  - 抽出結果を `set()` 化して比較: `assert label_keys == tip_keys, f"MODE_LABEL keys {sorted(label_keys)} != MODE_TIP keys {sorted(tip_keys)}"`
  - **多層 fail-fast guard**:
    - `assert label_keys`、`assert tip_keys` で空 set 化 (= regex 完全失敗で false green) を阻止
    - 個数固定 assertion を **`EXPECTED_MODE_COUNT = 3` 定数経由** で書く (DRY + 自己文書化):
      ```python
      EXPECTED_MODE_COUNT = 3  # mode は dual / llm-only / user-only の 3 種。将来拡張時はここを更新
      assert len(label_keys) == EXPECTED_MODE_COUNT, f"MODE_LABEL key count drifted: {sorted(label_keys)}"
      assert len(tip_keys) == EXPECTED_MODE_COUNT, f"MODE_TIP key count drifted: {sorted(tip_keys)}"
      ```
      これで block 抽出 regex `[^}]+` の partial-match で短い set が返るリスクを閉じる。将来 mode を 4 種に拡張するときは定数 1 箇所の更新で済む (= test 自身が contract document として機能、意図しない縮退を fail-fast)
      なお、**期待キー集合の literal pin** (`assert label_keys == {'dual', 'llm-only', 'user-only'}`) は採用しない。schema の正本を test 内に持つと test が schema 側に過剰結合するため、「個数 + set 等価」の現状粒度で十分と判断 (set 等価 assertion が片側更新を別経路で検出する)
- stdlib のみ (`re`, pathlib) で実装、外部 dep 追加禁止
- **既存 L218-223 の display parity test との関係**: 重複は意図的。L218-223 は **value-based** (`'🤝 Dual'` 等の表示文字列の有無) で、本 test は **key-based** (`'dual'` 等の lookup キーの集合一致)。両軸が直交しているため defense-in-depth として両方残す

この時点で実行 → 旧 `MODE_TIP` (`'mixed'`) のままでは set が一致せず **fail** することを確認 (red state、Step 2 とは独立に新 test も red)。

### Step 4-pre (verification): `'mixed'` の他参照が無いことを grep で確認

`MODE_TIP` 内の 1 箇所以外で `'mixed'` literal が使われていないことを確認する。schema → renderer → tooltip の 3 層が一致しているという調査済事実 (= rename safety) を、機械的に検証可能な invariant に変換する step。

```
rg -n "'mixed'" dashboard/ subagent_metrics.py reports/ scripts/ docs/spec/
rg -n "\bmixed\b" dashboard/template/styles/   # CSS class .mode-mixed が残っていないか
```

期待出力 (v0.7.3 head 時点で取得した baseline、件数を pin):
- `dashboard/template/scripts/90_data_tooltip.js`: **1 件** (`MODE_TIP` 内 L226 のみ → Step 4 で消える)
- `subagent_metrics.py` / `reports/` / `scripts/`: **0 件**
- `dashboard/template/styles/` (`\bmixed\b`): **0 件** (CSS `.mode-mixed` セレクタ無し確認済)
- `tests/test_dashboard_wording.py`: **7 件** (L172 docstring + L185 invariants + L210/L211/L214/L216/L221 のコメント・assertion → Step 2 ですべて消化または rewrite)
- `docs/plans/89-dashboard-wording.md` 等の plan archive: 履歴ドキュメント、修正不要

**閾値判定**: 上記件数からの増減 (特に `dashboard/` が 2 件以上、`subagent_metrics.py`/`reports/`/`scripts/`/`styles/` が 1 件以上) があれば本 issue の scope を超える可能性があるため、ユーザーに escalate して判断する (gold-plating せず、reflexive contraction せず、ask に倒す)。release branch (`v0.7.3`) が動いた場合の baseline 陳腐化は許容 (実装時点で再 capture 推奨)。

**closing invariant (Step 6 で再確認)**: Step 2 + Step 4 完了後に同じ grep を再実行すると、`dashboard/` と `tests/` の `'mixed'` literal はいずれも **0 件** に落ちている想定 (温存契約と本体ともに消化されたため)。残骸が出たら fix 漏れのサインとして停止。

### Step 4 (GREEN): MODE_TIP key を `'mixed'` → `'dual'` に rename (本体修正)

`dashboard/template/scripts/90_data_tooltip.js` L223-227 を編集:

```js
const MODE_TIP = {
  'llm-only':  '🤖 LLM-only',
  'user-only': '👤 User-only',
  'dual':      '🤝 Dual',  // 'mixed' → 'dual' (Issue #94)
};
```

- value の display 文字列 `'🤝 Dual'` は不変 (Issue #89 で確立済の paired 文字列を維持)
- L235 の `MODE_TIP[mode] || mode` lookup ロジックは不変 (構造を変えず key だけ整合させる最小修正)
- 他に `'mixed'` literal が `90_data_tooltip.js` 内で使われていないことを grep で再確認 (本 key 以外で使っていれば該当箇所も検討、ただし調査済事実から MODE_TIP 内のみ)

実行 → Step 2 / Step 3 の test がいずれも green になることを確認。

### Step 5 (GREEN): EXPECTED_TEMPLATE_SHA256 bump

`tests/test_dashboard_template_split.py` L28 の `EXPECTED_TEMPLATE_SHA256` を更新する。

- capture 手順: 当該 test は `dashboard/server.py` を import して `mod._HTML_TEMPLATE` の sha256 を取る方式 (test L31-42, L52-53 参照)。
- **第一手 (推奨)**: Step 4 を当てた状態で `pytest tests/test_dashboard_template_split.py::test_html_template_byte_equivalent_to_pre_split_snapshot` を 1 回実行 → assertion error の `actual:` 行から新 hash をコピーして L28 の literal を置換。test 内で実際に hash 計算する経路と完全に同一なので drift 0
- **第二手 (fallback)**: 開発フローで test を回せない場合のみ、等価な capture スクリプトを 1 回限り実行 (内部実装は test の `_load_dashboard_module()` と同じく `USAGE_JSONL` 注入 → `spec_from_file_location` で import の経路。意図的に test と同 import path を再現するので結果は等価):
  ```
  python -c "import importlib.util, hashlib, pathlib, os, tempfile; \
  tmp = tempfile.mkdtemp(); \
  pathlib.Path(tmp, 'usage.jsonl').write_text(''); \
  os.environ['USAGE_JSONL'] = str(pathlib.Path(tmp, 'usage.jsonl')); \
  spec = importlib.util.spec_from_file_location('d', 'dashboard/server.py'); \
  mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); \
  print(hashlib.sha256(mod._HTML_TEMPLATE.encode('utf-8')).hexdigest())"
  ```
- 旧 hash `41025f937a4a054075b00d439dc350aaa3f5f8f2fe25f13aad3c95278fc167e0` を新 hash に完全置換
- 第一手と第二手で異なる hash が出た場合は何かが壊れているサインなので即停止して原因究明 (両者は同一 import path を辿る前提のため、差分が出る = 環境変数 / 副作用の混入)

### Step 6 (GREEN 検証): 全 test 実行

- `pytest tests/test_dashboard_wording.py` → green
- `pytest tests/test_dashboard_template_split.py` → green (sha256 一致)
- `pytest` 全体実行 → 既存 test に regression がないことを確認 (key rename の波及で他の wording / parity test に意図せず引っかかっていないか)

### Step 7 (REFACTOR): コミット粒度の整理

**重要原則**: ローカルでは TDD red→green を踏むが、**push する commit はすべて green を保つ単位** にする (red commit を main 系列に流さない / bisect クリーン)。

修正は機械的かつ最小 (1 file rename + test 3 箇所) なので、**Option A (1 commit) を推奨**:

- **Option A (推奨)**: 単一 commit で `MODE_TIP` rename + paired-negative 反転 + 新 parity test 追加 + `EXPECTED_TEMPLATE_SHA256` bump をまとめる。bisect で「この 1 commit が原因」と pin できる
- **Option B**: 2 commit に分割
  - commit 1: 新 parity test を追加 (additive、ただしこの commit 単体では新 test も既存 paired-negative も両方 red になるため、次の commit と合わせて green を保つ — つまり論理的に分割できないので **このパターンは選ばない**)
  - → 結果的に Option A 一択

コミットメッセージ例:

```
fix(dashboard): align MODE_TIP key 'mixed' -> 'dual' (Issue #94)

Surface inv panel の Skill 起動経路 chip で mode='dual' 行の tooltip
lookup が miss していたバグを修正。MODE_LABEL (writer) の key に
MODE_TIP (reader) を合わせる構造修正。display 文字列は不変。

- 90_data_tooltip.js: MODE_TIP key 'mixed' -> 'dual'
- test_dashboard_wording.py: paired-negative invariant 反転
- test_dashboard_wording.py: MODE_LABEL/MODE_TIP key parity test 追加
- test_dashboard_template_split.py: EXPECTED_TEMPLATE_SHA256 bump
```

### Step 8: PR 作成

- base: `v0.7.3`
- head: `feature/94-mode-tip-alignment`
- title: `fix(dashboard): MODE_TIP key alignment for 'dual' (Issue #94)`
- body には Issue #94 link と AC checklist を転記

## Acceptance Criteria

Issue #94 本文の AC をそのまま転記 + 新 test 追加分:

- [ ] `MODE_TIP` のキーが `'mixed'` → `'dual'` に rename されている
- [ ] Issue #89 で導入した paired-negative test が新キー前提に更新されている
- [ ] `tests/test_dashboard_template_split.py` の sha256 fixture を bump
- [ ] schema → renderer → tooltip のキーが完全一致している
- [ ] 新規追加された `MODE_LABEL`/`MODE_TIP` key 集合一致 invariant test (`test_mode_label_tip_key_parity()` 等) が green で、将来の片側更新を回帰ガードする

## Risks / Rollback

### Risks

1. **`'mixed'` literal の他参照**: 本 issue body 内の調査済事実に基づけば `MODE_TIP` 内のみだが、`90_data_tooltip.js` 全体で `'mixed'` を grep して別用途の literal がないか Step 4 直前に再確認すること。万一あれば追加の整合修正 or scope 切り分けを検討
2. **schema 側で `mode='mixed'` を出力していた場合の dead key 化**: 調査済事実では `data-mode` 属性は `MODE_LABEL` の key (= `'dual'`) を書き出すと明記されている。schema-renderer-tooltip 3 層が一致するため `'mixed'` キーは元々 dead だった (= 本修正は dead key の除去でもある)。schema 出力側の確認は不要
3. **sha256 bump の漏れ**: template build pipeline で `_HTML_TEMPLATE` 以外に hash check 対象があれば追従が必要。`test_dashboard_template_split.py` 以外で sha256 を期待している test がないか `grep -r "EXPECTED_.*SHA256\|sha256" tests/` で再確認
4. **Issue #89 PR (#95) との競合**: 既に v0.7.3 へ merge 済のため stack 不要、conflict 懸念は低い。ただし v0.7.3 から派生した他 hotfix branch が同じファイルを触っている場合は rebase 必要

### Rollback

- 単一 file (`90_data_tooltip.js`) の 1 key rename + test 3 箇所の更新のみで surface area が極小。問題発覚時は `git revert <commit>` でクリーンに巻き戻せる
- release branch model なので、v0.7.3 patch release (v0.7.3.1 等) として cut 済の場合は revert PR を v0.7.3 に流して次 patch release を切る

## Out-of-Scope

- 周辺 chip/tooltip pair (`TREND_LABEL`/`TREND_TIP`、`STATUS_LABEL`/`STATUS_TIP` 以外も含む) の網羅 audit (本 issue では `MODE_*` のみ修正、他 pair は調査済で整合確認済のため touch しない)
- **新 parity test の `*_LABEL` / `*_TIP` 全 pair への generalize** (= 全 LABEL/TIP 対の構造的回帰ガード) — 現時点では他 pair が一致しているため defer。本 issue 完了後、別 issue として再評価可 (defer disposition: 本 PR では追加せず、現状維持)
- `MODE_LABEL` の display 文字列 (`'🤝 Dual'` 等) の変更
- `MODE_TIP[mode] || mode` の fallback ロジック自体の見直し (例: `assert` で missing key を fail-fast にする等は別 issue) — **disposition: 本 PR では追跡 issue を立てない (defer のまま放置)**。Issue #94 の修正で実害は消える (現状 schema が出す mode は `dual` / `llm-only` / `user-only` の 3 種で、新 parity test がそれらと MODE_TIP のキー一致を保証する)。将来 schema が新 mode 値を出力するときに表面化する仮説的問題のため、その時点で必要なら別 issue 化する
- schema 側の mode 値生成ロジックの見直し
- v0.7.3 milestone の他 issue (#94 以外) の同梱

## Test 戦略

### 反転対象 paired-negative の現状ブロック

- `tests/test_dashboard_wording.py` `test_invariant_keys_unchanged()` (L168-223)
  - L184: invariants コメント・期待文字列の更新 (`'mixed'` → `'dual'`、温存コメント削除)
  - L211: `assert "'mixed'" in mode_tip_block` → `assert "'dual'" in mode_tip_block` (反転)
  - L212: `assert "'dual'" not in mode_tip_block` (削除)
  - 追加: `assert "'mixed'" not in mode_tip_block` (paired-negative の対称性確保)
  - L215-216 (MODE_LABEL 側): 不変
  - L218-223 (chip ↔ tooltip display parity): 不変

### 新 test の置き場所

- `tests/test_dashboard_wording.py` 末尾、`test_invariant_keys_unchanged()` の直後
- 関数名: `test_mode_label_tip_key_parity()` (`test_invariant_*` シリーズに揃えるなら `test_invariant_mode_label_tip_key_parity()` でも可、既存命名規則に合わせて選択)
- stdlib のみ (`re`, pathlib) で `MODE_LABEL` / `MODE_TIP` block からキー集合を抽出して `set()` 等価比較
- 抽出方法は既存 `test_invariant_keys_unchanged()` の block 切り出し helper を再利用すれば DRY

### sha256 capture コマンド

`tests/test_dashboard_template_split.py` 自体が `dashboard/server.py` の `_HTML_TEMPLATE` を assemble して sha256 を取るので、

- 推奨: Step 4 の本体修正後に `pytest tests/test_dashboard_template_split.py::test_html_template_byte_equivalent_to_pre_split_snapshot` を 1 回実行 → assertion error の `actual:` に表示される hash を L28 にコピー
- 補助スクリプト (上記 Step 5 の `python -c ...` ワンライナー) で抽出してもよい

### TDD サイクル確認

- Step 2 後: `pytest tests/test_dashboard_wording.py::test_invariant_keys_unchanged` → **fail** (red 確認)
- Step 3 後: `pytest tests/test_dashboard_wording.py::test_mode_label_tip_key_parity` → **fail** (red 確認、独立 red)
- Step 4 後: 上記 2 test が **pass** (green 確認)
- Step 5 後: `pytest tests/test_dashboard_template_split.py` も **pass**
- Step 6: `pytest` 全体 green、regression 無し

red を必ず一度通過させること (test 先行を skip して green から始めない)。
