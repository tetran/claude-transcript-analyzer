"""tests/test_platform_process.py — analyzer/platform/process.py の seam テスト (Issue #121)。

OS 別 detach (spawn_detached) と pid 生存確認 (is_pid_alive) を直接ユニットテストする。
launch_dashboard / launch_archive / restart_dashboard が共有する process seam の
契約を pin し、OS 分岐の重複排除が behavior-preserving であることを担保する。
"""
import os
import subprocess
import sys
from unittest import mock

import analyzer.platform.process as process


class TestSpawnDetached:
    def test_posix_uses_start_new_session(self):
        """POSIX: start_new_session=True で親 PG/SID から切り離す。creationflags は渡さない。"""
        with mock.patch.object(process.sys, "platform", "linux"), \
             mock.patch.object(process.subprocess, "Popen") as popen:
            process.spawn_detached(["echo", "hi"])
        kwargs = popen.call_args.kwargs
        assert kwargs.get("start_new_session") is True
        assert "creationflags" not in kwargs

    def test_win32_uses_creationflags(self):
        """Windows: DETACHED_PROCESS(0x8) | CREATE_NEW_PROCESS_GROUP(0x200)。"""
        with mock.patch.object(process.sys, "platform", "win32"), \
             mock.patch.object(process.subprocess, "Popen") as popen:
            process.spawn_detached(["echo", "hi"])
        kwargs = popen.call_args.kwargs
        assert "start_new_session" not in kwargs
        cf = kwargs.get("creationflags", 0)
        assert cf & 0x00000008, f"DETACHED_PROCESS が無い: cf={cf:#x}"
        assert cf & 0x00000200, f"CREATE_NEW_PROCESS_GROUP が無い: cf={cf:#x}"

    def test_stdio_redirected_to_devnull(self):
        """親 hook の pipe を引き継がない。"""
        with mock.patch.object(process.subprocess, "Popen") as popen:
            process.spawn_detached(["echo", "hi"])
        kwargs = popen.call_args.kwargs
        assert kwargs.get("stdin") == subprocess.DEVNULL
        assert kwargs.get("stdout") == subprocess.DEVNULL
        assert kwargs.get("stderr") == subprocess.DEVNULL
        assert kwargs.get("close_fds") is True

    def test_oserror_returns_none(self):
        """Popen 失敗 (OSError) は silent fail で None を返す。"""
        with mock.patch.object(process.subprocess, "Popen", side_effect=OSError("boom")):
            assert process.spawn_detached(["echo"]) is None


class TestIsPidAlive:
    def test_self_pid_is_alive(self):
        assert process.is_pid_alive(os.getpid()) is True

    def test_dead_pid_returns_false(self):
        # 確実に存在しない pid: 子を spawn して回収後の pid を使う
        with subprocess.Popen([sys.executable, "-c", "pass"]) as proc:
            proc.wait()
        assert process.is_pid_alive(proc.pid) is False
