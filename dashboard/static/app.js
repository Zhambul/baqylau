// dashboard/static/app.js — the single-page app.
//
// Server-rendered op HTML (dashboard/opshtml.py — escaped there, the
// neutralize() analog) is the ONLY thing inserted via innerHTML; everything
// built from JSON (timelines, stats, session rows) goes through el() /
// textContent, so transcript text can never become markup.
"use strict";

const $view = document.getElementById("view");
const $toasts = document.getElementById("toasts");
const $conn = document.getElementById("conn");
const $notifbtn = document.getElementById("notifbtn");

const S = {
  sessions: [],          // last global snapshot
  cur: null,             // sid of the open session view
  ses: null,             // per-session state {es, lastId, stream, stats, agents, costs, meta, timer}
  esGlobal: null,
};

/* ---------- tiny DOM + fmt helpers ---------- */

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function frag(...kids) { const f = document.createDocumentFragment(); kids.forEach(k => k && f.append(k)); return f; }

function kfmt(n) {
  n = +n || 0;
  if (n >= 999500) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 1000) return Math.round(n / 1000) + "k";
  return String(n);
}
function usd(c) {
  if (c == null || isNaN(c)) return "";
  if (c === 0) return "$0";
  if (c < 0.005) return "<$0.01";
  if (c < 10) return "$" + c.toFixed(2);
  if (c < 1000) return "$" + Math.round(c);
  return "$" + (c / 1000).toFixed(1) + "k";
}
function dur(sec) {
  sec = Math.max(0, sec | 0);
  if (sec < 60) return sec + "s";
  if (sec < 3600) return (sec / 60 | 0) + "m" + String(sec % 60).padStart(2, "0") + "s";
  if (sec < 86400) return (sec / 3600 | 0) + "h" + String(sec % 3600 / 60 | 0).padStart(2, "0") + "m";
  return (sec / 86400 | 0) + "d" + String(sec % 86400 / 3600 | 0).padStart(2, "0") + "h";
}
function ago(ts) {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 90) return "just now";
  if (s < 3600) return (s / 60 | 0) + "m ago";
  if (s < 86400) return (s / 3600 | 0) + "h ago";
  return (s / 86400 | 0) + "d ago";
}
function proj(row) {
  const c = row.cwd || "";
  return c ? c.split("/").filter(Boolean).pop() : (row.sid || "").slice(0, 18);
}
function shortSid(sid) { return (sid || "").length > 20 ? sid.slice(0, 8) + "…" + sid.slice(-4) : sid; }

const TAB_LABEL = {
  "": "no tab", "idle": "idle", "thinking": "busy", "working": "busy",
  "executing": "running", "awaiting-bg": "running",
  "awaiting-command": "asking you", "awaiting-response": "your turn",
};

/* ---------- toasts + OS notifications ---------- */

function toast(kind, t1, t2, onclick) {
  const n = el("div", "toast " + (kind || ""));
  n.append(el("div", "t1", t1));
  if (t2) n.append(el("div", "t2", t2));
  n.onclick = () => { n.remove(); onclick && onclick(); };
  $toasts.append(n);
  setTimeout(() => n.remove(), 7000);
}

function osNotify(title, body, sid) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (!document.hidden) return;                    // in-page toast covers visible
  const n = new Notification(title, { body, tag: "claude-" + sid });
  n.onclick = () => { window.focus(); location.hash = "#/s/" + sid; n.close(); };
}

function initNotifBtn() {
  if (!("Notification" in window)) return;
  if (Notification.permission === "default") {
    $notifbtn.hidden = false;
    $notifbtn.onclick = () =>
      Notification.requestPermission().then(() => { $notifbtn.hidden = true; });
  }
}

/* ---------- global event stream ---------- */

function connectGlobal() {
  const es = new EventSource("/events");
  S.esGlobal = es;
  es.onopen = () => { $conn.dataset.on = "1"; };
  es.onerror = () => { $conn.dataset.on = "0"; };
  es.addEventListener("sessions", (e) => {
    S.sessions = JSON.parse(e.data);
    if (!S.cur) renderList();
    else updateHeadFromList();
  });
  es.addEventListener("notify", (e) => {
    const d = JSON.parse(e.data);
    const asking = d.kind === "asking";
    const t1 = (d.project || d.sid) + (asking ? " needs you" : " is done");
    const t2 = asking ? "Claude is asking a question" : "finished — your turn";
    toast(asking ? "ask" : "done", t1, t2, () => { location.hash = "#/s/" + d.sid; });
    osNotify(t1, t2, d.sid);
  });
}

/* ---------- router ---------- */

window.addEventListener("hashchange", route);

function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "s" && parts[1]) {
    const sid = decodeURIComponent(parts[1]);
    if (parts[2] === "a" && parts[3]) return showAgent(sid, decodeURIComponent(parts[3]));
    return showSession(sid, parts[2] || "mirror");
  }
  showList();
}

function leaveSession() {
  if (S.ses) {
    if (S.ses.es) S.ses.es.close();
    if (S.ses.timer) clearTimeout(S.ses.timer);
    if (S.ses.poll) clearInterval(S.ses.poll);
  }
  S.ses = null;
  S.cur = null;
}

/* ---------- sessions list view ---------- */

function showList() {
  leaveSession();
  renderList();
  if (!S.sessions.length)
    fetch("/api/sessions").then(r => r.json()).then(d => { S.sessions = d; renderList(); });
}

function renderList() {
  if (S.cur) return;
  $view.textContent = "";
  $view.append(el("div", "mhead", "sessions"));
  const grid = el("div", "sgrid");
  if (!S.sessions.length) grid.append(el("div", "empty", "no sessions recorded yet"));
  for (const row of S.sessions) grid.append(sessionCard(row));
  $view.append(grid);
}

function sessionCard(row) {
  const a = el("a", "scard");
  a.href = "#/s/" + encodeURIComponent(row.sid);
  const st = row.stats || {};
  a.append(el("div", "proj", proj(row)));
  a.append(el("div", "sid", row.sid));
  const corner = el("div", "corner");
  corner.append(el("span", "chip2 " + (row.live ? "live" : "parked"),
                   row.live ? "live" : (row.parked ? "parked" : "gone")));
  a.append(corner);
  const r = el("div", "row");
  const badge = el("span", "badge");
  badge.dataset.tab = row.tab || "";
  badge.append(el("span", "st"), document.createTextNode(TAB_LABEL[row.tab || ""] || row.tab));
  r.append(badge);
  if (st.commands) r.append(seg(st.commands + " cmds"));
  const tok = (st.tk_in | 0) + (st.tk_out | 0) + (st.tk_read | 0) + (st.tk_create | 0);
  if (tok) r.append(seg(kfmt(tok) + " tok"));
  if (st.cost) r.append(segc(usd(st.cost), "cost"));
  if (row.started_at) r.append(seg(ago(row.started_at)));
  a.append(r);
  return a;
}
function seg(text) { const s = el("span"); s.append(el("span", "v", text)); return s; }
function segc(text, cls) { const s = el("span"); s.append(el("span", cls, text)); return s; }

function updateHeadFromList() {
  const row = S.sessions.find(r => r.sid === S.cur);
  if (row && S.ses && S.ses.badge) setBadge(S.ses.badge, row.tab || "");
}

/* ---------- session view ---------- */

function showSession(sid, tab) {
  if (S.cur !== sid) {
    leaveSession();
    S.cur = sid;
    S.ses = { lastId: 0, stream: el("div", "stream"), stats: {}, agents: [],
              costs: null, meta: null, es: null, timer: null, poll: null };
    S.ses.stream.append(el("div", "waiting", "waiting for activity…"));
    fetch("/api/session/" + encodeURIComponent(sid))
      .then(r => r.json())
      .then(d => {
        if (S.cur !== sid) return;
        S.ses.meta = d;
        S.ses.stats = d.stats || {};
        S.ses.agents = d.agents || [];
        S.ses.costs = d.costs || null;
        renderSessionChrome(tab);
      });
    connectSession(sid);
  }
  S.ses.tab = tab;
  renderSessionChrome(tab);
}

function connectSession(sid) {
  if (!S.ses || S.cur !== sid) return;
  const es = new EventSource("/events/session/" + encodeURIComponent(sid)
                             + "?after=" + S.ses.lastId);
  S.ses.es = es;
  es.addEventListener("ops", (e) => {
    const d = JSON.parse(e.data);
    if (d.last <= S.ses.lastId) return;
    S.ses.lastId = d.last;
    appendOps(d.html);
  });
  es.addEventListener("stats", (e) => { S.ses.stats = JSON.parse(e.data); updateStatsRow(); });
  es.addEventListener("agents", (e) => { S.ses.agents = JSON.parse(e.data); updateAgents(); });
  es.addEventListener("costs", (e) => { S.ses.costs = JSON.parse(e.data); updateStatsRow(); });
  es.addEventListener("tab", (e) => {
    const d = JSON.parse(e.data);
    if (S.ses && S.ses.badge) setBadge(S.ses.badge, d.tab || "");
  });
  es.onopen = () => { $conn.dataset.on = "1"; };
  es.onerror = () => {
    es.close();
    if (S.cur !== sid) return;
    S.ses.timer = setTimeout(() => connectSession(sid), 1500);
  };
}

function appendOps(htmlBlocks) {
  const st = S.ses.stream;
  const w = st.querySelector(".waiting");
  if (w) w.remove();
  const nearBottom =
    window.innerHeight + window.scrollY >= document.body.scrollHeight - 60;
  for (const h of htmlBlocks) st.insertAdjacentHTML("beforeend", h);
  while (st.childElementCount > 4000) st.firstElementChild.remove();
  if (nearBottom && st.isConnected) window.scrollTo(0, document.body.scrollHeight);
}

function setBadge(badge, tab) {
  badge.dataset.tab = tab;
  badge.replaceChildren(el("span", "st"),
                        document.createTextNode(TAB_LABEL[tab] || tab || "no tab"));
}

function renderSessionChrome(tab) {
  const ses = S.ses;
  if (!ses) return;
  const meta = ses.meta || {};
  $view.textContent = "";

  const head = el("div", "shead");
  const l1 = el("div", "l1");
  l1.append(el("span", "proj", meta.cwd ? proj(meta) : shortSid(S.cur)));
  const badge = el("span", "badge");
  ses.badge = badge;
  setBadge(badge, meta.tab || "");
  l1.append(badge);
  l1.append(el("span", "chip2 " + (meta.live ? "live" : "parked"),
               meta.live ? "live" : "parked"));
  l1.append(el("span", "sid", S.cur));
  head.append(l1);
  const sr = el("div", "statsrow");
  ses.statsRow = sr;
  head.append(sr);
  $view.append(head);
  updateStatsRow();

  const tabs = el("div", "tabs");
  const mk = (key, label, count) => {
    const a = el("a", key === tab ? "on" : "");
    a.href = "#/s/" + encodeURIComponent(S.cur) + (key === "mirror" ? "" : "/" + key);
    a.append(document.createTextNode(label));
    if (count) a.append(el("span", "count", String(count)));
    tabs.append(a);
  };
  mk("mirror", "mirror");
  mk("activity", "activity");
  mk("agents", "agents", (ses.agents || []).length);
  mk("errors", "errors", meta.error_count || 0);
  $view.append(tabs);

  const body = el("div");
  ses.body = body;
  $view.append(body);

  if (tab === "mirror") {
    const split = el("div", "split");
    split.append(ses.stream);
    const rail = el("div", "rail");
    ses.rail = rail;
    split.append(rail);
    body.append(split);
    updateAgents();
  } else if (tab === "activity") {
    renderTimelineInto(body, "/api/session/" + encodeURIComponent(S.cur) + "/activity",
                       "main thread");
  } else if (tab === "agents") {
    const wrap = el("div", "sgrid");
    ses.agentsGrid = wrap;
    body.append(wrap);
    updateAgents();
  } else if (tab === "errors") {
    renderErrorsInto(body);
  }
}

function updateStatsRow() {
  const ses = S.ses;
  if (!ses || !ses.statsRow) return;
  const st = ses.stats || {};
  const sr = ses.statsRow;
  sr.textContent = "";
  const add = (label, value, cls) => {
    const s = el("span");
    if (label) s.append(document.createTextNode(label + " "));
    s.append(el("span", cls || "v", value));
    sr.append(s);
  };
  if (st.commands) {
    add("", st.commands + " cmds");
    if (st.failed) add("", "(" + st.failed + "✗)", "neg");
  }
  if (st.start)
    add("⏱", dur(Date.now() / 1000 - st.start - (+st.paused || 0)));
  if (st.files) add("", st.files + " files");
  if (st.added) add("", "+" + st.added, "pos");
  if (st.removed) add("", "−" + st.removed, "neg");
  const tin = st.tk_in | 0, tout = st.tk_out | 0, tread = st.tk_read | 0, tcre = st.tk_create | 0;
  const tot = tin + tout + tread + tcre;
  if (tot)
    add("Σ", kfmt(tot) + " (" + kfmt(tin) + " in · " + kfmt(tout) + " out · "
        + kfmt(tread) + " cache · " + kfmt(tcre) + " write)");
  const cost = (ses.costs && ses.costs.total_usd) || st.cost;
  if (cost) add("≈", usd(cost), "cost");
  if (st.msg_delivered)
    add("✉", st.msg_delivered + " msgs" +
        (st.msg_read ? " · " + st.msg_read + " read" : ""));
  const errn = (ses.meta && ses.meta.error_count) || 0;
  if (errn) add("", "⚠ " + errn, "warn");
}

function agentStatus(a) {
  if (a.ended_at == null && !a.done && a.started_at) return ["running", "st-run"];
  const er = a.end_reason || "";
  if (!er && a.ended_at == null) return ["unknown", "st-warn"];
  if (er.startsWith("stop-sentinel") || er.startsWith("state-db-parked")) return ["done", "st-ok"];
  if (er.includes("cancel") || er.includes("rejected")) return ["cancelled", "st-bad"];
  if (er === "crash" || er.includes("timeout")) return [er, "st-bad"];
  return [er || "done", "st-ok"];
}

function isHusk(a) {
  // a slot row with no kind/desc/transcript: an agent whose streamer never
  // ran (hidden auxiliary spawns) — shown dim, after the attributed ones
  return !a.kind && !a.desc && !a.transcript;
}

function sortedAgents(agents) {
  return [...agents].sort((x, y) => (isHusk(x) - isHusk(y))
    || ((x.started_at || 0) - (y.started_at || 0)));
}

function agentCard(a) {
  const card = el("a", "acard" + (isHusk(a) ? " husk" : ""));
  card.href = "#/s/" + encodeURIComponent(S.cur) + "/a/" + encodeURIComponent(a.agent_id);
  card.append(el("div", "aid", (a.kind === "teammate" ? "👥 " : "◇ ") + a.agent_id));
  if (a.desc) card.append(el("div", "desc", a.desc));
  const m = el("div", "meta");
  const [sttxt, stcls] = agentStatus(a);
  m.append(el("span", stcls, sttxt));
  if (a.tools != null) m.append(el("span", "", a.tools + " events"));
  if (a.started_at && a.ended_at)
    m.append(el("span", "", dur(a.ended_at - a.started_at)));
  else if (a.started_at)
    m.append(el("span", "", ago(a.started_at)));
  card.append(m);
  return card;
}

function updateAgents() {
  const ses = S.ses;
  if (!ses) return;
  const agents = sortedAgents(ses.agents || []);
  if (ses.tab === "mirror" && ses.rail && ses.rail.isConnected) {
    ses.rail.textContent = "";
    if (agents.length) ses.rail.append(el("div", "mhead", "agents"));
    for (const a of agents) ses.rail.append(agentCard(a));
  }
  if (ses.tab === "agents" && ses.agentsGrid && ses.agentsGrid.isConnected) {
    ses.agentsGrid.textContent = "";
    if (!agents.length) ses.agentsGrid.append(el("div", "empty", "no subagents in this session"));
    for (const a of agents) ses.agentsGrid.append(agentCard(a));
  }
}

/* ---------- timeline (activity / agent drill-down) ---------- */

function showAgent(sid, aid) {
  if (S.cur !== sid) showSession(sid, "agents");
  S.ses.tab = "agent:" + aid;
  const ses = S.ses;
  $view.querySelectorAll(".tabs a").forEach(a => a.classList.remove("on"));
  if (ses.body) {
    ses.body.textContent = "";
    renderTimelineInto(ses.body,
                       "/api/session/" + encodeURIComponent(sid) + "/agent/" + encodeURIComponent(aid),
                       aid);
  }
}

function renderTimelineInto(container, apiUrl, title) {
  container.append(el("div", "empty", "loading " + title + "…"));
  fetch(apiUrl).then(r => r.json()).then(d => {
    if (!container.isConnected) return;
    container.textContent = "";
    container.append(timelineHead(d, title));
    const list = el("div", "tl");
    const entries = d.entries || [];
    if (!entries.length) list.append(el("div", "empty", "no recorded activity"));
    for (const ent of entries) list.append(timelineEntry(ent));
    container.append(list);
  }).catch(() => {
    if (!container.isConnected) return;
    container.textContent = "";
    container.append(el("div", "empty", "no transcript available for " + title));
  });
}

function timelineHead(d, title) {
  const h = el("div", "tlhead");
  const add = (label, value) => {
    const s = el("span");
    s.append(document.createTextNode(label + " "));
    s.append(el("span", "v", value));
    h.append(s);
  };
  add("◈", title);
  if (d.model) add("model", d.model);
  if (d.tools) add("tools", String(d.tools));
  const u = d.usage || {};
  const tot = (u.in | 0) + (u.out | 0) + (u.cache | 0) + (u.create | 0);
  if (tot)
    add("Σ", kfmt(tot) + " (" + kfmt(u.in) + " in · " + kfmt(u.out) + " out · "
        + kfmt(u.cache) + " cache · " + kfmt(u.create) + " write)");
  if (d.bad_lines) add("⚠", d.bad_lines + " bad lines");
  return h;
}

function inputSummary(input) {
  if (!input || typeof input !== "object") return "";
  const parts = [];
  for (const [k, v] of Object.entries(input)) {
    const vs = typeof v === "string" ? v : JSON.stringify(v);
    parts.push(k + ": " + vs);
  }
  return parts.join("  ·  ");
}

function firstLine(s, n) {
  s = (s || "").trim();
  const nl = s.indexOf("\n");
  if (nl >= 0) s = s.slice(0, nl);
  return s.length > (n || 160) ? s.slice(0, n || 160) + "…" : s;
}

function timelineEntry(ent) {
  const box = el("div", "ent");
  const hd = el("div", "hd");
  const bd = el("div", "bd");
  let kcls = "k-message", ktxt = ent.t, sum = "", open = false;

  if (ent.t === "prompt") {
    kcls = "k-prompt"; ktxt = "prompt"; sum = firstLine(ent.text); open = true;
    bd.append(pre(ent.text));
  } else if (ent.t === "teammsg") {
    kcls = "k-teammsg"; ktxt = "✉ " + (ent.sender || "team");
    sum = firstLine(ent.body); open = false;
    bd.append(pre(ent.body));
  } else if (ent.t === "message") {
    kcls = ent.final ? "k-final" : "k-message";
    ktxt = ent.final ? "result" : "message";
    sum = firstLine(ent.text); open = !!ent.final;
    bd.append(pre(ent.text));
  } else if (ent.t === "compact") {
    kcls = "k-compact"; ktxt = "compact";
    sum = "context compacted"; open = false;
    bd.append(pre(JSON.stringify(ent.meta || {}, null, 2)));
  } else if (ent.t === "tool") {
    kcls = ent.failed ? "k-toolfail" : "k-tool";
    ktxt = ent.tool || "tool";
    sum = firstLine(inputSummary(ent.input)); open = false;
    if (ent.input && Object.keys(ent.input).length) {
      bd.append(el("div", "lbl", "input"));
      bd.append(pre(JSON.stringify(ent.input, null, 2)));
    }
    if (ent.output != null) {
      const lbl = el("div", "lbl", ent.failed ? "output · failed" : "output");
      if (ent.failed) lbl.classList.add("fail");
      bd.append(lbl);
      bd.append(pre(ent.output));
    } else {
      bd.append(el("div", "lbl", "no result recorded"));
    }
  } else if (ent.t === "orphan-result") {
    kcls = "k-orphan"; ktxt = "result";
    sum = firstLine(ent.output); open = false;
    bd.append(pre(ent.output));
  } else {
    sum = JSON.stringify(ent).slice(0, 160);
    bd.append(pre(JSON.stringify(ent, null, 2)));
  }

  hd.append(el("span", "k " + kcls, ktxt));
  const sspan = el("span", "sum");
  sspan.append(document.createTextNode(sum || ""));
  hd.append(sspan);
  box.dataset.open = open ? "1" : "0";
  hd.onclick = () => { box.dataset.open = box.dataset.open === "1" ? "0" : "1"; };
  box.append(hd, bd);
  return box;
}

function pre(text) { const p = el("pre"); p.textContent = text == null ? "" : String(text); return p; }

/* ---------- errors tab ---------- */

function renderErrorsInto(container) {
  container.append(el("div", "empty", "loading…"));
  fetch("/api/session/" + encodeURIComponent(S.cur) + "/errors")
    .then(r => r.json()).then(rows => {
      if (!container.isConnected) return;
      container.textContent = "";
      const wrap = el("div", "errs");
      if (!rows.length) wrap.append(el("div", "empty", "no swallowed exceptions — clean session"));
      for (const r of rows) {
        const e = el("div", "err");
        e.append(el("div", "h", "⚠ " + (r.script || "?") + " · " + (r.func || "?")
                    + (r.ts ? " · " + new Date(r.ts * 1000).toLocaleString() : "")));
        if (r.traceback) e.append(pre(r.traceback));
        wrap.append(e);
      }
      container.append(wrap);
    });
}

/* ---------- ⧉ copy / click-to-view (server-rendered .cc anchors) ---------- */

document.addEventListener("click", (e) => {
  const a = e.target.closest && e.target.closest("a.cc");
  if (!a) return;
  e.preventDefault();
  const cc = (a.dataset.cc || "").split("/");
  if (cc.length !== 3) return;
  const [key, gid, what] = cc;
  if (what === "view") return toggleView(a, key, gid);
  fetch("/api/session/" + encodeURIComponent(key) + "/copy/"
        + encodeURIComponent(gid) + "/" + encodeURIComponent(what))
    .then(r => r.text())
    .then(text => {
      if (!text.trim()) return toast("", "nothing to copy", "");
      navigator.clipboard.writeText(text).then(
        () => toast("done", "copied " + (what === "cmd" ? "command" : what === "out" ? "output" : "block"),
                    text.length + " chars"),
        () => toast("ask", "copy failed", "clipboard permission?"));
    });
});

function toggleView(anchor, key, gid) {
  const host = anchor.closest("[data-v]");
  if (!host) return;
  const next = host.nextElementSibling;
  if (next && next.classList.contains("view-block")) { next.remove(); return; }
  fetch("/api/session/" + encodeURIComponent(key) + "/view/" + encodeURIComponent(gid))
    .then(r => r.ok ? r.text() : null)
    .then(html => {
      if (html == null) return toast("", "nothing to show", "");
      host.insertAdjacentHTML("afterend", html);
    });
}

/* ---------- boot ---------- */

initNotifBtn();
connectGlobal();
route();
