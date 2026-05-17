"""dashboard/server.py — re-export shim + エントリポイント (Issue #123 Phase 1).

#123 で server.py を責務単位の 5 サブモジュール (config / aggregate / api /
render / http_runtime) へ分割した。本ファイルは外部 importer
(`reports/` ・ `tests/` ・ `hooks/`) が従来の import パス
(`from dashboard.server import ...` / `spec_from_file_location` 直 import) を
無改修で使い続けられるよう、全公開シンボルを束ねる re-export shim として残す。

tests/test_dashboard.py は本ファイルを `spec_from_file_location` で
**ファイルパス直 import** し、その都度 `USAGE_JSONL` 等の env をパッチして
fresh module として exec する。分割前の単一ファイル server.py は 1 回の
exec_module で全コードが patch 済み env 下に再評価され「1 load = 完全独立
インスタンス」だった。本 shim はこの契約を保つため、exec のたびに 5 つの
サブモジュールを `_load_submodule()` で **fresh module として読み直す**
(通常のパッケージ import + キャッシュ共有や `importlib.reload` では、後続の
shim load が先行 load の load_events / DashboardHandler の参照する
module dict を書き換え、先行 load が後発の env を読む cross-load leakage が
起きる — codex review #123 Round 1 P2)。
"""
import importlib.util
import sys
from pathlib import Path

# サブモジュールが import する `analyzer.*` を解決するため repo root を sys.path に
# 積む。本 shim は spec_from_file_location でファイル直 import されるため、
# サブモジュール load より前に最初の実行行として走らせる必要がある。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_PKG_DIR = Path(__file__).resolve().parent


def _load_submodule(name: str) -> None:
    """`dashboard/<name>.py` を fresh module として exec し sys.modules へ登録する。

    canonical な dotted 名 (`dashboard.<name>`) で登録するのは、サブモジュール
    同士の `from dashboard.config import ...` を **今 load した fresh コピー** に
    解決させるため。次の shim load は同じ key を上書きするだけで、先行 load の
    module オブジェクトは先行 shim の namespace と関数の `__globals__` から参照
    され生存し続けるので、load 間の独立性が保たれる。
    """
    spec = importlib.util.spec_from_file_location(f"dashboard.{name}", _PKG_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"dashboard.{name}"] = module
    spec.loader.exec_module(module)


# 依存順 (config → aggregate → render → api → http_runtime) に fresh load する。
# 下位を先に sys.modules へ登録しておかないと、上位の `from dashboard.<lower>
# import ...` が解決できない。各サブモジュールの import 時副作用
# (config.py の env 評価・render.py のテンプレート concat) はこの exec 時に
# loader がパッチした env 下で評価される。re-export は後続の
# `from dashboard.<name> import ...` が sys.modules 経由で fresh コピーを引く。
_load_submodule("config")
_load_submodule("aggregate")
_load_submodule("render")
_load_submodule("api")
_load_submodule("http_runtime")

from dashboard.config import (  # noqa: E402
    ALERTS_FILE,
    DATA_FILE,
    IDLE_SECONDS,
    POLL_INTERVAL,
    PORT,
    SERVER_JSON_PATH,
    TOP_N,
    _DEFAULT_ALERTS_PATH,
    _DEFAULT_PATH,
    _DEFAULT_SERVER_JSON_PATH,
    _PERIOD_DELTAS,
    _PERMISSION_NOTIFICATION_TYPES,
    _SKILL_USAGE_EVENT_TYPES,
    _resolve_idle_seconds,
    _resolve_poll_interval,
    _resolve_port,
)
from dashboard.aggregate import (  # noqa: E402
    PERMISSION_LINK_WINDOW_SECONDS,
    TOP_N_SKILL_INVOCATION,
    TOP_N_SKILL_LIFECYCLE,
    _HIBERNATING_ACTIVE_DAYS,
    _HIBERNATING_RESTING_DAYS,
    _LIFECYCLE_NEW_THRESHOLD_DAYS,
    _MAX_ALERTS,
    _attribute_permission,
    _filter_events_by_period,
    _filter_usage_events,
    _normalize_skill_name,
    _now_iso,
    _parse_iso_utc,
    _resolve_skills_dir,
    _skill_event_interval,
    aggregate_compact_density,
    aggregate_daily,
    aggregate_hourly_heatmap,
    aggregate_permission_breakdowns,
    aggregate_project_skill_matrix,
    aggregate_projects,
    aggregate_session_stats,
    aggregate_skill_cooccurrence,
    aggregate_skill_hibernating,
    aggregate_skill_invocation_breakdown,
    aggregate_skill_lifecycle,
    aggregate_skills,
    aggregate_subagents,
    load_events,
    load_health_alerts,
)
from dashboard.api import build_dashboard_data  # noqa: E402
from dashboard.render import (  # noqa: E402
    _CSS_FILES,
    _HTML_TEMPLATE,
    _MAIN_JS_FILES,
    _TEMPLATE_DIR,
    _build_html_template,
    _concat_main_js,
    render_static_html,
)
from dashboard.http_runtime import (  # noqa: E402
    DashboardHandler,
    DashboardServer,
    SSEClient,
    _FileWatcher,
    _IdleTracker,
    _SSE_PEER_CHECK_INTERVAL,
    _SseState,
    _file_lock,
    _lock_path_for,
    _peer_disconnected,
    _pid_matches,
    create_server,
    main,
    remove_server_json,
    run,
    server_registry,
    write_server_json,
)

# 分割前の server.py は analyzer/ 由来の関数群もトップレベル名前空間に束ねていた
# (`from analyzer.subagent import ...` / `from analyzer.cost import ...`)。
# 外部 importer / テストが `mod.aggregate_subagent_metrics` のように参照するため
# shim でも同じ surface を維持する。
from analyzer.cost import (  # noqa: E402
    TOP_N_SESSIONS,
    aggregate_model_distribution,
    aggregate_session_breakdown,
)
from analyzer.subagent import (  # noqa: E402
    _bucket_events,
    _build_invocations,
    _pair_invocations_with_stops,
    aggregate_subagent_failure_trend,
    aggregate_subagent_metrics,
    usage_invocation_events,
    usage_invocation_intervals,
)

__all__ = [
    "ALERTS_FILE",
    "DATA_FILE",
    "IDLE_SECONDS",
    "POLL_INTERVAL",
    "PORT",
    "SERVER_JSON_PATH",
    "TOP_N",
    "_DEFAULT_ALERTS_PATH",
    "_DEFAULT_PATH",
    "_DEFAULT_SERVER_JSON_PATH",
    "_PERIOD_DELTAS",
    "_PERMISSION_NOTIFICATION_TYPES",
    "_SKILL_USAGE_EVENT_TYPES",
    "_resolve_idle_seconds",
    "_resolve_poll_interval",
    "_resolve_port",
    "PERMISSION_LINK_WINDOW_SECONDS",
    "TOP_N_SKILL_INVOCATION",
    "TOP_N_SKILL_LIFECYCLE",
    "_HIBERNATING_ACTIVE_DAYS",
    "_HIBERNATING_RESTING_DAYS",
    "_LIFECYCLE_NEW_THRESHOLD_DAYS",
    "_MAX_ALERTS",
    "_attribute_permission",
    "_filter_events_by_period",
    "_filter_usage_events",
    "_normalize_skill_name",
    "_now_iso",
    "_parse_iso_utc",
    "_resolve_skills_dir",
    "_skill_event_interval",
    "aggregate_compact_density",
    "aggregate_daily",
    "aggregate_hourly_heatmap",
    "aggregate_permission_breakdowns",
    "aggregate_project_skill_matrix",
    "aggregate_projects",
    "aggregate_session_stats",
    "aggregate_skill_cooccurrence",
    "aggregate_skill_hibernating",
    "aggregate_skill_invocation_breakdown",
    "aggregate_skill_lifecycle",
    "aggregate_skills",
    "aggregate_subagents",
    "load_events",
    "load_health_alerts",
    "build_dashboard_data",
    "_CSS_FILES",
    "_HTML_TEMPLATE",
    "_MAIN_JS_FILES",
    "_TEMPLATE_DIR",
    "_build_html_template",
    "_concat_main_js",
    "render_static_html",
    "DashboardHandler",
    "DashboardServer",
    "SSEClient",
    "_FileWatcher",
    "_IdleTracker",
    "_SSE_PEER_CHECK_INTERVAL",
    "_SseState",
    "_file_lock",
    "_lock_path_for",
    "_peer_disconnected",
    "_pid_matches",
    "create_server",
    "main",
    "remove_server_json",
    "run",
    "server_registry",
    "write_server_json",
    "TOP_N_SESSIONS",
    "aggregate_model_distribution",
    "aggregate_session_breakdown",
    "_bucket_events",
    "_build_invocations",
    "_pair_invocations_with_stops",
    "aggregate_subagent_failure_trend",
    "aggregate_subagent_metrics",
    "usage_invocation_events",
    "usage_invocation_intervals",
]


if __name__ == "__main__":
    main()
