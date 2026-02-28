# Claude Code トランスクリプトのファイル構造

このプロジェクトが解析対象とする Claude Code トランスクリプト（`.jsonl`）の場所と構造をまとめたドキュメント。

## ファイルの場所

```
~/.claude/projects/<project-dir>/<session-id>.jsonl
```

### `<project-dir>` の命名規則

プロジェクトの絶対パスを `/` → `-` に変換したもの（先頭の `-` はそのまま残る）。

```
/Users/foo/myapp        →  -Users-foo-myapp
/Users/kkoichi/Developer/personal/chirper  →  -Users-kkoichi-Developer-personal-chirper
```

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

| Hook イベント | 収集対象 | スクリプト |
|--------------|---------|-----------|
| `PostToolUse(Skill)` | Skill ツール呼び出し | `hooks/record_skill.py` |
| `UserPromptSubmit` | ユーザー slash コマンド | `hooks/record_skill.py` |
| `PostToolUse(Task)` | Subagent 起動 | `hooks/record_subagent.py` |

収集されたイベントは `data/usage.jsonl` に追記される。

過去セッションのトランスクリプトをさかのぼって解析したい場合は、`skill-usage-analysis.md` および `subagent-usage-analysis.md` の bash/Python スクリプトを参照。

