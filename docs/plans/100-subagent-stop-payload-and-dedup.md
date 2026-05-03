# Issue #100 Implementation Plan — SubagentStop hook payload 修正 + agent_id dedup 追加

> Issue: https://github.com/tetran/claude-transcript-analyzer/issues/100
> Milestone: v0.7.4 / Closes #93
> Plan iteration: 4 (iteration 3 reviewer findings P1-P3 / Q1-Q2 folded)

## 1. Goal

`SubagentStop` hook の payload 実態 (`duration_ms`/`success` は **存在しない**, `agent_id` は多重発火する) に `hooks/record_subagent.py` と `subagent_metrics.py` を整合させ、欠損フィールドの誤読み取りを停止し、同 `(session_id, subagent_id)` の `subagent_stop` を first-wins で 1 件化する。`agent_transcript_path` を新規 capture して将来の filter validation 用 evidence を残し、 `subagent_type == ""` 暗黙除外の意図を doc / コメントで pin する。

## 2. Critical files

**編集 (production):**
- `hooks/record_subagent.py`
- `subagent_metrics.py`

**編集 (tests, 既存 file への追記):**
- `tests/test_record_subagent.py`

**新規 (test, 新 file):**
- `tests/test_subagent_metrics.py`
  *(`test_subagent_quality.py` は Issue #60 由来の quality 限定スコープなので、generic な `subagent_metrics` レベルの dedup テストは新 file に置いて、metrics 全体の test owner を作る。これは Issue #93 で見つけた anomaly に対する long-lived regression suite のホームになる。)*

**編集 (docs):**
- `docs/reference/subagent-invocation-pairing.md`
- `docs/spec/usage-jsonl-events.md`

## 3. Out of scope (Issue 本文より再掲)

- per-subagent transcript の `message.usage` 露出 (#93 末尾で言及された別発見)
- Claude Code 本体への bug report
- type='' の post-hoc heuristic ペアリング (実 subagent 不在で救う対象なし → 不採用)
- `agent_transcript_path` を filter / dedup key として使うこと (ユーザー確定事項 A1: capture のみ)
- 既存の `subagent_type == ""` 暗黙除外の挙動変更 (コメントのみ追記)

## 4. TDD-first ordered steps

> **TDD cycle 単位**: production の 1 行為 (1 field 追加 / 1 field 削除 / 1 dedup ステップ) ごとに `RED → GREEN → refactor` を完結させる。下記 Step 2 / Step 3 / Step 4 はそれぞれ独立 cycle。

---

### Step 0 — Dashboard regression baseline snapshot (production data 不変条件のための pre-flight)

**Why**: DoD は「dashboard Quality ページの `subagent_ranking.failure_rate` / `avg_duration_ms` が変更前後で同値」を要求する。実装着手後では before snapshot を取れないため、**branch を切る前に一度だけ baseline を保存**する。

**手順**:

1. 現 HEAD (= `main`) の状態で、production の `~/.claude/transcript-analyzer/usage.jsonl` を **コピーせず read-only** で参照し、専用スクリプト 1 本を `python3 -c '...'` の oneliner で走らせて以下を記録 (reviewer iteration 2 Q1 で `usage_invocation_intervals` カバレッジ追加):
   ```python
   import json
   from subagent_metrics import aggregate_subagent_metrics, usage_invocation_intervals
   events = [json.loads(l) for l in open("/Users/kkoichi/.claude/transcript-analyzer/usage.jsonl") if l.strip()]
   metrics = aggregate_subagent_metrics(events)
   for name in sorted(metrics):
       m = metrics[name]
       print(f"M\t{name}\t{m['count']}\t{m['failure_count']}\t{m['failure_rate']:.6f}\t{m['avg_duration_ms']}")
   # usage_invocation_intervals も snapshot に含める (Q1):
   # stop.duration_ms 削除が permission-attribution 経路に regression を生まないか
   # snapshot diff で検出するため。
   intervals = usage_invocation_intervals(events)
   total_dur = sum((e - s) for s, e, _rep in intervals if e is not None and s is not None)
   print(f"I\tcount={len(intervals)}\ttotal_duration_epoch_diff={total_dur:.3f}")
   ```
2. 出力を **作業 scratch (e.g. `/tmp/issue-100-baseline-pre.tsv`)** に保存。**repo 内には commit しない** (transient artifact)。
3. PR description / 検証ログに「base = `<commit-sha>`, n_types = X, sha256 = Y」を pin。

> 調査 #93 の「全期間 183/307 type='' / 多重 stop 7 件」の数字は **本変更前の構造的 status quo** (iteration-1 review 時点で計測)。Step 0 を実装直前に再実行した時点で件数が増えている可能性は当然あり (= run 時点で生きている usage.jsonl をそのまま baseline 化する)。**Step 0 で capture した数字 = 本実装の baseline**、illustrative な #93 数字との差は drift ではない。

> **Note**: `subagent_kinds_total` 等は `subagent_type == ""` を `_bucket_events` 段で既に弾いているため、Step 0 snapshot に出ない type は最初から count=0。**snapshot 比較は「snapshot に出てくる non-empty type」のみで OK**。intervals 出力は count + total duration の 2 指標を見る (= permission attribution の構造変化を catch する coarse-grained guard)。

> **Q1 snapshot の coverage 限界 (reviewer iteration 3 P1)**: `usage_invocation_intervals` の duration 算出は rep (`subagent_start`) 自体の `duration_ms` を最優先で使い、次に `stop.duration_ms` を fallback (`subagent_metrics.py:153-158`)。`subagent_start` が `duration_ms` を持つ invocation は本変更前後で値が変わらないため、`total_duration_epoch_diff` snapshot は **「lifecycle-only invocation で paired stop の `duration_ms` に依存していたスライス」のみ** を catch する coarse guard。`subagent_start`-led invocation は本指標で insulate されている。完全な regression 担保ではないので、Step 6a 全テスト緑 + Step 6b dashboard snapshot diff と組み合わせて DoD 達成とする。

---

### Step 1 — Failing tests (RED 一括)

3 つの失敗 (drift guard / capture / dedup) を先に書く。RED → GREEN に進める順序は cycle 単位 (Step 2-4)。

#### 1a. `tests/test_record_subagent.py` への追記 — drift guard (`duration_ms`/`success` を **書き出さない**)

`TestSubagentStopEvent` クラス末尾に以下のテストを追加:

```python
def test_subagent_stop_drops_duration_ms_field_when_provided(self, tmp_path):
    """drift guard: 実 SubagentStop payload に duration_ms は無い (#93 調査結果)。
    仮に input に紛れていても event には書き出さないことを pin。"""
    usage_file = str(tmp_path / "usage.jsonl")
    stdin = {
        "hook_event_name": "SubagentStop",
        "agent_type": "Explore",
        "agent_id": "agent-x",
        "duration_ms": 99999,        # 実 hook では来ない値
        "success": True,             # 実 hook では来ない値
        "session_id": "s1",
        "cwd": "/p",
    }
    run_script(stdin, usage_file)
    ev = read_events(usage_file)[0]
    assert ev["event_type"] == "subagent_stop"
    assert "duration_ms" not in ev, "duration_ms は実 SubagentStop payload に存在しない (Issue #100 / #93)"
    assert "success" not in ev, "success は実 SubagentStop payload に存在しない (Issue #100 / #93)"
```

加えて、**既存テスト 2 本を改修**:
- `test_subagent_stop_with_duration_and_success` → `duration_ms`/`success` が **イベントに含まれない** ことを assert する形へ書き換え
- `test_subagent_stop_with_failure` → 同様に `success` が含まれない方向へ書き換え (test 名は `..._with_failure_payload_does_not_persist_success` 等にリネーム可)

#### 1b. `tests/test_record_subagent.py` への追記 — `agent_transcript_path` capture

```python
def test_subagent_stop_captures_agent_transcript_path(self, tmp_path):
    """新規 capture: agent_transcript_path は filter validation の evidence。
    値そのものは下流 dedup / filter で使わない (capture only)。"""
    usage_file = str(tmp_path / "usage.jsonl")
    stdin = {
        "hook_event_name": "SubagentStop",
        "agent_type": "Explore",
        "agent_id": "agent-x",
        "agent_transcript_path": "/Users/kkoichi/.claude/projects/foo/agent-x.jsonl",
        "session_id": "s1",
        "cwd": "/p",
    }
    run_script(stdin, usage_file)
    ev = read_events(usage_file)[0]
    assert ev["agent_transcript_path"] == "/Users/kkoichi/.claude/projects/foo/agent-x.jsonl"

def test_subagent_stop_omits_agent_transcript_path_when_absent(self, tmp_path):
    """payload に無いときは event に key を入れない (後方互換 + メイン誤発火検出シグナル)。
    Issue #93 観察: メインスレッド誤発火時は実 subagent 不在 → transcript file も不在。"""
    usage_file = str(tmp_path / "usage.jsonl")
    stdin = {
        "hook_event_name": "SubagentStop",
        "agent_type": "",  # メイン誤発火パターン
        "session_id": "s1",
        "cwd": "/p",
    }
    run_script(stdin, usage_file)
    ev = read_events(usage_file)[0]
    assert "agent_transcript_path" not in ev
```

#### 1c. `tests/test_subagent_metrics.py` (新規 file) — agent_id dedup

```python
"""tests/test_subagent_metrics.py — subagent_metrics の generic regression suite。

Issue #100 (= #93 調査結果対応): 同 (session_id, subagent_id) で複数発火した
subagent_stop を first-wins で 1 件化する dedup の pin。
"""
import subagent_metrics


def _start(name, session, ts, success=True, duration_ms=None):
    ev = {"event_type": "subagent_start", "subagent_type": name,
          "session_id": session, "project": "p", "timestamp": ts, "success": success}
    if duration_ms is not None:
        ev["duration_ms"] = duration_ms
    return ev


def _stop(name, session, ts, agent_id="agent-x"):
    return {"event_type": "subagent_stop", "subagent_type": name,
            "subagent_id": agent_id, "session_id": session, "project": "p", "timestamp": ts}


class TestSubagentStopAgentIdDedup:
    def test_four_stops_same_agent_id_collapse_to_one_invocation(self):
        """同 (session, subagent_id) の subagent_stop が 4 件発火しても 1 invocation 扱い。
        最大 4 重複 (Issue #93 観察) を first-wins で 1 件化する pin。"""
        events = [
            _start("Explore", "s", "2026-04-22T10:00:00+00:00"),
            _stop("Explore",  "s", "2026-04-22T10:00:01+00:00", agent_id="agent-A"),
            _stop("Explore",  "s", "2026-04-22T10:00:02+00:00", agent_id="agent-A"),
            _stop("Explore",  "s", "2026-04-22T10:00:03+00:00", agent_id="agent-A"),
            _stop("Explore",  "s", "2026-04-22T10:00:04+00:00", agent_id="agent-A"),
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)
        assert m["Explore"]["count"] == 1, "start 1 件 → invocation 1 件"
        # 重複 stop が orphan としても積まれず、failure_count drift も起きない
        assert m["Explore"]["failure_count"] == 0

    def test_dedup_keeps_earliest_timestamp_stop_regardless_of_input_order(self):
        """dedup は **timestamp 最小** の stop を保持 (= 入力順 first ではない)。
        rescan_transcripts.py --append 経由で input order が timestamp 順と乖離する
        ケースに備え、min(timestamp) semantic を pin する (Issue #100 reviewer P2 由来)。

        入力順 (09 → 01 → 05) と timestamp 順 (01 → 05 → 09) が異なる構成で、
        surviving stop の timestamp が earliest (10:00:01) であることを直接 pin。

        Note: public API (`aggregate_subagent_metrics` / `invocation_records`) は
        surviving stop の timestamp を露出しないため、`_bucket_events()` を直接
        probe する。`_bucket_events` は dedup semantic の contract owner なので
        regression-pin として直 probe は適切 (reviewer iteration 2 P2 の判断)。"""
        events = [
            _start("Plan", "s", "2026-04-22T10:00:00+00:00"),
            _stop("Plan",  "s", "2026-04-22T10:00:09+00:00", agent_id="agent-B"),  # later (input first)
            _stop("Plan",  "s", "2026-04-22T10:00:01+00:00", agent_id="agent-B"),  # earliest
            _stop("Plan",  "s", "2026-04-22T10:00:05+00:00", agent_id="agent-B"),
        ]
        # 公開 API レベルの drift guard
        m = subagent_metrics.aggregate_subagent_metrics(events)
        assert m["Plan"]["count"] == 1
        trend = subagent_metrics.aggregate_subagent_failure_trend(events)
        from collections import Counter
        ct = Counter(r["subagent_type"] for r in trend)
        assert ct["Plan"] == 1
        # min(timestamp) semantic の本命 assert: contract owner を直接 probe
        _starts, stops, _lifecycle = subagent_metrics._bucket_events(events)
        plan_stops = stops[("s", "Plan")]
        assert len(plan_stops) == 1, "重複 stop が 1 件に集約される"
        assert plan_stops[0]["timestamp"] == "2026-04-22T10:00:01+00:00", \
            "min(timestamp) semantic: earliest stop が survive する (input-order first ではない)"

    def test_dedup_does_not_collapse_distinct_agent_ids(self):
        """異なる subagent_id は別 invocation として扱う (= 同 type 並行実行は dedup しない)。
        INVOCATION_MERGE_WINDOW=1s を超える間隔で 2 invocation を立て、両 stop が
        どちらも paired されることを pin (drift guard)。"""
        events = [
            _start("Explore", "s", "2026-04-22T10:00:00+00:00"),
            _start("Explore", "s", "2026-04-22T10:00:10+00:00"),  # 10s 後 → 別 invocation
            _stop("Explore",  "s", "2026-04-22T10:00:05+00:00", agent_id="agent-X"),
            _stop("Explore",  "s", "2026-04-22T10:00:15+00:00", agent_id="agent-Y"),
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)
        assert m["Explore"]["count"] == 2
        # drift guard: failure_count は両 success 不明 → 0
        assert m["Explore"]["failure_count"] == 0

    def test_dedup_missing_subagent_id_treats_each_stop_separately(self):
        """subagent_id="" の stop は dedup key を共有しない → 個別扱い (= 既存挙動)。
        record_subagent.py:107 が agent_id 不在時に "" を入れる現契約を pin。

        reviewer iteration 3 P2 強化: test name の主張「treats each stop separately」を
        `_bucket_events` 直接 probe で実際に pin する (= start count だけでは
        '空 agent_id stop が 2 件残ること' の挙動が assert されない問題の対処)。"""
        events = [
            _start("Explore", "s", "2026-04-22T10:00:00+00:00"),
            _stop("Explore",  "s", "2026-04-22T10:00:01+00:00", agent_id=""),
            _stop("Explore",  "s", "2026-04-22T10:00:02+00:00", agent_id=""),
        ]
        # 公開 API 側: invocation は start 由来で 1 件
        m = subagent_metrics.aggregate_subagent_metrics(events)
        assert m["Explore"]["count"] == 1
        # contract owner 直 probe: 空 agent_id の stop は dedup key を共有しないので 2 件残る
        _starts, stops, _lc = subagent_metrics._bucket_events(events)
        assert len(stops[("s", "Explore")]) == 2, \
            "subagent_id='' な stop は dedup key を共有しない → 2 件残る"


class TestEmptySubagentTypeStillExcluded:
    """既存 `if not name: continue` (= subagent_type == "" 暗黙除外) が
    本変更後も維持されていることの drift guard。Issue #93 で確認した
    メイン誤発火 type='' record が aggregator に漏れない pin。"""

    def test_type_empty_subagent_stop_does_not_create_invocation(self):
        events = [
            {"event_type": "subagent_stop", "subagent_type": "",
             "subagent_id": "agent-z", "session_id": "s1", "project": "p",
             "timestamp": "2026-04-22T10:00:00+00:00"},
        ]
        m = subagent_metrics.aggregate_subagent_metrics(events)
        assert m == {}, "type='' は構造的に除外されている (主流 invocation を生まない)"
```

加えて、**`tests/test_subagent_quality.py` の cross-aggregator invariant test (`test_failure_count_matches_metrics_failure_count`) が dedup 追加後も pass すること** を最終確認 (= 既存テストが green のまま)。

> **TDD invariant**: Step 1 完了時点で、上記 1a/1b/1c の new tests が **全て RED** であることを `pytest tests/test_record_subagent.py tests/test_subagent_metrics.py` で確認する。RED の理由が「想定どおりの assertion 失敗」であることも目視確認 (Phase 0 reverse-assert: `assert "duration_ms" not in ev` が `KeyError 等` ではなく `AssertionError` で落ちること)。

---

### Step 1.5 — Existing-fixture audit: silent-collapse 対策 (reviewer P1)

**Why**: 既存 `tests/test_subagent_quality.py` 等が `_stop()` helper の **default `subagent_id="agent_x"`** を共有して同 session に複数 stop を積む形で書かれている fixture がある。本 Step 4 の dedup 追加後、これらの fixture 内 stops は **新 dedup ロジックで silent collapse** され、test 自体は green を返すが意図された scenario (orphan stop 等) を **もはや実行していない** 状態になる (= silent regression)。Plan reviewer P1 で指摘。

**手順** (reviewer iteration 2 P3 で grep 範囲を broaden):

1. **Sibling-case scan (2-step procedure)**:
   - **Step A — 全 stop イベント生成箇所の列挙**: ヘルパー名や session id が異なる variant も漏らさず捕捉:
     ```bash
     grep -rnE '"event_type":\s*"subagent_stop"' tests/
     ```
     これで `_stop()` / `make_stop()` / `mk_stop()` / inline dict literal / parametrized fixture すべての sites を列挙。
   - **Step B — 各 match の周辺 fixture を確認**: `(session_id, subagent_id)` が同じ stop が ≥2 件ある fixture を特定。`_stop()` helper の default `subagent_id="agent_x"` を共有しているケースが特に注意。
   - 特に既知のターゲット: `tests/test_subagent_quality.py:236-272` の `test_orphan_stops_do_not_contaminate_percentile_samples` と `test_orphan_stops_do_not_affect_failure_count_drift` (default-`agent_id="agent_x"` 共有を reviewer iteration 1 が確認済)。

2. **対応方針** (case ごとに選択):
   - **(a) distinct agent_id 化**: 各 stop に異なる `subagent_id` を渡して original semantic を維持。`_stop("Explore", "s", ts1, agent_id="a-1")` / `_stop(..., agent_id="a-2")` の形へ書き換え。
   - **(b) 削除 + supersede 記載**: scenario が新 dedup で構造的に不可能になっている場合 (= もはや production で発生し得ない) は test を削除し、PR description に「Issue #100 dedup により scenario が構造的に消滅 → `tests/test_subagent_metrics.py::TestSubagentStopAgentIdDedup::...` で代替」と記録。

   **(b) を選ぶ判定 guideline** (reviewer iteration 3 Q1): 削除予定 test の **assertion セット** を 1 件ずつ列挙し、新 `tests/test_subagent_metrics.py` の対応 test がそれぞれを cover しているか check する。削除 test の assertion ⊆ 新 test の assertion なら (b)、不足があるなら新 test に assertion を追加するか (a) を選ぶ。判定結果 (=「削除 OK / assertion ペア表」) を PR description に短い表で記載する。

3. **`_stop()` helper の future-proof 化** (オプション、Bonus): `tests/test_subagent_quality.py` の `_stop()` の default `subagent_id` を `None` (= caller が必ず指定) に変更し、collapse 危険を fixture-write 時点で surface させる。既存 caller への impact 大なら見送り、PR description に "future improvement" として記録。

**Acceptance**: Step 4 (dedup 実装) GREEN 化後、上記対象 test が「scenario を本当に exercise している」ことを `pytest -v --no-cov tests/test_subagent_quality.py -k orphan` の出力 + diff で確認。

> **Why this is structurally important**: Silent neutralized tests are worse than failing tests — green pass で future reader の信頼予算を消費する。CLAUDE.md 「After applying a user correction, scan the same artifact for sibling cases」の codebase-wide 適用。

---

### Step 2 — `_handle_subagent_stop` から `duration_ms` / `success` 読み取りを削除 (1 RED-GREEN cycle)

**file**: `hooks/record_subagent.py`

`_handle_subagent_stop` 関数 L112-115:
```python
if "duration_ms" in data:
    event["duration_ms"] = data["duration_ms"]
if "success" in data:
    event["success"] = data["success"]
```
を **削除**。

**確認**: Step 1a の drift guard test 1 本 + 改修した既存テスト 2 本が GREEN へ。

---

### Step 3 — `_handle_subagent_stop` で `agent_transcript_path` を capture (1 RED-GREEN cycle)

**file**: `hooks/record_subagent.py`

`_handle_subagent_stop` 関数 (Step 2 で短くなった) の末尾、`append_event` 直前に追加:
```python
if "agent_transcript_path" in data:
    event["agent_transcript_path"] = data["agent_transcript_path"]
```

**確認**: Step 1b の capture test 2 本が GREEN へ。

---

### Step 4 — `subagent_metrics._bucket_events` に `agent_id` dedup 追加 (1 RED-GREEN cycle)

**file**: `subagent_metrics.py`

#### 4a. `_bucket_events` に **2-pass min(timestamp)** dedup を入れる

> **Decision (reviewer P2)**: 当初案の「素朴な input-order first-wins」から、**2-pass min(timestamp)** に変更。理由: `scripts/rescan_transcripts.py --append` で input order が timestamp 順と乖離するケースがあり、input-order first-wins では rescan 経由で取り込まれた重複 stop が「後から timestamp 古い行が来る」状況で wrong-row 採用となる。2-pass の cost は events を 1 回余分になめるだけで O(n) のまま — over-engineer ではない。

```python
def _bucket_events(events: list[dict]) -> tuple[dict, dict, dict]:
    """events を `(session_id, subagent_type)` キーで starts / stops / lifecycle に振り分け。

    `subagent_type == ""` は構造的に除外 (Issue #100 / #93):
    SubagentStop hook はメインスレッド停止時にも誤発火し、その場合 type が空。
    実 subagent 不在 / per-subagent transcript file も不在 (#93 ローカル調査) なので
    aggregation 時 filter で 100% 即時救済できる。post-hoc heuristic ペアリングは
    対象不在 → 不採用。詳細は docs/reference/subagent-invocation-pairing.md
    "Known artifact" セクション参照。

    `subagent_stop` は `(session_id, subagent_id)` で **min(timestamp)** dedup
    (Issue #100 / #93): Claude Code が同 stop hook を最大 4 重発火する観察あり
    (3 組 / 全期間 7 件)。timestamp 最小の 1 件のみ採用 (rescan_transcripts.py
    --append 経由で input order が時間順と乖離しても正しく earliest を選ぶため
    2-pass 化)。subagent_id が空 ("") の場合は dedup key を共有せず個別扱い
    (= 既存ペアリング挙動を破壊しない)。

    Key 設計: `(session_id, subagent_id)` で集約。Issue 本文の文言通り。
    `subagent_id` がグローバル一意であっても session_id を含めることで
    over-keying は無害 (false-collapse は発生しない); 仮にグローバル衝突が
    あった場合の防御も兼ねる。
    """
    # Pre-pass: (session_id, subagent_id) ごとに earliest timestamp を集計
    earliest_ts_by_dedup: dict = {}
    for ev in events:
        if ev.get("event_type") != "subagent_stop":
            continue
        if not ev.get("subagent_type", ""):
            # type='' は filter (= dedup から完全排除)。
            # 順序: type filter → dedup. 逆順だと type='' stop の subagent_id が
            # seen を汚染し、本物 stop の dedup key と衝突する潜在リスク (R2 参照)。
            continue
        sid = ev.get("subagent_id", "")
        if not sid:
            continue
        dedup_key = (ev.get("session_id", ""), sid)
        ts = ev.get("timestamp", "")
        cur = earliest_ts_by_dedup.get(dedup_key)
        if cur is None or ts < cur:
            earliest_ts_by_dedup[dedup_key] = ts

    starts_by_key: dict = {}
    stops_by_key: dict = {}
    lifecycle_by_key: dict = {}
    accepted_dedup_keys: set = set()  # tie-break: 同一 earliest ts の duplicate 入力に備え 1 件採用
    for ev in events:
        et = ev.get("event_type")
        name = ev.get("subagent_type", "")
        if not name:
            # メインスレッド停止時の SubagentStop hook 誤発火 (Issue #100 / #93) を
            # 構造的に除外。実 subagent 不在のため救済対象なし。
            continue
        key = (ev.get("session_id", ""), name)
        if et == "subagent_start":
            starts_by_key.setdefault(key, []).append(ev)
        elif et == "subagent_stop":
            sid = ev.get("subagent_id", "")
            if sid:
                dedup_key = (ev.get("session_id", ""), sid)
                # min(timestamp) と一致する 1 件のみ採用
                if ev.get("timestamp", "") != earliest_ts_by_dedup[dedup_key]:
                    continue
                # 同 timestamp の duplicate (極稀) は最初の 1 件で確定
                if dedup_key in accepted_dedup_keys:
                    continue
                accepted_dedup_keys.add(dedup_key)
            stops_by_key.setdefault(key, []).append(ev)
        elif et == "subagent_lifecycle_start":
            lifecycle_by_key.setdefault(key, []).append(ev)
    return starts_by_key, stops_by_key, lifecycle_by_key
```

**重要 — dedup の order**: 「**type='' filter → agent_id dedup**」の順。`type == ""` の stop は dedup 検討にすら入れない (= `earliest_ts_by_dedup` を汚染しない)。これは「メイン誤発火」と「実 subagent stop の重複発火」を別問題として扱う設計。R2 参照。

#### 4b. Sibling `if not name: continue` の audit + 必要箇所への同コメント追記 (reviewer Q2 + iteration 2 P1)

`subagent_metrics.py` 内で `if not name: continue` (or 同等の `subagent_type == ""` filter) の occurrence を **2 sub-question で classify** する:

- **Q-a**: この site は `subagent_stop` を iterate するか? — yes なら新 `(session_id, subagent_id)` dedup の追加が必要。
- **Q-b**: この site は同コメント (「メインスレッド停止時の SubagentStop hook 誤発火を構造的に除外する意図 (Issue #100 / #93)」) を必要とするか? — `subagent_type == ""` filter の意図を pin する必要があれば yes。

**手順**:
```bash
grep -nE 'if not name|subagent_type == ""' subagent_metrics.py
```
で全 occurrence を列挙し、各 occurrence について Q-a / Q-b を判定。

**Pre-classification (reviewer iteration 2 P1 指摘)**:
- `_bucket_events()` (Step 4a で改修): Q-a=YES (subagent_stop を iterate) / Q-b=YES (filter 意図 pin) → 改修済 + コメント追加済
- `usage_invocation_events()` (L84-117): Q-a=NO (`subagent_start` / `subagent_lifecycle_start` のみ iterate、`subagent_stop` は読まない) / Q-b=YES (filter の意図 pin は欲しい) → **dedup 不要、コメントのみ追加**
- その他 occurrence: 判定後 plan 内に列挙し、改修方針を pin

**原則**: 機械的全置換は避ける (= 意図が異なる箇所には付けない)。判定結果を PR description に 1-2 行で要約 (実装者の追加判断を不要にする)。

#### 4c. cross-aggregator invariant test (`test_failure_count_matches_metrics_failure_count` in `test_subagent_quality.py`) の pass を確認

`_bucket_events` は `aggregate_subagent_metrics` / `invocation_records` / `usage_invocation_intervals` の 3 経路から呼ばれる single source なので、dedup を一箇所に入れれば全て同期する (= drift 構造的不可能)。

**確認**: Step 1c の dedup test 群が GREEN へ。`tests/test_subagent_quality.py` の既存 invariant test も依然 green (Step 1.5 の fixture 修正後の状態で)。

---

### Step 5 — Documentation updates

#### 5a. `docs/reference/subagent-invocation-pairing.md` に "Known artifact" セクションを追加

**位置**: ファイル末尾 (`## 関連 source` テーブルの直前) に新セクションとして追加 — 既存の概念解説 (invocation 同定 / pair-with-stop / timing semantics / permission attribution / module boundary) の後段で「観測される現実の anomaly カタログ」として位置付ける。

**内容**:

```markdown
## Known artifact: SubagentStop hook の type 欠損 / agent_id 多重発火

Issue #93 (#100 で対応) のローカル観察で判明した Claude Code SubagentStop hook の anomaly。
本リポは aggregator 側 filter / dedup で 100% 救済する (post-hoc heuristic 不要)。

### 観測 (起票時 #93 + 全期間)

- **type='' (subagent_type 空文字)**: 起票時 137/238 (57.6%), 全期間 183/307 (59.6%)
- **agent_id 多重発火**: 全期間 3 組 / 最大 4 重複

### type='' の正体 — メインスレッド停止時の SubagentStop hook 誤発火

実 subagent 不在 / per-subagent transcript file (`~/.claude/projects/.../<agent_id>.jsonl`)
も filesystem 上に存在しない。**メインスレッド停止時に SubagentStop hook が
混信して発火している** と判定。Claude Code 本体側の bug 性が高いが、本リポでは
aggregation 時の `if not name: continue` (= `_bucket_events` の type='' 除外)
で構造的に弾く方針。`_handle_subagent_stop` で `agent_transcript_path` を新規 capture
することで「値の有無 = 実 subagent 由来か / メイン誤発火か」を後追い検証可能にする
(filter validation 用 evidence、aggregator では使わない)。

### `duration_ms` / `success` フィールドは実 payload に存在しない

#93 ローカル観察で SubagentStop hook の生 payload を確認したところ、
`duration_ms` も `success` も hook 入力に存在しない。`_handle_subagent_stop` の
`if "duration_ms" in data` / `if "success" in data` ガードは **常に False**
だったため、削除しても data loss なし (= aggregator は元々 stop の duration を
受け取っていなかった。`_invocation_duration` の stop fallback も実は never-hit)。

### agent_id 多重発火 = `(session_id, subagent_id)` min(timestamp) dedup で救済

`_bucket_events` の `subagent_stop` 振り分け段で `(session_id, subagent_id)` を
dedup key とし、**timestamp 最小** の 1 件のみを採用する (2-pass)。`subagent_id`
が空 ("") の場合は dedup key を共有せず個別扱い (= 既存ペアリング挙動を破壊
しない)。type='' filter が先で agent_id dedup が後 — 順序を逆にすると `type=''`
stop の `agent_id` で `seen` を汚染して本物の subagent の dedup に干渉する
可能性がある。input-order first-wins ではなく min(timestamp) を採用する理由は
`scripts/rescan_transcripts.py --append` 経由で input order が timestamp 順と
乖離する可能性があるため。

### `agent_transcript_path` capture の対称性

`agent_transcript_path` capture は **`SubagentStop` event のみ** に追加される。
`PostToolUseFailure(Task|Agent)` 経由で記録される `subagent_start (success=False)`
レコード等には含まれない (= 上流 hook payload にそもそも該当 field が無い)。
失敗 subagent の forensics は別シグナル (`subagent_start.success` / 直前 start
ペアリング等) を使う必要がある。reviewer P5 で指摘された非対称の pin。

### 過去 data 救済範囲

- type='' event 183 件 + 多重 stop 7 件は aggregation 時 filter / dedup で
  100% 即時救済 (新規 backfill は不要)
- 過去 archive にも `duration_ms` / `success` が含まれる stop event が残るが、
  reader は付加 field を silent ignore するため互換問題なし
  (`_invocation_duration` は順に `stop.duration_ms` → `start.duration_ms` を
  なめるが、本変更後の新規 stop には `duration_ms` が来ない / 過去 stop には
  来ているケースもある混在を許容)

### 関連
- Issue #100 (本対応 PR) / Issue #93 (調査母体)
- `_handle_subagent_stop` capture 拡張: `hooks/record_subagent.py`
- dedup 実装: `subagent_metrics._bucket_events`
```

#### 5b. `docs/spec/usage-jsonl-events.md` の `subagent_stop` 説明を更新

**位置**: `subagent_stop` 例示直後に説明文を挿入。または `## 関連 reference` の前に新 subsection を追加。

**変更案 (例示を本変更後の shape に差し替え + 説明追記)**:

```jsonc
// Subagent 終了（SubagentStop）
// 実 hook payload に duration_ms / success は **存在しない** (Issue #100 / #93)。
// 集計時は (session_id, subagent_id) で first-wins dedup される (同 hook 最大 4 重発火を観測)。
{"event_type": "subagent_stop", "subagent_type": "Explore", "subagent_id": "agent_...",
 "project": "chirper", "session_id": "...", "timestamp": "2026-02-28T10:07:30+00:00",
 "agent_transcript_path": "/Users/.../projects/.../agent_....jsonl"}
```

そして例示直下に短い節を追加 (= **現行 contract のみ**を pin。過去互換 / 観察値 / forensics gotcha は reference 側に置く — reviewer P4):

```markdown
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
```

> **P4 補足**: 当初案の「過去 archive には旧仕様で書き込まれた値が残る可能性があるが、reader は silent ignore で互換維持」 一文は spec から削除し、reference 側の「過去 data 救済範囲」サブセクション (Step 5a 内) に統合。**spec = 現行 contract のみ / reference = 過去 archive の踏み抜き gotcha** という CLAUDE.md の仕分け原則に従う。

---

### Step 6 — DoD 実機確認

#### 6a. 全テスト緑

```bash
python3 -m pytest tests/
```

新 file `tests/test_subagent_metrics.py` を含めて全 green を確認。特に注意するクロステスト:
- `tests/test_subagent_quality.py::TestAggregateSubagentFailureTrend::test_failure_count_matches_metrics_failure_count`
- `tests/test_subagent_quality.py::TestSubagentMetricsAddsPercentileFields::test_orphan_stops_do_not_affect_failure_count_drift`

#### 6b. Dashboard regression check

**Plan A (snapshot 比較で代用 — 推奨, 実機 1 セッション運用不要)**:

1. branch 上の HEAD で Step 0 と同じ oneliner snapshot script を再実行 → `/tmp/issue-100-baseline-post.tsv` に保存。
2. `diff /tmp/issue-100-baseline-pre.tsv /tmp/issue-100-baseline-post.tsv` を取り、**`failure_rate` / `avg_duration_ms` 列が完全一致**することを確認。
   - `count` / `failure_count` も一致するはず (= 既存 invariant test の延長)。
   - もし差分が出るなら **dedup が想定外に既存 invocation を潰している** か、`duration_ms` 削除が `_invocation_duration` の stop fallback で実際に値を消している可能性あり (= Step 4 の「`stop.duration_ms` は元々来ていなかった」前提が崩れる)。
3. 差分 0 を PR description に記録。

**Plan B (dashboard 起動による視覚確認 — 補完)**:

1. `python3 -m dashboard.server` でローカル dashboard 起動 (read-only, production data)
2. Quality ページの `subagent_ranking` 表で `failure_rate` / `avg_duration_ms` 列の type 別値を main HEAD 時点の screenshot と比較。
3. 数字が一致していることを目視確認。

> **snapshot 比較で代用できる根拠**: `subagent_ranking` は `aggregate_subagents()` → `aggregate_subagent_metrics()` の thin wrapper (`dashboard/server.py:385-388`) で、`name` / `count` / `failure_count` / `failure_rate` / `avg_duration_ms` / `p50/p90/p99/sample_count` field をそのまま forward している。Step 0 snapshot script の出力 = dashboard 表示値 (top 10 cap を除く)。**実機 1 セッション運用は不要**。

> **Top-10 boundary catch (reviewer Q1)**: dashboard は `top_n=10` cap を適用する (`dashboard/server.py:387`)。dedup で count が変動して top-10 境界 (= 10 位 vs 11 位) の type が入れ替わるケースは、Step 0/6b の snapshot script が **全 type を出力** するため `diff` で必ず捕捉される (= 上位 10 type の入れ替わりも全 type 一致が崩れた瞬間に detect)。snapshot に追加で「order ranking」列を出す必要はない。

#### 6c. 新 subagent_stop event の field shape 実機確認 (PR description にエビデンスを記録)

> **Reviewer P3 対応**: 当初は「実機確認」とだけ書いていたが、reviewer 不可視の manual 検証は DoD 強度として弱い。下記の **コマンド出力を PR description にコピペ** して PR レビュアーが目視確認できる形に formalize する。Unit test (Step 1a/1b) は同 shape を unit レベルで cover 済みなので、6c の役割は「production 配線後の実 hook payload で同 shape が出るか」の integration 確認に集約する。

**手順**:

1. 任意の subagent (例: Explore) を 1 回呼ぶ — 本 issue の docs / test 編集中に自然に発生する subagent invocation で OK。
2. 直後に以下を 2 種類実行 (reviewer iteration 3 Q2 で empty-type ペア追加):
   ```bash
   # (i) 実 subagent 由来の最新 subagent_stop (= 非空 type / agent_transcript_path 含む想定)
   tail -200 ~/.claude/transcript-analyzer/usage.jsonl | grep '"event_type": "subagent_stop"' | grep -v '"subagent_type": ""' | tail -1 | python3 -m json.tool
   # (ii) メイン誤発火由来の最新 subagent_stop (= 空 type / agent_transcript_path 不在の想定)
   tail -200 ~/.claude/transcript-analyzer/usage.jsonl | grep '"event_type": "subagent_stop"' | grep '"subagent_type": ""' | tail -1 | python3 -m json.tool
   ```
3. PR description の「Verification」セクションに 2 つの出力をコピペ (= Step 1b の test pair: capture / omit が実機でも対称的であることを実証)。
4. レビュアーが目視で確認:
   - **(i) 非空 type record**: `duration_ms` 不在 ✓ / `success` 不在 ✓ / `agent_transcript_path` 存在 ✓
   - **(ii) 空 type record** (もし得られなければ「期間内に出現せず」の旨記載): `duration_ms` 不在 ✓ / `success` 不在 ✓ / `agent_transcript_path` 不在 ✓

## 5. Risks & tradeoffs

### R1. 過去 archive 内 `subagent_stop` event は旧仕様 (`duration_ms` / `success` 付き) のまま残る

**影響**: 想定上 silent ignore で問題なし。`_invocation_duration` は `stop.duration_ms` → `start.duration_ms` の順で fallback するが、本変更後も「過去 stop に `duration_ms` がある」 + 「新 stop に無い」 の混在を読む。aggregator は per-event 判定なので混在は OK。
**Mitigation**: reference 側 ("Known artifact" 内 "過去 data 救済範囲") で明記。**過去 data の rewrite / migration は行わない** (Out of scope)。
**`_invocation_duration` の dead-stop branch コメント (reviewer iteration 2 Q2)**: 本 PR では **コメント追加せず ride it out** とする。理由: dead branch ではなく「old archive のみ通る path」であり、構造的に正しい挙動 (= 過去 archive を読むときは引き続き有効)。コメントを足すと「将来削除する」誤解を招く。Step 0 の `usage_invocation_intervals` snapshot で regression を catch する設計でカバー。

### R2. dedup と type='' filter の order

**選択した順**: `type='' filter` → `agent_id dedup` (= type='' な stop は dedup の `seen` を汚染しない)。
**逆順 (dedup → filter) のリスク**: 仮に `type=''` の stop が `subagent_id="agent-xyz"` を持つ場合、dedup `seen` に "agent-xyz" を登録してしまい、後続の **本物の `subagent_type=Explore` + `subagent_id=agent-xyz`** な stop を `seen` 衝突で誤って drop する可能性 (実機では `agent_id` 衝突は希少だが、構造的に防ぐべき)。
**Mitigation**: `_bucket_events` の `if not name: continue` を最初に置くことで type='' を dedup から完全排除。Step 1c の `test_type_empty_subagent_stop_does_not_create_invocation` がこの順序を pin。

### R3. dedup semantic は **min(timestamp)** で確定 (reviewer P2 で当初案から変更)

**選択した実装**: 2-pass min(timestamp) — events を 1 度なめて `(session_id, subagent_id) → earliest_ts` を計算し、本 pass で `ts == earliest_ts` の 1 件のみ採用 (同 ts の duplicate 入力は最初の 1 件で確定)。
**当初案 (input-order first-wins)**: `usage.jsonl` は append-only なので「入力順 first ≈ timestamp earliest」と仮定していたが、`scripts/rescan_transcripts.py --append` は timestamp ソート後に append するため、後発の append run で **既存 row より古い timestamp の行が後から入る** 可能性が判明 (reviewer P2 指摘)。
**コスト**: events を 1 回余分になめる O(n)。stdlib のみ、追加 dep なし。
**Test pin**: Step 1c の `test_dedup_keeps_earliest_timestamp_stop_regardless_of_input_order` が、入力順 (09 → 01 → 05) と timestamp 順 (01 → 05 → 09) を意図的に乖離させて min(timestamp) semantic を pin。input-order first-wins では 09 が survive、min(timestamp) では 01 が survive — 入力 design がこの 2 semantic を区別する。
**Tie-break 残余 (reviewer iteration 3 P3)**: 同一 timestamp の duplicate stop が入った場合の survivor は `accepted_dedup_keys` set による「最初の 1 件 (= 入力順 first)」 — ここだけは依然 input-order 依存。Mitigation 不要 (= document only): 同 timestamp の duplicate は実機観察上 wall-clock 衝突の極稀なケースで、survivor の field 値も同一なので production 影響なし (= `agent_transcript_path` 等は同 hook fire 由来で同じ)。完全決定論を求めるなら `(timestamp, subagent_id)` lexicographic を tie-break key にする 1 行追加で対処可能だが、本 PR では over-engineer。

### R4. dashboard regression を「実機 1 セッション運用」抜きで snapshot 比較で代用できるか

**できる**。理由:
- `subagent_ranking` は production の現状 `usage.jsonl` を入力 → `aggregate_subagent_metrics()` を経由 → そのまま dict として forward される thin wrapper。
- snapshot script は dashboard と同じ aggregation path を呼ぶ (= same input, same code).
- `top_n=10` cap (`dashboard/server.py:387`) があるので snapshot 全 type のうち上位 10 件のみ dashboard に出るが、上位 10 type が一致していれば DoD は満たされる (= `top_n` cap の境界が変わるなら snapshot で全 type を比較すれば十分検出される)。

**実機セッション運用が必要なケース**: 「新 hook payload (`agent_transcript_path` 含む) が実機で append される」ことの確認 (= Step 6c)。これは regression check の代わりではなく **DoD 3 項目目 (新 record の shape)** の確認なので、別物として 1 回だけ実行すれば良い。

### R5. 既存テスト改修 (Step 1a 内 `test_subagent_stop_with_duration_and_success` / `test_subagent_stop_with_failure` の置換) の semantic break

**理由**: 既存テストは「(誤った仮定で) `duration_ms` / `success` が event に書き出される」挙動を pin していた。これは Issue #93 で誤った仮定だと判明したため、test 自体を逆向きに書き直す。
**Mitigation**: PR description で「test 改修 (置換) の意図」を明記。test 名を `test_subagent_stop_with_duration_and_success_payload_does_not_persist_them` 等にリネームして readability を保つ。

## 6. Branch 名提案

- Release branch (既存予定): `v0.7.4` (main から事前に切る)
- Feature branch (本対応): `feature/100-subagent-stop-payload-and-dedup`

> CLAUDE.md の release branch model に従い、`v0.7.4` 上に派生 (main 上ではない)。

## 7. Definition of Done (Issue より転記 + 検証可能化)

- [ ] **テスト緑**: `python3 -m pytest tests/` が全 green
- [ ] **drift guard**: `tests/test_record_subagent.py::TestSubagentStopEvent::test_subagent_stop_drops_duration_ms_field_when_provided` が pass (= `_handle_subagent_stop` が `duration_ms` / `success` を出力しない)
- [ ] **capture**: `tests/test_record_subagent.py::TestSubagentStopEvent::test_subagent_stop_captures_agent_transcript_path` が pass
- [ ] **dedup**: `tests/test_subagent_metrics.py::TestSubagentStopAgentIdDedup::test_four_stops_same_agent_id_collapse_to_one_invocation` が pass
- [ ] **dedup semantic**: `tests/test_subagent_metrics.py::TestSubagentStopAgentIdDedup::test_dedup_keeps_earliest_timestamp_stop_regardless_of_input_order` が pass (= min(timestamp) semantic が input-order と乖離した入力で正しく動く)
- [ ] **fixture audit (Step 1.5)**: `tests/test_subagent_quality.py` 内の同 `subagent_id` 共有 fixture (orphan stop 系等) が dedup 追加後も意図された scenario を exercise している (= silent collapse していない) ことを `pytest -v -k orphan` の出力で確認 + PR description に「audit 結果」を 1 行記録
- [ ] **cross-aggregator invariant**: `tests/test_subagent_quality.py::TestAggregateSubagentFailureTrend::test_failure_count_matches_metrics_failure_count` が依然 pass (dedup 追加後も failure_count drift しない)
- [ ] **dashboard regression**: Step 0 で取った baseline snapshot vs Step 6b で取った post snapshot を `diff` し、`subagent_ranking.failure_rate` / `avg_duration_ms` 列の値が完全一致 (= regression 0)。**top-10 boundary** の入れ替わりも全 type 出力 diff が catch する。
- [ ] **新 record shape 実機確認** (Step 6c): `tail -10 usage.jsonl | grep subagent_stop` の最新 event 出力を **PR description の Verification セクションにコピペ** し、レビュアーが `duration_ms`/`success` 不在 + `agent_transcript_path` 存在を目視確認できる状態
- [ ] **docs**: `docs/reference/subagent-invocation-pairing.md` に "Known artifact" セクションが追加され、起票時 137/238 + 全期間 183/307 = 59.6% が pin され、`agent_transcript_path` の SubagentStop 限定 (非対称) も記載されている
- [ ] **docs**: `docs/spec/usage-jsonl-events.md` の `subagent_stop` 例示が新仕様 (no `duration_ms` / no `success` / +`agent_transcript_path`) を反映し、`subagent_type == ""` 構造除外と `agent_transcript_path` の semantic が **現行 contract のみ** で記載されている (= 過去 archive 互換 note は reference に統合済み)

## 8. PR description で必ず明記する事項 (reviewer iteration 2 caveat)

- [ ] **AC wording divergence**: Issue #100 の AC は「`agent_id` dedup ステップを追加 (同 session × 同 `subagent_id` の `subagent_stop` は **first wins** で 1 件化)」と記述されているが、本 PR の実装は **min(timestamp)** dedup に upgrade されている。理由: `scripts/rescan_transcripts.py --append` で input order が timestamp 順と乖離するケースを構造的に救うため (R3 参照)。append-only ordering 下では min(timestamp) ≡ first-wins なので **strict superset** であり、AC を満たすうえに rescan 経路でも正しい挙動。自動 checkbox レビュアーが「first-wins と書いてないから AC 不一致」と読まないよう、PR description に 1 段落で明記する。
- [ ] **Verification 出力**: Step 6c の `tail -10 ~/.claude/transcript-analyzer/usage.jsonl | grep '"event_type": "subagent_stop"' | tail -1 | python3 -m json.tool` の出力。
- [ ] **Snapshot diff**: Step 0 baseline (`/tmp/issue-100-baseline-pre.tsv`) と Step 6b post (`/tmp/issue-100-baseline-post.tsv`) の `diff` 結果 (= 0 行差分の確認)。
- [ ] **Step 1.5 fixture audit 結果**: scan で touch した test の一覧と、各 test の disposition (distinct agent_id 化 / 削除 + supersede / no change で OK)。
