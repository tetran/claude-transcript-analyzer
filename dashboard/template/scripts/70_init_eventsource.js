  // ---- 初回描画 + EventSource (live refresh) ----
  // 初回描画も scheduleLoadAndRender 経由で統一。Init 中に hashchange が割り込んでも
  // serialization wrapper が coalesce してくれる (25_live_diff.js を参照)。
  await scheduleLoadAndRender();
  if (typeof window.__DATA__ !== 'undefined') {
    // 静的 export 経路では EventSource を起動せず、バッジを「静的レポート」表示に固定。
    // heartbeat も start() 不要 (setConnStatus('static') 経由で hidden 確定)。
    setConnStatus('static');
  } else if (typeof EventSource !== 'undefined') {
    // Issue #83: live 経路で初めて heartbeat を起動 (= shell.html の default `hidden` を解除)。
    if (window.__heartbeat) window.__heartbeat.start();
    setConnStatus('reconnect');
    let offlineTimer = null;
    let firstError = null;
    const OFFLINE_AFTER_MS = 30000;
    const es = new EventSource('/events');
    es.addEventListener('open', () => {
      setConnStatus('online');
      firstError = null;
      if (offlineTimer) { clearTimeout(offlineTimer); offlineTimer = null; }
    });
    es.addEventListener('error', () => {
      // EventSource は readyState=CONNECTING 中に自動再接続を試みる
      if (es.readyState === EventSource.CONNECTING) {
        setConnStatus('reconnect');
        if (firstError === null) firstError = Date.now();
        if (!offlineTimer) {
          const remaining = Math.max(0, OFFLINE_AFTER_MS - (Date.now() - firstError));
          offlineTimer = setTimeout(() => setConnStatus('offline'), remaining);
        }
      } else {
        setConnStatus('offline');
      }
    });
    es.addEventListener('message', (ev) => {
      // payload は "refresh" のみだが拡張余地として弁別。
      // scheduleLoadAndRender 経由で並行 refresh を直列化する
      // (stale-snapshot race 対策 / 25_live_diff.js を参照)。
      if (typeof ev.data === 'string' && ev.data.indexOf('refresh') !== -1) {
        // Issue #83: refresh 受信を契機に heartbeat に山を 1 発立てる。
        if (window.__heartbeat) window.__heartbeat.bump();
        scheduleLoadAndRender().catch(err => console.error('refresh 失敗', err));
      }
    });
  }

