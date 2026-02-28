# Skill 使用状況の調べ方

Claude Code のトランスクリプト（`.jsonl`）を解析して、プロジェクト横断で Skill の使用状況を調べる方法。

## トランスクリプトの場所

```
~/.claude/projects/<project-dir>/<session-id>.jsonl
```

`<project-dir>` はプロジェクトのパスを `/` → `-` に変換したもの。
例: `/Users/foo/myapp` → `-Users-foo-myapp`

## Skill 起動の2種類のパターン

### 1. ユーザーが `/skill-name` で直接呼び出す

ユーザーメッセージ内に以下のタグが入る:

```json
{
  "message": {
    "role": "user",
    "content": "<command-name>/user-story-creation</command-name>\n<command-message>...</command-message>"
  }
}
```

### 2. アシスタントが Skill ツールを呼び出す

アシスタントメッセージの `content` 配列内に `tool_use` ブロックが入る:

```json
{
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

## クイック調査（bash）

### ユーザー slash コマンドの一覧

```bash
grep -r '<command-name>' ~/.claude/projects/ --include="*.jsonl" -h \
  | grep -oE '<command-name>[^<]+</command-name>' \
  | sort | uniq -c | sort -rn
```

### アシスタントが使った Skill の一覧

```bash
grep -r '"name":"Skill"' ~/.claude/projects/ --include="*.jsonl" -h \
  | grep -oE '"skill":"[^"]*"' \
  | sort | uniq -c | sort -rn
```

## 詳細解析（Python）

```python
import json
import os
import re
from collections import defaultdict

projects_base = os.path.expanduser("~/.claude/projects")
skill_data = []

for project_name in os.listdir(projects_base):
    project_path = os.path.join(projects_base, project_name)
    if not os.path.isdir(project_path):
        continue

    for fname in os.listdir(project_path):
        if not fname.endswith('.jsonl'):
            continue

        fpath = os.path.join(project_path, fname)
        session_id = fname.replace('.jsonl', '')

        with open(fpath, 'r', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = obj.get('message', {})
                role = msg.get('role', '')
                content = msg.get('content', '')
                timestamp = obj.get('timestamp', '')

                # パターン1: ユーザーの slash コマンド
                if role == 'user' and isinstance(content, str):
                    m = re.search(r'<command-name>(/\S+)</command-name>', content)
                    if m:
                        cmd = m.group(1)
                        # 組み込みコマンドを除外
                        builtin = {'/exit', '/clear', '/mcp', '/config', '/resume',
                                   '/model', '/context', '/skills', '/compact', '/help'}
                        if cmd not in builtin:
                            skill_data.append({
                                'type': 'user_slash_command',
                                'skill': cmd,
                                'project': project_name,
                                'session': session_id,
                                'timestamp': timestamp,
                            })

                # パターン2: アシスタントの Skill ツール呼び出し
                if role == 'assistant' and isinstance(content, list):
                    for block in content:
                        if (isinstance(block, dict)
                                and block.get('type') == 'tool_use'
                                and block.get('name') == 'Skill'):
                            inp = block.get('input', {})
                            skill_data.append({
                                'type': 'assistant_skill_tool',
                                'skill': inp.get('skill', 'unknown'),
                                'args': inp.get('args', ''),
                                'project': project_name,
                                'session': session_id,
                                'timestamp': timestamp,
                            })

# プロジェクト別集計
project_skills = defaultdict(list)
for item in skill_data:
    project_skills[item['project']].append(item)

print(f"Total: {len(skill_data)} invocations\n")
for proj, items in sorted(project_skills.items(), key=lambda x: -len(x[1])):
    print(f"{proj} ({len(items)}):")
    counts = defaultdict(int)
    for item in items:
        counts[item['skill']] += 1
    for skill, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {count}x {skill}")
```

## 除外すべき組み込みコマンド

以下は Claude Code の組み込み slash コマンドなので Skill ではない:

- `/exit` `/clear` `/help` `/compact`
- `/mcp` `/config` `/model`
- `/resume` `/context` `/skills`

## 調査結果メモ（2026-02-28 時点）

| Skill | 回数 | プロジェクト |
|-------|------|------------|
| `user-story-creation` | 4 | personal-chirper |
| `/insights` | 4 | chirper, hobo |
| `webapp-testing` | 2 | personal-chirper |
| `ready-for-issue` | 1 | personal-chirper |

- chirper プロジェクトが Skill 活用の中心（10/11回）
- hobo・LAQQO・llm-git では Skill はほぼ未使用
