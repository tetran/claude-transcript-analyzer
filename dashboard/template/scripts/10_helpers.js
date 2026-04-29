  function esc(s){ return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]); }
  function fmtN(n){ return Number(n).toLocaleString('en-US'); }
  function pad(s,n){ s=String(s); return s.length>=n?s:('0'.repeat(n-s.length)+s); }

  // Issue #65: header / sparkline で local TZ 表示するための共通ヘルパ。
  // server は UTC のまま timestamp / hour bucket を返し、ここで local TZ 変換に統一する
  // (前例: hourly_heatmap renderer)。
  //
  // formatLocalTimestamp: ISO 8601 → "YYYY-MM-DD HH:mm <TZ>"。TZ 短縮名は
  // Intl.DateTimeFormat の timeZoneName: 'short' に委譲しており、環境依存
  // (例: "JST" / "GMT+9" のいずれかが返ることがある)。
  function formatLocalTimestamp(iso) {
    const dt = new Date(iso);
    if (isNaN(dt.getTime())) return '';
    let tz = '';
    try {
      const parts = new Intl.DateTimeFormat(undefined, { timeZoneName: 'short' }).formatToParts(dt);
      const tp = parts.find(p => p.type === 'timeZoneName');
      if (tp) tz = tp.value;
    } catch (e) {
      // Intl が壊れている環境では TZ を空にして本体だけ返す
      tz = '';
    }
    const base = dt.getFullYear() + '-' + pad(dt.getMonth()+1, 2) + '-' + pad(dt.getDate(), 2)
      + ' ' + pad(dt.getHours(), 2) + ':' + pad(dt.getMinutes(), 2);
    return tz ? base + ' ' + tz : base;
  }

  // localDailyFromHourly: hourly_heatmap.buckets ([{hour_utc, count}]) を local TZ 日付で
  // 集約し sparkline 用 [{date: "YYYY-MM-DD", count}] (date 昇順) を返す。
  //
  // toISOString は使わない (UTC 日付に戻ってしまうため、DST / JST 23h UTC 等で誤集約する)。
  // key は getFullYear / getMonth / getDate を手組みで連結する。
  function localDailyFromHourly(buckets) {
    const counter = new Map();
    const list = Array.isArray(buckets) ? buckets : [];
    for (const b of list) {
      if (!b || !b.hour_utc) continue;
      const dt = new Date(b.hour_utc);
      if (isNaN(dt.getTime())) continue;
      const key = dt.getFullYear() + '-' + pad(dt.getMonth()+1, 2) + '-' + pad(dt.getDate(), 2);
      const inc = Number(b.count) || 0;
      counter.set(key, (counter.get(key) || 0) + inc);
    }
    return [...counter.entries()]
      .sort((a, b) => a[0] < b[0] ? -1 : (a[0] > b[0] ? 1 : 0))
      .map(([date, count]) => ({ date, count }));
  }

  // ============================================================
  //  Live connection badge (Phase B)
  // ============================================================
  const STATUS_LABEL = {
    online:    '● 接続中',
    reconnect: '○ 再接続中',
    offline:   '× 停止中',
    static:    '— 静的レポート',
  };
  function setConnStatus(state) {
    const el = document.getElementById('connStatus');
    if (!el) return;
    el.dataset.state = state;
    el.textContent = STATUS_LABEL[state] || '';
  }

