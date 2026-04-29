// ============================================================
//  Hash router (Issue #57) — loadAndRender とは独立した IIFE。
//  副作用は (1) `<section data-page="X">` の hidden 切替、
//  (2) `<a data-page-link>` の active class / aria-current、
//  (3) `body[data-active-page]` 属性 (後続 PR の page-scoped early-out 用)。
//  SSE refresh 経路と独立しているため、Overview 以外のページ表示中も
//  loadAndRender() は走り続け、戻ってきたときに最新データが見える。
// ============================================================
(function(){
  const HASH_TO_PAGE = {
    '': 'overview', '#': 'overview', '#/': 'overview',
    '#/patterns': 'patterns',
    '#/quality': 'quality',
    '#/surface': 'surface',
  };
  function applyRoute(rawHash) {
    // 未知 hash (`#/foo`, `#bar`, percent-encoded など) は overview に倒す
    const page = HASH_TO_PAGE[rawHash] || 'overview';
    document.querySelectorAll('.page').forEach(el => {
      el.hidden = (el.dataset.page !== page);
    });
    document.querySelectorAll('[data-page-link]').forEach(a => {
      const isActive = a.dataset.pageLink === page;
      a.classList.toggle('active', isActive);
      a.setAttribute('aria-current', isActive ? 'page' : 'false');
    });
    // 後続 PR (#58〜#62) はこの属性を読んで page-scoped early-out:
    //   if (document.body.dataset.activePage !== 'patterns') return;
    document.body.dataset.activePage = page;
  }
  window.addEventListener('hashchange', function(){ applyRoute(location.hash); });
  applyRoute(location.hash);
})();
