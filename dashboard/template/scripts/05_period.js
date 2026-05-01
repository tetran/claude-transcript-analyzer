  // Issue #85: Dashboard period toggle (Overview / Patterns 限定).
  //
  // closure-private state は wrapping IIFE 直下に置く。`__period` prefix で名前空間
  // 隔離 (25_live_diff.js の `__live*` 慣習踏襲)。
  //
  // 注意: 05_period.js は concat order 05 で評価される → IIFE 評価時点で
  // `window.__liveDiff` (concat order 25) は **未定義**。click handler 内では
  // `window.__liveDiff?.scheduleLoadAndRender?.()` の **property lookup を呼び出し時に
  // 毎回行う** 形で書く (concat order 上位の依存先は call-time lookup する rule)。
  let __periodCurrent = "all";
  const __PERIOD_VALUES = ["7d", "30d", "90d", "all"];

  function getCurrentPeriod() {
    return __periodCurrent;
  }

  function setCurrentPeriod(p) {
    if (typeof p === "string" && __PERIOD_VALUES.indexOf(p) !== -1) {
      __periodCurrent = p;
    }
  }

  function wirePeriodToggle() {
    if (typeof document === "undefined") return;
    // 静的 export 経路 (window.__DATA__ 既存) では toggle UI を非表示にして click bind を skip。
    // server を経由しないので period 切り替え自体に意味がない。
    if (typeof window !== "undefined" && typeof window.__DATA__ !== "undefined") {
      const el = document.getElementById("periodToggle");
      if (el && typeof el.setAttribute === "function") {
        el.setAttribute("hidden", "");
      }
      return;
    }
    const buttons = document.querySelectorAll('#periodToggle button[data-period]');
    if (!buttons || typeof buttons.length !== "number" || buttons.length === 0) return;
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function (ev) {
        const target = ev && ev.currentTarget ? ev.currentTarget : btn;
        const period = target && target.dataset ? target.dataset.period : null;
        if (!period || __PERIOD_VALUES.indexOf(period) === -1) return;
        setCurrentPeriod(period);
        // aria-pressed の付け替え (active 表現)
        buttons.forEach(function (other) {
          if (typeof other.setAttribute === "function") {
            other.setAttribute("aria-pressed", other === target ? "true" : "false");
          }
        });
        // call-time lookup: 05_period.js 評価時に __liveDiff 未定義のため。
        // optional chaining で no-op safe (IIFE 評価中の captured 参照を取らない)。
        if (typeof window !== "undefined" && window.__liveDiff && typeof window.__liveDiff.scheduleLoadAndRender === "function") {
          window.__liveDiff.scheduleLoadAndRender();
        }
      });
    });
  }

  if (typeof window !== "undefined") {
    window.__period = {
      getCurrentPeriod: getCurrentPeriod,
      setCurrentPeriod: setCurrentPeriod,
      wirePeriodToggle: wirePeriodToggle,
    };
  }

  // shell.html の DOM が読み込まれた後に wire する。70_init_eventsource.js の
  // 初回 scheduleLoadAndRender() より早く動かす必要があるが、wrapping IIFE 内
  // は同期評価で進むので 70 番までに wirePeriodToggle が呼ばれていれば OK。
  wirePeriodToggle();

