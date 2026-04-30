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
  // PQRS 風の単一スパイク shape (上に -9px → 下に +4px → 0px)。10 sample 固定 duration、
  // 強弱は __hbSpikeAmp で別軸制御 (online=1.0 / reconnect=0.3 / offline・static=0.0)。
  const HB_SPIKE_SHAPE = [-2, -5, -9, -7, -3, 2, 4, 3, 1, 0];
  // 1 sample あたりの経過時間 (ms)。30 sample/s = 30 px/s (プラン Decisions: 60fps 環境で
  // frame あたり 0.5 px に整合)。requestAnimationFrame の timestamp 引数で elapsed time
  // 駆動するため、120Hz / 60Hz の display refresh rate に挙動が依存しない。
  const HB_MS_PER_SAMPLE = 33;
  // tab 復帰や long-paused tab で huge dt が来たときに waveform が急流するのを防ぐ
  // ための catch-up 上限。HB_SPIKE_SHAPE.length を超えない値にしておけば、
  // 「1 frame で spike 全消費」のような視覚 glitch も自然に避けられる。
  const HB_MAX_CATCHUP_SAMPLES = 5;
  let __hbState = 'idle';
  let __hbBuf = new Float32Array(HB_SAMPLES);
  let __hbSpikeRemain = 0;
  let __hbSpikeAmp = 1.0;
  let __hbRafId = null;
  let __hbReducedMotion = false;
  let __hbLastTickMs = 0;
  let __hbAccumMs = 0;
  // 各 sample 進む度にインクリメント。idle 時の subtle baseline breathing wave の
  // phase 入力 + buffer overflow worry なし (30 sample/s × Number.MAX_SAFE_INTEGER で
  // 数万年スケール)。
  let __hbTickCount = 0;

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

  function __hbAdvanceOneSample() {
    // 1 sample 左シフト + 末尾追加。
    // spike 残量があれば SHAPE * amp、なければ idle baseline breathing wave。
    // breathing は振幅 ~0.6px / 周期 ~3.3s の sin。プラン acceptance criteria
    // 「idle 30s 以上でも line が左→右に流れ続ける (= 凍ってない)」を視覚的に
    // 担保するため (Issue #83 codex Round 2 P1)。__hbSpikeAmp で state スケール
    // するので offline/static (amp=0) では完全 flat = 既存テストの「buf 全 0」契約と整合。
    for (let i = 0; i < __hbBuf.length - 1; i++) {
      __hbBuf[i] = __hbBuf[i + 1];
    }
    let nextSample;
    if (__hbSpikeRemain > 0) {
      const idx = HB_SPIKE_SHAPE.length - __hbSpikeRemain;
      nextSample = HB_SPIKE_SHAPE[idx] * __hbSpikeAmp;
      __hbSpikeRemain -= 1;
    } else {
      nextSample = 0.6 * Math.sin(__hbTickCount * 0.06) * __hbSpikeAmp;
    }
    __hbBuf[__hbBuf.length - 1] = nextSample;
    __hbTickCount += 1;
  }

  function __hbTick(timestamp) {
    // elapsed-ms 駆動: 経過 ms を HB_MS_PER_SAMPLE で割って消費する sample 数を決める。
    // 60Hz / 120Hz いずれの display でも 30 sample/s で進む = scroll 速度 / spike duration が
    // refresh rate に依存しない。timestamp は requestAnimationFrame が渡す DOMHighResTimeStamp。
    let dt = 0;
    if (__hbLastTickMs > 0 && typeof timestamp === 'number') {
      dt = timestamp - __hbLastTickMs;
      if (dt < 0) dt = 0;
    }
    if (typeof timestamp === 'number') __hbLastTickMs = timestamp;
    __hbAccumMs += dt;
    let samples = Math.floor(__hbAccumMs / HB_MS_PER_SAMPLE);
    if (samples > HB_MAX_CATCHUP_SAMPLES) samples = HB_MAX_CATCHUP_SAMPLES;
    if (samples > 0) {
      __hbAccumMs -= samples * HB_MS_PER_SAMPLE;
      for (let s = 0; s < samples; s++) __hbAdvanceOneSample();
      __hbRenderPoly();
    }
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
    // pause 後の resume 時に stale dt で catch-up モード暴走を起こさないよう
    // timing state を毎回クリア (Issue #83 codex Round 2 P2)。次回 tick の
    // 初回 frame で `__hbLastTickMs > 0` ガードに落ちて dt = 0 から再開する。
    __hbLastTickMs = 0;
    __hbAccumMs = 0;
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
        __hbLastTickMs = 0;
        __hbAccumMs = 0;
        __hbTickCount = 0;
      },
    };
  }

