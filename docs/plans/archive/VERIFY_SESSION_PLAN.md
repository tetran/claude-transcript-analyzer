# 実装計画: StopHookによるリアルタイムデータ収集異常検知

## Context

Issue #2, #3 でClaude Codeの仕様変更（ツール名変更・payloadフォーマット変更）によりデータ収集が
サイレントに失敗した。同様の変更が起きたとき、**同セッション内で確実に検知する**仕組みが必要。
自動修復は今回の対象外。検知してアラートに記録することのみを行う。

## アプローチ

毎ターン `Stop` hook が発火するタイミングで、トランスクリプト（真実のログ）と usage.jsonl を
session_id 単位で照合し、差分があれば `data/health_alerts.jsonl` に記録する。

```
Claude が1ターン応答し終わる
  ↓
Stop hook 発火
  ↓
hooks/verify_session.py 実行
  ↓
トランスクリプト（~/.claude/projects/.../<session_id>.jsonl）を読む
  ↓ _scan_transcript_file() を再利用
usage.jsonl の同じ session_id のイベントと照合
  ↓
差分なし → サイレント終了
差分あり → data/health_alerts.jsonl に記録
           {"timestamp":..., "session_id":..., "missing_count":N, "missing_types":[...]}
```

## 運用方法

| 状況 | 動作 |
|-----|------|
| 通常時 | Stop hookが自動実行、差分なし → 完全透明 |
| Issue #3 型（ツール名変更）が再発 | 同セッション内でアラート記録 |
| Issue #2 型（パース失敗）が再発 | 同セッション内でアラート記録 |
| 確認したいとき | `cat data/health_alerts.jsonl` またはダッシュボードで確認 |
| セットアップ | `./install.sh` を再実行 |

## 実装コンポーネント

### 1. `hooks/verify_session.py`（新規）

```
入力（stdin）:
  {"hook_event_name": "Stop", "session_id": "xxx", "cwd": "/path/to/project"}

処理:
  1. session_id と cwd を取得
  2. トランスクリプトパスを特定:
       ~/.claude/projects/<cwd-encoded>/<session_id>.jsonl
       ※ cwd-encoded = cwd.replace('/', '-').lstrip('-')
       ※ 実装時に docs/transcript-format.md で確認
  3. _scan_transcript_file() でイベントを抽出（rescan_transcripts.py から import）
  4. usage.jsonl から同じ session_id のイベントを読む
  5. 差分を計算（dedup key: event_type + subagent_type/skill + timestamp）
  6. 差分ありなら health_alerts.jsonl に追記してサイレント終了
  7. 差分なし or トランスクリプト不在 → サイレント終了

出力: なし（stderr への警告ログのみ）
```

### 2. `tests/test_verify_session.py`（新規）

TDD：テスト先行。主なテストケース:

```
TestHandleStop:
  - test_no_alert_when_transcript_and_usage_match
      （正常系: 差分なし → health_alerts が空のまま）
  - test_alert_when_subagent_missing_issue3_scenario
      （Issue #3 再現: transcript に subagent_start 5件、usage に 0件 → アラート記録）
  - test_alert_when_slash_command_missing_issue2_scenario
      （Issue #2 再現: transcript に user_slash_command 2件、usage に 0件 → アラート記録）
  - test_no_alert_when_transcript_not_found
      （トランスクリプトが存在しない場合 → サイレント終了）
  - test_no_duplicate_alert_on_repeated_calls
      （同セッションで2回呼ばれても重複アラートを出さない）
  - test_alert_contains_required_fields
      （アラートに timestamp, session_id, missing_count, missing_types が含まれる）

TestMain:
  - test_exits_cleanly_with_valid_input
  - test_exits_cleanly_with_invalid_json
  - test_exits_cleanly_when_session_id_missing
```

### 3. `install/merge_settings.py` + `install.sh`（変更）

`settings.json` の `hooks.Stop` に追加:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {"type": "command", "command": "python3 /abs/path/hooks/verify_session.py"}
        ]
      }
    ]
  }
}
```

既存の PostToolUse hooks と共存させる（merge_settings.py のべき等マージ処理を拡張）。

### 4. `dashboard/server.py`（変更）

`/api/data` レスポンスに health_alerts を追加:

```json
{
  "health_alerts": [
    {
      "timestamp": "2026-03-01T10:00:00+00:00",
      "session_id": "xxx",
      "missing_count": 5,
      "missing_types": ["subagent_start"]
    }
  ]
}
```

`build_dashboard_data()` に `load_health_alerts()` を追加。
既存テスト 12 件を壊さないよう注意。

## 重要ファイル

| ファイル | 用途 |
|--------|------|
| `scripts/rescan_transcripts.py:104` | `_scan_transcript_file()` を import して再利用 |
| `docs/transcript-format.md` | トランスクリプトパスの特定方法を確認 |
| `install/merge_settings.py` | Stop hook 追加のマージ処理を拡張 |
| `tests/test_rescan_transcripts.py` | テストパターン（sys.path.insert, tmp_path）を参考 |
| `tests/test_dashboard.py` | 既存 12 件テストを壊さないよう確認 |

## 実装順序（TDD）

1. `tests/test_verify_session.py` を先に書く（Issue #3, #2 の再現テストから）
2. `hooks/verify_session.py` を実装してテストをパスさせる
3. `install/merge_settings.py` を拡張して Stop hook のマージを対応
4. `tests/test_dashboard.py` に health_alerts テストを追加
5. `dashboard/server.py` に health_alerts を追加
6. `python3 -m pytest tests/` で全テストパスを確認
7. `./install.sh` を実行して動作確認

## 検証方法

1. `python3 -m pytest tests/test_verify_session.py` が全パス
2. `python3 -m pytest tests/` で既存 80 件を含む全テストがパス
3. `./install.sh` を実行後に Claude Code を再起動
4. 数ターン使用して `data/health_alerts.jsonl` が空 → 正常
5. （任意）`record_subagent.py` の `_SUBAGENT_TOOL_NAMES` から "Agent" を一時的に外し、
   数ターン使うと health_alerts にアラートが記録されることを確認
