# Claude Code トランスクリプトのファイル構造

このプロジェクトが解析対象とする Claude Code トランスクリプト（`.jsonl`）の場所と構造をまとめたドキュメント。

## ファイルの場所

```
~/.claude/projects/<project-dir>/<session-id>.jsonl
```

Windows では `%USERPROFILE%\.claude\projects\<project-dir>\<session-id>.jsonl` (Claude Code 本体の `HOME` 解決と同じ規約)。

### `<project-dir>` の命名規則

プロジェクトの絶対パスを正規化したもの。Claude Code 本体は **`/`, `\`, `:`, `.` をすべて `-`** に変換する (Issue #24 で実機 `ls ~/.claude/projects/` で確認済み)。先頭の `-` はそのまま残る。

```
POSIX:
  /Users/foo/myapp                          →  -Users-foo-myapp
  /Users/kkoichi/Developer/personal/chirper →  -Users-kkoichi-Developer-personal-chirper
  /Users/foo/.worktrees/issue-1             →  -Users-foo--worktrees-issue-1   (dot 入り)
  /Users/foo/my.app/sub                     →  -Users-foo-my-app-sub          (dot 入り)

Windows:
  C:\Users\foo\myapp                        →  C--Users-foo-myapp
  C:\Users\foo\.config\app                  →  C--Users-foo--config-app
```

このエンコード規則は `hooks/verify_session.py:_encode_cwd()` に集約されている。

### `<session-id>`

Claude Code セッションごとに UUID 形式のファイルが1つ作られる。

---

## レコードの基本形式

各行が1つの JSON オブジェクト（JSONL 形式）。

```json
{
  "timestamp": "2026-02-28T10:00:00.000Z",
  "message": {
    "role": "user" | "assistant",
    "content": <string または array>
  }
}
```

---

## このプロジェクトが対象とするレコード

### 1. ユーザーの slash コマンド（`UserPromptSubmit`）

ユーザーが `/skill-name` を直接入力したとき、`role: "user"` のレコードに `<command-name>` タグが埋め込まれる。

```json
{
  "timestamp": "2026-02-28T10:00:00.000Z",
  "message": {
    "role": "user",
    "content": "<command-name>/user-story-creation</command-name>\n<command-message>Issue #6</command-message>\n..."
  }
}
```

- `content` は **文字列**
- スキル名は `<command-name>` と `</command-name>` の間に入る
- Claude Code 組み込みコマンド（`/exit`, `/clear`, `/help` など）は除外する

### 2. アシスタントによる Skill ツール呼び出し（`PostToolUse(Skill)`）

アシスタントが `Skill` ツールを使ったとき、`role: "assistant"` のレコードの `content` 配列に `tool_use` ブロックが入る。

```json
{
  "timestamp": "2026-02-28T10:05:00.000Z",
  "message": {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "name": "Skill",
        "input": {
          "skill": "user-story-creation",
          "args": "6"
        }
      }
    ]
  }
}
```

- `content` は **配列**
- `block.name == "Skill"` で判別
- `input.skill` がスキル名、`input.args` がオプション引数

### 3. アシスタントによる Subagent 起動（`PostToolUse(Task)`）

アシスタントが `Task` ツール（Subagent）を使ったとき、`tool_use` ブロックのツール名が `"Task"` になる。

```json
{
  "timestamp": "2026-02-28T10:06:00.000Z",
  "message": {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "name": "Task",
        "input": {
          "subagent_type": "Explore",
          "description": "Explore controller patterns",
          "prompt": "Explore the existing controller patterns...",
          "run_in_background": false
        }
      }
    ]
  }
}
```

- `block.name == "Task"` で判別（`"Agent"` ではない）
- `input.subagent_type` がエージェント種別（`Explore`, `Plan`, `general-purpose` など）
- `input.run_in_background` でバックグラウンド実行かどうかがわかる

---

## 除外すべき組み込みコマンド

以下は Claude Code の組み込み slash コマンドのため、Skill としてカウントしない:

```
/exit  /clear  /help  /compact
/mcp   /config /model
/resume /context /skills /hooks /fast
```

---

## このプロジェクトでの利用方法

このプロジェクトは `.jsonl` を直接 parse するのではなく、**Claude Code Hooks** を使ってリアルタイムにイベントを収集する方式を採用している。

> Hook 仕様の出典は公式ドキュメント [Claude Code — Hooks](https://code.claude.com/docs/en/hooks) を参照。
> 本ドキュメントは v0.2 時点の実装が依存しているフィールドのみを抜粋している。

### 購読している Hook イベント

| Hook イベント | 収集対象 | スクリプト |
|--------------|---------|-----------|
| `PostToolUse(Skill)` | Skill ツール呼び出し成功 | `hooks/record_skill.py` |
| `PostToolUseFailure(Skill)` | Skill ツール失敗 | `hooks/record_skill.py` |
| `UserPromptSubmit` | ユーザー slash コマンド（フォールバック） | `hooks/record_skill.py` |
| `UserPromptExpansion` | ユーザー slash コマンド（主経路） | `hooks/record_skill.py` |
| `PostToolUse(Task\|Agent)` | Subagent 起動成功 | `hooks/record_subagent.py` |
| `PostToolUseFailure(Task\|Agent)` | Subagent 起動失敗 | `hooks/record_subagent.py` |
| `SubagentStart` | Subagent ライフサイクル開始 | `hooks/record_subagent.py` |
| `SubagentStop` | Subagent ライフサイクル終了 | `hooks/record_subagent.py` |
| `SessionStart` / `SessionEnd` | セッション境界 | `hooks/record_session.py` |
| `PreCompact` / `PostCompact` | コンテキスト圧縮境界 | `hooks/record_session.py` |
| `Notification` | idle / 確認待ち等 | `hooks/record_session.py` |
| `InstructionsLoaded` | CLAUDE.md / memory / skill 等のロード | `hooks/record_session.py` |
| `Stop` | 停止時の transcript ↔ usage 整合性チェック | `hooks/verify_session.py` |

収集されたイベントは `~/.claude/transcript-analyzer/usage.jsonl` に追記される（`USAGE_JSONL` 環境変数で差し替え可）。
過去セッションのトランスクリプトを遡って取り込みたい場合は `scripts/rescan_transcripts.py` を使う。

---

## Hook 入力 JSON の上位スキーマ

Hook スクリプトは標準入力で JSON を受け取る。共通フィールドと、本プロジェクトの実装が依存している付加フィールドを下記にまとめる。
※ 完全な仕様は [公式ドキュメント](https://code.claude.com/docs/en/hooks) を参照。

### 共通フィールド（全イベント）

| フィールド | 型 | 説明 |
|-----------|----|------|
| `hook_event_name` | string | `PostToolUse` / `SubagentStart` / `SessionStart` 等のイベント名 |
| `session_id` | string | セッション UUID |
| `cwd` | string | プロジェクトの絶対パス |
| `transcript_path` | string | 当該セッションの `.jsonl` への絶対パス |

### `PostToolUse` 固有フィールド

| フィールド | 型 | 説明 |
|-----------|----|------|
| `tool_name` | string | `Skill` / `Task` / `Agent` 等 |
| `tool_input` | object | ツールに渡された入力（`{"skill": "...", "args": "..."}` など） |
| `tool_response` | object | ツールの戻り値。`{"success": bool, ...}` を含む |
| `tool_use_id` | string | `toolu_...` 形式の ID。同一呼び出しを横串で結ぶ |
| `duration_ms` | number | ツール実行時間（ミリ秒） |
| `permission_mode` | string | `acceptEdits` / `bypassPermissions` / `default` / `plan` 等 |

### `PostToolUseFailure` 固有フィールド

`PostToolUse` と同じフィールドに加え：

| フィールド | 型 | 説明 |
|-----------|----|------|
| `error` | string | エラーメッセージ |
| `is_interrupt` | bool | ユーザー介入による中断か |

`tool_response` は来ないため、本プロジェクトでは `success: false` を強制セットする。

### `UserPromptSubmit` / `UserPromptExpansion`

`UserPromptSubmit` は生 prompt を含み、本プロジェクトは `<command-name>` タグおよび先頭 `/<token>` を抽出する。
`UserPromptExpansion` は slash コマンド展開時に発火し、より構造化された情報を持つ：

| フィールド | 型 | 説明 |
|-----------|----|------|
| `expansion_type` | string | `slash_command` のみ採用 |
| `command_name` | string | スキル名（`/` プレフィクスは付かないことがある） |

両方とも発火するため、`record_skill.py` は記録時に `source: "expansion" | "submit"` を付与し、submit 経路の dedup は **source!="submit" のレコードに対してのみ** 5 秒以内の重複として抑止する。
これにより expansion→submit の二重発火は 1 件にまとめつつ、expansion が来ない経路での submit 連打は両方記録される。

> **既知の制約 (mixed-mode dedup)**: 1 回目が expansion+submit ペアで発火し、2 回目が submit のみ（expansion 落ち）で発火する mixed-mode シナリオでは、2 回目の submit が直前の expansion を見て dedup されてしまい undercount になる。append-only ログで「ペア消費」状態を持てない構造的制約。実機の Claude Code は通常両方発火するため実害は限定的。
>
> **structural limit と implementation choice の区別**: 上記の undercount は **structural limit**（schema 変更なしには直せない構造的制約）であって implementation の選び方ミスではない。append-only ログ + write-time dedup の組み合わせは「skip した record は downstream から見えない / skip 自体も検出できない」という一方通行を不可避に伴う。
>
> **構造的に正しい代替案 (どちらも schema 変更を伴うため次バージョンスコープ)**:
> - **(a) record + mark**: skip せずに全 record を残し、`superseded_by_<event>` 等のマーカーフィールドで downstream filter する。コスト：1 record あたり 1 フィールド増。利得：dedup の判断を後段に移譲できて recoverable
> - **(b) skip イベント自体を残す**: `dedup_skipped` イベントを毎回 emit しておき、count metric が「実発火 + skip」として完全に保存される
>
> **設計時の判断ルール**: write-time dedup を入れる前に「downstream の任意の count metric が 5% skip されたら何が壊れるか」を必ず先に問う。「count metric が壊れる」が答えなら write-time dedup を採用せず record + mark に倒す。schema 変更は rescan_transcripts / dashboard / exporter まで波及するため、**現 PR で fix せず次バージョンスコープに defer する**判断も併せて取る（後からの schema 変更コストの方が、確定的な undercount 1 ケースより高い）。

### `SubagentStart` / `SubagentStop`

| フィールド | 型 | 説明 |
|-----------|----|------|
| `agent_type` | string | サブエージェント種別 |
| `agent_id` | string | サブエージェント ID（Stop 時に有用） |
| `duration_ms` | number | 実行時間（Stop 時） |
| `success` | bool | 成否（Stop 時） |

> **設計メモ — 観測点の役割分担**
> `PostToolUse(Task\|Agent)` を **正規観測点** として `event_type: subagent_start` で記録する（count 集計の主ソース）。
> `SubagentStart` hook 由来は補助観測として `event_type: subagent_lifecycle_start` で別個に記録する。
> 集計側 (`subagent_metrics.aggregate_subagent_metrics`) は `(session_id, subagent_type)` バケット内で
> 両ソースを timestamp 順マージし `INVOCATION_MERGE_WINDOW_SECONDS` (1 秒) 以内なら同一 invocation の重複扱い、
> それ以上離れていれば別 invocation として count する。これにより：
> - 両 hook 並列発火 → 1 invocation（重複統合）
> - PostToolUse 不在で lifecycle のみ → lifecycle 件数だけ invocation
> - 起動失敗 (start) と独立した lifecycle → 別 invocation として両方カウント
>
> ヘッドラインメトリクス (`total_events` / `daily_trend` / `project_breakdown`) も
> `subagent_metrics.usage_invocation_events()` 経由で同じ invocation 同定を使い、
> 各 invocation の代表イベント (start を優先、無ければ lifecycle) 1 件だけを反映する。
> これで lifecycle-only invocation も headline に現れ、`subagent_ranking` と数字が必ず一致する。
> 詳細は `CLAUDE.md` の同名セクションを参照。
>
> **設計教訓 — frozenset(event_types) フィルタは dedup を担えない**:
> 過去に `subagent_ranking` と `total_events` で件数が食い違った（PR #12 codex review round 9-10 P2）。原因は前者が `aggregate_subagent_metrics` の invocation 同定経由、後者が `frozenset(event_types) ⊂ {whitelist}` の生イベントフィルタ経由で、後者に dedup 知識が無かった。`frozenset(event_types)` フィルタは **「include / exclude」しか表現できず dedup semantics を持てない** — 同じイベントログから 2 つ以上の view を出していて片方が dedup する場合、もう一方も同じ helper を経由させなければ silent な UI inconsistency が出る。
>
> **DRY 圧の発生源**: `aggregate_*_metrics()` がドメインに既にあるなら、ヘッドライン / total 系のメトリクスは **必ずそれ経由** で計算する。convention で揃える、ではなく shared helper を passing through することで構造的に揃える。
>
> **回帰防止テスト**: 同一 fixture に対して `headline_count == ranking_count` を assert する **cross-aggregator invariant test** を、dual-hook の各 flake モード（start-only / lifecycle-only / both-merged-within-1s / both-disjoint）について書く。per-aggregator の単体テストは pass しても cross が壊れているケースを catch する形。

### `SessionStart` / `SessionEnd`

| フィールド | 型 | 説明 |
|-----------|----|------|
| `source` | string | `startup` / `resume` / `clear` 等 |
| `model` | string | 使用モデル ID |
| `agent_type` | string | サブエージェントとして起動した場合のみ |
| `reason` | string | `SessionEnd` の終了理由 |

### `PreCompact` / `PostCompact`

| フィールド | 型 | 説明 |
|-----------|----|------|
| `trigger` | string | `auto` / `manual` 等 |

### `Notification`

| フィールド | 型 | 説明 |
|-----------|----|------|
| `notification_type` | string | `permission` / `permission_prompt` / `idle` / `idle_prompt` 等 |

> 集計側 (`dashboard/server.py` / `reports/summary.py`) では `permission` と `permission_prompt` を同一視して
> permission_prompt_count に加算する（公式仕様の短縮形と過去実装の値ゆれを吸収）。

### `InstructionsLoaded`

| フィールド | 型 | 説明 |
|-----------|----|------|
| `file_path` | string | ロードされたファイル |
| `memory_type` | string | `user` / `project` / `skill` 等 |
| `load_reason` | string | `session_start` / `glob_match` 等 |
| `globs` / `trigger_file_path` / `parent_file_path` | any | オプションのコンテキスト情報 |

