  // ============================================================
  //  Sessions ページ renderer (Issue #103)
  //  ----------------------------------------------------------
  //  data.session_breakdown (cost_metrics.aggregate_session_breakdown 由来) を
  //  受け取り、KPI 行 4 枚 + 12 列の table を描画する。
  //
  //  pure helpers (formatCostUsd / fmtTokens / inferModelFamily /
  //  buildModelChips / buildTierChips / buildSessionRow / computeKpi /
  //  buildKpiHTML) は window.__sessions に expose し、Node round-trip
  //  test (tests/test_dashboard_sessions_ui.py) から個別呼び出して検証する。
  //
  //  page-scoped early-out: body[data-active-page="sessions"] 以外では
  //  DOM を触らない (Surface / Quality 系 renderer 慣習踏襲)。
  // ============================================================
  (function(){
    function formatCostUsd(n) {
      const v = Number(n);
      if (!isFinite(v)) return '$0.0000';
      return '$' + v.toFixed(4);
    }

    function fmtTokens(n) {
      const v = Number(n);
      if (!isFinite(v) || v <= 0) return '0';
      if (v >= 1000000) return (v / 1000000).toFixed(1) + 'M';
      if (v >= 1000) return (v / 1000).toFixed(1) + 'k';
      return String(Math.round(v));
    }

    // 未知 model 名 → sonnet fallback (cost_metrics.calculate_message_cost と整合)
    function inferModelFamily(model) {
      const m = String(model || '').toLowerCase();
      if (m.indexOf('opus') !== -1) return 'opus';
      if (m.indexOf('haiku') !== -1) return 'haiku';
      if (m.indexOf('sonnet') !== -1) return 'sonnet';
      return 'sonnet';
    }

    function buildModelChips(models) {
      if (!models || typeof models !== 'object') return '<span class="dim">—</span>';
      const entries = Object.keys(models).map(k => [k, Number(models[k]) || 0])
                          .filter(e => e[1] > 0)
                          .sort((a, b) => b[1] - a[1]);
      if (entries.length === 0) return '<span class="dim">—</span>';
      return '<div class="model-chips">' + entries.map(function(pair) {
        const name = pair[0];
        const count = pair[1];
        const family = inferModelFamily(name);
        return '<span class="model-chip m-' + family + '">' +
          esc(family) + ' <span class="ct">' + count + '</span></span>';
      }).join('') + '</div>';
    }

    function buildTierChips(tiers) {
      if (!tiers || typeof tiers !== 'object') return '<span class="dim">—</span>';
      const KNOWN = { priority: 1, standard: 1, batch: 1 };
      const entries = Object.keys(tiers).map(k => [k, Number(tiers[k]) || 0])
                          .filter(e => e[1] > 0)
                          .sort((a, b) => b[1] - a[1]);
      if (entries.length === 0) return '<span class="dim">—</span>';
      return '<div class="tier-chips">' + entries.map(function(pair) {
        const name = pair[0];
        const count = pair[1];
        const cls = KNOWN[name] ? name : 'standard';
        return '<span class="tier-chip t-' + cls + '">' +
          esc(name) + ' <span class="ct">' + count + '</span></span>';
      }).join('') + '</div>';
    }

    // 開始時刻を local TZ で "MM/DD HH:mm" 形式に整形
    function formatStartedAt(iso) {
      if (!iso) return '';
      const dt = new Date(iso);
      if (isNaN(dt.getTime())) return '';
      return pad(dt.getMonth() + 1, 2) + '/' + pad(dt.getDate(), 2) + ' ' +
        pad(dt.getHours(), 2) + ':' + pad(dt.getMinutes(), 2);
    }

    function formatDuration(secs) {
      if (secs == null) return '—';
      const v = Number(secs);
      if (!isFinite(v)) return '—';
      const s = Math.max(0, Math.floor(v));
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      if (h > 0) return h + 'h ' + m + 'm';
      if (m > 0) return m + 'm';
      return s + 's';
    }

    function computeKpi(sessions) {
      const list = Array.isArray(sessions) ? sessions : [];
      if (list.length === 0) {
        return { totalCost: 0, medianCost: 0, avgCost: 0, cacheEfficiency: 0,
                 topCost: 0, minCost: 0, sessionCount: 0,
                 totalInputTokens: 0, totalCacheReadTokens: 0 };
      }
      const costs = list.map(function(s){ return Number(s.estimated_cost_usd) || 0; });
      const totalCost = costs.reduce(function(a,b){ return a + b; }, 0);
      const sorted = costs.slice().sort(function(a,b){ return a - b; });
      // 偶数件では中央 2 値の平均、奇数件では中央値そのもの (= true median)。
      // TOP_N_SESSIONS = 20 (偶数) が常用ケースなので、偶数の正しさが UX 上 load-bearing。
      const n = sorted.length;
      const median = (n % 2 === 0)
        ? (sorted[n / 2 - 1] + sorted[n / 2]) / 2
        : sorted[Math.floor(n / 2)];
      const avg = totalCost / list.length;

      let inputSum = 0;
      let cacheReadSum = 0;
      for (let i = 0; i < list.length; i++) {
        const t = list[i].tokens || {};
        inputSum += Number(t.input || 0);
        cacheReadSum += Number(t.cache_read || 0);
      }
      const denom = inputSum + cacheReadSum;
      const cacheEfficiency = denom > 0 ? (cacheReadSum / denom) : 0;

      return {
        totalCost: totalCost,
        medianCost: median,
        avgCost: avg,
        cacheEfficiency: cacheEfficiency,
        topCost: sorted[sorted.length - 1] || 0,
        minCost: sorted[0] || 0,
        sessionCount: list.length,
        totalInputTokens: inputSum,
        totalCacheReadTokens: cacheReadSum,
      };
    }

    function buildSessionRow(s, maxCost) {
      const isActive = !s.ended_at;
      const cost = Number(s.estimated_cost_usd) || 0;
      const isWhale = (maxCost > 0) && (cost === maxCost);
      const costPct = maxCost > 0 ? Math.round((cost / maxCost) * 100) : 0;

      const classes = [];
      if (isActive) classes.push('is-active');
      if (isWhale) classes.push('is-whale');

      const startCell = formatStartedAt(s.started_at);
      const durHtml = isActive
        ? '<span class="live-pill">進行中</span>'
        : esc(formatDuration(s.duration_seconds));
      const durTdCls = isActive ? '' : ' class="sess-dur"';

      const tokens = s.tokens || {};
      const skillCount = Number(s.skill_count || 0);
      const subagentCount = Number(s.subagent_count || 0);
      const skillCls = skillCount > 0 ? 'count-pos' : 'count-zero';
      const subCls = subagentCount > 0 ? 'count-pos' : 'count-zero';

      const classAttr = classes.length ? ' class="' + classes.join(' ') + '"' : '';

      return '<tr' + classAttr + ' tabindex="0">' +
        '<td class="sess-time">' + esc(startCell) + '</td>' +
        '<td' + durTdCls + '>' + durHtml + '</td>' +
        '<td class="sess-proj">' + esc(s.project || '') + '</td>' +
        '<td>' + buildModelChips(s.models) + '</td>' +
        '<td class="num">' + esc(fmtTokens(tokens.input)) + '</td>' +
        '<td class="num">' + esc(fmtTokens(tokens.output)) + '</td>' +
        '<td class="num tok-cr sess-tok-cache">' + esc(fmtTokens(tokens.cache_read)) + '</td>' +
        '<td class="num tok-cc sess-tok-cache">' + esc(fmtTokens(tokens.cache_creation)) + '</td>' +
        '<td>' +
          '<div class="cost-cell" style="--cost-pct: ' + costPct + '%;">' +
            '<span class="cost-val">' + formatCostUsd(cost) + '</span>' +
            '<span class="cost-bar"></span>' +
          '</div>' +
        '</td>' +
        '<td>' + buildTierChips(s.service_tier_breakdown) + '</td>' +
        '<td class="num ' + skillCls + '">' + skillCount + '</td>' +
        '<td class="num ' + subCls + '">' + subagentCount + '</td>' +
        '</tr>';
    }

    function buildKpiHTML(kpi) {
      // 直近 N 件 合計コスト / 中央値 / 平均 / Cache 効率 — mock の決定通り 4 枚
      const totalLabel = '直近' + (kpi.sessionCount || 0) + '件 合計コスト';
      const cacheRead = kpi.totalCacheReadTokens || 0;
      const cacheDenom = (kpi.totalInputTokens || 0) + cacheRead;
      const cacheSubText = (cacheDenom > 0)
        ? 'cache_read <em>' + esc(fmtTokens(cacheRead)) + '</em> / 入力合計 <em>' +
          esc(fmtTokens(cacheDenom)) + '</em> tokens'
        : 'no data';
      const cards = [
        { id: 'kpi-sess-total', cls: '', k: totalLabel, v: formatCostUsd(kpi.totalCost), s: '' },
        { id: 'kpi-sess-median', cls: 'c-coral', k: 'セッション中央値', v: formatCostUsd(kpi.medianCost), s: '最大 <em>' + formatCostUsd(kpi.topCost) + '</em> · 最小 <em>' + formatCostUsd(kpi.minCost) + '</em>' },
        { id: 'kpi-sess-avg', cls: 'c-peri', k: 'セッション平均', v: formatCostUsd(kpi.avgCost), s: '' },
        { id: 'kpi-sess-cache', cls: 'c-peach', k: 'Cache 効率', v: Math.round((kpi.cacheEfficiency || 0) * 100) + '%', s: cacheSubText },
      ];
      return cards.map(function(g){
        return '<div class="kpi ' + g.cls + '" id="' + g.id + '">' +
          '<div class="k-row"><span class="k">' + esc(g.k) + '</span></div>' +
          '<div class="v sm">' + g.v + '</div>' +
          (g.s ? '<div class="s">' + g.s + '</div>' : '<div class="s">&nbsp;</div>') +
        '</div>';
      }).join('');
    }

    function renderSessions(data) {
      if (typeof document === 'undefined') return;
      if (document.body && document.body.dataset.activePage !== 'sessions') return;

      const sessions = (data && Array.isArray(data.session_breakdown)) ? data.session_breakdown : [];
      const tbody = document.querySelector('#sessionsTable tbody');
      const kpiRow = document.getElementById('sessionsKpi');
      const sub = document.getElementById('sessionsSub');

      const kpi = computeKpi(sessions);
      const maxCost = kpi.topCost;

      if (kpiRow) {
        kpiRow.innerHTML = buildKpiHTML(kpi);
      }

      if (tbody) {
        if (sessions.length === 0) {
          tbody.innerHTML = '<tr><td colspan="12" class="empty" style="text-align:center;color:var(--ink-faint);padding:20px;">no data</td></tr>';
        } else {
          tbody.innerHTML = sessions.map(function(s){ return buildSessionRow(s, maxCost); }).join('');
        }
      }

      if (sub) {
        const projectSet = {};
        for (let i = 0; i < sessions.length; i++) {
          const p = sessions[i].project || '';
          if (p) projectSet[p] = 1;
        }
        const projCount = Object.keys(projectSet).length;
        sub.textContent = sessions.length + ' sessions · ' + projCount + ' projects';
      }

      // help-pop の位置調整 (右端の help-pop が viewport を溢れないよう)
      if (typeof placeAllPops === 'function') {
        placeAllPops();
      }
    }

    if (typeof window !== 'undefined') {
      window.__sessions = {
        renderSessions: renderSessions,
        formatCostUsd: formatCostUsd,
        fmtTokens: fmtTokens,
        inferModelFamily: inferModelFamily,
        buildModelChips: buildModelChips,
        buildTierChips: buildTierChips,
        formatStartedAt: formatStartedAt,
        formatDuration: formatDuration,
        buildSessionRow: buildSessionRow,
        buildKpiHTML: buildKpiHTML,
        computeKpi: computeKpi,
      };
    }
  })();
