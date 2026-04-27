# Issue #34 実装計画 — ダッシュボード URL を systemMessage で即時通知

## 1. ゴール (Why)

ダッシュボードは hook 経由で fork-and-detach の **silent 起動** をしているため、
ユーザーから URL が直接見えない。`cat ~/.claude/transcript-analyzer/server.json`
での確認は手間でかつ「裏で動いている事実」自体に気付きにくい。

**目的**: hook output の `systemMessage` 経由で「気付ける」状態にする。
かつ会話を埋めない (noise を抑える)。

## 2. 採用方針 (一行で)

`hooks/launch_dashboard.py` が次のいずれかを満たす時のみ stdout に
`{"systemMessage": "📊 Dashboard: <url>"}` を 1 行出力する。それ以外は silent。

| 条件 | 出力 | 理由 |
|------|------|------|
| サーバー新規 spawn 成功 | ✅ | spawn の事実 = ユーザーへの通知価値が最大 |
| 既起動 + `SessionStart` hook | ✅ | セッション開始時の親切な再表示 (resume / 別セッション開始時) |
| 既起動 + その他 hook (UserPromptExpansion / UserPromptSubmit / PostToolUse) | ❌ silent | 毎ターン発火するため出すと会話を埋める |
| 例外発生 / stdin parse 失敗 / spawn 直後 server.json race | ❌ silent | 既存「silent fail」原則を維持。次回 hook で復活 |

## 3. 公式仕様の根拠

- 公式 hooks docs (`https://code.claude.com/docs/en/hooks.md`) で確認済:
  - `systemMessage` は **all hooks** で利用可、ユーザー UI に warning-style で表示される
  - `additionalContext` は model 専用で UI 非表示 → 本 issue の用途外
- `hook_event_name` は Claude Code の hook input JSON に含まれる field
  (SessionStart / PostToolUse / UserPromptSubmit / UserPromptExpansion 全てで存在)

## 4. 既存の不変条件との整合

| 不変条件 | 影響 | 対応 |
|----------|------|------|
| 既起動検出経路 < 100ms | ✅ 維持 | stdin の 1 行 read + json.loads + json.dumps は < 数 ms |
| 例外時 silent exit 0 | ✅ 維持 | `try` の中で systemMessage を組み立て、例外時は出力しない |
| 子プロセスの stdin/stdout/stderr DEVNULL | ✅ 維持 | 子は引き続き DEVNULL。**親 launcher** の stdout のみ用途追加 |
| 「silent exit 0」原則 | ⚠️ 緩和 | 「成功経路で hook output JSON を 1 行書いてもよい」と緩和。例外時 / 該当条件外は依然 silent |

## 5. 実装ステップ (TDD)

### 5.1 ブランチ運用 (plan-reviewer Proposal 4 反映)

- **新ブランチ**: `feature/34-systemmessage-dashboard-url` を `main` から切る
- 現在の `feature/33-multi-os-python-resolution` ブランチには Issue #33 関連の
  uncommitted 変更が残っているため、それは PR #35 に commit/push する別作業
- **本 PR は `hooks/hooks.json` を一切変更しない** ことを §7 (DoD) に AC として
  書き込む。これで PR #35 (hooks.json の python alias 解決を全 hook で書き換え) と
  Issue #34 (launch_dashboard.py のみ) が構造的に直交し merge 衝突が発生しない
- → 上記 AC が保証されるため **main から先行で切ってよい**。マージ順序待ちは
  生産性ロスなので回避。**plan-reviewer 後にユーザーには二択提示で確認**:
  「main から先行で進める (推奨) / PR #35 マージ待ち」

### 5.2 テスト (Red 先行)

`tests/test_launch_dashboard.py` に新クラス追加。

#### `TestSystemMessageOutput` (14 ケース)

- `test_spawn_session_start_emits_system_message`:
  spawn 経路 + `hook_event_name=SessionStart` → stdout に
  `{"systemMessage": "📊 Dashboard: <url>"}` 1 行。`url` は spawn 後の server.json と一致
- `test_spawn_user_prompt_expansion_emits_system_message` (Q3 反映 / 新規追加):
  spawn 経路 + `hook_event_name=UserPromptExpansion` → 出力あり。idle 復活直後の
  最も日常的な経路でユーザー価値中心
- `test_spawn_user_prompt_submit_emits_system_message`:
  spawn 経路 + `hook_event_name=UserPromptSubmit` → 出力あり (spawn したから)
- `test_spawn_post_tool_use_emits_system_message`:
  spawn 経路 + `hook_event_name=PostToolUse` → 出力あり (spawn したから)
- `test_alive_session_start_emits_system_message`:
  既起動 + `hook_event_name=SessionStart` → 出力あり (再表示ポリシー)
- `test_alive_user_prompt_expansion_silent`:
  既起動 + `hook_event_name=UserPromptExpansion` → stdout 空
- `test_alive_user_prompt_submit_silent`:
  既起動 + `hook_event_name=UserPromptSubmit` → stdout 空
- `test_alive_post_tool_use_silent`:
  既起動 + `hook_event_name=PostToolUse` → stdout 空
- `test_empty_stdin_silent`:
  stdin 空文字列 → silent fallback (stdout 空)
- `test_invalid_json_stdin_silent`:
  stdin 非 JSON → silent fallback
- `test_missing_hook_event_name_silent`:
  `hook_event_name` 欠落 JSON → silent fallback (event 不明のため出力しない)
- `test_unknown_hook_event_name_silent` (Proposal 3 反映):
  `hook_event_name="Bogus"` のような未知値 → silent fallback (set membership 判定)
- `test_spawn_race_server_json_absent_silent` (Proposal 1 反映):
  spawn 後 poll の上限まで server.json 出ない → stdout 空 (次回 hook で復活)。
  `_spawn_server()` が `Optional[Popen]` を返す形にした上で、Popen 成功 + json なし
  パターンを mock で構築
- `test_spawn_oserror_no_system_message_and_no_poll` (Proposal 1 反映):
  Popen が OSError → `_spawn_server()` が `None` 返却 → poll を呼ばずに silent。
  `_wait_for_server_json_url` を mock しておき呼ばれないことを assert
- `test_spawn_reads_only_self_pid_server_json` (Proposal 1 反映 / 新規):
  spawn 直前に他人 (現プロセス) の有効 server.json が残っているケースで、
  spawn 後 poll が `info.pid == proc.pid` 条件で stale json を採用しないこと
- `test_unexpected_exception_no_system_message`:
  内部で例外 → systemMessage 出力なし、exit 0 維持

#### `TestSystemMessageStructure` (Proposal 5 反映 / 新規クラス)

`capsys.readouterr()` の (out, err) に対する構造制約を pin。
出力ありケース 4 件 + silent ケース 1 件 を共有 fixture or helper で:

- 出力ありケースは: `out.count("\n") == 1` and
  `json.loads(out.strip())` が dict and 値の `systemMessage` が
  `"📊 Dashboard: http"` で始まる文字列 and `err == ""`
- silent ケースは: `out == ""` and `err == ""`
- 既存テストへも `assert err == ""` を波及させ、stderr に何も出ない不変条件を維持

#### `TestPerformance` への追加

- `test_alive_path_under_100ms_with_session_start_event`:
  既起動 + SessionStart 経路 (= systemMessage 出力経路) でも < 100ms 維持
- 既存 `test_alive_path_under_100ms` は `hook_event_name` が無い stdin で実行 →
  silent path として budget 維持を pin
- パラメトリック化: 4 hook 名すべてで budget pin

#### `TestSpawnPathBudget` (Proposal 2 反映 / 新規)

- `test_spawn_wait_budget_under_300ms`:
  spawn 経路で server.json を書かない子を mock し、`main()` 全体が
  `SPAWN_WAIT_TIMEOUT_SECONDS + 50ms slack = 300ms` 以内に終わることを実測
- 定数 `SPAWN_WAIT_TIMEOUT_SECONDS = 0.25` / `SPAWN_WAIT_INTERVAL_SECONDS = 0.05` を
  module 定数として export し、テストから参照する形にすることで budget の constraint を
  test に封じ込める

#### 既存テストの確認

- 既存 `TestMainSpawnDecision` / `TestSpawnArguments` 等は `mod.main()` の戻り値だけ
  見るので互換。stdin が空のとき silent path → 影響なし
- ただし Proposal 5 の延長として、既存テストに `assert err == ""` を波及させる
  (autouse fixture or helper) — stderr 汚染への regression をすべての経路で pin

### 5.3 実装 (Green)

**`hooks/launch_dashboard.py`** 変更点:

1. **set membership で hook_event_name を判定** (Proposal 3):
   ```python
   _EXPECTED_HOOK_EVENTS = frozenset({
       "SessionStart", "UserPromptExpansion", "UserPromptSubmit", "PostToolUse",
   })

   def _read_hook_event_name() -> Optional[str]:
       """sys.stdin から hook input JSON を読み hook_event_name を取得。失敗時 None。
       未知の値は None 返却 (silent fallback) で表記揺れに強くする。"""
       try:
           raw = sys.stdin.read()
       except Exception:
           return None
       if not raw.strip():
           return None
       try:
           data = json.loads(raw)
       except Exception:
           return None
       if not isinstance(data, dict):
           return None
       name = data.get("hook_event_name")
       if not isinstance(name, str):
           return None
       name = name.strip()
       return name if name in _EXPECTED_HOOK_EVENTS else None
   ```

   - debug hook (opt-in / Proposal 3): `os.environ.get("DASHBOARD_DEBUG_HOOK_EVENT")` が
     truthy のとき `~/.claude/transcript-analyzer/hook_event_debug.jsonl` に append。
     **本番経路には副作用ゼロ** (env 未設定時は完全 no-op)。実機で 1 セッションだけ
     env を立てて値採取し、不要になれば次の PR で除去 (または env 永続化)。

2. **systemMessage emitter 追加**:
   ```python
   def _emit_dashboard_message(url: str) -> None:
       """`{"systemMessage": "📊 Dashboard: <url>"}` を stdout に 1 行出力。"""
       try:
           sys.stdout.write(json.dumps({"systemMessage": f"📊 Dashboard: {url}"}) + "\n")
           sys.stdout.flush()
       except Exception:
           pass  # silent fail (writer 失敗で Claude Code をブロックしない)
   ```

3. **`_spawn_server()` を `Optional[subprocess.Popen]` 返却に変更** (Proposal 1):
   ```python
   def _spawn_server() -> Optional[subprocess.Popen]:
       """fork-and-detach で起動。Popen を返す。失敗時 None。"""
       if not _SERVER_SCRIPT.exists():
           return None
       kwargs: dict = { ... 既存と同じ ... }
       try:
           return subprocess.Popen([sys.executable, str(_SERVER_SCRIPT)], **kwargs)
       except OSError:
           return None
   ```

4. **spawn 後 server.json 出現待ち poll** (Proposal 1, 2):
   ```python
   SPAWN_WAIT_TIMEOUT_SECONDS = 0.25  # module 定数 — テストから参照
   SPAWN_WAIT_INTERVAL_SECONDS = 0.05

   def _wait_for_self_server_json_url(self_pid: int) -> Optional[str]:
       """spawn した子の server.json が現れるまで poll。
       `info.pid == self_pid` で **自分が spawn した子** の json か確認 (Proposal 1)。
       上限 SPAWN_WAIT_TIMEOUT_SECONDS。失敗時 None (silent fallback)。"""
       deadline = time.monotonic() + SPAWN_WAIT_TIMEOUT_SECONDS
       while time.monotonic() < deadline:
           info = _read_server_json(SERVER_JSON_PATH)
           if (info and info.get("pid") == self_pid
                   and isinstance(info.get("url"), str)):
               return info["url"]
           time.sleep(SPAWN_WAIT_INTERVAL_SECONDS)
       return None
   ```

5. **`main()` の出力判断追加** (Proposal 1 反映 — Popen None 時は poll を呼ばない):
   ```python
   def main() -> int:
       try:
           event = _read_hook_event_name()  # 未知値 / 不在は None
           if _server_is_alive():
               # 既起動: SessionStart のみ URL 再表示
               if event == "SessionStart":
                   info = _read_server_json(SERVER_JSON_PATH)
                   if info and isinstance(info.get("url"), str):
                       _emit_dashboard_message(info["url"])
               return 0
           # spawn 経路
           _remove_stale_server_json()
           proc = _spawn_server()
           if proc is None:
               return 0  # Popen 失敗 → silent。poll を呼ばない (古い json 誤読防止)
           # event が EXPECTED に入っていれば spawn 経路で出力する
           # (event=None でも出さない: hook 経由でない直叩きと区別するため)
           if event is None:
               return 0
           url = _wait_for_self_server_json_url(proc.pid)
           if url:
               _emit_dashboard_message(url)
       except Exception:
           pass
       return 0
   ```

   - **トレードオフ判断**: spawn 経路は既起動経路の高速 path ではなく、毎 hook 走るが
     spawn 自体が稀 (新セッション or idle 復活時のみ) なので 250ms は許容
   - alive path の budget 制約 (< 100ms) は spawn 経路には適用されない (issue 本文の
     「既起動検出経路の `< 100ms` 維持」は alive path 限定)
   - 注意: `event is None` (hook 経由でない直叩き / 未知値) のとき spawn 経路でも出力
     しない。これにより `python3 launch_dashboard.py` の手動起動時に systemMessage を
     stdout に吐いて confusion を起こさない

6. **`HEALTHZ_TIMEOUT_SECONDS` 等の既存定数は変更なし**

### 5.4 ドキュメント

- **`CLAUDE.md`**:
  - 「URL 確認方法」セクションを「URL の通知タイミング」に拡張
  - 4 hook + spawn/既起動 の組み合わせ表 (本プラン §2 表) を貼る
  - cat server.json は「fallback の確認手段」として残す
- **`MEMORY.md`**:
  - launch_dashboard 記述 (l.133〜) に systemMessage 出力ポリシー 1 段落を追加
  - 観測対象 Hook 早見表 (l.62〜) の launch_dashboard 行に「systemMessage 出力経路あり」を追記
- **README.md**:
  - 既に PR #35 で modified なのでスコープ外 (Issue #34 では触らない)
  - もし Issue #34 で触るなら別 commit / 別レビュー対象として明記

### 5.5 バージョンアップ

- `.claude-plugin/plugin.json`: `0.5.1` → `0.5.2`
- ユーザーから「リリースバージョンは `0.5.2`」と Issue #34 コメントで指示済み

### 5.6 CI / 手動確認

- `python3 -m pytest tests/` で全テスト pass
- 実機確認 (Definition of Done):
  - 新セッションで SessionStart → 画面に `📊 Dashboard: http://localhost:XXXX` 表示
  - 視認性 (color / icon / persistence) を実機で観察、視認不足なら本文を再考
    (例: prefix を `[claude-transcript-analyzer]` にする等)
  - idle 停止 (DASHBOARD_IDLE_SECONDS を一時的に短縮して再現) 後に prompt → 復活
    + systemMessage 表示
  - 通常会話で連発しない (UserPromptExpansion / Submit / PostToolUse 経由で silent)

## 6. リスク・未解決事項

### 6.1 systemMessage の UI 表示形態

公式 docs に色 / アイコン / 永続性の細記述なし。実機で観察して、視認性が低ければ
本文の prefix / 絵文字を変える可能性あり。本プランでは `📊 Dashboard: <url>` を
ベースラインにする。

**長さ制限について (plan-reviewer Q2)**: 公式 docs に明記なし。`http://localhost:65535`
で 36 文字程度なので safe。将来 `started_at` / port 名等を含めたい場合に備え、本文を
**「📊 Dashboard: <url>」** から拡張するときの最大長は 200 文字目安として §6.1 に
留めておく (制限値は実機で観察したうえで CLAUDE.md に明記する余地あり)。

### 6.2 spawn 後 server.json poll の budget (Proposal 2 反映)

`SPAWN_WAIT_TIMEOUT_SECONDS = 0.25` / `SPAWN_WAIT_INTERVAL_SECONDS = 0.05` を
**module 定数** として固定し、`TestSpawnPathBudget.test_spawn_wait_budget_under_300ms`
で構造的に pin。

PostToolUse 経由で偶発的に spawn が走るのは「`_server_is_alive()` が False を返す」=
「server.json 不在 or pid 死亡」の異常状態のみで、日常的には起きない (idle stop 直後の
極めて短い窓限定)。日常的に PostToolUse で spawn が走るなら別の bug → 別 issue で扱う。

許容できない場合のフォールバック案 (記録のみ):
- A: spawn 後 poll せず即時 silent return → 新セッションで気付き機会喪失 → **不採用**
- B: poll を 100ms に縮める → 採用候補 (test 値だけ変えれば済む)
- C: 子プロセスが server.json を書いたら親に signal を送る → やりすぎ

### 6.3 spawn 失敗時の古い server.json 誤読 (Proposal 1 反映)

旧プランでは `_remove_stale_server_json()` をすり抜けた古い server.json (broken JSON
や non-dict は残す設計のため) が、spawn 失敗後の poll で偶然読まれて誤通知する
risk があった。

**対策**:
- `_spawn_server()` を `Optional[Popen]` 返却に。Popen 失敗時は `None` を返し、
  `main()` で `proc is None` のとき即 silent return (poll を呼ばない)
- `_wait_for_self_server_json_url(self_pid)` は `info.pid == self_pid` で
  「自分が spawn した子の json か」を確認。pid 一致しなければ無視

### 6.4 stdin の re-entrance / 二重 read

launch_dashboard は launcher (1 回 1 read) なので問題なし。子サーバーは DEVNULL
で stdin を継承しないので影響なし。

### 6.5 既存「silent exit 0」契約の文書化

CLAUDE.md / MEMORY.md / `launch_dashboard.py` docstring を更新して
「成功経路で hook output JSON を 1 行書いてもよい」と緩和を明示する。

### 6.6 `hook_event_name` 値の表記揺れ (Proposal 3 反映)

公式 docs 上は PascalCase (`SessionStart`, `UserPromptSubmit`, `UserPromptExpansion`,
`PostToolUse`)。

**対策** (実装段階で defensive に):
- `name.strip()` で正規化したうえで `frozenset` メンバシップ判定 (`name in
  _EXPECTED_HOOK_EVENTS`)
- 未知値は silent path に倒れる → 二重出力や crash には繋がらない (被害は気付き機会喪失のみ)
- **opt-in debug hook**: `DASHBOARD_DEBUG_HOOK_EVENT` 環境変数が truthy のとき
  `~/.claude/transcript-analyzer/hook_event_debug.jsonl` に append。env 未設定時は
  完全 no-op。merge 後 1 セッションだけ env を立てて実値採取、不要になれば次の PR で
  除去 (or 永続化)
- 値テストで表記揺れの 1 パターンを意図的にテスト (`test_unknown_hook_event_name_silent`)

### 6.7 resume 時の SessionStart 発火 (plan-reviewer Q1)

`claude --resume` で既起動セッションに復帰したとき `SessionStart` が再発火するかは
公式 docs に明記なし。再発火しないなら「既起動 + SessionStart」セルは事実上 trigger
されず、§2 表が spawn 経路のみに縮小する。

→ §6.1 の視認性チェックと **同じセッションで実機確認** する。再発火しないなら、
将来「resume 経路で systemMessage を出すには独自 trigger が要る」という別 issue 化。
本 PR の AC には影響しない (再発火するなら表のとおり、しないなら spawn 経路だけが
価値を持つが既存実装は spawn 経路で出力するので価値は維持)。

### 6.8 stdout output の構造制約 (Proposal 5 反映)

hook output protocol は format-fragile。デバッグ print の混入 / BOM / trailing
whitespace で壊れる risk あり。

**対策** (テスト段階で structural pin):
- `TestSystemMessageStructure` で出力ありケースの構造制約 (1 行 + strict JSON parse +
  prefix 一致 + stderr 空) を assert
- silent ケースは out == "" + err == "" を assert
- 既存テストにも `assert err == ""` を波及 (autouse fixture or helper)

## 7. Definition of Done (Issue #34 本文 + plan-reviewer Proposal 反映)

- [ ] `python3 -m pytest tests/` 全 pass
- [ ] 新規テスト (TestSystemMessageOutput) 15 件 pass (UserPromptExpansion + Popen 戻り値 +
      pid 一致 stale-json 拒否 + unknown event 反映で 13→15)
- [ ] `TestSystemMessageStructure` で stdout 単一行 + strict JSON + prefix + stderr 空を pin
- [ ] `TestSpawnPathBudget` で spawn 経路 < 300ms (= SPAWN_WAIT_TIMEOUT_SECONDS + 50ms slack) を pin
- [ ] 既起動経路 < 100ms 維持を新テスト (4 hook 名パラメトリック) で pin
- [ ] 実機で SessionStart → 画面に systemMessage 表示
- [ ] 実機で通常会話中に連発されない
- [ ] **本 PR で `hooks/hooks.json` を変更しない** (Proposal 4 — PR #35 と直交保証)
- [ ] CLAUDE.md / MEMORY.md / `launch_dashboard.py` docstring 更新
- [ ] `.claude-plugin/plugin.json` を `0.5.2` に bump
- [ ] resume 時の SessionStart 再発火を実機で確認 (Q1)
- [ ] `DASHBOARD_DEBUG_HOOK_EVENT` で hook_event_name 実値を 1 セッション採取し、
      公式 docs の PascalCase と一致を確認 (Q3 / Proposal 3)
- [ ] PR を起こす (タイトル例: `feat(launch_dashboard): systemMessage で URL を即時通知 (Issue #34)`)

## 8. ステップ別チェックリスト (実装順)

1. [x] **plan-reviewer 通過** (本ドキュメント) — 5 Proposals + 3 Questions 反映済
2. [ ] ブランチ戦略をユーザーに二択で確認 (main 先行 [推奨] / PR #35 マージ待ち)
3. [ ] 新ブランチ作成 + 本計画書 commit
4. [ ] テスト追加 (Red): TestSystemMessageOutput 15 件 + TestSystemMessageStructure +
      TestSpawnPathBudget + TestPerformance 4 hook パラメトリック
5. [ ] 実装 (Green): `_read_hook_event_name` (set membership) /
      `_emit_dashboard_message` / `_wait_for_self_server_json_url` (pid 一致) /
      `_spawn_server` を Optional[Popen] 返却に
6. [ ] `main()` の出力判断分岐実装 (proc is None で poll 呼ばない / event is None
      で出力しない)
7. [ ] テスト全 pass 確認
8. [ ] CLAUDE.md / MEMORY.md / `launch_dashboard.py` docstring 更新 +
      「silent exit 0 緩和」明記
9. [ ] plugin.json version bump 0.5.2
10. [ ] 実機確認 (Definition of Done) — UI 視認性 / resume 時 SessionStart /
      hook_event_name 実値採取
11. [ ] commit + push + PR 作成
