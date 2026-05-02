  // ============================================================
  //  Subagent percentile table (Issue #60 / A5)
  //  page-scoped early-out: Quality 非表示中は no-op (#59 規範踏襲)。
  //  data 源は subagent_ranking (= aggregate_subagent_metrics の dict 値を spread し name キーを付加)。
  //  各行に p50/p90/p99 + sample_count + avg + count を出す。
  // ============================================================
  function renderSubagentPercentile(items) {
    if (document.body.dataset.activePage !== 'quality') return;
    const tbody = document.querySelector('#quality-percentile tbody');
    const sub = document.getElementById('quality-percentile-sub');
    if (!tbody) return;
    const list = Array.isArray(items) ? items : [];
    if (list.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty">no data</td></tr>';
    } else {
      tbody.innerHTML = list.map((it) => {
        const c = it.count || 0;
        const samples = it.sample_count || 0;
        const avg = (it.avg_duration_ms != null) ? it.avg_duration_ms : null;
        const p50 = (it.p50_duration_ms != null) ? it.p50_duration_ms : null;
        const p90 = (it.p90_duration_ms != null) ? it.p90_duration_ms : null;
        const p99 = (it.p99_duration_ms != null) ? it.p99_duration_ms : null;
        const al = it.name + ': p50 ' + fmtDur(p50) + ' / p90 ' + fmtDur(p90) + ' / p99 ' + fmtDur(p99);
        return '<tr data-tip="percentile" data-name="' + esc(it.name) +
          '" data-c="' + c + '" data-p50="' + (p50 != null ? p50 : '') +
          '" data-p90="' + (p90 != null ? p90 : '') +
          '" data-p99="' + (p99 != null ? p99 : '') + '" tabindex="0" role="row" ' +
          'aria-label="' + esc(al) + '">' +
          '<td class="name">' + esc(it.name) + '</td>' +
          '<td class="num">' + fmtN(c) + '</td>' +
          '<td class="num dim">' + fmtN(samples) + '</td>' +
          '<td class="num dim">' + fmtDur(avg) + '</td>' +
          '<td class="num">' + fmtDur(p50) + '</td>' +
          '<td class="num">' + fmtDur(p90) + '</td>' +
          '<td class="num">' + fmtDur(p99) + '</td>' +
          '</tr>';
      }).join('');
    }
    if (sub) sub.textContent = list.length + ' subagent types';
  }

  // ============================================================
  //  Subagent failure weekly trend (Issue #60 / B3)
  //  data 源は subagent_failure_trend = list[{week_start, subagent_type, count, failure_count, failure_rate}]。
  //  server は top-N で切らない (P2)。client 側で count 上位 5 type に絞って描画 (default top-5、affordance)。
  //  weeks.length === 1 の degenerate path では polyline を描かず circle のみ (P4)。
  //  page-scoped early-out: Quality 非表示中は no-op。
  // ============================================================
  function renderSubagentFailureTrend(items) {
    if (document.body.dataset.activePage !== 'quality') return;
    const root = document.getElementById('quality-trend');
    const legend = document.getElementById('quality-trend-legend');
    const sub = document.getElementById('quality-trend-sub');
    if (!root) return;
    const list = Array.isArray(items) ? items : [];
    if (list.length === 0) {
      root.innerHTML = '<div class="empty" style="padding:24px;text-align:center;color:var(--ink-faint)">no data</div>';
      if (legend) legend.innerHTML = '';
      if (sub) sub.textContent = '';
      return;
    }

    const byType = new Map();
    const weekSet = new Set();
    for (const r of list) {
      if (!byType.has(r.subagent_type)) byType.set(r.subagent_type, { total: 0, byWeek: new Map() });
      const e = byType.get(r.subagent_type);
      e.total += r.count;
      e.byWeek.set(r.week_start, r);
      weekSet.add(r.week_start);
    }
    // server は観測ゼロ週を返さない (sparse) ので、観測週だけで weekSet を作ると
    // W1/W3 のみ観測時に空 W2 が x-axis から消えて W1 と W3 が隣接描画される。
    // observedWeeks の最初〜最後を 7-day 増分で densify し、空週も timeline 上に
    // calendar 時系列として表示する (xOf(i) が暦週位置に揃う)。
    // 単一週入力 / 連続観測時は densify しても元の axis と等価。
    const observedWeeks = [...weekSet].sort();
    const weeks = [];
    if (observedWeeks.length === 1) {
      weeks.push(observedWeeks[0]);
    } else if (observedWeeks.length >= 2) {
      const startDate = new Date(observedWeeks[0] + 'T00:00:00Z');
      const endDate = new Date(observedWeeks[observedWeeks.length - 1] + 'T00:00:00Z');
      const cursor = new Date(startDate);
      // safety: 最大 1040 週 (= 約 20 年) で打ち切り (異常データへの保険)
      let safety = 0;
      while (cursor <= endDate && safety < 1040) {
        weeks.push(cursor.toISOString().slice(0, 10));
        cursor.setUTCDate(cursor.getUTCDate() + 7);
        safety += 1;
      }
    }
    const top = [...byType.entries()].sort((a, b) => b[1].total - a[1].total).slice(0, 5);

    const W = 600, H = 220, padL = 36, padR = 12, padT = 14, padB = 28;
    const innerW = W - padL - padR;
    const innerH = H - padT - padB;
    const xOf = (i) => padL + (weeks.length === 1 ? innerW / 2 : (innerW * i / (weeks.length - 1)));
    const yOf = (rate) => padT + innerH - innerH * rate;

    const palette = ['#FF6E70','#FFC97A','#6FE3C8','#9AB3FF','#D6A6FF'];
    let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg"' +
      ' role="img" aria-label="Subagent 失敗率 週次トレンド (top ' + top.length + ')">';
    for (const [r, lbl] of [[0, '0%'], [0.5, '50%'], [1.0, '100%']]) {
      const y = yOf(r);
      svg += '<line class="grid" x1="' + padL + '" x2="' + (W - padR) + '" y1="' + y + '" y2="' + y + '"/>';
      svg += '<text class="axis-label" x="' + (padL - 6) + '" y="' + (y + 3) + '" text-anchor="end">' + lbl + '</text>';
    }
    const tickIdx = weeks.length === 1 ? [0] : [0, Math.floor((weeks.length - 1) / 2), weeks.length - 1];
    for (const i of tickIdx) {
      const x = xOf(i);
      svg += '<text class="axis-label" x="' + x + '" y="' + (H - 10) + '" text-anchor="middle">' + esc(weeks[i]) + '</text>';
    }
    top.forEach(([name, e], idx) => {
      const color = palette[idx % palette.length];
      const pts = [];
      weeks.forEach((w, i) => {
        const r = e.byWeek.get(w);
        if (r) pts.push({ i, r });
      });
      // gap-bridging を防ぐため、weeks 上で連続する index ごとに run を組み run 単位で
      // polyline を出す。type が観測されなかった中間週 (= byWeek に key 無し) を跨ぐ
      // 単一 line を描かない (= 観測実態を視覚的に正しく反映)。
      // 単一点 run は run.length >= 2 guard で polyline スキップされ circle のみ残る。
      const runs = [];
      let current = null;
      pts.forEach(p => {
        if (current && p.i === current[current.length - 1].i + 1) {
          current.push(p);
        } else {
          current = [p];
          runs.push(current);
        }
      });
      runs.forEach(run => {
        if (run.length >= 2) {
          svg += '<polyline class="line" stroke="' + color + '" points="' +
            run.map(p => xOf(p.i) + ',' + yOf(p.r.failure_rate)).join(' ') + '"/>';
        }
      });
      pts.forEach(p => {
        const al = name + ' ' + p.r.week_start + ': ' + Math.round(p.r.failure_rate * 100) + '% (' + p.r.failure_count + '/' + p.r.count + ')';
        svg += '<circle class="pt" stroke="' + color + '" fill="' + color + '" cx="' + xOf(p.i) +
          '" cy="' + yOf(p.r.failure_rate) + '" r="2.5" data-tip="trend"' +
          ' data-name="' + esc(name) + '" data-w="' + esc(p.r.week_start) +
          '" data-rate="' + p.r.failure_rate + '" data-fc="' + p.r.failure_count +
          '" data-c="' + p.r.count + '" tabindex="0" role="img" aria-label="' + esc(al) + '"/>';
      });
    });
    svg += '</svg>';
    root.innerHTML = svg;

    if (legend) {
      legend.innerHTML = top.map(([name, e], i) =>
        '<span><span class="marker" style="background:' + palette[i % palette.length] + '"></span>' + esc(name) + '</span>'
      ).join('');
    }
    if (sub) {
      const weekLabel = weeks.length === 1 ? '1 week only' : (weeks.length + ' weeks');
      sub.textContent = weekLabel + ' · top ' + top.length + ' / ' + byType.size + ' types';
    }
  }

  // ============================================================
  //  Permission breakdowns (Issue #61 / A2) — skill / subagent
  //  page-scoped early-out で Quality 非表示中は skip。
  // ============================================================
  function renderPermissionSkillBreakdown(items) {
    if (document.body.dataset.activePage !== 'quality') return;
    const tbody = document.querySelector('#quality-perm-skill tbody');
    const sub = document.getElementById('quality-perm-skill-sub');
    if (!tbody) return;
    const list = Array.isArray(items) ? items : [];
    if (list.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty">no data</td></tr>';
    } else {
      tbody.innerHTML = list.map(it => {
        const c = it.prompt_count || 0;
        const inv = it.invocation_count || 0;
        const rate = it.permission_rate || 0;
        const rateClass = rate >= 0.5 ? 'num rate-warn' : 'num';
        const al = it.skill + ': ' + c + ' prompts / ' + inv + ' 呼び出し (' + Math.round(rate * 100) + '%)';
        return '<tr data-tip="perm-skill" data-name="' + esc(it.skill) +
          '" data-c="' + c + '" data-inv="' + inv + '" data-rate="' + rate +
          '" tabindex="0" role="row" aria-label="' + esc(al) + '">' +
          '<td class="name">' + esc(it.skill) + '</td>' +
          '<td class="num">' + fmtN(c) + '</td>' +
          '<td class="num dim">' + fmtN(inv) + '</td>' +
          '<td class="' + rateClass + '">' + Math.round(rate * 100) + '%</td>' +
          '</tr>';
      }).join('');
    }
    if (sub) sub.textContent = list.length + ' skill(s)';
  }

  function renderPermissionSubagentBreakdown(items) {
    if (document.body.dataset.activePage !== 'quality') return;
    const tbody = document.querySelector('#quality-perm-subagent tbody');
    const sub = document.getElementById('quality-perm-subagent-sub');
    if (!tbody) return;
    const list = Array.isArray(items) ? items : [];
    if (list.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty">no data</td></tr>';
    } else {
      tbody.innerHTML = list.map(it => {
        const c = it.prompt_count || 0;
        const inv = it.invocation_count || 0;
        const rate = it.permission_rate || 0;
        const rateClass = rate >= 0.5 ? 'num rate-warn' : 'num';
        const al = it.subagent_type + ': ' + c + ' prompts / ' + inv + ' 呼び出し (' + Math.round(rate * 100) + '%)';
        return '<tr data-tip="perm-subagent" data-name="' + esc(it.subagent_type) +
          '" data-c="' + c + '" data-inv="' + inv + '" data-rate="' + rate +
          '" tabindex="0" role="row" aria-label="' + esc(al) + '">' +
          '<td class="name">' + esc(it.subagent_type) + '</td>' +
          '<td class="num">' + fmtN(c) + '</td>' +
          '<td class="num dim">' + fmtN(inv) + '</td>' +
          '<td class="' + rateClass + '">' + Math.round(rate * 100) + '%</td>' +
          '</tr>';
      }).join('');
    }
    if (sub) sub.textContent = list.length + ' subagent type(s)';
  }

  // ============================================================
  //  Compact density (Issue #61 / A3) — histogram + worst sessions
  // ============================================================
  function renderCompactDensity(payload) {
    if (document.body.dataset.activePage !== 'quality') return;
    const histRoot = document.getElementById('quality-compact-hist');
    const worstTbody = document.querySelector('#quality-compact-worst tbody');
    const sub = document.getElementById('quality-compact-sub');
    const data = (payload && typeof payload === 'object') ? payload : {};
    const hist = (data.histogram && typeof data.histogram === 'object') ? data.histogram : {};
    const worst = Array.isArray(data.worst_sessions) ? data.worst_sessions : [];

    // histogram SVG (4 bars)
    const buckets = ['0', '1', '2', '3+'];
    const counts = buckets.map(b => Number(hist[b] || 0));
    const maxC = Math.max(1, ...counts);
    const W = 360, H = 180, padL = 28, padR = 12, padT = 18, padB = 30;
    const innerW = W - padL - padR;
    const innerH = H - padT - padB;
    const barW = (innerW / buckets.length) * 0.6;
    const gap = (innerW - barW * buckets.length) / (buckets.length + 1);

    if (histRoot) {
      let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Compact 回数 histogram (per session)">';
      buckets.forEach((b, i) => {
        const c = counts[i];
        const h = (c / maxC) * innerH;
        const x = padL + gap + i * (barW + gap);
        const y = padT + innerH - h;
        const al = b + ' compact(s): ' + c + ' session(s)';
        svg += '<rect class="bar" x="' + x + '" y="' + y + '" width="' + barW +
          '" height="' + h + '" data-tip="histogram" data-bucket="' + esc(b) +
          '" data-c="' + c + '" tabindex="0" role="img" aria-label="' + esc(al) + '"/>';
        svg += '<text class="bar-num" x="' + (x + barW / 2) + '" y="' + (y - 4) + '">' + c + '</text>';
        svg += '<text class="axis-label" x="' + (x + barW / 2) + '" y="' + (H - 10) +
          '" text-anchor="middle">' + esc(b) + '</text>';
      });
      svg += '</svg>';
      histRoot.innerHTML = svg;
    }

    // worst sessions table
    if (worstTbody) {
      if (worst.length === 0) {
        worstTbody.innerHTML = '<tr><td colspan="3" class="empty">no data</td></tr>';
      } else {
        worstTbody.innerHTML = worst.map(w => {
          const sid = w.session_id || '';
          const sidShort = sid.length > 8 ? sid.slice(0, 8) : sid;
          const proj = w.project || '';
          const c = w.count || 0;
          // P3 反映: 空 project は (unknown) ラベルで明示。空セルだと「データ欠損」と
          // 「project が空文字」が見分けつかなくなる UX 問題を回避。
          const projCell = proj === '' ? '<span class="dim">（不明）</span>' : esc(proj);
          const projForLabel = proj === '' ? '不明' : proj;
          const al = sidShort + ' (' + projForLabel + '): ' + c + ' compacts';
          return '<tr data-tip="worst-session" data-sid="' + esc(sid) + '" data-proj="' + esc(proj) +
            '" data-c="' + c + '" tabindex="0" role="row" aria-label="' + esc(al) + '">' +
            '<td class="sid" title="' + esc(sid) + '">' + esc(sidShort) + '</td>' +
            '<td class="proj">' + projCell + '</td>' +
            '<td class="num">' + fmtN(c) + '</td>' +
            '</tr>';
        }).join('');
      }
    }

    if (sub) {
      const total = counts.reduce((a, b) => a + b, 0);
      sub.textContent = total + ' session(s) tracked';
    }
  }

