# `usage.jsonl` のイベント形式

`hooks/record_*.py` 各種が `~/.claude/transcript-analyzer/usage.jsonl`
(プラグイン稼働時) または `data/usage.jsonl` (テスト時) に追記する
**append-only event log** の event schema 仕様。

生 transcript (`~/.claude/projects/`) のフォーマットと Hook 入力 JSON の
schema は `docs/transcript-format.md` を参照。
Archive 互換性のための schema 進化規約 (tier 1/2/3 fingerprint, secondary_key
dispatch table) も `docs/transcript-format.md` に集約。

## 共通

- 1 イベント = 1 行 JSON (JSONL)。
- 共通フィールド: `event_type` / `project` / `session_id` / `timestamp` (ISO 8601 UTC `+00:00`)。
- PostToolUse 系には `duration_ms` / `permission_mode` / `tool_use_id` / `success` が付加される。
- PostToolUseFailure 系には `success: false` と `error` / `is_interrupt` が付加される。

## イベント代表例

```jsonc
// Skill ツール呼び出し（PostToolUse(Skill)）
{"event_type": "skill_tool", "skill": "user-story-creation", "args": "6",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:00:00+00:00",
 "duration_ms": 1234, "permission_mode": "acceptEdits", "tool_use_id": "toolu_...", "success": true}

// Skill ツール失敗（PostToolUseFailure(Skill)）
{"event_type": "skill_tool", "skill": "user-story-creation", "args": "6",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:00:01+00:00",
 "success": false, "error": "...", "is_interrupt": false}

// ユーザーの slash コマンド（UserPromptExpansion / UserPromptSubmit）
// source は "expansion" (主経路) / "submit" (フォールバック) のいずれか。
// dedup 判定は source!="submit" のレコードに対してのみ働き、submit 連打は両方記録される。
{"event_type": "user_slash_command", "skill": "/insights", "args": "", "source": "expansion",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:05:00+00:00"}

// Subagent 起動（PostToolUse(Task|Agent) のみが count に入る正規観測点）
// `tool_use_id` は live hook / rescan 共通 schema。
{"event_type": "subagent_start", "subagent_type": "Explore",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:06:00+00:00",
 "duration_ms": 90000, "permission_mode": "default", "tool_use_id": "toolu_..."}

// Subagent ライフサイクル開始（SubagentStart 経由・補助観測。count に入らない）
{"event_type": "subagent_lifecycle_start", "subagent_type": "Explore",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:06:00+00:00"}

// Subagent 終了（SubagentStop）
// 実 hook payload に duration_ms / success は **存在しない** (Issue #100 / #93)。
// 集計時は (session_id, subagent_id) で min(timestamp) dedup される (同 hook 最大 4 重発火を観測)。
{"event_type": "subagent_stop", "subagent_type": "Explore", "subagent_id": "agent_...",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:07:30+00:00",
 "agent_transcript_path": "/Users/.../projects/.../agent_....jsonl"}

// セッション開始 / 終了
{"event_type": "session_start", "source": "startup", "model": "claude-opus-4-7",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:00:00+00:00"}
{"event_type": "session_end", "reason": "logout",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T11:00:00+00:00"}

// コンテキスト圧縮
{"event_type": "compact_start", "trigger": "auto",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:30:00+00:00"}
{"event_type": "compact_end", "trigger": "auto",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:30:05+00:00"}

// Notification（idle, 確認待ち等）
// notification_type は `permission` / `permission_prompt` / `idle` / `idle_prompt` 等が観測される。
// 集計側 (dashboard / summary) は `permission` と `permission_prompt` を同一視してカウント。
{"event_type": "notification", "notification_type": "idle",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:45:00+00:00"}

// InstructionsLoaded（CLAUDE.md / memory / skill 等のロード）
{"event_type": "instructions_loaded", "file_path": "/path/to/CLAUDE.md",
 "memory_type": "project", "load_reason": "session_start",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:00:01+00:00"}

// assistant message ごとの token + model 観測（Stop hook で main / per-subagent transcript から抽出）
// Issue #99 / v0.8.0〜。session 単位 cost (実測 token × 価格表) の入力源。
{"event_type": "assistant_usage",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:00:00+00:00",
 "model": "claude-sonnet-4-6",
 "input_tokens": 1234, "output_tokens": 567,
 "cache_read_tokens": 8900, "cache_creation_tokens": 0,
 "message_id": "msg_abc...",
 "service_tier": "standard",        // 任意。transcript の message.usage.service_tier。欠損時 null
 "inference_geo": "us-east",        // 任意。transcript の message.usage.inference_geo。欠損時 null
 "source": "main"}                  // "main" | "subagent" — どの transcript から拾ったか（集計軸）
```

### `subagent_stop` 注意

- **`subagent_type == ""` レコードが構造的に存在する**: SubagentStop hook は
  メインスレッド停止時にも誤発火することがあり、その場合 `subagent_type` が空。
  集計側 (`subagent_metrics._bucket_events`) で `if not name: continue` により
  構造的に除外している。背景・観察値・diagnostic 手順は
  `docs/reference/subagent-invocation-pairing.md` の "Known artifact" 節を参照。
- **`duration_ms` / `success` は記録しない**: 実 hook payload に存在しないため
  `hooks/record_subagent.py:_handle_subagent_stop` はこれらを書き出さない。
- **`agent_transcript_path`**: SubagentStop hook 入力に含まれる場合のみ
  capture (filter validation 用 evidence)。aggregator では filter / dedup key
  として使わない (capture only)。

### `assistant_usage` 注意 (Issue #99 / v0.8.0〜)

- **dedup key**: `(session_id, message_id)` の pair で **first wins**。rescan 二重実行 /
  hook 再発火 / main + subagent 経路で同 `message_id` を二重観測しても 1 件に
  集約する idempotent 保証。live hook (`hooks/record_assistant_usage.py`) と
  rescan (`scripts/rescan_transcripts.py`) の両経路で同 schema / 同 dedup key を
  使う。既存 `usage.jsonl` を line 単位で scan して set 化してから新規分のみ append する。
- **transcript ↔ event field の key 名マッピング** (intentional な改名):

  | transcript の `message.usage.*` | event field |
  |---|---|
  | `input_tokens` | `input_tokens` |
  | `output_tokens` | `output_tokens` |
  | `cache_read_input_tokens` | `cache_read_tokens` |
  | `cache_creation_input_tokens` | `cache_creation_tokens` |
  | `service_tier` | `service_tier` (passthrough) |
  | `inference_geo` | `inference_geo` (passthrough) |

  cache 系のみ `_input_tokens` サフィックスを `_tokens` に統一する
  (`docs/reference/cost-calculation-design.md` §6 と整合)。
- **`service_tier` / `inference_geo`**: transcript の `message.usage` から passthrough。
  値の正規化はしない (real-world data quirks をそのまま見せる方針)。観測欠損時は
  `null`。
- **`source` field**: `"main"` (メイン session transcript = hook 入力
  `transcript_path`) / `"subagent"` (`<session_dir>/subagents/agent-<agent_id>.jsonl`)
  の 2 値。後段集計で軸として使う (= subagent invocation 単位の cost 帰属)。
- **per-subagent transcript の対象絞り込み**: Issue #93 で確定した
  `subagent_type == ""` filter rule 適用後の **type 入り invocation のみ**
  処理する (空 type の orphan invocation は除外)。
- **drop 条件 (silent skip)**:
  - `message_id` 欠損 → drop。dedup key を作れないため
  - timestamp parse 失敗 / 空 → drop
  - naive datetime も UTC として扱わず drop (= 既存
    `subagent_metrics._week_start_iso` の safety belt 規律とは別。本 event は
    transcript の `+00:00` 付き ISO のみを正とする)
- **archive tier 2 dispatch**: `assistant_usage` event は
  `(session_id, message_id)` の secondary key を持つので、`scripts/archive_usage.py`
  の `_TIER2_DISPATCH` table に `assistant_usage` → `message_id` を additive で
  追記する (後続 archive 互換性確保)。

## 関連 reference

- `subagent_start` / `subagent_lifecycle_start` / `subagent_stop` の三本立て
  観測点が **なぜそうなっているか**、`(session, type)` バケットでの invocation
  同定アルゴリズム、failure / duration ペアリング、`frozenset(event_types)`
  フィルタの教訓は `docs/reference/subagent-invocation-pairing.md` を参照。
- 生 transcript ↔ usage.jsonl の照合 (Stop hook での verify_session) が依拠する
  Hook 入力 schema は `docs/transcript-format.md` を参照。
- `assistant_usage` の集計仕様 (4 token × per-1M-token rate / model 別集約 /
  Sonnet fallback) は `docs/reference/cost-calculation-design.md` §10 を参照。
