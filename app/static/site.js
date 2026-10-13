// Shared by every page: the Steam login button in the top bar and the window that says, before going to
// Steam, exactly what the site gets. Put <span id="account"></span> in the page's top bar.
(function () {
  const css = `
  .acc { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; }
  .acc img { width: 22px; height: 22px; border-radius: 50%; }
  .acc a { color: var(--text); }
  .acc button, .lg-actions button { border: 0; border-radius: 999px; padding: 6px 12px; font: inherit; cursor: pointer; }
  .acc .in { background: var(--pink); color: #fff; }
  .acc .out { background: var(--row); color: var(--text); }
  .lg-back { position: fixed; inset: 0; background: rgba(20, 10, 35, .55); display: flex; align-items: center; justify-content: center; padding: 16px; z-index: 50; }
  .lg-box { background: var(--card); color: var(--text); border-radius: 16px; max-width: 460px; width: 100%; padding: 18px 20px; box-shadow: 0 10px 40px rgba(40, 10, 70, .35); }
  .lg-box h2 { margin: 0 0 10px; font-size: 18px; }
  .lg-box ul { margin: 0 0 12px; padding-left: 18px; }
  .lg-box li { margin: 4px 0; font-size: 14px; }
  .lg-box .no { color: var(--muted); font-size: 13px; }
  .lg-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
  .lg-actions .go { background: var(--pink); color: #fff; }
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
      if (me.admin) { const ad = el("a", null, "API"); ad.href = "/api/docs"; slot.appendChild(ad); }
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
