# Cross-platform 対応 — Windows porting + Python launcher trilemma

このプロジェクトを macOS / Linux / Windows すべてで動かすときに踏みやすい defects と、`python` vs `python3` の launcher 選択をめぐる設計空間。Issue #24 / #33 / PR #31 が背景。

---

## §1. Windows porting checklist

`install.sh` から plugin 配置に切り替えた時点で、以下 5 つの defects が macOS/Linux で不可視ながら Windows で破綻する。

### 1. `commands/*.md` も launcher サーフェス

`commands/usage-dashboard.md` / `usage-summary.md` / `usage-export-html.md` の本文は **`python3 ${CLAUDE_PLUGIN_ROOT}/...`** をハードコードしている。`hooks/hooks.json` だけ launcher 規約を変えても、`/usage-dashboard` 等のスラッシュコマンドが Windows でサイレント失敗する。

**ルール**: `${CLAUDE_PLUGIN_ROOT}/...` を resolve するエントリーポイントは `hooks/hooks.json` 本体 **and** `commands/*.md` 本体の **両方** を必ず一貫して扱う。新しい launcher 戦略を入れるときは 28+ 箇所を一度にスイープ（§2 の数値参照）。

### 2. `verify_session.py:_encode_cwd()` の `replace("/", "-")`

```python
# 現状（Windows で no-op になり transcript path 解決が壊れる）
encoded = cwd.replace("/", "-")
```

Windows の `cwd` は `C:\Users\foo\myapp` で forward slash を含まない → `replace("/", "-")` が何もしない → encoded transcript path がサイレントに壊れる。

**正解**: `transcript-format.md` の Windows エンコード規約（`/`, `\`, `:`, `.` をすべて `-`）に従う。`hooks/verify_session.py:_encode_cwd()` に集約。

### 3. JSONL append の改行モード未指定

```python
# ❌ プラットフォーム既定モード（Windows で \r\n になる）
open(path, "a", encoding="utf-8")

# ✅ 全環境で LF 固定
open(path, "a", encoding="utf-8", newline="\n")
```

`open(path, "a", encoding="utf-8")` だけでは Windows 上で `\n` が `\r\n` に翻訳される。JSONL parser の多くは混入を許容するが、macOS で書き始め Windows で続けるとファイル中に EOL が混在する → `gunzip | diff` 等が壊れる。

**ルール**: 共有ファイル append は **常に** `newline="\n"` 明示。OS の text-mode 既定を信用しない。

### 4. `~/.claude` の Windows 規約

```python
Path.home() / ".claude" / "transcript-analyzer"
```

Issue #44 (`hooks/_lock.py` の Windows lock 抽象化) で Windows 実機経路が一通り通った段階で `~/.claude` (= `Path.home() / ".claude"`) を本プロジェクトの data root として確定。Claude Code 本体も Windows で `~/.claude` 配下にトランスクリプトを置く規約のため、`%APPDATA%\Claude` 等への移行は不要。`USAGE_JSONL` / `ARCHIVE_DIR` / `DASHBOARD_SERVER_JSON` の env override で別 root にも振り替えられる（テスト隔離 + 個別ユーザー fallback 用）。

### 5. `dashboard/server.py:remove_server_json()` の compare-and-delete レース

```python
# ❌ 4 ステップが critical section に入っていない
data = json.loads(server_json.read_text())
if data["pid"] == os.getpid():
    server_json.unlink()
```

A: pid=100 を読む → B: クラッシュ → C: pid=300 で起動・新 server.json 書込 → A: `unlink()` → **C の生きているレジストリを A が削除**。

**修正済 (Issue #24 / Issue #44)**: `dashboard/server.py:remove_server_json(expected_pid=...)` の compare-and-delete 化と、`hooks/_lock.py` の `fcntl.flock` (POSIX) / `msvcrt.locking` (Windows) 抽象 lock 層で critical section を構造的に守るようになった。Windows は SH 概念が無いため SH も EX 相当で動作 (concurrency 落ちるが correctness は保たれる)。発端の codex flag は PR #23。

### Windows 監査時の汎用ルール

- `${CLAUDE_PLUGIN_ROOT}/...` を resolve する **すべての** エントリーポイントを列挙してから launcher 戦略を決める
- 任意の `str.replace("/", X)` がパス文字列に対して走っているなら **Windows 即赤信号**。`os.sep` aware か uniform encoding に置き換える
- レジストリ系ファイル（server.json / lockfile / pid file）の compare-and-delete は **常に** ロック付きか rename-then-delete に
- `Path.home()` / `~/.claude` 系の data root はプロジェクト assumption として一度確定し、コメントで pin

---

## §2. Python launcher の trilemma — `python` vs `python3` vs shebang

`hooks/hooks.json` 等で hook スクリプトを launch する際の launcher 選択。`python` / `python3` / shebang のどれも単独では cross-OS で機能せず、Issue #33 に case table がある。

### 現状の選択（Issue #33 解決形）

`hooks/hooks.json` および `commands/*.md` は **両 launcher を fallback chain でブリッジする** 形式を採用：

```bash
"$(command -v python3 || command -v python)" ${CLAUDE_PLUGIN_ROOT}/hooks/foo.py
```

- macOS Homebrew (`python3` のみ存在) → `command -v python3` がヒットして解決
- Windows (`python` のみ存在) → `python3` 不在で `python` にフォールバック
- 両者あり → `python3` 優先 (大半の Linux 配布物で意味的に正しい)

**経緯**: PR #31 で `python3 → python` に倒した時点で macOS Homebrew が壊れる副作用が顕在化 (Issue #33 case A〜H)。最終的に shell の `command -v` を使った両対応 fallback で macOS / Linux / Windows の 3 OS が同じ JSON 行で動くように統一した。Per-OS object dispatch を Claude Code hooks JSON が持たないので、shell 側の解決能力を借りる pragmatic 解。

### 設計空間サマリ

業界の主要パターン：

| アプローチ | 採用例 | 本プロジェクトでの適用可否 |
|---|---|---|
| Per-OS object dispatch（`windows`/`linux`/`osx` キー） | VS Code | ❌ Claude Code hooks JSON にこの構造なし |
| Env-var override | Renovate / Poetry | ❌ Claude Code hooks に env-var 注入経路が標準化されていない |
| Launcher 自動翻訳（`python3 → py -3`） | pre-commit | ❌ Claude Code 側で未提供 |
| Pick-one-launcher | 22 個調査した third-party plugin の **全て** | ❌ どちらか単独だと別 OS が壊れる |
| **Shell-fallback chain** (`command -v python3 \|\| command -v python`) | 本プロジェクト | ✅ 採用（Issue #33 解決形） |

### Launcher 規約の Sweep 対象

launcher 文字列を変える場合は、**すべて同時** に変更する。最新分布：

- `hooks/hooks.json`: **20** occurrences
- `commands/*.md`: **13** occurrences (5 ファイル合計)
- `install/merge_settings.py`: 削除済（Issue #11）

合計 **33 サイト**。1 箇所だけ変えると drift する。grep one-liner で sweep を確認:

```bash
grep -rn 'CLAUDE_PLUGIN_ROOT' hooks/hooks.json commands/*.md
```

### Launcher 戦略を変更する前のチェックリスト

1. Issue #33 の case table（A〜H）を再読
2. Windows GitHub Actions runner（`windows-latest`）で動作確認
3. macOS Homebrew Python（`brew install python3` 単体）で動作確認
4. 上記 33 サイトすべての一括変更パッチを用意

3 つ未確認の段階で実装に進めない。

### 個別ユーザーへの暫定回避（fallback chain 採用後は通常不要）

```bash
# 万が一 python3 / python のどちらも PATH にない極端な環境向け
ln -sf "$(which python3)" ~/.local/bin/python
# PATH に ~/.local/bin を含めればこれで python が解決する
```

shell-fallback chain が landed した現在では、片方の launcher 名のみ提供される標準環境（macOS Homebrew / Windows 純正）はいずれもそのまま動く。

---

## §3. 関連 issue / PR

| 番号 | タイトル / 内容 |
|---|---|
| Issue #24 | Windows porting checklist の発端 |
| Issue #33 | Python launcher 設計空間（case A〜H） — shell-fallback chain で解決 |
| Issue #44 | retention/archive 機構の Windows 対応 (`hooks/_lock.py` で `fcntl.flock` / `msvcrt.locking` 抽象化) |
| Issue #45 | retention/archive の Windows 非対応 caveat（README 追従） |
| PR #31 | `python3 → python` の load-bearing コミット（その後 Issue #33 解決形に置換） |
| PR #23 | `remove_server_json()` レースの codex flag（Issue #24 で fix landed） |

