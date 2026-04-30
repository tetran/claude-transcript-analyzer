  // Issue #83: live heartbeat sparkline.
  //
  // ・closure-private state は 15 番ファイル冒頭 (= 全 main_js を wrap する単一
  //   shared IIFE 直下) に置く。20 番以降から `window.__heartbeat` 経由で参照。
  // ・全識別子を `__hb` prefix で名前空間隔離 (Issue #69 の `__livePrev` 等と同じ慣習)。
  // ・別 file (10_helpers.js / 70_init_eventsource.js) からは window.__heartbeat 経由で
  //   API 越しにアクセスする (= __hbState 等を直接 read/write しない)。
  const HB_SAMPLES = 60;
  const HB_VIEW_W = 140;
  const HB_VIEW_H = 22;
  // PQRS 風の単一スパイク shape (上に -9px → 下に +4px → 0px)。10 frames 固定 duration、
  // 強弱は __hbSpikeAmp で別軸制御 (online=1.0 / reconnect=0.3 / offline・static=0.0)。
  const HB_SPIKE_SHAPE = [-2, -5, -9, -7, -3, 2, 4, 3, 1, 0];
  let __hbState = 'idle';
  let __hbBuf = new Float32Array(HB_SAMPLES);
  let __hbSpikeRemain = 0;
  let __hbSpikeAmp = 1.0;
  let __hbRafId = null;
  let __hbReducedMotion = false;

  function __hbDetectReducedMotion() {
    if (typeof window === 'undefined') return false;
    if (typeof window.matchMedia !== 'function') return false;
    try {
      return !!(window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (e) {
      return false;
    }
  }

  function __hbRenderPoly() {
    if (typeof document === 'undefined') return;
    const svg = document.getElementById('heartbeat');
    if (!svg) return;
    const poly = (typeof svg.querySelector === 'function') ? svg.querySelector('polyline') : null;
    if (!poly || typeof poly.setAttribute !== 'function') return;
    const stepX = HB_VIEW_W / (HB_SAMPLES - 1);
    const midY = HB_VIEW_H / 2;
    let pts = '';
    for (let i = 0; i < HB_SAMPLES; i++) {
      pts += (i * stepX).toFixed(2) + ',' + (midY + __hbBuf[i]).toFixed(2);
      if (i < HB_SAMPLES - 1) pts += ' ';
    }
    poly.setAttribute('points', pts);
  }

  function __hbTick() {
    // 1 sample 左シフト + 末尾追加
    for (let i = 0; i < __hbBuf.length - 1; i++) {
      __hbBuf[i] = __hbBuf[i + 1];
    }
    let nextSample = 0;
    if (__hbSpikeRemain > 0) {
      const idx = HB_SPIKE_SHAPE.length - __hbSpikeRemain;
      nextSample = HB_SPIKE_SHAPE[idx] * __hbSpikeAmp;
      __hbSpikeRemain -= 1;
    }
    __hbBuf[__hbBuf.length - 1] = nextSample;
    __hbRenderPoly();
    __hbRafId = requestAnimationFrame(__hbTick);
  }

  function startHeartbeat() {
    if (typeof window === 'undefined') return;
    if (__hbRafId !== null) return;
    __hbReducedMotion = __hbDetectReducedMotion();
    // shell.html の default `hidden` を解除して live 経路で初めて見せる
    const svg = (typeof document !== 'undefined') ? document.getElementById('heartbeat') : null;
    if (svg) svg.hidden = false;
    if (__hbReducedMotion) {
      // flat line を 1 度だけ描画して以降 tick しない
      for (let i = 0; i < __hbBuf.length; i++) __hbBuf[i] = 0;
      __hbRenderPoly();
      return;
    }
    __hbRafId = requestAnimationFrame(__hbTick);
  }

  function stopHeartbeat() {
    if (__hbRafId !== null) {
      cancelAnimationFrame(__hbRafId);
      __hbRafId = null;
    }
  }

  // 全 state で __hbSpikeAmp を明示的に書き込む (state transition 時の amplitude leak 防止)。
  // STATUS_LABEL keys (online / reconnect / offline / static) と 1:1 対応 (test pin)。
  function setHeartbeatState(state) {
    __hbState = state;
    const svg = (typeof document !== 'undefined') ? document.getElementById('heartbeat') : null;
    if (svg) {
      if (svg.dataset) svg.dataset.state = state;
      else if (typeof svg.setAttribute === 'function') svg.setAttribute('data-state', state);
    }
    switch (state) {
      case 'online':
        __hbSpikeAmp = 1.0;
        if (svg) svg.hidden = false;
        if (__hbRafId === null && !__hbReducedMotion && typeof requestAnimationFrame === 'function') {
          __hbRafId = requestAnimationFrame(__hbTick);
        }
        break;
      case 'reconnect':
        __hbSpikeAmp = 0.3;
        if (svg) svg.hidden = false;
        if (__hbRafId === null && !__hbReducedMotion && typeof requestAnimationFrame === 'function') {
          __hbRafId = requestAnimationFrame(__hbTick);
        }
        break;
      case 'offline':
        __hbSpikeAmp = 0.0;
        stopHeartbeat();
        for (let i = 0; i < __hbBuf.length; i++) __hbBuf[i] = 0;
        __hbSpikeRemain = 0;
        __hbRenderPoly();
        if (svg) svg.hidden = false;
        break;
      case 'static':
        __hbSpikeAmp = 0.0;
        stopHeartbeat();
        for (let i = 0; i < __hbBuf.length; i++) __hbBuf[i] = 0;
        __hbSpikeRemain = 0;
        if (svg) svg.hidden = true;
        break;
      default:
        // 未知 state: no-op (defensive)。STATUS_LABEL keys 1:1 は test pin。
        break;
    }
  }

  function bumpHeartbeat() {
    if (__hbReducedMotion) {
      // reduced-motion: 視覚 spike を出さず aria-live 領域に通知。連発時にも
      // 同一文字列の no-op 化を避けるため一旦 '' に戻してから本文を再書き込む
      // (microtask boundary を挟んで DOM diff として確実に発火させる)。
      const sr = (typeof document !== 'undefined') ? document.getElementById('heartbeatSr') : null;
      if (sr) {
        sr.textContent = '';
        Promise.resolve().then(function () { sr.textContent = '更新を受信しました'; });
      }
      return;
    }
    if (__hbState === 'offline' || __hbState === 'static') return;
    __hbSpikeRemain = HB_SPIKE_SHAPE.length;
  }

  if (typeof window !== 'undefined') {
    window.__heartbeat = {
      bump: bumpHeartbeat,
      setState: setHeartbeatState,
      start: startHeartbeat,
      stop: stopHeartbeat,
      // _buf / _reset は test 専用 hook。production 経路から呼ばない。
      _buf: function () { return __hbBuf; },
      _reset: function () {
        if (__hbRafId !== null) {
          if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(__hbRafId);
          __hbRafId = null;
        }
        __hbReducedMotion = __hbDetectReducedMotion();
        for (let i = 0; i < __hbBuf.length; i++) __hbBuf[i] = 0;
        __hbSpikeRemain = 0;
      },
    };
  }

