// Shared by every page: the Steam login button in the top bar and the window that says, before going to
// Steam, exactly what the site gets. Put <span id="account"></span> in the page's top bar.
(function () {
  const css = `
  .acc { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; }
  .acc img { width: 22px; height: 22px; border-radius: 50%; }
  .acc a { color: var(--text); }
  .acc button, .lg-actions button { border: 0; border-radius: 999px; padding: 6px 12px; font: inherit; cursor: pointer; }
  .acc .in { background: #c2307f; color: #fff; font-weight: 600; }
  .acc .out { background: var(--row); color: var(--text); }
  .lg-back { position: fixed; inset: 0; background: rgba(20, 10, 35, .55); display: flex; align-items: center; justify-content: center; padding: 16px; z-index: 50; }
  .lg-box { background: var(--card); color: var(--text); border-radius: 16px; max-width: 460px; width: 100%; padding: 18px 20px; box-shadow: 0 10px 40px rgba(40, 10, 70, .35); }
  .lg-box h2 { margin: 0 0 10px; font-size: 18px; }
  .lg-box ul { margin: 0 0 12px; padding-left: 18px; }
  .lg-box li { margin: 4px 0; font-size: 14px; }
  .lg-box .no { color: var(--muted); font-size: 13px; }
  .lg-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
  .lg-actions .go { background: #c2307f; color: #fff; font-weight: 600; }
  .lg-actions .cancel { background: var(--row); color: var(--text); }`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }

  // the window before the login: what the site gets and keeps, in plain words
  window.mlpLogin = function (next) {
    const back = el("div", "lg-back"), box = el("div", "lg-box");
    back.setAttribute("role", "dialog"); back.setAttribute("aria-modal", "true");
    box.appendChild(el("h2", null, "Вход через Steam"));
    const ul = el("ul");
    [
      "Вход проходит на сайте Steam (steamcommunity.com). Пароль вы вводите только там — нам он не виден.",
      "Steam сообщает нам только ваш SteamID64 — публичный номер профиля (тот, что в ссылке на профиль).",
      "С публичной страницы профиля мы берём ник и аватар, чтобы показывать вас автором песен.",
      "Храним: SteamID64, ник, аватар, ваши песни и время входа. Вход запоминается в cookie этого сайта на 30 дней.",
    ].forEach(t => ul.appendChild(el("li", null, t)));
    box.appendChild(ul);
    box.appendChild(el("div", "no", "Не получаем и не храним: пароль, почту, телефон, друзей, инвентарь, список игр. Ничего не публикуем от вашего имени. Выйти можно в любой момент кнопкой «Выйти»."));
    const act = el("div", "lg-actions");
    const cancel = el("button", "cancel", "Отмена"), go = el("button", "go", "Войти через Steam");
    cancel.onclick = () => back.remove();
    go.onclick = () => { location.href = "/auth/steam/login?next=" + encodeURIComponent(next || location.pathname + location.search + location.hash); };
    act.appendChild(cancel); act.appendChild(go); box.appendChild(act);
    back.appendChild(box);
    back.addEventListener("click", e => { if (e.target === back) back.remove(); });
    document.addEventListener("keydown", function esc(e) { if (e.key === "Escape") { back.remove(); document.removeEventListener("keydown", esc); } });
    document.body.appendChild(back);
    go.focus();
  };

  window.mlpMe = null;
  async function account() {
    const slot = document.getElementById("account");
    let me = null;
    try { const r = await fetch("/api/v1/me", { credentials: "same-origin" }); if (r.ok) me = await r.json(); } catch (e) {}
    window.mlpMe = me && me.logged_in ? me : null;
    document.dispatchEvent(new CustomEvent("mlp-me", { detail: window.mlpMe }));
    if (!slot) return;
    slot.textContent = ""; slot.className = "acc";
    if (window.mlpMe) {
      if (me.avatar) { const img = el("img"); img.src = me.avatar; img.alt = ""; slot.appendChild(img); }
      const a = el("a", null, me.nick || me.steamid); a.href = me.profile; a.target = "_blank"; a.rel = "noopener"; slot.appendChild(a);
      if (me.admin) { const ad = el("a", null, "админка"); ad.href = "/admin"; slot.appendChild(ad); }
      const out = el("button", "out", "Выйти");
      out.onclick = async () => { await fetch("/auth/logout", { method: "POST", credentials: "same-origin" }); location.reload(); };
      slot.appendChild(out);
    } else {
      const b = el("button", "in", "Войти через Steam"); b.onclick = () => window.mlpLogin(); slot.appendChild(b);
    }
    if (new URLSearchParams(location.search).get("login") === "failed") slot.appendChild(el("span", "no", " вход не удался"));
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", account); else account();
})();

// ------------------------------------------------------------------------------------------------ the theme switch
// The first visit follows the system; a click picks light / dark and is kept (if "remember settings" is allowed).
(function () {
  const css = `
  .th { border: 0; border-radius: 999px; width: 32px; height: 28px; padding: 0; font-size: 16px; line-height: 28px; cursor: pointer;
        background: var(--row); color: var(--text); margin-right: 8px; vertical-align: middle; }
  .th:hover { outline: 2px solid var(--line); }
  .th.lang { width: auto; padding: 0 9px; font-size: 12px; font-weight: 700; letter-spacing: .03em; }
  .top-right { display: inline-flex; align-items: center; }`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
  function label(b) {
    const dark = window.mlpTheme() === "dark";
    b.textContent = dark ? "☀️" : "🌙";
    b.title = (dark ? "Светлая тема" : "Тёмная тема") + (window.mlpConsent().prefs ? "" : " (не запомнится: сохранение настроек выключено в «Cookie»)");
    b.setAttribute("aria-label", b.title);
  }
  function setup() {
    const slot = document.getElementById("account");
    const b = document.createElement("button"); b.className = "th"; b.type = "button";
    b.onclick = () => { window.mlpSetTheme(window.mlpTheme() === "dark" ? "light" : "dark"); };
    document.addEventListener("mlp-theme", () => label(b)); document.addEventListener("mlp-consent", () => label(b));
    label(b);
    // the language: the button shows the other one
    const lb = document.createElement("button"); lb.className = "th lang"; lb.type = "button";
    const llabel = () => {
      const ru = window.mlpLang() === "ru";
      lb.textContent = ru ? "EN" : "RU";
      lb.title = (ru ? "Язык: English" : "Язык: русский") + (window.mlpConsent().prefs ? "" : " (не запомнится: сохранение настроек выключено в «Cookie»)");
      lb.setAttribute("aria-label", lb.title);
    };
    lb.onclick = () => window.mlpSetLang(window.mlpLang() === "ru" ? "en" : "ru");
    document.addEventListener("mlp-lang", () => { llabel(); label(b); }); document.addEventListener("mlp-consent", llabel);
    llabel();
    if (slot) { const w = document.createElement("span"); w.className = "top-right"; slot.parentNode.insertBefore(w, slot); w.append(lb, b, slot); }
    else document.body.append(lb, b);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup); else setup();
})();

// ------------------------------------------------------------------------------------------------ the cookie window
// Two things only, each with what's lost without it: the login cookie and remembering settings in the browser.
// No ads, no analytics, no third-party trackers. The choice is kept in the browser; «Cookie» at the bottom reopens it.
(function () {
  const css = `
  .ck { position: fixed; left: 50%; bottom: 16px; transform: translateX(-50%); width: min(640px, calc(100vw - 32px)); z-index: 60;
        background: var(--card); color: var(--text); border-radius: 16px; padding: 16px 18px; box-shadow: 0 10px 40px rgba(40, 10, 70, .35);
        border: 1px solid var(--line); max-height: calc(100vh - 32px); overflow: auto; }
  .ck h2 { margin: 0 0 6px; font-size: 17px; }
  .ck p { margin: 6px 0; font-size: 14px; }
  .ck .muted { color: var(--muted); font-size: 13px; }
  .ck .opt { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; padding: 10px 0; border-top: 1px solid var(--line); }
  .ck .opt input { width: 18px; height: 18px; margin: 2px 0 0; accent-color: #c2307f; grid-row: span 3; }
  .ck .opt b { font-size: 14px; }
  .ck .opt .loss { font-size: 13px; color: #a3381a; }
  :root[data-theme="dark"] .ck .opt .loss { color: #ffb08a; }
  .ck ul { margin: 6px 0; padding-left: 20px; font-size: 14px; }
  .ck ul li { margin: 3px 0; }
  .ck .acts { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; margin-top: 12px; }
  .ck .acts button { border: 0; border-radius: 999px; padding: 8px 14px; font: inherit; font-size: 14px; cursor: pointer; background: var(--row); color: var(--text); }
  .ck .acts .yes { background: #c2307f; color: #fff; font-weight: 600; }
  .ck .acts .no { background: transparent; color: var(--text); text-decoration: underline; }
  .ck-foot { text-align: center; padding: 10px 16px 18px; font-size: 12px; color: var(--muted); }
  .ck-foot button { border: 0; background: none; color: var(--muted); text-decoration: underline; font: inherit; cursor: pointer; padding: 0; }`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
  const KEYS = ["mlpTheme", "mlpLang", "mlpEdKeyMode", "lf-mode:ban_words"];   // what "remember settings" keeps
  const LOSS_LOGIN = "Не получится войти через Steam: не будет «Мои песни», загрузки и редактора песен, «Моей статистики». Если вы уже вошли — выйдете.";
  const LOSS_PREFS = "Язык, тема, настройки редактора и вид списков не запомнятся: при каждом открытии — снова как в системе и по умолчанию.";
  const LOSS_LOGIN_ROW = "Без него: не получится войти через Steam — не будет «Мои песни», загрузки и редактора песен, «Моей статистики». Если вы уже вошли — выйдете.";
  const LOSS_PREFS_ROW = "Без этого: язык, тема, настройки редактора и вид списков не запомнятся — при каждом открытии снова как в системе и по умолчанию.";

  async function save(login, prefs) {
    const was = window.mlpConsent();
    try { localStorage.setItem("mlpConsent", JSON.stringify({ v: 1, login, prefs, at: Date.now() })); } catch (e) {}
    if (prefs) window.mlpKeepTheme();
    else KEYS.forEach(k => { try { localStorage.removeItem(k); } catch (e) {} });
    document.dispatchEvent(new CustomEvent("mlp-consent", { detail: { login, prefs } }));
    if (!login && (window.mlpMe || was.login)) {      // the login cookie is no longer allowed: drop it
      try { await fetch("/auth/logout", { method: "POST", credentials: "same-origin" }); } catch (e) {}
      if (window.mlpMe) location.reload();
    }
  }

  let open = null;
  // mode: "first" (the first visit), "settings" (the link), "login" (asked by the login button; then = go on to Steam)
  window.mlpCookies = function (mode, then) {
    if (open) open.remove();
    const c = window.mlpConsent(), box = el("div", "ck");
    box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Cookie и настройки");
    open = box;
    const close = () => { box.remove(); if (open === box) open = null; };
    function main() {
      box.textContent = "";
      box.appendChild(el("h2", null, mode === "login" ? "Для входа нужен cookie" : "Cookie и настройки"));
      box.appendChild(el("p", null, mode === "login"
        ? "Вход через Steam запоминается в cookie этого сайта. Без него войти нельзя. Разрешите его — и продолжим вход."
        : "Здесь нет рекламы, аналитики и чужих трекеров. Используется только то, без чего не работают функции сайта:"));
      const opts = [
        { id: "login", on: mode === "login" ? true : (c.decided ? c.login : true), title: "Вход через Steam",
          what: "Cookie mlp_session на 30 дней — помнит, что вы вошли. Только для этого сайта.", loss: LOSS_LOGIN_ROW },
        { id: "prefs", on: c.decided ? c.prefs : true, title: "Запоминать настройки",
          what: "Память браузера (localStorage): язык, тема, настройки редактора нот (режим, громкость, высота дорожки), вид списков. Никуда не отправляется.", loss: LOSS_PREFS_ROW },
      ];
      const boxes = {};
      opts.forEach(o => {
        const row = el("label", "opt"), cb = el("input"); cb.type = "checkbox"; cb.checked = o.on; boxes[o.id] = cb;
        row.append(cb, el("b", null, o.title), el("span", "muted", o.what), el("span", "loss", o.loss));
        box.appendChild(row);
      });
      box.appendChild(el("p", "muted", "Рекорды и список песен открыты всем и работают без cookie. Передумать можно в любой момент — «Cookie и настройки» внизу страницы."));
      const acts = el("div", "acts");
      const no = el("button", "no", "Отказаться от всего"), pickBtn = el("button", null, "Сохранить выбор"), all = el("button", "yes", mode === "login" ? "Разрешить и войти" : "Принять все");
      no.onclick = () => confirmNo();
      pickBtn.onclick = async () => {
        const l = boxes.login.checked, p = boxes.prefs.checked;
        if (!l || !p) return confirmNo(l, p);
        await save(true, true); close(); if (then) then();
      };
      all.onclick = async () => { await save(true, mode === "login" ? boxes.prefs.checked : true); close(); if (then) then(); };
      if (mode !== "first") { const x = el("button", null, "Закрыть"); x.onclick = close; acts.appendChild(x); }
      acts.append(no, pickBtn, all);
      box.appendChild(acts);
      all.focus();
    }
    // the step before refusing: exactly what goes away
    function confirmNo(login = false, prefs = false) {
      box.textContent = "";
      box.appendChild(el("h2", null, login || prefs ? "Без части cookie вы потеряете" : "Без cookie вы потеряете"));
      const ul = el("ul");
      if (!login) ul.appendChild(el("li", null, LOSS_LOGIN));
      if (!prefs) ul.appendChild(el("li", null, LOSS_PREFS));
      box.appendChild(ul);
      box.appendChild(el("p", "muted", "Останется: рекорды, профили игроков, список песен и их прослушивание. Ваш выбор запомнится, чтобы не спрашивать снова."));
      const acts = el("div", "acts");
      const back = el("button", null, "Назад"), sure = el("button", "yes", "Всё равно отказаться");
      back.onclick = main;
      sure.onclick = async () => { await save(login, prefs); close(); if (then && login) then(); };
      acts.append(back, sure); box.appendChild(acts); back.focus();
    }
    main();
    document.body.appendChild(box);
  };

  // the login button asks for the login cookie first
  const login = window.mlpLogin;
  window.mlpLogin = function (next) {
    if (window.mlpConsent().login) return login(next);
    window.mlpCookies("login", () => login(next));
  };

  function setup() {
    const f = el("div", "ck-foot"), b = el("button", null, "Cookie и настройки");
    b.onclick = () => window.mlpCookies("settings"); f.appendChild(b);
    const main = document.querySelector("main");
    (main && main.parentNode ? main.parentNode : document.body).insertBefore(f, main ? main.nextSibling : null);
    const c = window.mlpConsent();
    if (!c.decided) window.mlpCookies("first");
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup); else setup();
  // logged in from before the window existed, and the login cookie was refused since: drop it
  document.addEventListener("mlp-me", e => { const c = window.mlpConsent(); if (e.detail && c.decided && !c.login) save(false, c.prefs); });
})();
