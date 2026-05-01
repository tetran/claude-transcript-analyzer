  async function loadAndRender() {
  let data;
  try {
    // Issue #85: period toggle 値を毎 fetch 時に評価して URL に載せる。
    // 関数参照を IIFE 評価時に capture せず call-time lookup する形なので、
    // toggle 切替直後の SSE refresh も新 period で fetch される (race-free)。
    const __periodVal = (typeof getCurrentPeriod === 'function') ? getCurrentPeriod() : 'all';
    const __apiUrl = '/api/data?period=' + encodeURIComponent(__periodVal);
    data = (typeof window.__DATA__ !== 'undefined')
      ? window.__DATA__
      : await (await fetch(__apiUrl, { cache: 'no-store' })).json();
  } catch (e) {
    console.error('データの読み込みに失敗しました:', e);
    return;
  }
  const ss = data.session_stats || {};

  // header (Issue #65: local TZ 表記に統一)
  document.getElementById('lastRx').textContent = formatLocalTimestamp(data.last_updated);
  document.getElementById('sessVal').textContent = (ss.total_sessions || 0) + ' sessions';

  // Issue #65: daily 系 KPI / sparkline は data.daily_trend (= server UTC bucket) ではなく
  // hourly_heatmap.buckets を local TZ で再集計した localDays を使う。
  // daily_trend は /api/data の backward-compat field として残るが client は読まない。
  const localDays = localDailyFromHourly((data.hourly_heatmap || {}).buckets || []);
  document.getElementById('ledeEvents').textContent = fmtN(data.total_events);
  document.getElementById('ledeDays').textContent = localDays.length;
  // Issue #81: KPI / lede の "unique kinds" は `*_kinds_total` / `project_total` (cap 無し)。
  // ranking 配列 (`*_ranking` / `project_breakdown`) は引き続き 10 件 cap で UI 表示用。
  // defensive fallback: 古い静的 HTML / 一時 server-frontend 不整合では新 field 不在 → 旧 length に fallback。
  // `??` ではなく `!= null` 三項演算子: KPI counter は `0` も valid な値で、`||` は `0` を falsy 扱いしてしまうため。
  document.getElementById('ledeProjects').textContent =
    (data.project_total != null ? data.project_total : (data.project_breakdown||[]).length);

  // ---- KPI definitions (ヘルプ本文を含む) ----
  const kpis = [
    { id: 'kpi-total', k: 'total events', v: fmtN(data.total_events), s: '<em>' + localDays.length + '</em> 日間の観測', cls: '',
      helpTtl: '総イベント数', helpBody: 'スキル利用と subagent invocation の合計件数。subagent は PostToolUse / SubagentStart の重複発火を <code>1 invocation = 1 件</code> に dedup 済み。session_start や notification は含めない。' },
    { id: 'kpi-skills', k: 'skills',
      v: (data.skill_kinds_total != null ? data.skill_kinds_total : (data.skill_ranking||[]).length),
      s: 'unique kinds', cls: '',
      helpTtl: 'スキル種別数', helpBody: '観測されたスキルの種類数。スキル本体（PostToolUse(Skill)）とユーザー入力のスラッシュコマンド（UserPromptExpansion / Submit）を合算してカウント。' },
    { id: 'kpi-subs', k: 'subagents',
      v: (data.subagent_kinds_total != null ? data.subagent_kinds_total : (data.subagent_ranking||[]).length),
      s: 'unique kinds', cls: 'c-coral',
      helpTtl: 'Subagent 種別数', helpBody: '観測された subagent の種類数（invocation 単位で dedup 済み）。' },
    { id: 'kpi-projs', k: 'projects',
      v: (data.project_total != null ? data.project_total : (data.project_breakdown||[]).length),
      s: 'distinct cwds', cls: 'c-peach',
      helpTtl: 'プロジェクト数', helpBody: '利用が観測されたプロジェクト（cwd 単位）。同じディレクトリ配下のセッションは同一プロジェクトとして集計。' },
    { id: 'kpi-sess', k: 'sessions', v: ss.total_sessions || 0, cls: 'c-peri',
      helpTtl: 'セッション数', helpBody: 'SessionStart hook で観測された Claude Code セッションの開始回数。同じ session_id の startup と resume は別セッションとして数える。' },
    { id: 'kpi-resume', k: 'resume rate', v: ss.total_sessions ? Math.round((ss.resume_rate||0)*100)+'%' : '--', sm: true, cls: 'c-mute',
      helpTtl: 'Resume 率', helpBody: 'セッション開始のうち <code>--resume</code> での再開（source="resume"）が占める割合。新規 startup と区別される。' },
    { id: 'kpi-compact', k: 'compactions', v: ss.compact_count || 0, sm: true, cls: 'c-mute',
      helpTtl: 'Compact 数', helpBody: 'コンテキスト自動圧縮（PreCompact hook）の発生回数。auto / manual の両方を合算。' },
    { id: 'kpi-perm', k: 'permission gate', v: ss.permission_prompt_count || 0, sm: true,
      cls: (ss.permission_prompt_count||0) > 5 ? 'warn' : 'c-mute',
      warn: (ss.permission_prompt_count||0) > 5,
      helpTtl: 'Permission Prompt', helpBody: '許可ダイアログ（Notification の type=<code>permission</code> / <code>permission_prompt</code>）の発生回数。多いと作業中の中断が増えていることを示す。' },
  ];

  document.getElementById('kpiRow').innerHTML = kpis.map(g => {
    const popId = 'hp-' + g.id;
    return '<div class="kpi ' + g.cls + (g.warn?' warn':'') + '" id="' + g.id + '">' +
      '<div class="k-row">' +
        '<span class="k">' + esc(g.k) + '</span>' +
        '<span class="help-host">' +
          '<button class="help-btn" type="button" aria-label="説明を表示" aria-expanded="false" aria-describedby="' + popId + '" data-help-id="' + popId + '">?</button>' +
          '<span class="help-pop" id="' + popId + '" role="tooltip" data-place="right">' +
            '<span class="pop-ttl">' + esc(g.helpTtl) + '</span>' +
            '<span class="pop-body">' + g.helpBody + '</span>' +
          '</span>' +
        '</span>' +
      '</div>' +
      '<div class="v' + (g.sm?' sm':'') + '">' + g.v + '</div>' +
      (g.s ? '<div class="s">' + g.s + '</div>' : '<div class="s">&nbsp;</div>') +
    '</div>';
  }).join('');

  // ---- ranking renderer ----
  function renderRank(elId, items, kind) {
    const el = document.getElementById(elId);
    if (!items.length) { el.innerHTML = '<div style="color:var(--ink-faint);text-align:center;padding:20px">no data</div>'; return; }
    const max = Math.max(...items.map(i => i.count));
    el.innerHTML = items.map((it, i) => {
      const slash = it.name.startsWith('/');
      let nameHtml;
      if (slash) {
        const rest = it.name.slice(1);
        const colon = rest.indexOf(':');
        if (colon > -1) nameHtml = '<span class="slash">/</span><span class="ns">' + esc(rest.slice(0,colon+1)) + '</span>' + esc(rest.slice(colon+1));
        else nameHtml = '<span class="slash">/</span>' + esc(rest);
      } else {
        nameHtml = esc(it.name);
      }
      const pct = max ? (it.count/max*100) : 0;
      const meta = [];
      if (it.failure_count > 0) meta.push('<span class="fail">FAIL ' + it.failure_count + ' (' + Math.round((it.failure_rate||0)*100) + '%)</span>');
      if (it.avg_duration_ms != null) meta.push('avg ' + (it.avg_duration_ms>=1000? (it.avg_duration_ms/1000).toFixed(1)+'s':Math.round(it.avg_duration_ms)+'ms'));
      const metaHtml = meta.length ? '<div class="meta">' + meta.join(' · ') + '</div>' : '';
      // data-tip="rank" で行全体（gauge-bar / 名前 / meta 含む）を hover 対象にする
      // (Issue #50)。native title= は floating tooltip と重複するため削除。
      const dataAttrs =
        ' data-tip="rank" data-name="' + esc(it.name) + '" data-c="' + it.count + '"' +
        ' data-kind="' + kind + '"' +
        (it.failure_count != null ? ' data-fail="' + it.failure_count + '"' : '') +
        (it.failure_rate != null ? ' data-fail-rate="' + it.failure_rate + '"' : '') +
        (it.avg_duration_ms != null ? ' data-avg="' + it.avg_duration_ms + '"' : '');
      const al = it.name + ': ' + it.count + (kind === 'subagent' ? ' invocations' : ' uses');
      return '<div class="rank-row ' + kind + '"' + dataAttrs +
        ' tabindex="0" role="img" aria-label="' + esc(al) + '">' +
        '<div class="rk">' + pad(i+1,2) + '</div>' +
        '<div class="rn">' + nameHtml + '</div>' +
        '<div class="rv">' + fmtN(it.count) + '</div>' +
        '<div class="gauge-bar"><div class="gb" style="width:' + pct + '%"></div></div>' +
        metaHtml +
      '</div>';
    }).join('');
  }
  renderRank('skillBody', data.skill_ranking || [], 'skill');
  renderRank('subBody', data.subagent_ranking || [], 'subagent');
  document.getElementById('skillSub').textContent = 'top ' + (data.skill_ranking||[]).length + ' · max ' + (((data.skill_ranking||[])[0]||{}).count || 0);
  document.getElementById('subSub').textContent = 'top ' + (data.subagent_ranking||[]).length + ' · max ' + (((data.subagent_ranking||[])[0]||{}).count || 0);

  // ---- sparkline (Issue #65: local TZ 集約) ----
  // localDays は localDailyFromHourly で sort 済 / local 日付 key。
  const trend = localDays;
  if (trend.length) {
    const W = 800, H = 168, pad_x = 10, pad_y = 18;
    const byDate = new Map(trend.map(d=>[d.date, d.count]));
    // 観測 0 の中間日も x-axis に並べる densify を local TZ で iterate する。
    // toISOString は UTC 日付を返してしまうため使わない。年月日の数値を保持して
    // new Date(y, m-1, d).setDate(+1) で 1 日進める (DST 境界跨ぎは Date が
    // 自動補正してくれるので、月 / 年またぎでも setDate(+1) が正しく wrap する)。
    const days = [];
    const [sy, sm, sd] = trend[0].date.split('-').map(Number);
    const [ey, em, ed] = trend[trend.length-1].date.split('-').map(Number);
    const cursor = new Date(sy, sm-1, sd);
    const endLocal = new Date(ey, em-1, ed);
    // 異常 input (start > end) でも無限ループしない safety: 最大 365 * 5 日 = 5 年
    let safety = 0;
    while (cursor <= endLocal && safety < 365 * 5) {
      const ds = cursor.getFullYear() + '-' + pad(cursor.getMonth()+1, 2) + '-' + pad(cursor.getDate(), 2);
      days.push({ date: ds, count: byDate.get(ds) || 0 });
      cursor.setDate(cursor.getDate() + 1);
      safety += 1;
    }
    const max = Math.max(...days.map(d=>d.count));
    const xs = (i) => pad_x + i * (W - 2*pad_x) / Math.max(1, days.length-1);
    const ys = (c) => H - pad_y - (max ? (c/max) * (H - 2*pad_y) : 0);

    const linePath = days.map((d,i)=> (i===0?'M':'L') + xs(i).toFixed(2) + ' ' + ys(d.count).toFixed(2)).join(' ');
    const areaPath = linePath + ' L' + xs(days.length-1).toFixed(2) + ' ' + (H-pad_y) + ' L' + xs(0).toFixed(2) + ' ' + (H-pad_y) + ' Z';

    const peakIdx = days.findIndex(d => d.count === max);
    const peakDate = days[peakIdx].date;

    // 可視 dot は count>0 のみ（0 を打つと視覚ノイズ）。data 属性を持たない pure
    // visual。hover 判定は後段の day-band rect が持つので分離している。
    const dots = days.map((d,i) => {
      if (d.count <= 0) return '';
      const cx = xs(i).toFixed(2);
      const cy = ys(d.count).toFixed(2);
      return '<circle cx="' + cx + '" cy="' + cy + '" r="1.7" fill="#8aa6ff" fill-opacity="0.85"/>';
    }).join('');

    // 各日に対する全高 hit-band（透明な rect / 0 件の日も含む / Issue #50）。
    // dot 直径 12px から chart 全高 (~130px) へ判定領域を拡張し、value=0 でも
    // tooltip を出せるよう band 単位で構成する。bands は SVG の最後に rendering
    // して z-order 上 line / dots / peak line より前面に置く（透明だが pointer-events
    // を受ける）。
    const bandHalfW = days.length > 1
      ? (W - 2*pad_x) / (days.length - 1) / 2
      : (W - 2*pad_x) / 2;
    const bands = days.map((d,i) => {
      const cx = xs(i);
      const x = Math.max(0, cx - bandHalfW).toFixed(2);
      const w = Math.min(W - parseFloat(x), bandHalfW * 2).toFixed(2);
      const al = d.date + ': ' + d.count + ' events';
      return '<rect class="day-band" x="' + x + '" y="0" width="' + w + '" height="' + (H - pad_y) + '" ' +
        'fill="transparent" data-tip="daily" data-d="' + d.date + '" data-c="' + d.count + '" ' +
        'tabindex="0" role="img" aria-label="' + al + '"/>';
    }).join('');

    const ticks = days.map((d,i) => i % Math.ceil(days.length/8) === 0
      ? '<text x="' + xs(i).toFixed(2) + '" y="' + (H - 3) + '" font-size="9.5" font-family="JetBrains Mono, monospace" fill="#7e8290" text-anchor="middle">' + d.date.slice(5) + '</text>'
      : ''
    ).join('');

    const grid = [0, 0.25, 0.5, 0.75, 1].map(p => {
      const y = pad_y + p*(H - 2*pad_y);
      return '<line x1="0" y1="' + y + '" x2="' + W + '" y2="' + y + '" stroke="rgba(138,166,255,0.06)" stroke-width="1"/>';
    }).join('');

    document.getElementById('spark').innerHTML = '' +
      '<defs><linearGradient id="g1" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0%" stop-color="#8aa6ff" stop-opacity="0.32"/>' +
        '<stop offset="100%" stop-color="#8aa6ff" stop-opacity="0"/>' +
      '</linearGradient></defs>' +
      grid +
      '<path d="' + areaPath + '" fill="url(#g1)"/>' +
      '<path d="' + linePath + '" stroke="#8aa6ff" stroke-width="1.6" fill="none" stroke-linejoin="round" stroke-linecap="round"/>' +
      dots +
      (max > 0 ? (
        '<line x1="' + xs(peakIdx) + '" y1="' + pad_y + '" x2="' + xs(peakIdx) + '" y2="' + (H-pad_y) + '" stroke="#ffc97a" stroke-dasharray="3,3" stroke-width="1" stroke-opacity="0.75"/>' +
        '<text x="' + xs(peakIdx) + '" y="' + (pad_y - 5) + '" font-size="9.5" font-family="JetBrains Mono, monospace" fill="#ffc97a" text-anchor="middle">peak ' + max + '</text>'
      ) : '') +
      ticks +
      bands;

    const total = days.reduce((s,d)=>s+d.count, 0);
    const avg = total / days.length;
    const active = days.filter(d=>d.count>0).length;
    const sparkStats = [
      { k: 'peak',     v: max + (max > 0 ? ' / ' + peakDate.slice(5) : '') },
      { k: 'avg/day',  v: avg.toFixed(1) },
      { k: 'active',   v: active + '/' + days.length + 'd' },
      { k: 'window',   v: days[0].date.slice(5) + ' → ' + days[days.length-1].date.slice(5) },
    ];
    document.getElementById('sparkStats').innerHTML = sparkStats.map(r =>
      '<div class="row"><span class="k">' + r.k + '</span><span class="v">' + r.v + '</span></div>'
    ).join('');
    document.getElementById('dailySub').textContent = days.length + ' days · ' + active + ' active';
  }

  // ---- projects ----
  const projs = (data.project_breakdown||[]);
  const projTotal = projs.reduce((s,p)=>s+p.count, 0);
  const palette = ['#6fe3c8','#ff8a76','#8aa6ff','#ffc97a','#ff6f9c','#a78bfa','#7ed3a3','#ffa86b','#5dc9e2','#e6a8e8'];
  function projPct(p) { return projTotal ? (p.count/projTotal*100).toFixed(1) + '%' : '0.0%'; }
  function projAria(p, pct) { return esc(p.project) + ': ' + fmtN(p.count) + ' events (' + pct + ')'; }
  document.getElementById('stack').innerHTML = projs.map((p, i) => {
    const w = projTotal ? (p.count/projTotal*100) : 0;
    const pct = projPct(p);
    return '<div class="seg" data-tip="proj" data-p="' + esc(p.project) + '" data-c="' + p.count + '" data-pct="' + pct + '" ' +
      'tabindex="0" role="img" aria-label="' + projAria(p, pct) + '" ' +
      'style="background:' + palette[i % palette.length] + ';width:' + w + '%"></div>';
  }).join('');
  document.getElementById('stackLegend').innerHTML = projs.map((p, i) => {
    const pct = projPct(p);
    const display = p.project.length > 28 ? p.project.slice(0,26) + '…' : p.project;
    return '<div class="leg-row" data-tip="proj" data-p="' + esc(p.project) + '" data-c="' + p.count + '" data-pct="' + pct + '" ' +
      'tabindex="0" aria-label="' + projAria(p, pct) + '">' +
      '<div class="sw" style="background:' + palette[i % palette.length] + '"></div>' +
      '<div class="pn">' + esc(display) + '</div>' +
      '<div class="pc">' + fmtN(p.count) + '</div>' +
      '<div class="pp">' + pct + '</div>' +
    '</div>';
  }).join('');
  document.getElementById('projSub').textContent = projs.length + ' projects · Σ ' + fmtN(projTotal);

  // ---- hourly heatmap (Issue #58) ----
  renderHourlyHeatmap(data.hourly_heatmap);

  // ---- skill cooccurrence (Issue #59 / B1) ----
  renderSkillCooccurrence(data.skill_cooccurrence);

  // ---- project × skill heatmap (Issue #59 / B2) ----
  renderProjectSkillMatrix(data.project_skill_matrix);

  // ---- subagent percentile table (Issue #60 / A5) ----
  renderSubagentPercentile(data.subagent_ranking);
  // ---- subagent failure weekly trend (Issue #60 / B3) ----
  renderSubagentFailureTrend(data.subagent_failure_trend);

  // ---- A2 permission breakdowns (Issue #61) ----
  renderPermissionSkillBreakdown(data.permission_prompt_skill_breakdown);
  renderPermissionSubagentBreakdown(data.permission_prompt_subagent_breakdown);
  // ---- A3 compact density (Issue #61) ----
  renderCompactDensity(data.compact_density);

  // ---- Surface 3 panel (Issue #74) ----
  renderSkillInvocationBreakdown(data.skill_invocation_breakdown);
  renderSkillLifecycle(data.skill_lifecycle);
  renderSkillHibernating(data.skill_hibernating);

  // dynamic re-render 後の help-pop 再配置 (Issue #41)。kpiRow を含む全 popup を
  // walk して、右端 KPI tooltip の viewport overflow を防ぐ。
  placeAllPops();

  // Issue #69: live mode のときだけ差分 highlight + 更新概要 toast を発火。
  // static export (window.__DATA__) 経路では prevSnapshot の比較相手が無く、
  // diff が UX 上 noise なので skip する。__livePrev は 25_live_diff.js 側に
  // closure-private に置かれていて、ここからは read-only 参照 + commit helper
  // 経由でのみ更新する (catch 経路で commit を呼ばない契約を構造保証)。
  if (typeof window === 'undefined' || typeof window.__DATA__ === 'undefined') {
    const __liveNext = buildLiveSnapshot(data);
    if (__livePrev !== null) {
      const __liveDiff = diffLiveSnapshot(__livePrev, __liveNext);
      applyHighlights(__liveDiff);
      showLiveToast(formatToastSummary(__liveDiff));
    }
    commitLiveSnapshot(__liveNext);
  }
  } // end loadAndRender
