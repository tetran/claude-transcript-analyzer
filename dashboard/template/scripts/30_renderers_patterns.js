  // ============================================================
  //  Hourly heatmap renderer (Issue #58)
  //  server は UTC hour bucket を返し、ここで local TZ 変換 + (Mon=0..Sun=6) bin。
  //  page-scoped early-out で Patterns 非表示中は skip (#59〜#62 規範)。
  //  hashchange listener (下) が page 切替時に loadAndRender を再実行するので、
  //  navigate 直後でも空のままにはならない。
  // ============================================================
  function renderHourlyHeatmap(payload) {
    if (document.body.dataset.activePage !== 'patterns') return;
    const root = document.getElementById('patterns-heatmap');
    if (!root) return;
    const buckets = (payload && payload.buckets) || [];
    const matrix = Array.from({ length: 7 }, () => Array(24).fill(0));
    let max = 0;
    for (const b of buckets) {
      const d = new Date(b.hour_utc);
      if (isNaN(d.getTime())) continue;
      const wd = (d.getDay() + 6) % 7; // Mon=0..Sun=6
      const h = d.getHours();
      matrix[wd][h] += b.count;
      if (matrix[wd][h] > max) max = matrix[wd][h];
    }
    const labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    let html = '<div class="heatmap-col-axis"><span></span>';
    for (let h = 0; h < 24; h++) html += '<span>' + pad(h, 2) + '</span>';
    html += '</div>';
    for (let wd = 0; wd < 7; wd++) {
      html += '<div class="heatmap-row-label">' + labels[wd] + '</div>';
      for (let h = 0; h < 24; h++) {
        const c = matrix[wd][h];
        const intensity = max ? c / max : 0;
        const bg = c
          ? 'background: rgba(111, 227, 200, ' + (0.08 + intensity * 0.92).toFixed(3) + ')'
          : '';
        const al = labels[wd] + ' ' + pad(h, 2) + ':00 — ' + c + ' events';
        html += '<div class="heatmap-cell" style="' + bg + '"' +
          ' data-tip="heatmap" data-wd="' + labels[wd] + '" data-h="' + pad(h, 2) +
          '" data-c="' + c + '" tabindex="0" role="img" aria-label="' + al + '"></div>';
      }
    }
    root.innerHTML = html;
    const legend = document.getElementById('patterns-heatmap-legend');
    if (legend) {
      legend.innerHTML =
        '<span>0</span><span class="heatmap-legend-bar" aria-hidden="true"></span>' +
        '<span>peak ' + fmtN(max) + '</span>';
    }
    const sub = document.getElementById('patterns-heatmap-sub');
    if (sub) {
      const total = buckets.reduce((s, b) => s + b.count, 0);
      sub.textContent = fmtN(total) + ' events · ' + fmtN(buckets.length) + ' hour buckets';
    }
  }

  // ============================================================
  //  Skill cooccurrence renderer (Issue #59 / B1)
  //  page-scoped early-out + 空配列時の empty state 表示。pair は server から
  //  すでに count 降順 + lexicographic 昇順で並んでおり、再 sort 不要。
  // ============================================================
  function renderSkillCooccurrence(items) {
    if (document.body.dataset.activePage !== 'patterns') return;
    const tbody = document.querySelector('#patterns-cooccurrence tbody');
    if (!tbody) return;
    const list = Array.isArray(items) ? items : [];
    if (list.length === 0) {
      tbody.innerHTML = '<tr><td colspan="3" class="empty">共起データなし</td></tr>';
    } else {
      tbody.innerHTML = list.map((it) => {
        const a = (it.pair && it.pair[0]) || '';
        const b = (it.pair && it.pair[1]) || '';
        const c = it.count || 0;
        const al = a + ' ⨉ ' + b + ': ' + c + ' sessions';
        return '<tr data-tip="cooc" data-a="' + esc(a) + '" data-b="' + esc(b) + '"' +
          ' data-c="' + c + '" tabindex="0" aria-label="' + esc(al) + '">' +
          '<td class="skill">' + esc(a) + '</td>' +
          '<td class="skill">' + esc(b) + '</td>' +
          '<td class="num">' + fmtN(c) + '</td>' +
          '</tr>';
      }).join('');
    }
    const sub = document.getElementById('patterns-cooccurrence-sub');
    if (sub) {
      sub.textContent = list.length + ' pairs (top 100)';
    }
  }

  // ============================================================
  //  Project × Skill heatmap renderer (Issue #59 / B2)
  //  server は dense matrix + covered_count + total_count を返し、ここで描画。
  //  カバー率 (covered/total) を sub label に出して top 漏れの量を可視化 (Proposal 2)。
  // ============================================================
  function renderProjectSkillMatrix(payload) {
    if (document.body.dataset.activePage !== 'patterns') return;
    const root = document.getElementById('patterns-projskill');
    if (!root) return;
    const projects = (payload && Array.isArray(payload.projects)) ? payload.projects : [];
    const skills = (payload && Array.isArray(payload.skills)) ? payload.skills : [];
    const counts = (payload && Array.isArray(payload.counts)) ? payload.counts : [];

    const legend = document.getElementById('patterns-projskill-legend');
    const sub = document.getElementById('patterns-projskill-sub');

    if (projects.length === 0 || skills.length === 0) {
      root.style.gridTemplateColumns = '';
      root.innerHTML = '<div class="projskill-empty">データなし</div>';
      if (legend) legend.innerHTML = '';
      if (sub) sub.textContent = '';
      return;
    }

    let max = 0;
    for (const row of counts) {
      for (const c of row) if (c > max) max = c;
    }

    // dynamic grid: row label 列 + N skill 列
    root.style.gridTemplateColumns = '160px repeat(' + skills.length + ', minmax(40px, 1fr))';

    let html = '<div class="projskill-col-axis"><span></span>';
    for (const s of skills) html += '<span title="' + esc(s) + '">' + esc(s) + '</span>';
    html += '</div>';
    for (let i = 0; i < projects.length; i++) {
      const p = projects[i];
      html += '<div class="projskill-row-label" title="' + esc(p) + '">' + esc(p) + '</div>';
      const row = counts[i] || [];
      for (let j = 0; j < skills.length; j++) {
        const c = row[j] || 0;
        const intensity = max ? c / max : 0;
        const bg = c
          ? 'background: rgba(255, 201, 122, ' + (0.08 + intensity * 0.92).toFixed(3) + ')'
          : '';
        const al = p + ' × ' + skills[j] + ': ' + c + ' events';
        html += '<div class="projskill-cell" style="' + bg + '"' +
          ' data-tip="projskill" data-p="' + esc(p) + '" data-s="' + esc(skills[j]) +
          '" data-c="' + c + '" tabindex="0" role="img" aria-label="' + esc(al) + '"></div>';
      }
    }
    root.innerHTML = html;

    if (legend) {
      legend.innerHTML =
        '<span>0</span><span class="projskill-legend-bar" aria-hidden="true"></span>' +
        '<span>peak ' + fmtN(max) + '</span>';
    }
    if (sub) {
      const covered = (payload && payload.covered_count) || 0;
      const total = (payload && payload.total_count) || 0;
      let s = projects.length + ' projects × ' + skills.length + ' skills';
      if (total > 0) {
        const pct = Math.round((covered / total) * 100);
        s += ' · ' + pct + '% covered (' + fmtN(covered) + '/' + fmtN(total) + ')';
      }
      sub.textContent = s;
    }
  }

