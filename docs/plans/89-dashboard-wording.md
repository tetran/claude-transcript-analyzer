# Issue #89 Implementation Plan — ダッシュボードの文言全体修正

> Issue: https://github.com/tetran/claude-transcript-analyzer/issues/89
> Milestone: v0.7.3
> Base branch: `v0.7.3`
> Feature branch: `feature/89-dashboard-wording`

## 1. Goal / Non-Goals

### Goal
ダッシュボード (Overview / Patterns / Quality / Surface) と export_html の静的レポートに散在する**英語残存・表記揺れ・難解な日本語**を統一・整理し、読みやすい UI 文言に揃える。Claude Code 公式日本語ドキュメントの表記をプロジェクト共通の表記レジストリ（=本 plan §2）として確立し、後続 Issue でも参照できる形にする。

### Non-Goals
- **`dual` ↔ `mixed` のキー不一致バグ** (`50_renderers_surface.js` の `MODE_LABEL` キーは `'dual'` だが `90_data_tooltip.js` の `MODE_TIP` キーは `'mixed'`) は本 issue では扱わない。**別 issue として切り出す** (本 plan の §7 に記載)。本 plan ではバグを「踏まない」最小限の範囲、すなわち **キー文字列を変えず、ラベル値（chip 表示文字列 / tooltip 表示文字列）だけを訳す** に留める。
- **`reports/summary.py`（ターミナル集計の英語ラベル）は対象外**。CLI 出力は別の意思決定軸（grep 利便性 / 既存スクリプト互換）があるため、本 issue では触らない。
- **`reports/export_html.py` 自体には独自 UI 文言は無い**。`render_static_html()` が dashboard テンプレートを inline する設計なので、dashboard 側を直せば自動で反映される。export_html 専用の追加変更はしない。
- **CLI の argparse help 文字列、server.py のログ出力 (`Dashboard available: …`)、エラーメッセージ (`SSE not supported on this server`) などの開発者向け文言**は対象外。
- **`/api/data` レスポンスの schema フィールド名**（例: `permission_prompt_count` / `subagent_ranking` / `failure_rate`）は **絶対に変えない**。UI ラベルだけ変える。
- **CSS class 名・data-\* attribute key・aria-\* role 名・DOM id**（例: `data-tip="rank"` / `data-mode="dual"` / `mode-dual` / `kpi-perm` / `data-page-link="overview"` / `data-place="right"` / `body.dataset.activePage` の page 名 `"overview"` 等）も **絶対に変えない**。これらは JS / CSS / テストが key match しているため。
- 機能変更・レイアウト変更・新パネル追加は伴わない。**純粋な文言修正**。
- **`<code>` ラップの追補は「文言を編集している pop-body / 文の line のみ」に scope する**（iter2 reviewer C4 反映）。すでに bare で書かれている schema-field token の網羅的な `<code>` 化は本 plan の対象外、別 issue 候補。touch していない pop-body の bare token は本 PR で **触らない**。

---

## 2. 訳語 / 表記ガイドライン

### 2.1 出典 / 優先順位（**v3 方針**: Claude 公式日本語 docs が混在語は **英語側に寄せる**）

> **方針**: Claude Code 公式日本語ドキュメントは Claude-spec 用語 (skill / subagent / session / slash command / hook 等) を **片仮名と英語で混在使用** している (`/ja/skills` 表題は「スキルで Claude を拡張する」だが本文には bare `skill` も多数登場、`/ja/sub-agents` は「カスタムサブエージェントの作成」だが prose に `subagent` 並存、`/ja/hooks` 表題は「Hooks リファレンス」、等)。混在状態の語に対して片仮名形を採用すると公式と片方ずつしか合わず、UI 内の一貫性も崩れる。**よって本 plan は「混在している語は英語形に寄せる」を採用する**。

1. **Claude-spec 用語**（公式日本語 docs で混在使用が確認された語）は **英語形を採用**:
   - `skill` (panel title では先頭大文字 `Skill`、prose / chip では `skill`)
   - `subagent` / `Subagent`
   - `session` / `Session`
   - `slash command` / `Slash command`
   - `hook` / `Hook`
   出典確認: `/ja/skills`, `/ja/sub-agents`, `/ja/how-claude-code-works`, `/ja/commands`, `/ja/hooks` (いずれも公式日本語 docs で英語形と片仮名形が混在)。
2. **プロジェクト固有の集計用語 / 一般 UI 英単語**（issue 本文の「日本語化候補」群: invocation / dedup / legend / mtime / signal / worst 等）は **日本語に意訳**。これらは Claude-spec ではなく一般用語なので、読みやすさ優先で日本語化（§2.2 表参照）。
3. **意味不明な日本語**（上位漏れ / 共起 / 長尾分布）は **読み手が理解できる日本語に書き換え**（§2.4 参照）。
4. **Empty state プレースホルダー** は全て **`no data`** に統一（§2.3 参照）。
5. **既に英語化されている短い stats / chip ラベル**（peak / avg / active / Top N / mode chip 等）は **英語のまま維持して chip スタイルの一貫性を取る** ことを許容。意訳すると逆に冗長になるラベルや、複数箇所で英語が定着しているラベルは英語維持。

### 2.2 訳語表（本 plan のレジストリ）

#### A. Claude-spec 用語（**英語形に統一** — 公式日本語 docs での混在を踏まえ §2.1 §1 方針）

| 原語 | 採用形 | 注記 / 代表的な置換例 |
|---|---|---|
| skill / スキル | **`skill` / `Skill`** | panel ttl: `スキル利用ランキング` → `Skill 利用ランキング` / `スキル共起マトリクス` → `Skill 同時利用マトリクス` / `スキルライフサイクル` → `Skill lifecycle` / scope-note `Lifecycle panel` 維持 / aria-label / sub-badge `件のスキル` → `skills` |
| subagent / サブエージェント | **`subagent` / `Subagent`** | panel ttl: `サブエージェント呼び出し` → `Subagent 呼び出し` / `サブエージェント所要時間` → `Subagent 所要時間` (既) 維持 / `サブエージェント失敗率` → `Subagent 失敗率` (既) 維持 / sub-badge `種のサブエージェント` → `subagent types` |
| session / セッション | **`session` / `Session`** | th `セッション数` → `sessions` / footer `セッション` → `sessions` / KPI helpTtl `セッション数` 維持 (§2.5 参照) / sub-badge `セッションを集計` → `session(s) tracked` (実装は単複対応で `session(s)` 表記) |
| slash command / スラッシュコマンド | **`slash command`** | shell.html 内で日本語形が混じる箇所のみ書き換え。`/foo` literal はそのまま |
| hook / フック | **`hook` / `Hook`** | help body「PostToolUse hook」維持。文中で `フック` と書かれている箇所は `hook` に揃える |

#### B. 一般 stats / UI 英単語（**日本語に意訳** — issue 本文「日本語化候補」+ 洗い出し）

| 原語 | 訳語 / 表記 | 注記 / 代表的な置換例 |
|---|---|---|
| invocation / invocations | 呼び出し / 呼び出し回数 | help body の自然文 / aria-label / sub badge を日本語化。ただし KPI key・スキーマ名 (`invocation_count`)、および subagent ranking pop-body の `<code>1 invocation = 1 件</code>` のような **schema 用語並びの `<code>`-fenced literal** は英語維持 |
| dedup（issue 上の `deduce` の正体） | 重複排除 | help body の自然文中は日本語化（例: 時間帯ヒートマップ pop-body 「subagent は呼び出し単位に重複排除済み」）。subagent ranking pop-body の `<code>1 invocation = 1 件</code> に dedup` は schema 用語並びで **英語維持** (上記 invocation の例外と pair) |
| legend | 凡例 | help body「legend の数字は…」→「凡例の数字は…」 |
| mtime | 更新日時 | th `mtime` → `更新日時` / help body「mtime ≤14 日 / 未使用」→「更新日時 14 日以内 / 未使用」 |
| signal | 兆候 / シグナル | Quality lede「摩擦シグナルを可視化」→「摩擦の兆候を可視化」 / help body「逃した signal」→「逃した兆候」 |
| worst | 最多 / 最悪 | `worst session` → `最多セッション一覧` / aria-label / scope-note も同 |
| 上位漏れ | 上位 10×10 に含まれない組み合わせ | §2.4 参照 |
| 共起 | 同時利用 | panel ttl `Skill 同時利用マトリクス` / pop-ttl `Skill 同時利用` (§2.4) |
| 長尾分布 | 裾の長い分布（long tail） | percentile help body |

#### C. 各位置別の判断（**ユーザー判断 v4 反映** — ❶〜❿ の選択結果）

| カテゴリ | 採用 | 注記 |
|---|---|---|
| **❶ Hibernating skills** (panel title) | **`休眠スキル`** に翻訳 | v3-A 「skill → 英語」の例外。panel title context で片仮名「スキル」採用 (brand exception)。pop-ttl `未活用 / 新着検知` 維持、内部 chip `🌱 新着 / 💤 休眠 / 🪦 死蔵` 維持 |
| **❷ Overview / Patterns / Quality / Surface** (h1 / nav) | **英語維持** | ブランド化された page 名 |
| **❸ KPI tile `k:` キャプション** | **全英語維持** | `total events` / `skills` / `subagents` / `projects` / `sessions` / `resume rate` / `compactions` / `permission gate` を全て英語維持。chip 性が強い `k:` は §2.2 D の chip-style 短ラベルとして英語側に寄せる |
| **❸ KPI tile `s:` sub-caption** | **日本語化** | `unique kinds` → **`種類`**、`distinct cwds` → **`ディレクトリ単位`**、`<em>N</em> 日間の観測` 維持 |
| **❹ Sub-badge ラベル** (`pairs (top 100)` / `1 week only` / `subagent types` / `' skill(s)'` / `' hour buckets'` / `' covered '` 等) | **英語維持** | sub-badge は chip スタイル UI |
| **❺ Data-tooltip `<span class="lbl">` 値** (`events` / `share` / `prompts` / `invocations` / `rate` / `compacts` / `loads` / `mode` / `autonomy` / `total` / `30d` / `status` 等) | **英語維持** | tooltip の chip キャプション、日本語化すると tooltip 幅が広がりレイアウトが崩れる。**例外**: `(unknown)` 表示文字列は `（不明）` (全角括弧) に置換 — §2.4 と同系統の「不明な値」改訳として例外的に日本語化（worst-session 空 project セル / aria-label 内 literal `'unknown'` も対象） |
| **❻ `<th>` ヘッダー** | **Claude-spec のみ英語 + 統計記法のみ英語、他は日本語** | 詳細は下記マトリクス |
| **❼ Sparkline stats** (`peak` / `avg/day` / `active` / `window`) | **日本語化** | `ピーク` / `1 日あたり平均` / `稼働日数` / `期間` |
| **❽ Mode chip 値** | **`'🤝 Dual'` (sentence-case)** | MODE_LABEL / MODE_TIP 両方の chip 表示文字列を sentence-case で揃える (chip-tooltip parity) |
| **❾ panel title 形式** | **ハイブリッド** (`Skill 利用ランキング` 等) | 英語固有名詞 + 日本語助詞 |
| **❿ 二層ルール** (aria-label = prose 日本語 / tooltip lbl = chip 英語) | **採用** | §2.2 D 参照 |
| trend chip 値 (`accelerating` / `stable` / `decelerating` / `new`) | 既日本語化済 (📈 加速 / ➡️ 安定 / 📉 減速 / 🌱 新規) 維持 | プロジェクト固有用語、定着済 |
| status chip 値 (`warming_up` / `resting` / `idle`) | 既日本語化済 (🌱 新着 / 💤 休眠 / 🪦 死蔵) 維持 | 同上 |
| Top N / top 10 / top 20 / top 100 | **英語維持** | panel header / sub-badge `top N` 慣習が定着 |
| LLM / User | 両方英語維持 | chip pairing `🤖 LLM` / `👤 User` の対称性のため (footer aria-label の `User` を含む。`<th>👤 User</th>` は ❻ により日本語化検討対象 — 後述マトリクス参照) |
| `description` / `trigger` / `first_seen` / `last_seen` / `tool_count` / `slash_count` / `invocation_count` / `permission_rate` 等のスキーマ・フィールド名 | **英語維持** | `<code>` 化は「文言を編集している pop-body のみ」、未 touch の bare token は触らない (§1 Non-Goals 反映) |
| (unknown) | （不明） | worst-session 空 project の `(unknown)` → `（不明）` (全角) |

##### `<th>` 詳細マトリクス（❻ 適用）

| `<th>` 現状 | 採用 | 根拠 |
|---|---|---|
| `<th>Skill A</th>` / `<th>Skill B</th>` (Patterns / 共起) | **`<th>Skill A</th>` / `<th>Skill B</th>` 維持** | Claude-spec |
| `<th class="num">Sessions</th>` (Patterns / 共起) | **`<th class="num">Sessions</th>` 維持** | Claude-spec |
| `<th>Subagent</th>` (Quality / percentile) | **`<th>Subagent</th>` 維持** | Claude-spec |
| `<th class="num">Count</th>` (Quality / percentile) | **`<th class="num">件数</th>`** | 一般語 |
| `<th class="num">Samples</th>` (Quality / percentile) | **`<th class="num">サンプル数</th>`** | 一般語 |
| `<th class="num">avg</th>` (Quality / percentile) | **`<th class="num">平均</th>`** | 一般語 |
| `<th class="num">p50/p90/p99</th>` (Quality / percentile) | **維持** | 統計記法 |
| `<th>Skill</th>` (Quality / perm-skill, Surface / inv, life, hib) | **維持** | Claude-spec |
| `<th class="num">Prompts</th>` (Quality / perm) | **`<th class="num">プロンプト数</th>`** | 一般語 |
| `<th class="num">Invocations</th>` (Quality / perm) | **`<th class="num">呼び出し回数</th>`** | issue 本文「invocation 日本語化候補」 |
| `<th class="num">Rate</th>` (Quality / perm) | **`<th class="num">比率</th>`** | 一般語 |
| `<th>Subagent</th>` (Quality / perm-subagent) | **維持** | Claude-spec |
| `<th>Session</th>` (Quality / compact worst) | **維持** | Claude-spec |
| `<th>Project</th>` (Quality / compact worst) | **`<th>プロジェクト</th>`** | 一般語 |
| `<th class="num">Compacts</th>` (Quality / compact worst) | **維持** | Q1 ユーザー判断: Compact ファミリーは全英語維持 (Claude-spec 扱い) |
| `<th>Mode</th>` (Surface / inv) | **`<th>起動モード</th>`** | 一般語 |
| `<th class="num">🤖 LLM</th>` (Surface / inv) | **維持** | LLM = 略語 (英語慣習) |
| `<th class="num">👤 User</th>` (Surface / inv) | **`<th class="num">👤 ユーザー</th>`** | 一般語 (chip pairing 例外: column header は短く読める日本語へ) |
| `<th>LLM率</th>` (Surface / inv) | **維持** (既ハイブリッド日本語) | - |
| `<th>初回</th>` / `<th>直近</th>` / `<th class="num">30日件数</th>` / `<th class="num">全期間件数</th>` / `<th>トレンド</th>` (Surface / life) | **維持** | 既日本語 |
| `<th>状態</th>` (Surface / hib) | **維持** | 既日本語 |
| `<th>mtime</th>` (Surface / hib) | **`<th>更新日時</th>`** | issue 本文「mtime 日本語化候補」 |
| `<th>最終呼び出し</th>` / `<th class="num">経過</th>` (Surface / hib) | **維持** | 既日本語 |

#### D. **二層ルール** (prose vs chip): aria-label と tooltip lbl の扱い分岐

**iter3 P3 反映** — 同じ英単語でも **使われる場所** によって翻訳ルールが分岐するため、メタルールを明文化する:

- **prose 文字列** (aria-label / scope-note / lede / pop-body の地の文): **§2.2 B（一般語の日本語化）を適用**。スクリーンリーダー音読 / 自然文として読まれるので、日本語化したほうが読みやすい。
- **chip-style 短ラベル** (data-tooltip の `<span class="lbl">` 内 / sub-badge / panel sub-label / KPI tile `k:` `s:`): **§2.2 C（英語維持）を適用**。視覚密度が高い chip UI は短い英語の方が読みやすく、日本語化すると tooltip 幅が広がりレイアウトが崩れる。

**例**: `events` という同じ英単語が:
- aria-label `' events'` (sparkline day-band の prose) → **`' 件'`** (§2.2 B 適用)
- tooltip lbl `<span class="lbl">events</span>` (daily-tip の chip キャプション) → **維持** (§2.2 C 適用)

§4 Step 3 (line aria-label 部) と Step 5 (tooltip lbl 部) はこの二層ルールに基づいて分岐している。

### 2.3 表記揺れの統一（empty state — 全て **`no data`** に統一）

ユーザー方針 (v3): empty state プレースホルダーは **全て `no data`** に揃える。日本語と英語の混在を消し、テスト assertion も最小化する。

| 既存文言 | 統一後 | 出現箇所 |
|---|---|---|
| `no data` | `no data`（維持） | `20_load_and_render.js:89` |
| `共起データなし` | `no data` | `30_renderers_patterns.js:68` |
| `データなし` (projskill-empty) | `no data` | `30_renderers_patterns.js:108` |
| `subagent データなし` | `no data` | `40_renderers_quality.js:14` |
| `trend データなし` | `no data` | `40_renderers_quality.js:57` |
| `permission prompt なし` (×2) | `no data` | `40_renderers_quality.js:175, 203` |
| `compact なし` | `no data` | `40_renderers_quality.js:268` |
| `観測なし` (×3) | `no data` | `50_renderers_surface.js:26, 74, 118` |

トーンルール: 全 9 箇所が同一 literal `no data`。

### 2.4 意味不明な日本語の改訳

- **「上位漏れ」** (`shell.html:202`): プロジェクト × スキルの上位 10×10 行列に入りきらなかった残りを指している。→ **「上位 10×10 に含まれない組み合わせ」** に書き換え（または周辺文脈再構成）。
- **「共起」** (`shell.html:173,177`, `30_renderers_patterns.js:68`): セッション内で同時に使われたペア。→ **「同時利用」** に書き換え（panel ttl は「スキル同時利用マトリクス」/「スキル同時利用」）。help body の説明文も「同じセッション内で一緒に使われた…」と整合済みなので語感の繋がりが良い。tooltip kind key は data-tip 値なので**変更しない** (`data-tip="cooc"` は維持)。
- **「長尾分布」** (`shell.html:233`): 統計用語の long-tail。→ **「裾の長い分布（長尾, long tail）」** または **「外れ値の重い分布」**。help body 文脈は「avg だけでは見えない裾の長い分布を確認できる」が自然。

### 2.5 KPI 配列の各 entry: `k:` / `helpTtl:` / `s:` 整合性表

`20_load_and_render.js` の `kpis[]` 配列は `k:` (tile 小キャプション) + `helpTtl:` (popup タイトル) + `s:` (sub-caption) の **3 surface** を持つ。chip 性が強い `k:` は全英語維持、popup タイトルとして prose に近い `helpTtl:` は日本語化、`s:` は一般語のみ日本語化 — の 3 surface 分岐 (二層ルール §2.2 D の延長)。

| `id` | `k:` | `helpTtl:` | `s:` |
|---|---|---|---|
| `kpi-total` | `total events` | `総イベント数` | `<em>N</em> 日間の観測` |
| `kpi-skills` | `skills` | `スキル種別数` | `種類` |
| `kpi-subs` | `subagents` | `Subagent 種別数` | `種類` |
| `kpi-projs` | `projects` | `プロジェクト数` | `ディレクトリ単位` |
| `kpi-sess` | `sessions` | `セッション数` | （なし） |
| `kpi-resume` | `resume rate` | `Resume 率` | （なし） |
| `kpi-compact` | `compactions` | `Compact 数` | （なし） |
| `kpi-perm` | `permission gate` | `承認待ち` | （なし） |

#### Compact / Permission ファミリーの surface 分岐

- **Compact ファミリー**: k `'compactions'` / `<th>Compacts</th>` / tooltip lbl `compacts` は **英語維持**、helpTtl のみ `'Compact 数'` (日本語化)。
- **Permission ファミリー**: k `'permission gate'` / tooltip lbl `prompts` は **英語維持** (chip スタイル, §2.2 C ❺)、helpTtl `'承認待ち'` のみ日本語化。該当 `<th>` 列なし。

`Resume`、`LLM`、`Subagent` のように Claude Code 用語として広く通じる固有語は片仮名化せず英語綴り混じりを許容（`Resume 率` / `Compact 数` / `Subagent 種別数` の妥協形）。

### 2.6 期間トグル / footer / 接続バッジ

- 期間トグル `7d / 30d / 90d / 全期間` の半角 d は維持（既慣例）。aria-label `集計期間` も維持。
- footer `v0.7.2` は **plan 範囲外** だが、本 issue がマージされる時点で v0.7.3 release の sha256 更新と合流するため、bump は **release PR タイミング**で実施（patch-release skill の責務）。本 plan では触らない。
- 接続バッジ `STATUS_LABEL` の `● 接続中 / ○ 再接続中 / × 停止中 / — 静的レポート` は既に日本語化済 → 維持。

---

## 3. Critical Files

| ファイル | 役割 |
|---|---|
| `dashboard/template/shell.html` | DOM。h1 / lede / panel ttl / pop-ttl / pop-body / th / scope-note / footer / aria-label。最大の touch 範囲 |
| `dashboard/template/scripts/20_load_and_render.js` | KPI 定義 (`kpis` 配列の `k` / `s` / `helpTtl` / `helpBody`)、ranking renderer の `no data` / aria-label `uses`/`invocations` / sparkStats `k:` ラベル / spark の `peak N` text / sub バッジ `'days'` / `'active'` / sparkline 関連 inline 文字列 |
| `dashboard/template/scripts/30_renderers_patterns.js` | heatmap / 共起 / project×skill の sub バッジ (`hour buckets` / `pairs (top 100)` / `% covered`) と empty state |
| `dashboard/template/scripts/40_renderers_quality.js` | percentile / failure trend / permission breakdown / compact density の empty state, sub バッジ (`subagent types` / `weeks` / `tracked` / `1 week only`)、`(unknown)` |
| `dashboard/template/scripts/50_renderers_surface.js` | Surface 3 panel の MODE_LABEL chip 文字列 / TREND_LABEL / STATUS_LABEL（既日本語）/ aria-label 組み立て / sub バッジ / scope-note 「Lifecycle panel (上位 20 件)」 |
| `dashboard/template/scripts/90_data_tooltip.js` | data tooltip 内 lbl テキスト (`events` / `share` / `prompts` / `invocations` / `rate` / `compacts` / `loads` / `expansion` / `submit` / `mode` / `LLM` / `User` / `autonomy` / `total` / `30d` / `status`)、MODE_TIP / TREND_TIP / STATUS_TIP の表示文字列 |
| `tests/test_dashboard_template_split.py` | template sha256 を pin している。**touch する行は最後に sha256 値を新値に更新** + 各履歴 entry に Issue #89 の bullet を追加 |
| `tests/test_dashboard_*.py`（既存テスト） | 既存の文言 grep に依存している test がある場合は同期更新（要 grep 確認） |

参考（修正不要）:
- `dashboard/server.py` — UI 露出文字列なし。`_HTML_TEMPLATE` は shell + styles + scripts の concat 結果なので、本 plan は server.py を一切変更しない。
- `reports/export_html.py` — `render_static_html()` 経由で dashboard テンプレートを inline するだけ。自動反映。
- `reports/summary.py` — 対象外（§1 Non-Goals）。

---

## 4. Step-by-step 実装手順

ブランチ: `feature/89-dashboard-wording` を `v0.7.3` から派生。

### Step 0. 前作業
1. `v0.7.3` を最新化、`feature/89-dashboard-wording` を作成。
2. 全 13 scripts を一巡 grep し、§2.2 訳語表に**漏れている英語語**を洗い出して plan に追補（plan-as-living-document）。具体的には:
   ```
   grep -nE "[A-Za-z]" dashboard/template/scripts/*.js dashboard/template/shell.html | \
     grep -ivE "(class=|id=|data-|aria-|<code>|http|//|/\*|\*/)"
   ```
   を粗く回し、英単語が文中で UI 露出するものに印を付ける。
3. 念のため §5.2 forbidden list の **case sensitivity sanity grep**:
   ```
   grep -niE "🤝 dual|🤖 llm-only|👤 user-only|🤝 mixed" \
     dashboard/template/scripts/*.js
   ```
   大文字小文字違いの取りこぼし（例: `Mixed` vs `mixed`）を念のため確認。
4. **代替設計の意識化**: ゴールデンファイル方式（`tests/fixtures/expected_template_wording.txt` を 1 本置き diff 比較）も検討したが、本 plan では **「forbidden / required / invariant」3 軸方式を採用**。理由は (a) 何を契約しているかが test 名で明示される、(b) regression diff の出力が読みやすい (失敗 string 単位)、(c) ゴールデンファイル全体の sha256 と shell.html sha256 fixture が二重維持になる、の 3 点。後年「全 UI 文字列の網羅 snapshot が欲しい」となったら別 issue で golden 方式への置き換えを検討する。
5. **forbidden 候補の現状存在 sanity probe** (iter2 P1 対応): §5.2 test 1 の forbidden list の各 entry が **現在の assembled template に literal として実在する** ことを確認:
   ```python
   from tests._dashboard_template_loader import load_assembled_template
   t = load_assembled_template()
   for s in FORBIDDEN_LIST:
       assert s in t, f"forbidden 候補 {s!r} がテンプレに存在しない (vacuous assertion)"
   ```
   現状で存在しない literal を forbidden に入れると Red→Green が空振りする (TDD 失敗信号が立たない)。動的構築 (`countLabel` ternary など) は **literal 形ではなく ternary 式そのもの** (`'invocations' : 'uses'`) を forbidden に書く。

### Step 1. TDD: テスト先行（失敗を確認）

テスト戦略は §5 で詳述。ここでは順序のみ。

1. 新規 test ファイル `tests/test_dashboard_wording.py` を作成（後述 §5 で内容定義）。これ自体を **iteration の独立 commit** として残す（`test(dashboard): add wording assertions for #89 (RED)`）。
2. `_dashboard_template_loader.load_assembled_template()` でテンプレートを読み、**Positive assertion**（変更後に期待される文字列が含まれる）と **Negative assertion**（旧英語文字列が含まれない）を書く。
3. 失敗を実行確認: `python3 -m pytest tests/test_dashboard_wording.py -v` → 全部 fail することを確認（Red）。
4. 既存 `test_dashboard_template_split.py` の sha256 assertion も同タイミングで fail し始める。これは Step 6 の **専用コミット** で sha256 を bump して直す（一時 xfail / skip にはしない、Red 状態で commit を残す）。

### Step 2. shell.html の修正（v3 方針反映: Claude-spec → 英語、一般語 → 日本語、empty state → `no data`）

カテゴリ別に編集。1 セクション = 1 commit を目安に細かく刻む（reviewer の負荷軽減）。

1. **Overview header / lede**: `Claude Code Usage Overview` h1 維持。lede `events · days observed · projects` の表記は **英語維持** (`events`/`projects` は KPI key と同じ chip スタイルなので)。日本語化する必要はない。
2. **Overview パネル群**:
   - panel ttl `スキル利用ランキング` → **`Skill 利用ランキング`** (Claude-spec → 英語、§2.2 A)
   - panel ttl `サブエージェント呼び出し` → **`Subagent 呼び出し`**
   - panel ttl `日別利用件数の推移` 維持 (Claude-spec 不在)
   - panel ttl `プロジェクト分布` 維持
   - help body 内の英文混在は §2.2 B に従い: `dedup` → 「重複排除」、`legend` → 「凡例」、`active` 文章中 → 「稼働日」、`peak` 文章中 → 「ピーク日」(これらは一般語の日本語化候補)
   - ただし help body 中の `skill` / `subagent` / `session` は §2.2 A に従い英語維持
3. **Patterns ページ**:
   - panel ttl `スキル共起マトリクス` → **`Skill 同時利用マトリクス`** (skill は英語化、共起 → 同時利用)
   - pop-ttl `スキル共起` → **`Skill 同時利用`**
   - `プロジェクト × スキル` の panel ttl: `プロジェクト × スキル` → **`Project × Skill`** (Skill は §2.2 A、Project は ❻ で `<th>` レベルで日本語化対象だが、panel ttl 形式は §2.2 ❾ ハイブリッド慣習のため英語名を残す)
   - pop-body「上位漏れは表示しないが」→「上位 10×10 に含まれない組み合わせは表示しないが」
   - `<th>Skill A</th><th>Skill B</th><th class="num">Sessions</th>` → **全て維持** (Skill A/B/Sessions = Claude-spec、❻ マトリクス参照)
4. **Quality ページ**:
   - h1 `Quality` 維持、lede `実行品質と摩擦シグナルを可視化します。` → **`実行品質と摩擦の兆候を可視化します。`** (signal → 兆候、§2.2 B)
   - panel ttl `Subagent 所要時間 (percentile)` 維持 (Subagent Claude-spec、percentile 統計記法)
   - pop-body 「avg 平均値だけでは見えない長尾分布を…」→ **「平均だけでは見えない、裾の長い分布 (long tail) を…」** (avg → 平均、長尾分布 改訳)
   - `<th>Subagent</th><th class="num">Count</th><th class="num">Samples</th><th class="num">avg</th><th class="num">p50/p90/p99</th>` → **`<th>Subagent</th>` 維持 / `Count` → `件数` / `Samples` → `サンプル数` / `avg` → `平均` / `p50/p90/p99` 維持** (❻)
   - panel ttl `Subagent 失敗率 (週次)` 維持
   - pop-body「default で count 上位 5 type に絞って描画」→「既定で件数上位 5 種に絞って描画」
   - panel ttl `Permission prompt × skill (top 10)` / `Permission prompt × subagent (top 10)` 維持 (panel ttl 英語慣習、❷)
   - 同 pop-body 内の `permission notification` → 「権限通知」、`disjoint` → 「skill / subagent 重複なし」、`interval-cover` → 「実行区間内」、`clamp` → 「上限 1.0 で打ち切らない」、「subagent invocation の execution interval」→「subagent 呼び出しの実行区間」
   - `<th>Skill</th><th class="num">Prompts</th><th class="num">Invocations</th><th class="num">Rate</th>` → **`<th>Skill</th>` 維持 / `Prompts` → `プロンプト数` / `Invocations` → `呼び出し回数` / `Rate` → `比率`** (❻)
   - `<th>Subagent</th><th class="num">Prompts</th><th class="num">Invocations</th><th class="num">Rate</th>` → 同上
   - panel ttl `Compact 発生密度 (per session)` 維持
   - pop-body 内 `worst session` → 「最多セッション一覧」、`signal` → 「兆候」、`タイミングを逃した signal` → 「タイミングを逃した兆候」
   - `<th>Session</th><th>Project</th><th class="num">Compacts</th>` → **`<th>Session</th>` 維持 / `Project` → `プロジェクト` / `Compacts` 維持** (❻、Q1: Compact ファミリー全英語維持)
5. **Surface ページ**:
   - lede `スキルが「呼ばれているか」「育っているか」「使われていないか」を 3 panel で可視化します。` → **`Skill が「呼ばれているか」「育っているか」「使われていないか」を 3 つのパネルで可視化します。`** (skill 英語化、3 panel → 3 つのパネル)
   - panel ttl `Skill 起動経路 (top 20)` 維持
   - pop-ttl `LLM 自律 vs ユーザー手動` 維持
   - pop-body 内 `description` / `trigger` → `<code>description</code>` / `<code>trigger</code>` (§2.2 C: 触っている line のみ retrofit)
   - `<th>Skill</th><th>Mode</th><th class="num">🤖 LLM</th><th class="num">👤 User</th><th>LLM率</th>` → **`Skill` 維持 / `Mode` → `起動モード` / `🤖 LLM` 維持 / `👤 User` → `👤 ユーザー` / `LLM率` 維持** (❻)
   - panel ttl `Skill lifecycle (top 20)` 維持
   - pop-ttl `初回 / 直近 / トレンド` 維持
   - pop-body の `first_seen` / `last_seen` 等の schema 名は `<code>` で囲う retrofit
   - `<th>Skill</th><th>初回</th>…<th>トレンド</th>` 維持（既日本語）
   - **panel title `Hibernating skills` → `休眠スキル`** (❶: ユーザー判断による翻訳。本 plan 唯一の panel-title-level 片仮名「スキル」採用)
   - pop-body 内: 「14日以内に呼ばれた skill は active として除外」→「直近 14 日以内に呼ばれた skill は稼働中として除外」、`Plugin-bundled skill` → `Plugin 同梱の skill`、`cross-reference` → 「突合せ」、`mtime` → 「更新日時」
   - `<th>Skill</th><th>状態</th><th>mtime</th><th>最終呼び出し</th><th class="num">経過</th>` → **`Skill` 維持 / `状態` 維持 / `mtime` → `更新日時` / 他維持** (❻)
6. **共通 footer**: footer の `<span class="k">セッション</span>` → **`<span class="k">sessions</span>`** に書き換える (§2.2 A 反映、iter3 P2 反映: §2.2 訳語表が normative source)。`<span class="k">最終更新</span>` / `aria-label` / 接続バッジ (`● 接続中` 等) / クレジット部 (`stdlib only · no third-party js`) は **触らない** (Claude-spec 不在 or 既日本語)。

### Step 3. scripts/20_load_and_render.js（KPI / sparkline / ranking）

方針: KPI tile `k:` は **❸ 全英語維持** (chip スタイル)、`helpTtl:` は **prose 扱いで日本語化**、`s:` は **一般語のみ日本語化**、sparkline stats は **❼ 日本語化**、sub-badge は **❹ 英語維持**、aria-label は **❿ 二層ルールで日本語化**。

- `kpis` 配列を §2.5 整合性表どおりに更新:
  - `k:` `total events` / `skills` / `subagents` / `projects` / `sessions` / `resume rate` / `compactions` / `permission gate` を **全て英語維持**
  - `s:` `unique kinds` (×2) → **`種類`**、`distinct cwds` → **`ディレクトリ単位`**、`<em>N</em> 日間の観測` 維持
  - `helpTtl:` `'Permission Prompt'` → **`'承認待ち'`** (Permission ファミリーの helpTtl のみ日本語化)。その他の helpTtl はすべて既日本語維持 (`'Compact 数'` / `'Subagent 種別数'` / `'Resume 率'` 等)
  - `helpBody` 内の `legend` などは §2.2 B に従い書き換え (touch している line のみ `<code>` retrofit 対象)。subagent ranking の `<code>1 invocation = 1 件</code> に dedup` は schema 用語並びの例外として **英語維持**
- `'<div … >no data</div>'` → **`no data` 維持** (§2.3)
- aria-label 組み立て `+ ' invocations' : ' uses'` → **`+ ' 呼び出し' : ' 件'`** (§2.2 B + ❿)
- sparkStats の `peak` / `avg/day` / `active` / `window` ラベル → **`ピーク` / `1 日あたり平均` / `稼働日数` / `期間`** (❼ 日本語化)
- spark SVG 内の `<text>peak N</text>` → そのまま英語維持 (SVG fixed text、日本語フォント描画安定性のため例外)
- sub-badge `' days · ' + active + ' active'` → **英語維持** (sub-badge ❹)
- footer の `(ss.total_sessions || 0) + ' sessions'` → **英語維持** (`sessions` Claude-spec)
- aria-label `' events'` → **`' 件'`** (aria prose 日本語化 ❿)、`' uses'` / `' invocations'` → **`' 件'` / `' 呼び出し'`**

### Step 4a. scripts/30_renderers_patterns.js (heatmap / 同時利用 / project×skill)

**1 commit**: `feat(dashboard): translate Patterns renderer wording (Issue #89)`

- empty state: `共起データなし` → **`no data`** / `データなし` (projskill-empty) → **`no data`** (§2.3)
- sub-badge:
  - heatmap: `' events · ' + buckets.length + ' hour buckets'` → **英語維持** (chip スタイル、§2.2 C)
  - cooccurrence: `list.length + ' pairs (top 100)'` → **英語維持**
  - projskill: `' projects × ' + skills.length + ' skills'` → **英語維持**、`' covered '` → 維持
- aria-label (prose):
  - heatmap cell `'... — ' + c + ' events'` → **`'... — ' + c + ' 件'`** (一般語 prose 日本語化)
  - cooc row `' sessions'` → **`' sessions' 維持`** (Claude-spec 英語維持、§2.2 A)
  - projskill cell `' events'` → **`' 件'`** (一般語 prose 日本語化)

### Step 4b. scripts/40_renderers_quality.js (percentile / failure trend / permission / compact density)

**1 commit**: `feat(dashboard): translate Quality renderer wording (Issue #89)`

- empty state を §2.3 統一表に: `subagent データなし` / `trend データなし` / `permission prompt なし` (×2) / `compact なし` → 全て **`no data`**
- sub-badge:
  - percentile: `' subagent types'` → **`' subagent types'` 維持** (§2.2 A: subagent 英語維持、`types` は chip スタイル英語維持)
  - failure trend: `'1 week only'` / `weeks.length + ' weeks'` / `' types'` → 全て **英語維持** (chip スタイル)
  - permission: `' skill(s)'` / `' subagent type(s)'` → **英語維持** (§2.2 A + chip スタイル)
  - compact: `' session(s) tracked'` → **英語維持**
- aria-label (prose):
  - percentile row 維持（p50/p90/p99 統計記法）
  - permission row `' prompts / ' + inv + ' invocations'` → **`' prompts / ' + inv + ' 呼び出し'`** (`prompts` は chip 単位英語維持、invocation は日本語化候補)。実装上 prose と chip が分離されている tooltip 側 (§5) と整合
  - histogram bar `' compact(s): ' + c + ' session(s)'` → **英語維持** (chip 単位)
  - worst-session row `' compacts'` → **英語維持** (`compacts` は chip 単位)、`'unknown'` (aria-label literal) → **`'不明'`** (一般語 prose)、`(unknown)` 表示文字列は **`（不明）`**（全角括弧、§2.2 C）

### Step 4c. scripts/50_renderers_surface.js (Surface 3 panel + chip / scope-note)

**1 commit**: `feat(dashboard): translate Surface renderer wording (Issue #89)`

- empty state: `観測なし` (×3) → 全て **`no data`** (§2.3)
- sub-badge: 各 panel `' skill(s)'` → **英語維持** (§2.2 A + chip スタイル)
- `MODE_LABEL` chip 表示文字列を **sentence-case 統一** (chip スタイル英語維持):
  ```js
  const MODE_LABEL = {
    'dual':      '🤝 Dual',     // 旧: '🤝 dual' (大文字統一で chip 一貫性)
    'llm-only':  '🤖 LLM-only', // 旧: '🤖 llm-only'
    'user-only': '👤 User-only', // 旧: '👤 user-only'
  };
  ```
  **キー文字列 (`'dual'`, `'llm-only'`, `'user-only'`) は変えない。値の casing だけ大文字化** (chip 表示の一貫性のため)。
- scope-note 「Lifecycle panel (上位 20 件) で見えます」→ **`Lifecycle panel (上位 20 件) で見えます` 維持** (Claude-spec 系 panel 名は英語維持)。
- aria-label (prose) の組み立て文字列:
  - `LLM` 維持 (略語英語)
  - `User` → **維持** (chip pairing で `LLM` と対称、§2.2 C)
  - `autonomy` 維持 (chip 単位)

### Step 5. scripts/90_data_tooltip.js（data tooltip 本文）

**v3 方針**: data tooltip の lbl は chip 単位（小さなカード型 UI）なので **英語維持** がデフォルト。意訳すると tooltip 幅が広がりレイアウトが崩れる懸念がある (§6)。Claude-spec 用語 (skill / subagent / session) も英語維持。

→ **本 Step 5 の文言変更は最小**:

- `kind === 'daily'`: `events` → **維持**
- `kind === 'proj'`: `events` / `share` → **維持**
- `kind === 'heatmap'`: `events` → **維持**
- `kind === 'rank'`: `countLabel` の `' invocations' : ' uses'` → **維持** (chip 単位)、`fail` / `avg` → **維持**
- `kind === 'cooc'`: `sessions` → **維持** (Claude-spec 英語維持、chip 単位)
- `kind === 'projskill'`: `events` → **維持**
- `kind === 'percentile'`: `p50/p90/p99` → **維持**
- `kind === 'trend'`: 維持
- `kind === 'perm-skill'/'perm-subagent'`: `prompts` / `invocations` / `rate` → **維持** (chip 単位)
- `kind === 'histogram'`: `' compact(s)'` / `sessions` → **維持**
- `kind === 'worst-session'`: `project` / `compacts` → **維持**、`(unknown)` 表示文字列 → **`（不明）`** (全角、§2.2 C 例外: 「不明な値」を示す日本語ラベルは固有 UX 改善として日本語化)
- `kind === 'source'`: `expansion` / `submit` / `rate` → **維持**
- `kind === 'instr-bar'` / `'glob'`: `loads` → **維持**
- `kind === 'inv'`:
  - `MODE_TIP` の **キー文字列は触らない** (§1 Non-Goals: バグ温存)。
  - **値は MODE_LABEL と表示一致** させる (chip ↔ tooltip parity、iter2 reviewer P4 反映):
    ```js
    const MODE_TIP = {
      'llm-only':  '🤖 LLM-only',  // 旧: '🤖 LLM-only' → 維持
      'user-only': '👤 User-only', // 旧: '👤 User-only' → 維持
      'mixed':     '🤝 Dual',      // 旧: '🤝 Mixed' → MODE_LABEL['dual']='🤝 Dual' と一致させる
    };
    ```
    **重要**: MODE_TIP の **値** を `'🤝 Dual'` にすることで、`mode='dual'` が runtime で MODE_TIP[mode] lookup に失敗しても、旧バグで `'dual'` literal が表示される既存挙動 (`🤝 Dual` ではなく素の `'dual'`) は残る。**しかし**、ある日「`MODE_TIP` の `'mixed'` キーを `'dual'` に rename する」別 issue が動いたとき、UI の見え方は `🤝 Dual` のまま変わらない (= UX 回帰なし)。
  - `mode` / `LLM` / `User` / `autonomy` → **維持** (chip 単位)
- `kind === 'life'`: `30d` / `total` / `trend` → **維持**
- `kind === 'hib'`: `STATUS_TIP` 値の説明文 (`mtime ≤14 日 / 未使用` / `15〜30 日未使用` / `30 日以上未使用`) は既日本語化済 → 維持。ただし `mtime` は §2.2 B に従い「更新日時」化: `mtime ≤14 日 / 未使用` → 「更新日時 14 日以内 / 未使用」。`status` lbl → **維持**

### Step 6. テスト更新（**専用コミット 2 本に分割**）

#### Step 6.1. sha256 fixture bump (atomic commit)
**1 commit**: `chore(test): bump dashboard template sha256 for #89`

- `test_dashboard_template_split.py` の `EXPECTED_TEMPLATE_SHA256` を新値に更新し、履歴コメントに本 issue の bullet を 1 行追記:
  ```
  #   - <new-sha>...: Issue #89 / Dashboard 文言全体修正 (英語残存 / 表記揺れ / 難解日本語の整理)
  ```
- このコミットは「機械的更新のみ」と reviewer / future bisect に明示するため、wording 関連の修正と**混ぜない**。

#### Step 6.2. 回帰確認 + 既存 test の grep 衝突追従
- Step 1 で書いた `tests/test_dashboard_wording.py` が全 green になることを確認。
- 既存テストで grep に依存しているもの（例: `test_dashboard_period_toggle.py` で `'7d'` をチェック等）を full pytest で回し、回帰を検出:
  ```
  python3 -m pytest tests/ -x
  ```
- 既存の `test_export_html.py` も実行し、`render_static_html()` 経由の HTML 出力に新文言が反映されていることを確認。
- 衝突が発生した場合は **同じコミット内 (`test: follow up grep assertions for #89 wording`)** で修正する。

### Step 7. 仕上げ
- 全ファイル末尾に空行 1 行（CLAUDE.md 規約）が保たれているか確認。
- shell.html の `lang="ja"` 維持確認。
- PR description に「変更前 / 変更後の対照表」を貼る（reviewer ボット対策。§6 参照）。
- export_html を 1 度実行して目視確認（任意）。

---

## 5. TDD test plan

新規 test ファイル `tests/test_dashboard_wording.py` を作成。stdlib only。テスト戦略は **assembled template に対する文字列 assertion** を中心とする。

### 5.1 共通 fixture
- `tests/_dashboard_template_loader.load_assembled_template()` を import して 1 度だけテンプレ読み込み。`@functools.lru_cache` 済なので各テストは無料に近い。

### 5.2 テストケース構成（pytest 関数 6〜8 本）

> **assertion target 注記** (本テストファイル全体): 検証対象は `load_assembled_template()` が返す **assembled template の生文字列** (= shell.html + concat 済 styles + concat 済 scripts の literal source)。**ランタイムの DOM ではない**。したがって forbidden / required の文字列は JS コード内のクォート付き string literal を含む形 (`"'🤝 両方'"` のように) で書き、テンプレ source 上の文字列マッチを行う。

1. **`test_no_residual_english_labels`**: 旧文言（v3 方針で書き換え対象）が消えていること。
   ```python
   forbidden = [
       # Claude-spec 片仮名（→ 英語形に統一: §2.2 A）
       "スキル利用ランキング",        # → "Skill 利用ランキング"
       "サブエージェント呼び出し",      # → "Subagent 呼び出し"
       "<span class=\"pop-ttl\">スキル共起</span>",  # → "Skill 同時利用"
       "スキル共起マトリクス",        # → "Skill 同時利用マトリクス"
       "プロジェクト × スキル",       # panel ttl → "Project × Skill"
       "スキルが「呼ばれているか」",   # Surface lede → "Skill が「呼ばれているか」"

       # Empty state (§2.3 全て `no data` に統一)
       "共起データなし",
       ">データなし<",                  # projskill-empty (`<div class="projskill-empty">データなし</div>`)
       "subagent データなし",
       "trend データなし",
       "permission prompt なし",
       "compact なし",
       "観測なし",

       # 意味不明な日本語 (§2.4)
       "上位漏れ",
       "長尾分布",

       # 一般語日本語化 (§2.2 B)
       "実行品質と摩擦シグナルを可視化します",  # → "実行品質と摩擦の兆候を可視化します"
       ">mtime<",                       # `<th>mtime</th>` → `<th>更新日時</th>` (Surface hib panel th)
       "mtime ≤14 日 / 未使用",         # tooltip STATUS_TIP → "更新日時 14 日以内 / 未使用"
       "タイミングを逃した signal",      # shell.html:347 (Compact pop-body) → "タイミングを逃した兆候"
       "上位漏れは表示しないが",         # → "上位 10×10 に含まれない…"

       # footer Claude-spec 用語 (§2.2 A) — iter3 P2 反映
       ">セッション</span>",            # footer `<span class="k">セッション</span>` → `<span class="k">sessions</span>`

       # Hibernating skills 翻訳 (❶, v4)
       "Hibernating skills",            # panel title → "休眠スキル"

       # KPI tile s: 一般語日本語化 (❸, §2.5)
       # ※ k: の「カードタイトル」は user follow-up により全英語維持で確定。
       # forbidden には残さない (total events / projects / resume rate / permission gate / compactions)。
       "s: 'unique kinds'",             # → "種類"
       "s: 'distinct cwds'",            # → "ディレクトリ単位"

       # KPI helpTtl 翻訳 (Permission ファミリーのみ)
       "helpTtl: 'Permission Prompt'",  # → "helpTtl: '承認待ち'"

       # `<th>` 一般語日本語化 (❻, v5 反映: Compact 維持)
       "<th class=\"num\">Count</th>",
       "<th class=\"num\">Samples</th>",
       "<th class=\"num\">avg</th>",
       "<th class=\"num\">Prompts</th>",
       "<th class=\"num\">Invocations</th>",
       "<th class=\"num\">Rate</th>",
       "<th>Project</th>",
       "<th>Mode</th>",
       "<th class=\"num\">👤 User</th>",
       # ※ `<th class="num">Compacts</th>` は forbidden に入れない (Q1: Compact 維持)

       # Sparkline stats (❼ 日本語化)
       "k: 'peak'",
       "k: 'avg/day'",
       "k: 'active'",
       "k: 'window'",

       # MODE_LABEL chip 旧 lowercase (Step 4c で大文字化)
       "'🤝 dual'", "'🤖 llm-only'", "'👤 user-only'",
       # MODE_TIP 旧 'Mixed' (Step 5 で 'Dual' に変更)
       "'🤝 Mixed'",

       # 動的構築の TERNARY EXPR (forbidden を literal 形では grep できないので式そのもの)
       "' invocations' : ' uses'",      # 20_load_and_render.js aria-label ternary
   ]
   for s in forbidden:
       assert s not in template, f"{s!r} がテンプレに残存している"
   ```

2. **`test_required_new_labels_present`**: 新ラベルが追加されていること（positive assertion）。
   ```python
   required = [
       # Claude-spec 英語 (§2.2 A)
       "Skill 利用ランキング",
       "Subagent 呼び出し",
       "Skill 同時利用マトリクス",
       "<span class=\"pop-ttl\">Skill 同時利用</span>",
       "Project × Skill",
       "Skill が「呼ばれているか」",

       # Empty state (§2.3)
       ">no data<",                     # `class="empty"` セルの中身

       # 意味不明な日本語の改訳 (§2.4)
       "上位 10×10 に含まれない",
       "裾の長い分布",
       "Skill 同時利用",                # panel pop-ttl

       # 一般語日本語化 (§2.2 B)
       "実行品質と摩擦の兆候を可視化します",
       "更新日時 14 日以内 / 未使用",      # STATUS_TIP
       "<th>更新日時</th>",              # Surface hib
       "タイミングを逃した兆候",          # shell.html:347 forbidden の対称

       # footer Claude-spec (§2.2 A) — iter3 P2 反映
       ">sessions</span>",              # footer `<span class="k">sessions</span>`

       # Hibernating skills 翻訳 (❶, v4)
       "休眠スキル",                    # panel title

       # KPI tile s: 日本語化 (❸, §2.5)
       # ※ k: は user follow-up により全英語維持。required にも pin しない
       # (total events / projects / resume rate / permission gate / compactions / skills / subagents / sessions)。
       "s: '種類'",
       "s: 'ディレクトリ単位'",

       # KPI helpTtl 翻訳 (Permission ファミリーのみ)
       "helpTtl: '承認待ち'",

       # `<th>` 日本語化 (❻, v5 反映: Compact 維持)
       "<th class=\"num\">件数</th>",
       "<th class=\"num\">サンプル数</th>",
       "<th class=\"num\">平均</th>",
       "<th class=\"num\">プロンプト数</th>",
       "<th class=\"num\">呼び出し回数</th>",
       "<th class=\"num\">比率</th>",
       "<th>プロジェクト</th>",
       "<th>起動モード</th>",
       "<th class=\"num\">👤 ユーザー</th>",

       # Sparkline stats (❼)
       "k: 'ピーク'",
       "k: '1 日あたり平均'",
       "k: '稼働日数'",
       "k: '期間'",

       # MODE_LABEL chip 大文字統一 (§2.2 C / Step 4c)
       "'🤝 Dual'", "'🤖 LLM-only'", "'👤 User-only'",
       # MODE_TIP 値も 'Dual' で MODE_LABEL と表示一致 (iter2 P4: chip-tooltip parity)
       # (key は 'mixed' のまま)
       # 既存の '🤖 LLM-only' と '👤 User-only' は維持なので require ではない

       # 動的構築の TERNARY EXPR 新形 (forbidden 表式の対称)
       "' 呼び出し' : ' 件'",            # 20_load_and_render.js aria-label
   ]
   for s in required:
       assert s in template, f"{s!r} がテンプレに見当たらない"
   ```

3. **`test_invariant_keys_unchanged`**: data-\* / class / id / page key が変わっていないこと（§1 Non-Goals 構造保証）。
   ```python
   invariants = [
       'data-page="overview"', 'data-page="patterns"',
       'data-page="quality"', 'data-page="surface"',
       'data-page-link="overview"',
       'data-tip="rank"', 'data-tip="cooc"', 'data-tip="projskill"',
       'data-tip="percentile"', 'data-tip="trend"',
       'data-tip="perm-skill"', 'data-tip="perm-subagent"',
       'data-tip="histogram"', 'data-tip="worst-session"',
       'data-tip="inv"', 'data-tip="life"', 'data-tip="hib"',
       "'dual'", "'llm-only'", "'user-only'",  # MODE_LABEL key
       "'mixed'",  # MODE_TIP key（バグ温存の証拠）
       "'accelerating'", "'stable'", "'decelerating'", "'new'",
       "'warming_up'", "'resting'", "'idle'",
       'id="kpi-total"', 'id="kpi-skills"', 'id="kpi-subs"',
       'id="kpi-projs"', 'id="kpi-sess"', 'id="kpi-resume"',
       'id="kpi-compact"', 'id="kpi-perm"',
       'id="dataTooltip"', 'id="liveToast"', 'id="connStatus"',
   ]
   for s in invariants:
       assert s in template, f"invariant key {s!r} が消えた"
   ```

   さらに **paired-negative invariants + chip-tooltip parity** で MODE_TIP / MODE_LABEL のキー境界の **不可逆性** + **表示文字列の一致** を契約化する（§7 で `dual`↔`mixed` バグを別 issue にデフェルした構造保証 + iter2 P2/P4 反映）:
   ```python
   import re
   # MODE_TIP は 90_data_tooltip.js 内、MODE_LABEL は 50_renderers_surface.js 内。
   # concat 後の template 全文から各 const 宣言ブロックを切り出して block-scoped に判定する。
   # iter2 P2: 早期 sanity assert で正規表現 fail 時の AttributeError を自己診断的なメッセージに

   mode_tip_match = re.search(r"const MODE_TIP\s*=\s*\{([^}]+)\}", template)
   assert mode_tip_match, "MODE_TIP 宣言ブロックが見つからない (90_data_tooltip.js の構造変化を疑え)"
   mode_tip_block = mode_tip_match.group(1)

   mode_label_match = re.search(r"const MODE_LABEL\s*=\s*\{([^}]+)\}", template)
   assert mode_label_match, "MODE_LABEL 宣言ブロックが見つからない (50_renderers_surface.js の構造変化を疑え)"
   mode_label_block = mode_label_match.group(1)

   # === Paired-negative key invariants (バグ温存契約) ===
   # MODE_TIP には 'mixed' があり、'dual' は無い (本 issue ではバグを温存し別 issue で修正)
   assert "'mixed'" in mode_tip_block, "MODE_TIP の 'mixed' キーが消えた (バグ温存契約)"
   assert "'dual'" not in mode_tip_block, "MODE_TIP に 'dual' を追加してはならない (別 issue)"

   # MODE_LABEL には 'dual' があり、'mixed' は無い (既存設計)
   assert "'dual'" in mode_label_block, "MODE_LABEL の 'dual' キーが消えた"
   assert "'mixed'" not in mode_label_block, "MODE_LABEL に 'mixed' を追加してはならない"

   # === Chip ↔ tooltip parity (iter2 P4: 表示文字列が両ブロックで一致する契約) ===
   # MODE_LABEL[dual] と MODE_TIP[mixed] は **同じ表示文字列** を持つ。
   # これにより別 issue で 'mixed' → 'dual' rename しても UX 表示が変わらず、
   # かつ runtime で素の 'dual' literal が MODE_TIP lookup miss で表示される現バグも維持される。
   assert "'🤝 Dual'" in mode_label_block, "MODE_LABEL の 'dual' 値は '🤝 Dual'"
   assert "'🤝 Dual'" in mode_tip_block, "MODE_TIP の 'mixed' 値も MODE_LABEL と同じ '🤝 Dual'"
   # 両方が共通して持っているべき他の chip 値
   assert "'🤖 LLM-only'" in mode_label_block and "'🤖 LLM-only'" in mode_tip_block
   assert "'👤 User-only'" in mode_label_block and "'👤 User-only'" in mode_tip_block
   ```

4. **`test_period_toggle_labels_intact`**: `7d` / `30d` / `90d` / `全期間` のボタン文言が維持されていること（§2.6）。

5. **`test_kpi_help_titles_localized`**: `helpTtl: '...'` の値がすべて日本語であることを正規表現で軽く確認（過渡期の取りこぼし検出）。
   ```python
   import re
   # helpTtl の値を抽出（quote は single/double どちらも許容）
   help_ttls = re.findall(r"""helpTtl:\s*['"]([^'"]+)['"]""", template)
   # iter2 C3 反映: 「`kpis` 配列の entry 数」と「helpTtl 数」が一致することを cross-ref で assert
   # → KPI tile の追加 / 削除どちらも未対応なら catch される
   kpi_entries = re.findall(r"id:\s*'kpi-[a-z]+'", template)
   assert len(help_ttls) == len(kpi_entries), \
       f"helpTtl 数 {len(help_ttls)} ≠ KPI entry 数 {len(kpi_entries)}"
   for ttl in help_ttls:
       # ASCII printable のみで構成された helpTtl は無いはず（"Resume 率" 等 mix は OK）
       assert not re.fullmatch(r"[\x20-\x7E]+", ttl), f"{ttl} が完全 ASCII"
   ```

6. **`test_empty_state_messages_unified`**: 全 empty state 文言が **`no data`** に統一されていること（v3 方針 §2.3）。
   ```python
   import re
   # word-boundary に厳密化: class 値が "empty" 単体 か "empty <修飾>" / "<修飾> empty"
   # の形のみマッチ。`empty-row` / `empty-state-warn` のような派生 class 名は除外する。
   pattern = re.compile(r'class="(?:[^"]*\s)?empty(?:\s[^"]*)?">([^<]+)<')
   matches = pattern.findall(template)
   assert matches, "empty 状態セルが 1 件もマッチしない (regex 不一致の可能性)"
   # `no data` 完全一致 (前後 trim はせず literal で比較。trim 必要なら後年再調整)
   for txt in matches:
       assert txt.strip() == "no data", f"empty state {txt!r} が 'no data' でない"
   ```

7. **`test_template_sha256_updated`**: `test_dashboard_template_split.EXPECTED_TEMPLATE_SHA256` が更新されていることを暗黙にカバー（既存テストが green であれば OK）。新規 test では明示的に書かない。

8. **（任意）`test_export_html_round_trip_uses_new_wording`**: `tests/test_export_html.py` 系の既存テストにラベル assertion を 1 件追加（例: `"スキル A"` が出力 HTML に含まれる）して export 経路の自動反映を最終確認。

### 5.3 TDD 順序（コミット単位 1:1 対応）

コミットメッセージ書式は最近の v0.7.3 ブランチ慣習 (`(Issue #85)` `(Issue #85 follow-up)`) に揃える。

| Phase | コミット | 期待状態 |
|---|---|---|
| Red 1 | `test(dashboard): add wording assertions (Issue #89, RED)` (Step 1) | wording test 全 fail / sha256 test 既存合格のまま |
| Red 2 | （sha256 test は wording 修正開始時に自動的に fail する。明示コミットなし） | sha256 test fail に変わる |
| Green-html | `feat(dashboard): translate shell.html wording (Issue #89)` (Step 2 — 各 page を更にサブコミットに刻んでもよい) | shell.html 由来の wording test が一部 green に |
| Green-20 | `feat(dashboard): translate Overview renderer wording (Issue #89)` (Step 3) | KPI / sparkline / ranking 系の test が green に |
| Green-30 | `feat(dashboard): translate Patterns renderer wording (Issue #89)` (Step 4a) | Patterns 系 test が green に |
| Green-40 | `feat(dashboard): translate Quality renderer wording (Issue #89)` (Step 4b) | Quality 系 test が green に |
| Green-50 | `feat(dashboard): translate Surface renderer wording (Issue #89)` (Step 4c) | Surface 系 test が green に |
| Green-90 | `feat(dashboard): translate data tooltip wording (Issue #89)` (Step 5) | tooltip 系 test が green / wording test 全 green |
| Green-fixture | `chore(test): bump dashboard template sha256 (Issue #89)` (Step 6.1) | sha256 test も green / 全 test green |
| Green-followup | `test: follow up grep assertions (Issue #89)`（必要時のみ Step 6.2） | 既存 test が wording 変化に追従して green |

**重要**: 上記コミット境界は **bisect 可能性** を担保するため。途中の commit が一時的に test fail でも構わないが、各 commit は **single concern**（1 ファイルか 1 概念）に保つ。Red→Green を細かく刻むことで、後日「どの wording 変更で何が壊れたか」を bisect で特定できる。

**注**: Step 2 (shell.html) は §4 Step 2 で「1 セクション = 1 commit を目安に」と書いた通り、Overview / Patterns / Quality / Surface の 4 サブコミットに更に刻んでもよい (`feat(dashboard): translate Overview wording (Issue #89)` 等)。

---

## 6. Risks / Tradeoffs

| リスク | 内容 | 緩和策 |
|---|---|---|
| **data-\* key の取り違え** | tooltip / chip の表示文字列を変えた際、`data-mode` / `data-tip` などの key 文字列まで誤って書き換えると、JS の MODE_LABEL/TIP lookup が破綻する | §3 で「キー文字列を変えない」を invariant 化、§5 test 3 で grep 保証。MODE_TIP の `'mixed'` キーは既存バグの温存とし test で **明示的に存在を assert** |
| **dual ↔ mixed バグへの誤踏み込み** | 文言修正のついでに「直して良くなる」と誤判断して `MODE_TIP` の `'mixed'` を `'dual'` に変えてしまうと、本 issue のスコープを逸脱しレビューが滞る | plan §1 / §4 Step 5 / §5 test 3 の 3 箇所で「`'mixed'` キー維持」を pin。別 issue (§7) を切る |
| **MODE_TIP 表示文字列変更（iter3 P4）** | `MODE_TIP['mixed']` 値が `'🤝 Mixed'` → `'🤝 Dual'` に変わる。schema 経路で `mode='mixed'` が生成されている場合、tooltip 表示が「Mixed」から「Dual」に切り替わる UX 変化が生じる | **事前確認結果**: backend (`subagent_metrics.py` / `dashboard/server.py`) を grep した結果、`mode='mixed'` を生成する schema 経路は存在しない（事実上のデッドコード）。よって runtime UX への影響は無い見込み。ただし plan §5.2 test 3 の chip-tooltip parity assertion で `'🤝 Dual'` が両ブロックに揃っていることを契約化し、将来「`mode='mixed'` を生成する別 PR」が出ても UX 一貫性は保たれる |
| **aria-label の破損** | aria-label 文字列を機械的に書き換えると、エスケープが壊れて `aria-label="..."` が壊れる。screen reader 使えなくなる | shell.html / scripts の aria-label 組み立てを 1 件ずつ手動編集。`role="img"` を持つ要素は test で aria-label 存在のみ assert（中身は見ない） |
| **CSS class 名衝突** | `.empty` `.warn` `.bumped` 等 CSS class 名は触らない | §3 invariant、grep で確認 |
| **sha256 fixture の更新ミス** | `EXPECTED_TEMPLATE_SHA256` の更新を忘れると CI が真っ赤 | Step 6 で履歴コメント追記とセットで pin |
| **export_html の自動反映に欠陥** | `render_static_html()` が `</head>` 1 回 replace しているだけなので、文言修正で衝突する箇所はない（CSS / JS は body 内 `<script>` で inline 済） | 既存テスト `test_export_html.py` を回して回帰なしを確認 |
| **既存テストの grep assertion** | 過去 PR で `'top 10'` / `'sessions'` 等の文字列を assert しているテストがある場合 fail する | Step 1 直後に `pytest tests/ -k dashboard --collect-only` でテスト名一覧を確認、Step 6 で `pytest tests/` 全実行して網羅検出 |
| **reviewer ボットの誤検知** | claude[bot] / Codex が「英語が日本語より一般的では？」と false-positive を出す | PR 説明に §2 訳語表 + 出典 URL（Claude Code 公式日本語 docs）を貼り、issue オーナー方針と合致していることを明示 |
| **過剰意訳** | "skill description" を「スキルの説明文」など過度に長く訳すと UI 内で行折り返しが起きる | 訳語表で語数を厳格化（例: `description` は文中に出さず literal `description` を残すか「説明」と短くする）、PR review で目視確認 |
| **tooltip 表示崩れ** | tooltip の lbl テキストが日本語化されて幅が広がり、カラム配置が崩れる | CSS は touch しない方針なので、test で文字列は確認、PR で screenshot 添付（`mcp__chrome-devtools` などを利用、本 plan の自動化対象外） |
| **片仮名 / 漢字混在の煩雑さ** | 「サブエージェント」「コンテキスト圧縮」など長い片仮名語が並ぶ | §2.2 の表でケースバイケースに英語維持の判断（`Resume`, `LLM`, `Compact`）を明示、reviewer 議論を予期して plan に書き残す |

---

## 7. Out of Scope / Defer to Follow-up Issue

1. **Issue #94 (ダッシュボード `dual` ↔ `mixed` 表記不整合バグ修正)** — 起票済 https://github.com/tetran/claude-transcript-analyzer/issues/94 — `50_renderers_surface.js` の `MODE_LABEL` キー (`'dual'`) と `90_data_tooltip.js` の `MODE_TIP` キー (`'mixed'`) が不一致で、tooltip で `mode='dual'` のときラベルが引けず素の `'dual'` 文字列が表示される。data flow（schema → renderer → tooltip）を 1 つに揃える。本 plan の Step 5 で MODE_TIP の `'mixed'` キーを残すのは、本バグのスコープを別 issue (#94) に分離するため。
2. **`reports/summary.py` のターミナル出力日本語化（任意）** — CLI 出力は grep 利便性 / 既存スクリプト互換の観点で別判断。観点ごと issue 化するなら別 PR。
3. **CLI argparse の help 文言** — server.py / scripts/*.py の `argparse` help は開発者向け。日本語化するかは別議論。
4. **shell.html の h1 / nav 名 (Overview/Patterns/Quality/Surface) を完全日本語化するか** — 本 plan は固有名詞扱いで英語維持。日本語化したい場合は別 issue で UX 議論（タブナビが「概観 / 利用パターン / 品質 / 露出」だと見出しの長さが揃わず CSS にも影響）。
5. **CSS / SVG 内 fixed text の日本語化** — sparkline の `<text>peak N</text>` などは fixed-width font が無いと縦書きで崩れる懸念があり、本 plan では英語維持。後続 issue で検討余地。
6. **export_html の独立した「印刷向け」レイアウト** — 既存実装は dashboard テンプレートの inline で済むが、印刷 / PDF 用に文言を圧縮したい場合は別 issue。

---

## 8. Acceptance Criteria

- [ ] `python3 -m pytest tests/` が全 green。
- [ ] `tests/test_dashboard_wording.py` が新規追加され、forbidden（旧英語）と required（新日本語）の双方を assert している。
- [ ] `tests/test_dashboard_template_split.py` の `EXPECTED_TEMPLATE_SHA256` が更新され、履歴コメントに Issue #89 の 1 行が追加されている。
- [ ] §2.2 訳語表に従って shell.html / scripts/*.js の UI 文字列が修正されている。
- [ ] data-\* / class / id / page key / MODE_LABEL key / TREND_LABEL key / STATUS_LABEL key / data-tip kind 値 / `'dual'` / `'mixed'` / `'llm-only'` / `'user-only'` / `'accelerating'` 等の **invariant key 文字列は変更されていない**。
- [ ] `dual ↔ mixed` バグは温存されている（別 issue で扱うため）。
- [ ] reports/export_html.py に変更がない（自動反映を確認）。
- [ ] reports/summary.py に変更がない。
- [ ] dashboard/server.py に変更がない。
- [ ] CLAUDE.md ファイル末尾規約（空行 1 行）を順守。
- [ ] PR description に変更前 / 変更後の対照表 + Claude Code 公式日本語 docs 出典 URL が貼られている。

### 8.1 Manual Smoke Checklist（reviewer 用）

文字列 assertion でカバーできない視覚回帰を担保するためのチェック。dashboard を実機起動 (`/usage-dashboard`) し、以下を 1 度通す:

- [ ] Overview / Patterns / Quality / Surface の 4 ページを順に開き、各ページの hover tooltip を 1 つずつ発火（旧 `events` / `share` / `prompts` 等が日本語化されていることを目視）
- [ ] DOM Inspector で aria-label の中身を 5 箇所サンプリング（壊れたエスケープ・`undefined`・空文字なし）
- [ ] 期間トグル `7d` / `30d` / `90d` / `全期間` 切り替え後も、各 panel sub-badge の数値ラベルが日本語化された状態で再描画される
- [ ] 空 project の worst-session 行が **`（不明）`** （全角括弧）で表示される
- [ ] `python3 reports/export_html.py --output /tmp/test.html` を実行後、生成 HTML をブラウザで開いて wording 反映を確認（dashboard と同じテキストになっていること）
- [ ] 文字化け・raw `&amp;` 漏れ・割れたエスケープ・JS console エラーがゼロ
