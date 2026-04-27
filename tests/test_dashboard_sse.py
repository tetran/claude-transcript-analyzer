"""tests/test_dashboard_sse.py — Issue #20 Phase B: ライブダッシュボードの SSE 配信テスト。

`tests/test_dashboard_live.py` (Phase A) と同じく `load_dashboard_module` を再利用。
Phase B のスコープ:
- `/events` エンドポイント (text/event-stream)
- usage.jsonl の (inode, size, mtime) を 1 秒間隔でポーリングし、変化検知時に
  全 SSE クライアントへ `data: refresh\\n\\n` をブロードキャスト
- SSE 接続中は idle watchdog がサーバーを終了させない
"""
# pylint: disable=line-too-long
import json
import socket
import threading
import time
from dataclasses import dataclass

from test_dashboard import load_dashboard_module


def _start_server_in_thread(server) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


@dataclass
class SseConn:
    """raw socket ベースの SSE クライアント。

    `http.client.HTTPConnection.close()` は macOS で FIN 送信が遅延するケースがあり、
    サーバー側の peer-disconnect 検知テストが動かない。raw socket + SHUT_WR で
    即座に FIN を送るほうが実機ブラウザ close の挙動に近い。
    """
    sock: socket.socket
    status: int
    headers: dict

    def read_some(self, max_bytes: int = 256, timeout: float = 2.0) -> bytes:
        self.sock.settimeout(timeout)
        try:
            return self.sock.recv(max_bytes)
        except (socket.timeout, OSError):
            return b""

    def disconnect(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def _open_sse(host: str, port: int, timeout: float = 3.0) -> SseConn:
    """`/events` を raw socket で開き、レスポンスヘッダまで読んだ SseConn を返す。"""
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.sendall(f"GET /events HTTP/1.1\r\nHost: {host}\r\nAccept: text/event-stream\r\n\r\n".encode())
    f = sock.makefile("rb", buffering=0)
    status_line = f.readline().rstrip(b"\r\n")
    parts = status_line.split(b" ", 2)
    status = int(parts[1]) if len(parts) >= 2 else 0
    headers = {}
    while True:
        line = f.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        k, _, v = line.rstrip(b"\r\n").partition(b":")
        headers[k.decode("latin1").lower()] = v.strip().decode("latin1")
    return SseConn(sock=sock, status=status, headers=headers)


class TestEventsEndpoint:
    """Phase B: `/events` が SSE ストリームを返す。"""

    def test_events_returns_text_event_stream_headers(self, tmp_path):
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        port = server.server_address[1]
        _start_server_in_thread(server)
        c = None
        try:
            c = _open_sse("127.0.0.1", port)
            assert c.status == 200
            ctype = c.headers.get("content-type", "")
            assert "text/event-stream" in ctype, f"Content-Type: {ctype!r}"
            cache_ctl = c.headers.get("cache-control", "")
            assert "no-cache" in cache_ctl, f"Cache-Control: {cache_ctl!r}"
        finally:
            if c is not None:
                c.disconnect()
            server.shutdown()
            server.server_close()

    def test_events_sends_initial_comment_ping(self, tmp_path):
        """サーバー → クライアントの最初のバイトとして comment 行 (`:` 始まり) を flush。

        - ブラウザ EventSource の onopen を即座に発火させる
        - SSE 仕様で comment は `event` を発火しないので無害
        """
        mod = load_dashboard_module(tmp_path / "nonexistent.jsonl")
        server = mod.create_server(port=0, idle_seconds=0)
        port = server.server_address[1]
        _start_server_in_thread(server)
        c = None
        try:
            c = _open_sse("127.0.0.1", port)
            chunk = c.read_some(max_bytes=128, timeout=2.0)
            assert chunk.startswith(b":"), f"先頭が comment 行で始まっていない: {chunk!r}"
            # SSE のメッセージ区切り `\n\n` で終わっている
            assert b"\n\n" in chunk, f"メッセージ区切りが無い: {chunk!r}"
        finally:
            if c is not None:
                c.disconnect()
            server.shutdown()
            server.server_close()


class TestRefreshBroadcast:
    """Phase B: usage.jsonl 変更を検知し全 SSE クライアントへ refresh を配信する。"""

    def test_usage_jsonl_change_broadcasts_refresh(self, tmp_path):
        usage = tmp_path / "usage.jsonl"
        usage.write_text("", encoding="utf-8")  # 空ファイルから開始
        mod = load_dashboard_module(usage)
        # poll_interval を短くしてテストを早める
        server = mod.create_server(port=0, idle_seconds=0, poll_interval=0.1)
        port = server.server_address[1]
        _start_server_in_thread(server)
        c = None
        try:
            c = _open_sse("127.0.0.1", port)
            # 接続確立 (initial comment) を捨てる
            c.read_some(max_bytes=128, timeout=2.0)
            # 接続が server に登録されるまで少し待つ
            time.sleep(0.3)
            # usage.jsonl を append 改変 → mtime/size が変わる
            with usage.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "event_type": "skill_tool", "skill": "x", "args": "",
                    "project": "p", "session_id": "s",
                    "timestamp": "2026-04-27T00:00:00+00:00",
                }) + "\n")
            # poll 間隔 + 配信遅延を見込んで 2s deadline で読む
            data = c.read_some(max_bytes=256, timeout=2.0)
            assert b"data: refresh" in data, f"refresh が届かなかった: {data!r}"
        finally:
            if c is not None:
                c.disconnect()
            server.shutdown()
            server.server_close()

    def test_broadcast_survives_single_client_disconnect(self, tmp_path):
        """1 クライアントが切断しても、生きている他クライアントへの配信は継続する。"""
        usage = tmp_path / "usage.jsonl"
        usage.write_text("", encoding="utf-8")
        mod = load_dashboard_module(usage)
        server = mod.create_server(port=0, idle_seconds=0, poll_interval=0.1)
        port = server.server_address[1]
        _start_server_in_thread(server)
        a = b = None
        try:
            a = _open_sse("127.0.0.1", port)
            b = _open_sse("127.0.0.1", port)
            a.read_some(128, 1.0)
            b.read_some(128, 1.0)
            time.sleep(0.3)
            # A を強制切断 (FIN を即送る)
            a.disconnect()
            a = None
            time.sleep(0.2)
            # ファイル変更
            with usage.open("a", encoding="utf-8") as f:
                f.write("{}\n")
            data_b = b.read_some(256, 2.0)
            assert b"data: refresh" in data_b, f"B に refresh が届かなかった: {data_b!r}"
        finally:
            for c in (a, b):
                if c is not None:
                    c.disconnect()
            server.shutdown()
            server.server_close()

    def test_no_refresh_when_file_unchanged(self, tmp_path):
        """ファイル変更が無いときは data: refresh を送らない（ping/comment は許容）。"""
        usage = tmp_path / "usage.jsonl"
        usage.write_text("", encoding="utf-8")
        mod = load_dashboard_module(usage)
        server = mod.create_server(port=0, idle_seconds=0, poll_interval=0.1)
        port = server.server_address[1]
        _start_server_in_thread(server)
        c = None
        try:
            c = _open_sse("127.0.0.1", port)
            c.read_some(128, 1.0)
            time.sleep(0.5)  # poll が複数回回るのに十分
            extra = c.read_some(256, 0.3)
            assert b"data: refresh" not in extra, f"変更なしで refresh が届いた: {extra!r}"
        finally:
            if c is not None:
                c.disconnect()
            server.shutdown()
            server.server_close()


class TestSseIdleCounter:
    """Phase B: SSE 接続中は idle watchdog でサーバーを終了させない。"""

    def test_sse_connection_keeps_server_alive_past_idle_seconds(self, tmp_path):
        usage = tmp_path / "usage.jsonl"
        usage.write_text("", encoding="utf-8")
        mod = load_dashboard_module(usage)
        # idle_seconds を非常に短く: 0.2s。SSE 接続があれば落ちない
        server = mod.create_server(port=0, idle_seconds=0.2, poll_interval=0.1)
        port = server.server_address[1]
        t = _start_server_in_thread(server)
        c = None
        try:
            c = _open_sse("127.0.0.1", port)
            c.read_some(128, 1.0)
            # idle_seconds の 4 倍待ってもまだ alive
            time.sleep(0.8)
            assert t.is_alive(), "SSE 接続中なのに idle watchdog で終了した"
        finally:
            if c is not None:
                c.disconnect()
            server.shutdown()
            server.server_close()
            t.join(timeout=2.0)

    def test_sse_disconnect_lets_idle_shutdown_resume(self, tmp_path):
        """SSE クライアントが全て切断した後は idle 経過で graceful shutdown する。

        keepalive を短く (50ms) して切断検知を早める（実機 default は 15s）。
        """
        usage = tmp_path / "usage.jsonl"
        usage.write_text("", encoding="utf-8")
        mod = load_dashboard_module(usage)
        server = mod.create_server(
            port=0, idle_seconds=0.3, poll_interval=0.1, sse_keepalive=0.05,
        )
        port = server.server_address[1]
        t = _start_server_in_thread(server)
        c = None
        try:
            c = _open_sse("127.0.0.1", port)
            c.read_some(128, 1.0)
            time.sleep(0.2)
            # 切断 (FIN を即送る)
            c.disconnect()
            c = None
            # idle (0.3) + watchdog 周期 + keepalive 検知の余裕を見て待つ
            t.join(timeout=3.0)
            assert not t.is_alive(), "SSE 切断後も watchdog で終了しなかった"
        finally:
            if c is not None:
                c.disconnect()
            server.server_close()
