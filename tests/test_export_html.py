"""tests/test_export_html.py — render_static_html() と export_html.py のテスト。"""
# pylint: disable=line-too-long
import importlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

# dashboard モジュールへのパスを通す
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# render_static_html() のテスト
# ---------------------------------------------------------------------------

class TestRenderStaticHtml:
    def _import(self):
        import dashboard.server as m
        importlib.reload(m)
        return m

    def test_returns_html_string(self):
        m = self._import()
        data = {"total_events": 5, "skill_ranking": []}
        html = m.render_static_html(data)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_contains_window_data_script(self):
        m = self._import()
        data = {"total_events": 42, "skill_ranking": [{"name": "commit", "count": 3}]}
        html = m.render_static_html(data)
        assert "window.__DATA__" in html

    def test_window_data_matches_input(self):
        m = self._import()
        data = {
            "total_events": 7,
            "skill_ranking": [{"name": "review", "count": 2}],
            "subagent_ranking": [],
        }
        html = m.render_static_html(data)
        # window.__DATA__ = {...}; の JSON 部分を取り出して検証
        marker = "window.__DATA__ = "
        idx = html.index(marker) + len(marker)
        end = html.index(";</script>", idx)
        embedded = json.loads(html[idx:end])
        assert embedded["total_events"] == 7
        assert embedded["skill_ranking"][0]["name"] == "review"

    def test_script_inserted_before_head_close(self):
        m = self._import()
        data = {}
        html = m.render_static_html(data)
        script_pos = html.index("window.__DATA__")
        head_close_pos = html.index("</head>")
        assert script_pos < head_close_pos

    def test_static_html_no_server_required(self):
        """静的 HTML は /api/data fetch なしでデータを持つことを確認。"""
        m = self._import()
        data = {"total_events": 1}
        html = m.render_static_html(data)
        assert "window.__DATA__" in html


# ---------------------------------------------------------------------------
# _HTML_TEMPLATE の JavaScript が window.__DATA__ フォールバックを持つことのテスト
# ---------------------------------------------------------------------------

class TestHtmlTemplateFallback:
    def _import(self):
        import dashboard.server as m
        importlib.reload(m)
        return m

    def test_template_has_window_data_check(self):
        m = self._import()
        assert "window.__DATA__" in m._HTML_TEMPLATE

    def test_template_has_dynamic_fallback_fetch(self):
        """動的ダッシュボードとしても機能するよう fetch フォールバックが残っていることを確認。"""
        m = self._import()
        assert "fetch('/api/data'" in m._HTML_TEMPLATE

    def test_template_prefers_window_data_over_fetch(self):
        """window.__DATA__ が fetch より先に参照されることを確認。"""
        m = self._import()
        tmpl = m._HTML_TEMPLATE
        window_data_pos = tmpl.index("window.__DATA__")
        fetch_pos = tmpl.index("fetch('/api/data'")
        assert window_data_pos < fetch_pos

    def test_template_uses_static_badge_for_window_data(self):
        """codex Finding 1 回帰: 静的 export では接続バッジを 'static' state に固定する。

        修正前は EventSource 結線が `window.__DATA__` 経路で丸ごとスキップされ、
        初期 'reconnect' 表示のまま固まっていた。
        """
        m = self._import()
        tmpl = m._HTML_TEMPLATE
        # static 用 CSS が定義されている
        assert '[data-state="static"]' in tmpl
        # static 用ラベルと setConnStatus('static') 呼び出しが入っている
        assert "static:" in tmpl
        assert "setConnStatus('static')" in tmpl


# ---------------------------------------------------------------------------
# reports/export_html.py の main() のテスト
# ---------------------------------------------------------------------------

class TestExportHtmlMain:
    def _import_export_html(self):
        import dashboard.server as server_m
        importlib.reload(server_m)
        import reports.export_html as m
        importlib.reload(m)
        return m

    def test_main_creates_file(self, tmp_path):
        out = tmp_path / "report.html"
        events_file = tmp_path / "usage.jsonl"
        events_file.write_text(
            '{"event_type": "skill_tool", "skill": "commit", "project": "proj", "session_id": "s1", "timestamp": "2026-01-01T00:00:00+00:00"}\n',
            encoding="utf-8",
        )
        env = {
            "USAGE_JSONL": str(events_file),
            "HEALTH_ALERTS_JSONL": str(tmp_path / "alerts.jsonl"),
        }
        with mock.patch.dict(os.environ, env):
            m = self._import_export_html()
            m.main(["--output", str(out)])
        assert out.exists()

    def test_main_output_contains_html(self, tmp_path):
        out = tmp_path / "report.html"
        events_file = tmp_path / "usage.jsonl"
        events_file.write_text("", encoding="utf-8")
        env = {
            "USAGE_JSONL": str(events_file),
            "HEALTH_ALERTS_JSONL": str(tmp_path / "alerts.jsonl"),
        }
        with mock.patch.dict(os.environ, env):
            m = self._import_export_html()
            m.main(["--output", str(out)])
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "window.__DATA__" in content

    def test_main_default_output_path(self, tmp_path, monkeypatch):
        """--output 未指定時のデフォルト出力先を確認する。"""
        events_file = tmp_path / "usage.jsonl"
        events_file.write_text("", encoding="utf-8")
        default_out = tmp_path / "report.html"
        env = {
            "USAGE_JSONL": str(events_file),
            "HEALTH_ALERTS_JSONL": str(tmp_path / "alerts.jsonl"),
        }
        with mock.patch.dict(os.environ, env):
            m = self._import_export_html()
            # デフォルトパスを tmp_path 内に向ける
            monkeypatch.setattr(m, "_DEFAULT_OUTPUT", default_out)
            m.main([])
        assert default_out.exists()

    def test_main_prints_output_path(self, tmp_path, capsys):
        out = tmp_path / "report.html"
        events_file = tmp_path / "usage.jsonl"
        events_file.write_text("", encoding="utf-8")
        env = {
            "USAGE_JSONL": str(events_file),
            "HEALTH_ALERTS_JSONL": str(tmp_path / "alerts.jsonl"),
        }
        with mock.patch.dict(os.environ, env):
            m = self._import_export_html()
            m.main(["--output", str(out)])
        captured = capsys.readouterr()
        assert str(out) in captured.out

    def test_main_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "nested" / "deep" / "report.html"
        events_file = tmp_path / "usage.jsonl"
        events_file.write_text("", encoding="utf-8")
        env = {
            "USAGE_JSONL": str(events_file),
            "HEALTH_ALERTS_JSONL": str(tmp_path / "alerts.jsonl"),
        }
        with mock.patch.dict(os.environ, env):
            m = self._import_export_html()
            m.main(["--output", str(out)])
        assert out.exists()

    def test_main_html_embeds_events(self, tmp_path):
        out = tmp_path / "report.html"
        events_file = tmp_path / "usage.jsonl"
        events_file.write_text(
            "\n".join([
                '{"event_type": "skill_tool", "skill": "myskill", "project": "p", "session_id": "s1", "timestamp": "2026-01-02T00:00:00+00:00"}',
                '{"event_type": "skill_tool", "skill": "myskill", "project": "p", "session_id": "s2", "timestamp": "2026-01-03T00:00:00+00:00"}',
            ]) + "\n",
            encoding="utf-8",
        )
        env = {
            "USAGE_JSONL": str(events_file),
            "HEALTH_ALERTS_JSONL": str(tmp_path / "alerts.jsonl"),
        }
        with mock.patch.dict(os.environ, env):
            m = self._import_export_html()
            m.main(["--output", str(out)])
        content = out.read_text(encoding="utf-8")
        assert '"total_events": 2' in content
        assert "myskill" in content


class TestRenderStaticHtmlSecurity:
    def _import(self):
        import dashboard.server as m
        importlib.reload(m)
        return m

    def test_script_tag_in_data_is_escaped(self):
        """データに </script> が含まれても HTML が壊れないことを確認。"""
        m = self._import()
        data = {"skill_ranking": [{"name": "</script>", "count": 1}]}
        html = m.render_static_html(data)
        # JSON データ部分を取り出す
        marker = "window.__DATA__ = "
        idx = html.index(marker) + len(marker)
        end = html.index(";</script>", idx)
        embedded_json = html[idx:end]
        # </script> がエスケープされとること
        assert "</script>" not in embedded_json
        assert r"<\/script>" in embedded_json

