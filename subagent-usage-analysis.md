# Subagent (Task ツール) 使用状況の調べ方

Claude Code のトランスクリプト（`.jsonl`）を解析して、プロジェクト横断で Subagent の使用状況を調べる方法。

## トランスクリプト上での記録形式

Subagent はアシスタントメッセージ内の `tool_use` ブロックとして記録される。
ツール名は **`Task`**（`Agent` ではない）。

```json
{
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

### input フィールド

| フィールド | 内容 |
|-----------|------|
| `subagent_type` | エージェント種別（`Explore`, `Plan`, `general-purpose` 等） |
| `description` | 短い説明（3〜5語） |
| `prompt` | エージェントへの詳細な指示 |
| `run_in_background` | バックグラウンド実行かどうか（`true`/`false`） |

## クイック調査（bash）

### subagent_type 別の使用回数

```bash
grep -r '"name":"Task"' ~/.claude/projects/ --include="*.jsonl" -h \
  | grep -oE '"subagent_type":"[^"]*"' \
  | sort | uniq -c | sort -rn
```

### 全ツール名の使用回数（どんなツールが使われているか確認）

```bash
python3 - << 'EOF'
import json, os
from collections import Counter

projects_base = os.path.expanduser("~/.claude/projects")
tool_names = Counter()

for project_name in os.listdir(projects_base):
    project_path = os.path.join(projects_base, project_name)
    if not os.path.isdir(project_path):
        continue
    for fname in os.listdir(project_path):
        if not fname.endswith('.jsonl'):
            continue
        with open(os.path.join(project_path, fname), 'r', errors='ignore') as f:
            for line in f:
                try:
                    obj = json.loads(line.strip())
                except:
                    continue
                content = obj.get('message', {}).get('content', '')
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'tool_use':
                            tool_names[block.get('name', '')] += 1

for name, count in tool_names.most_common():
    print(f"{count:5d}  {name}")
EOF
```

## 詳細解析（Python）

```python
import json, os
from collections import defaultdict, Counter

projects_base = os.path.expanduser("~/.claude/projects")
task_data = []

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
                try:
                    obj = json.loads(line.strip())
                except:
                    continue
                msg = obj.get('message', {})
                if msg.get('role') != 'assistant':
                    continue
                content = msg.get('content', '')
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get('type') == 'tool_use' and block.get('name') == 'Task':
                        inp = block.get('input', {})
                        task_data.append({
                            'project': project_name,
                            'session': session_id,
                            'timestamp': obj.get('timestamp', ''),
                            'subagent_type': inp.get('subagent_type', 'unknown'),
                            'description': inp.get('description', ''),
                            'run_in_background': inp.get('run_in_background', False),
                        })

# subagent_type 別
print(f"Total: {len(task_data)} invocations\n")
print("=== By subagent_type ===")
for t, c in Counter(d['subagent_type'] for d in task_data).most_common():
    print(f"  {c:3d}x  {t}")

# プロジェクト別
print("\n=== By Project ===")
proj_data = defaultdict(list)
for d in task_data:
    proj_data[d['project']].append(d)
for proj, items in sorted(proj_data.items(), key=lambda x: -len(x[1])):
    proj_short = proj.replace('-Users-kkoichi-Developer-', '')
    print(f"\n  {proj_short} ({len(items)}):")
    for t, c in Counter(d['subagent_type'] for d in items).most_common():
        print(f"    {c}x {t}")

# タイムライン
print("\n=== Timeline ===")
for d in sorted(task_data, key=lambda x: x['timestamp']):
    ts = d['timestamp'][:16]
    proj_short = d['project'].replace('-Users-kkoichi-Developer-', '')
    bg = " [bg]" if d['run_in_background'] else ""
    print(f"  {ts}  {proj_short:30s}  {d['subagent_type']:15s}{bg}  {d['description'][:60]}")
```

## 並列起動の検出

同一分内に複数の Task が起動されている場合を抽出する（並列 Subagent の証拠）:

```python
from collections import defaultdict

# task_data は上記スクリプトで収集済みとする
groups = defaultdict(list)
for d in task_data:
    groups[(d['project'], d['session'], d['timestamp'][:16])].append(d)

for (proj, sess, ts), items in sorted(groups.items(), key=lambda x: x[0][2]):
    if len(items) > 1:
        proj_short = proj.replace('-Users-kkoichi-Developer-', '')
        print(f"{ts}  {proj_short}  ({len(items)}個並列):")
        for d in items:
            bg = "[bg]" if d['run_in_background'] else "   "
            print(f"    {bg} {d['subagent_type']:10s}  {d['description'][:55]}")
        print()
```

## 調査結果メモ（2026-02-28 時点）

### 全体

| 項目 | 値 |
|------|---|
| 総起動回数 | 48回 |
| Subagent を使ったプロジェクト | 1（personal-chirper のみ） |
| subagent_type の種類 | 2種類（Explore / Plan） |

### subagent_type 別

| subagent_type | 回数 |
|---------------|------|
| `Explore` | 31 |
| `Plan` | 17 |

### 並列起動パターン（同一分内に複数起動）

| 日時 | 並列数 | 内容 |
|------|--------|------|
| 2026-02-07T09:51 | 2 | Explore × 2（ページネーション調査） |
| 2026-02-07T10:07 | 2 | Explore × 2（views + tests） |
| 2026-02-08T09:03 | 3 | Explore × 3（controllers / views / tests） |
| 2026-02-08T14:05 | 3 | Explore × 3（実装パターン / Policy / DB） |
| 2026-02-11T05:29 | 3 | Explore × 3（models / controllers / tests） |

### 気づき

- **chirper プロジェクトのみで全 48回** — 他プロジェクトでは一度も使われていない
- **Explore → Plan の順で使われるパターンが多い** — コードベースを先に調査してから設計する流れ
- **並列 Explore が頻繁** — 同時に複数の観点（controllers / views / tests）からコードベースを調べるパターンが確立している
- `run_in_background: true` の使用は確認されず
