"""dashboard/server.py — re-export shim + エントリポイント (Issue #123 Phase 1).

#123 で server.py を責務単位の 5 サブモジュール (config / aggregate / api /
render / http_runtime) へ分割した。本ファイルは外部 importer
(`reports/` ・ `tests/` ・ `hooks/`) が従来の import パス
(`from dashboard.server import ...` / `spec_from_file_location` 直 import) を
無改修で使い続けられるよう、全公開シンボルを束ねる re-export shim として残す。

tests/test_dashboard.py は本ファイルを `spec_from_file_location` で
**ファイルパス直 import** し、その都度 `USAGE_JSONL` 等の env をパッチして
fresh module として exec する。サブモジュール (`dashboard.config` 等) は
通常のパッケージ import で `sys.modules` にキャッシュされるため、本 shim は
exec のたびにサブモジュールを依存順に reload し、import 時 env 評価
(`config.DATA_FILE` / `render._HTML_TEMPLATE`) を patch 済み env 下で
再評価させる。reload しないと 2 回目以降の load で env override が効かず
テスト隔離が静かに壊れる。
"""
import importlib
import sys
from pathlib import Path

# サブパッケージ import (`import dashboard.config` 等) を解決するため repo root を
# sys.path に積む。本 shim は spec_from_file_location でファイル直 import される
# ため、サブモジュール import より前に必ず最初の実行行として走らせる必要がある。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# サブモジュール import はモジュールトップレベルで行う (遅延 import 禁止)。
# 1 回の exec_module 内で config.py の env 評価・render.py のテンプレート concat 等の
# import 時副作用が、loader がパッチした env 下で一括評価されるようにするため。
import dashboard.config as _config  # noqa: E402
import dashboard.aggregate as _aggregate  # noqa: E402
import dashboard.render as _render  # noqa: E402
import dashboard.api as _api  # noqa: E402
import dashboard.http_runtime as _http_runtime  # noqa: E402

# 依存順 (config → aggregate → render → api → http_runtime) に reload して、
# キャッシュ済みサブモジュールの import 時 env 評価を patch 済み env 下で
# やり直させる。reload を上位から先に回すと下位の `from ... import` が
# 旧バインディングを掴むため、依存順は厳守する。
for _submodule in (_config, _aggregate, _render, _api, _http_runtime):
    importlib.reload(_submodule)

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
