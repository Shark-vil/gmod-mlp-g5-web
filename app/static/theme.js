// Loaded in <head> of every page, before it's drawn: the theme (no flash of the wrong one) and the consent.
//   window.mlpConsent()          -> { decided, login, prefs }  (what the visitor allowed in the cookie window)
//   window.mlpStore.get / set    -> localStorage, only when "prefs" are allowed (else nothing is kept)
//   window.mlpTheme()            -> "light" | "dark";  window.mlpSetTheme("light" | "dark")
// The visitor's own choice of the cookie window is the one thing kept without asking: it's needed to not ask again.
(function () {
  const CONSENT = "mlpConsent", THEME = "mlpTheme";
  function raw(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  window.mlpConsent = function () {
    try { const c = JSON.parse(raw(CONSENT) || "null"); if (c && c.v === 1) return { decided: true, login: !!c.login, prefs: !!c.prefs }; } catch (e) {}
    return { decided: false, login: false, prefs: false };
  };
  window.mlpStore = {
    get(k) { return window.mlpConsent().prefs ? raw(k) : null; },
    set(k, v) { if (!window.mlpConsent().prefs) return false; try { localStorage.setItem(k, v); return true; } catch (e) { return false; } },
  };
  const sys = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  let chosen = null;                                   // a choice made on this page (kept here if storage isn't allowed)
  function current() {
    const t = chosen || window.mlpStore.get(THEME);
    return t === "dark" || t === "light" ? t : (sys && sys.matches ? "dark" : "light");
  }
  function apply() {
    const t = current(), root = document.documentElement;
    if (root.dataset.theme !== t) { root.dataset.theme = t; document.dispatchEvent(new CustomEvent("mlp-theme", { detail: t })); }
  }
  window.mlpTheme = current;
  window.mlpSetTheme = function (t) { chosen = t; window.mlpStore.set(THEME, t); apply(); };
  window.mlpThemeSaved = function () { return !!window.mlpStore.get(THEME); };
  window.mlpKeepTheme = function () { if (chosen) window.mlpStore.set(THEME, chosen); };   // after "prefs" got allowed
  if (sys && sys.addEventListener) sys.addEventListener("change", apply);     // no own choice: follow the system
  // the consent changed (e.g. settings no longer kept): this page keeps the theme it shows until it's closed
  document.addEventListener("mlp-consent", () => { if (!chosen && !window.mlpConsent().prefs) chosen = document.documentElement.dataset.theme; apply(); });
  apply();
})();
