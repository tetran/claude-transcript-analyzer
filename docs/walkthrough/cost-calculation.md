# claude-transcript-analyzer コスト計算 walkthrough

*2026-05-06T17:46:29+0900 by Showboat 0.6.1*
<!-- showboat-id: 803ae27b-cbd6-4c3e-bc40-bc07227d00b1 -->

このドキュメントは [showboat](https://github.com/simonw/showboat) で書かれた **動く walkthrough** です💸  各 bash ブロックは実際に実行されており、output ブロックはその場で取れた出力をそのまま貼り付けています。

リポジトリのルートで次のコマンドを叩くと、全ブロックが再実行され、出力が一致するか検証されます👇

    showboat verify docs/walkthrough/cost-calculation.md

サンプルとして使うイベントは `docs/walkthrough/fixtures/cost-sample.jsonl` に置いてあります。これは作者の `~/.claude/transcript-analyzer/usage.jsonl` から `assistant_usage` event を抜き出して 6 件に絞り、`/model` 切替の節約効果を見せるための合成 session を 1 つだけ追加したものです🐶

データフロー全体を先に見たい場合は同じディレクトリの `data-flow.md` を読んでから戻ってきてください🌱

---

## このドキュメントを 1 行で

> Stop hook で transcript から拾った `(model, 4 種類の token カウント)` が、価格表との掛け算と reduce 合算を経て、ダッシュボードの `estimated_cost_usd` 列になるまで。

コストの旅は 3 つの層で進みます。

    [Claude Code transcript]                     ← 観測源 (message.usage)
         │
         │  Stop hook → hooks/record_assistant_usage.py
         ▼
    [usage.jsonl: assistant_usage event]         ← (1) 収集 / per-message 永続化
         │
         │  読込 → cost_metrics.py
         ▼
    [calculate_message_cost / _get_pricing]      ← (2) 1 件ごと rate 適用
         │
         │  reduce 合算
         ▼
    [aggregate_session_breakdown]                ← (3) session 単位 row dict
         │
         ▼
    /api/data session_breakdown[].estimated_cost_usd

設計の中心は **「価格は DB に保存しない / 表示時にオンデマンド計算する」** です。価格改定があっても `cost_metrics.MODEL_PRICING` の数値を書き換えるだけで、過去の `assistant_usage` event も含めて全期間が再計算されます。設計判断の出典 (外部リファレンス調査と採用判断) は `docs/reference/cost-calculation-design.md` § 9-§ 10 にまとまっています。

---

## 第 0 章: 主要ファイル at a glance 🗺️

コスト計算に絡む登場人物は 3 つだけです。

```bash
ls -1 hooks/record_assistant_usage.py cost_metrics.py dashboard/server.py
```

```output
cost_metrics.py
dashboard/server.py
hooks/record_assistant_usage.py
```

| ファイル | 役割 |
|---|---|
| `hooks/record_assistant_usage.py` | Stop hook で main / per-subagent transcript から `(model, tokens, message_id)` を抽出して `assistant_usage` event として append。`(session_id, message_id)` で first-wins dedup |
| `cost_metrics.py` | 価格表 (`MODEL_PRICING`) + 1 件 cost (`calculate_message_cost`) + session cost (`calculate_session_cost`) + session row 組み立て (`aggregate_session_breakdown`)。**全部純関数** |
| `dashboard/server.py` | `build_dashboard_data` から `aggregate_session_breakdown` を呼んで `/api/data` の `session_breakdown` 配列を返す |

価格表は **ソースコード直書き / per-1M-token USD** で、`cost_metrics.py` の docstring に出典 URL と取得日時が pin してあります。改定時はそこを書き換えるだけで OK です。

---

## 第 1 章: `assistant_usage` event の中身 🔬

サンプル fixture の event_type 内訳:

```bash
wc -l docs/walkthrough/fixtures/cost-sample.jsonl && du -h docs/walkthrough/fixtures/cost-sample.jsonl
```

```output
      12 docs/walkthrough/fixtures/cost-sample.jsonl
4.0K	docs/walkthrough/fixtures/cost-sample.jsonl
```

```bash
python3 -c "
import json, collections
c = collections.Counter()
for line in open('docs/walkthrough/fixtures/cost-sample.jsonl'):
    c[json.loads(line)['event_type']] += 1
for k, v in c.most_common():
    print(f'{v:5d}  {k}')"
```

```output
    8  assistant_usage
    2  session_start
    2  session_end
```

`assistant_usage` event 1 件を覗いてみます。

```bash
python3 -c "
import json
for line in open('docs/walkthrough/fixtures/cost-sample.jsonl'):
    e = json.loads(line)
    if e['event_type'] == 'assistant_usage':
        print(json.dumps(e, ensure_ascii=False, indent=2))
        break"
```

```output
{
  "event_type": "assistant_usage",
  "project": "gh-extensions",
  "session_id": "865b6309-a5c4-455e-8147-cc701870f2df",
  "timestamp": "2026-05-06T08:25:19.515000+00:00",
  "model": "claude-opus-4-7",
  "input_tokens": 6,
  "output_tokens": 325,
  "cache_read_tokens": 0,
  "cache_creation_tokens": 34998,
  "message_id": "msg_01KL7heBwyjwkAdhSZGitgSa",
  "service_tier": "standard",
  "inference_geo": "",
  "source": "main"
}
```

各フィールドの意味:

- `model` — assistant message を生成したモデル ID。**`/model` 切替があれば session 内でも値が変わる**ので per-message 単位で記録する必要がある
- `input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_creation_tokens` — Anthropic の課金 4 軸。transcript 上は `cache_read_input_tokens` / `cache_creation_input_tokens` だが、event field では `_input_tokens` サフィックスを `_tokens` に統一している (`docs/spec/usage-jsonl-events.md` § assistant_usage の対応表)
- `message_id` — `(session_id, message_id)` の pair で **first-wins dedup**。rescan 二重実行 / hook 再発火 / main + subagent 二重観測でも 1 件に集約する idempotent 保証 key
- `service_tier` — `priority` / `standard` 等の passthrough。欠損時は `null`。Sessions ページの `service_tier_breakdown` の入力
- `source` — `"main"` (メイン session transcript) / `"subagent"` (`<session>/subagents/agent-*.jsonl`) の 2 値。Issue #93 の `subagent_type == ""` filter rule を通った type 入り invocation のみ subagent 経路に乗る

スキーマの正本は `docs/spec/usage-jsonl-events.md` の `assistant_usage` 節にあります。

> 💡 transcript 1 行 = `assistant_usage` 1 件 ではありません。`message.role == "assistant"` かつ `message.usage` が dict、かつ `message.id` と tz-aware `timestamp` が揃っている行だけが残ります。残せないものは silent skip (Stop hook をブロックしない規律 / `record_assistant_usage.py:_extract_assistant_usage`)。

---

## 第 2 章: 1 message のコスト計算 💴

`cost_metrics.calculate_message_cost(model, in, out, cr, cc)` は副作用なしの純関数です。第 1 章で見た 1 件目 (opus-4-7, in=6 / out=325 / cr=0 / cc=34998) を流し込んでみます。

```bash
python3 -c "
from cost_metrics import calculate_message_cost
# 第 1 章で見た 1 件目: opus-4-7, in=6 out=325 cr=0 cc=34998
cost = calculate_message_cost('claude-opus-4-7', 6, 325, 0, 34998)
print(f'cost (USD): {cost}')
# 内訳: opus-4-7 rate は input=\$5 / output=\$25 / cache_read=\$0.5 / cache_creation=\$6.25 (per 1M)
print('breakdown:')
print(f'  input        6 * \$5    /1M = {6/1_000_000*5:.6f}')
print(f'  output     325 * \$25   /1M = {325/1_000_000*25:.6f}')
print(f'  cache_read   0 * \$0.5  /1M = {0/1_000_000*0.5:.6f}')
print(f'  cache_create 34998 * \$6.25 /1M = {34998/1_000_000*6.25:.6f}')"
```

```output
cost (USD): 0.2269
breakdown:
  input        6 * $5    /1M = 0.000030
  output     325 * $25   /1M = 0.008125
  cache_read   0 * $0.5  /1M = 0.000000
  cache_create 34998 * $6.25 /1M = 0.218738
```

数式の本体はこの 1 行に集約されます (`cost_metrics.calculate_message_cost`):

```python
cost = (
    (input_tokens         / 1_000_000) * p.input
  + (output_tokens        / 1_000_000) * p.output
  + (cache_read_tokens    / 1_000_000) * p.cache_read
  + (cache_creation_tokens / 1_000_000) * p.cache_creation
)
return round(cost, 4)
```

ポイント:

- 単位は **USD per 1M tokens**。`(tokens / 1_000_000) * rate` の素直な式に揃えて、メンタルモデルを単純化している (外部リファレンス `AgenticSec/ClaudeCodeUsageDashboard` から拝借した規律)
- 4 桁丸め (USD 0.0001 = 1/100 セント) で表示誤差を最小化。集計後の合算では十分な精度
- 上の例では cost の **96% が `cache_creation` 成分** (0.219 / 0.227)。Claude Code の典型的な workload では cache 系が cost を支配しがちなので、cache の 4 軸を保存していないと再現できない

---

## 第 3 章: 価格表マッチングと未知 model fallback 🎯

価格表は `cost_metrics.MODEL_PRICING` に Python dict で持っていて、key は **公式 model ID prefix** です。`_get_pricing(model)` は次の 3 段階で rate を返します:

1. **完全一致**: `MODEL_PRICING[model]` がそのまま hit
2. **token-boundary prefix match (longest wins)**: `claude-haiku-4-5-20251001` のような date-suffix 付き ID は `claude-haiku-4-5-` prefix で hit。`claude-opus-4` と `claude-opus-4-5` の両方が startswith で当たるケースは **長い方** ($5) が勝つ
3. **Sonnet fallback**: どれにも当たらなければ `claude-sonnet-4-6` rate (中央値プロキシ)

実際に呼んでみます。

```bash
python3 -c "
from cost_metrics import _get_pricing
for m in [
    'claude-opus-4-7',
    'claude-haiku-4-5-20251001',
    'claude-3-5-haiku-20241022',
    'claude-opus-4',
    'claude-opus-4-5',
    'claude-future-model-99',
]:
    p = _get_pricing(m)
    print(f'{m:32s}  input=\${p.input:5.2f}  output=\${p.output:6.2f}  cache_read=\${p.cache_read:.2f}  cache_create=\${p.cache_creation:.2f}')"
```

```output
claude-opus-4-7                   input=$ 5.00  output=$ 25.00  cache_read=$0.50  cache_create=$6.25
claude-haiku-4-5-20251001         input=$ 1.00  output=$  5.00  cache_read=$0.10  cache_create=$1.25
claude-3-5-haiku-20241022         input=$ 0.80  output=$  4.00  cache_read=$0.08  cache_create=$1.00
claude-opus-4                     input=$15.00  output=$ 75.00  cache_read=$1.50  cache_create=$18.75
claude-opus-4-5                   input=$ 5.00  output=$ 25.00  cache_read=$0.50  cache_create=$6.25
claude-future-model-99            input=$ 3.00  output=$ 15.00  cache_read=$0.30  cache_create=$3.75
```

読み方:

- `claude-haiku-4-5-20251001` (Claude 4.x naming = `claude-{model}-{version}-{date}`) と `claude-3-5-haiku-20241022` (Claude 3.x naming = `claude-{version}-{model}-{date}`) は **順序が逆** ですが、prefix を `MODEL_PRICING` に両形式で pin してあるのでどちらも正しく hit します (codex review Round 2 / P2 で固めたケース)
- `claude-opus-4` ($15) と `claude-opus-4-5` ($5) は **3 倍違う**。longest-match を取り違えると致命的なので、`_get_pricing` は `model.startswith(p + "-")` (= 末尾 `-` 付き) で token-boundary 比較してから `max(matches, key=len)` で勝者を選ぶ
- `claude-future-model-99` のような未知 model は Sonnet 4.6 の rate にフォールバックします。新 model 登場で UI が空になったり例外で落ちたりしないための **silent な安全側設計**

> ⚠️ 価格は 2026-05-06 に Anthropic 公式 docs (`https://platform.claude.com/docs/en/about-claude/pricing`) から目視で pin した値です。改定時の更新は `cost_metrics.py` の `MODEL_PRICING` dict と同 module docstring の対応表 1 箇所だけで完結します。

---

## 第 4 章: session 単位の合算と `/model` 切替の節約効果 📐

`cost_metrics.calculate_session_cost(events)` は events list を `assistant_usage` だけに絞り込み、`calculate_message_cost` を per-event で適用して reduce 合算します。

```python
def calculate_session_cost(events_for_session):
    total = 0.0
    for ev in events_for_session:
        if ev.get("event_type") != "assistant_usage":
            continue
        total += calculate_message_cost(
            ev.get("model", ""),
            int(ev.get("input_tokens") or 0),
            int(ev.get("output_tokens") or 0),
            int(ev.get("cache_read_tokens") or 0),
            int(ev.get("cache_creation_tokens") or 0),
        )
    return round(total, 4)
```

ここで重要なのは **「per-event で rate を当ててから sum する」** こと。`SUM(input_tokens)` を取ってから 1 つの rate を掛ける素朴な設計だと、session 内で `/model haiku` 切替された場合に opus と haiku の token を一緒くたにしてしまい数字が壊れます (`docs/reference/cost-calculation-design.md` §5)。

fixture の 2 session でやってみます。

```bash
python3 -c "
import json
from cost_metrics import calculate_session_cost, calculate_message_cost

events = [json.loads(l) for l in open('docs/walkthrough/fixtures/cost-sample.jsonl')]
real_evs = [e for e in events if e.get('session_id') == '865b6309-a5c4-455e-8147-cc701870f2df']
demo_evs = [e for e in events if e.get('session_id') == 'demo-aaaa-bbbb-cccc-dddd0001']

print(f'real session  cost: \${calculate_session_cost(real_evs):.4f}  ({sum(1 for e in real_evs if e[\"event_type\"]==\"assistant_usage\")} assistant messages)')
print(f'demo session  cost: \${calculate_session_cost(demo_evs):.4f}  ({sum(1 for e in demo_evs if e[\"event_type\"]==\"assistant_usage\")} assistant messages)')

# /model 切替の節約デモ: demo-app は opus → haiku で 1 message ごとの cost がどう違うか
for ev in demo_evs:
    if ev['event_type']!='assistant_usage': continue
    c = calculate_message_cost(ev['model'], ev['input_tokens'], ev['output_tokens'], ev['cache_read_tokens'], ev['cache_creation_tokens'])
    print(f\"  {ev['timestamp'][11:19]}  model={ev['model']:32s}  msg cost=\${c:.6f}\")"
```

```output
real session  cost: $0.6653  (6 assistant messages)
demo session  cost: $0.1721  (2 assistant messages)
  10:00:05  model=claude-opus-4-7                   msg cost=$0.162600
  10:01:30  model=claude-haiku-4-5-20251001         msg cost=$0.009500
```

`demo-app` session は同じ token 構成 (in=10 / out=1500、cache の方向が opus 側は creation 中心、haiku 側は read 中心) で `/model` だけ切り替わっています。1 件目 (opus) が $0.1626、2 件目 (haiku) が $0.0095 で **約 17 倍** の差。これが per-message 集計を採用したことで初めて見える節約効果です (= 外部リファレンス AgenticSec の弱点 §7 を回避)。

---

## 第 5 章: `aggregate_session_breakdown` の row dict 🧮

ダッシュボードの Sessions ページが消費するのは session 単位の row 配列です。`cost_metrics.aggregate_session_breakdown(events)` は次のステップで row を組み立てます (`_build_session_row`):

1. session ごとに events を group
2. `session_start` 不在の orphan session は drop
3. `assistant_usage` を全件走査して `models` (model 別 message 数) / 4 軸 token 累計 / `service_tier_breakdown` を作る
4. 同じループで per-message cost を `calculate_message_cost` で出して `cost_total` に積む
5. `skill_count` (`skill_tool` + `user_slash_command`) と `subagent_count` (`session_subagent_counts`) を足して row に
6. `started_at` 降順で sort、先頭 `TOP_N_SESSIONS = 20` を返す

fixture 全体を流し込んで row dict を見てみます。

```bash
python3 -c "
import json
from cost_metrics import aggregate_session_breakdown

events = [json.loads(l) for l in open('docs/walkthrough/fixtures/cost-sample.jsonl')]
rows = aggregate_session_breakdown(events)
print(json.dumps(rows, ensure_ascii=False, indent=2))"
```

```output
[
  {
    "session_id": "demo-aaaa-bbbb-cccc-dddd0001",
    "project": "demo-app",
    "started_at": "2026-05-06T10:00:00+00:00",
    "ended_at": "2026-05-06T10:05:00+00:00",
    "duration_seconds": 300.0,
    "models": {
      "claude-opus-4-7": 1,
      "claude-haiku-4-5-20251001": 1
    },
    "tokens": {
      "input": 20,
      "output": 3000,
      "cache_read": 20000,
      "cache_creation": 20000
    },
    "estimated_cost_usd": 0.1721,
    "service_tier_breakdown": {
      "priority": 1,
      "standard": 1
    },
    "skill_count": 0,
    "subagent_count": 0
  },
  {
    "session_id": "865b6309-a5c4-455e-8147-cc701870f2df",
    "project": "gh-extensions",
    "started_at": "2026-05-06T08:25:00+00:00",
    "ended_at": "2026-05-06T08:42:00+00:00",
    "duration_seconds": 1020.0,
    "models": {
      "claude-opus-4-7": 6
    },
    "tokens": {
      "input": 16,
      "output": 3036,
      "cache_read": 130708,
      "cache_creation": 83831
    },
    "estimated_cost_usd": 0.6653,
    "service_tier_breakdown": {
      "standard": 6
    },
    "skill_count": 0,
    "subagent_count": 0
  }
]
```

読み方:

- `models` は session 内 `/model` 切替も per-message 単位で count される (demo-app session が opus + haiku の両 key を持つ)
- `service_tier_breakdown` は **欠損 / null を除外**し、real value のみ集計 (real-world data quirks をそのまま見せる方針)
- `ended_at` / `duration_seconds` は `session_end` 不在で両方とも `null` (= UI 側「進行中」pill の trigger)
- 並び順は `started_at` 降順 (最新 session が先頭)。10:00 開始の demo session が、08:25 開始の real session より上に来ている

`aggregate_session_breakdown(events, period_events=...)` の 2 引数渡しは period 跨ぎ session の表示用 trick です: boundary (= `session_start` / `session_end`) は全期間 events から、content (= cost / token / models / service_tier / skill_count) は period-filtered subset から、別々に取ります。これで `session_start` が period cutoff より前にあっても、in-period に `assistant_usage` がある session は cost / token を in-period 限定で合算した状態で render されます (codex review Round 1 / cross-cutoff regression 対策)。詳しい契約は `docs/spec/dashboard-api.md` §session_breakdown へ。

---

## 第 6 章: 既知の制限 ⚠️

cost は **実測 token × 価格表掛け算による参考値** で、3 つの既知の under/over-estimate ポイントがあります (`cost_metrics.py` module docstring に列挙)。

- **5-minute cache write のみ採用**: Anthropic は 5m / 1h で cache write 単価が違う (5m: 1.25x base、1h: 2x base)。transcript の `cache_creation_input_tokens` には TTL の区別が無いので default の 5m を採用。1h cache を多用するワークロードでは under-estimate になる
- **`inference_geo` の 1.1x multiplier 未適用**: data-residency 機能 (US-only routing) 使用時は +10% 課金されるが、global routing が default なので大半は影響なし。`assistant_usage.inference_geo` には raw 値が記録されているので、将来 issue で乗算するときの土台はある
- **価格改定で過去値も動く**: 価格表は `cost_metrics.MODEL_PRICING` でコード直書き。**DB / event log に snapshot しない方針** なので、Anthropic が値下げ・値上げすると過去レポートの数字も自動で動く (= 監査用途は scope 外、参考値仕様)

「過去レポートは時点固定であってほしい」という監査ログ用途には別設計 (cost snapshot 別テーブル等) が必要です。詳しいトレードオフは `docs/reference/cost-calculation-design.md` §4 を参照。

---

## 続きの読み物 📚

- 💡 **採用判断の研究ノート (なぜ DB に cost を保存しないか / なぜ per-message 集計か)** → `docs/reference/cost-calculation-design.md`
- 📋 **`assistant_usage` event の正式スキーマ** → `docs/spec/usage-jsonl-events.md`
- 🌐 **`/api/data` の `session_breakdown` 契約** → `docs/spec/dashboard-api.md` §session_breakdown
- 🔬 **データフロー全体像 (Skills / Subagents 含む)** → `docs/walkthrough/data-flow.md`
- 🪝 **生 transcript の中身 (`message.usage` の出どころ)** → `docs/transcript-format.md`

---

## おまけ: この walkthrough 自体を再生する 🔁

このドキュメントは showboat 0.6.1 で作られています。書き直す手順だけを取り出すには `showboat extract docs/walkthrough/cost-calculation.md` を実行すると、`init` / `note` / `exec` の系列が出力されます。出力ブロックは含まれない (verify が再生成する) ので、価格改定後に数字を更新したいときは extract → MODEL_PRICING を更新 → verify で再生成、という順でやれば OK です🛠️
