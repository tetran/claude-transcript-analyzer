"""dashboard/config.py — env 解決 + モジュール定数 (Issue #123 Phase 1).

dashboard/server.py から区画 A を切り出したリーフモジュール。
`DATA_FILE` / `ALERTS_FILE` / `SERVER_JSON_PATH` などの env 依存定数は
import 時に評価される。テストは server.py shim を patch 済み env 下で
reload することでこの再評価を引き起こす。
"""
import os
from pathlib import Path


_DEFAULT_PATH = Path.home() / ".claude" / "transcript-analyzer" / "usage.jsonl"
DATA_FILE = Path(os.environ.get("USAGE_JSONL", str(_DEFAULT_PATH)))

_DEFAULT_ALERTS_PATH = Path.home() / ".claude" / "transcript-analyzer" / "health_alerts.jsonl"
ALERTS_FILE = Path(os.environ.get("HEALTH_ALERTS_JSONL", str(_DEFAULT_ALERTS_PATH)))

_DEFAULT_SERVER_JSON_PATH = Path.home() / ".claude" / "transcript-analyzer" / "server.json"
SERVER_JSON_PATH = Path(os.environ.get("DASHBOARD_SERVER_JSON", str(_DEFAULT_SERVER_JSON_PATH)))


def _resolve_port() -> int:
    """`DASHBOARD_PORT` 未指定 or `0` → OS 任せ、具体値 → そのまま。"""
    raw = os.environ.get("DASHBOARD_PORT")
    if raw is None:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def _resolve_idle_seconds() -> float:
    """`DASHBOARD_IDLE_SECONDS` 未指定 → 600s デフォルト、`0` → 無効。"""
    raw = os.environ.get("DASHBOARD_IDLE_SECONDS")
    if raw is None:
        return 600.0
    try:
        return float(raw)
    except ValueError:
        return 600.0


def _resolve_poll_interval() -> float:
    """`DASHBOARD_POLL_INTERVAL` 未指定 → 1.0s デフォルト、`0` → ファイル監視を無効化。"""
    raw = os.environ.get("DASHBOARD_POLL_INTERVAL")
    if raw is None:
        return 1.0
    try:
        return float(raw)
    except ValueError:
        return 1.0


PORT = _resolve_port()
IDLE_SECONDS = _resolve_idle_seconds()
POLL_INTERVAL = _resolve_poll_interval()

TOP_N = 10

# Notification.notification_type は公式仕様で `permission`、過去実装/テストでは `permission_prompt` を観測。
# 両方を許可ダイアログ系としてカウントする。
_PERMISSION_NOTIFICATION_TYPES = frozenset({"permission", "permission_prompt"})

# total_events / aggregate_daily / aggregate_projects はプロジェクト目的の
# 「Skills と Subagents の使用状況」を示すメトリクスなので、usage 系イベントだけで集計する。
# session_*, notification, instructions_loaded, compact_*, subagent_stop は session_stats /
# health_alerts 等に分かれて表示されるため、ここで二重カウントしない。
_SKILL_USAGE_EVENT_TYPES = frozenset({
    "skill_tool",
    "user_slash_command",
})


_PERIOD_DELTAS = {"7d": 7, "30d": 30, "90d": 90}
