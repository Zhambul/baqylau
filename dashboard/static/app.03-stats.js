"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.12-init.js for the boot/init sequence.

function onStats() {
  return location.hash.replace(/^#\/?/, "").split("/")[0] === "stats";
}
function svgel(tag, attrs) {
  const n = document.createElementNS(SVGNS, tag);
  if (attrs) for (const k in attrs) n.setAttribute(k, attrs[k]);
  return n;
}
// local YYYY-MM-DD — MUST match the server's date(...,'localtime') keys
function fmtDay(dt) {
  return dt.getFullYear() + "-"
    + String(dt.getMonth() + 1).padStart(2, "0") + "-"
    + String(dt.getDate()).padStart(2, "0");
}
function kv(label, val, cls) {
  const s = el("span", "kv");
  s.append(el("span", "k", label), el("span", "v" + (cls ? " " + cls : ""), val));
  return s;
}

function showStats() {
  leaveSession();
  renderStats();                                 // instant paint from cache, if any
  fetch("/api/stats").then(r => r.json())
    .then(d => { S.stats = d; renderStats(); }).catch(() => {});
}

function renderStats() {
  if (!onStats()) return;                         // a late fetch resolved off-route
  $view.textContent = "";
  const wrap = el("div", "stats");
  const d = S.stats;
  if (!d) { wrap.append(el("div", "empty", "loading stats…")); $view.append(wrap); return; }
  if (!d.total_sessions) {
    wrap.append(el("div", "empty", "no sessions recorded yet"));
    $view.append(wrap); return;
  }
  wrap.append(statsHeader(d), pulseSection(d), heatSection(d),
              punchSection(d), projectsSection(d));
  $view.append(wrap);
}

function statsHeader(d) {
  const h = el("div", "statstop");
  h.append(el("h1", "statsh1", "Insights"));
  const sub = el("div", "statssub");
  sub.append(el("span", null, d.total_sessions + " sessions all-time"));
  if (d.generated_at) sub.append(el("span", "statsgen", "updated " + ago(d.generated_at)));
  h.append(sub);
  return h;
}

function pulseSection(d) {
  const sec = el("section", "stsec");
  const head = el("div", "sthead");
  head.append(el("h2", null, "Pulse"));
  const btns = el("div", "pulsebtns");
  [["7d", "7 days"], ["30d", "30 days"], ["all", "all time"]].forEach(([w, lbl]) => {
    const b = el("button", "pbtn" + (S.statsWindow === w ? " on" : ""), lbl);
    b.onclick = () => { S.statsWindow = w; renderStats(); };
    btns.append(b);
  });
  head.append(btns);
  sec.append(head);
  const win = (d.windows && d.windows[S.statsWindow]) || { sessions: 0 };
  const grid = el("div", "statgrid");
  const tile = (val, label, cls) => {
    const t = el("div", "sttile");
    t.append(el("div", "stval" + (cls ? " " + cls : ""), val), el("div", "stlbl", label));
    return t;
  };
  grid.append(tile(String(win.sessions || 0), "sessions"));
  grid.append(tile(String(win.active || 0), "active", win.active ? "pos" : ""));
  grid.append(tile(String(win.ended || 0), "ended"));
  grid.append(tile(kfmt(win.tokens || 0), "tokens", "gold"));
  grid.append(tile(usd(win.cost || 0), "cost", "cost"));
  if (win.errors) grid.append(tile(String(win.errors), "errors", "neg"));
  sec.append(grid);
  const tops = win.projects || [];
  if (tops.length) {
    const max = Math.max.apply(null, tops.map(p => p.sessions));
    const list = el("div", "pbars");
    tops.forEach(p => {
      const row = el("div", "pbrow");
      row.append(el("span", "pbname", p.name));
      const track = el("span", "pbtrack");
      const fill = el("span", "pbfill");
      fill.style.width = (max ? Math.max(4, p.sessions / max * 100) : 0) + "%";
      track.append(fill);
      row.append(track, el("span", "pbval", String(p.sessions)));
      list.append(row);
    });
    sec.append(list);
  }
  return sec;
}

function heatSection(d) {
  const sec = el("section", "stsec");
  sec.append(el("h2", null, "Contributions"));
  const counts = {};
  (d.daily || []).forEach(([day, n]) => { counts[day] = n; });
  // 5 self-normalized buckets: 0 + quartiles of the nonzero days (GitHub-style)
  const vals = (d.daily || []).map(x => x[1]).filter(n => n > 0).sort((a, b) => a - b);
  const q = p => vals.length ? vals[Math.min(vals.length - 1, Math.floor(p * vals.length))] : 0;
  const t1 = q(.25), t2 = q(.5), t3 = q(.75);
  const level = n => !n ? 0 : n <= t1 ? 1 : n <= t2 ? 2 : n <= t3 ? 3 : 4;
  const CELL = 12, GAP = 3, TOP = 16, LEFT = 26;
  const end = new Date(); end.setHours(0, 0, 0, 0);
  const cur = new Date(end);
  cur.setDate(cur.getDate() - 7 * 52 - end.getDay());   // 53 weeks back, Sunday-aligned
  const weeks = [];
  while (cur <= end) {
    const month = cur.getMonth();
    const days = [];
    for (let dow = 0; dow < 7; dow++) {
      const ds = fmtDay(cur);
      days.push(cur <= end ? { date: ds, n: counts[ds] || 0 } : null);
      cur.setDate(cur.getDate() + 1);
    }
    weeks.push({ days, month });
  }
  const W = LEFT + weeks.length * (CELL + GAP);
  const H = TOP + 7 * (CELL + GAP);
  const s = svgel("svg", { class: "heat", viewBox: "0 0 " + W + " " + H, width: W, height: H });
  let lastMonth = -1;
  weeks.forEach((wk, wi) => {
    if (wk.month !== lastMonth && wk.days[0]) {
      lastMonth = wk.month;
      const t = svgel("text", { x: LEFT + wi * (CELL + GAP), y: 11, class: "hmlabel" });
      t.textContent = MON[wk.month];
      s.append(t);
    }
  });
  [[1, "Mon"], [3, "Wed"], [5, "Fri"]].forEach(([r, txt]) => {
    const t = svgel("text", { x: 0, y: TOP + r * (CELL + GAP) + CELL - 2, class: "hmlabel" });
    t.textContent = txt;
    s.append(t);
  });
  weeks.forEach((wk, wi) => wk.days.forEach((c, dow) => {
    if (!c) return;
    const rect = svgel("rect", {
      x: LEFT + wi * (CELL + GAP), y: TOP + dow * (CELL + GAP),
      width: CELL, height: CELL, rx: 2, class: "hm l" + level(c.n) });
    const title = svgel("title");
    title.textContent = c.n + " session" + (c.n === 1 ? "" : "s") + " on " + c.date;
    rect.append(title);
    s.append(rect);
  }));
  const scroll = el("div", "heatscroll");
  scroll.append(s);
  sec.append(scroll);
  const leg = el("div", "hmlegend");
  leg.append(el("span", "hmleglbl", "less"));
  for (let l = 0; l <= 4; l++) {
    const b = svgel("svg", { width: CELL, height: CELL, class: "hmswatch" });
    b.append(svgel("rect", { width: CELL, height: CELL, rx: 2, class: "hm l" + l }));
    leg.append(b);
  }
  leg.append(el("span", "hmleglbl", "more"));
  sec.append(leg);
  return sec;
}

function punchSection(d) {
  const sec = el("section", "stsec");
  sec.append(el("h2", null, "When you work"));
  const grid = {};
  let max = 0;
  (d.punch || []).forEach(([dow, hr, n]) => { grid[dow + "_" + hr] = n; if (n > max) max = n; });
  const CELL = 20, LEFT = 34, TOP = 4, R = CELL / 2 - 2;
  const W = LEFT + 24 * CELL, H = TOP + 7 * CELL + 16;
  const s = svgel("svg", { class: "punch", viewBox: "0 0 " + W + " " + H, width: W, height: H });
  DOW.forEach((lbl, r) => {
    const t = svgel("text", { x: 0, y: TOP + r * CELL + CELL / 2 + 3, class: "hmlabel" });
    t.textContent = lbl;
    s.append(t);
  });
  for (let h = 0; h < 24; h += 2) {
    const t = svgel("text", { x: LEFT + h * CELL + CELL / 2, y: H - 4, class: "hmlabel", "text-anchor": "middle" });
    t.textContent = h;
    s.append(t);
  }
  for (let r = 0; r < 7; r++) for (let h = 0; h < 24; h++) {
    const n = grid[r + "_" + h] || 0;
    if (!n) continue;
    const c = svgel("circle", {
      cx: LEFT + h * CELL + CELL / 2, cy: TOP + r * CELL + CELL / 2,
      r: Math.max(2, R * Math.sqrt(n / max)), class: "punchdot" });
    const title = svgel("title");
    title.textContent = n + " session" + (n === 1 ? "" : "s") + " · " + DOW[r] + " " + h + ":00";
    c.append(title);
    s.append(c);
  }
  const scroll = el("div", "heatscroll");
  scroll.append(s);
  sec.append(scroll);
  return sec;
}

function projectsSection(d) {
  const sec = el("section", "stsec");
  sec.append(el("h2", null, "Projects"));
  const cards = el("div", "projcards");
  (d.projects || []).forEach(p => cards.append(projCard(p)));
  sec.append(cards);
  return sec;
}

function projCard(p) {
  const card = el("div", "projcard");
  const h = el("div", "pchead");
  h.append(el("span", "pcname", p.name), el("span", "pcses", p.sessions + " sess"));
  card.append(h, sparkline(p.spark));
  const row = el("div", "statsrow");
  row.append(kv("Σ", kfmt(p.tokens), "gold"), kv("$", usd(p.cost), "cost"));
  if (p.errors) row.append(kv("⚠", String(p.errors), "neg"));
  card.append(row);
  return card;
}

// small trend line over the project's last 90 days (0-filled for comparability)
function sparkline(spark) {
  const W = 220, H = 34, PAD = 2, DAYS = 90;
  const s = svgel("svg", { class: "spark", viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" });
  if (!spark || !spark.length) return s;
  const map = {};
  spark.forEach(([day, n]) => { map[day] = n; });
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const series = [];
  for (let i = DAYS - 1; i >= 0; i--) {
    const dt = new Date(today); dt.setDate(dt.getDate() - i);
    series.push(map[fmtDay(dt)] || 0);
  }
  const max = Math.max.apply(null, [1].concat(series));
  const pts = series.map((n, i) => {
    const x = PAD + i / (series.length - 1) * (W - 2 * PAD);
    const y = H - PAD - (n / max) * (H - 2 * PAD);
    return x.toFixed(1) + "," + y.toFixed(1);
  }).join(" ");
  s.append(svgel("polyline", { points: pts, class: "sparkline", fill: "none" }));
  return s;
}

function leaveSession() {
  stopDictation();               // a mic must never outlive its composer
  if (S.ses) {
    if (S.ses.es) S.ses.es.close();
    closeAgentStream();
    clearMonitorPoll();
    clearJobPoll();
    if (S.ses.timer) clearTimeout(S.ses.timer);
    if (S.ses.poll) clearInterval(S.ses.poll);
    // disarm optimistic stale watchdogs (composer bubbles + the ask/plan card
    // pends): navigating away is a deliberate abandon, not a stuck state, so it
    // mustn't beacon `stale` (close pends are global — S.closePend — and keep
    // reconciling from the sessions poll regardless of the current view)
    if (S.ses.pending)
      S.ses.pending.forEach(p => { if (p.timer) clearTimeout(p.timer); });
    if (S.ses.askPend && S.ses.askPend.timer) clearTimeout(S.ses.askPend.timer);
    if (S.ses.planPend && S.ses.planPend.timer) clearTimeout(S.ses.planPend.timer);
  }
  // a single-Esc arms a 450ms interrupt hold-timer that reads S.cur/S.ses at
  // FIRE time; leaving within that window (e.g. ⌃⇧←/→ tab-cycle) would land the
  // interrupt on whatever session is open next. Disarm it on navigation.
  if (escHold) { clearTimeout(escHold); escHold = null; }
  S.ses = null;
  S.cur = null;
}

/* ---------- sessions list view ---------- */

