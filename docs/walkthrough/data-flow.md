# claude-transcript-analyzer データフロー入門

*2026-05-01T13:43:44Z by Showboat 0.6.1*
<!-- showboat-id: cff983cd-5245-4778-842f-360292a440b3 -->

このドキュメントは [showboat](https://github.com/simonw/showboat) で書かれた **動く walkthrough** です🚀  各 bash ブロックは実際に実行されており、output ブロックはその場で取れた出力をそのまま貼り付けています。

リポジトリのルートで次のコマンドを叩くと、全ブロックが再実行され、出力が一致するか検証されます👇

    showboat verify docs/walkthrough/data-flow.md

サンプルとして使うイベントは `docs/walkthrough/fixtures/usage-sample.jsonl` に置いてあります。これは実運用 (作者の `~/.claude/transcript-analyzer/usage.jsonl`) からこのリポジトリ自身の作業ぶんだけを抜き出してサニタイズした、**本物のドッグフードデータ** です🐶

---

## このプロジェクトを 1 行で

> Claude Code が動くたびに発火する **Hook** をフックして、Skills と Subagents の使用状況を `.jsonl` に貯め込み、ブラウザで見られるようにするツール。

データの旅は 3 つの層で進みます。

    [Claude Code]                          ← イベント発生源
         │
         │  Hook (PostToolUse / SessionStart / …)
         ▼
    [hooks/record_*.py]                    ← (1) 収集 (Collect)
         │
         │  append-only with file lock
         ▼
    [usage.jsonl] / [archive/YYYY-MM.gz]   ← (2) 保管 (Store)
         │
         ├── reports/summary.py            ← (3a) ターミナル集計
         ├── reports/export_html.py        ← (3b) 静的 HTML
         └── dashboard/server.py           ← (3c) ブラウザダッシュボード

以下、それぞれの層を実機で確認していきます。

---

## 第 0 章: ディレクトリ at a glance 🗺️

まずは登場人物を確認します。

```bash
ls -1 hooks reports dashboard | grep -v "__pycache__"
```

```output
dashboard:
server.py
template

hooks:
_append.py
_launcher_common.py
_lock.py
hooks.json
launch_archive.py
launch_dashboard.py
record_assistant_usage.py
record_session.py
record_skill.py
record_subagent.py
verify_session.py

reports:
_archive_loader.py
export_html.py
summary.py
```

それぞれのファイルの役割を一言で:

| ファイル | 役割 |
|---|---|
| `hooks/record_skill.py` | Skill ツール実行 / スラッシュコマンド入力を `usage.jsonl` に記録 |
| `hooks/record_subagent.py` | Task / Agent ツール実行と SubagentStart/Stop を記録 |
| `hooks/record_session.py` | セッション開始/終了, PreCompact, 通知などを記録 |
| `hooks/record_assistant_usage.py` | Stop hook で transcript から `(model, 4 種 token, message_id)` を抽出して `assistant_usage` event を記録 (詳しい流れは `cost-calculation.md`) |
| `hooks/_append.py` | ファイルロック付きで append-only 書き込み |
| `hooks/verify_session.py` | Stop hook で transcript と usage.jsonl を突き合わせて整合性チェック |
| `hooks/launch_dashboard.py` | ダッシュボードを fork-and-detach でべき等に起動 |
| `reports/summary.py` | ターミナルに集計レポート |
| `reports/export_html.py` | スタンドアロン HTML レポート生成 |
| `dashboard/server.py` | ブラウザ向けライブダッシュボード (SSE 配信) |

---

## 第 1 章: イベントの形を見る 🔬

`usage.jsonl` は **1 行 = 1 イベント** の append-only ログです。サンプルファイルの規模を見てみます。

```bash
wc -l docs/walkthrough/fixtures/usage-sample.jsonl && du -h docs/walkthrough/fixtures/usage-sample.jsonl
```

```output
     625 docs/walkthrough/fixtures/usage-sample.jsonl
200K	docs/walkthrough/fixtures/usage-sample.jsonl
```

イベントの種類別に件数を出してみると、何がどれくらい飛んでくるか見えます。

```bash
python3 -c "
import json, collections
c = collections.Counter()
for line in open(\"docs/walkthrough/fixtures/usage-sample.jsonl\"):
    c[json.loads(line)[\"event_type\"]] += 1
for k, v in c.most_common():
    print(f\"{v:5d}  {k}\")"
```

```output
  140  notification
  110  instructions_loaded
   95  subagent_stop
   65  user_slash_command
   61  session_start
   58  session_end
   41  subagent_lifecycle_start
   31  subagent_start
   24  skill_tool
```

たとえば `skill_tool` (Claude が Skill ツールを呼び出した) と `user_slash_command` (ユーザが `/foo` と打った) は両方とも「スキルが使われた」を意味しますが、**経路が違うので別 event_type** になっています。

実際の 1 件を覗いてみましょう。

```bash
python3 -c "
import json
for line in open(\"docs/walkthrough/fixtures/usage-sample.jsonl\"):
    e = json.loads(line)
    if e[\"event_type\"] == \"skill_tool\":
        print(json.dumps(e, ensure_ascii=False, indent=2))
        break"
```

```output
{
  "event_type": "skill_tool",
  "skill": "codex-review",
  "args": "差分をレビュー！(Round 2)",
  "project": "claude-transcript-analyzer",
  "session_id": "339b08d0-bf45-4f14-bd5c-a27bd74ba3cd",
  "timestamp": "2026-04-29T00:37:55.791779+00:00",
  "duration_ms": 297728,
  "permission_mode": "auto",
  "tool_use_id": "toolu_01TLicmmWovs39b9R9hszgiq",
  "success": true
}
```

スキーマのフィールドはこんな意味です:

- `skill` — 起動された skill 名 (slash 系は `/foo` 形式)
- `args` — Skill 呼び出しに渡された引数 (今回は「差分をレビュー！(Round 2)」)
- `project` — 作業ディレクトリ名 (`cwd` の basename)
- `session_id` — Claude Code のセッション UUID
- `duration_ms` — Skill が完走するのに掛かった時間
- `permission_mode` — 実行時のパーミッションモード (`auto` / `default` など)
- `success` — Skill が成功したか (失敗だと `event_type` 同じまま `success=false`)

スキーマの正本は `docs/spec/usage-jsonl-events.md` にあります。

---

## 第 2 章: イベント 1 件のライフサイクル 🌱

ではいよいよ「Claude Code から飛んできた raw な hook 入力が、どうやって `usage.jsonl` の 1 行になるか」を実機で再現します。

Hook の入り口 `hooks/record_skill.py` は **stdin から JSON を 1 つ受け取り、stdout には何も出さず、ファイルに 1 行 append する** という単純なフィルタです。

模擬の `PostToolUse(Skill)` 入力を流し込んでみます👇

```bash
TMP=$(mktemp -d)
EVENT='{"hook_event_name":"PostToolUse","tool_name":"Skill","tool_input":{"skill":"frontend-design","args":"ダッシュボードの UI 改善"},"cwd":"/projects/claude-transcript-analyzer","session_id":"demo-session-001","duration_ms":4567,"permission_mode":"default","tool_use_id":"toolu_demo_xyz","tool_response":{"success":true}}'
echo "$EVENT" | USAGE_JSONL="$TMP/usage.jsonl" python3 hooks/record_skill.py
python3 -c "
import json
for line in open('$TMP/usage.jsonl'):
    e = json.loads(line)
    e['timestamp'] = '<set by the hook to now>'
    print(json.dumps(e, ensure_ascii=False, indent=2))"
rm -rf "$TMP"

```

```output
{
  "event_type": "skill_tool",
  "skill": "frontend-design",
  "args": "ダッシュボードの UI 改善",
  "project": "claude-transcript-analyzer",
  "session_id": "demo-session-001",
  "timestamp": "<set by the hook to now>",
  "duration_ms": 4567,
  "permission_mode": "default",
  "tool_use_id": "toolu_demo_xyz",
  "success": true
}
```

重要なポイント:
- 入力では `hook_event_name=PostToolUse` + `tool_name=Skill` だったのが、出力では `event_type=skill_tool` に **正規化** されている
- `cwd=/projects/claude-transcript-analyzer` の basename だけが `project` として保存される (フルパスは漏らさない)
- `timestamp` は hook 側で `datetime.now(timezone.utc).isoformat()` を打つ (元の入力にはない)

次にユーザが **`/foo` とタイプした** ケースを見ます。これは `UserPromptExpansion` という別の hook で同じ `record_skill.py` が処理します。

```bash
TMP=$(mktemp -d)
EVENT='{"hook_event_name":"UserPromptExpansion","expansion_type":"slash_command","command_name":"insights","cwd":"/projects/claude-transcript-analyzer","session_id":"demo-session-002"}'
echo "$EVENT" | USAGE_JSONL="$TMP/usage.jsonl" python3 hooks/record_skill.py
python3 -c "
import json
for line in open('$TMP/usage.jsonl'):
    e = json.loads(line)
    e['timestamp'] = '<set by the hook to now>'
    print(json.dumps(e, ensure_ascii=False, indent=2))"
rm -rf "$TMP"

```

```output
{
  "event_type": "user_slash_command",
  "skill": "/insights",
  "args": "",
  "source": "expansion",
  "project": "claude-transcript-analyzer",
  "session_id": "demo-session-002",
  "timestamp": "<set by the hook to now>"
}
```

スラッシュコマンドは `event_type=user_slash_command` になり、`source=expansion` が付きます。`/clear` や `/help` のような **組み込みコマンドは記録しない** ロジックも `record_skill.py` の中で BUILTIN_COMMANDS リストとして弾かれています (`/exit /clear /help /compact /mcp /config /model /resume /context /skills /hooks /fast`)。

> 💡 サブエージェント (`Task` / `Agent` ツール) には専用の `hooks/record_subagent.py` があり、`SubagentStart` / `SubagentStop` のペアリングを通じて invocation 単位で集計できるようになっています。アルゴリズムの解説は `docs/reference/subagent-invocation-pairing.md` にあります。

---

## 第 3 章: 集計レポート 📊

ここまでが **収集 (Collect) と保管 (Store)**。次は **提示 (Present)** です。

`reports/summary.py` は `usage.jsonl` を読んで、ターミナルに人間向けの集計を出します。サンプル fixture で走らせると、これだけ見れます👇

```bash
USAGE_JSONL=docs/walkthrough/fixtures/usage-sample.jsonl python3 reports/summary.py
```

```output
Total events: 625

=== Sessions ===
  Total sessions:       61
  Resume rate:          0 (0%)
  Compact events:       0
  Permission prompts:   16

=== Skills (skill_tool + user_slash_command) ===
    23  fail=  0 (-)  /kk-save-findings
    23  fail=  0 (-)  /claude-transcript-analyzer:restart-dashboard
     5  fail=  0 (-)  codex-review
     4  fail=  0 (-)  /codex-review
     4  fail=  0 (-)  /kk-review-candidates
     4  fail=  0 (-)  /start-issue-planning
     3  fail=  0 (-)  skill-creator
     3  fail=  0 (-)  chrome-devtools-mcp
     3  fail=  0 (-)  /start-implementation
     2  fail=  0 (-)  /patch-release
     1  fail=  0 (-)  patch-release
     1  fail=  0 (-)  /kk-organize-findings
     1  fail=  0 (-)  /start-issue-with-planning
     1  fail=  0 (-)  claude-code-harness-reference
     1  fail=  0 (-)  llm-doc-authoring
     1  fail=  0 (-)  cross-os-python-portability
     1  fail=  0 (-)  python-testing-patterns
     1  fail=  0 (-)  claude-code-github-authoring-patterns
     1  fail=  0 (-)  frontend-tooltip-patterns
     1  fail=  0 (-)  fork-and-detach-launcher-pattern
     1  fail=  0 (-)  verify-bot-review
     1  fail=  0 (-)  stacked-pr-workflow
     1  fail=  0 (-)  ruby-gem-security-triage
     1  fail=  0 (-)  laravel-restful-controllers
     1  fail=  0 (-)  rails-restful-controllers

=== Subagents ===
    50  fail=  0 (-)  avg= 112.1s  plan-reviewer
    10  fail=  0 (-)  avg=      -  general-purpose
     8  fail=  0 (-)  avg= 273.7s  Plan
     4  fail=  0 (-)  avg=  63.4s  claude-code-guide
```

読み方:
- **Sessions** — 61 セッションぶんの集計。`Permission prompts: 16` は許可ダイアログが出た回数なので、UX 摩擦の代理指標 (Issue #19 で `friction_signals` として整備された)
- **Skills** — `skill_tool` (素の skill 起動) と `user_slash_command` (`/foo`) を統合した skill 利用ランキング。`fail=` は失敗回数
- **Subagents** — Subagent 種別ごとの呼び出し回数と平均実行時間。`plan-reviewer` を 50 回・平均 112.1 秒も使ってるのが見て取れる

`reports/export_html.py` を使えば同じ集計を **スタンドアロン HTML ファイル** として書き出せます (CI で生成して slack に貼るような用途向け)。

---

## 第 4 章: 本番運用での居場所 🏠

ここまで全て `docs/walkthrough/fixtures/usage-sample.jsonl` を読ませて来ましたが、**プラグインとして動いている本番では別の場所にデータが貯まります**。

    ~/.claude/transcript-analyzer/
    ├── usage.jsonl              ← hot tier (直近 180 日)
    ├── archive/
    │   └── 2025-11.jsonl.gz     ← cold tier (180 日超を月次 .jsonl.gz)
    │   └── 2025-12.jsonl.gz
    └── .archive_state.json      ← rotation の進捗 marker

設計のキモ:
- **append-only** — `hooks/_append.py` が cross-platform なファイルロックを取って 1 行ずつ追記する。複数の Claude Code インスタンスが同時に書いても壊れない
- **180 日 retention** — `scripts/archive_usage.py` が SessionStart 時にべき等起動され、180 日超のイベントを月次 gzip に押し出す
- **Archive は不変** — 一度 cold tier に入ったイベントは **書き換えない**。`reports/summary.py --include-archive` のような opt-in でだけ読まれる

詳しい契約は `docs/spec/archive-runtime.md`、設計判断は `docs/reference/storage.md`。

---

## 第 5 章: ブラウザで見る 🖥️

`dashboard/server.py` が起動すると、ローカルポートで HTML を配信し、`/api/data` で JSON を吐き、`/sse` で **ライブ更新** を流します。

仕組み的には:
1. SessionStart hook で `hooks/launch_dashboard.py` が **fork-and-detach でべき等に** サーバを立ち上げる
2. ブラウザは `/sse` を購読し、新規イベントが入るたびに差分を流し込む (Issue #83 で 1 秒ハートビートを 3 秒明滅に)
3. 5 分間アクセスが無いと idle 停止する

設計の非自明ポイント (テンプレート分割の sentinel concat、JSON-in-`<script>` の escape など) は `docs/reference/dashboard-server.md` に蒸留されています。

dashboard は本番環境を必要とするのでこの walkthrough では実行しませんが、手元で見るには `python3 dashboard/server.py` を立ち上げて `http://localhost:8347` を開く、もしくはスラッシュコマンド `/usage-dashboard` でも起動できます。

---

## 続きの読み物 📚

- 🧬 **生 transcript の中身** → `docs/transcript-format.md`
- 📋 **`usage.jsonl` の正式スキーマ** → `docs/spec/usage-jsonl-events.md`
- 🌐 **`/api/data` の JSON schema** → `docs/spec/dashboard-api.md`
- 💾 **保管・アーカイブの設計判断** → `docs/reference/storage.md`
- 🪝 **Hook 哲学の整理** → `docs/reference/hook-philosophy.md`

---

## おまけ: この walkthrough 自体を再生する 🔁

このドキュメントは showboat 0.6.1 で作られています。書き直す手順だけを取り出すには `showboat extract docs/walkthrough/data-flow.md` を実行すると、`init` / `note` / `exec` の系列が出力されます。出力ブロックは含まれない (verify が再生成する) ので、別のリポジトリ向けに再利用したい場合の雛形にも使えます🛠️

