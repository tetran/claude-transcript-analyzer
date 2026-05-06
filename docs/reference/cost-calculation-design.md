# コスト推定設計 — 外部リファレンス研究 + 本リポへの移植検討

このドキュメントは外部プロジェクト [`AgenticSec/ClaudeCodeUsageDashboard`](https://github.com/AgenticSec/ClaudeCodeUsageDashboard) のコスト推定設計を読み解き、本リポ（claude-transcript-analyzer）に取り込む際の判断材料を整理した **研究ノート** である。本リポにはまだコスト機能は無く、将来 issue 化するときの叩き台として残す。

仕様 (contract) ではないので `docs/spec/` には置かない。設計判断・gotcha・採用パターンの収集場所として `docs/reference/` を選んだ。

## 出典

- リポジトリ: <https://github.com/AgenticSec/ClaudeCodeUsageDashboard>
- 参照コミット: `c1326eb827fa`（main, 2026-04-17 時点）
- 主要ファイル: `dashboard/app/lib/cost.ts` / `dashboard/app/lib/db.server.ts` / `dashboard/migrations/0001_initial.sql` / `plugin/hooks/session-uploader.py`
- 調査日: 2026-04-30

---

## §1. 全体像

AgenticSec はコストを **DB に保存しない**。代わりに `(model, 4 種類の token カウント)` を raw 保存し、表示・集計のたびにアプリで純関数として計算する。

```
session-uploader.py (Stop hook)
  │  transcript から (model, input/output/cache_read/cache_creation tokens) を抽出
  ▼
POST /api/v1/usage/ingest → D1 (sessions テーブル: token カラムのみ、cost なし)
  ▼
表示時: db.server.ts が SQL で「モデル別」に集約 → app 側で calculateEstimatedCost() を適用 → reduce で合算
```

設計の中心は **「raw token 永続化 + 派生量はオンデマンド計算」** の正規化方針。価格改定があっても DB をいじらず `cost.ts` の price table を書き換えるだけで全期間が再計算される。

---

## §2. 価格テーブル: コード直書き / per-million-token

`dashboard/app/lib/cost.ts` 全 50 行のシンプルな構造（出典: 同ファイル）。

```ts
interface ModelPricing {
  input: number;
  output: number;
  cache_read: number;
  cache_creation: number;
}

const MODEL_PRICING: Record<string, ModelPricing> = {
  "claude-opus-4-6":   { input: 5, output: 25, cache_read: 0.5, cache_creation: 6.25 },
  "claude-sonnet-4-6": { input: 3, output: 15, cache_read: 0.3, cache_creation: 3.75 },
  "claude-haiku-4-5":  { input: 1, output: 5,  cache_read: 0.1, cache_creation: 1.25 },
};

const DEFAULT_PRICING = MODEL_PRICING["claude-sonnet-4-6"];
```

学べる点:

- **4 種類のトークンを個別レートで持つ** (`input` / `output` / `cache_read` / `cache_creation`)。Anthropic の公開価格表に従い、cache_read は input の 1/10、cache_creation は input の 1.25 倍として固定。
- **単位は USD per 1M tokens** に揃えると計算式が `(tokens / 1_000_000) * rate` の素直な形になり、メンタルモデルが単純化する。
- **未知モデルは Sonnet にフォールバック** し `null` を返さない。新モデルが Anthropic から出ても UI 側に if 文を増やさず、価格表更新だけで対応できる。中央値プロキシとしての安全側設計。

数値そのものは Anthropic の公式価格と一致するか別途検証が必要（このドキュメントの責務外）。本リポに移植する際は移植時点で公式価格表を確認すること。

---

## §3. 計算式: 純関数化

```ts
export function calculateEstimatedCost(
  model: string,
  inputTokens: number,
  outputTokens: number,
  cacheReadTokens: number,
  cacheCreationTokens: number
): number {
  const p = getPricing(model);
  const cost =
    (inputTokens / 1_000_000) * p.input +
    (outputTokens / 1_000_000) * p.output +
    (cacheReadTokens / 1_000_000) * p.cache_read +
    (cacheCreationTokens / 1_000_000) * p.cache_creation;
  return Math.round(cost * 10000) / 10000; // 4 桁丸め (¢ 以下 4 桁)
}
```

- 引数 5 個、戻り値 1 個、副作用ゼロの純関数。テストしやすい。
- 4 桁丸め (USD 0.0001 = 1/100 セント) で表示時の切り捨て誤差を最小化。集計後の合算では十分な精度。

---

## §4. DB にコストを保存しない（戦略的判断）

`dashboard/migrations/0001_initial.sql` の `sessions` テーブル抜粋:

```sql
CREATE TABLE sessions (
  ...
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  cache_creation_tokens INTEGER NOT NULL DEFAULT 0
  -- estimated_cost カラムは存在しない
);
```

**意図的に cost カラムを持たない** という判断のメリット:

1. **価格改定への耐性**: Anthropic が値下げ・値上げしても backfill 不要。`cost.ts` の数値を書き換えるだけで過去データの表示も自動更新。
2. **正規化**: token + model から導出可能なので保存しない。冗長データを排除。
3. **テスト容易性**: 「同じ token 入力なら同じ cost」を property test で検証できる。

トレードオフ:

- 「過去レポートは時点固定であってほしい」という監査ログ用途には不向き。価格表書き換えで遡って数字が変わるため、参考値としてのみ使う割り切り。
- 監査要件があるならコスト snapshot を別テーブルに採取するハイブリッド設計が必要。本リポ採用時は「参考値」と明記する。

---

## §5. モデル別集約 → アプリで合計（非自明な集計設計）

`dashboard/app/lib/db.server.ts:236-263` の KPI 集計が肝。よくある「`SUM(tokens) × rate`」では **モデル混在で間違える** という罠を回避している。

ダメな設計:

```sql
-- opus と haiku の token を一緒くたに sum してしまう
SELECT SUM(input_tokens) FROM sessions;
```

→ どのレートを掛けるか決まらない。

採用されている設計:

```sql
SELECT model,
       SUM(input_tokens) as input_tokens,
       SUM(output_tokens) as output_tokens,
       SUM(cache_read_tokens) as cache_read_tokens,
       SUM(cache_creation_tokens) as cache_creation_tokens
FROM sessions
GROUP BY model;
```

その後、アプリ層で:

```ts
const totalEstimatedCost = kpiCostResult.results.reduce(
  (sum, r) => sum + calculateEstimatedCost(
    r.model, r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_creation_tokens
  ),
  0
);
```

**`GROUP BY model` で分割集計 → アプリ側で price 適用 → reduce で合算**。このパターンが KPI / ユーザーランキング / 日次トレンド / recent sessions の **全集計クエリで一貫**して使われている。

本リポに移植するときも、Python 側で `defaultdict[str, TokenAccumulator]` 等で model 別に集計してから cost 関数を適用する形にすれば同じ規律を保てる。

---

## §6. Token 収集と dedup（session-uploader.py 側）

`plugin/hooks/session-uploader.py:parse_transcript()` のトークン集計部:

```python
# message id で dedup（last wins）— assistant の重複 record による二重計上を防ぐ
messages_by_id = {}
for rec in assistant_recs:
    msg = rec.get("message", {})
    msg_id = msg.get("id", id(rec))
    messages_by_id[msg_id] = msg

input_tokens = sum(
    m.get("usage", {}).get("input_tokens", 0)
    for m in messages_by_id.values()
)
# output / cache_read_input_tokens / cache_creation_input_tokens も同様
```

transcript の `usage` field キー名と DB カラムの対応:

| transcript の usage キー | DB カラム |
|---|---|
| `input_tokens` | `input_tokens` |
| `output_tokens` | `output_tokens` |
| `cache_read_input_tokens` | `cache_read_tokens` |
| `cache_creation_input_tokens` | `cache_creation_tokens` |

cache 系は transcript 側で `_input_tokens` サフィックスが付くが、DB では `_tokens` に統一されている点に注意。

本リポは hook 経由で event 単位に独立して書き込むので二重観測は出にくいが、transcript を後追い rescan する経路（`scripts/rescan_transcripts.py`）では同じ message id ベース dedup を適用するべき。

---

## §7. Model 同定: 最頻モデル採用

```python
model_counter = Counter(
    msg.get("model", "unknown") for msg in messages_by_id.values()
)
model = model_counter.most_common(1)[0][0] if model_counter else "unknown"
```

session 内で `/model` で切り替えた場合 **過半数モデルしか記録されない** 割り切り。session 単位 1 model 1 cost で運用している。

これは **AgenticSec 設計の弱点**。Claude Code は `/model` 切替が頻繁なので、session 単位の最頻モデル採用だと中盤の haiku 切替による節約効果が見えなくなる。本リポでは差別化候補として扱う（§9）。

---

## §8. 本リポへの移植検討

| 取り入れる項目 | 難易度 | コメント |
|---|---|---|
| **コストカラム持たない・導出計算** | 低 | 既存 `usage.jsonl` も token は raw 保存済みで相性が良い |
| **4 トークン × per-1M レート table** | 低 | Python dict で同じ構造。`reports/cost.py` 新設が綺麗 |
| **モデル別集約 → 合計** | 中 | `subagent_metrics.py` 隣に `cost_metrics.py` 新設。集約 dict を model 別に持ち、reduce 適用 |
| **不明モデルは Sonnet fallback** | 低 | 新モデル登場時に画面が壊れない安全策 |
| **dedup by message id** | 既に近い | hook 経由なら不要だが rescan 経路では同規律で適用 |
| **mid-session model switch を捨てる** | 不採用案 | `/model` 切替の節約可視化を諦める設計なので、本リポでは別案を採用すべき（§9） |

---

## §9. 差別化候補: per-message cost 集計

AgenticSec が session 単位で `(最頻 model, sum tokens)` を保持する一方、本リポは **assistant message ごとに `(model, tokens)` のペアで集計** すれば `/model haiku` 切替の節約効果も可視化できる。

実装スケッチ:

```python
# rescan / hook いずれの経路でも、message 単位で
event = {
    "event_type": "assistant_usage",
    "session_id": ...,
    "timestamp": msg["timestamp"],
    "model": msg["model"],
    "input_tokens": usage["input_tokens"],
    "output_tokens": usage["output_tokens"],
    "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
    "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
    "message_id": msg["id"],  # dedup key
}
```

集計時は event を model 別に GROUP（dict accumulator）し、cost 関数を適用後に session / day / user 軸で再集計。session 単位の summary が必要なら view 側でロールアップする。

これにより:

- `/model` 切替の節約効果が tracable
- session が長期化しても model 切替を気にせず正確
- 既存 `usage.jsonl` の append-only 規律と整合

実装する際は新 event_type の schema を `docs/spec/usage-jsonl-events.md` に追記し、本ドキュメントは reference として残す。

---

## §10. 採用版 (Issue #99 / v0.8.0): per-subagent transcript 統合 + service_tier

§9 の per-message 集計案を Issue #99 で採用する。AgenticSec 設計から離脱した本リポ独自の差別化点:

### 採否サマリ

| 採否 | 設計 | 備考 |
|---|---|---|
| ✅ 採用 | §9 per-message 集計 | `assistant_usage` event を message 単位で記録、cost も per-message rate 適用 → reduce 合算。`/model` 切替の節約効果が見える |
| ❌ 不採用 | §7 最頻モデル採用 | session 内 model 切替を可視化できない弱点を回避するため不採用 (= AgenticSec の弱点) |
| ✅ 採用 | §2 4 種 token × per-1M rate | `cost_metrics.MODEL_PRICING` に dict で持つ (Python 実装) |
| ✅ 採用 | §4 cost を保存しない | event log には raw token のみ。価格改定で過去値も動く参考値仕様 |
| ✅ 採用 | §5 model 別集約 → reduce 合算 | per-message で rate 適用後 sum、混在 sum の罠なし |
| ✅ 採用 | §2 不明 model Sonnet fallback | 中央値プロキシとしての安全側設計 (新 model 登場で UI が壊れない) |

### 収集経路の二系統化

メイン session transcript に加えて、**per-subagent transcript** (`~/.claude/projects/<encoded-cwd>/<session>/subagents/agent-<agent_id>.jsonl`) も収集対象とする。Issue #93 調査中の偶発的発見:

- 各 assistant 行に `message.usage` (4 種 token + `service_tier` + `inference_geo`) が完全に揃って記録されている
- 実測サンプル (n=10 invocation, 直近 type 入り subagent_stop): 全件で `message.usage` 取得成功 (n=278 行)、`service_tier` 取得成功 10/10

これにより subagent invocation 単位の cost 帰属が可能 (= 将来の drill-down で「どの subagent で幾らかかったか」を出せる土台)。

### 対象絞り込み (Issue #93 連結)

per-subagent transcript の収集は **Issue #93 で確定した `subagent_type == ""` filter rule 適用後の type 入り invocation のみ**。空 type の orphan invocation は除外。

### service_tier / inference_geo の集計

`assistant_usage` event に passthrough して、Sessions ページで **service_tier_breakdown** (priority / standard 比率) を表示。priority tier 利用率の可視化が新たに可能になる。`inference_geo` は任意 field として記録のみ (集計表示は将来 issue)。

### dedup の二重観測対応

`(session_id, message_id)` first-wins dedup は、main + subagent 経路で同 message_id を二重観測した場合にも 1 件に集約する idempotent 保証として機能する (§6 の規律をそのまま延長)。

### 価格表 pin の出典

- **pin 出典**: `https://platform.claude.com/docs/en/about-claude/pricing`
  (`https://www.anthropic.com/pricing` から redirect、API pricing は docs サイト
  側に集約されている)
- **取得日時**: 2026-05-06
- **pin 対象 model**: Opus 4 / 4.1 / 4.5 / 4.6 / 4.7、Sonnet 3.7 / 4 / 4.5 / 4.6、
  Haiku 3 / 3.5 / 4.5。CLAUDE.md "Technical identifiers" ルール準拠で `cost_metrics.py`
  module docstring に table 全体を verbatim 転記
- **5-minute cache write のみ採用**: Anthropic は 5m / 1h で cache write 単価が異なる
  (5m: 1.25x base、1h: 2x base)。transcript の `cache_creation_input_tokens` には
  TTL の区別が無い (= 観測不能) ため default の 5m を採用。1h 利用が一般化したら
  schema 拡張で別 field 化する将来 issue
- **`inference_geo` の 1.1x multiplier 未適用**: data-residency 機能 (US-only routing)
  使用時 +10% だが、global routing が default のため大半は影響なし。`assistant_usage.inference_geo`
  には raw 値が記録される (集計表示は将来 issue)

### 用語整理

- **token**: 実測値 (transcript の `message.usage` から直接抽出)
- **cost**: 実測 token × 価格表掛け算による **参考値** (価格改定で過去値も動く)
- 「推計」表現は混乱を招くため、UI / docstring では「実測 token + 価格表」と説明する

### 関連 issue

- Issue #99: infra (`assistant_usage` event + `cost_metrics` + main / per-subagent transcript 収集 + `/api/data` 拡張)
- Issue #103: Sessions ページ UI (cost 列 + service_tier 表示)
- Issue #104: rescan + reports 拡張
- Issue #93: 前提 (`subagent_type == ""` filter rule、agent_id dedup)

---

## 関連 spec / reference

- 移植実装時に追記する spec: `docs/spec/usage-jsonl-events.md`（新 `assistant_usage` event の schema、`service_tier` / `inference_geo` / `source` 含む）
- 関連する既存 reference: `docs/reference/storage.md`（JSONL primary 規律と整合させる）
- 集計ロジックの宿先: `subagent_metrics.py` と並ぶ位置に `cost_metrics.py` を置く想定
- 全体 plan: `docs/plans/session-page-cost-estimation.md`

