  // Issue #69: live ダッシュボードの差分ハイライト + 更新概要 toast。
  //
  // ・closure-private state は 25 番ファイル冒頭 (= 全 main_js を wrap する単一
  //   IIFE 直下) に置く。20 番の loadAndRender が呼ばれる時点で評価済になるため
  //   TDZ ReferenceError は構造的に発生しない。
  // ・`commitLiveSnapshot(next)` を `__livePrev` への唯一の writer とし、
  //   20_load_and_render.js からの直接代入を禁止 (literal pin で grep 可能)。
  //   catch 経路で commit を呼ばないことで、fetch 失敗を跨いで snap1→snap3 の
  //   累積 delta が toast に出る (= 「失敗中も裏で動いていた」signal)。
  // ・rank row は loadAndRender ごとに innerHTML 完全置換で element 参照が detach
  //   するため、per-element timer state は WeakMap (NOT Map) で持つ。
  let __livePrev = null;
  let __toastTimer = null;       // display 期間終了 → fade-out 開始 timer
  let __toastFadeTimer = null;   // fade-out animation 終了 → hidden = true timer
  const __highlightTimers = new WeakMap();
  // SSE refresh / hashchange からの loadAndRender 並行発火による stale-snapshot
  // race を直列化で防ぐための state。
  //   __activeRender    : 現在 in-flight の loadAndRender 結果 promise (なければ null)
  //   __pendingRefresh  : in-flight 中に追加 schedule された場合の coalesce フラグ
  // race の具体例: fetch1 / fetch2 が overlap し fetch2 が先に return → DOM が
  // 古い fetch1 の data で上書きされ commitLiveSnapshot で __livePrev が snap1 に
  // 巻き戻る → 次の refresh diff が「snap1 → 現在」の累積 delta を toast に
  // 出してしまう。serialization で fetch を必ず順番に流すことで構造的に防ぐ。
  let __activeRender = null;
  let __pendingRefresh = false;

  // KPI / lede / ranking row のラベル定義。toast 対象 (LABEL テーブル) と
  // highlight のみの key を分離する。順序固定 = priority order を兼ねる
  // (5 種以上同時 delta 時に先頭 4 セグメントが残る)。
  const __TOAST_LABELS = [
    { id: 'kpi-total',   sing: 'event',                plur: 'events' },
    { id: 'kpi-skills',  sing: 'skill',                plur: 'skills' },
    { id: 'kpi-subs',    sing: 'subagent invocation',  plur: 'subagent invocations' },
    { id: 'kpi-sess',    sing: 'session',              plur: 'sessions' },
    { id: 'kpi-projs',   sing: 'project',              plur: 'projects' },
    { id: 'kpi-compact', sing: 'compaction',           plur: 'compactions' },
    { id: 'kpi-perm',    sing: 'permission',           plur: 'permissions' },
  ];
  const __TOAST_MAX_SEGMENTS = 4;
  const __HIGHLIGHT_MS = 1500;
  const __TOAST_MS = 4000;
  // CSS の `.toast { transition: opacity 240ms ease, transform 240ms ease }` と同期。
  // `.show` を remove したあと `__TOAST_FADE_MS` 経過してから `hidden = true` にする
  // ことで fade-out transition を見える状態で完走させる (display: none で transition
  // を打ち切らない)。CSS 側を変えたらこの定数も同期して変えること。
  // prefers-reduced-motion 環境では CSS transition が 200ms に短縮されるが、240ms
  // 待つことでフェード完了後の hidden 化を構造的に保証する。
  const __TOAST_FADE_MS = 240;

  function buildLiveSnapshot(data) {
    const d = (data && typeof data === 'object') ? data : {};
    const ss = (d.session_stats && typeof d.session_stats === 'object') ? d.session_stats : {};
    const skillRanking = Array.isArray(d.skill_ranking) ? d.skill_ranking : [];
    const subRanking = Array.isArray(d.subagent_ranking) ? d.subagent_ranking : [];
    const projects = Array.isArray(d.project_breakdown) ? d.project_breakdown : [];
    const buckets = (d.hourly_heatmap && Array.isArray(d.hourly_heatmap.buckets))
      ? d.hourly_heatmap.buckets : [];
    const localDays = localDailyFromHourly(buckets);

    // Issue #81: KPI tile / lede の "unique kinds" は 20_load_and_render.js と同じ
    // `*_kinds_total` / `project_total` (cap 無し) を読む。defensive fallback (`!= null`)
    // で旧 length に逃げる構造保証は loadAndRender 側と完全に揃える。
    // `Number(...) || 0` を使わない理由: 値が `0` のとき falsy として fallback の
    // length に化けてしまい、本当に 0 種類のときの diff 比較が壊れる。
    const kpi = {
      'kpi-total':   Number(d.total_events) || 0,
      'kpi-skills':  (d.skill_kinds_total != null ? Number(d.skill_kinds_total) : skillRanking.length),
      'kpi-subs':    (d.subagent_kinds_total != null ? Number(d.subagent_kinds_total) : subRanking.length),
      'kpi-projs':   (d.project_total != null ? Number(d.project_total) : projects.length),
      'kpi-sess':    Number(ss.total_sessions) || 0,
      'kpi-resume':  Number(ss.resume_rate) || 0,
      'kpi-compact': Number(ss.compact_count) || 0,
      'kpi-perm':    Number(ss.permission_prompt_count) || 0,
    };
    const lede = {
      ledeEvents:   Number(d.total_events) || 0,
      ledeDays:     localDays.length,
      ledeProjects: (d.project_total != null ? Number(d.project_total) : projects.length),
    };
    const rankSkill = new Map();
    for (const it of skillRanking) {
      if (it && typeof it.name === 'string' && it.name) {
        rankSkill.set(it.name, Number(it.count) || 0);
      }
    }
    const rankSub = new Map();
    for (const it of subRanking) {
      if (it && typeof it.name === 'string' && it.name) {
        rankSub.set(it.name, Number(it.count) || 0);
      }
    }
    return { kpi, lede, rankSkill, rankSub };
  }

  function diffLiveSnapshot(prev, next) {
    const empty = { kpi: [], lede: [], rankSkill: [], rankSub: [] };
    if (prev === null || prev === undefined || !next) return empty;
    const kpi = [];
    for (const id of Object.keys(next.kpi || {})) {
      const cur = Number(next.kpi[id]) || 0;
      const old = Number((prev.kpi || {})[id]) || 0;
      const delta = cur - old;
      if (delta > 0) kpi.push({ id, delta });
    }
    const lede = [];
    for (const id of Object.keys(next.lede || {})) {
      const cur = Number(next.lede[id]) || 0;
      const old = Number((prev.lede || {})[id]) || 0;
      const delta = cur - old;
      if (delta > 0) lede.push({ id, delta });
    }
    const rankSkill = __diffRankMap(prev.rankSkill, next.rankSkill);
    const rankSub = __diffRankMap(prev.rankSub, next.rankSub);
    return { kpi, lede, rankSkill, rankSub };
  }

  function __diffRankMap(prevMap, nextMap) {
    const out = [];
    if (!nextMap || typeof nextMap.forEach !== 'function') return out;
    nextMap.forEach((cur, name) => {
      const old = (prevMap && typeof prevMap.get === 'function' && prevMap.has(name))
        ? Number(prevMap.get(name)) || 0
        : 0;
      const delta = (Number(cur) || 0) - old;
      if (delta > 0) out.push({ name, delta });
    });
    return out;
  }

  function formatToastSummary(diff) {
    if (!diff || !Array.isArray(diff.kpi)) return '';
    const byId = new Map(diff.kpi.map(e => [e.id, e.delta]));
    const segments = [];
    for (const lab of __TOAST_LABELS) {
      const delta = byId.get(lab.id);
      if (typeof delta !== 'number' || delta <= 0) continue;
      const label = (delta === 1) ? lab.sing : lab.plur;
      segments.push('+' + delta + ' ' + label);
      if (segments.length >= __TOAST_MAX_SEGMENTS) break;
    }
    return segments.join(' · ');
  }

  function applyHighlights(diff) {
    if (!diff || typeof document === 'undefined') return;
    if (Array.isArray(diff.kpi)) {
      for (const e of diff.kpi) __bumpById(e.id);
    }
    if (Array.isArray(diff.lede)) {
      for (const e of diff.lede) __bumpById(e.id);
    }
    if (Array.isArray(diff.rankSkill)) {
      for (const e of diff.rankSkill) __bumpRankRow(e.name, 'skill');
    }
    if (Array.isArray(diff.rankSub)) {
      for (const e of diff.rankSub) __bumpRankRow(e.name, 'subagent');
    }
  }

  function __bumpById(id) {
    const el = document.getElementById(id);
    if (!el) return;
    __bumpElement(el);
  }

  function __bumpRankRow(name, kind) {
    const root = document.getElementById(kind === 'subagent' ? 'subBody' : 'skillBody');
    if (!root) return;
    // CSS attribute selector に流す前にエスケープ。"\\" の literal で 1 個の
    // バックスラッシュを送る (attribute value 内の特殊文字 quoting)。
    const safeName = String(name).replace(/(["\\])/g, '\\$1');
    const el = root.querySelector('.rank-row[data-name="' + safeName + '"]');
    if (!el) return;
    __bumpElement(el);
  }

  function __bumpElement(el) {
    const prevTimer = __highlightTimers.get(el);
    if (prevTimer) {
      clearTimeout(prevTimer);
      el.classList.remove('bumped');
      // animation 再起動 (reflow) — 同 element の連続 bump で先頭 frame からやり直す
      void el.offsetWidth;
    }
    el.classList.add('bumped');
    const t = setTimeout(() => {
      el.classList.remove('bumped');
      __highlightTimers.delete(el);
    }, __HIGHLIGHT_MS);
    __highlightTimers.set(el, t);
  }

  function showLiveToast(msg) {
    if (typeof document === 'undefined') return;
    const el = document.getElementById('liveToast');
    if (!el) return;
    // 連続 refresh で複数 toast が来たら、前回の display end / fade-out end の
    // どちらの timer も取り消して新 toast の lifecycle を 0 から始める。
    if (__toastTimer) { clearTimeout(__toastTimer); __toastTimer = null; }
    if (__toastFadeTimer) { clearTimeout(__toastFadeTimer); __toastFadeTimer = null; }
    if (!msg) {
      el.hidden = true;
      el.textContent = '';
      el.classList.remove('show');
      el.classList.remove('fading');
      return;
    }
    el.textContent = msg;
    el.hidden = false;
    // 「上書きされた」signal を確実に出すため CSS animation (@keyframes toast-in)
    // を毎回再起動する。CSS transition (前後値の差分判定) ではなく CSS animation
    // (class が付いた瞬間に再生) を使うことで、表示中の toast に re-trigger が
    // 来ても reflow trick (`.remove + offsetWidth + .add`) で確実に slide-in が
    // 再生される (CSS transition 方式だと Browser が同フレーム内の連続 style 変更を
    // collapse して transition を skip する問題があり、実機検証で動かないことを確認済)。
    //
    // prefers-reduced-motion 環境では CSS 側で animation を無効化し opacity の
    // transition のみ残す (10_components.css 参照)。
    el.classList.remove('show');
    el.classList.remove('fading');
    void el.offsetWidth;
    el.classList.add('show');
    __toastTimer = setTimeout(() => {
      // fade-out animation (@keyframes toast-out) を発火。display: none を即座に
      // 当てると animation が打ち切られるため、__TOAST_FADE_MS 後に hidden = true。
      el.classList.remove('show');
      el.classList.add('fading');
      __toastTimer = null;
      __toastFadeTimer = setTimeout(() => {
        el.hidden = true;
        el.classList.remove('fading');
        __toastFadeTimer = null;
      }, __TOAST_FADE_MS);
    }, __TOAST_MS);
  }

  function commitLiveSnapshot(next) {
    __livePrev = next;
  }

  // loadAndRender の overlap を直列化する wrapper。
  //
  // 70_init_eventsource.js の SSE message handler / 60_hashchange_listener.js
  // から fire-and-forget で呼ばれる経路を経由させ、in-flight 中の追加要求は
  // __pendingRefresh = true で coalesce し、現 render 完了後に 1 回だけ追加 fire
  // する。これにより:
  //   - DOM (kpiRow / skillBody / sparkline 等) が古い data の遅延 return で上書き
  //     されるのを防ぐ
  //   - commitLiveSnapshot の writer も常に 1 つに絞られるため __livePrev の
  //     stale snapshot 巻き戻りを構造的に防ぐ
  //   - burst 中に queue が肥大しない (常に最大 1 件 pending)
  //
  // Promise.resolve().then(...) で loadAndRender 呼出を 1 microtask 遅延させて
  // いるのは、`scheduleLoadAndRender()` の戻り値で `__activeRender` を見せた
  // 直後に同期的に loadAndRender を呼ばないためで、テスト時の microtask flush
  // 観測を簡単にする副次効果もある。
  function scheduleLoadAndRender() {
    if (__activeRender) {
      __pendingRefresh = true;
      return __activeRender;
    }
    __activeRender = Promise.resolve()
      .then(function () { return loadAndRender(); })
      .finally(function () {
        __activeRender = null;
        if (__pendingRefresh) {
          __pendingRefresh = false;
          scheduleLoadAndRender();
        }
      });
    return __activeRender;
  }

  // test fixture / dev probe からの read-only access。production 経路では使わない
  // (20 番からは diffLiveSnapshot の第一引数として渡す path のみ)。
  if (typeof window !== 'undefined') {
    window.__liveDiff = {
      buildLiveSnapshot,
      diffLiveSnapshot,
      formatToastSummary,
      commitLiveSnapshot,
      scheduleLoadAndRender,
      getLivePrev: function () { return __livePrev; },
    };
  }

