# Prompt 永続化 — 三層モデル

claude-transcript-analyzer は **slash command 等のユーザ prompt を 3 つの層
で保持** する。各層は寿命と保持内容が異なり、用途も別れている。

「ユーザが本当に何を打ったのか」を replay したい時にどこを見るのか、設計
意図と recovery path をまとめる。

---

## §1. 三層の概観

| 層 | 寿命 | 保持内容 | 用途 |
|---|---|---|---|
| **(1) stdin JSON** | hook プロセス実行中のみ | 完全な hook payload (`prompt` 含む `<command-name>` タグなど) | hook が抽出するソース。プロセス終了で消滅 |
| **(2) `usage.jsonl`** | 永続 (180 日 hot tier + archive) | skill 名 + `source` (expansion / submit) + メタデータのみ。**prompt 本文は意図的に捨てる** | 集計・analytics・dashboard。size / privacy のため digest |
| **(3) Claude Code transcript** | 永続 (Anthropic 側 retention に依存) | 完全 fidelity の `role: "user"` メッセージ (`<command-name>` / `<command-message>` タグ + 展開済本文) | 「実際に打たれたもの」の source of truth。recovery / verification の起点 |

実体パス:

- (1) stdin: hook プロセスの stdin (`hooks/record_skill.py` 等)
- (2) usage.jsonl: `~/.claude/transcript-analyzer/usage.jsonl` + `archive/YYYY-MM.jsonl.gz`
- (3) transcript: `~/.claude/projects/<project-hash>/<session-id>.jsonl`

---

## §2. 設計意図 — なぜ digest

`usage.jsonl` に prompt 本文を保存しない判断は **(a) ファイルサイズ抑制**
**(b) privacy** **(c) digest と full-fidelity の責務分離** の 3 点。

- (a) — 1 prompt あたり数 KB 〜 数十 KB は積もると 180 日 hot tier の容量を
  食う。集計目的では skill 名 + 数フィールドあれば足りる。
- (b) — usage.jsonl はローカルだが、export / 共有経路で flow しうる。
  prompt 本文がそこに混ざると意図せず外に出る経路が増える。
- (c) — full-fidelity 復元が必要な時は transcript を読めばよい。usage.jsonl
  は集計用 digest という単一責務に留める。

逆に **transcript 依存はなるべく hot path から外す** という対称規律もある。
hot path (hook → usage.jsonl) は transcript の format / 保持期間に依存しない:

- Claude Code が transcript format を変えても hook の broad-capture path は
  動く (hook 入力 JSON schema は別経路で安定)
- transcript が rotate / 消えても usage.jsonl は破壊されない

ただし **verification (`hooks/verify_session.py` Stop hook)** と
**recovery (`scripts/rescan_transcripts.py`)** は transcript に依存する。
transcript 不在時は **silent skip** で degrade する (alert は記録される)。

---

## §3. 「ユーザが何を打ったか見たい」時のパス

| 用途 | 見る先 | コマンド |
|---|---|---|
| 完全な user prompt 本文 | transcript (3) | `cat ~/.claude/projects/<hash>/<session>.jsonl \| jq 'select(.type == "user")'` |
| skill 起動の集計 (どの skill が何回 / どの session) | usage.jsonl (2) | `python3 reports/summary.py --skills` |
| 直近 hook の入力 payload デバッグ | hook process の stderr / strace | hook 自体に print debug を仕込む。stdin JSON は走った瞬間に消える |

`usage.jsonl` を見て「prompt 本文がない」とは「層 (2) の設計通り」であって
バグではない。本文が必要なら (3) を見る。

---

## §4. Recovery: 過去のギャップを埋める

usage.jsonl の hook 記録が落ちた時間窓があったら (例: `record_*.py` が
crash した、archive lock contention で `health_alerts.jsonl` に
`append_skipped_due_to_archive_lock` が積まれた、など)、transcript 経由で
**遡及補完** できる。

```bash
# 全 transcripts を rescan して usage.jsonl に追記 (v0.8.0 からデフォルト動作)
python3 scripts/rescan_transcripts.py

# 確定的にクリーンな再生成が要る場合 (全 event 種で重複なし)
python3 scripts/rescan_transcripts.py --overwrite

# その後、180 日超を archive に移して hot tier を整理
python3 scripts/archive_usage.py
# または slash command:
/usage-archive
```

`rescan_transcripts.py` は **`assistant_usage` event について idempotent**
(`(session_id, message_id)` first-wins、v0.8.0 から)。`skill_tool` /
`subagent_start` / `user_slash_command` 等は dedup されないため、確定的に
クリーンな再生成が要るときは `--overwrite` flag を使う。

---

## §5. 制約 — transcript の保持期間

(3) は **Anthropic 側の transcript rotation policy** が ceiling。
recovery 完成度はそこに依存する。

設計上の含意:

- 本リポの archive (180 日超 → cold tier) は **transcript の rotation よりも
  長期** の data retention を保証する経路。usage.jsonl が canonical で、
  transcript は復旧用。
- 「transcript からしか取れない情報」(例: 完全な prompt 本文 / token カウント)
  は transcript が消える前に取り出さないと永久に失う。
- transcript を hot path に使う設計を増やすと、Anthropic の format / retention
  変更でリポ全体が壊れるリスクを背負う。

「broad capture / hot tier 主」「transcript は recovery / verification 補助」
の分担はこの retention 上限ゆえ。詳細は `docs/reference/hook-philosophy.md`
参照。

---

## §6. 新 event type を設計する時の指針

新しい hook event を usage.jsonl に書き足す時、どの層に full-fidelity を
置くかを最初に決める:

- **集計 / dashboard で使うフィールドのみ** → usage.jsonl に digest として
  書く。本文は捨てる。
- **完全本文の永続が要る** (例: 監査ログ) → transcript からの再構築経路を
  整えるか、別 sidecar ファイルを設計する。`usage.jsonl` に raw prompt を
  入れない。
- **デバッグ用に短期保持で良い** → hook process の stderr / log file に
  落とす。usage.jsonl には入れない。

`prompt` のような human-input 大文字列を `usage.jsonl` の event field に
直接入れることは **digest 設計を破壊する** ので避ける。

---

## 関連 reference / spec

- `docs/reference/storage.md` — usage.jsonl の hot/cold tier 設計と dedup 規律
- `docs/reference/hook-philosophy.md` — broad capture vs Stop-only の hook 思想比較
- `docs/spec/usage-jsonl-events.md` — event の正式 schema
- `docs/transcript-format.md` — transcript の生フォーマット + Hook 入力 JSON schema
