# Issue #121 リファクタリング 実装計画 — `analyzer/` パッケージ集約 + プラットフォーム seam 統合

## 📋 plan-reviewer 反映ログ

| Proposal | 内容 | 反映箇所 |
|----------|------|----------|
| (初稿) | — | — |

### 二次レビュー反映 (iteration 1 → 2)

| Proposal | 内容 | 反映箇所 |
|----------|------|----------|
| P1 (actionable) | entry-point dir は薄い起動口であると同時に package-style import 先でもある | Goal 達成基準注記 / 新設「entry-point モジュールの package-style import (残余)」節 / Critical files の触ってはいけないファイル |
| P2 (actionable) | `# noqa: E402` は per-line 必要、leaf あたり複数 import 行 | `hooks/_bootstrap.py` の設計 (正典スニペット修正) / Step 3.4 sub-note |
| P3 (actionable) | `restart_dashboard.py:145` の `!= "win32"` polarity に専用 RED テスト要 | TDD plan §2 / Step 3.1 |
| P4 (actionable) | Step 1.5/1.6 が transient red-suite を作る → `_lock`+`_append` を 1 step に統合 | Ordered steps Step 1.5 (統合) / Step 1.6 削除 / Step 1.7 注記 |
| P5 (advisory) | `analyzer/platform/` と stdlib `platform` の名前衝突確認 | Step 1.1 に grep チェック追加 |

### 三次レビュー反映 (iteration 2 — actionable ゼロ、advisory polish)

| Proposal | 内容 | 反映箇所 |
|----------|------|----------|
| A1 (advisory) | Step 2.1 の `record_assistant_usage` 依存を either/or でなく確定方針に | Step 2.1 (確定方針: 共有コアを `analyzer/` へ移設、leaf 維持案は不採用) / テスト先行書き換え行 |
| A2 (advisory) | Step 3.5 の `"sys.path"` 部分一致 assert を変更系シグネチャに限定 | Step 3.5 / TDD plan §3 (`sys.path.insert`/`sys.path.append` に限定) |
| — | Risk #9 (`export_html.py` 調査未完) を「調査済」に更新 | Risk / tradeoffs #9 |

---

## Goal

repo root に散乱した共有モジュールと `sys.path.insert` ハックを排除し、import 対象ロジックを新設 `analyzer/` パッケージへ集約する。entry-point ディレクトリ (`hooks/` `commands/` `dashboard/` `reports/` `scripts/`) を「薄い起動口」に純化する (ただし一部の entry-point モジュールは他モジュールから package-style に import される**残余クロスパッケージ import 先**を兼ねる — 後述「entry-point モジュールの package-style import (残余)」節を参照。完全な純粋シェルにはならない)。OS 分岐を `analyzer/platform/` に 1 本化する。

達成基準:

1. repo root 直下から共有 `.py` (`cost_metrics.py` / `server_registry.py` / `subagent_metrics.py`) が消える。
2. `analyzer/` パッケージ内部は絶対 import のみ。パッケージ内 `sys.path` ハックゼロ。
3. 残る `sys.path` 行は entry-point leaf のみ。「repo root を足す」同一イディオム 1 行に統一 (`hooks/_bootstrap.py` で吸収)。
4. `if sys.platform == "win32"` の production code 出現が 5 ファイル → 2 ファイル (`analyzer/platform/lock.py` / `analyzer/platform/process.py`) に減る。
5. `pyproject.toml` の `E402` 全域 ignore を撤廃。本当に必要な entry-point leaf の `sys.path` 後 import のみ行末 `# noqa: E402` を残す。
6. **挙動を一切変えない (behavior-preserving)**。各ステップ後に `python3 -m pytest tests/` が緑。

スコープ外: 肥大化モジュール (`dashboard/server.py` 75 KB 等) の責務分割 → #123。本計画は配置のみ変更する。

---

## 現状調査の結果

### `sys.path.insert` を含むファイル (調査実数)

**非テスト: 19 ファイル** (Issue 記載の「19」と一致):

```
cost_metrics.py            server_registry は使わず subagent_metrics を bare import
dashboard/server.py        repo root を足し subagent_metrics / cost_metrics / server_registry
hooks/_append.py           hooks/ を足し _lock を bare import
hooks/launch_archive.py    hooks/ を足し _launcher_common を bare import
hooks/launch_dashboard.py  repo root を足し server_registry を bare import
hooks/record_assistant_usage.py
hooks/record_session.py
hooks/record_skill.py
hooks/record_subagent.py
hooks/verify_session.py    scripts/ を足し rescan_transcripts._scan_transcript_file を bare import (越境)
reports/_archive_loader.py hooks/ を足し _lock を bare import (reports→hooks 越境)
reports/export_html.py
reports/summary.py         repo root を足す。bare/package 混在 import (後述)
scripts/archive_usage.py   hooks/ を足し _lock を bare import
scripts/build_demo_fixture.py
scripts/build_live_diff_fixture.py
scripts/build_surface_fixture.py
scripts/rescan_transcripts.py  hooks/ を足し record_assistant_usage を bare import
scripts/restart_dashboard.py   repo root を足し hooks.launch_dashboard (package) + server_registry (bare) を import
```

**テスト: 19 ファイル** (Issue 記載の「約 19」と一致): `conftest.py` ほか
`test_archive_loader / test_archive_state / test_cost_metrics / test_dashboard_live / test_dashboard_no_archive / test_dashboard_sessions_api / test_export_html / test_hooks_append_lock / test_hooks_python_resolution / test_launch_archive / test_lock / test_model_distribution / test_model_distribution_template / test_record_assistant_usage / test_rescan_assistant_usage / test_rescan_transcripts / test_summary_include_archive / test_verify_session`。
注: 一部テストは関数内 (テストメソッド内) でも `sys.path.insert` を再実行している (`test_archive_state.py:436`, `test_hooks_append_lock.py:105`, `test_lock.py:123` 等) → 書き換え時の見落としリスク。

### root 3 モジュールの依存関係

- `cost_metrics.py` → `subagent_metrics.session_subagent_counts` を bare import (root flat namespace 経由)。
- `subagent_metrics.py` → stdlib のみ。葉。
- `server_registry.py` → stdlib のみ。葉。OS 分岐 (`fcntl` / `msvcrt`) を**自前で持つ**。
- consumers:
  - `dashboard/server.py` → 3 つ全部 import。`server_registry` の内部 private (`_file_lock` / `_lock_path_for` / `_pid_matches` / `_lock_fd` 等) を**再 export して既存テスト互換を維持**している (server.py:36-43)。**この再 export は保持必須**。
  - `reports/summary.py` → `subagent_metrics` / `cost_metrics`。
  - `reports/export_html.py` → (要確認、summary 同等)。
  - `hooks/launch_dashboard.py` / `scripts/restart_dashboard.py` → `server_registry.remove_server_json`。

### `if sys.platform == "win32"` の正確な出現箇所

production code: **5 ファイル** (Issue は「7」と記載するが実数は 5。残り 2 はテストファイル):

```
dashboard/server.py            2 箇所 (server.py:1358 cleanup signal, server.py:1552 allow_reuse_address)
hooks/_launcher_common.py       1 箇所 (spawn_detached の detach 分岐)
hooks/launch_dashboard.py       2 箇所 (_is_pid_alive dispatch:223 / _spawn_server detach:333)
scripts/restart_dashboard.py    1 箇所 (restart_dashboard.py:145 signal 分岐)
server_registry.py              1 箇所 (server_registry.py:23 _lock_fd/_unlock_fd 定義)
```

テスト (移設対象外): `test_dashboard_live.py` / `test_hooks_append_lock.py` / `test_launch_dashboard.py` / `test_restart_dashboard.py`。

**重複の実態**:
- **ファイルロック**: `hooks/_lock.py` (SH/EX 両対応の完全な抽象化、`fcntl`/`msvcrt` を try-import で degrade) と `server_registry.py:23-42` (EX 専用の `_lock_fd`/`_unlock_fd` を `if sys.platform == "win32"` でインライン定義)。**2 実装**。
- **プロセス detach**: `hooks/_launcher_common.py:spawn_detached` (Win flags を getattr fallback で持つ汎用版) と `hooks/launch_dashboard.py:_spawn_server` (`_WIN_DETACHED_PROCESS` 定数と detach kwargs を**丸ごとコピー**、`_launcher_common` を import せず独自実装)。**重複コピー**。

### E402 ignore の設定箇所

`pyproject.toml` (repo root) `[tool.ruff.lint]` セクション:

```toml
[tool.ruff.lint]
ignore = [
    # 旧 .pylintrc の wrong-import-position に対応 (sys.path 操作後 import を許容)
    "E402",
]
```

`ruff.toml` / `setup.cfg` / `.ruff.toml` は**存在しない**。lint 設定は `pyproject.toml` 一本。撤廃方法: Phase 3 で `ignore` リストから `"E402"` を削除し、`[tool.ruff.lint]` を空 (= ruff default の E+F) に戻す。lint 実行は `.github/workflows/ruff.yml` の `astral-sh/ruff-action@v3`。

### plugin entry point のパス直参照箇所

- `hooks/hooks.json`: 全 hook が `${CLAUDE_PLUGIN_ROOT}/hooks/<name>.py` を直参照。対象 leaf: `record_skill.py` / `record_subagent.py` / `record_session.py` / `record_assistant_usage.py` / `verify_session.py` / `launch_dashboard.py` / `launch_archive.py`。
- `commands/*.md`: `${CLAUDE_PLUGIN_ROOT}/reports/export_html.py` / `reports/summary.py` / `scripts/archive_usage.py` / `scripts/restart_dashboard.py` / `hooks/launch_dashboard.py` / `dashboard/server.py` を直参照。
- 起動方式は `"$(command -v python3 || command -v python)" <abs path>/<leaf>.py` の**単体スクリプト起動** (`python -m` ではない)。

**結論**: entry-point leaf のファイルパス・ファイル名は**固定**。これらは移動・改名禁止。leaf 内部の import を `analyzer.*` 絶対 import に書き換えるには `sys.path` に repo root を 1 行足す必要があり、これは制約上**許容される唯一の sys.path**。

### entry-point モジュールの package-style import (残余)

entry-point dir は「薄い起動口」だが、**同時に他モジュールから package-style に import される import 先でもある**。これらは `analyzer/` には移さず entry-point dir に残るため、「薄いシェル」化は不完全 (= 純粋シェルにはならない) であることを明示する。調査で確認した残余クロスパッケージ import:

```
from dashboard.server import build_dashboard_data, render_static_html
    ← reports/export_html.py:18, scripts/build_surface_fixture.py:230, scripts/build_demo_fixture.py:452
from reports.summary import load_events       ← reports/export_html.py:19
from reports._archive_loader import ...       ← reports/summary.py:11  (→ Step 1.7 で analyzer.archive.loader へ)
hooks.launch_dashboard (package import)       ← scripts/restart_dashboard.py
```

各モジュールの扱い:

- **`dashboard/server.py`**: entry-point leaf (Step 3.3 で repo-root sys.path 4 行イディオムを持つ) **かつ** `dashboard.server` として import される package モジュール。本計画では `build_dashboard_data` / `render_static_html` 等の importable ロジックを `analyzer/` へ移さない (それは #123 server.py 責務分割のスコープ)。→ **`dashboard/server.py` は top-level `sys.path` イディオムを持ち、かつ import-reachable のまま残る**。これは意図的判断。`export_html.py` から `dashboard.server` を import すると server.py の top-level `sys.path.insert` が import 副作用として実行されるが、`if _REPO_ROOT not in sys.path` ガードで冪等なので問題ない (Step 3.5 で確認テストを追加)。
- **`reports/summary.py`**: `reports.summary.load_events` が `export_html.py` から import される。`summary.py` 自身は entry-point leaf でもある。importable ロジック (`load_events` 等) は `summary.py` に残す (#121 スコープでは移設しない)。`from reports._archive_loader import` のみ Step 1.7 で `analyzer.archive.loader` へ切り替え。
- **`hooks/launch_dashboard.py`**: `restart_dashboard.py` が `hooks.launch_dashboard` を package import。leaf として残り、import-reachable のまま。
- **`reports/_archive_loader.py`**: Step 1.7 で `analyzer/archive/loader.py` へ移設するため、残余ではなく解消対象。

**Goal の「薄い起動口」は厳密には「薄い起動口 + 残余クロスパッケージ import 先」**。将来の保守者が「薄いシェル」を文字通り受け取り `dashboard/server.py` の importable 関数を移そうとしたり top-level `sys.path` 行を剥がしたりすると `export_html.py` / fixture builder が壊れる。この二面性は意図的設計として固定する。

---

## `analyzer/` パッケージの内部構成 (提案)

flat ではなく責務でサブパッケージに切る。entry-point leaf からの import パスが意味的に読めることを優先する。

```
analyzer/
  __init__.py            空 (パッケージマーカー)
  cost.py                ← cost_metrics.py
  subagent.py            ← subagent_metrics.py
  server_registry.py     ← server_registry.py (名前維持 / 後述の OS 分岐は platform/ へ委譲)
  hot_append.py          ← hooks/_append.py
  archive/
    __init__.py
    usage.py             ← scripts/archive_usage.py のコアロジック (Phase 2)
    loader.py            ← reports/_archive_loader.py
  rescan/
    __init__.py
    transcripts.py       ← scripts/rescan_transcripts.py のコアロジック (Phase 2)
  launcher.py            ← hooks/_launcher_common.py (spawn_detached)
  platform/
    __init__.py
    lock.py              ← hooks/_lock.py + server_registry の lock 分岐を統合
    process.py           ← detach 分岐 (_launcher_common + launch_dashboard) + pid-alive + signal 分岐を統合
```

設計判断:
- `analyzer/server_registry.py` は名前維持。`launch_dashboard` / `restart_dashboard` / `dashboard/server.py` の import 先が `analyzer.server_registry` で意味が通る。
- `analyzer/platform/` は OS 別ファイル分割を**しない**。`lock.py` 内に `if sys.platform == "win32"` を**隣接させて 1 箇所に閉じ込める** (Issue スコープ §2 の方針)。
- `archive/` `rescan/` はサブパッケージ。Phase 2 でコアを移すが、Phase 1 では `loader.py` のみ先行移設 (entry-point の `archive_usage.py` 本体は Phase 2)。
- `analyzer/` 内部は**絶対 import** (`from analyzer.subagent import ...`)。相対 import も可だが、grep 可視性のため絶対 import で統一。

### `hooks/_bootstrap.py` の設計

hook leaf は `python <abs>/record_skill.py` で単体起動されるため、`analyzer` パッケージを import するには repo root を `sys.path` に乗せる必要がある。これを 1 イディオムに集約する。

問題: `from hooks import _bootstrap` 自体が「`hooks/` の親 = repo root が path にある」前提。hook leaf は `hooks/` 配下にあるため、`hooks/` 自身が path に乗っていない単体起動では `import hooks` も解決できない。

**採用案**: leaf は `_bootstrap` を `import` で呼ばず、各 leaf の冒頭に**素の 4 行イディオム**を置く。`hooks/_bootstrap.py` は「同一イディオムの正典 (canonical snippet) を定義・ドキュメント化する場所」とする。leaf 冒頭は:

```python
import sys
from pathlib import Path
_REPO_ROOT = str(Path(__file__).resolve().parents[1])  # entry-point dir -> repo root
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from analyzer.cost import aggregate_session_breakdown  # noqa: E402
from analyzer.subagent import session_subagent_counts  # noqa: E402
import analyzer.server_registry as server_registry  # noqa: E402
```

この `sys.path` 4 行が「repo root を足す同一イディオム」。全 entry-point dir (`hooks/` `reports/` `scripts/` `dashboard/`) は repo root 直下のため `parents[1]` で統一解決される。`hooks/_bootstrap.py` はこのスニペットの単一の参照実装・コメント根拠を持ち、新規 leaf 追加時にコピー元になる。これにより「sys.path 行は leaf のみ」「同一イディオム」を満たす。

**重要 — `# noqa: E402` は per-line**: ruff の E402 は「非 import 文 (`sys.path.insert` 等) の後に来る**すべての**module-level import 行」に発火する。`# noqa` は行単位指定のため、`sys.path.insert` ブロックより後ろの **import 文 1 行ごとに** `# noqa: E402` が要る (上記スニペットのように複数行)。leaf あたりの `# noqa: E402` 個数 = 「`sys.path` ブロック後の import 文の本数」であり、leaf の本数ではない。例えば `dashboard/server.py` は現状 `from subagent_metrics import (...)` / `from cost_metrics import ...` / `import server_registry` の 3 import を持つため (server.py:19-36 で確認)、`analyzer.*` 化後も **3 行以上**の `# noqa: E402` が必要。`reports/summary.py` も同様に複数行。stdlib import (`import sys` 等) は `sys.path.insert` より前にあるため E402 抵触なし。

---

## Critical files

### 移設対象 (root / entry-point dir → `analyzer/`)

| 移設元 | 移設先 | Phase |
|--------|--------|-------|
| `cost_metrics.py` | `analyzer/cost.py` | 1 |
| `subagent_metrics.py` | `analyzer/subagent.py` | 1 |
| `server_registry.py` | `analyzer/server_registry.py` | 1 |
| `hooks/_append.py` | `analyzer/hot_append.py` | 1 |
| `hooks/_lock.py` | `analyzer/platform/lock.py` | 1 (Phase 3 で server_registry 分岐統合) |
| `hooks/_launcher_common.py` | `analyzer/launcher.py` (+ Phase 3 で `platform/process.py`) | 1 |
| `reports/_archive_loader.py` | `analyzer/archive/loader.py` | 1 |
| `scripts/archive_usage.py` コア | `analyzer/archive/usage.py` | 2 |
| `scripts/rescan_transcripts.py` コア | `analyzer/rescan/transcripts.py` | 2 |

### import 書き換え対象 (移設はしないが import 文を直す)

- entry-point leaf: `hooks/record_*.py` (4), `hooks/verify_session.py`, `hooks/launch_dashboard.py`, `hooks/launch_archive.py`, `dashboard/server.py`, `reports/summary.py`, `reports/export_html.py`, `scripts/restart_dashboard.py`, `scripts/build_*_fixture.py` (3), `scripts/archive_usage.py` (薄ラッパ化後), `scripts/rescan_transcripts.py` (薄ラッパ化後)。
- テスト 19 ファイル全部 (`sys.path` 行と `import` 文)。

### 触ってはいけないファイル

- `hooks/hooks.json` — plugin entry point パス固定。leaf のファイル名・パスを変えないため変更不要。
- `commands/*.md` — 同上 (パス参照テキスト)。
- `.claude-plugin/plugin.json` / `marketplace.json` — plugin メタ。
- `dashboard/template/` 配下の HTML/CSS/JS。
- `dashboard/server.py` の `server_registry` private 再 export ブロック (server.py:30-43) の**意図**は維持 (import 元が `analyzer.server_registry` に変わるだけ)。

---

## TDD test plan (実装より先に記述)

このリファクタは behavior-preserving が大原則のため、**新規テストより「既存スイートが各ステップで緑のまま」がセーフティネットの中心**。

### 1. 回帰検出セーフティネット (既存スイート)

各ステップ後に `python3 -m pytest tests/ -q` を必ず実行し緑を確認する。特に以下が回帰検出の要:

- `test_cost_metrics.py` / `test_model_distribution*.py` — `cost.py` 移設後の import 健全性。
- `test_subagent_metrics.py` / `test_subagent_quality.py` — `subagent.py` 移設後。
- `test_dashboard_live.py` / `test_dashboard_router.py` / `test_dashboard.py` / `test_dashboard_sessions_api.py` — `server.py` の `server_registry` 再 export とロック共有が壊れていないことの最強の検出器。
- `test_lock.py` / `test_hooks_append_lock.py` — `_lock` → `analyzer/platform/lock.py` 移設後。
- `test_launch_archive.py` / `test_launch_dashboard.py` / `test_restart_dashboard.py` — detach / launcher 経路。
- `test_verify_session.py` / `test_rescan_transcripts.py` / `test_rescan_assistant_usage.py` — 越境依存解消後の回帰。
- `test_archive_loader.py` / `test_archive_usage.py` / `test_archive_state.py` / `test_summary_include_archive.py` — archive 移設後。
- `test_plugin_structure.py` — plugin entry point パス固定の検証 (移動禁止ファイルが動いていないか)。

### 2. 新規ユニットテスト (`analyzer/platform/` seam — TDD で先に書く)

Phase 3 で `analyzer/platform/lock.py` / `process.py` に OS 分岐を統合する際、**統合前に失敗するテストを先に書く**:

- `tests/test_platform_lock.py` (新規) — `analyzer.platform.lock` の `shared_lock` / `exclusive_lock` / `acquire_*` / `release` / `open_lock_file` の挙動。既存 `test_lock.py` の assertion を移植 + `server_registry` が使う EX-only API も同 module から取れることを検証。`fcntl` 不在 / `msvcrt` 不在の degrade 経路を `sys.modules` mock で両分岐とも踏む。
- `tests/test_platform_process.py` (新規) — `analyzer.platform.process` の `spawn_detached` (POSIX/Win 両分岐を `sys.platform` mock で), `is_pid_alive` (POSIX/Win dispatch), signal 分岐ヘルパ。`_launcher_common` と `launch_dashboard._spawn_server` の重複を統合した先の単一実装を検証。
- **signal polarity 専用 RED テスト (P3)**: `scripts/restart_dashboard.py:145` は `if sys.platform != "win32":` という**逆極性**チェック (他箇所の `== "win32"` と向きが逆)。逆極性分岐を共有ヘルパへ畳む際は polarity bug が最も入りやすい。`test_platform_process.py` に**明示的に名前付きの RED テスト**を置く: 例 `test_signal_helper_posix_sends_signal` (POSIX で SIGTERM 等が送られる) / `test_signal_helper_win32_no_posix_signal` (Win では POSIX signal を送らない経路)。`sys.platform` を両値で mock し、`restart_dashboard.py:145` の `!= "win32"` 挙動が helper 抽出後も**同一極性で保たれる**ことを assert。これを実装より先に RED で書く。
- これら新規テストは「先に書いて RED → seam module 実装で GREEN」の TDD 順を厳守。

### 3. import パス検証テスト (新規、推奨)

- `tests/test_analyzer_package_imports.py` (新規) — `analyzer` 配下の全モジュールが `importlib.import_module` で副作用なく import できることを検証。`analyzer/` パッケージ内に `sys.path.insert` / `sys.path.append` が 1 つも無いことを `Path.read_text` で検証 (変更系シグネチャに絞る)。これは「パッケージ内 sys.path ハックゼロ」のリグレッションガード。
- root に `cost_metrics.py` / `server_registry.py` / `subagent_metrics.py` が**存在しないこと**を assert (移設完了の検証)。

### 4. lint ガード

- Phase 3 完了時、ローカルで `ruff check .` を実行し E402 撤廃後にエラーゼロを確認 (entry-point leaf の `# noqa: E402` 残し分を除く)。CI の `ruff.yml` が最終ゲート。

---

## Ordered steps

各ステップは behavior-preserving。ステップ末尾で必ず `python3 -m pytest tests/ -q` 緑を確認する。**TDD: テスト変更を伴うステップは「テストを先に直す/書く」を実装より前に置く**。

### Step 0 — ブランチ準備

1. `git checkout main && git pull` で main を最新化。
2. `git checkout -b v0.8.2 main` → `git push -u origin v0.8.2` (milestone v0.8.2 のリリースブランチを main から新規作成)。
3. `git checkout -b feature/121-analyzer-package v0.8.2` (作業ブランチを v0.8.2 から分岐)。
4. PR は最終的に `feature/121-analyzer-package` → `v0.8.2` で 1 本。

### Phase 1 — `analyzer/` 作成 + root 3 モジュール + hook/reports ヘルパ移設

> 方針: 1 モジュールずつ移設。各モジュール移設は「`git mv` → import 書き換え → テスト緑」を 1 単位とし、minimal-diff を保つ。

**Step 1.1 — パッケージ骨格作成 + 名前衝突チェック**
- `analyzer/__init__.py`, `analyzer/platform/__init__.py`, `analyzer/archive/__init__.py`, `analyzer/rescan/__init__.py` を空ファイルで作成 (末尾空行)。
- **`analyzer/platform/` の名前衝突チェック (P5)**: 移設対象ファイル群に対し `grep -rn "import platform" <移設対象>` を実行し、stdlib `platform` モジュール (`platform.system()` 等) を import しているコードが無いことを確認する。Python 3 は absolute import がデフォルトのため `analyzer/platform/` サブパッケージ名は本来安全だが、stdlib import が混在すると混乱・tooling edge case の元になる。clean なら「Py3 absolute-import semantics 下で安全」と注記。万一ヒットしたらサブパッケージ名を `analyzer/osseam/` 等に変更 (この判断は Step 1.1 時点で確定させる — Phase 3 で発覚すると全 `analyzer.platform.*` import を触る羽目になる)。
- テスト: 影響なし。スイート緑のまま。

**Step 1.2 — `subagent_metrics.py` → `analyzer/subagent.py`** (葉から先に。依存が無いため最小リスク)
- `git mv subagent_metrics.py analyzer/subagent.py`。
- consumers の import 書き換え:
  - `cost_metrics.py`: `from subagent_metrics import session_subagent_counts` → `from analyzer.subagent import session_subagent_counts` (この時点では `cost_metrics.py` はまだ root にあり sys.path で repo root を持つ)。
  - `dashboard/server.py`: `from subagent_metrics import (...)` → `from analyzer.subagent import (...)`。
  - `reports/summary.py`: `from subagent_metrics import aggregate_subagent_metrics` → `from analyzer.subagent import ...`。
  - `reports/export_html.py`: 同様 (要 grep 確認)。
- テスト書き換え (実装より先): `test_subagent_metrics.py` / `test_subagent_quality.py` / `test_dashboard_sessions_api.py` の `from subagent_metrics import ...` を `from analyzer.subagent import ...` に。
- テスト緑確認。

**Step 1.3 — `cost_metrics.py` → `analyzer/cost.py`**
- `git mv cost_metrics.py analyzer/cost.py`。
- `analyzer/cost.py` 内部: 既存の `if str(_ROOT) not in sys.path: sys.path.insert(...)` + `from subagent_metrics import ...` を**削除**し、`from analyzer.subagent import session_subagent_counts` に置換 (パッケージ内絶対 import / sys.path ハック除去)。
- consumers: `dashboard/server.py` の `from cost_metrics import ...` → `from analyzer.cost import ...`。`reports/summary.py` の関数内 `from cost_metrics import aggregate_session_breakdown` → `from analyzer.cost import ...`。
- テスト先行書き換え: `test_cost_metrics.py` / `test_model_distribution.py` / `test_model_distribution_template.py` / `test_dashboard_sessions_api.py` の `from cost_metrics import ...` を書き換え。`test_cost_metrics.py:15` 等の `sys.path.insert(ROOT)` は repo root を足す形に統一 (後述 Step 3.3 で最終整理だが、import 先変更により repo root が必要なので残す)。
- テスト緑確認。

**Step 1.4 — `server_registry.py` → `analyzer/server_registry.py`**
- `git mv server_registry.py analyzer/server_registry.py`。OS 分岐は**この時点では触らない** (Phase 3 で統合)。
- consumers:
  - `dashboard/server.py`: `import server_registry` → `import analyzer.server_registry as server_registry` (再 export ブロック `_file_lock = server_registry._file_lock` 等は alias 名で**そのまま動く**)。
  - `hooks/launch_dashboard.py`: `from server_registry import remove_server_json` → `from analyzer.server_registry import remove_server_json`。
  - `scripts/restart_dashboard.py`: `from server_registry import remove_server_json` → `from analyzer.server_registry import ...`。
- テスト先行書き換え: `test_dashboard_live.py:23` の `import server_registry` → `import analyzer.server_registry as server_registry`。`test_restart_dashboard.py` / `test_launch_dashboard.py` で `server_registry` を参照する箇所も。
- テスト緑確認。

**Step 1.5 — `hooks/_lock.py` + `hooks/_append.py` を一括移設** (P4: 2 ファイルは 1 step で移す)
> `_lock` と `_append` は分離して移すと transient red-suite が発生するため**一括**で移す。理由: Step 1.5 で `_lock` のみ移し `_append.py` の import を `from analyzer.platform import lock` に書き換えても、その時点の `_append.py` はまだ `hooks/` 配下で `hooks/` のみを `sys.path` に足しており repo root が path に無い → `analyzer.platform.lock` が解決できずスイートが赤くなる。`_append` は `_lock` の (in-`hooks`) 唯一の consumer なので、両者を同一 step で移して invariant 「step 末尾でスイート緑」を守る。
- `git mv hooks/_lock.py analyzer/platform/lock.py` (Phase 1 では純移設のみ。server_registry 分岐統合は Phase 3)。
- `git mv hooks/_append.py analyzer/hot_append.py`。
- `analyzer/hot_append.py` 内部: `sys.path.insert(hooks/)` + `import _lock` を**削除**し `from analyzer.platform import lock as _lock` に (絶対 import / ハック除去)。
- その他 `_lock` consumers の import 書き換え:
  - `reports/_archive_loader.py`: `sys.path.insert(hooks/)` + `import _lock` → `from analyzer.platform import lock as _lock`。
  - `scripts/archive_usage.py`: 同様。
- `_append` consumers: `hooks/record_*.py` の `_append` import を書き換え。これら record hook は entry-point leaf なので、既存 `sys.path.insert(hooks/)` を `sys.path.insert(repo root)` に差し替えて `from analyzer.hot_append import append_event` に (leaf イディオムの正典化は Step 3.3)。
- テスト先行書き換え: `test_lock.py` の `import _lock` を `from analyzer.platform import lock as _lock` に (関数内 `sys.path.insert` + `import _lock` の重複箇所 `test_lock.py:123,154,189,265` も全て)。`test_hooks_append_lock.py` の `import _lock` / `import _append` 系 (関数内重複 `:105,107,246,248,281,292,303` 含む) を `analyzer.platform.lock` / `analyzer.hot_append` に。`test_archive_loader.py` / `test_summary_include_archive.py` の `import _lock`。`test_record_*` で `_append` を参照する箇所も grep で洗う。
- テスト緑確認。

**Step 1.6 — `hooks/_launcher_common.py` → `analyzer/launcher.py`**
- `git mv hooks/_launcher_common.py analyzer/launcher.py`。OS 分岐は Phase 3 で `platform/process.py` に統合するため Phase 1 では純移設。
- consumer は `hooks/launch_archive.py` (entry-point leaf) のみ。`launch_archive.py` の `sys.path.insert(hooks/)` を repo-root sys.path に差し替え、`from _launcher_common import spawn_detached` → `from analyzer.launcher import spawn_detached` に。consumer が leaf 1 つだけのため P4 のような transient red-suite は発生しない (`launch_archive.py` 自身が repo root を path に足す)。
- テスト先行書き換え: `test_launch_archive.py` の `import launch_archive` 周辺と `_launcher_common` 参照を確認・書き換え。
- テスト緑確認。

**Step 1.7 — `reports/_archive_loader.py` → `analyzer/archive/loader.py`**
- `git mv reports/_archive_loader.py analyzer/archive/loader.py`。
- 内部: `sys.path.insert(hooks/)` + `import _lock` → `from analyzer.platform import lock as _lock` (越境 reports→hooks を構造的に解消)。
- consumers: `reports/summary.py` の `from reports._archive_loader import ...` → `from analyzer.archive.loader import ...` (bare/package 混在の package 側を解消)。`reports/export_html.py` も同様。
- テスト先行書き換え: `test_archive_loader.py` の `sys.path.insert(PROJECT_ROOT/hooks)` 削除、import を `from analyzer.archive.loader import ...` に。`test_summary_include_archive.py` / `test_export_html_include_archive.py` も。
- テスト緑確認。
- **Phase 1 完了チェックポイント**: root 直下に共有 `.py` が 0 (`cost_metrics`/`subagent_metrics`/`server_registry` 消滅)。`analyzer/` 内部に `sys.path` ハック 0。`git mv` で履歴保持。フルスイート緑。

### Phase 2 — `archive_usage` / `rescan_transcripts` コア移設 + 越境依存解消

**Step 2.1 — `rescan_transcripts` コアを `analyzer/rescan/transcripts.py` へ**
- `scripts/rescan_transcripts.py` のコア純関数群 (`_scan_transcript_file`, `_extract_events_from_row`, `scan_all`, `write_events_with_dedup`, `derive_valid_agent_ids_from_transcript`, `scan_assistant_usage_for_session` 等) を `analyzer/rescan/transcripts.py` へ移す。
- **`record_assistant_usage` 依存の解消 (確定方針)**: `scripts/rescan_transcripts.py:21` は `from record_assistant_usage import (...)` で hook leaf `hooks/record_assistant_usage.py` から関数を import している (依存方向は rescan → leaf)。`analyzer/rescan/transcripts.py` は package モジュールで sys.path ハック禁止のため、leaf からの bare import は持ち込めない。→ **着手時に `rescan_transcripts` が `record_assistant_usage` から import している関数を特定し、その共有コアを `analyzer/` 側へ移設する** (規模に応じて `analyzer/rescan/` 内の新規モジュール、または `analyzer/` 直下)。`hooks/record_assistant_usage.py` は entry-point leaf として薄く残し、移設後のコアを `from analyzer...` で参照する形にする。「leaf が import される形を維持」する案 (rescan が leaf を import し続ける) は**採らない** — P1 が指摘した越境と同型のため。
- `scripts/rescan_transcripts.py` は `main()` + argparse のみ残す**薄いラッパ**にし、`from analyzer.rescan.transcripts import *` 相当でコアを呼ぶ。`commands` 側のパス参照が `scripts/rescan_transcripts.py` 固定のため**ファイルは残す**。
- テスト先行書き換え: `test_rescan_transcripts.py` (`import rescan_transcripts as rs`) を `from analyzer.rescan import transcripts as rs` に。`test_rescan_assistant_usage.py` も。`record_assistant_usage` の共有コア移設に伴い `test_record_assistant_usage.py` の import も追従。
- テスト緑確認。

**Step 2.2 — `verify_session.py` → `scripts` 越境の解消**
- `hooks/verify_session.py` の `sys.path.insert(scripts/)` + `from rescan_transcripts import _scan_transcript_file` を、`from analyzer.rescan.transcripts import _scan_transcript_file` に置換。これで `hooks/` → `scripts/` の越境が消える (両者とも `analyzer/` の公開ロジックを参照する形)。
- `verify_session.py` は hook leaf なので冒頭の repo-root sys.path イディオムを経由。
- テスト先行書き換え: `test_verify_session.py` の `sys.path.insert(_HOOKS_DIR)` + `import verify_session` を、repo-root sys.path + `import verify_session` (hook leaf 直 import は `hooks/` を path に乗せる必要があるため `parents[1]/"hooks"` の扱いを Step 3.3 で統一)。
- テスト緑確認。

**Step 2.3 — `archive_usage` コアを `analyzer/archive/usage.py` へ**
- `scripts/archive_usage.py` のコア (`run_archive`, `_partition_events`, `_merge_with_existing_archive`, `YearMonth`, `ArchivePaths`, `_atomic_*`, `_read_state`/`_write_state` 等) を `analyzer/archive/usage.py` へ。`import _lock` → `from analyzer.platform import lock`。
- `scripts/archive_usage.py` は `main()`/argparse のみの薄ラッパに (commands パス固定のためファイル残す)。
- `hooks/launch_archive.py` が archive_usage を import しない設計 (launch_archive.py:119 のコメント「launcher は archive_usage を import しない」) は維持。
- テスト先行書き換え: `tests/conftest.py` の `sys.path.insert(scripts/)` + `import archive_usage` → `from analyzer.archive import usage as archive_usage` (`importlib.reload` 対象も変更)。`test_archive_usage.py` / `test_archive_state.py` (関数内 `sys.path.insert` `:436` 含む) / `test_archive_smoke.py`。
- テスト緑確認。
- **Phase 2 完了チェックポイント**: `verify_session`→`scripts` 越境消滅。`archive`/`rescan` コアが `analyzer/` 配下。entry-point の `archive_usage.py`/`rescan_transcripts.py` は薄ラッパ化。フルスイート緑。

### Phase 3 — プラットフォーム seam 統合 + sys.path イディオム統一 + E402 撤廃

**Step 3.1 — `analyzer/platform/process.py` 新設 (TDD: テスト先行)**
- まず `tests/test_platform_process.py` を書く (RED) — `spawn_detached`, `is_pid_alive` (POSIX/Win dispatch), signal 分岐ヘルパの期待挙動。**signal polarity の名前付き RED テスト (`test_signal_helper_posix_*` / `test_signal_helper_win32_*`、TDD §2 参照) を必ず含める**。
- `analyzer/platform/process.py` を実装 (GREEN):
  - `analyzer/launcher.py` の `spawn_detached` を `process.py` に移動 (または `process.py` に統合し `launcher.py` を薄く再 export)。
  - `hooks/launch_dashboard.py` の `_spawn_server` の detach 重複コピー (`_WIN_DETACHED_PROCESS` 定数 + detach kwargs) を**削除**し、`process.spawn_detached` を呼ぶ形に。`_is_pid_alive` / `_is_pid_alive_windows` / `_is_pid_alive_posix` / `_win_kernel32` を `process.py` へ移し、`launch_dashboard.py` は `from analyzer.platform.process import is_pid_alive` を呼ぶだけに。
  - `scripts/restart_dashboard.py:145` の `if sys.platform != "win32"` signal 分岐を `process.py` のヘルパに寄せる。**逆極性チェックのため polarity bug 注意** — 上記の `test_signal_helper_*` RED テストを先に書いてから抽出する。
  - **fallback 判断**: `restart_dashboard.py` の signal ロジックが安全に抽出できないほど絡まっている場合、その 1 分岐は**そのまま残し**、`dashboard/server.py` の 2 箇所と並ぶ 3 つ目の残余 `sys.platform` site として明示記録する (無理に `process.py` へ畳まない)。この場合 production の `sys.platform` 出現は最終 4 ファイルになり、その差はハンドオフ / Step 3.2 の spec 乖離注記に追記する。
- テスト書き換え: `test_launch_dashboard.py` / `test_restart_dashboard.py` で `_spawn_server`/`_is_pid_alive` を monkeypatch している箇所を新 import パスに追従。
- テスト緑確認。`sys.platform == "win32"` の出現が `launch_dashboard.py` (2→0)、`launcher.py`、`restart_dashboard.py` (1→0、ただし上記 fallback 採用時は据え置き) から消える。

**Step 3.2 — `analyzer/platform/lock.py` への server_registry 分岐統合 (TDD: テスト先行)**
- まず `tests/test_platform_lock.py` を書く (RED) — `lock.py` が SH/EX 両 API + server_registry が必要とする EX-only ヘルパ (`file_lock` context manager 相当) を提供することを検証。
- `analyzer/server_registry.py` の `if sys.platform == "win32"` (`_lock_fd`/`_unlock_fd` インライン定義) を**削除**し、`analyzer/platform/lock.py` の API (`acquire_exclusive`/`release` 等) を使う形に書き換え。`server_registry._file_lock` は `lock.py` の `exclusive_lock` context manager を委譲呼び出しする実装に。
- 注意: `dashboard/server.py` が `server_registry._file_lock` / `_lock_path_for` / `_pid_matches` を再 export しテストが monkeypatch している (server.py コメントの「内部実装の monkeypatch は server_registry に対して行う」)。再 export の**シンボル名と所在 (`analyzer.server_registry`) は維持**し、内部実装だけ `lock.py` 委譲に変える。`test_dashboard_live.py` のロック関連 monkeypatch が壊れないか重点確認。
- テスト緑確認。`sys.platform == "win32"` の production 出現が `server_registry.py` から消え、`dashboard/server.py` (2 箇所、cleanup signal + allow_reuse_address) のみ残る。最終的に production の `sys.platform == "win32"` は `analyzer/platform/lock.py` + `analyzer/platform/process.py` + `dashboard/server.py` の 3 ファイル。

  > **plan-reviewer 確認ポイント / 要ユーザー確認 (#121 spec 乖離)**: Issue は「`sys.platform == "win32"` の出現が 7 → 2 ファイル」を掲げるが、調査実数は production 5 ファイル (テスト 4 を含めれば 9)。`dashboard/server.py` の `allow_reuse_address`/cleanup signal の 2 箇所は OS 分岐の**重複ではなく** server.py 固有のロジックで、#123 (server.py 責務分割) のスコープに属する。本計画は「OS 分岐の**重複**を排除し seam を `analyzer/platform/` の 2 ファイルに集約」を達成基準とし、`dashboard/server.py` 固有 2 箇所は #123 へ送る (Issue スコープ外注記と整合)。最終 `sys.platform` production 出現は `platform/lock.py` + `platform/process.py` + `dashboard/server.py` = 3 ファイル。Issue 本文の「2 ファイル」とは 1 ファイル差。**この乖離はユーザー判断事項としてハンドオフで明示する**。

**Step 3.3 — sys.path イディオム統一 + `hooks/_bootstrap.py` 正典化**
- `hooks/_bootstrap.py` を新設 (前述「設計」の通り、同一イディオムの参照実装 + コメント根拠)。
- 全 entry-point leaf (`hooks/record_*.py`, `verify_session.py`, `launch_dashboard.py`, `launch_archive.py`, `dashboard/server.py`, `reports/summary.py`, `reports/export_html.py`, `scripts/restart_dashboard.py`, `scripts/build_*_fixture.py`, `scripts/archive_usage.py`, `scripts/rescan_transcripts.py`) の `sys.path` 行を**「repo root を足す同一 4 行イディオム」に統一**:
  ```python
  import sys
  from pathlib import Path
  _REPO_ROOT = str(Path(__file__).resolve().parents[1])
  if _REPO_ROOT not in sys.path:
      sys.path.insert(0, _REPO_ROOT)
  from analyzer.<...> import ...  # noqa: E402   ← sys.path 後の import 文 1 行ごとに付与
  ```
  全 entry-point dir は repo root 直下のため `parents[1]` で統一解決される。`hooks/` `scripts/` 個別を足す形 (`PROJECT_ROOT/"scripts"` 等) は廃止。`# noqa: E402` は per-line 指定なので、`sys.path` ブロック後の **import 文すべてに 1 行ずつ**付ける (P2 — 詳細は「`hooks/_bootstrap.py` の設計」節)。
- テストの `sys.path.insert` 19 ファイルを統一: `conftest.py` で repo root を 1 回足す形に集約できるなら集約。個別テストの `sys.path.insert(ROOT)` / `sys.path.insert(HOOKS_DIR)` / `sys.path.insert(SCRIPTS_DIR)` を、`analyzer.*` 絶対 import 化により**原則不要にして削除**。hook leaf を直 import するテスト (`test_verify_session.py` の `import verify_session`, `test_launch_archive.py` の `import launch_archive` 等) のみ `hooks/` を path に足す必要が残るため、`conftest.py` で `hooks/` / `scripts/` / `dashboard/` を 1 箇所に集約 (テストは plugin 制約対象外なので conftest 集約が許容される)。
- **関数内 `sys.path.insert` の見落とし防止**: `test_archive_state.py:436`, `test_hooks_append_lock.py:105`, `test_lock.py:123,154,189,265`, `test_model_distribution.py:193`, `test_record_assistant_usage.py` 内の重複を grep で全数洗い出し、`grep -rn 'sys.path' tests/` の結果が conftest の 1 箇所 (+ 必要な leaf 直 import 用集約) のみになることを確認。
- テスト緑確認。

**Step 3.4 — E402 全域 ignore 撤廃 (確定方針)**
- `pyproject.toml` の `[tool.ruff.lint]` から `"E402"` を削除。コメントも更新 (「sys.path 操作後 import を許容」→「entry-point leaf のみ行単位 noqa」)。`[tool.ruff.lint]` セクションは ruff default (E+F) に戻る。
- ローカルで `ruff check .` を実行。`analyzer/` 内部は sys.path ハックゼロのため E402 は出ないはず。entry-point leaf の `sys.path` 後 import で出る E402 のみ、その import 行末に `# noqa: E402` を**個別付与** (全域 ignore ではなく行単位)。
- **`# noqa: E402` の付与数 = leaf 数ではなく「sys.path ブロック後の import 文の本数」(P2)**: ruff の E402 は `sys.path.insert` 後のすべての module-level import 行に発火する。`dashboard/server.py` のように `analyzer.*` import が 3 本以上ある leaf は **3 行以上**の `# noqa: E402` が要る。`reports/summary.py` も複数行。各 leaf で `ruff check <leaf>` を実行し、E402 が 0 になるまで全 import 行に `# noqa` を付け切る (1 行だけ付けて他が漏れると CI `ruff.yml` が赤くなる)。
- E402 撤廃で**新たに顕在化する他の lint エラー**(E402 ignore がマスクしていた可能性のある E/F 系) があれば、behavior-preserving の範囲で最小修正。挙動を変える修正が必要なら別 issue に切り出し本 PR では `# noqa` で明示保留。
- `ruff check .` がクリーン (leaf の意図的 `# noqa: E402` のみ) になることを確認。CI `ruff.yml` で最終検証。

**Step 3.5 — import パス検証テスト追加 + 最終確認**
- `tests/test_analyzer_package_imports.py` を追加: `analyzer` 全モジュール import 成功 + `analyzer/` 配下に `sys.path.insert` / `sys.path.append` (ハックの実シグネチャ) 不在を assert + root に `cost_metrics.py`/`server_registry.py`/`subagent_metrics.py` 不在を assert。※ 単純な `"sys.path"` 部分一致だとコメントや読み取り専用行で false-positive するため、変更系の `sys.path.insert`/`sys.path.append` に絞る。
- 全プラットフォーム想定: `python3 -m pytest tests/ -q` 緑、`ruff check .` クリーン。
- `git log` で全 Phase の `git mv` 履歴が保持されていること、minimal-diff であることを確認。
- PR 作成: `feature/121-analyzer-package` → `v0.8.2`、単一 PR。

---

## Risks / tradeoffs

1. **plugin entry point パス固定との衝突**: `hooks/hooks.json` / `commands/*.md` が `${CLAUDE_PLUGIN_ROOT}/hooks/<leaf>.py` 等を直参照。leaf を `analyzer/` に移すと plugin が壊れる。→ **緩和**: leaf ファイルは移動・改名せず「薄い起動口」として残し、内部 import のみ `analyzer.*` に変える。`archive_usage.py`/`rescan_transcripts.py` も薄ラッパ化でファイルを残す。`test_plugin_structure.py` で回帰検出。

2. **hook 単体起動 (`python -m` 不可) と `analyzer` パッケージ import の両立**: hook は `python <abs>/<leaf>.py` 起動で `analyzer` が見えない。→ **緩和**: leaf 冒頭の repo-root sys.path 4 行イディオムを唯一許容。`hooks/_bootstrap.py` を正典化。この 1 行群が「残る sys.path は leaf のみ」の意図的な例外。

3. **import グラフ破壊**: 1 モジュール移設で複数 consumer の import が同時に壊れる。→ **緩和**: Phase 1 を「葉から (`subagent`→`cost`→`server_registry`→...) 1 モジュールずつ `git mv` → 全 consumer 書き換え → テスト緑」の単位に分割。各 Step で必ず `pytest` 緑を確認し、壊れたら即特定できる粒度。

4. **テストファイル約 19 個の sys.path 書き換え漏れ**: トップレベルだけでなく**テストメソッド内**にも `sys.path.insert` が散在 (`test_archive_state.py:436`, `test_lock.py:123/154/189/265`, `test_hooks_append_lock.py:105`, `test_model_distribution.py:193` 等)。→ **緩和**: Step 3.3 完了後に `grep -rn 'sys.path' tests/` の残存が「conftest の集約 1〜数箇所のみ」になることを明示的に検証。関数内の重複も grep で全数洗う。

5. **`dashboard/server.py` の `server_registry` private 再 export 互換**: server.py が `_file_lock`/`_lock_path_for`/`_pid_matches` を `server_registry` から再 export し、テストがそれを monkeypatch する (binding は値コピーのため `server_registry` 側を差し替える設計)。→ **緩和**: Step 1.4/3.2 で再 export のシンボル名と所在 (`analyzer.server_registry`) を厳守。`test_dashboard_live.py` のロック monkeypatch を重点回帰チェック。

6. **E402 撤廃で新たな lint エラーが顕在化**: `E402` 全域 ignore が他の `E402` 違反 (意図しない箇所) や、ignore に紛れていた問題をマスクしていた可能性。撤廃後に CI `ruff.yml` が赤くなるリスク。→ **緩和**: Step 3.4 をローカル `ruff check .` 先行実行で潰す。leaf の意図的 `sys.path` 後 import は**行単位 `# noqa: E402`** に切り替え (全域 ignore からの正しい移行)。behavior-preserving に反する修正が必要なら別 issue に分離し本 PR では `# noqa` 明示保留。

7. **OS 分岐統合の behavior 差異 (Phase 3)**: `server_registry` の lock は EX-only・別ファイル `.lock` 経由・取得失敗で `yield False`。`_lock.py` は SH/EX 両対応・degrade no-op あり。両者の**semantics が微妙に違う** (server_registry は取得失敗時に呼び出し側を安全側に倒す設計、`_lock` は degrade)。安易に統合すると Windows の TOCTOU race 解消 (Issue #24) が壊れる。→ **緩和**: Step 3.2 は TDD で `test_platform_lock.py` を先に書き、server_registry が必要とする「取得失敗を呼び出し側に伝える」契約を明示テスト化。統合は「`lock.py` が EX-only 経路も提供」する形に留め、server_registry の `_file_lock` の `yield bool` 契約を変えない。

8. **`git mv` 履歴 vs minimal-diff**: 大量移設で diff が膨らみレビュー困難。単一 PR 方針のためなおさら。→ **緩和**: 必ず `git mv` を使い rename 検出を効かせる。import 書き換えは機械的最小変更に限定。Phase ごとのコミットを分け、各コミットメッセージで Phase/Step を明示。

9. **`reports/export_html.py` の import (調査済)**: 二次レビューで確認済。`export_html.py:18-19` は `from dashboard.server import build_dashboard_data, render_static_html` + `from reports.summary import load_events`。詳細は「entry-point モジュールの package-style import (残余)」節を参照。`summary.py` と同方針で `subagent_metrics`/`cost_metrics` 系の bare import のみ `analyzer.*` 化し、`dashboard.server`/`reports.summary` の package import は残余として維持する。
