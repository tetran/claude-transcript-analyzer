  // hashchange → loadAndRender 再実行 (Issue #58 Q2)。router IIFE が先に
  // body.dataset.activePage を更新し、main IIFE の本リスナーが loadAndRender を
  // 呼び直すことで、page navigate 直後の page-scoped widget が即時描画される。
  window.addEventListener('hashchange', () => {
    loadAndRender().catch((err) => console.error('route change render 失敗', err));
  });

