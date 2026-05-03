# Fork-and-detach launcher pattern

`hooks/launch_dashboard.py` / `hooks/launch_archive.py` のような **hook-triggered な fork-and-detach launcher** を書く・デバッグするときのレシピ集。`subprocess.Popen(..., start_new_session=True, stdin/stdout/stderr=DEVNULL)` 構造に固有の失敗モード別に章立てしてある。本プロジェクトの 2 つの launcher (dashboard / archive) で踏み抜いた gotcha を一次情報としてまとめたもの。

## Section index

| Section | Trigger |
|---|---|
| Race-window absorption (pid-trust > healthz) | Idempotent launcher; double-spawn under hook firing rate |
| Silent-crash debug pattern | Child dies silently; parent sees `exit 0`; reproduces only on CI |
| Parent stdout = user-voice channel | Need to surface URL / status to user from a Claude Code hook |
| Silent-fail contract with carve-out | Adding output to a previously-silent script |
| Spawn-race recovery + pid-match read | Launcher reads a registration file; stale records possible |
| Restart vs idempotent spawn — separate scripts SRP | Daemon already running but needs to pick up new code (template change, code reload) |

---

## 1. Race-window absorption — pid-trust over healthz

The naive predicate `pid alive AND healthz 200 → don't spawn; else → spawn` has a **double-spawn race window**: between `write_server_json()` and `serve_forever()` in the daemon, hooks fire and observe `pid alive + healthz fail`, which spawns a second instance. Both children fight to overwrite server.json, leaving an orphan.

### Fix

Bias the launcher's "is alive?" predicate toward **trusting pid liveness**: if pid is alive, return `True` (don't spawn) regardless of healthz outcome, after a short healthz retry (3 × 50 ms). The deadlock-recovery path is delegated to ops (manual `kill`) — accepted tradeoff vs the permanent risk of orphan processes from the race.

```python
def _server_is_alive(server_json_path: Path) -> bool:
    info = _read_server_json(server_json_path)
    if info is None:
        return False
    pid = info.get("pid")
    if pid is None or not _pid_alive(pid):
        return False
    # pid alive — try healthz with bounded retry; final result doesn't gate spawn
    for _ in range(3):
        if _healthz_ok(info.get("url")):
            return True
        time.sleep(0.05)
    return True  # pid alive trumps healthz failure
```

### Pure predicate + explicit cleanup

The original implementation deleted `server.json` inside the predicate when pid was dead. Reviewers flagged this as surprising. Split into:

- `_server_is_alive(...)` — pure, no side effects
- `_remove_stale_server_json(...)` — explicit verb-named cleanup, called from `main()` when predicate is False

### Manual entrypoint must share the launcher

When a daemon has both **auto-start** (hook) and **manual** (CLI / slash command) entrypoints, route manual through the same launcher. Direct daemon invocation is a debug-only escape hatch with a "check before invoke" warning, not the normal user path. Two entrypoints with separate idempotency checks always drift.

### Detach essentials checklist

For the launcher's `Popen`:

- **Detach the child process group** — POSIX: `start_new_session=True`. Windows: `start_new_session=True` is a **silent no-op** (the child stays bound to the parent's job/console)、代わりに `creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` を渡す。本プロジェクトでは `hooks/_launcher_common.py` に OS 別ディスパッチを集約しており、`getattr(subprocess, "DETACHED_PROCESS", 0)` 等で POSIX import 互換も担保している。
- `stdin/stdout/stderr=DEVNULL` (don't inherit parent pipes — Claude Code's hook stdout/stderr would otherwise leak; needed on both OSes)
- `close_fds=True` (POSIX default but worth being explicit; harmless on Windows)
- Wrap in `try/except OSError` and silently swallow — launcher must not block the host process under any failure mode

### Regression test

Write the race-window test directly:

```python
def test_alive_pid_with_persistent_healthz_failure_returns_true_to_avoid_double_spawn(tmp_path):
    # pid that is guaranteed alive, url that is guaranteed unreachable
    server_json_path = tmp_path / "server.json"
    server_json_path.write_text(json.dumps({
        "pid": os.getpid(),
        "url": "http://127.0.0.1:1",  # unbound port
    }))
    assert _server_is_alive(server_json_path) is True  # no spawn
```

The unit test "persistent healthz failure → True when pid alive" is the canonical regression for this race.

---

## 2. Silent-crash debug pattern — staged instrumentation

A launcher reports `exit 0` while the spawned child never wrote its expected `server.json`. With `stdin/stdout/stderr=DEVNULL` on the Popen call, the child's death is completely silent — no stack trace, no exit code propagation, no log artifact. Standard "add a print and re-run" debugging doesn't work when parent and child don't share IO.

The pattern: **two-round staged instrumentation** with debug-only env vars, removed in a single cleanup commit after the fix lands.

### Round 1 — route child output to a file

Add a debug env var (`_LAUNCH_DASHBOARD_CHILD_LOG` — leading underscore signals internal/temporary) that, when set, replaces `DEVNULL` with `open(path, "w")`. In the test:

```python
def test_launcher_smoke(tmp_path):
    log_path = tmp_path / "child.log"
    env = os.environ | {"_LAUNCH_DASHBOARD_CHILD_LOG": str(log_path)}
    result = subprocess.run([...], env=env, capture_output=True, text=True)
    if not (tmp_path / "server.json").exists():
        text = log_path.read_text() if log_path.exists() else "(no log file)"
        pytest.fail(
            f"---- child stdout+stderr ({len(text)} bytes) ----\n{text}"
        )
```

### Interpreting the file size

- **0 bytes** → child died before its first print (look at fork / exec / very-early imports)
- **>0 bytes with traceback** → child started, read the traceback
- **>0 bytes with traces but no expected final trace** → blocked between last trace and expected final

A 0-byte child.log is **diagnostically valuable** — it rules out import errors and stack-trace-on-startup, narrowing the suspect window to "before the first print".

### Round 2 — named trace points

Add named trace prints around each suspected blocking call (with `flush=True`):

```python
print("[trace] _spawn_server: about to Popen", flush=True)
proc = subprocess.Popen(...)
print("[trace] _spawn_server: Popen returned", flush=True)
```

Use module-distinct prefixes (`[trace]` for launcher, `[server-trace]` for child) so multi-process logs are separable by `grep`.

### Binary search via gap

The gap between two adjacent traces *is* the bug location. Push, wait one CI run, read the file, narrow.

### Cleanup commit

The cleanup commit is **part of the pattern, not an afterthought**. Shipping production code with `_DEBUG_TRACE` env-var checks left in is scope creep. Discipline:

> Diagnose → fix → revert the diagnostic scaffolding in the same PR.

Title the cleanup commit clearly so reviewers see the symmetric instrument-then-remove flow.

### CI-only repros

Don't try to reproduce locally first — by definition the bug is environment-specific, so local-first wastes time. Push round 1 → wait one CI run (~3 min) → push round 2 if needed.

### 適用範囲についての注意

本プロジェクトでは Python の `subprocess.Popen + DEVNULL` 構成で運用したパターン。原理 (file-based output + named-step tracing + ephemeral env-var gating) は IO 切断を伴う detach 起動全般 (shell `&` + redirect / `nohup` / systemd 等) に応用できそうだが、本プロジェクト内では Python 以外で検証していない。

---

## 3. Parent stdout = the only user-voice channel (Claude Code hooks)

A Claude Code hook that fork-and-detaches a daemon **cannot communicate anything to the user from the child process**. The DEVNULL redirection is required to detach from parent pipes — without it, the child blocks the hook's `< 100ms` budget — but the side effect is that the child has no audible voice for the rest of its lifetime.

This is non-obvious because the architecture *looks* like it has output channels (a server can `print()` and write logs), but those outputs land in `/dev/null` from the user's perspective. The dashboard URL, error states, and "I just started" announcements all evaporate.

### The only window

The launcher process's **stdout** before it exits — that's the standard Claude Code hook output channel. JSON like:

```json
{"systemMessage": "📊 Dashboard: http://localhost:53421"}
```

is rendered into the user's transcript.

### Decision happens in the launcher

State-aware messaging (e.g. "URL only on first spawn / SessionStart / idle resume") must be decided by the launcher *before* exiting. The detached child cannot make that decision later. The launcher reads incoming hook stdin JSON, checks `hook_event_name`, and emits accordingly:

```python
hook_input = json.loads(sys.stdin.read())
hook_event = hook_input.get("hook_event_name")
if just_spawned or hook_event == "SessionStart":
    print(json.dumps({"systemMessage": f"📊 Dashboard: {url}"}))
```

### Invariants to keep

- **Launcher silent exit 0** is **not** the same as "silent stdout" — the launcher *can* write hook output JSON in `< 1 ms` and still meet the `< 100ms` budget
- **Side files** (e.g. `server.json`) are debug/ops fallback, not a UX channel — the user has to manually `cat` them
- **stdout reserved** for hook output JSON; **stderr and child fds are DEVNULL by design** — document this in the launcher header

### When you need mid-session messages from the child

Reach for a different mechanism — a separate hook (e.g. `PostToolUse`) that re-reads child state and emits — not the original launcher. Fork-and-detach is a one-way valve.

### 適用範囲

本プロジェクトでは `launch_dashboard.py` と `launch_archive.py` の 2 つの hook-triggered launcher で同じ制約を踏んでいる。Claude Code hook の DEVNULL detach 構造に由来する制約なので、同種の launcher を新たに足す場合は同じパターンが効くはず (本プロジェクト外への汎化は未検証)。

---

## 4. Silent-fail contract with conditional relaxation

A hook script with a strong invariant — "any exception → silent exit 0" (don't block the host) — sometimes needs to **add stdout output for one specific success path**. The contract should be **relaxed conditionally, not dropped**.

### The relaxation form

Original:

> "Any exception → silent exit 0"

Relaxed:

> "Any exception → silent exit 0; success path X → one-line hook output JSON to stdout"

The relaxation is **explicit and bounded**. "Silent fail except for X" is maintainable; "no longer silent fail" loses the original safety property.

### Three pins to prevent re-violation

1. **Module docstring** declares both the original invariant AND the conditional carve-out. Any future PR touching stdout writes has to read this and make a deliberate choice.
2. **Code comment at the emit site** ties the carve-out to its origin Issue (`# Issue #34: emit dashboard URL on first spawn / SessionStart`) so the rationale survives docstring shrinking.
3. **Test class pinning the structure** — assert in every code path:
   - `out.count("\n") <= 1` (single line)
   - `json.loads(out.strip())` succeeds (strict JSON parse)
   - Expected top-level key (`systemMessage`) matches a prefix
   - `err == ""` (stderr stays empty in every path)

```python
class TestSystemMessageStructure:
    def test_first_spawn_emits_one_line_json(self): ...
    def test_already_alive_silent_path_emits_nothing(self): ...
    def test_no_print_to_stderr_in_any_path(self):
        for case in ALL_CASES:
            assert run(case).stderr == ""
```

### `print()` is forbidden

For hook scripts, **never use `print()` for debug** — `print()` writes to stdout and silently corrupts the hook output JSON protocol. Use:

- `sys.stderr.write(...)` if anywhere visible is needed
- An env-gated jsonl debug log (`DASHBOARD_DEBUG_HOOK_EVENT=1`) — observability without protocol corruption

### Format-fragile protocols need structural pins

String-equality tests pass even when a BOM or trailing whitespace breaks the protocol. Assert the **structure**, not the bytes.

### When in doubt

Write the test before the code. The test's assertion phrasing makes the contract explicit, which is harder to do with English-only docs.

---

## 5. Spawn-race recovery — Optional[Popen] + pid-match read

A "spawn a server, then read its registration file (pid / port / url) to notify the user" workflow has a structural race where a **stale registration file from a previously crashed instance** can be misread as the newly-spawned child's. Result: user-visible misnotifications (wrong URL, "running" indicator pointing at a dead pid).

### The 3-state correctness shape

Polling a file written by a child you just spawned is a 3-state problem, not 2:

1. **Popen failed** → no child exists
2. **Popen succeeded, child hasn't written yet** → keep polling
3. **Popen succeeded, child has written** → file matches `proc.pid`

Without distinguishing state 1 from state 2, you poll for nothing while reading a stale file from a previous crash.

### Pattern

```python
def _spawn_server() -> Optional[subprocess.Popen]:
    try:
        return subprocess.Popen([...], **detach_kwargs)
    except OSError:
        return None  # OSError might be PermissionError, fork-limit, sandbox restriction

def main() -> int:
    proc = _spawn_server()
    if proc is None:
        return 0  # any registration file we'd find is by definition not ours
    _wait_for_registration(server_json_path, self_pid=proc.pid)
    return 0

def _wait_for_registration(path: Path, self_pid: int, timeout_s: float = 0.25) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        info = _read_server_json(path)
        if info and info.get("pid") == self_pid:  # pid-match — ours, not stale
            return
        time.sleep(0.05)
```

### Two rules that close the race

- **Writer side** (zombie cleanup) — "compare-and-delete": delete only if pid matches what we're trying to delete
- **Reader side** (this pattern) — "pid-match-on-read": ignore records whose pid is not ours, regardless of how recent their `started_at` looks

Both are needed. Cleanup alone leaves broken JSON / non-dict files alone (to avoid TOCTOU with concurrent writers), so a non-dict file from a previous crashed write can sit there and be picked up by a peek-style read. The pid-match check closes that hole.

### Poll budget

The `timeout_s` must be **shorter than the next-hook firing interval** so a missed notification can recover on the next hook. 250 ms is fine inside a hook that's expected to fire on every user prompt anyway.

### Test triple

| Case | Expected |
|---|---|
| (a) no file | spawn, wait, succeed when child writes |
| (b) file with my pid | use as-is |
| (c) file with **other** pid | ignore + spawn |

Case (c) is the one that gets forgotten. A test that only covers (a) and (b) will not catch the regression introduced if someone removes the pid-match check during a future refactor.

### Document the rationale in code

```python
# pid-match: a stale registration file from a previously-crashed instance
# may sit here with broken JSON shape that _remove_stale_server_json
# deliberately preserves (TOCTOU avoidance). Without this check, we'd
# misread it as our just-spawned child's file.
if info.get("pid") != self_pid:
    continue
```

Without the comment, a future maintainer may "simplify" by dropping the pid match.

---

## 6. Restart vs idempotent spawn — separate scripts SRP

An idempotent launcher's "if already running, do nothing" contract is what makes it safe to call dozens of times per hook firing. Adding a "restart if newer code" branch to the launcher leaks an explicit kill+respawn operation into a path designed for silent idempotency, breaking the original safety property.

The right move is a **separate restart script** with a different contract:

- explicit error output to stderr (`[restart] sending SIGTERM to dashboard pid=12345`)
- non-zero exit on failure (kill couldn't terminate → exit 1, no respawn — prevents double-instance)
- subprocess-call into the launcher to reuse spawn logic (DRY without compromising the launcher's silent-fail contract)
- separate verb-based slash command (`/restart-X`, not `/X-launcher restart`) so the operation is visible at the user-facing trigger level

Same end-state (a fresh daemon with new code), two scripts with two responsibilities.

### When to keep them separate

When extending a daemon launcher with a new operation, ask: "does this new operation share the silent-fail / idempotent contract?" If no → new script. The hidden coupling that makes hook-fired entry points safe to call N times per session is what the new operation would break.

Subprocess-call beats shared-module reuse when the two callers have different stdout/stderr discipline. The launcher subprocess gets `capture_output=True` and `input=b"{}"`; the restart script writes its own stderr lines. No need to duplicate the lock-protected spawn / atomic-write / fork-and-detach logic.

### Signal escalation ladder — separate constants for separate intents

In the restart script's SIGTERM → SIGKILL escalation, **never reuse a single timeout constant for both stages**. The two timeouts are independent design parameters with non-overlapping semantics:

```python
GRACEFUL_TIMEOUT_SECONDS = 5.0   # SIGTERM: drain SSE connections, etc.
KILL_TIMEOUT_SECONDS = 0.5       # SIGKILL: OS-level immediate; just confirm reap
```

- `GRACEFUL_TIMEOUT_SECONDS` is bounded by user-perceived UX — don't make the user wait too long for a graceful drain.
- `KILL_TIMEOUT_SECONDS` is bounded by polling overhead alone — the kernel reaps the process before `kill(2)` returns; no graceful drain to wait for. 0.5 s is plenty.

Reusing `GRACEFUL_TIMEOUT_SECONDS` for the SIGKILL wait silently doubles the worst-case stuck-process timeout (5 + 5 = 10 s when 5 + 0.5 = 5.5 s is correct). Harmless on green paths, surfaces only when something's actually hung — exactly when the user least wants extra seconds.

Name timeouts after their semantic intent (`GRACEFUL_TIMEOUT`, `KILL_TIMEOUT`), not their value (`TIMEOUT_5S` or a single shared `WAIT_TIMEOUT`). If you find yourself reusing a timeout constant from a different signal stage, write a comment justifying it (you usually can't, which is the point) — or split the constant.

### Test discipline — pin the contract boundary

- `test_run_launcher_silent_when_server_json_does_not_appear` — confirms the restart path doesn't fabricate a URL when spawn fails. The launcher's silent-fallback discipline carries through into the restart script.
- `test_terminate_uses_short_timeout_after_sigkill` — records the actual `timeout` argument passed to `_wait_for_pid_exit` for each signal stage and asserts they're different. Catches accidental constant-reuse the moment the test reads the source.

A separate script also makes the restart operation testable in isolation: kill+wait+respawn is hard to unit-test inside a hook handler, but trivial in a standalone script that mocks `os.kill` and `subprocess.run`.
