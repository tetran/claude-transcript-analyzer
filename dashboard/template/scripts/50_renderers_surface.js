  // ---- Surface 3 panel (Issue #74) ----
  // 共通 helper: ISO 8601 timestamp → "X日前" / "今日" の relative day 表示。
  function relDay(iso, nowMs) {
    if (!iso) return '—';
    const ms = Date.parse(iso);
    if (!isFinite(ms)) return '—';
    const days = Math.max(0, Math.floor((nowMs - ms) / 86400000));
    if (days === 0) return '今日';
    if (days === 1) return '昨日';
    return days + '日前';
  }

  // Panel 1: Skill 起動経路
  function renderSkillInvocationBreakdown(items) {
    if (document.body.dataset.activePage !== 'surface') return;
    const tbody = document.querySelector('#surface-inv tbody');
    const sub = document.getElementById('surface-inv-sub');
    if (!tbody) return;
    const MODE_LABEL = {
      'dual':      '🤝 dual',
      'llm-only':  '🤖 llm-only',
      'user-only': '👤 user-only',
    };
    const list = Array.isArray(items) ? items : [];
    if (list.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty">観測なし</td></tr>';
    } else {
      tbody.innerHTML = list.map(it => {
        const mode = it.mode || '';
        const t = Number(it.tool_count || 0);
        const s = Number(it.slash_count || 0);
        const rate = it.autonomy_rate;
        const hasRate = rate !== null && rate !== undefined;
        const rateNum = hasRate ? Number(rate) : null;
        const rateCell = hasRate
          ? Math.round(rateNum * 100) + '%'
          : '<span class="dim">—</span>';
        const rateClass = (hasRate && rateNum < 0.5) ? 'num rate-warn' : 'num';
        const tCellClass = t === 0 ? 'num dim' : 'num';
        const sCellClass = s === 0 ? 'num dim' : 'num';
        const al = it.skill + ' (' + mode + '): LLM ' + t + ' / User ' + s +
                   (hasRate ? ' / autonomy ' + Math.round(rateNum * 100) + '%' : '');
        return '<tr data-tip="inv" data-name="' + esc(it.skill) +
          '" data-mode="' + esc(mode) + '" data-t="' + t + '" data-s="' + s +
          '" data-rate="' + (hasRate ? rateNum : '') +
          '" tabindex="0" role="row" aria-label="' + esc(al) + '">' +
          '<td class="name">' + esc(it.skill) + '</td>' +
          '<td><span class="mode-chip mode-' + esc(mode) + '">' +
            esc(MODE_LABEL[mode] || mode) + '</span></td>' +
          '<td class="' + tCellClass + '">' + fmtN(t) + '</td>' +
          '<td class="' + sCellClass + '">' + fmtN(s) + '</td>' +
          '<td class="' + rateClass + '">' + rateCell + '</td>' +
          '</tr>';
      }).join('');
    }
    if (sub) sub.textContent = list.length + ' skill(s)';
  }

  // Panel 2: Skill lifecycle
  function renderSkillLifecycle(items) {
    if (document.body.dataset.activePage !== 'surface') return;
    const tbody = document.querySelector('#surface-life tbody');
    const sub = document.getElementById('surface-life-sub');
    if (!tbody) return;
    const TREND_LABEL = {
      'accelerating': '📈 加速',
      'stable':       '➡️ 安定',
      'decelerating': '📉 減速',
      'new':          '🌱 新規',
    };
    const list = Array.isArray(items) ? items : [];
    const nowMs = Date.now();
    if (list.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">観測なし</td></tr>';
    } else {
      tbody.innerHTML = list.map(it => {
        const trend = it.trend || 'stable';
        const c30 = Number(it.count_30d || 0);
        const ct = Number(it.count_total || 0);
        const first = relDay(it.first_seen, nowMs);
        const last = relDay(it.last_seen, nowMs);
        const al = it.skill + ': first=' + first + ', last=' + last +
                   ', 30d=' + c30 + ', total=' + ct + ', trend=' + trend;
        return '<tr data-tip="life" data-name="' + esc(it.skill) +
          '" data-trend="' + esc(trend) +
          '" data-30d="' + c30 + '" data-total="' + ct +
          '" tabindex="0" role="row" aria-label="' + esc(al) + '">' +
          '<td class="name">' + esc(it.skill) + '</td>' +
          '<td>' + esc(first) + '</td>' +
          '<td>' + esc(last) + '</td>' +
          '<td class="num">' + fmtN(c30) + '</td>' +
          '<td class="num">' + fmtN(ct) + '</td>' +
          '<td><span class="trend-chip trend-' + esc(trend) + '">' +
            esc(TREND_LABEL[trend] || trend) + '</span></td>' +
          '</tr>';
      }).join('');
    }
    if (sub) sub.textContent = list.length + ' skill(s)';
  }

  // Panel 3: Hibernating skills
  function renderSkillHibernating(payload) {
    if (document.body.dataset.activePage !== 'surface') return;
    const data = (payload && typeof payload === 'object') ? payload : {};
    const items = Array.isArray(data.items) ? data.items : [];
    const activeExcluded = Number(data.active_excluded_count || 0);
    const tbody = document.querySelector('#surface-hib tbody');
    const sub = document.getElementById('surface-hib-sub');
    const activeNote = document.getElementById('surface-hib-active-note');
    const activeText = document.getElementById('surface-hib-active-text');
    const STATUS_LABEL = {
      'warming_up': '🌱 新着',
      'resting':    '💤 休眠',
      'idle':       '🪦 死蔵',
    };
    if (!tbody) return;
    if (items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty">観測なし</td></tr>';
    } else {
      const nowMs = Date.now();
      tbody.innerHTML = items.map(it => {
        const status = it.status || 'idle';
        const mtime = relDay(it.mtime, nowMs);
        const last = it.last_seen ? relDay(it.last_seen, nowMs) :
                     '<span class="dim">(未使用)</span>';
        const days = it.days_since_last_use;
        const daysCell = (days === null || days === undefined) ?
                         '<span class="dim">—</span>' : (Number(days) + 'd');
        const al = it.skill + ' (' + status + '): mtime=' + mtime +
                   ', last=' + (it.last_seen ? relDay(it.last_seen, nowMs) : '未使用') +
                   ((days === null || days === undefined) ? '' : ', ' + days + '日経過');
        return '<tr data-tip="hib" data-name="' + esc(it.skill) +
          '" data-status="' + esc(status) +
          '" tabindex="0" role="row" aria-label="' + esc(al) + '">' +
          '<td class="name">' + esc(it.skill) + '</td>' +
          '<td><span class="status-chip status-' + esc(status) + '">' +
            esc(STATUS_LABEL[status] || status) + '</span></td>' +
          '<td>' + esc(mtime) + '</td>' +
          '<td>' + last + '</td>' +
          '<td class="num">' + daysCell + '</td>' +
          '</tr>';
      }).join('');
    }
    if (sub) sub.textContent = items.length + ' skill(s)';
    if (activeNote && activeText) {
      if (activeExcluded > 0) {
        activeText.textContent = '14日以内に使われた ' + activeExcluded +
                                  ' 件は非表示 (= active)。Lifecycle panel (上位 20 件) で見えます。';
        activeNote.hidden = false;
      } else {
        activeNote.hidden = true;
      }
    }
  }

  function fmtDur(ms) {
    if (ms == null || ms === '') return '-';
    const v = Number(ms);
    if (!isFinite(v)) return '-';
    if (v >= 1000) return (v / 1000).toFixed(1) + 's';
    return Math.round(v) + 'ms';
  }

