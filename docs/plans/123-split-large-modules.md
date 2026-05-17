# Issue #123 リファクタリング 実装計画 — 肥大化モジュールの責務分割

## 📋 plan-reviewer 反映ログ

| Proposal | 内容 | 反映箇所 |
|---|---|---|
| (初稿) | — | — |

### 二次レビュー反映（iteration 1）

| Proposal | 内容 | 反映箇所 |
|---|---|---|
| P1 (actionable) | `dashboard/server.py:18` の `sys.path.insert` がパッケージ化後の shim 内サブパッケージ import で load-bearing。扱い未記載 | Phase 1「注意点」に最重要項目として追加（shim 最初の実行行に残す）／ Step 1.8 を「`sys.path.insert` を含む」記述に更新 |
| P2 (actionable) | 依存 DAG の `render.py ← api.py` エッジが誤り（`render_static_html` は `build_dashboard_data` 非依存） | Phase 1 依存 DAG 図を修正／ DAG 直後に `config.py`・`render.py` がリーフである旨の説明文を追加 |
| P3 (advisory) | Phase 4 の private import 集合が plan 時点で未確定 | Phase 4「外部 importer」段落を Step 4.1 を明示的 gating step とする記述に変更／ `_TIER2_DISPATCH` の mutable state 注意を追記 |
| P4 (advisory) | `__init__.py` の private re-export が underscore 名を package surface に固定化 | Phase 2 目標レイアウト段落に技術的負債の明記を追加（将来 Issue での撤去方針を reference doc に残す） |
| P5 (advisory) | shim のサブモジュール import 順序（遅延 import 禁止）が制約として未明記 | Step 1.8 に「トップレベル import 必須・遅延 import 禁止」の制約を追記 |

### 三次レビュー反映（iteration 2 — proceed with minor revisions）

| Proposal | 内容 | 反映箇所 |
|---|---|---|
| 三次 advisory | Risk 2 の旧文「`api.py` ↔ `render.py` を一方向化」が修正済み DAG（兄弟・エッジなし）と矛盾 | Risk 2 の文言を「`api.py` と `render.py` 間にエッジを作らない（独立した兄弟）」に修正 |
| 追加調査（reviewer Q1） | packaging manifest がモジュールパスを列挙していれば file→package 昇格で破損する懸念 | `plugin.json`・`pyproject.toml` を確認 — モジュールパス列挙なし（`test_analyzer_package_imports.py` が `rglob` で動的検出）。更新不要と確定 |
| 追加発見（orchestrator） | `pyproject.toml` の E402 方針上、shim の `sys.path.insert` 後の import は E402 を誘発する | Phase 1「注意点」の `sys.path.insert` 項に per-line `# noqa: E402` 付与の sub-bullet を追加 |

---

## Goal

`#121`（パッケージ再配置）完了後の整理として、4 つの肥大化モジュールを**責務単位**で内部分割する。

達成基準:
- **公開 API / JSON contract は不変**: `dashboard/server.py` の `build_dashboard_data()` が返す `/api/data` JSON schema（`docs/spec/dashboard-api.md`）、`reports/` 出力、外部 importer が使う関数シグネチャは byte レベルで挙動不変。
- **TDD first**: 各実装ステップの前に必ず failing-test ステップを置く（characterization テストを先に書き、移設後 GREEN を確認）。
- **stdlib only**: 外部依存を一切増やさない。
- 4 モジュールそれぞれが独立した feature branch / PR。`dashboard` を最優先・最初に着手。他 3 つは `dashboard` 完了後、相互独立に並行可能。
- ブランチ: `feature/123-<slug>` を `v0.8.2` から切る（モジュールごとに 1 本）。

### 重要な前提（#121 完了後の実体）

| Issue 記載（旧名） | 現在の実体 | サイズ | 本 Issue での扱い |
|---|---|---|---|
| `dashboard/server.py` | `dashboard/server.py`（不変） | 74.7 KB / 1721 行 | **Phase 1（最優先）** |
| `subagent_metrics.py` | `analyzer/subagent.py` | 27.9 KB | Phase 2 |
| `cost_metrics.py` | `analyzer/cost.py` | 20.5 KB | Phase 3 |
| `scripts/archive_usage.py` | コアは `analyzer/archive/usage.py`（28.1 KB）に既に移設済み。`scripts/archive_usage.py` は薄い wrapper | 28.1 KB | Phase 4 |

`analyzer/archive/usage.py` の調査結果（後述 Phase 4）: #121 は「scripts → analyzer への**配置**移動」を済ませただけで、ファイル内部は依然 28 KB / 28 関数の単一モジュール。CLI parsing・I/O プリミティブ・partition ロジック・state 管理・dispatch コアが 1 ファイルに同居しており、**責務分割の line item は #121 では満たされていない**。Phase 4 として実施する価値あり（ただし優先度は最後）。

---

## 共通方針（全 Phase に適用）

### TDD ordering の鉄則

各 Phase は次の順序を厳守する。「実装してからテスト」は本リポジトリではディフェクト。

1. **RED（characterization）**: 移設対象の公開シンボルについて、現状の挙動を pin する characterization テストを書く（または既存テストを確認）。この時点では既存実装に対して**全 GREEN**であることを確認（characterization は現状を固定するものなので最初から通る — これは「移設の安全網が機能している」ことの検証）。
2. **新ファイル骨格に対する RED**: 新モジュール（例 `dashboard/aggregate.py`）からの import を期待するテストを書く。新ファイルが空なので `ImportError` で **RED**。
3. **GREEN（移設）**: コードを新ファイルへ cut & paste で移設し、旧ファイルは re-export shim にする。RED が GREEN に変わることを確認。
4. **drift guard**: 旧ファイル経由・新ファイル経由の両 import パスで全既存テストが GREEN を維持することを確認。

「characterization は最初から GREEN」という性質上、「failing-test-first」の文字どおりの RED は **手順 2（新 import パスの ImportError）** が担う。各 Phase の Ordered steps で明示する。

### 公開サーフェスを守る shim 戦略

外部 importer（`hooks/`・`reports/`・`scripts/`・`tests/`）が現状の import パスを使い続けられるよう、**分割元ファイルは re-export shim として残す**。これにより:
- 本 Issue のスコープを「内部分割のみ」に閉じ込められる（importer 側の変更ゼロ = PR 独立性が担保される）。
- 将来 importer を新パスへ移行するのは別 Issue（#121 同様の段階移行）。

`dashboard/server.py` は `tests/test_dashboard.py` が `importlib.util.spec_from_file_location("dashboard_server", _DASHBOARD_PATH)` で**ファイルパス直 import** しているため（パッケージ import ではない）、`server.py` 自身がトップレベルで全公開シンボルを束ねる shim であり続ける必要がある。これは Phase 1 の設計制約。

### 既存テストの所在（characterization の土台）

調査で確認した、各モジュールの公開サーフェスを既に守っているテスト:

| 対象 | 既存 characterization テスト | 備考 |
|---|---|---|
| `dashboard/server.py` | `tests/test_dashboard.py`（64 KB）, `test_dashboard_live.py`, `test_dashboard_sse.py`, `test_dashboard_router.py`, `test_dashboard_period_toggle.py`（85 KB）, `test_dashboard_heartbeat.py`, `test_dashboard_local_tz.py`, `test_dashboard_no_archive.py`, `test_dashboard_cross_tabs.py`, `test_dashboard_heatmap.py` ほか | `build_dashboard_data` / aggregator 群 / SSE / server lifecycle を網羅。**移設の安全網は既に十分** |
| `analyzer/subagent.py` | `tests/test_subagent_metrics.py`, `test_subagent_quality.py`（31 KB）, `test_record_subagent.py` | invocation pairing / dedup / aggregation を網羅 |
| `analyzer/cost.py` | `tests/test_cost_metrics.py`（18 KB）, `test_model_distribution.py`, `test_dashboard_sessions_api.py` | pricing / session breakdown / model distribution |
| `analyzer/archive/usage.py` | `tests/test_archive_usage.py`（28 KB）, `test_archive_state.py`（23 KB）, `test_archive_smoke.py`, `test_launch_archive.py` | run_archive / partition / state |
| パッケージ構造の不変条件 | `tests/test_analyzer_package_imports.py` | analyzer 配下の全モジュール import 健全性・sys.path ハック禁止を pin。**Phase 2/3/4 で新モジュールが自動的にこのガードの対象に入る** |

各 Phase の RED ステップは「不足分の characterization を補う」ことに集中する（フルスクラッチではない）。

---

## Phase 1 — `dashboard/server.py` の分割（最優先 / 最初の PR）

ブランチ: `feature/123-dashboard-server-split`（`v0.8.2` 起点）

### 1721 行の実コードから同定した seam

`grep` で抽出した 41 個のトップレベル定義を機能で分類した結果、Issue の「router / API handler 群 / SSE / 集計グルー」仮説は概ね妥当だが、**最大の塊は aggregator 群（約 700 行 / `aggregate_*` 14 関数 + period filter ヘルパー）**であり、ここを最優先で切り出すのが効果的。具体的な 5 区画:

| 区画 | 現状の行範囲（目安） | 内容 |
|---|---|---|
| A. 設定・env 解決 | 44–107 | `DATA_FILE` / `ALERTS_FILE` / `SERVER_JSON_PATH` / `_resolve_port` / `_resolve_idle_seconds` / `_resolve_poll_interval` / `PORT` / `IDLE_SECONDS` / `POLL_INTERVAL` / `TOP_N` / frozenset 定数群 |
| B. period filter + aggregator 群 | 110–1030 | `_filter_events_by_period` / `_filter_usage_events` / `load_events` / `aggregate_skills` / `aggregate_subagents` / `aggregate_daily` / `aggregate_projects` / `aggregate_skill_cooccurrence` / `aggregate_project_skill_matrix` / `aggregate_hourly_heatmap` / `_skill_event_interval` / `_attribute_permission` / `aggregate_permission_breakdowns` / `_normalize_skill_name` / `_parse_iso_utc` / `aggregate_skill_invocation_breakdown` / `aggregate_skill_lifecycle` / `_resolve_skills_dir` / `aggregate_skill_hibernating` / `aggregate_compact_density` / `aggregate_session_stats` / `load_health_alerts` |
| C. 集計グルー | 1032–1125 | `build_dashboard_data`（B の aggregator 群 + `analyzer/` を束ねる唯一の関数） |
| D. テンプレート組み立て | 1128–1216 | `render_static_html` / `_concat_main_js` / `_build_html_template` / `_HTML_TEMPLATE` / `_CSS_FILES` / `_MAIN_JS_FILES` / `_TEMPLATE_DIR` |
| E. SSE + HTTP server ランタイム | 1219–1717 | `_peer_disconnected` / `SSEClient` / `_SseState` / `_FileWatcher` / `_IdleTracker` / `DashboardHandler` / `DashboardServer` / `create_server` / `run` / `main` |

### 提案する目標ファイルレイアウト

`dashboard/` をパッケージ化（`dashboard/__init__.py` 新設）し、`server.py` は **re-export shim 兼エントリポイント**として残す:

```
dashboard/
├── __init__.py          # 新設（空 or 最小）
├── server.py            # ← shim に縮小。全公開シンボルを下記から re-export +
│                        #    DashboardHandler/DashboardServer/create_server/run/main を保持。
│                        #    `if __name__ == "__main__"` エントリも維持。
│                        #    tests が spec_from_file_location でファイル直 import するため
│                        #    トップレベル名前空間契約を死守する。
├── config.py            # 区画 A: env 解決 + モジュール定数
├── aggregate.py         # 区画 B: period filter + 全 aggregate_* + load_events + load_health_alerts
├── api.py               # 区画 C: build_dashboard_data（集計グルー）
├── render.py            # 区画 D: render_static_html + _build_html_template + _HTML_TEMPLATE
└── http_runtime.py      # 区画 E: SSEClient / _SseState / _FileWatcher / _IdleTracker /
                         #         DashboardHandler / DashboardServer / create_server / run / main
```

依存方向（一方向 DAG、循環なし）:
```
config.py  ─┐
            ├← aggregate.py ← api.py ← http_runtime.py
render.py  ─┘                render.py ← http_runtime.py
server.py (shim) → 全モジュールを import して re-export
```

`config.py` と `render.py` はどちらもリーフ（互いに依存なし）。`render_static_html` は `_HTML_TEMPLATE` と `json` のみに依存し `build_dashboard_data` を呼ばないため、`render.py` は `api.py` に依存しない（初稿の `render.py ← api.py` エッジは誤りで削除済み）。`api.py`・`render.py` は兄弟関係でエッジなし。`http_runtime.py` が両者に依存する。

注意点:
- **`sys.path.insert` の扱い（Phase 1 最重要の機械的詳細）**: 現 `dashboard/server.py:18` は `from analyzer.subagent import ...` の直前に `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`（= repo root を `sys.path` に追加）を実行している。`test_dashboard.py` は `server.py` を `spec_from_file_location` で**ファイルパス直 import**するため、`server.py` shim 自身が行う `import dashboard.config` 等のサブパッケージ import は、この `sys.path.insert` が**先に走っていないと解決できない**。よって **shim の最初の実行行に `sys.path.insert` を残す**（サブモジュール import より前）。`analyzer/` 配下を禁ずる `test_analyzer_package_imports.py` のガードは `dashboard/` を対象外とするため、この行は規約違反にならない。各サブモジュール（`config.py` 等）は `analyzer.*` を import するが、shim が先に `sys.path` を整えているので個別の `sys.path.insert` は不要。
  - **ruff E402**: `pyproject.toml` のコメント方針どおり、shim の `sys.path.insert` 後に来るサブモジュール import 行（not-at-top-of-file）には per-line `# noqa: E402` を個別付与する（entry-point leaf の既存パターンと同じ。全域 ignore は #121 で撤廃済み）。`config.py` 等のサブモジュール側は `sys.path` ハックを持たないため E402 は出ない。
- `dashboard/template.py` という**モジュール名**は既存の `dashboard/template/` **ディレクトリ**と同名になり Python で衝突する。本計画では衝突回避のため区画 D のファイルを **`dashboard/render.py`** とする。
- `build_dashboard_data` が `analyzer.subagent` / `analyzer.cost` から import している関数群（`aggregate_subagent_metrics` 等）は `api.py` / `aggregate.py` が直接 import する。Phase 2/3 で `analyzer/` 側を分割しても、`analyzer/subagent.py` / `analyzer/cost.py` を shim 化するので Phase 1 の import 文は無改修で生き残る（**Phase 間の独立性が成立**）。
- `server_registry` の re-export（`server.py` 38–42 行の `_file_lock` / `write_server_json` 等）は `http_runtime.py` へ移し、`server.py` shim でさらに re-export。元コメント（monkeypatch は `server_registry` に対して行う必要があるという注意書き）を移設先に持っていく。
- `_HTML_TEMPLATE = _build_html_template()` は **import 時副作用**（テンプレートファイル群を読んで concat）。`render.py` の import 時に走る。`tests/test_dashboard_template_split.py` が `EXPECTED_TEMPLATE_SHA256` で byte 一致を pin しているため、concat ロジックは 1 byte も変えない（cut & paste のみ）。

### Phase 1 の critical files

- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/dashboard/server.py`（分割元 → shim）
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_dashboard.py`（最大の characterization 安全網）
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_dashboard_period_toggle.py`（`_concat_main_js` テスト seam・period filter を pin）
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_dashboard_template_split.py`（テンプレート byte 一致 SHA256 pin）
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/docs/spec/dashboard-api.md`（不変であるべき JSON contract の正典）

### Phase 1 — Ordered steps（failing-test-first）

- **Step 1.0**: `v0.8.2` から `feature/123-dashboard-server-split` を作成（`docs/reference/branching-workflow.md` の base discovery 手順に従う）。
- **Step 1.1 (RED — characterization 補完)**: `tests/test_dashboard.py` に「`build_dashboard_data` が返す dict の**全トップレベルキー**が存在し型が一致する」ことを pin する schema characterization テストを 1 つ追加（既存テストはキーごとに散在しているため、移設前のキー集合スナップショットを 1 箇所に固める）。`docs/spec/dashboard-api.md` のキー一覧と verbatim 突合。**この時点では現実装に対して GREEN**（characterization の正常性確認）。
- **Step 1.2 (RED — 新 import パス)**: `tests/test_dashboard.py` に「`import dashboard.config` / `dashboard.aggregate` / `dashboard.api` / `dashboard.render` / `dashboard.http_runtime` がすべて成功し、想定公開シンボルが存在する」ことを assert するテストを追加。新ファイルが未作成なので **ImportError で RED**。
- **Step 1.3 (GREEN — config.py)**: `dashboard/__init__.py` と `dashboard/config.py` を新設、区画 A を cut。`server.py` で明示名 re-export。`test_default_data_path.py` を含む全 dashboard テストが GREEN を維持することを確認。
- **Step 1.4 (GREEN — aggregate.py)**: 区画 B を `dashboard/aggregate.py` へ cut。`analyzer.subagent` / `analyzer.cost` の import 文も移設。`server.py` で re-export。Step 1.1 の schema テスト + aggregator 系テスト GREEN 確認。
- **Step 1.5 (GREEN — render.py)**: 区画 D を `dashboard/render.py` へ cut。`_HTML_TEMPLATE` 組み立ての import 時副作用込みで移設。`test_dashboard_template_split.py` の SHA256 が一致することを確認（最重要回帰ポイント）。
- **Step 1.6 (GREEN — api.py)**: `build_dashboard_data` を `dashboard/api.py` へ cut。`aggregate.py` から import。`render_static_html` は aggregator に依存せず `data: dict` を受けるだけなので `render.py` 側に置く。`server.py` で re-export。
- **Step 1.7 (GREEN — http_runtime.py)**: 区画 E を `dashboard/http_runtime.py` へ cut。`server_registry` re-export とコメントも移設。`DashboardHandler` は `api.build_dashboard_data` と `render._HTML_TEMPLATE` を import。
- **Step 1.8 (shim 確定)**: `server.py` を「`sys.path.insert`（最初の実行行）+ サブモジュール import + 全公開シンボルの re-export + `main()` + `if __name__ == "__main__"`」のみに縮小。`tests/test_dashboard.py` の `load_dashboard_module`（`spec_from_file_location` 直 import）が全シンボルを引けることを確認。**制約**: shim のサブモジュール import（`config` / `aggregate` / `api` / `render` / `http_runtime`）は**モジュールトップレベル**で行う（遅延 import 禁止）。これにより 1 回の `exec_module` 呼び出しで `config.py` の env 評価・`render.py` のテンプレート concat 等の import 時副作用が、loader がパッチした env 下で一括評価され、現状の単一ファイル挙動と一致する。遅延 import すると loader の `finally` で env 復元後に env 依存定数が評価され、テスト隔離が静かに壊れる。
- **Step 1.9 (drift guard)**: `python3 -m pytest tests/` フルラン。`test_launch_dashboard.py` / `test_restart_dashboard.py` / `test_export_html.py`（`reports/export_html.py` が `server.py` を import）まで含めて全 GREEN。
- **Step 1.10**: `docs/reference/dashboard-server.md` に新ファイルレイアウトを追記（contract 不変なので `docs/spec/dashboard-api.md` は無改修）。PR 作成（target: `v0.8.2`）。

---

## Phase 2 — `analyzer/subagent.py` の分割

ブランチ: `feature/123-subagent-split`（`v0.8.2` 起点 / Phase 1 とは独立）

### 実コードから同定した seam

22 個のトップレベル定義。Issue の「invocation pairing / aggregation」仮説は妥当。3 区画:

| 区画 | 関数 | 内容 |
|---|---|---|
| A. パース・interval プリミティブ | `_parse_ts` / `subagent_invocation_interval` / `INVOCATION_MERGE_WINDOW_SECONDS` | 低レベル共通ヘルパー |
| B. invocation pairing | `_build_invocations` / `usage_invocation_events` / `usage_invocation_intervals` / `_bucket_events` / `_invocation_duration` / `_pair_invocations_with_stops` / `_process_bucket` / `_ts_key` / `_bucket_invocation_records` / `invocation_records` | start↔lifecycle↔stop の同定・ペアリング |
| C. aggregation | `_aggregate_bucket` / `_percentiles` / `_build_metrics` / `_week_start_iso` / `aggregate_subagent_failure_trend` / `session_subagent_counts` / `aggregate_subagent_metrics` | pairing 結果からメトリクス算出 |

### 提案する目標ファイルレイアウト

```
analyzer/
└── subagent/                  # ← ファイルからパッケージへ昇格
    ├── __init__.py            # 全公開シンボルを re-export（shim）。
    │                          #   dashboard 側（Phase1後は aggregate.py）が
    │                          #   `from analyzer.subagent import _bucket_events, ...` で
    │                          #   private 名まで import しているため、private も re-export 必須。
    ├── pairing.py             # 区画 A + B
    └── metrics.py             # 区画 C
```

依存方向: `pairing.py ← metrics.py`。`__init__.py` が両方を re-export。

**重要なクロスモジュール依存**（調査済）: `dashboard/server.py`（Phase 1 後は `dashboard/aggregate.py`）は `from analyzer.subagent import _bucket_events, _build_invocations, _pair_invocations_with_stops, ...` と **private 関数まで import**している。`analyzer/cost.py` は `from analyzer.subagent import session_subagent_counts` を import。`__init__.py` shim はこれらを全て（private 含め）re-export しなければ Phase 1 / Phase 3 のコードが壊れる。`tests/test_analyzer_package_imports.py` の `test_all_analyzer_modules_import` が新サブモジュールを自動的にカバーする。

ファイル→ディレクトリ昇格時、`analyzer/subagent.py` を削除して `analyzer/subagent/` を作る。`tests/test_subagent_metrics.py` の `import analyzer.subagent as subagent_metrics` は `__init__.py` 経由で透過的に動く。

**技術的負債の明記**: `__init__.py` が private 名（`_bucket_events` 等）を re-export するのは、既存の `dashboard` ↔ `analyzer` private カップリングを壊さないための互換 shim であり、PR 独立性のため**必須**。ただしこれは underscore 名をパッケージ surface に institutionalize する。将来の別 Issue で `dashboard/aggregate.py` を `analyzer.subagent.pairing` から直接 import するよう移行し private re-export を撤去すべき旨を、Phase 2 の reference doc（Step 2.5）に 1 行記録する（Phase 3 の `analyzer/cost/__init__.py` も同様の方針）。

### Phase 2 の critical files

- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/analyzer/subagent.py`（分割元 → `analyzer/subagent/__init__.py`）
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_subagent_metrics.py`
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_subagent_quality.py`
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_analyzer_package_imports.py`（パッケージ構造の不変条件）

### Phase 2 — Ordered steps（failing-test-first）

- **Step 2.0**: `feature/123-subagent-split` 作成。
- **Step 2.1 (RED — 公開サーフェス pin)**: `tests/test_subagent_metrics.py` に「`analyzer.subagent` が公開する全シンボル名（`dashboard` 側が import している private 名を含む）の集合」を pin する characterization テストを追加。現実装に対し **GREEN**。
- **Step 2.2 (RED — 新 import パス)**: 同テストに `import analyzer.subagent.pairing` / `analyzer.subagent.metrics` を assert するテストを追加。**ImportError で RED**。
- **Step 2.3 (GREEN — パッケージ化)**: `analyzer/subagent.py` → `analyzer/subagent/__init__.py` + `pairing.py` + `metrics.py` に分割。`__init__.py` で全シンボル（private 含む）を明示 re-export。RED → GREEN。
- **Step 2.4 (drift guard)**: `python3 -m pytest tests/` フルラン。特に `test_dashboard.py`（Phase 1 後 `dashboard/aggregate.py` が `analyzer.subagent` の private を import）・`test_cost_metrics.py`・`test_record_subagent.py` の GREEN を確認。
- **Step 2.5**: `docs/reference/subagent-invocation-pairing.md` にレイアウト追記。PR 作成（target: `v0.8.2`）。

---

## Phase 3 — `analyzer/cost.py` の分割

ブランチ: `feature/123-cost-split`（`v0.8.2` 起点 / 他 Phase と独立）

### 実コードから同定した seam

Issue の「model 価格表 / aggregation」仮説は妥当。2 区画:

| 区画 | シンボル | 内容 |
|---|---|---|
| A. pricing | `ModelPricing` / `MODEL_PRICING` / `DEFAULT_PRICING` / `_get_pricing` / `calculate_message_cost` / `_FAMILY_CANONICAL_ORDER` / `infer_model_family` | 価格表 + per-message cost + model family 推論。価格表 docstring もここに同伴 |
| B. aggregation | `TOP_N_SESSIONS` / `aggregate_model_distribution` / `calculate_session_cost` / `_parse_iso` / `_build_session_row` / `aggregate_session_breakdown` | session / model 単位集計 |

### 提案する目標ファイルレイアウト

```
analyzer/
└── cost/
    ├── __init__.py            # 全公開シンボル re-export（shim）
    ├── pricing.py             # 区画 A（価格表 docstring 同伴）
    └── aggregate.py           # 区画 B
```

依存方向: `pricing.py ← aggregate.py`。`aggregate.py` は `analyzer.subagent`（Phase 2 後はパッケージ）から `session_subagent_counts` を import。

**外部 importer**: `dashboard/server.py`（Phase 1 後 `dashboard/api.py`）が `from analyzer.cost import TOP_N_SESSIONS, aggregate_model_distribution, aggregate_session_breakdown`。`reports/summary.py` が遅延 import で `from analyzer.cost import aggregate_session_breakdown`。`__init__.py` shim でこれら全てを re-export すれば無改修。

価格表 docstring（module docstring、`MODEL_PRICING` の出典 pin）は `pricing.py` の module docstring へ移す。`docs/reference/cost-calculation-design.md` への参照リンクは維持。

### Phase 3 の critical files

- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/analyzer/cost.py`（分割元 → `analyzer/cost/__init__.py`）
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_cost_metrics.py`
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_model_distribution.py`
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_dashboard_sessions_api.py`

### Phase 3 — Ordered steps（failing-test-first）

- **Step 3.0**: `feature/123-cost-split` 作成。
- **Step 3.1 (RED — pin)**: `tests/test_cost_metrics.py` に「`MODEL_PRICING` の全 model キーと各 `ModelPricing` 値」「公開シンボル集合」を pin する characterization テストを追加。価格表は監査価値が高いので値スナップショットを明示的に固める。現実装に対し **GREEN**。
- **Step 3.2 (RED — 新 import パス)**: `import analyzer.cost.pricing` / `analyzer.cost.aggregate` を assert。**ImportError で RED**。
- **Step 3.3 (GREEN — パッケージ化)**: `analyzer/cost.py` → `analyzer/cost/__init__.py` + `pricing.py` + `aggregate.py`。`__init__.py` で全シンボル re-export。RED → GREEN。
- **Step 3.4 (drift guard)**: フルテスト。`test_dashboard.py`・`test_summary.py`（`reports/summary.py` 経由）・`test_rescan_cost` 系の GREEN 確認。
- **Step 3.5**: `docs/reference/cost-calculation-design.md` にレイアウト追記。PR 作成。

---

## Phase 4 — `analyzer/archive/usage.py` の分割

ブランチ: `feature/123-archive-usage-split`（`v0.8.2` 起点 / 他 Phase と独立）

### 調査結果と分割の正当性

`scripts/archive_usage.py` は既に薄い wrapper（`from analyzer.archive.usage import main` のみ、17 行）。コアは `analyzer/archive/usage.py`（28.1 KB / 28 関数）。#121 は配置移動のみで**内部分割は未実施**。CLI 引数解析・gzip I/O プリミティブ・partition ロジック・state JSON 管理・dispatch コアが 1 ファイルに同居しており、責務分割の余地が明確にある。Phase 4 として実施する。

### 実コードから同定した seam

| 区画 | シンボル | 内容 |
|---|---|---|
| A. データモデル + パス解決 | `YearMonth` / `ArchivePaths` / `ArchiveResult` / `ArchiveReadError` / `_resolve_paths` / `DEFAULT_RETENTION_DAYS` / `_DEFAULT_DATA_FILE` / `_resolve_default_retention_days` | dataclass 群 + env からのパス/保持日数解決 |
| B. partition ロジック | `_calculate_archive_target_months` / `_calculate_archivable_horizon` / `_event_year_month` / `_TIER2_DISPATCH` / `_structural_fingerprint` / `_partition_events` | どのイベントをどの月へ振り分けるか |
| C. I/O プリミティブ | `_read_hot_tier` / `_merge_with_existing_archive` / `_atomic_write_gzip` / `_atomic_rewrite_hot` / `_archive_buckets_to_gz` | gzip 読み書き・hot tier の atomic rewrite |
| D. state 管理 | `_read_state` / `_write_state` / `_read_last_archived_month` / `_compute_new_last_archived` / `_finalize_state` | `.archive_state.json` の読み書き |
| E. オーケストレーション + CLI | `run_archive` / `_open_log` / `main` | 全区画を束ねる + CLI エントリ |

### 提案する目標ファイルレイアウト

```
analyzer/
└── archive/
    ├── __init__.py            # 既存（空 1 byte）
    ├── loader.py              # 既存（不変 — 別責務の cold-tier reader）
    └── usage/                 # ← ファイルからパッケージへ昇格
        ├── __init__.py        # 全公開シンボル re-export（shim）。
        │                      #   `from analyzer.archive.usage import main` /
        │                      #   `run_archive` の既存 import を死守。
        ├── model.py           # 区画 A
        ├── partition.py       # 区画 B
        ├── gzip_io.py         # 区画 C（gzip / atomic write。stdlib `io` 衝突回避で gzip_io）
        ├── state.py           # 区画 D
        └── runner.py          # 区画 E（run_archive + main）
```

依存方向（一方向 DAG）: `model.py ← {partition.py, gzip_io.py, state.py} ← runner.py`。

**外部 importer**: `scripts/archive_usage.py` が `from analyzer.archive.usage import main`。`hooks/launch_archive.py` は `analyzer.platform.process` を import するのみ（archive コアは spawn 経由なので直 import なし）。テストは `tests/test_archive_usage.py` / `test_archive_state.py` が `run_archive` ほか多数の private を直接 import している可能性が高い。

**Step 4.1 を明示的な gating discovery step とする**: Phase 4 は 4 モジュール中最大（28 関数 / 5 分割）かつ surface contract が最も未確定。Step 4.1 の grep 出力（`from analyzer.archive.usage import` の全列挙）を **completeness checklist の正典**として扱い、grep で見つかった名前が非 `__init__` サブモジュールに着地する場合は `__init__.py` shim で必ず re-export する。特に `_TIER2_DISPATCH` はモジュールレベルの dict（mutable state）。`partition.py` へ移して re-export する場合、分割後に他モジュールから rebind されないこと（参照のみ）を確認する。

`io.py` というモジュール名は stdlib `io` と衝突するため **`gzip_io.py`** とする。

### Phase 4 の critical files

- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/analyzer/archive/usage.py`（分割元 → `analyzer/archive/usage/__init__.py`）
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_archive_usage.py`
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/tests/test_archive_state.py`
- `/Users/kkoichi/Developer/personal/claude-transcript-analyzer/docs/spec/archive-runtime.md`（不変であるべき archive contract）

### Phase 4 — Ordered steps（failing-test-first）

- **Step 4.0**: `feature/123-archive-usage-split` 作成。
- **Step 4.1 (RED — pin)**: `tests/test_archive_usage.py` に「`analyzer.archive.usage` の公開シンボル集合」「テストが直接 import する private 名の集合」を pin する characterization テストを追加。`grep` で `test_archive_*.py` の `from analyzer.archive.usage import` 行を全列挙して網羅。現実装に対し **GREEN**。
- **Step 4.2 (RED — 新 import パス)**: `import analyzer.archive.usage.model` / `partition` / `gzip_io` / `state` / `runner` を assert。**ImportError で RED**。
- **Step 4.3 (GREEN — パッケージ化)**: `analyzer/archive/usage.py` → `analyzer/archive/usage/__init__.py` + 5 サブモジュール。`__init__.py` で全シンボル（private 含む）re-export。RED → GREEN。
- **Step 4.4 (drift guard)**: フルテスト。`test_launch_archive.py`・`test_archive_smoke.py`・`test_summary_include_archive.py`・`scripts/archive_usage.py` 経由の起動を確認。`test_analyzer_package_imports.py` が新サブモジュールを自動カバー。
- **Step 4.5**: `docs/spec/archive-runtime.md` / `docs/reference/storage.md` にレイアウト追記。PR 作成。

---

## Risks / Tradeoffs

1. **公開サーフェス回帰（最重要）**: shim が 1 シンボルでも re-export し損なうと importer が壊れる。特に `dashboard/server.py` が `analyzer.subagent` から **private 関数（`_bucket_events` 等）まで import** している点が落とし穴。各 Phase の Step x.1 で「テストが import する private 名」まで grep で全列挙して pin する。緩和策: characterization テストを「シンボル集合の集合一致」で書く（個別 assert の漏れを防ぐ）。

2. **import 循環リスク**: 全 Phase のレイアウトを一方向 DAG（`config/model/pricing/pairing → 上位`）で設計済み。最大の注意点は Phase 1 の `dashboard/render.py`（旧 `template`）— 既存 `dashboard/template/` ディレクトリと同名回避のため `render.py` に改名する。また `analyzer/archive/usage/gzip_io.py` は stdlib `io` 衝突回避のため命名済み。`render_static_html` を `render.py` 側に置くことで `api.py` と `render.py` 間にエッジを作らない（両者は独立した兄弟モジュール）。

3. **テンプレート byte 一致（Phase 1 固有）**: `_HTML_TEMPLATE = _build_html_template()` は import 時副作用で、`test_dashboard_template_split.py` が `EXPECTED_TEMPLATE_SHA256` で byte 一致を pin。`render.py` への移設は concat ロジックを 1 byte も変えない cut & paste に限定。`_concat_main_js` は `test_dashboard_period_toggle.py` のテスト seam なので名前を維持。

4. **ファイル直 import 制約（Phase 1 固有）**: `tests/test_dashboard.py` は `spec_from_file_location("dashboard_server", server.py)` でパッケージ import を経由しない。よって `server.py` shim はトップレベル名前空間に全公開シンボルを束ねる必要があり、純粋な「空 wrapper」にはできない。`USAGE_JSONL` 等の env override がモジュール import 時に評価される（`config.py` の `DATA_FILE = Path(os.environ.get(...))`）ため、`load_dashboard_module` の monkeypatch タイミングと整合することを Step 1.8 で確認。

5. **PR 独立性**: 4 Phase は別ブランチ・別 PR。Phase 2/3/4 は `analyzer/` 配下、Phase 1 は `dashboard/` 配下で**ファイル非交差**。かつ全 Phase が shim 戦略で importer を無改修に保つため、`v0.8.2` への merge 順序は任意（dashboard 先着推奨だが技術的制約ではない）。唯一の論理依存は「Phase 3 の `analyzer/cost/aggregate.py` が `analyzer.subagent` を import する」点だが、Phase 2 が `analyzer.subagent` を**パッケージ shim 化**しても import パス文字列は不変なので、Phase 2/3 のどちらが先でも壊れない。

6. **import 時副作用の評価順**: `config.py`（env 解決）・`render.py`（テンプレート concat）・`pricing.py`（`MODEL_PRICING` 構築）は import 時に評価される。shim が `from x import *` ではなく**明示名 re-export**を使うことで、評価順を分割前と一致させる。

7. **docstring の所在**: `analyzer/cost.py` の価格表 docstring、`dashboard/server.py` の長大な実装注記コメント（period filter の三段ロジック等）は、対応するコードと同じファイルへ同伴移設する。コメントは実装の「正典」なので分離しない。

8. **スコープ規律**: 本 Issue は内部分割のみ。importer を新パスへ移行する誘惑（例 `dashboard/aggregate.py` を直接 import するよう `reports/` を書き換える）には乗らない — それは #121 同様の段階移行で別 Issue。`docs/spec/dashboard-api.md` / `docs/spec/archive-runtime.md` の JSON / contract 記述は無改修（`docs/reference/` 側にのみレイアウト追記）。
