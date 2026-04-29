  // ---- 初回描画 + EventSource (live refresh) ----
  await loadAndRender();
  if (typeof window.__DATA__ !== 'undefined') {
    // 静的 export 経路では EventSource を起動せず、バッジを「静的レポート」表示に固定
    setConnStatus('static');
  } else if (typeof EventSource !== 'undefined') {
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
      // payload は "refresh" のみだが拡張余地として弁別
      if (typeof ev.data === 'string' && ev.data.indexOf('refresh') !== -1) {
        loadAndRender().catch(err => console.error('refresh 失敗', err));
      }
    });
  }

