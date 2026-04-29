  // ============================================================
  //  Help popover behavior
  // ============================================================
  function closeAllPops(except) {
    document.querySelectorAll('.help-pop[data-open="true"]').forEach(pop => {
      if (pop === except) return;
      pop.removeAttribute('data-open');
      const btn = document.querySelector('button[data-help-id="' + pop.id + '"]');
      if (btn) btn.setAttribute('aria-expanded', 'false');
    });
  }

  function placePop(pop, btn) {
    pop.setAttribute('data-place', 'right');
    const prevOpen = pop.getAttribute('data-open');
    pop.setAttribute('data-open', 'true');
    const rect = pop.getBoundingClientRect();
    const vw = window.innerWidth;
    if (rect.right > vw - 8) {
      pop.setAttribute('data-place', 'left');
    }
    if (!prevOpen) pop.removeAttribute('data-open');
  }

  // 全 popup に対して placePop を呼んで data-place を再計算する。
  // hover 表示は CSS `:hover` が直接 visibility を切り替えるため click 経路の
  // placePop が呼ばれず、右端 KPI (PERMISSION GATE) の tooltip が viewport を
  // 飛び出して横スクロールを誘発していた (Issue #41)。dynamic re-render 後と
  // resize 後にこれを呼んで、hover 表示前に正しい data-place を確定させる。
  function placeAllPops() {
    document.querySelectorAll('.help-pop').forEach(pop => {
      const btn = document.querySelector('button[data-help-id="' + pop.id + '"]');
      if (btn) placePop(pop, btn);
    });
  }

  document.addEventListener('click', function(e) {
    const btn = e.target.closest('.help-btn');
    if (btn) {
      e.preventDefault();
      e.stopPropagation();
      const popId = btn.getAttribute('data-help-id');
      const pop = document.getElementById(popId);
      const isOpen = pop.getAttribute('data-open') === 'true';
      closeAllPops(isOpen ? null : pop);
      if (isOpen) {
        pop.removeAttribute('data-open');
        btn.setAttribute('aria-expanded', 'false');
      } else {
        placePop(pop, btn);
        pop.setAttribute('data-open', 'true');
        btn.setAttribute('aria-expanded', 'true');
      }
      return;
    }
    if (!e.target.closest('.help-host')) {
      closeAllPops(null);
    }
  });

  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      const opened = document.querySelector('.help-pop[data-open="true"]');
      if (opened) {
        const btn = document.querySelector('button[data-help-id="' + opened.id + '"]');
        closeAllPops(null);
        if (btn) btn.focus();
      }
    }
  });

  window.addEventListener('resize', placeAllPops);

