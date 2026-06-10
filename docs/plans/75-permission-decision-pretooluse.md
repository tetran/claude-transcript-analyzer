# Plan: Issue #75 — Permission breakdown を構造化したい (PreToolUse hook で何の許可か記録)

## 📋 plan-reviewer 反映ログ

| Proposal | 内容 | 反映箇所 |
|----------|------|----------|
| (初稿) | — | — |

## 1. Goal

`Notification(permission|permission_prompt)` イベントの「何の許可か zero-information」問題を、新規 `PreToolUse` hook で `permission_decision in {ask, deny}` を**因果直結**で記録することで構造化する。新 event_type `permission_decision` に `tool_name` / `tool_input` (whitelist + 8KB cap) / `permission_decision` / `permission_decision_reason` / `tool_use_id` を載せ、これを唯一の attribution 入力として `_attribute_permission` を rewrite する (= 30 秒 backward-window heuristic を削除)。

dashboard / Quality タブの「Permission breakdown」は 3 セクション構成に再編する:

- **Skill 行** (`permission_prompt_skill_breakdown` の意味再定義) — `tool_name == "Skill"` の `ask` のみ。`tool_use_id` で `skill_tool` に直接 join し attribution heuristic を撤廃
- **Subagent 行** (`permission_prompt_subagent_breakdown` の意味再定義) — `tool_name in {Task, Agent}` の `ask` のみ。同上で `subagent_start` に直接 join
- **🆕 Tool 行** (新 API field `permission_prompt_tool_breakdown`) — 新 event 全体を `tool_name` でグルーピング。`Bash` / `Edit` / `WebFetch` / `mcp__*` 等を表面化し top command も提示

`permission_rate` の値が前 version より小さくなる (= 既存 2 field の値が変わる **観測可能 breaking change**) ことは Issue 確定 spec に従い受容、release notes に明記する。stdlib only / TDD first / additive forward-compat を厳守。

## 2. 採用 spec まとめ (前提固定)

確定済 spec (Issue #75 comment + Plan kickoff) を冒頭に固定して逸脱しない:

- 新 event_type: **`permission_decision`** (additive、archive tier 2 dispatch に追記)
- 購読 hook: **`PreToolUse`** のみ。filter は `permission_decision ∈ {"ask", "deny"}` (allow は記録しない)
- 保存ポリシー: **tool-specific whitelist + 各 field 8 KB cap**。未知 tool は `tool_input` を一切 dump せず `tool_name` のみで記録
- 旧 `Notification(permission|permission_prompt)` の **記録は継続** (= `record_session.py` の handler は無改修)。ただし新 attribution パイプラインの入力にはしない (= 旧 Notification は新 (α) 集計の対象外)
- attribution: **(α) のみ** — `tool_name in {Skill, Task, Agent}` の `permission_decision` を `tool_use_id` で `skill_tool` / `subagent_start` に直接 join。skill / subagent の内側で立つ Bash 等の ask は親 context へは紐付けず、Tool 行にだけ計上する **(β)**
- 30 秒 backward-window heuristic は **削除** (`PERMISSION_LINK_WINDOW_SECONDS` / `_skill_event_interval` を撤去)
- `permission_rate` は (α) のみで計算するため、Issue #61 当時より値が小さくなる。clamping は引き続き行わない
- `--include-archive` 時の旧データ (= `permission_decision` event を持たない月) は **新 (α) 集計に貢献しない**。dashboard 側で「旧形式は集計対象外」を help-pop に明記。Tool 行が空 / Skill 行 / Subagent 行が薄くなる挙動は受容
- target milestone: **v0.8.1**。`feature/75-permission-decision` を v0.8.1 release branch から切る (release branch を新規作成)
- stdlib only / TDD first / 既存 2 field shape は不変 (中身の semantics のみ再定義) / 新 field は additive に追加

### `tool_input` whitelist (Plan 確定版)

| `tool_name` | 残す field | 備考 |
|-------------|------------|------|
| `Bash` | `command` | 一番情報量が多い。secrets を含む可能性あり (R8) |
| `Edit` / `MultiEdit` / `Write` | `file_path` | path のみ。content は捨てる |
| `Read` / `NotebookEdit` | `file_path` | 同上 |
| `WebFetch` | `url` | host/path はそのまま |
| `WebSearch` | `query` | search 文字列を残す |
| `Skill` | `skill` | skill 起動時 ask の identifier (= `tool_use_id` 経由で skill_tool join するが補助情報としても保持) |
| `Task` / `Agent` | `subagent_type`, `description` | description は短い概要文 |
| `mcp__chrome-devtools__navigate_page` | `url` | デバッグ MCP 用、URL 残す |
| `mcp__chrome-devtools__evaluate_script` | (なし) | JS 全文は危険 = `tool_name` のみ |
| `mcp__chrome-devtools__take_screenshot` | `selector` | DOM hook のみ残す |
| `mcp__chrome-devtools__*` (上記以外) | (なし) | 既知 click/fill/hover 等は引数に座標 / DOM hint しか含まないため `tool_name` のみで十分 |
| `mcp__plugin_figma_figma__use_figma` | `code` を **除外**、`fileKey`, `nodeId` のみ残す | code 内容は危険、参照系のみ残す |
| `mcp__plugin_figma_figma__get_*` | `fileKey`, `nodeId` | 参照系 |
| `mcp__plugin_figma_figma__*` (上記以外) | (なし) | 慎重側 fallback |
| `mcp__*` (whitelist 未登録) | (なし) | unknown MCP は `tool_name` のみ (= 安全側) |
| その他 (未知 tool) | (なし) | 未知 tool fallback |

各保存 field は **str 化後 utf-8 8192 bytes で truncate** (= byte-level cap、UTF-8 マルチバイト境界で切らないために `encode → 8192 で slice → decode(errors="ignore")` を使う)。truncate 発生時は同 event に `"tool_input_truncated": true` を additive に追加 (downstream 側 defensive 不要だが観測可能性のため)。

dispatch table は `hooks/record_permission.py` 内に閉じる (= 他モジュールから import しない)。新 MCP 追加は dispatch table のみの 1 行追記で済むことを Plan で保証。

## 3. API contract: `permission_prompt_tool_breakdown` (新) + 既存 2 field の意味再定義

### 3.1 既存 field の意味再定義 (Issue #61 → Issue #75)

```jsonc
// 既存 field shape は維持。中身の attribution semantics のみ再定義
"permission_prompt_skill_breakdown": [
  {"skill": "user-story-creation", "prompt_count": 3, "invocation_count": 12, "permission_rate": 0.25}
],
"permission_prompt_subagent_breakdown": [
  {"subagent_type": "Explore", "prompt_count": 5, "invocation_count": 10, "permission_rate": 0.5}
]
```

| 項目 | Issue #61 (旧) | Issue #75 (新) |
|------|---------------|---------------|
| 入力 event | `notification(permission\|permission_prompt)` | `permission_decision (decision in {ask, deny})` |
| 帰属手段 | execution-window cover + 30s backward | `tool_use_id` で `skill_tool` / `subagent_start` に **直接 join** |
| skill 行の対象 | 旧 notification の skill_tool 帰属分 | `tool_name == "Skill"` の `permission_decision` を `tool_use_id` join → 親 `skill_tool.skill` を name に採用 |
| subagent 行の対象 | 旧 notification の subagent invocation 帰属分 | `tool_name in {"Task", "Agent"}` の `permission_decision` を `tool_use_id` join → `subagent_start.subagent_type` を name に採用 |
| `prompt_count` | attribute された notification 数 | join された `permission_decision` 数 (decision="deny" も含む) |
| `invocation_count` | 同 session の skill_tool / subagent invocation 件数 | (変更なし) — drift guard として `aggregate_subagent_metrics` の count と一致させる契約は継続 |
| `permission_rate` | `prompt_count / invocation_count` (clamp なし) | (変更なし、定義は同じ。値は (α) only に縮減されるため小さくなる) |

### 3.2 新 field: `permission_prompt_tool_breakdown`

```json
"permission_prompt_tool_breakdown": [
  {
    "tool_name": "Bash",
    "ask_count": 14,
    "deny_count": 2,
    "decision_count": 16,
    "top_inputs": [
      {"summary": "git push origin main", "count": 5},
      {"summary": "rm -rf node_modules", "count": 3},
      {"summary": "<no command>", "count": 1}
    ]
  },
  {
    "tool_name": "WebFetch",
    "ask_count": 3,
    "deny_count": 0,
    "decision_count": 3,
    "top_inputs": [
      {"summary": "https://example.com/api", "count": 2}
    ]
  },
  {
    "tool_name": "mcp__chrome-devtools__evaluate_script",
    "ask_count": 7,
    "deny_count": 0,
    "decision_count": 7,
    "top_inputs": []
  }
]
```

- **shape**: list[dict]、`decision_count` (= ask + deny) 降順 → `tool_name` 昇順で明示 sort
- **top-N**: 上位 **10 tool** で cap (= 既存 ranking の慣習)
- **`top_inputs`**: 各 tool で観測した `tool_input` summary (whitelist で残った field を 1 文字列に結合) ごとの上位 **3 件**、count 降順 → summary 昇順。Bash なら `command`、Edit なら `file_path` 等。tool_input が dump されない (= unknown tool / mcp 系 evaluate_script 等) は `top_inputs: []`
- **`summary` の作り方**: whitelist 残置 field を `" ".join(values)` で結合、120 chars で truncate (UI 表示余裕)。空 / すべて欠損なら `"<no input>"` の sentinel 文字列
- **`ask_count` / `deny_count`** は decision 別に分けて出す (= deny が多い tool は危険シグナル / 既存 settings で完全 block している tool の表面化)
- **空 events / `permission_decision` 0 件**: `[]` を返す (defensive 不要を browser に与える慣習)
- **period scope**: 既存 2 field と同じく **全期間 (period 不変、Quality タブ scope)**。`permission_rate` の cross-period drift を避けるため

### 3.3 ヘルパー関数 shape (`dashboard/server.py`)

`aggregate_permission_breakdowns(events, top_n=TOP_N)` の戻り値を additive に拡張:

```python
{
  "skill": [...],     # 既存 shape 不変
  "subagent": [...],  # 既存 shape 不変
  "tool": [...],      # 新 key (additive)
}
```

`build_dashboard_data` 側で `permission_breakdowns["tool"]` を `permission_prompt_tool_breakdown` キーに展開する (additive)。既存 2 field の名前は変更しない (= API consumer の互換維持、wording だけ help-pop で update)。

### 3.4 archive 互換性

- `_TIER2_DISPATCH` (`scripts/archive_usage.py:212`) に `"permission_decision": ("tool_name", "tool_use_id")` を追記。`tool_use_id` は (α) ask では必ず存在する前提だが (β) ask でも常に来るので tier 2 secondary key として load-bearing
- archive 後の月は新 attribution 入力として使える。逆に **old archive 月** (= `permission_decision` event ない) は help-pop で「旧形式 / 集計対象外」を明記
- `Notification(permission|permission_prompt)` event は引き続き archive される (記録継続のため、別経路 dedup ロジックは触らない)

## 4. Critical files (実装で触る箇所、行番号 pin)

### 編集
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/dashboard/server.py`
  - **line 91-95** 周辺: 既存 `_PERMISSION_NOTIFICATION_TYPES` は **残す** (`aggregate_session_stats` から参照されているため)。新規定数は別途追加
  - **line 467-472** (`PERMISSION_LINK_WINDOW_SECONDS` 定数): 削除
  - **line 475-496** (`_skill_event_interval`): 削除
  - **line 499-532** (`_attribute_permission`): 削除 (新実装に置換)
  - **line 535-652** (`aggregate_permission_breakdowns`): 全面 rewrite。新実装は `permission_decision` event を入力に `tool_use_id` join + tool 別集計を 1 関数で行う。`subagent_metrics.usage_invocation_intervals` 依存も削除
  - **line 1086-1125** (`build_dashboard_data` return dict): `permission_prompt_tool_breakdown` を additive に追加
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/hooks/hooks.json`
  - 新 `"PreToolUse"` セクション追加 (記録 hook 1 つ)
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/scripts/archive_usage.py`
  - **line 211-229** (`_TIER2_DISPATCH`): `"permission_decision": ("tool_name", "tool_use_id")` を追記
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/docs/transcript-format.md`
  - **line 205-214** 直後 / Hook 入力 schema セクション内に `PreToolUse` 固有フィールド表を追加 (`tool_name`, `tool_input`, `permission_decision`, `permission_decision_reason`, `tool_use_id`)
  - **line 297-304** 直後 (`Notification` セクション直後): 新 `permission_decision` event_type 説明
  - **line 346-357** (Tier 2 dispatch table): `permission_decision` 行を追加
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/docs/spec/usage-jsonl-events.md`
  - イベント代表例セクション末尾に `permission_decision` の jsonc 例追加
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/docs/spec/dashboard-api.md`
  - **line 39-42** (全期間 scope) に `permission_prompt_tool_breakdown` を追記
  - **line 461-563** (`permission_prompt_*_breakdown` セクション): 意味再定義 + 新 Tool field 仕様。Issue #61 の旧 30s window 説明を「Issue #75 で削除」明記 (歴史保持)
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/dashboard/template/shell.html`
  - **line 325-353** (skill panel) / **line 355-383** (subagent panel) help-pop 文を意味再定義に合わせて差し替え (新 wording §7)
  - line 383 直後 (subagent panel `</div>` の直後、`<!-- (5) A3: Compact density -->` の直前) に **新 Tool panel** `<div class="panel" id="quality-perm-tool-panel">` を挿入
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/dashboard/template/scripts/20_load_and_render.js`
  - **line 273-275** に `renderPermissionToolBreakdown(data.permission_prompt_tool_breakdown);` を追加
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/dashboard/template/scripts/40_renderers_quality.js`
  - **line 165-222** (renderPermission 系 2 つ) の直後に `renderPermissionToolBreakdown` を追記。skill/subagent 側はロジック変更なし (data shape は不変)
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/dashboard/template/scripts/90_data_tooltip.js`
  - **line 140-155** (`perm-skill` / `perm-subagent` data-tip) の直後に `perm-tool` 用 data-tip を追加
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/dashboard/template/styles/50_quality.css`
  - **line 77-99** (`.perm-table` 系) の直後に `.perm-tool-table` (子テーブル / top_inputs リスト) 用 rule を additive 追記。配色は `--peri` (panel-head c-peri 用、未使用色) を新規 dot に充てる
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_dashboard_template_split.py`
  - **line 28** `EXPECTED_TEMPLATE_SHA256` を bump

### 新規作成
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/hooks/record_permission.py` — `PreToolUse` 受信 → filter → enrich (whitelist + 8KB cap) → `append_event` の record-style hook
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_record_permission.py` — record_permission.py の RED → GREEN
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_permission_decision.py` — server-side aggregator (新 `aggregate_permission_breakdowns` の rewrite + `build_dashboard_data` 統合)
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_permission_decision_template.py` — DOM / CSS / JS renderer / help-pop verbatim
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/docs/plans/75-permission-decision-pretooluse.md` — 本 plan 本体

### 触らない (波及なし確定)
- `subagent_metrics.py` — `tool_use_id` 直結で済むため変更不要
- `reports/summary.py` / `reports/export_html.py` — terminal report への Tool breakdown 追加は **本 PR scope 外** とし、`permission_prompt_count` (Notification 経由) は据え置き。`reports/summary.py` の "Permission prompts:" 出力は Notification 件数を見続ける (= 旧来の "raw 件数" として意味維持、Quality dashboard とは別軸)。新 Tool breakdown を report に出すかは別 issue で議論
- `aggregate_session_stats` (`dashboard/server.py:993` 周辺の `permission_prompt_count`) は **不変** (= Notification 経由の生件数 KPI として継続、KPI tile `kpi-perm` も不変)。新 attribution は Quality タブ panel のみで動く

## 5. 6-phase 実装計画 (TDD ordered RED → GREEN)

### Phase 1 — `hooks/record_permission.py` (PreToolUse hook 新設)

**RED**: `tests/test_record_permission.py` を作成。実装前なので import / 関数呼び出しが全て失敗する。

- `TestRecordPermissionDecisionEvent::test_ask_records_permission_decision_event` — `hook_event_name="PreToolUse"`, `permission_decision="ask"`, `tool_name="Bash"`, `tool_input={"command":"git push"}`, `tool_use_id="toolu_X"` の stdin → usage.jsonl に `event_type=permission_decision` の行が 1 件追記される
- `TestRecordPermissionDecisionEvent::test_deny_records_permission_decision_event` — `permission_decision="deny"` も同様に記録
- `TestRecordPermissionDecisionEvent::test_allow_does_not_record` — `permission_decision="allow"` は何も書かない (= 0 件 append)
- `TestRecordPermissionDecisionEvent::test_missing_permission_decision_does_not_record` — field 欠損 → 0 件 (defensive: silent skip)
- `TestRecordPermissionDecisionEvent::test_non_pre_tool_use_event_ignored` — `hook_event_name="PostToolUse"` → 0 件
- `TestToolInputWhitelistBash::test_bash_keeps_command_only` — `tool_input={"command":"git push","timeout":5000}` → 保存 event の `tool_input == {"command":"git push"}` (timeout は drop)
- `TestToolInputWhitelistEdit::test_edit_keeps_file_path_only` — `tool_input={"file_path":"/x.py","old_string":"abc","new_string":"def"}` → `{"file_path":"/x.py"}`
- `TestToolInputWhitelistMultiEdit::test_multiedit_keeps_file_path_only` — 同上
- `TestToolInputWhitelistRead::test_read_keeps_file_path_only`
- `TestToolInputWhitelistWebFetch::test_webfetch_keeps_url_only`
- `TestToolInputWhitelistSkill::test_skill_keeps_skill_only` — `args` は捨てる (skill 識別だけ残す)
- `TestToolInputWhitelistTask::test_task_keeps_subagent_type_and_description` — `prompt` (= 入力プロンプト全文) は捨てる
- `TestToolInputWhitelistMcpEvaluate::test_mcp_evaluate_script_drops_all_input` — `tool_name="mcp__chrome-devtools__evaluate_script"` → `tool_input` キー自体が event に存在しない
- `TestToolInputWhitelistUnknownTool::test_unknown_tool_drops_all_input` — `tool_name="MysteryTool"` → `tool_input` キー無し、`tool_name` のみ
- `TestToolInputCap::test_8kb_truncation_byte_level` — `command` を 10000 byte で渡す → 保存値は 8192 byte で truncate、`tool_input_truncated=True` フラグ追加
- `TestToolInputCap::test_truncation_does_not_break_utf8` — multibyte 文字 (日本語) 境界で 8192 byte 切る → decode 例外なし
- `TestToolInputCap::test_no_truncation_under_cap` — 100 byte の command は無傷、`tool_input_truncated` フラグ無し
- `TestEnrichment::test_event_has_session_and_project_and_timestamp` — `cwd`, `session_id` から共通 field
- `TestEnrichment::test_event_records_permission_decision_reason_when_present` — `permission_decision_reason="user denied"` を保存
- `TestEnrichment::test_event_records_tool_use_id_when_present`
- `TestEnrichment::test_event_records_permission_mode_when_present` — `permission_mode="default"` 等を保存 (PostToolUse 系と整合)
- `TestEnrichment::test_event_omits_optional_fields_when_absent` — `permission_decision_reason` / `tool_use_id` / `permission_mode` 不在時は event に key 自体を出さない (= dict get with default ではなく明示的 omit)

**GREEN**: `hooks/record_permission.py` を実装。

```python
# 概観 signature
_TOOL_INPUT_WHITELIST: dict[str, tuple[str, ...]] = {
    "Bash": ("command",),
    "Edit": ("file_path",),
    "MultiEdit": ("file_path",),
    "Write": ("file_path",),
    "Read": ("file_path",),
    "NotebookEdit": ("file_path",),
    "WebFetch": ("url",),
    "WebSearch": ("query",),
    "Skill": ("skill",),
    "Task": ("subagent_type", "description"),
    "Agent": ("subagent_type", "description"),
    "mcp__chrome-devtools__navigate_page": ("url",),
    "mcp__chrome-devtools__take_screenshot": ("selector",),
    "mcp__plugin_figma_figma__use_figma": ("fileKey", "nodeId"),
    # mcp__plugin_figma_figma__get_* は startswith dispatch (下記)
}
_MCP_PREFIX_WHITELIST: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mcp__plugin_figma_figma__get_", ("fileKey", "nodeId")),
)
_TOOL_INPUT_BYTE_CAP = 8192

def _truncate_utf8(value: str, cap: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= cap:
        return value, False
    return encoded[:cap].decode("utf-8", errors="ignore"), True

def _filter_tool_input(tool_name: str, tool_input: dict) -> tuple[dict | None, bool]:
    fields = _TOOL_INPUT_WHITELIST.get(tool_name)
    if fields is None:
        for prefix, fs in _MCP_PREFIX_WHITELIST:
            if tool_name.startswith(prefix):
                fields = fs
                break
    if fields is None:
        return None, False
    out: dict = {}
    truncated = False
    for f in fields:
        v = tool_input.get(f)
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        if v is None:
            continue
        s = str(v)
        s, t = _truncate_utf8(s, _TOOL_INPUT_BYTE_CAP)
        out[f] = s
        truncated = truncated or t
    return (out if out else None), truncated

def _handle_pre_tool_use(data: dict) -> None:
    decision = data.get("permission_decision")
    if decision not in ("ask", "deny"):
        return
    tool_name = data.get("tool_name", "")
    tool_input_raw = data.get("tool_input") or {}
    filtered_input, truncated = _filter_tool_input(tool_name, tool_input_raw)
    event = {
        "event_type": "permission_decision",
        "tool_name": tool_name,
        "permission_decision": decision,
        "project": _project_from_cwd(data.get("cwd", "")),
        "session_id": data.get("session_id", ""),
        "timestamp": _now_iso(),
    }
    if filtered_input is not None:
        event["tool_input"] = filtered_input
    if truncated:
        event["tool_input_truncated"] = True
    if "permission_decision_reason" in data:
        event["permission_decision_reason"] = data["permission_decision_reason"]
    if "tool_use_id" in data:
        event["tool_use_id"] = data["tool_use_id"]
    if "permission_mode" in data:
        event["permission_mode"] = data["permission_mode"]
    append_event(DATA_FILE, event)
```

**順序根拠**: hook 自体は他モジュールに依存しない最下層。先に GREEN にして以降の phase が同じ event shape を test fixture として再利用できる。

### Phase 2 — `hooks/hooks.json` 登録 + archive tier 2 dispatch

**RED**:
- `tests/test_record_permission.py::TestHooksJsonRegistration::test_pre_tool_use_section_exists` — `hooks.json` を読み `hooks.PreToolUse` が list として存在
- `TestHooksJsonRegistration::test_pre_tool_use_invokes_record_permission` — 同セクションに `record_permission.py` の command line が含まれる
- `tests/test_archive_usage.py` (既存) に新 test class:
  - `TestPermissionDecisionDispatch::test_tier2_dispatch_includes_permission_decision` — `_TIER2_DISPATCH["permission_decision"] == ("tool_name", "tool_use_id")`
  - `TestPermissionDecisionDispatch::test_dedup_uses_tier2_secondary_key` — 同 (session_id, timestamp, tool_name, tool_use_id) の event 2 件を hot tier に置いて archive → 重複登録されない
  - `TestPermissionDecisionDispatch::test_distinct_tool_use_ids_archive_separately` — tool_use_id 違いの 2 件は両方 archive される

**GREEN**:
- `hooks/hooks.json` に `"PreToolUse"` キー新設、record_permission.py の command を追加
- `scripts/archive_usage.py:212` の `_TIER2_DISPATCH` に `"permission_decision": ("tool_name", "tool_use_id")` を 1 行追加

**順序根拠**: archive job が新 event を読む準備を Phase 3 の aggregator 実装より先にしておく (= 旧 archive 月を再 archive しても新 event がきちんと dedup される invariant を Phase 2 で pin)。

### Phase 3 — server-side aggregator rewrite (`aggregate_permission_breakdowns`)

**RED**: `tests/test_permission_decision.py` を新規作成。

- `TestAttributeSkill::test_skill_ask_joins_to_skill_tool_via_tool_use_id` — fixture: `skill_tool(tool_use_id=T1, skill=S)` + `permission_decision(tool_name=Skill, tool_use_id=T1, decision=ask)` → `result["skill"] == [{"skill": S, "prompt_count": 1, "invocation_count": 1, "permission_rate": 1.0}]`
- `TestAttributeSkill::test_skill_deny_also_counted` — decision=deny も `prompt_count` に入る (= 全 ask+deny の合計)
- `TestAttributeSkill::test_unmatched_tool_use_id_dropped` — orphan `permission_decision(tool_name=Skill, tool_use_id=GHOST)` は集計に出ない
- `TestAttributeSkill::test_invocation_count_uses_session_skill_tool_count` — drift guard: `invocation_count == #skill_tool events for that skill in same session`
- `TestAttributeSubagent::test_task_ask_joins_to_subagent_start_via_tool_use_id` — fixture: `subagent_start(tool_use_id=T2, subagent_type=Explore)` + `permission_decision(tool_name=Task, tool_use_id=T2, decision=ask)` → subagent 行に Explore が出る
- `TestAttributeSubagent::test_agent_tool_name_also_works` — `tool_name="Agent"` も Task と同じく subagent 行に行く
- `TestAttributeSubagent::test_invocation_count_matches_aggregate_subagent_metrics` — drift guard: `invocation_count == aggregate_subagent_metrics(events)[name]["count"]` (Issue #61 と同じ契約)
- `TestAttributeNonRouting::test_bash_ask_does_not_appear_in_skill_or_subagent_rows` — `tool_name="Bash"` の ask は skill / subagent 行に **出ない** (= (β) は Tool 行のみ)
- `TestAttributeNonRouting::test_bash_ask_appears_in_tool_breakdown` — 同 fixture で `result["tool"]` に `Bash` 行
- `TestNoBackwardWindow::test_old_style_notification_event_is_ignored` — fixture: 旧 `notification(notification_type=permission)` のみ + `skill_tool` を 1 件 → `result == {"skill":[], "subagent":[], "tool":[]}` (新パイプラインは Notification を見ない)
- `TestNoBackwardWindow::test_orphan_permission_decision_with_no_tool_use_id_drops_from_skill_subagent` — `tool_use_id` 不在の Skill ask は skill 行に出ない (= 旧 30s window で拾われていたケースを意図的に drop)
- `TestNoBackwardWindow::test_orphan_permission_decision_still_appears_in_tool_breakdown` — 同 fixture でも Tool 行 (Skill row) には出る
- `TestToolBreakdown::test_tool_rows_grouped_by_tool_name` — fixture 多 tool → tool_name でグルーピング、decision_count 降順
- `TestToolBreakdown::test_top_inputs_top_3_by_count` — Bash ask × 5 種 (count: 5,4,3,2,1) → `top_inputs` は 3 件、count 降順
- `TestToolBreakdown::test_top_inputs_summary_uses_whitelist_fields_joined` — Edit ask の `tool_input={"file_path":"/x.py"}` → `summary == "/x.py"`
- `TestToolBreakdown::test_top_inputs_summary_truncated_to_120_chars` — 200 char `command` → summary は 120 chars
- `TestToolBreakdown::test_unknown_tool_has_empty_top_inputs` — `tool_input` 欠損 / 未 whitelist tool → `top_inputs == []`
- `TestToolBreakdown::test_no_input_sentinel_when_all_inputs_empty` — 全 ask が `tool_input` なし → 1 行に集約 `summary="<no input>"` ではなく `top_inputs == []` (空配列で表現)
- `TestToolBreakdown::test_ask_count_and_deny_count_split` — Bash ask × 5 / deny × 2 → `ask_count=5, deny_count=2, decision_count=7`
- `TestToolBreakdown::test_top_n_10_cap` — 11 種類の tool → 10 件で cap
- `TestToolBreakdown::test_sort_decision_count_desc_then_tool_name_asc`
- `TestEmpty::test_no_permission_decision_events_returns_empty_lists` — fixture 旧 events のみ → `{"skill":[], "subagent":[], "tool":[]}`
- `TestBuildDashboardDataIntegration::test_permission_prompt_tool_breakdown_field_present`
- `TestBuildDashboardDataIntegration::test_existing_skill_subagent_field_shape_unchanged` — additive contract guard: 既存 `permission_prompt_skill_breakdown` / `permission_prompt_subagent_breakdown` の dict キーセットは Issue #61 と完全同じ
- `TestBuildDashboardDataIntegration::test_old_archive_data_does_not_attribute_to_skill_or_subagent` — fixture 旧 Notification + skill_tool のみ → `permission_prompt_skill_breakdown == []` (= 旧データ集計 0、Issue 確定 spec の breaking change pin)
- `TestRemovedHelpersAreGone::test_PERMISSION_LINK_WINDOW_SECONDS_removed` — `assert not hasattr(server, "PERMISSION_LINK_WINDOW_SECONDS")` (= 30s heuristic 撤去 invariant)
- `TestRemovedHelpersAreGone::test__skill_event_interval_removed` — 同上 (= 旧 helper 撤去 invariant)

**GREEN**:
- `dashboard/server.py` line 467-652 を rewrite
  - `PERMISSION_LINK_WINDOW_SECONDS` / `_skill_event_interval` / 旧 `_attribute_permission` を削除
  - 新 `aggregate_permission_breakdowns(events, top_n=TOP_N)` を実装:
    1. session 単位で `skill_tool` / `subagent_start` / `permission_decision` を bucket
    2. skill 行: `permission_decision(tool_name=="Skill")` を `tool_use_id` で `skill_tool` に join → `skill_tool.skill` を name に。orphan は drop
    3. subagent 行: `permission_decision(tool_name in {"Task","Agent"})` を `tool_use_id` で `subagent_start` に join → `subagent_start.subagent_type` を name に。orphan drop
    4. tool 行: 全 `permission_decision` event を `tool_name` でグルーピング、`ask_count` / `deny_count` を計算、`tool_input` の summary を Counter で top 3 抽出
  - 戻り値 dict に `"tool"` key を additive 追加
- `build_dashboard_data` line 1101-1103 周辺に `"permission_prompt_tool_breakdown": permission_breakdowns["tool"]` を additive 追加

**順序根拠**: hook 側の event shape (Phase 1) が固まったので fixture を構築できる。aggregator GREEN まで進めば API contract が確定し template 側 (Phase 4-) は安心して DOM を作れる。

### Phase 4 — Template DOM (shell.html) + SHA256 bump

**RED**: `tests/test_permission_decision_template.py` を新規作成。

- `TestPermToolPanelDOM::test_panel_exists_with_id` — `id="quality-perm-tool-panel"` が assembled template に存在
- `TestPermToolPanelDOM::test_panel_inside_quality_section` — `<section data-page="quality">` 内
- `TestPermToolPanelDOM::test_panel_after_subagent_perm_panel_before_compact_panel` — 文字列 index 比較で `quality-perm-subagent-panel` < `quality-perm-tool-panel` < `quality-compact-panel`
- `TestPermToolPanelDOM::test_panel_head_uses_c_peri` — head に `c-peri` class (新色 token)
- `TestPermToolPanelDOM::test_panel_title_text` — `<span class="ttl">` に `承認待ち × ツール (top 10)` 含む
- `TestPermToolPanelDOM::test_help_pop_id_is_hp_perm_tool` — `id="hp-perm-tool"`
- `TestPermToolPanelDOM::test_help_pop_body_verbatim` — help-pop body に **集計ロジックの正本** verbatim 含む (4-axis verification、§7 の文案):
  - `PreToolUse` (= 入力 hook 名)
  - `permission_decision` (= filter キーワード)
  - `ask` `deny` (= filter 値)
  - `tool_name` (= グルーピング軸)
  - `tool_use_id` (skill/subagent help-pop 側、§7 で別 verbatim)
- `TestPermSkillPanelHelpPopRewrite::test_skill_help_pop_no_more_30s_mention` — `<span id="hp-perm-skill">` の body に `30 秒` / `30s` 文字列が **含まれない** (= heuristic 削除 verbatim pin)
- `TestPermSkillPanelHelpPopRewrite::test_skill_help_pop_mentions_tool_use_id_join`
- `TestPermSubagentPanelHelpPopRewrite::test_subagent_help_pop_no_more_30s_mention`
- `TestPermSubagentPanelHelpPopRewrite::test_subagent_help_pop_mentions_tool_use_id_join`
- `TestPermToolPanelDOM::test_panel_body_has_tool_table` — `<table class="perm-tool-table" id="quality-perm-tool">` 含む
- `TestPermToolPanelDOM::test_table_has_columns_tool_decision_count_top_inputs` — thead に `Tool` `ask` `deny` `Top inputs` 列ヘッダ
- `TestSha256Bump::test_expected_sha256_updated` — Phase 4 開始時に shell.html 編集すると `EXPECTED_TEMPLATE_SHA256` が即 fail。新 hash を pin

**GREEN**:
- `shell.html` line 325-353 / 355-383 の help-pop body を §7 文案で差し替え (旧 30 秒文言を削除、`tool_use_id` join 文言を入れる)
- line 383 直後に新 panel を追加 (約 25 行)
- `tests/test_dashboard_template_split.py:28` の `EXPECTED_TEMPLATE_SHA256` を新 hash に bump (Phase 4 GREEN commit に同梱)

**順序根拠**: Phase 3 で API contract (= field 名 / shape) 確定後に DOM 側の `id` / `data-*` を pin。renderer (Phase 5) が読む DOM hook を Phase 4 で先に作る。

### Phase 5 — Renderer JS + CSS additive

**RED**: `tests/test_permission_decision_template.py` に追加。

- `TestPermToolRenderer::test_render_function_defined` — `40_renderers_quality.js` 文字列に `function renderPermissionToolBreakdown(` 含む
- `TestPermToolRenderer::test_load_and_render_calls_renderer` — `20_load_and_render.js` line 273 周辺に `renderPermissionToolBreakdown(data.permission_prompt_tool_breakdown);` verbatim 含む
- `TestPermToolRenderer::test_quality_page_scoped_early_out` — renderer 関数に `dataset.activePage !== 'quality'` early-out
- `TestPermToolRenderer::test_renderer_handles_empty_array_with_no_data_row` — Node round-trip: `renderPermissionToolBreakdown([])` → tbody に `no data` 行
- `TestPermToolRenderer::test_renderer_renders_tool_name_ask_deny_columns` — Node round-trip で 1 行 fixture → tool_name / ask_count / deny_count が DOM に出る
- `TestPermToolRenderer::test_top_inputs_rendered_as_subrows` — top_inputs が 3 件あれば 3 サブ行が `summary` 文字列で表示
- `TestPermToolRenderer::test_top_inputs_summary_html_escaped` — `<script>` を含む summary が escape される (XSS guard、`esc()` helper 使用 pin)
- `TestPermToolRenderer::test_data_tip_attributes_set` — `data-tip="perm-tool" data-name=... data-ask=... data-deny=...` が出る
- `TestDataTooltip::test_perm_tool_kind_handled` — `90_data_tooltip.js` 文字列に `kind === 'perm-tool'` 分岐含む
- `TestPermToolCss::test_perm_tool_table_class_defined` — `50_quality.css` に `.perm-tool-table {` 含む
- `TestPermToolCss::test_panel_head_c_peri_dot_color` — `.panel-head.c-peri .ttl .dot { background: var(--peri); }` 含む (token 確認、`00_base.css` で既定義)
- `TestPermToolCss::test_data_tip_perm_tool_border_color` — `.data-tip[data-kind="perm-tool"]` rule 存在
- `TestPermToolCss::test_main_js_files_unchanged` — `dashboard.server._MAIN_JS_FILES` を import → 既存 13 entries と完全一致 (= 新 file 追加禁止 invariant)
- `TestPermToolCss::test_css_files_unchanged` — `_CSS_FILES` 同上
- `TestRemovedHooks::test_no_30_second_constant_in_renderer_or_help_pop` — assembled `_HTML_TEMPLATE` 全文を grep で `30 秒` `30s` `PERMISSION_LINK_WINDOW` が出ない invariant

**GREEN**:
- `40_renderers_quality.js` line 222 直後に `renderPermissionToolBreakdown(items)` を追加 (skill / subagent renderer と同じ shape: page-scoped early-out, list 安全化, tbody 構築, sub label 設定)
- `20_load_and_render.js` line 275 直後に renderer 呼び出し追加
- `90_data_tooltip.js` line 155 直後に `perm-tool` kind の data-tip handling 追加
- `50_quality.css` line 99 直後に `.perm-tool-table` / `.perm-tool-table .top-inputs` / `.data-tip[data-kind="perm-tool"]` rule を additive 追記。`.panel-head.c-peri .ttl .dot { background: var(--peri); }` 1 行も `c-mint` / `c-coral` 行の隣に追加 (50_quality.css line 2 周辺、token は `00_base.css` 既定義)
- `EXPECTED_TEMPLATE_SHA256` を Phase 5 GREEN 時点で再 bump (= Phase 4 と同じ pattern、phase ごと bump 運用、bisect note は R5 参照)

**順序根拠**: Phase 4 で DOM hook (id, data-* 属性, class 名) が確定済。Phase 5 は renderer + CSS が DOM hook を読みに行く順序で dead test / dead CSS を回避。

### Phase 6 — docs (transcript-format.md / dashboard-api.md / usage-jsonl-events.md / memory file)

非 TDD phase (docs テストは sha 系以外存在しないため manual review checklist):

- `docs/transcript-format.md`:
  - line 205-214 (`PostToolUse` 固有フィールド表) の直後に `### PreToolUse 固有フィールド` 表を追加 (`tool_name`, `tool_input`, `permission_decision`, `permission_decision_reason`, `tool_use_id`, `permission_mode`)
  - line 297-304 (`Notification` セクション直後) に `### permission_decision (Issue #75 / v0.8.1〜)` を追加: filter rule (`ask` / `deny`)、whitelist 方針、8 KB cap、未知 tool fallback、attribution 用途
  - line 346-357 (Tier 2 dispatch 表) に `permission_decision | tool_name, tool_use_id` 行を追加
- `docs/spec/usage-jsonl-events.md`:
  - イベント代表例セクション末尾に `permission_decision` の jsonc 例を追加 (Bash ask / Skill ask の 2 例)
- `docs/spec/dashboard-api.md`:
  - line 39-42 (全期間 scope) に `permission_prompt_tool_breakdown` を追加
  - line 461-563 の `permission_prompt_*_breakdown` セクションを Issue #75 spec に書き換え:
    - Issue #61 の execution-window + 30s heuristic 説明は **歴史保持枠** に追いやる (= 「Issue #75 までの実装」と注記、削除はせず)
    - 新 attribution: `tool_use_id` 直結 join、(α) only、(β) は Tool 行のみに記載
    - 新 field `permission_prompt_tool_breakdown` の shape / sort / top-N / `top_inputs` 構造を完全記載
    - archive 互換性節 (旧データの skill / subagent 行集計 0 件問題)
- `memory/permission-decision-attribution.md` を新規作成 (= 設計判断メモ、`MEMORY.md` に 1 行ポインタ追加)
- `MEMORY.md` (root) に「Issue #75: PreToolUse(permission_decision) hook 経由で tool_use_id 直結 attribution。30s window heuristic 撤去」1 行追加

### Phase 7 — PR 作成

- v0.8.1 release branch を未作成なら main から push
- `feature/75-permission-decision` を v0.8.1 から切る (CLAUDE.md branching workflow)
- PR は `--base v0.8.1` 指定。Test plan に visual smoke (chrome-devtools MCP で Quality タブの新 Tool panel screenshot) を含める
- Release notes (PR 本文 + `docs/release-notes.md` がない場合は PR 本体に直接) に **breaking change 文面**:
  > `permission_prompt_skill_breakdown` / `permission_prompt_subagent_breakdown` の集計ロジックが Issue #61 (時系列推測 + 30 秒 backward window) から Issue #75 (PreToolUse hook の `permission_decision` event を `tool_use_id` で直結) に変わりました。新ロジックは **Issue #75 以降に記録された session のみ** で動くため、`--include-archive` で過去月を含めても旧月の skill / subagent 行は 0 件のままになります。`permission_rate` の値が前 version より小さく見えるのは仕様です。

## 6. test pin (各 phase で書く具体テストケース)

### `tests/test_record_permission.py` (Phase 1-2)

- TestRecordPermissionDecisionEvent (5 cases): ask / deny / allow drop / missing decision / non-PreToolUse drop
- TestToolInputWhitelist (8 cases per tool): Bash / Edit / MultiEdit / Read / WebFetch / Skill / Task / mcp_evaluate_drop / unknown_drop
- TestToolInputCap (3 cases): 8KB truncation / utf8 boundary / no-truncation passthrough
- TestEnrichment (5 cases): common fields / decision_reason / tool_use_id / permission_mode / optional omit
- TestHooksJsonRegistration (2 cases): PreToolUse セクション存在 / record_permission.py 起動
- TestArchiveTier2Dispatch (3 cases) → `tests/test_archive_usage.py` 側: dispatch 登録 / dedup / 別 tool_use_id 別登録

### `tests/test_permission_decision.py` (Phase 3)

- TestAttributeSkill (4 cases): tool_use_id join / deny も count / orphan drop / invocation_count drift guard
- TestAttributeSubagent (3 cases): Task & Agent 両方 / invocation_count == aggregate_subagent_metrics drift guard
- TestAttributeNonRouting (2 cases): Bash は skill/subagent に出ない / Tool 行には出る
- TestNoBackwardWindow (3 cases): 旧 Notification 無視 / orphan tool_use_id drop / Tool 行には残る
- TestToolBreakdown (8 cases): グルーピング / top_inputs top 3 / summary 結合 / 120 char truncate / unknown empty top_inputs / 空 sentinel / ask & deny split / top-N 10 / sort
- TestEmpty (1 case)
- TestBuildDashboardDataIntegration (3 cases): field 存在 / 既存 shape 不変 / 旧データ無視
- TestRemovedHelpersAreGone (2 cases): `PERMISSION_LINK_WINDOW_SECONDS` / `_skill_event_interval` 撤去 invariant

### `tests/test_permission_decision_template.py` (Phase 4-5)

- TestPermToolPanelDOM (10 cases): panel id / quality section 内 / 順序 / c-peri / title / hp-perm-tool / help-pop verbatim / table / 列ヘッダ / 既存 sha bump
- TestPermSkillPanelHelpPopRewrite (2 cases): 30 秒文言除去 / tool_use_id join 言及
- TestPermSubagentPanelHelpPopRewrite (2 cases): 同上
- TestPermToolRenderer (8 cases): function defined / load_and_render 呼び出し / page scope / empty no-data / column render / top_inputs subrow / XSS escape / data-tip attrs
- TestDataTooltip (1 case): `perm-tool` kind 分岐
- TestPermToolCss (5 cases): perm-tool-table / c-peri dot / data-tip border / `_MAIN_JS_FILES` 不変 / `_CSS_FILES` 不変
- TestRemovedHooks (1 case): assembled template に 30 秒文言 / `PERMISSION_LINK_WINDOW` 不在

## 7. Wording (help-pop / panel header) 案

### 7.1 Skill panel 既存 help-pop 書き換え (`hp-perm-skill`)

**旧** (line 334):
> 承認待ち発生の **直前 30 秒以内 (or 実行中)** に発火していた `skill_tool` に帰属。1 prompt は 1 候補にのみ帰属 …

**新**:
> Skill 起動 (`tool_name="Skill"`) に対する `PreToolUse` の `permission_decision` (= `ask` / `deny`) を、同じ `tool_use_id` を持つ `skill_tool` event に **直接 join** して帰属。1 invocation で複数回 ask されると `permission_rate > 1.0` になる (上限なし)。`Issue #75` 以降の session のみ集計対象 (旧 `Notification(permission)` 経路は対象外)。settings.json の `permissions.allow` 整理ヒントに使う。

### 7.2 Subagent panel 既存 help-pop 書き換え (`hp-perm-sub`)

**新**:
> subagent 起動 (`tool_name in {Task, Agent}`) に対する `PreToolUse` の `permission_decision` (= `ask` / `deny`) を、同じ `tool_use_id` を持つ `subagent_start` event に **直接 join** して帰属。subagent の **内側で** 立つ Bash 等の ask は本表では数えず「ツール別 (top 10)」表に分離して計上。`Issue #75` 以降の session のみ集計対象。

### 7.3 新 Tool panel header + help-pop (`hp-perm-tool`)

**header**: `承認待ち × ツール (top 10)` (= ASCII / 日本語短文併用、dashboard-wording skill の方針)

**help-pop**:
> `PreToolUse` event で `permission_decision in {ask, deny}` だったものを **tool_name でグルーピング**。`Bash` / `Edit` / `WebFetch` / `mcp__*` 等の粒度で「何の許可ダイアログが多いか」を可視化。各 tool の `Top inputs` 列は `tool_input` の whitelist field (Bash → `command`, Edit → `file_path`, WebFetch → `url` 等) を要約した上位 3 件。`mcp__chrome-devtools__evaluate_script` 等の whitelist 外 tool は `tool_name` のみ表示し input は捨てる (security 配慮)。settings.json の `permissions.allow` への追加候補が一目で分かる。

### 7.4 4-axis verification table (load-bearing)

| 軸 | help-pop 文 verbatim | 実装 verbatim |
|----|---------------------|---------------|
| filter 条件 (Tool) | 「`PreToolUse` event で `permission_decision in {ask, deny}`」 | `record_permission.py:_handle_pre_tool_use` の `decision not in ("ask","deny")` filter |
| グルーピング軸 (Tool) | 「`tool_name` でグルーピング」 | `aggregate_permission_breakdowns` の Counter keyed by `tool_name` |
| filter 条件 (Skill) | 「`tool_name="Skill"` の `permission_decision`」 | `event["tool_name"] == "Skill"` |
| join 手段 (Skill) | 「同じ `tool_use_id` を持つ `skill_tool` event に直接 join」 | dict[tool_use_id] → skill_tool での O(1) lookup |
| filter 条件 (Subagent) | 「`tool_name in {Task, Agent}`」 | `event["tool_name"] in ("Task","Agent")` |
| 新旧切替 | 「`Issue #75` 以降の session のみ」 | 旧 Notification(permission) は集計に使わない |

## 8. CSS / JS renderer additive scope

**新ファイル禁止 / 既存 file 末尾追記のみ**:

| 既存 file | 追記内容 | 行数見込 |
|-----------|---------|---------|
| `dashboard/template/scripts/40_renderers_quality.js` | `renderPermissionToolBreakdown(items)` (skill / subagent renderer の構造を踏襲、tbody 内に親行 + top_inputs サブ行) | 40-50 行 |
| `dashboard/template/scripts/20_load_and_render.js` | line 275 の `renderPermissionSubagentBreakdown(...)` 直後に `renderPermissionToolBreakdown(data.permission_prompt_tool_breakdown);` 1 行 | 1 行 |
| `dashboard/template/scripts/90_data_tooltip.js` | line 155 直後に `if (kind === 'perm-tool')` 分岐 (tool name + ask + deny + decision_count を出す block) | 15-20 行 |
| `dashboard/template/styles/50_quality.css` | line 2 に `.panel-head.c-peri .ttl .dot { background: var(--peri); }` 1 行追加 (mint/coral と並列) + line 99 直後に `.perm-tool-table` rule + `.perm-tool-table .top-inputs` 子行 rule + `.data-tip[data-kind="perm-tool"]` rule | 30-40 行 |

`_MAIN_JS_FILES` / `_CSS_FILES` tuple は **無改変** (= concat 順 / SHA logic に影響なし、Phase 5 RED の `test_main_js_files_unchanged` で structural pin)。`shell.html` の line 数 ±1 は許容するが panel 順序 (= subagent 直後 / compact 直前) は維持。

## 9. Risks / Tradeoffs

### R1. Breaking change: `permission_rate` 値の縮減
- 既存 `permission_prompt_skill_breakdown` / `permission_prompt_subagent_breakdown` の数値が **前 version より小さくなる** (= (α) only への絞り込み)
- mitigation: PR 本文 / `docs/spec/dashboard-api.md` / Quality panel help-pop 3 箇所に「Issue #75 以降のみ」を verbatim 明記。Phase 6 docs check で 4 箇所同期確認
- 受容判断は Issue #75 確定 spec で済んでいる

### R2. Privacy: `tool_input.command` に secrets が混入する可能性
- `usage.jsonl` は local-only (外部送信なし) のため明示 redaction しない方針 (Issue 確定 spec)
- mitigation: 8 KB cap で大量漏洩は防ぐ。MCP `evaluate_script` 系は完全 dump 抑止 (whitelist 外)
- 将来 secrets redaction が必要になったら record_permission.py 内に正規表現 redaction layer を追加可 (= aggregator 側変更不要)
- README / SECURITY.md に「`tool_input` を含むため `usage.jsonl` を共有しない」注意書きを Phase 6 で 1 行追加 (本 plan scope 内、文案は実装時)

### R3. Event 数増加: `usage.jsonl` の体積膨張
- 計測必要。実機 `usage.jsonl` で `permission_decision` 推定発生頻度を Phase 1 GREEN 後に dry-run sampling
- worst case: 1 session あたり数十回 (= Bash ask 連発のヘビーユーザー)
- mitigation: archive job が hot tier 180 日 cap で自然に bound。8 KB cap も体積線形上限を作る
- TDD 中に sample 計測を Phase 3 開始前に実施 (Issue #75 確定 spec の "TDD 中に実施" 項)

### R4. Archive 互換性ボーダー: 旧月再 archive で event_type 不在
- 旧 archive 月は `permission_decision` event を持たない → 新 (α) 集計に貢献しない (= Issue 確定 spec)
- `_TIER2_DISPATCH` への新 entry は **新月 archive のみ** に効く。旧月再生成しても旧 schema のまま
- mitigation: Phase 2 RED で別 tool_use_id の dedup invariant を pin、旧月との互換性は Phase 6 docs で「旧月は集計対象外」明記
- 旧 `Notification(permission)` event は archive されたまま継続 (記録 path 不変、`record_session.py` 無改修)

### R5. SHA256 bump (template DOM 変更) と bisect note
- 必須。shell.html (panel 追加 + help-pop 文書き換え) で文字列変化 = sha 不一致
- 運用: Phase 4 GREEN / Phase 5 GREEN の各 commit で `EXPECTED_TEMPLATE_SHA256` を再計測 → bump (= 各 phase commit が自己完結 GREEN)
- bisect note: Phase 4 GREEN 単独 commit は「DOM はあるが renderer 未実装で空 panel」中間状態。`git bisect` で `Phase 4 commit` を pickup した将来の reader が「空 panel = bug」と誤認しないよう、(a) Phase 4-5 を squash merge で 1 commit に潰す、または (b) Phase 4 commit message に「intermediate: Phase 5 まで empty panel」明記、を Phase 4 開始時に決める

### R6. 新色 token `--peri` を `c-peri` に充てる重複リスク
- `00_base.css` で `--peri` は定義済か未確認 → Phase 4 RED 開始時に `grep -n 'var(--peri)\|--peri:' dashboard/template/styles/00_base.css` で実測。未定義なら `--rose` 同様に `00_base.css` に新 token 追加が必要 = `_CSS_FILES` 内 file 編集 1 件追加
- 既存 panel-head 色: `c-mint` / `c-coral` / `c-peach` を Quality タブで既消費。3 panel が既に色を埋めているため新 panel に第 4 色が必要
- mitigation: Phase 4 RED で token 存在確認 → 未存在なら `00_base.css:line N` 加筆を Phase 4 GREEN scope に込み込み。`--peri` も使用済なら `--rose` (Issue #106 で `00_base.css:22` 追加済) を再利用候補に切り替え (panel-head 系では `c-rose` も Issue #106 で導入済 → 重複回避のため別色を優先)

### R7. 新 panel と既存 KPI tile (`kpi-perm`) の意味乖離
- `kpi-perm` (= header KPI の "permission gate" 件数) は引き続き **`Notification(permission|permission_prompt)` の生件数** を見ている (= `aggregate_session_stats.permission_prompt_count`)
- 新 panel は `permission_decision` event の (α) 集計
- 同じ「permission」ラベルだが分母が違う → ユーザーが「KPI 値 ≠ 新 panel 合計」を bug と誤認する risk
- mitigation: 本 plan では `kpi-perm` を **不変** とする (= 旧来の Notification 件数を 1 way KPI として維持)。help-pop の wording だけ Phase 6 で「Notification(permission) の発生回数 (= 新 Tool panel の `decision_count` とは別軸)」に書き換える 1 行作業を加える。`20_load_and_render.js:66` の helpBody 文字列 1 行 edit

### R8. Tool 行の `top_inputs.summary` HTML escape 漏れ
- `command` には `<script>` / `'` / `"` が混入しうる → renderer での escape が load-bearing
- mitigation: Phase 5 RED `test_top_inputs_summary_html_escaped` で `<script>alert(1)</script>` 入りの fixture を投入し escape 後文字列を assert
- renderer は既存 `esc()` helper (`10_helpers.js`) を使い回す (= 新 escape 関数を作らない、慣習踏襲)

### R9. Whitelist 不在 MCP tool 急増による「`top_inputs: []` の盲点」
- 新 MCP server 追加 (e.g. `mcp__new__do_thing`) は whitelist に未登録 → `top_inputs` 空で「許可した内容が見えない」
- mitigation: docs (`docs/transcript-format.md` / `record_permission.py` 内 docstring) に「新 MCP に対応する場合 `_TOOL_INPUT_WHITELIST` に 1 行追記」1 行 issue checklist を入れる
- 将来 `mcp__chrome-devtools__*` / `mcp__plugin_figma_figma__*` 等のグループに対する prefix dispatch を `_MCP_PREFIX_WHITELIST` で実装済 → 新 server の追加コストは「prefix 1 行」で済む構造

### R10. 既存 `_PERMISSION_NOTIFICATION_TYPES` 定数の去就
- `aggregate_session_stats` (`dashboard/server.py:993` 周辺) が `notification_type in _PERMISSION_NOTIFICATION_TYPES` を見て `permission_prompt_count` を計算 → KPI tile `kpi-perm` の入力
- 本 plan では **不変** (= R7 と同方針)。新 attribution パイプラインは `_PERMISSION_NOTIFICATION_TYPES` に依存しない。両者は **意図的に並存** する
- 将来 KPI tile も新 attribution に切り替えるなら別 issue で議論 (本 plan では touch しない)

## 10. 残検討項目 (next iteration)

- **`reports/summary.py` / `reports/export_html.py` への波及**: 本 plan では touch せず据え置き。Tool breakdown を terminal report / static HTML report にも出すかは別 issue。terminal report の "Permission prompts:" 行は引き続き Notification 生件数 (= R7 と同方針)
- **`subagent_metrics.py` 波及**: 不要 (`tool_use_id` 直結で済む) を Phase 3 GREEN で確認、確認できなかった場合 plan-reviewer 経由で議論 (現状は不要と判定)
- **`kpi-perm` を新 attribution に切り替え**: R7 / R10 で defer。別 issue
- **secrets redaction layer**: R2 で「将来 record_permission.py 内に追加可」とした拡張点。実機 sampling で漏洩観測されたら次 issue
- **MCP tool whitelist の継続メンテ**: R9 で 1 行追記契約を docstring に明記する以上の自動化は本 plan scope 外
- **forward attribution / next-gated-invocation algorithm** (= 旧 `dashboard-api.md:543-559` で言及していた heuristic 改善案): Issue #75 で `tool_use_id` 直結に切り替えるため不要に。本 plan で deprecate 完了
- **archive 時の旧月補完**: 旧 Notification(permission) を `permission_decision` event に "after-the-fact lift" する rescan tool は本 plan scope 外。`scripts/rescan_transcripts.py` は transcripts (~/.claude/projects/) を見るが、PreToolUse の `tool_input` / `permission_decision` は transcripts に保存されないため構造的に不可能 (= deferred & impossible)
- **Active session の `tool_input` truncation 観測値**: 8 KB cap が実機で発火する頻度は実装後 sampling で確認、必要なら cap 値を別 issue で再調整

