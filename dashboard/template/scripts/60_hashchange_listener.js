  // hashchange → loadAndRender 再実行 (Issue #58 Q2)。router IIFE が先に
  // body.dataset.activePage を更新し、main IIFE の本リスナーが loadAndRender を
  // 呼び直すことで、page navigate 直後の page-scoped widget が即時描画される。
  // scheduleLoadAndRender 経由で SSE refresh と並行発火しても直列化される
  // (stale-snapshot race 対策 / 25_live_diff.js を参照)。
  window.addEventListener('hashchange', () => {
    scheduleLoadAndRender().catch((err) => console.error('route change render 失敗', err));
  });

