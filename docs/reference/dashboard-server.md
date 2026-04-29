# Dashboard サーバー実装 — SSE / HTML embed / component composition

`dashboard/server.py` の実装上の非自明ポイントをまとめたリファレンス。stdlib のみで Server-Sent Events を提供し、`window.__DATA__` で初期データを注入し、pylint 違反を `.pylintrc` に逃さず合理的に解消した経緯。

---

## §1. stdlib http.server で SSE を実装する

`BaseHTTPRequestHandler` + `ThreadingHTTPServer` で `/events` を実装するときに、どの単一ドキュメントにも書かれていない 3 つの非自明要件。

### 要件 1: リクエストループの抑止

```python
def do_GET(self):
    if self.path == "/events":
        self.close_connection = True   # ← これが無いと ConnectionResetError
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        ...
```

`self.close_connection = True` を SSE handler の冒頭で立てる。これが無いと `do_GET` が return した後 `BaseHTTPRequestHandler.handle()` が次の `handle_one_request()` にループし、0 byte read で `ConnectionResetError` を stderr に吐く。

### 要件 2: ブロックしない peer disconnect 検知

```python
def _peer_disconnected(sock):
    """True if EOF/RST observed; False otherwise."""
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        if not readable:
            return False
        data = sock.recv(1, socket.MSG_PEEK)
        return data == b""  # EOF
    except (BlockingIOError, InterruptedError):
        return False
    except OSError:
        return True  # RST
```

`select.select([sock], [], [], 0)` + `sock.recv(1, MSG_PEEK)` で `self.connection` を非ブロッキング検査。EOF は `b""`、RST は `OSError`。

**書き込み失敗 (BrokenPipeError) を peer-disconnect 検知に使ってはいけない** — TCP send buffer が大量データを吸収し、`BrokenPipeError` が立つまでに数秒の遅延がある。

### 要件 3: peer-check と keepalive のケイデンス分離

```python
_SSE_PEER_CHECK_INTERVAL = 1.0  # seconds — 1 か所に集約するモジュール定数

# Naive (悪い):
while alive:
    send_keepalive()
    stop_event.wait(keepalive_seconds)   # 15s 寝る → disconnect 検知も 15s 遅れる

# Correct:
last_keepalive = time.monotonic()
while alive:
    tick = min(keepalive_seconds, _SSE_PEER_CHECK_INTERVAL)
    if stop_event.wait(tick):
        break
    if _peer_disconnected(self.connection):
        break
    now = time.monotonic()
    if 0 < keepalive_seconds <= now - last_keepalive:  # chained compare (R1716)
        send_keepalive()
        last_keepalive = now
```

ポイント：

- `stop_event.wait(tick)` でブロック時間を `_SSE_PEER_CHECK_INTERVAL=1.0` に制限
- Keepalive 送信は **時刻ベース**（`now - last_keepalive >= keepalive_seconds`）で判定
- `keepalive_seconds=0` は「無限・送らない」のセンチネル。`if 0 < keepalive <= now - last_keepalive` の **chained 比較** で扱う（pylint R1716 にも適合）

### 形式と broadcast

- SSE comment（`: ...\n\n`）は `onmessage` を発火しない。**ヘッダ flush 直後の初回送信** と **keepalive ping** に使う（`onopen` を即座に発火させる用途）
- 複数クライアント broadcast はスナップショットパターンで安全化：

```python
def broadcast(self, payload):
    with self._lock:
        snapshot = list(self._clients)
    dead = []
    for client in snapshot:
        try:
            client.send(payload)
        except (BrokenPipeError, OSError):
            dead.append(client)
    if dead:
        with self._lock:
            for c in dead:
                self._clients.discard(c)
```

ロック内でスナップショット → ロック外で送信 → ロック内で死亡クライアント reap。長い write が他の登録をブロックしない。

### スケール限界

`select.select` は数〜数十クライアントまで。100+ クライアントが現実味を帯びたら asyncio に移行。stdlib SSE は **≤10 同時接続** の dashboards に最適な pragmatic bridge。

---

## §2. JSON-in-`<script>` 埋め込みの escape

`window.__DATA__ = { ... }` で初期データを HTML に埋め込むときの **必要かつ十分な** escape は次の 1 行：

```python
script_payload = json.dumps(data).replace("</", r"<\/")
html = f"<script>window.__DATA__ = {script_payload};</script>"
```

これだけで HTML5 script-data-state の **両方の脱出経路** を閉じる。

### HTML5 script の脱出経路（仕様 § 12.2.5.4）

1. **Path 1（直接）**: `</script>` end-tag が即時に script を閉じる
2. **Path 2（comment 経由）**: `<!--` で script-data-escaped state に入り、その状態でも `</script>` を見ると閉じる

両方とも `</` を必ず含むので、`</` を `<\/` にすれば 1 発で塞がる。

### よくある間違い: 過剰 escape で JSON を壊す

`<!--` を別途エスケープしようとして `replace("<!--", r"<\!--")` を追加すると、`\!` が **JSON RFC 8259 違反** で `JSON.parse` がエラーを吐き、dashboard が white-screen する。

JSON RFC 8259 § 7 で valid な string escape は `\"`, `\\`, `\/`, `\b`, `\f`, `\n`, `\r`, `\t`, `\uXXXX` の **9 種のみ**。`\!`, `\$`, `\<` 等はすべて parse error。

しかも `<!--` 単体（`</script>` を伴わない）は実際には script context を抜けない（script-data-escaped state から出るには `</script>` が必要）。よって `<!--` の defensive escape は **冗長**。

### Round-trip 検証

defensive escape を 2 つ以上重ねたくなったら、必ず JSON.parse 往復を assert：

```python
import json
result = json.dumps(input).replace(...)  # escape チェーン
assert json.loads(result) == input  # round-trip 必須
```

文字列の中に `<\!--` が現れる」だけを assert する単体テストでは byte-level 等価性しか見ないので、`JSON.parse` 失敗を検出できない。

### `<!--` を文字列として完全保護したい場合（稀）

`replace("</", ...)` で塞がらないケースが本当に必要なら、JSON Unicode escape を使う：

```python
# JSON-valid: < で < を encode
result = json.dumps(input).replace("<", r"<")
```

これは round-trip するし、`<!--` も `<!--` になって HTML parser に届かない。

---

## §3. Component composition pattern（pylint R0902 対策）

`dashboard/server.py` の class が `>7 self.X = ...` で R0902 (`too-many-instance-attributes`) に触れたとき、**コンポーネント分解 + `@property` shim** で解消。3-tier escalation policy（refactor → local-disable → `.pylintrc` tweak）の最上位「refactor」で着地した実例。policy 自体は **global `~/.claude/CLAUDE.md` の Scope Discipline** に記載されており、本ドキュメントは project 固有の **architecture decision の記録**。

### 分解後の構造

| Component | 責務 |
|---|---|
| `_IdleTracker` | idle 時間の touch/idle_for/watchdog |
| `_SseState` | SSE clients / lock / keepalive 状態 |
| `_FileWatcher` | usage.jsonl の path / poll interval / 監視 thread |

各 component は private（`_idle`, `_sse`, `_watcher`）。public API はサーバークラス側に **1 行 delegate** メソッドを持たせて維持：

```python
class DashboardServer(...):
    def __init__(self, ...):
        # Components MUST be initialized BEFORE super().__init__()
        # (super() may call self.server_close() on bind failure;
        #  if components aren't ready, AttributeError masks the original OSError)
        self._idle = _IdleTracker(...)
        self._sse = _SseState(...)
        self._watcher = _FileWatcher(...)
        super().__init__(...)

    def touch(self):                    # 1-line delegate
        self._idle.touch()

    @property
    def idle_seconds(self):             # backward-compat shim for existing tests
        return self._idle.idle_seconds
```

### `@property` shim の効果

- 既存テストが `server.idle_seconds` / `server.poll_interval` で読んでいた public 属性を **そのまま維持** できる（テスト書き換え不要）
- `@property` getter は pylint の instance-attribute count に **入らない** ので R0902 が解ける
- CPython 3.11+ で関数呼び出し 1 回分のオーバーヘッドのみ。dashboard のスケール（≤10 req/s）では不可視

### 初期化順序の罠

```python
def __init__(self, ...):
    # ❌ NG: super() が先だと bind 失敗時に AttributeError でマスクされる
    super().__init__(...)
    self._idle = _IdleTracker(...)

    # ✅ OK: components が先
    self._idle = _IdleTracker(...)
    super().__init__(...)
```

`super().__init__()` は bind 失敗時に防御的に `self.server_close()` を呼びうる。`server_close` が component 属性を参照していると、未初期化で `AttributeError` が立ち、本来の `OSError`（ポート衝突など）の情報が失われる。**Components init → super().__init__() の順序を守るコメントを `__init__` 直上に書くこと。**

### 何故 `__getattr__` を使わないか

`__getattr__` で透過的に component メソッドを呼べそうに見えるが、

- 「どの component の何を呼んでいるのか」が読み手に見えなくなる
- typo した時の error が遅延する（`AttributeError` が `__getattr__` の中で出る）
- pylint / mypy が解析できない

明示的 1 行 delegate のほうが冗長でも保守性で勝つ。

