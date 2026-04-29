  function esc(s){ return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]); }
  function fmtN(n){ return Number(n).toLocaleString('en-US'); }
  function pad(s,n){ s=String(s); return s.length>=n?s:('0'.repeat(n-s.length)+s); }

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

