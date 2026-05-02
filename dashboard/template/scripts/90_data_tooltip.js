  // ============================================================
  //  Data tooltip (graph data points, [data-tip] elements)
  // ============================================================
  const dtip = document.getElementById('dataTooltip');
  let dtipActive = null;

  function dtipShow(html, kind) {
    dtip.innerHTML = html;
    dtip.setAttribute('data-kind', kind);
    dtip.setAttribute('data-show', 'true');
    dtip.setAttribute('aria-hidden', 'false');
  }
  function dtipHide() {
    dtip.removeAttribute('data-show');
    dtip.setAttribute('aria-hidden', 'true');
    dtipActive = null;
  }
  function dtipMove(clientX, clientY) {
    const offset = 14;
    let x = clientX + offset;
    let y = clientY + offset;
    const tw = dtip.offsetWidth;
    const th = dtip.offsetHeight;
    if (x + tw > window.innerWidth - 8) x = clientX - offset - tw;
    if (y + th > window.innerHeight - 8) y = clientY - offset - th;
    if (x < 4) x = 4;
    if (y < 4) y = 4;
    dtip.style.transform = 'translate3d(' + x + 'px,' + y + 'px,0)';
  }
  function dtipBuild(el) {
    const kind = el.getAttribute('data-tip');
    if (kind === 'daily') {
      const d = el.getAttribute('data-d');
      const c = el.getAttribute('data-c');
      return {
        kind: 'daily',
        html: '<span class="ttl">' + esc(d) + '</span>' +
              '<span class="lbl">events</span><span class="val">' + fmtN(c) + '</span>'
      };
    }
    if (kind === 'proj') {
      const p = el.getAttribute('data-p');
      const c = el.getAttribute('data-c');
      const pct = el.getAttribute('data-pct');
      return {
        kind: 'proj',
        html: '<span class="ttl">' + esc(p) + '</span>' +
              '<span class="lbl">events</span><span class="val">' + fmtN(c) + '</span>' +
              '<span class="sep">·</span>' +
              '<span class="lbl">share</span><span class="val">' + esc(pct) + '</span>'
      };
    }
    if (kind === 'heatmap') {
      // 時間帯ヒートマップ (Issue #58)。曜日 × hour cell の hover tooltip。
      const wd = el.getAttribute('data-wd') || '';
      const h = el.getAttribute('data-h') || '00';
      const c = el.getAttribute('data-c') || '0';
      return {
        kind: 'heatmap',
        html: '<span class="ttl">' + esc(wd) + ' ' + esc(h) + ':00</span>' +
              '<span class="lbl">events</span><span class="val">' + fmtN(c) + '</span>'
      };
    }
    if (kind === 'rank') {
      // skill / subagent ランキング行 (Issue #50)。kind サフィックスで accent 色を分岐。
      const name = el.getAttribute('data-name') || '';
      const c = el.getAttribute('data-c') || '0';
      const subKind = el.getAttribute('data-kind') || 'skill';
      const fail = el.getAttribute('data-fail');
      const failRate = el.getAttribute('data-fail-rate');
      const avg = el.getAttribute('data-avg');
      const countLabel = subKind === 'subagent' ? 'invocations' : 'uses';
      let body = '<span class="ttl">' + esc(name) + '</span>' +
        '<span class="lbl">' + countLabel + '</span>' +
        '<span class="val">' + fmtN(c) + '</span>';
      if (fail != null && parseInt(fail, 10) > 0) {
        const rate = failRate != null ? Math.round(parseFloat(failRate) * 100) : 0;
        body += '<span class="sep">·</span>' +
                '<span class="lbl">fail</span>' +
                '<span class="val">' + fmtN(fail) + ' (' + rate + '%)</span>';
      }
      if (avg != null) {
        const a = parseFloat(avg);
        const fmt = a >= 1000 ? (a / 1000).toFixed(1) + 's' : Math.round(a) + 'ms';
        body += '<span class="sep">·</span>' +
                '<span class="lbl">avg</span><span class="val">' + fmt + '</span>';
      }
      return { kind: 'rank-' + subKind, html: body };
    }
    if (kind === 'cooc') {
      // skill 共起テーブル行 (Issue #59 / B1)。count 単位は session 数。
      const a = el.getAttribute('data-a') || '';
      const b = el.getAttribute('data-b') || '';
      const c = el.getAttribute('data-c') || '0';
      return {
        kind: 'cooc',
        html: '<span class="ttl">' + esc(a) + ' ⨉ ' + esc(b) + '</span>' +
              '<span class="lbl">sessions</span><span class="val">' + fmtN(c) + '</span>'
      };
    }
    if (kind === 'projskill') {
      // project × skill heatmap cell (Issue #59 / B2)。count 単位は events。
      const p = el.getAttribute('data-p') || '';
      const s = el.getAttribute('data-s') || '';
      const c = el.getAttribute('data-c') || '0';
      return {
        kind: 'projskill',
        html: '<span class="ttl">' + esc(p) + ' × ' + esc(s) + '</span>' +
              '<span class="lbl">events</span><span class="val">' + fmtN(c) + '</span>'
      };
    }
    if (kind === 'percentile') {
      // subagent percentile row (Issue #60 / A5)
      const name = el.getAttribute('data-name') || '';
      const p50 = el.getAttribute('data-p50') || '';
      const p90 = el.getAttribute('data-p90') || '';
      const p99 = el.getAttribute('data-p99') || '';
      return {
        kind: 'percentile',
        html: '<span class="ttl">' + esc(name) + '</span>' +
              '<span class="lbl">p50</span><span class="val">' + fmtDur(p50) + '</span>' +
              ' <span class="lbl">p90</span><span class="val">' + fmtDur(p90) + '</span>' +
              ' <span class="lbl">p99</span><span class="val">' + fmtDur(p99) + '</span>'
      };
    }
    if (kind === 'trend') {
      // subagent failure weekly trend point (Issue #60 / B3)
      const name = el.getAttribute('data-name') || '';
      const w = el.getAttribute('data-w') || '';
      const rate = parseFloat(el.getAttribute('data-rate') || '0');
      const fc = el.getAttribute('data-fc') || '0';
      const c = el.getAttribute('data-c') || '0';
      return {
        kind: 'trend',
        html: '<span class="ttl">' + esc(name) + '</span>' +
              '<span class="lbl">' + esc(w) + '</span>' +
              '<span class="val">' + Math.round(rate * 100) + '% (' + fc + '/' + c + ')</span>'
      };
    }
    if (kind === 'perm-skill' || kind === 'perm-subagent') {
      // permission breakdown row (Issue #61 / A2)
      const name = el.getAttribute('data-name') || '';
      const c = el.getAttribute('data-c') || '0';
      const inv = el.getAttribute('data-inv') || '0';
      const rate = parseFloat(el.getAttribute('data-rate') || '0');
      return {
        kind,
        html: '<span class="ttl">' + esc(name) + '</span>' +
              '<span class="lbl">prompts</span><span class="val">' + fmtN(c) + '</span>' +
              '<span class="sep">·</span>' +
              '<span class="lbl">invocations</span><span class="val">' + fmtN(inv) + '</span>' +
              '<span class="sep">·</span>' +
              '<span class="lbl">rate</span><span class="val">' + Math.round(rate * 100) + '%</span>'
      };
    }
    if (kind === 'histogram') {
      // compact density histogram bar (Issue #61 / A3)
      const bucket = el.getAttribute('data-bucket') || '';
      const c = el.getAttribute('data-c') || '0';
      return {
        kind: 'histogram',
        html: '<span class="ttl">' + esc(bucket) + ' compact(s)</span>' +
              '<span class="lbl">sessions</span><span class="val">' + fmtN(c) + '</span>'
      };
    }
    if (kind === 'worst-session') {
      // worst-session row (Issue #61 / A3)
      const sid = el.getAttribute('data-sid') || '';
      const proj = el.getAttribute('data-proj') || '';
      const c = el.getAttribute('data-c') || '0';
      const projDisplay = proj === '' ? '（不明）' : proj;
      return {
        kind: 'worst-session',
        html: '<span class="ttl">' + esc(sid) + '</span>' +
              '<span class="lbl">project</span><span class="val">' + esc(projDisplay) + '</span>' +
              '<span class="sep">·</span>' +
              '<span class="lbl">compacts</span><span class="val">' + fmtN(c) + '</span>'
      };
    }
    if (kind === 'source') {
      // slash command source breakdown row (Issue #62 / A4)
      const name = el.getAttribute('data-name') || '';
      const e = el.getAttribute('data-e') || '0';
      const s = el.getAttribute('data-s') || '0';
      const rateRaw = el.getAttribute('data-rate') || '0';
      const rateText = Math.round(parseFloat(rateRaw) * 100) + '%';
      return {
        kind: 'source',
        html: '<span class="ttl">' + esc(name) + '</span>' +
              '<span class="lbl">expansion</span><span class="val">' + fmtN(e) + '</span>' +
              '<span class="lbl">submit</span><span class="val">' + fmtN(s) + '</span>' +
              '<span class="lbl">rate</span><span class="val">' + rateText + '</span>'
      };
    }
    if (kind === 'instr-bar') {
      // instructions_loaded distribution bar (Issue #62 / B4)
      const k = el.getAttribute('data-key') || '';
      const c = el.getAttribute('data-c') || '0';
      const fld = el.getAttribute('data-kind') || '';
      return {
        kind: 'instr-bar',
        html: '<span class="ttl">' + esc(k) + '</span>' +
              '<span class="lbl">' + esc(fld) + '</span><span class="val">' + fmtN(c) + '</span>'
      };
    }
    if (kind === 'glob') {
      // glob_match top row (Issue #62 / B4)
      const fp = el.getAttribute('data-fp') || '';
      const c = el.getAttribute('data-c') || '0';
      return {
        kind: 'glob',
        html: '<span class="ttl">' + esc(fp) + '</span>' +
              '<span class="lbl">loads</span><span class="val">' + fmtN(c) + '</span>'
      };
    }
    if (kind === 'inv') {
      // Surface Panel 1: skill 起動経路 (Issue #74)
      const name = el.getAttribute('data-name') || '';
      const mode = el.getAttribute('data-mode') || '';
      const t = el.getAttribute('data-t') || '0';
      const s = el.getAttribute('data-s') || '0';
      const rateRaw = el.getAttribute('data-rate') || '';
      const MODE_TIP = {
        'llm-only':  '🤖 LLM-only',
        'user-only': '👤 User-only',
        'mixed':     '🤝 Dual',
      };
      const rateText = rateRaw === '' ?
        '<span class="dim">—</span>' :
        Math.round(parseFloat(rateRaw) * 100) + '%';
      return {
        kind: 'inv',
        html: '<span class="ttl">' + esc(name) + '</span>' +
              '<span class="lbl">mode</span><span class="val">' +
                esc(MODE_TIP[mode] || mode) + '</span>' +
              '<span class="sep">·</span>' +
              '<span class="lbl">LLM</span><span class="val">' + fmtN(t) + '</span>' +
              '<span class="sep">·</span>' +
              '<span class="lbl">User</span><span class="val">' + fmtN(s) + '</span>' +
              '<span class="sep">·</span>' +
              '<span class="lbl">autonomy</span><span class="val">' + rateText + '</span>'
      };
    }
    if (kind === 'life') {
      // Surface Panel 2: skill lifecycle (Issue #74)
      const name = el.getAttribute('data-name') || '';
      const trend = el.getAttribute('data-trend') || '';
      const c30 = el.getAttribute('data-30d') || '0';
      const ct = el.getAttribute('data-total') || '0';
      const TREND_TIP = {
        'accelerating': '📈 加速',
        'stable':       '➡️ 安定',
        'decelerating': '📉 減速',
        'new':          '🌱 新規',
      };
      return {
        kind: 'life',
        html: '<span class="ttl">' + esc(name) + '</span>' +
              '<span class="lbl">30d</span><span class="val">' + fmtN(c30) + '</span>' +
              '<span class="sep">·</span>' +
              '<span class="lbl">total</span><span class="val">' + fmtN(ct) + '</span>' +
              '<span class="sep">·</span>' +
              '<span class="lbl">trend</span><span class="val">' +
                esc(TREND_TIP[trend] || trend) + '</span>'
      };
    }
    if (kind === 'hib') {
      // Surface Panel 3: hibernating skills (Issue #74)
      const name = el.getAttribute('data-name') || '';
      const status = el.getAttribute('data-status') || '';
      const STATUS_TIP = {
        'warming_up': '🌱 新着 (更新日時 14 日以内 / 未使用)',
        'resting':    '💤 休眠 (15〜30 日未使用)',
        'idle':       '🪦 死蔵 (30 日以上未使用)',
      };
      return {
        kind: 'hib',
        html: '<span class="ttl">' + esc(name) + '</span>' +
              '<span class="lbl">status</span><span class="val">' +
                esc(STATUS_TIP[status] || status) + '</span>'
      };
    }
    return null;
  }

  document.addEventListener('mouseover', function(e) {
    const el = e.target.closest('[data-tip]');
    if (!el) return;
    if (el !== dtipActive) {
      const info = dtipBuild(el);
      if (!info) return;
      dtipShow(info.html, info.kind);
      dtipActive = el;
    }
    dtipMove(e.clientX, e.clientY);
  });

  document.addEventListener('mousemove', function(e) {
    if (!dtipActive) return;
    const el = e.target.closest('[data-tip]');
    if (el === dtipActive) {
      dtipMove(e.clientX, e.clientY);
    }
  });

  document.addEventListener('mouseout', function(e) {
    if (!dtipActive) return;
    const el = e.target.closest('[data-tip]');
    if (el !== dtipActive) return;
    if (e.relatedTarget && dtipActive.contains(e.relatedTarget)) return;
    dtipHide();
  });

  // keyboard fallback: focus する要素にも tooltip を出す
  document.addEventListener('focusin', function(e) {
    const el = e.target.closest('[data-tip]');
    if (!el) return;
    const info = dtipBuild(el);
    if (!info) return;
    dtipShow(info.html, info.kind);
    dtipActive = el;
    const r = el.getBoundingClientRect();
    dtipMove(r.right, r.bottom);
  });

  document.addEventListener('focusout', function(e) {
    const el = e.target.closest('[data-tip]');
    if (el && el === dtipActive) dtipHide();
  });
