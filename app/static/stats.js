// The stats catalog (the same as the mod's lua/mlp_g5_lib/sh_statcats.lua), formatting, a player's profile
// and its charts — shared by the leaderboard page and "Моя статистика".
(function () {
const hours = v => (v / 3600).toFixed(1) + " ч";
const secs = v => { const m = Math.floor(v / 60), s = (v - m * 60).toFixed(2).padStart(5, "0"); return m + ":" + s; };
const stat = (key, label, fmt) => ({ key, label, fmt, url: "/api/v1/leaderboard?stat=" + key });
const CATS = [
  { label: "Общее", stats: [
    stat("stars", "Звёзды за всё время"),
    { key: "items:vanity", label: "Коллекция", url: "/api/v1/items/leaderboard?kind=vanity" },
    { key: "items:spell", label: "Заклинания", url: "/api/v1/items/leaderboard?kind=spell" },
    stat("playtime", "Время в игре", hours),
  ] },
  { label: "Танец", stats: [
    stat("best_dance", "Лучший счёт"), stat("wins_dance", "Победы"), stat("medal_dance_gold", "Золотые медали"), stat("games_dance", "Сыграно"),
  ] },
  { label: "Гонка в небе", stats: [ stat("time_skyrace", "Лучшее время", secs), stat("wins_skyrace", "Победы"), stat("games_skyrace", "Сыграно") ] },
  { label: "Гонка на роликах", stats: [ stat("time_groundrace", "Лучшее время", secs), stat("wins_groundrace", "Победы"), stat("games_groundrace", "Сыграно") ] },
  { label: "Таран", stats: [ stat("best_rampage", "Лучший счёт"), stat("wins_rampage", "Победы"), stat("rammed_npcs", "Сбито пони-тараном"), stat("games_rampage", "Сыграно") ] },
  { label: "Против игроков", stats: [
    stat("pvpwins", "Победы, всего"), stat("pvpwins_dance", "Победы в танце"), stat("pvpwins_skyrace", "Победы в небе"), stat("pvpwins_groundrace", "Победы на роликах"), stat("pvpwins_rampage", "Победы в таране"),
  ] },
  { label: "Против ботов", stats: [
    stat("botwins", "Победы, всего"), stat("botwins_master", "Над мастером"), stat("botwins_skilled", "Над опытным"),
    stat("botwins_casual", "Над любителем"), stat("botwins_rookie", "Над новичком"),
  ] },
];
const PONIES = { sunny: "Санни", izzy: "Иззи", hitch: "Хитч", pipp: "Пипп", zipp: "Зипп" };
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }
function fmt(st, v) { return st.fmt ? st.fmt(v) : Math.round(v).toLocaleString("ru-RU"); }

// ---- charts in a profile: play time per day (columns) and magic bits (line), the last 30 days.
// One series each (the title names it, no legend); hover / focus shows the day's value.
const DAYS = 30;
function lastDays() {
  const out = [];
  for (let i = DAYS - 1; i >= 0; i--) out.push(new Date(Date.now() - i * 86400000).toISOString().slice(0, 10));
  return out;
}
// snapshots [[day, total]] -> the total at the end of each day (carried forward)
function carry(rows, days) {
  const m = new Map(rows);
  let last = null;
  const before = rows.filter(r => r[0] < days[0]);
  if (before.length) last = before[before.length - 1][1];
  return days.map(d => { if (m.has(d)) last = m.get(d); return last; });
}
function niceTop(v) {
  if (v <= 0) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(v)));
  for (const k of [1, 2, 2.5, 5, 10]) if (v <= k * e) return k * e;
  return 10 * e;
}
const SVGNS = "http://www.w3.org/2000/svg";
function el(tag, attrs, parent) {
  const n = document.createElementNS(SVGNS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(n);
  return n;
}
function drawChart(box, title, kind, values, days, fmtV) {
  const card = document.createElement("div"); card.className = "chart";
  const t = document.createElement("div"); t.className = "t"; t.textContent = title; card.appendChild(t);
  const W = 320, H = 130, L = 34, R = 8, T = 6, B = 18, pw = W - L - R, ph = H - T - B;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title }, null);
  card.appendChild(svg);
  const tip = document.createElement("div"); tip.className = "tip"; card.appendChild(tip);
  const top = niceTop(Math.max(0, ...values.map(v => v || 0)));
  for (const f of [0, 0.5, 1]) {
    const y = T + ph - ph * f;
    el("line", { x1: L, x2: L + pw, y1: y, y2: y, stroke: "var(--grid)", "stroke-width": 1 }, svg);
    el("text", { x: L - 4, y: y + 3, "text-anchor": "end" }, svg).textContent = fmtV(top * f, true);
  }
  const n = values.length, slot = pw / n;
  const X = i => L + slot * (i + 0.5), Y = v => T + ph - ph * ((v || 0) / top);
  for (const i of [0, Math.floor(n / 2), n - 1]) {
    el("text", { x: X(i), y: H - 4, "text-anchor": "middle" }, svg).textContent = days[i].slice(8, 10) + "." + days[i].slice(5, 7);
  }
  const marks = [];
  if (kind === "columns") {
    const bw = Math.min(10, slot - 2);
    values.forEach((v, i) => {
      const h = Math.max(0, T + ph - Y(v));
      if (h < 0.5) { marks.push(null); return; }
      const r = Math.min(3, h / 2, bw / 2), x = X(i) - bw / 2, y = Y(v);
      // a rounded top, square at the baseline
      marks.push(el("path", { d: `M${x},${T + ph} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${T + ph} Z`, fill: "var(--mark)" }, svg));
    });
  } else {
    const pts = values.map((v, i) => v === null ? null : `${X(i)},${Y(v)}`).filter(Boolean);
    if (pts.length > 1) el("polyline", { points: pts.join(" "), fill: "none", stroke: "var(--mark)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    const li = values.length - 1;
    if (values[li] !== null) el("circle", { cx: X(li), cy: Y(values[li]), r: 4, fill: "var(--mark)", stroke: "var(--card)", "stroke-width": 2 }, svg);
  }
  const cross = el("line", { y1: T, y2: T + ph, stroke: "var(--muted)", "stroke-width": 1, visibility: "hidden" }, svg);
  const hot = el("circle", { r: 4, fill: "var(--mark)", stroke: "var(--card)", "stroke-width": 2, visibility: "hidden" }, svg);
  // the whole plot is the hit area: the nearest day
  const hit = el("rect", { x: L, y: T - 4, width: pw, height: ph + 8, fill: "transparent", tabindex: 0 }, svg);
  let cur = -1;
  function show(i) {
    if (cur >= 0 && marks[cur]) marks[cur].setAttribute("fill", "var(--mark)");
    cur = i;
    const v = values[i];
    if (kind === "columns" && marks[i]) marks[i].setAttribute("fill", "var(--mark-hover)");
    if (kind === "line") {
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("visibility", "visible");
      if (v !== null) { hot.setAttribute("cx", X(i)); hot.setAttribute("cy", Y(v)); hot.setAttribute("visibility", "visible"); }
    }
    tip.textContent = "";
    const b = document.createElement("b"); b.textContent = v === null ? "—" : fmtV(v); tip.appendChild(b);
    tip.appendChild(document.createTextNode(days[i].slice(8, 10) + "." + days[i].slice(5, 7)));
    const r = svg.getBoundingClientRect(), k = r.width / W;
    tip.style.left = (8 + X(i) * k) + "px";
    tip.style.top = (28 + Y(v) * k - 6) + "px";
    tip.style.display = "block";
  }
  function hide() {
    if (cur >= 0 && marks[cur]) marks[cur].setAttribute("fill", "var(--mark)");
    cur = -1; tip.style.display = "none"; cross.setAttribute("visibility", "hidden"); hot.setAttribute("visibility", "hidden");
  }
  hit.addEventListener("pointermove", e => {
    const r = svg.getBoundingClientRect(), x = (e.clientX - r.left) * W / r.width;
    show(Math.max(0, Math.min(n - 1, Math.floor((x - L) / slot))));
  });
  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("focus", () => show(n - 1));
  hit.addEventListener("blur", hide);
  hit.addEventListener("keydown", e => {
    if (e.key === "ArrowLeft") show(Math.max(0, cur - 1));
    if (e.key === "ArrowRight") show(Math.min(n - 1, cur + 1));
  });
  box.appendChild(card);
}
async function charts(sid, box) {
  try {
    const h = await (await fetch("/api/v1/history/" + encodeURIComponent(sid) + "?days=" + (DAYS + 7))).json();
    if (!h.series || !box.isConnected) return;
    const days = lastDays();
    const stars = carry(h.series.stars || [], days);
    const play = carry(h.series.playtime || [], days);
    let prev = null;
    const perDay = play.map(v => { const d = v === null || prev === null ? 0 : Math.max(0, Math.round((v - prev) / 60)); if (v !== null) prev = v; return d; });
    if (!stars.some(v => v !== null) && !play.some(v => v !== null)) return;
    const wrap = document.createElement("div"); wrap.className = "charts";
    const h4 = document.createElement("h4"); h4.textContent = "За 30 дней";
    box.appendChild(h4); box.appendChild(wrap);
    drawChart(wrap, "Время в игре по дням, мин", "columns", perDay, days, (v, tick) => tick ? String(Math.round(v)) : Math.round(v) + " мин");
    drawChart(wrap, "Звёзды за всё время", "line", stars, days, v => Math.round(v).toLocaleString("ru-RU"));
  } catch (e) {}
}

// a player's whole profile into box: every known stat by category, the ponies played, the charts
async function renderProfile(sid, box, stillWanted) {
  stillWanted = stillWanted || (() => box.isConnected);
  try {
    const r = await fetch("/api/v1/player/" + encodeURIComponent(sid));
    if (!stillWanted()) return;
    if (r.status === 404) { box.innerHTML = '<div class="empty">Пока ничего</div>'; return null; } // never reported by a server
    const p = await r.json();
    const s = p.stats || {};
    const groups = CATS.map(c => {
      const items = c.stats.map(st => {
        const v = st.key.startsWith("items:") ? (p.items || {})[st.key.slice(6)] : s[st.key];
        return v === undefined || v === null ? "" : `<div><span>${esc(st.label)}</span><b>${esc(fmt(st, v))}</b></div>`;
      }).join("");
      return items ? `<h4>${esc(c.label)}</h4><div class="grid">${items}</div>` : "";
    }).join("");
    const ch = (p.characters || []).map(c => PONIES[c] || c).join(", ");
    box.innerHTML = (groups || '<div class="empty">Пока ничего</div>') + (ch ? `<h4>Играл(а) за</h4><div>${esc(ch)}</div>` : "");
    charts(sid, box);
    return p;
  } catch (e) { box.textContent = "Не получилось загрузить"; }
}

window.MLPStats = { CATS, PONIES, esc, fmt, renderProfile };
})();
