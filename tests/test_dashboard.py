"""tests/test_dashboard.py — dashboard/server.py のテスト"""
import importlib.util
import json
import os
import socketserver
import threading
import urllib.request
from pathlib import Path

import pytest

_DASHBOARD_PATH = Path(__file__).parent.parent / "dashboard" / "server.py"


def load_dashboard_module(usage_jsonl: Path):
    """USAGE_JSONL をパッチした状態で dashboard モジュールを読み込む。"""
    os.environ["USAGE_JSONL"] = str(usage_jsonl)
    try:
        spec = importlib.util.spec_from_file_location("dashboard_server", _DASHBOARD_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        del os.environ["USAGE_JSONL"]
    return mod


def write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


class TestBuildDashboardData:
    def test_empty_events_returns_valid_structure(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        data = mod.build_dashboard_data([])
        assert data["total_events"] == 0
        assert data["skill_ranking"] == []
        assert data["subagent_ranking"] == []
        assert data["daily_trend"] == []
        assert data["project_breakdown"] == []
        assert "last_updated" in data

    def test_total_events_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert data["total_events"] == 2

    def test_skill_ranking_sorted_by_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "commit", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "commit", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "skill_tool", "skill": "review", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:02:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert data["skill_ranking"][0] == {"name": "commit", "count": 2}
        assert data["skill_ranking"][1] == {"name": "review", "count": 1}

    def test_skill_ranking_includes_user_slash_command(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "user_slash_command", "skill": "/insights", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "commit", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        names = [r["name"] for r in data["skill_ranking"]]
        assert "/insights" in names
        assert "commit" in names

    def test_subagent_ranking_sorted_by_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Plan", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:02:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert data["subagent_ranking"][0] == {"name": "Explore", "count": 2}
        assert data["subagent_ranking"][1] == {"name": "Plan", "count": 1}

    def test_daily_trend_grouped_by_date_sorted_asc(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s", "timestamp": "2026-01-02T10:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "b", "project": "p", "session_id": "s", "timestamp": "2026-01-01T15:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "c", "project": "p", "session_id": "s", "timestamp": "2026-01-01T10:00:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert data["daily_trend"][0] == {"date": "2026-01-01", "count": 2}
        assert data["daily_trend"][1] == {"date": "2026-01-02", "count": 1}

    def test_daily_trend_excludes_events_without_timestamp(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "a", "project": "p", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "b", "project": "p", "session_id": "s"},
        ]
        data = mod.build_dashboard_data(events)
        assert len(data["daily_trend"]) == 1
        assert data["daily_trend"][0]["count"] == 1

    def test_project_breakdown_sorted_by_count(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        events = [
            {"event_type": "skill_tool", "skill": "a", "project": "proj-a", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event_type": "skill_tool", "skill": "b", "project": "proj-a", "session_id": "s", "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event_type": "subagent_start", "subagent_type": "Explore", "project": "proj-b", "session_id": "s", "timestamp": "2026-01-01T00:02:00+00:00"},
        ]
        data = mod.build_dashboard_data(events)
        assert data["project_breakdown"][0] == {"project": "proj-a", "count": 2}
        assert data["project_breakdown"][1] == {"project": "proj-b", "count": 1}


class TestHTTPEndpoints:
    def _start_server(self, mod):
        server = socketserver.TCPServer(("127.0.0.1", 0), mod.DashboardHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever)
        t.daemon = True
        t.start()
        return server, port

    def test_api_data_returns_json_with_correct_structure(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        write_events(usage_file, [
            {"event_type": "skill_tool", "skill": "commit", "project": "proj", "session_id": "s", "timestamp": "2026-01-01T00:00:00+00:00"},
        ])
        mod = load_dashboard_module(usage_file)
        server, port = self._start_server(mod)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/data") as resp:
                assert resp.status == 200
                assert "application/json" in resp.headers["Content-Type"]
                data = json.loads(resp.read())
                assert data["total_events"] == 1
                assert data["skill_ranking"][0]["name"] == "commit"
        finally:
            server.shutdown()
            server.server_close()

    def test_api_data_empty_when_no_file(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server, port = self._start_server(mod)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/data") as resp:
                assert resp.status == 200
                data = json.loads(resp.read())
                assert data["total_events"] == 0
        finally:
            server.shutdown()
            server.server_close()

    def test_html_endpoint_returns_200(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server, port = self._start_server(mod)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
                assert resp.status == 200
                assert "text/html" in resp.headers["Content-Type"]
                body = resp.read().decode("utf-8")
                assert "<!DOCTYPE html>" in body
        finally:
            server.shutdown()
            server.server_close()

    def test_html_endpoint_handles_any_path(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server, port = self._start_server(mod)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/unknown") as resp:
                assert resp.status == 200
                assert "text/html" in resp.headers["Content-Type"]
        finally:
            server.shutdown()
            server.server_close()
