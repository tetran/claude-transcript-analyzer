"""tests/test_launch_archive.py

hooks/launch_archive.py の SessionStart hook 経由 archive job auto-launcher
(Issue #30 Phase C) のテスト。

不変条件:
- どんな例外でも silent exit 0
- < 100ms 既起動経路 (state read のみ — TestPerformance)
- 月跨ぎ判定: state 不在 / 壊れた JSON / 古い月 → spawn、当月 / 前月以降 → skip
- spawn は fork-and-detach で start_new_session=True
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
HOOKS_DIR = PROJECT_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

LAUNCH_ARCHIVE_PY = HOOKS_DIR / "launch_archive.py"


@pytest.fixture(name="launch_archive_module")
def _launch_archive_module_fixture(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCHIVE_STATE_FILE", str(tmp_path / ".archive_state.json"))
    sys.modules.pop("launch_archive", None)
    import launch_archive
    monkeypatch.setattr(launch_archive, "STATE_FILE", tmp_path / ".archive_state.json")
    return launch_archive


def _utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# TestNeedsArchive: 状態 → spawn 判定
# ---------------------------------------------------------------------------


class TestNeedsArchive:
    def test_no_state_file_means_needs_archive(self, launch_archive_module, tmp_path):
        """state 不在 → 未実行扱いで True (= spawn)."""
        state_file = tmp_path / ".archive_state.json"
        # state file 不在
        assert launch_archive_module._needs_archive(state_file, _utc(2026, 4, 15)) is True

    def test_corrupted_state_means_needs_archive(self, launch_archive_module, tmp_path):
        state_file = tmp_path / ".archive_state.json"
        state_file.write_text("not valid json {{{")
        assert launch_archive_module._needs_archive(state_file, _utc(2026, 4, 15)) is True

    def test_non_dict_state_means_needs_archive(self, launch_archive_module, tmp_path):
        state_file = tmp_path / ".archive_state.json"
        state_file.write_text(json.dumps(["array", "not", "dict"]))
        assert launch_archive_module._needs_archive(state_file, _utc(2026, 4, 15)) is True

    def test_state_at_previous_month_skips(self, launch_archive_module, tmp_path):
        """state.last_archived_month == 前月 → skip (spawn 不要)."""
        state_file = tmp_path / ".archive_state.json"
        # now = 2026-04, 前月 = 2026-03
        state_file.write_text(json.dumps({"last_archived_month": "2026-03"}))
        assert launch_archive_module._needs_archive(state_file, _utc(2026, 4, 15)) is False

    def test_state_at_current_month_skips(self, launch_archive_module, tmp_path):
        """state.last_archived_month == 当月 → skip (実害ないため)."""
        state_file = tmp_path / ".archive_state.json"
        state_file.write_text(json.dumps({"last_archived_month": "2026-04"}))
        assert launch_archive_module._needs_archive(state_file, _utc(2026, 4, 15)) is False

    def test_state_at_old_month_means_needs_archive(self, launch_archive_module, tmp_path):
        """state.last_archived_month が 2 ヶ月以上前 → spawn."""
        state_file = tmp_path / ".archive_state.json"
        # now = 2026-04, last = 2026-01
        state_file.write_text(json.dumps({"last_archived_month": "2026-01"}))
        assert launch_archive_module._needs_archive(state_file, _utc(2026, 4, 15)) is True

    def test_state_with_invalid_month_format_means_needs_archive(self, launch_archive_module, tmp_path):
        state_file = tmp_path / ".archive_state.json"
        state_file.write_text(json.dumps({"last_archived_month": "garbage"}))
        assert launch_archive_module._needs_archive(state_file, _utc(2026, 4, 15)) is True

    def test_year_boundary_january_after_december(self, launch_archive_module, tmp_path):
        """now=2027-01, last=2026-12 → 前月扱いで skip."""
        state_file = tmp_path / ".archive_state.json"
        state_file.write_text(json.dumps({"last_archived_month": "2026-12"}))
        assert launch_archive_module._needs_archive(state_file, _utc(2027, 1, 15)) is False

    def test_no_archived_with_horizon_at_current_skips(self, launch_archive_module, tmp_path):
        """archive 対象なしで run 終了 (last_archived_month 未設定) のあと、
        同じ archivable horizon を再観測したら skip (毎セッション spawn 防止)。

        codex 6th P2: 旧実装は last_run_at の calendar month で skip していたため、
        retention boundary が月末を跨いで新 eligibility が立った場合に最大 30 日
        spawn が抑止される bug があった。horizon 比較ベースに変更で解消。
        """
        state_file = tmp_path / ".archive_state.json"
        # now=2026-04-27, retention=180, cutoff=2025-10-29, horizon=2025-09
        state_file.write_text(json.dumps({
            "last_run_at": "2026-04-15T10:00:00+00:00",
            "last_archivable_horizon": "2025-09",
            # last_archived_month は意図的に欠落 (= no-op 完了)
        }))
        assert launch_archive_module._needs_archive(state_file, _utc(2026, 4, 27)) is False

    def test_no_archived_with_advanced_horizon_means_needs_archive(self, launch_archive_module, tmp_path):
        """codex 6th P2: **同月内で** retention boundary が月末を跨いで eligibility
        horizon が advance したら spawn する (旧実装の同月 skip bug の本丸ケース)。

        旧実装は last_run_at == this_month で問答無用 skip していたため、
        retention boundary が月末を跨いだ瞬間に立つ archive 対象が次の calendar
        月まで遅延していた。horizon 比較に変えることでこの遅延を解消。
        """
        state_file = tmp_path / ".archive_state.json"
        # 5/1 に走った時点: cutoff=2025-11-02, horizon=previous_month(2025,11)=2025-10
        state_file.write_text(json.dumps({
            "last_run_at": "2026-05-01T00:00:00+00:00",
            "last_archivable_horizon": "2025-10",
        }))
        # 5/30 (同 5 月内): cutoff=2025-12-01, horizon=previous_month(2025,12)=2025-11 (advanced)
        # 旧実装は last_run_at が 5 月内なので skip(=False) を返してしまう → bug
        assert launch_archive_module._needs_archive(state_file, _utc(2026, 5, 30)) is True

    def test_old_archived_with_horizon_covered_skips(self, launch_archive_module, tmp_path):
        """last_archived_month < 前月 でも last_archivable_horizon が現在の horizon を
        既にカバーしているなら skip (= no-op だったが新 eligibility は無し)。
        """
        state_file = tmp_path / ".archive_state.json"
        # now=2026-04-27, retention=180, cutoff=2025-10-29, horizon=2025-09
        state_file.write_text(json.dumps({
            "last_run_at": "2026-04-15T10:00:00+00:00",
            "last_archived_month": "2026-01",  # 過去の archived は新しい
            "last_archivable_horizon": "2025-09",  # 現在 horizon と一致
        }))
        # last_archived_month=2026-01 >= 前月 (2026-03)? No (older). → fall through to horizon check
        # last_archivable_horizon=2025-09 == current horizon=2025-09 → skip ✓
        assert launch_archive_module._needs_archive(state_file, _utc(2026, 4, 27)) is False

    def test_old_archived_with_horizon_advanced_means_needs_archive(self, launch_archive_module, tmp_path):
        """last_archived_month < 前月 + horizon が advance → 月跨ぎ retry で spawn."""
        state_file = tmp_path / ".archive_state.json"
        state_file.write_text(json.dumps({
            "last_run_at": "2026-03-15T10:00:00+00:00",
            "last_archived_month": "2026-01",
            "last_archivable_horizon": "2025-08",  # 古い horizon
        }))
        # now=2026-04-15, cutoff~2025-10-17, horizon=previous_month(2025,10)=2025-09 (advanced)
        assert launch_archive_module._needs_archive(state_file, _utc(2026, 4, 15)) is True

    def test_legacy_state_without_horizon_means_needs_archive(self, launch_archive_module, tmp_path):
        """codex 6th P2 後方互換: last_archivable_horizon を持たない旧 state は
        保守的に spawn する (= 一度走らせて horizon を書かせる)。
        """
        state_file = tmp_path / ".archive_state.json"
        # last_archived_month は前月でないので fast skip も効かない
        state_file.write_text(json.dumps({
            "last_run_at": "2026-04-15T10:00:00+00:00",
            "last_archived_month": "2026-01",
            # last_archivable_horizon 欠落 (旧 schema)
        }))
        assert launch_archive_module._needs_archive(state_file, _utc(2026, 4, 27)) is True

    def test_no_state_file_means_needs_archive_even_with_run_at(self, launch_archive_module, tmp_path):
        """state 不在のときは horizon の検査も走らない (data 未初期化扱い)."""
        state_file = tmp_path / ".archive_state.json"
        # state file 不在
        assert launch_archive_module._needs_archive(state_file, _utc(2026, 4, 15)) is True


# ---------------------------------------------------------------------------
# TestPerformance: 既起動経路 (= spawn 不要判定) は < 100ms
# ---------------------------------------------------------------------------


class TestPerformance:
    def test_skip_path_under_100ms(self, launch_archive_module, tmp_path):
        """state を見て skip 判定する経路は I/O 1 件で < 100ms."""
        state_file = tmp_path / ".archive_state.json"
        state_file.write_text(json.dumps({"last_archived_month": "2026-04"}))

        durations = []
        for _ in range(20):
            start = time.perf_counter()
            launch_archive_module._needs_archive(state_file, _utc(2026, 4, 15))
            durations.append(time.perf_counter() - start)
        durations.sort()
        p95 = durations[int(len(durations) * 0.95)]
        assert p95 < 0.1, f"_needs_archive p95 {p95 * 1000:.2f}ms exceeds 100ms"


# ---------------------------------------------------------------------------
# TestSilentExitZero: どんな例外でも exit 0
# ---------------------------------------------------------------------------


class TestSilentExitZero:
    def test_main_returns_zero_on_state_read_error(self, launch_archive_module, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("simulated catastrophic failure")
        monkeypatch.setattr(launch_archive_module, "_needs_archive", boom)
        assert launch_archive_module.main() == 0

    def test_main_returns_zero_on_spawn_failure(self, launch_archive_module, monkeypatch):
        # state 不在 → needs True path、ただし spawn は失敗する想定
        monkeypatch.setattr(launch_archive_module, "_spawn_archive_job", lambda: None)
        assert launch_archive_module.main() == 0


# ---------------------------------------------------------------------------
# TestSpawnLogic: needs True → spawn が呼ばれる、needs False → 呼ばれない
# ---------------------------------------------------------------------------


class TestSpawnLogic:
    def test_needs_archive_triggers_spawn(self, launch_archive_module, monkeypatch):
        monkeypatch.setattr(launch_archive_module, "_needs_archive", lambda *a, **kw: True)
        called: list[bool] = []

        def fake_spawn():
            called.append(True)
            return mock.MagicMock(pid=99999)

        monkeypatch.setattr(launch_archive_module, "_spawn_archive_job", fake_spawn)
        launch_archive_module.main()
        assert called == [True]

    def test_no_needs_skips_spawn(self, launch_archive_module, monkeypatch):
        monkeypatch.setattr(launch_archive_module, "_needs_archive", lambda *a, **kw: False)
        called: list[bool] = []

        def fake_spawn():
            called.append(True)
            return mock.MagicMock(pid=99999)

        monkeypatch.setattr(launch_archive_module, "_spawn_archive_job", fake_spawn)
        launch_archive_module.main()
        assert called == []

    def test_spawn_archive_job_runs_on_windows(self, launch_archive_module, monkeypatch):
        """Issue #44: 旧 codex P2 では archive_usage.py が POSIX fcntl 限定だった
        ため Windows では launcher 側で skip していた。`_lock` 経路の追加で
        Windows でも archive_usage が state を更新できるようになったので、
        Windows でも spawn される (skip しない)。

        spawn_detached を mock して「呼ばれた」ことだけ確認する (実機 Popen は
        platform 差で creationflags の妥当性が変わるため)。
        """
        monkeypatch.setattr(launch_archive_module.sys, "platform", "win32")
        called: list[list[str]] = []

        def fake_spawn(args, **kwargs):
            called.append(args)
            return mock.MagicMock(pid=99999)

        monkeypatch.setattr(launch_archive_module, "spawn_detached", fake_spawn)
        result = launch_archive_module._spawn_archive_job()
        assert result is not None
        assert len(called) == 1
        assert "archive_usage.py" in called[0][1]


# ---------------------------------------------------------------------------
# TestSmokeIntegration: 実 subprocess.run 経由で起動 → state 更新
# ---------------------------------------------------------------------------


class TestSmokeIntegration:
    def test_session_start_invocation_spawns_archive_job(self, tmp_path):
        """launch_archive を subprocess.run で起動 → archive_usage が detach 起動 →
        最終的に state marker と archive ファイルが生成される (実機相当)."""
        data_file = tmp_path / "usage.jsonl"
        archive_dir = tmp_path / "archive"
        state_file = tmp_path / ".archive_state.json"

        old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        old_event = {
            "event_type": "skill_tool",
            "session_id": "s_smoke",
            "timestamp": old_ts,
            "tool_use_id": "t_old",
        }
        with data_file.open("w", encoding="utf-8") as f:
            f.write(json.dumps(old_event) + "\n")

        env = os.environ.copy()
        env.update(
            {
                "USAGE_JSONL": str(data_file),
                "ARCHIVE_DIR": str(archive_dir),
                "ARCHIVE_STATE_FILE": str(state_file),
                "USAGE_JSONL_LOCK": str(tmp_path / "usage.jsonl.lock"),
                "USAGE_RETENTION_DAYS": "1",
                "HEALTH_ALERTS_JSONL": str(tmp_path / "health_alerts.jsonl"),
            }
        )

        result = subprocess.run(
            [sys.executable, str(LAUNCH_ARCHIVE_PY)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        # launcher 自体は < 100ms で exit 0 (spawn は detach なので待たない)
        assert result.returncode == 0

        # detach した子プロセス (archive_usage) が完了するまで polling
        deadline = time.time() + 5
        while time.time() < deadline:
            if state_file.exists() and (archive_dir.exists() and any(archive_dir.glob("*.jsonl.gz"))):
                break
            time.sleep(0.1)

        assert state_file.exists(), "state marker が作られなかった (archive_job が detach 起動していない)"
        archived = list(archive_dir.glob("*.jsonl.gz"))
        assert len(archived) == 1


# ---------------------------------------------------------------------------
# TestLauncherCommon: spawn_detached が POSIX で start_new_session=True を渡すこと
# ---------------------------------------------------------------------------


class TestLauncherCommon:
    def test_spawn_detached_uses_new_session_on_posix(self, monkeypatch):
        """POSIX では start_new_session=True が Popen に渡される."""
        sys.modules.pop("_launcher_common", None)
        import _launcher_common

        captured: dict = {}

        class FakePopen:
            def __init__(self, args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
                self.pid = 12345

        monkeypatch.setattr(_launcher_common.subprocess, "Popen", FakePopen)
        monkeypatch.setattr(_launcher_common.sys, "platform", "linux")

        proc = _launcher_common.spawn_detached(["python", "/some/script.py"])
        assert proc is not None
        assert captured["kwargs"].get("start_new_session") is True
        assert captured["kwargs"].get("close_fds") is True

    def test_spawn_detached_returns_none_on_oserror(self, monkeypatch):
        sys.modules.pop("_launcher_common", None)
        import _launcher_common

        def raising_popen(*args, **kwargs):
            raise OSError("fork failed")

        monkeypatch.setattr(_launcher_common.subprocess, "Popen", raising_popen)
        proc = _launcher_common.spawn_detached(["python"])
        assert proc is None
