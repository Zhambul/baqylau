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
const $attn = document.getElementById("attn");
const $favicon = document.getElementById("favicon");
const $newbtn = document.getElementById("newbtn");
const $modal = document.getElementById("modal");

const S = {
  sessions: [],          // last global snapshot
  cur: null,             // sid of the open session view
  ses: null,             // per-session state {es, lastId, stream, stats, agents, costs, meta, timer}
  esGlobal: null,
  folds: new Set(),      // open parked/archived subdivisions ("<cwd>|parked") —
                         // survives the list re-renders SSE snapshots trigger
  jump: null,            // pending jump-to-new-session watch ({cwd, known, until})
};

const ARCHIVE_S = 3 * 86400;   // sessions older than this fold into "archived"

/* ---------- tiny DOM + fmt helpers ---------- */

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function frag(...kids) { const f = document.createDocumentFragment(); kids.forEach(k => k && f.append(k)); return f; }

// The control-plane write: every POST carries the JSON content type AND the
// custom X-Claude-Dash header the server's _post_guard demands (both force a
// CORS preflight a cross-origin page can't pass). Resolves to the parsed JSON
// on success, rejects with the server's {error} on a 4xx/5xx.
function postJSON(url, body) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Claude-Dash": "1" },
    body: JSON.stringify(body || {}),
  }).then(r => r.json().then(
    d => r.ok ? d : Promise.reject(d || { error: "request failed" })));
}

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

/* The "running now" ribbon: glyph + short label per live `live`-table slot kind
   (sessionapi.running() — fg command, bg jobs, monitors, streaming agents). */
const RUN_GLYPH = {
  "fg": ["⚙", "fg"], "bg": ["⏳", "bg"], "monitor": ["👁", "monitor"],
  "sub.pid": ["◇", "agent"], "codex": ["◆", "codex"],
};
const RUN_ORDER = ["fg", "bg", "monitor", "sub.pid", "codex"];

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

/* ---------- persistent attention bar ---------- */
// The standing complement to the transient toasts: a slim bar under the header,
// on every view, listing every LIVE session that needs you — asking (red,
// awaiting-command) pills first, your-turn (green, awaiting-response) quieter
// after them — hidden entirely when nothing does. Fed from the global S.sessions
// snapshots the app already holds, plus the open session's `tab` SSE event
// (which patches its row in place so the bar reacts before the next snapshot).

const BASE_TITLE = "claude · dashboard";
const FAV_GLYPH = "<text y='13' font-size='13'>⬡</text>";
const favData = (extra) =>
  "data:image/svg+xml," + encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'>"
    + FAV_GLYPH + (extra || "") + "</svg>");
const FAVICON = favData("");
const FAVICON_ASK = favData("<circle cx='12' cy='4' r='4' fill='#e06c75'/>");

function attnPill(row) {
  const asking = row.tab === "awaiting-command";
  const a = el("a", "attn-pill " + (asking ? "ask" : "done")
                   + (row.sid === S.cur ? " self" : ""));
  a.href = "#/s/" + encodeURIComponent(row.sid);
  a.append(el("span", "adot"));
  a.append(el("span", "alabel", row.title || proj(row)));
  a.title = (asking ? "asking you" : "your turn") + " · " + row.sid;
  return a;
}

function renderAttention() {
  if (!$attn) return;
  const asking = [], yours = [];
  for (const row of S.sessions) {
    if (!row.live) continue;
    if (row.tab === "awaiting-command") asking.push(row);
    else if (row.tab === "awaiting-response") yours.push(row);
  }
  const show = asking.length + yours.length > 0;
  $attn.hidden = !show;
  document.body.classList.toggle("attn-on", show);
  $attn.textContent = "";
  if (show) {
    if (asking.length)
      $attn.append(el("span", "alead ask", asking.length + " asking"));
    for (const row of asking) $attn.append(attnPill(row));
    for (const row of yours) $attn.append(attnPill(row));
  }
  document.title = asking.length ? "(" + asking.length + ") " + BASE_TITLE : BASE_TITLE;
  if ($favicon) $favicon.href = asking.length ? FAVICON_ASK : FAVICON;
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
    renderAttention();
    checkJump();
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
    S.jump = null;      // the user picked a session themselves — the pending
    //                     auto-jump is stale (checkJump clears BEFORE navigating,
    //                     so its own hash change never lands here armed)
    const sid = decodeURIComponent(parts[1]);
    if (parts[2] === "a" && parts[3]) return showAgent(sid, decodeURIComponent(parts[3]));
    return showSession(sid, parts[2] || "mirror");
  }
  showList();
}

function leaveSession() {
  if (S.ses) {
    if (S.ses.es) S.ses.es.close();
    closeAgentStream();
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
    fetch("/api/sessions").then(r => r.json())
      .then(d => { S.sessions = d; renderList(); renderAttention(); });
}

function renderList() {
  if (S.cur) return;
  $view.textContent = "";
  if (!S.sessions.length) {
    $view.append(el("div", "empty", "no sessions recorded yet"));
    return;
  }
  renderDirGroups(S.sessions);
}

function renderDirGroups(rows) {
  // one group per directory (ordered by its newest session); inside each:
  // active cards visible, parked / archived (>3d) as click-to-open folds
  const groups = new Map();
  for (const row of rows) {
    const k = row.cwd || "";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(row);
  }
  const ordered = [...groups.entries()].sort((a, b) =>
    Math.max(...b[1].map(r => r.started_at || 0))
    - Math.max(...a[1].map(r => r.started_at || 0)));
  const now = Date.now() / 1000;
  for (const [cwd, grows] of ordered) {
    const active = grows.filter(r => r.live);
    const rest = grows.filter(r => !r.live);
    const old = r => !r.started_at || now - r.started_at > ARCHIVE_S;
    const parked = rest.filter(r => !old(r));
    const archived = rest.filter(old);

    const hd = el("div", "dirhead");
    hd.append(el("span", "dirname", cwd ? cwd.split("/").filter(Boolean).pop() : "no project"));
    if (cwd) hd.append(el("span", "dirpath", cwd));
    hd.append(el("span", "dircount", grows.length + (grows.length === 1 ? " session" : " sessions")));
    if (cwd) {
      const add = el("button", "dirnew", "+");
      add.title = "new session in " + cwd;
      add.onclick = () => openNewSession(cwd);
      hd.append(add);
    }
    $view.append(hd);
    if (active.length) {
      const grid = el("div", "sgrid");
      for (const row of active) grid.append(sessionCard(row));
      $view.append(grid);
    }
    fold(cwd, "parked", parked);
    fold(cwd, "archived", archived);
  }
}

function fold(cwd, kind, rows) {
  if (!rows.length) return;
  const key = cwd + "|" + kind;
  const open = S.folds.has(key);
  const btn = el("button", "fold" + (open ? " open" : ""),
                 (open ? "▾ " : "▸ ") + kind + " · " + rows.length);
  btn.onclick = () => {
    S.folds.has(key) ? S.folds.delete(key) : S.folds.add(key);
    renderList();
  };
  $view.append(btn);
  if (open) {
    const grid = el("div", "sgrid folded");
    for (const row of rows) grid.append(sessionCard(row));
    $view.append(grid);
  }
}

function sessionCard(row) {
  const a = el("a", "scard");
  a.href = "#/s/" + encodeURIComponent(row.sid);
  const st = row.stats || {};
  a.append(el("div", "proj", row.title || proj(row)));
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
    S.ses = { lastId: 0, mpos: 0, oldest: 0, stream: el("div", "stream"), stats: {},
              agents: [], costs: null, running: {}, meta: null, es: null, agentEs: null,
              timer: null, poll: null, blocks: new Map(), moreEl: null,
              loadingOlder: false,
              filter: { q: "", kind: "all" } };   // cleared per session (new S.ses)
    S.ses.stream.append(el("div", "waiting", "waiting for activity…"));
    fetch("/api/session/" + encodeURIComponent(sid))
      .then(r => r.json())
      .then(d => {
        if (S.cur !== sid) return;
        S.ses.meta = d;
        S.ses.stats = d.stats || {};
        S.ses.agents = d.agents || [];
        S.ses.costs = d.costs || null;
        S.ses.running = d.running || {};
        renderSessionChrome(tab);
      });
    connectSession(sid);
  }
  closeAgentStream();                       // leaving any agent drill-down view
  S.ses.tab = tab;
  renderSessionChrome(tab);
}

function connectSession(sid) {
  if (!S.ses || S.cur !== sid) return;
  const es = new EventSource("/events/session/" + encodeURIComponent(sid)
                             + "?after=" + S.ses.lastId + "&mpos=" + S.ses.mpos);
  S.ses.es = es;
  es.addEventListener("ops", (e) => {
    const d = JSON.parse(e.data);
    if (d.last <= S.ses.lastId && !d.items.length) return;
    S.ses.lastId = Math.max(S.ses.lastId, d.last);
    if (d.mpos != null) S.ses.mpos = Math.max(S.ses.mpos, d.mpos);
    // the initial (fresh-connection) backlog carries `oldest` — the smallest
    // op id painted; >0 means older blocks exist to lazy-load downward.
    if (d.oldest != null) { S.ses.oldest = d.oldest | 0; updateMoreBtn(); }
    appendItems(d.items);
  });
  es.addEventListener("msgs", (e) => {
    const d = JSON.parse(e.data);
    if (d.mpos <= S.ses.mpos) return;
    S.ses.mpos = d.mpos;
    appendItems(d.items);
  });
  es.addEventListener("stats", (e) => { S.ses.stats = JSON.parse(e.data); updateStatsRow(); });
  es.addEventListener("agents", (e) => { S.ses.agents = JSON.parse(e.data); updateAgents(); });
  es.addEventListener("costs", (e) => { S.ses.costs = JSON.parse(e.data); updateStatsRow(); });
  es.addEventListener("running", (e) => { S.ses.running = JSON.parse(e.data); updateRunning(); });
  es.addEventListener("errors", (e) => { updateErrCount(JSON.parse(e.data).count | 0); });
  es.addEventListener("tab", (e) => {
    const d = JSON.parse(e.data);
    if (S.ses && S.ses.badge) setBadge(S.ses.badge, d.tab || "");
    // patch the open session's row so the attention bar reacts before the
    // next global snapshot lands (item 4: react to the per-session tab event)
    const row = S.sessions.find(r => r.sid === S.cur);
    if (row) row.tab = d.tab || "";
    renderAttention();
  });
  es.onopen = () => { $conn.dataset.on = "1"; };
  es.onerror = () => {
    es.close();
    if (S.cur !== sid) return;
    S.ses.timer = setTimeout(() => connectSession(sid), 1500);
  };
}

// Stream items ({g, t, html}) fold into collapsible BLOCK cards by copy-group
// id: label ops become the block's summary chips (start chip, then the
// finished/duration chip), everything else goes to the fold-away body. The
// LAST `KEEP_OPEN` blocks stay expanded (the recent-activity tail you're
// actually reading); anything older folds to its one-line summary as new
// blocks push it out of the window — unless the user toggled it themselves,
// which always wins. Ungrouped items (messages, file-op one-liners) stay
// inline.
const KEEP_OPEN = 5;
const HISTORY_FETCH = 40;      // blocks per lazy-backlog /history page

function enforceWindow() {
  const blocks = [...S.ses.blocks.values()];
  const cut = blocks.length - KEEP_OPEN;
  blocks.forEach((b, i) => {
    if (i < cut && !b.userSet && b.root.dataset.open === "1")
      b.root.dataset.open = "0";
  });
}

// The stream is a FEED: newest on top. Items arrive oldest→newest and each
// is inserted at the top, so the batch lands newest-first; a block keeps the
// position of its first op (its body still reads top-down) and new blocks
// appear above it.
// A collapsible block card (root/head/chips/sum/body + fold-toggle handler),
// unplaced — the caller inserts .root and decides tracking. Shared by the live
// top-prepend path (appendItems) and the older-history bottom-append path
// (appendOlder).
function createBlock() {
  const root = el("div", "blk");
  root.dataset.open = "1";                       // enforceWindow folds elders
  root.dataset.kind = "commands";                // refineBlockKind upgrades to "agents"
  const head = el("div", "bhead");
  const chips = el("span", "bchips");
  const sum = el("span", "bsum");
  const body = el("div", "bbody");
  head.append(chips, sum);
  root.append(head, body);
  const b = { root, chips, sum, body, userSet: false, kindLocked: false };
  head.onclick = (e) => {
    if (e.target.closest("a")) return;           // ⧉ links keep working
    b.userSet = true;
    root.dataset.open = root.dataset.open === "1" ? "0" : "1";
  };
  return b;
}

// Add one grouped item to a block: label ops become summary chips, everything
// else appends to the body (and seeds the one-line summary). Body always reads
// oldest->newest (top-down), matching arrival order.
function fillBlock(b, it) {
  if (it.t === "label") {
    b.chips.insertAdjacentHTML("beforeend", it.html);
  } else {
    b.body.insertAdjacentHTML("beforeend", it.html);
    if (!b.sum.textContent && b.body.lastElementChild) {
      const line = (b.body.lastElementChild.textContent || "")
        .trim().split("\n").find(l => l.trim());
      if (line) b.sum.textContent = line.slice(0, 160);
    }
  }
}

function appendItems(items) {
  const st = S.ses.stream;
  const w = st.querySelector(".waiting");
  if (w) w.remove();
  for (const it of items) {
    if (!it.g) {
      st.insertAdjacentHTML("afterbegin", it.html);
      const elem = st.firstElementChild;
      if (elem) { elem.dataset.kind = ungroupedKind(it, elem); applyFilterTo(elem); }
      continue;
    }
    let b = S.ses.blocks.get(it.g);
    if (!b) {
      b = createBlock();
      st.prepend(b.root);
      S.ses.blocks.set(it.g, b);
    }
    fillBlock(b, it);
    refineBlockKind(b, it);
    applyFilterTo(b.root);
  }
  enforceWindow();
  while (st.childElementCount > 3000) {
    const last = st.lastElementChild;
    if (!last || last === S.ses.moreEl) break;   // the load-older affordance stays
    last.remove();
  }
  updateFilterCount();
}

// The lazy-backlog downward path (item 3): a chunk of OLDER items (server order
// oldest->newest) appended at the BOTTOM of the feed — the feed is newest-top,
// so older loads downward, and each successive page is older still, going lower.
// Blocks born in this chunk start FOLDED and are NOT tracked in the live
// S.ses.blocks map or the KEEP_OPEN window (they are history, not the live
// tail). A group that STRADDLES the load boundary (already live in the map) has
// its older ops appended into the existing card body at the end — acceptable;
// older ops trail the newer ones (docs/dashboard.md).
function appendOlder(items) {
  const st = S.ses.stream;
  const local = new Map();                       // g -> block, for this chunk only
  const frag = document.createDocumentFragment();
  for (const it of items) {
    if (!it.g) {
      const tmp = el("div");
      tmp.innerHTML = it.html;
      const elem = tmp.firstElementChild;
      if (elem) { elem.dataset.kind = ungroupedKind(it, elem); applyFilterTo(elem); frag.append(elem); }
      continue;
    }
    const live = S.ses.blocks.get(it.g);         // straddling group: fold in-place
    if (live) {
      fillBlock(live, it);
      refineBlockKind(live, it);
      applyFilterTo(live.root);
      continue;
    }
    let b = local.get(it.g);
    if (!b) {
      b = createBlock();
      b.root.dataset.open = "0";                 // history blocks arrive folded
      local.set(it.g, b);
      frag.append(b.root);
    }
    fillBlock(b, it);
    refineBlockKind(b, it);
    applyFilterTo(b.root);
  }
  if (S.ses.moreEl) st.insertBefore(frag, S.ses.moreEl);
  else st.append(frag);
  updateFilterCount();
}

// The "load older" affordance: a button pinned at the BOTTOM of the feed (a
// child of the stream, so appendItems' top-prepends never disturb it), shown
// while older blocks remain (S.ses.oldest > 0) and hidden once /history is
// exhausted (oldest 0). Each click fetches the previous page and appends it
// downward via appendOlder; filters apply to those items in appendOlder.
function ensureMoreEl() {
  const ses = S.ses;
  if (!ses) return null;
  if (ses.moreEl && ses.moreEl.isConnected) return ses.moreEl;
  const b = el("button", "loadmore");
  b.hidden = true;
  b.onclick = () => loadOlder();
  ses.moreEl = b;
  ses.stream.append(b);                          // bottom of the feed
  return b;
}

function updateMoreBtn() {
  const ses = S.ses;
  if (!ses) return;
  const b = ensureMoreEl();
  if (!b) return;
  const has = (ses.oldest | 0) > 0;
  b.hidden = !has;
  if (has && !ses.loadingOlder)
    b.textContent = "load older · " + HISTORY_FETCH + " more blocks…";
}

function loadOlder() {
  const ses = S.ses;
  if (!ses || ses.loadingOlder || (ses.oldest | 0) <= 0) return;
  ses.loadingOlder = true;
  const sid = S.cur, before = ses.oldest;
  if (ses.moreEl) ses.moreEl.textContent = "loading…";
  fetch("/api/session/" + encodeURIComponent(sid) + "/history?before=" + before
        + "&blocks=" + HISTORY_FETCH)
    .then(r => r.json())
    .then(d => {
      ses.loadingOlder = false;
      if (S.cur !== sid) return;                 // navigated away mid-fetch
      appendOlder(d.items || []);
      ses.oldest = d.oldest | 0;
      updateMoreBtn();
    })
    .catch(() => { ses.loadingOlder = false; updateMoreBtn(); });
}

/* ---------- stream search + kind filters ---------- */
// Every top-level stream child carries a data-kind (commands · files · agents ·
// messages) so the filter bar can hide non-matching items via a CSS class
// (never removing them — SSE keeps appending, and folded bodies stay
// textContent-searchable). data-kind is stamped once at creation: glyph/who
// sniffing (below) is stable against the exact chip text, which drifts, so we
// prefer a stamped attribute over re-sniffing the DOM on every filter pass.

// Main-session command/monitor/bg/fg blocks OPEN with one of these glyphs;
// subagent/teammate/codex blocks open with a who-prefix (agent label or
// "codex") before their glyph — that's the command-vs-agent tell.
const CMD_GLYPH = /^\s*[▶▷◉■]/;

function refineBlockKind(b, it) {
  if (b.root.dataset.kind === "agents") return;        // agent wins, monotonic
  if (/class="og"/.test(it.html)) {                    // outer gutter == nested subagent job
    b.root.dataset.kind = "agents";
    return;
  }
  if (it.t === "label" && !b.kindLocked) {
    const txt = (b.chips.textContent || "").trim();    // the block-opening chip
    if (txt) {
      b.root.dataset.kind = CMD_GLYPH.test(txt) ? "commands" : "agents";
      b.kindLocked = true;
    }
  }
}

function ungroupedKind(it, elem) {
  if (it.t === "msg") return "messages";
  // file-op one-liners carry the click-to-view id as data-v (.opl / gut ops)
  if (elem.matches("[data-v]") || elem.querySelector("[data-v]")) return "files";
  return "commands";
}

function streamItems() {
  return [...S.ses.stream.children].filter(el => el.dataset && el.dataset.kind);
}

function matchesFilter(elem) {
  const f = (S.ses && S.ses.filter) || { q: "", kind: "all" };
  if (f.kind !== "all" && elem.dataset.kind !== f.kind) return false;
  if (f.q && !(elem.textContent || "").toLowerCase().includes(f.q)) return false;
  return true;
}

function applyFilterTo(elem) {
  if (!elem || !elem.dataset || !elem.dataset.kind) return;
  const ok = matchesFilter(elem);
  elem.classList.toggle("fhide", !ok);
  const f = S.ses && S.ses.filter;
  elem.classList.toggle("fq", ok && !!(f && f.q));     // subtle text-match highlight
}

function applyFilter() {
  if (!S.ses) return;
  for (const elem of streamItems()) applyFilterTo(elem);
  updateFilterCount();
}

function updateFilterCount() {
  const ses = S.ses;
  if (!ses || !ses.countEl || !ses.countEl.isConnected) return;
  const items = streamItems();
  const shown = items.filter(elem => !elem.classList.contains("fhide")).length;
  ses.countEl.textContent = shown + " of " + items.length + " shown";
}

const FILTER_KINDS = ["all", "commands", "files", "agents", "messages"];

function buildFilterBar() {
  const ses = S.ses;
  const f = ses.filter;
  const bar = el("div", "fbar");

  const input = el("input", "finput");
  input.type = "text";
  input.placeholder = "filter…";
  input.spellcheck = false;
  input.value = f.q;
  ses.input = input;
  let deb;
  input.oninput = () => {
    clearTimeout(deb);
    deb = setTimeout(() => { f.q = input.value.trim().toLowerCase(); applyFilter(); }, 150);
  };
  input.onkeydown = (e) => {
    if (e.key === "Escape" && f.q) {
      input.value = ""; f.q = ""; applyFilter();
      e.stopPropagation();
    }
  };

  const clear = el("button", "fclear", "✕");
  clear.title = "clear filter";

  const chipwrap = el("div", "fchips");
  const chips = new Map();
  for (const key of FILTER_KINDS) {
    const c = el("button", "fchip" + (f.kind === key ? " on" : ""), key);
    c.onclick = () => {
      f.kind = key;
      chips.forEach((cc, k) => cc.classList.toggle("on", k === key));
      applyFilter();
    };
    chips.set(key, c);
    chipwrap.append(c);
  }

  clear.onclick = () => {
    input.value = ""; f.q = ""; f.kind = "all";
    chips.forEach((cc, k) => cc.classList.toggle("on", k === "all"));
    applyFilter();
    input.focus();
  };

  const count = el("span", "fcount");
  ses.countEl = count;
  bar.append(input, clear, chipwrap, count);
  ses.filterBar = bar;
  return bar;
}

/* ---------- control plane: the message composer ---------- */
// A textarea above the mirror feed that types a message into the session's
// kitty window (POST /message). Enter sends, Shift+Enter is a newline. Disabled
// with a hint when the session isn't live or has no window (a headless/daemon
// session — the /message endpoint would 409). The sent text surfaces in the
// stream on its own via the conversation tail, so we only clear + toast.
//
// The "/" menu (Claude-Code-style): a leading "/" with no whitespace yet opens
// a completion menu over GET /api/session/<sid>/commands (built-ins + the
// session cwd's .claude commands/skills — fetched once per session view).
// ↑/↓ move, Tab completes, Enter completes a PARTIAL token but sends when the
// token already IS the selection (so a fully-typed "/compact" sends on one
// Enter), Esc closes. The TUI stays authoritative — sending just types the
// command into the terminal and Claude Code's own palette executes it.

function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
}

function buildComposer() {
  const ses = S.ses;
  const meta = ses.meta || {};
  const wrap = el("div", "composer");
  const ta = el("textarea", "cinput");
  ta.rows = 1;
  ta.spellcheck = false;
  const canSend = !!(meta.live && meta.kitty_window_id);
  ta.disabled = !canSend;
  ta.placeholder = canSend
    ? "message this session…  (Enter to send · Shift+Enter for newline)"
    : (meta.live ? "no terminal window — can't message a headless session"
                 : "session is not live");
  const btn = el("button", "csend", "send");
  btn.disabled = !canSend;
  ses.composer = ta;
  const send = () => {
    const text = ta.value.trim();
    if (!text || ta.disabled) return;
    ta.disabled = true; btn.disabled = true;
    postJSON("/api/session/" + encodeURIComponent(S.cur) + "/message", { text })
      .then(() => { ta.value = ""; autoGrow(ta); toast("done", "message sent", ""); })
      .catch(e => toast("ask", "send failed", (e && e.error) || ""))
      .finally(() => {
        if (ses.composer === ta) { ta.disabled = !canSend; btn.disabled = !canSend; ta.focus(); }
      });
  };
  // --- the "/" command menu ---
  const menu = el("div", "cmenu");
  menu.hidden = true;
  let mItems = [], mSel = 0;

  const cmdsOnce = () => {
    if (!ses.cmds)
      ses.cmds = fetch("/api/session/" + encodeURIComponent(S.cur) + "/commands")
        .then(r => r.ok ? r.json() : [])
        .catch(() => []);
    return ses.cmds;
  };
  // the current "/" token being completed, or null when the menu shouldn't
  // show (no leading slash, or whitespace = arguments underway)
  const token = () => {
    const v = ta.value;
    if (!v.startsWith("/")) return null;
    const head = v.slice(1);
    return /\s/.test(head) ? null : head;
  };
  const closeMenu = () => { menu.hidden = true; mItems = []; };
  const complete = (c) => {
    ta.value = "/" + c.name + " ";
    closeMenu();
    autoGrow(ta);
    ta.focus();
  };
  const renderMenu = (list) => {
    mItems = list.slice(0, 30);
    if (!mItems.length) { closeMenu(); return; }
    mSel = Math.min(mSel, mItems.length - 1);
    menu.textContent = "";
    mItems.forEach((c, i) => {
      const row = el("div", "cmi" + (i === mSel ? " sel" : ""));
      row.append(el("span", "cmname", "/" + c.name));
      if (c.desc) row.append(el("span", "cmdesc", c.desc));
      if (c.src && c.src !== "built-in") row.append(el("span", "cmsrc", c.src));
      row.onmousedown = (e) => { e.preventDefault(); complete(c); };
      menu.append(row);
    });
    menu.hidden = false;
    if (menu.children[mSel]) menu.children[mSel].scrollIntoView({ block: "nearest" });
  };
  const refreshMenu = () => {
    const tok = token();
    if (tok === null) { closeMenu(); return; }
    cmdsOnce().then(cmds => {
      if (ses.composer !== ta || token() !== tok) return;   // view/input moved on
      mSel = 0;
      const q = tok.toLowerCase();
      renderMenu(cmds.filter(c => c.name.toLowerCase().startsWith(q)));
    });
  };

  ta.oninput = () => { autoGrow(ta); refreshMenu(); };
  ta.onblur = () => setTimeout(closeMenu, 150);   // menu clicks preventDefault, so
  //                                                 they never blur; this catches
  //                                                 clicking elsewhere on the page
  ta.onkeydown = (e) => {
    if (!menu.hidden && mItems.length) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        mSel = (mSel + (e.key === "ArrowDown" ? 1 : mItems.length - 1)) % mItems.length;
        renderMenu(mItems);
        return;
      }
      if (e.key === "Tab") { e.preventDefault(); complete(mItems[mSel]); return; }
      if (e.key === "Escape") { e.stopPropagation(); closeMenu(); return; }
      if (e.key === "Enter" && !e.shiftKey) {
        if (token() !== mItems[mSel].name) {      // partial token: complete first
          e.preventDefault();
          complete(mItems[mSel]);
          return;
        }
        closeMenu();                              // exact token: fall through to send
      }
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  };
  btn.onclick = send;
  wrap.append(ta, btn, menu);
  return wrap;
}

/* ---------- jump to a freshly launched session ---------- */
// A web launch can't know its session id up front — the server deliberately
// returns no synthetic row; the session appears through its own SessionStart.
// So the launch stashes the sids we already know and every following global
// snapshot is checked for a NEW live row in the launched cwd — the first match
// navigates there. Cancelled when the user opens any session themselves
// (route() clears the watch on user navigation) or by the timeout: a launch
// that never produces a session (claude failed to start) must not yank the
// browser somewhere minutes later.
const JUMP_TIMEOUT_MS = 120000;

function armJump(cwd) {
  S.jump = { cwd, known: new Set(S.sessions.map(r => r.sid)),
             until: Date.now() + JUMP_TIMEOUT_MS };
}

function checkJump() {
  const j = S.jump;
  if (!j) return;
  if (Date.now() > j.until) { S.jump = null; return; }
  const row = S.sessions.find(r => r.live && r.cwd === j.cwd && !j.known.has(r.sid));
  if (!row) return;
  S.jump = null;                       // clear FIRST — route() treats an armed
  //                                      watch on a session hash as user intent
  location.hash = "#/s/" + encodeURIComponent(row.sid);
  toast("done", "session started", row.title || proj(row));
}

/* ---------- control plane: the new-session form ---------- */
// Lives in the persistent #modal host (outside #view) so a list re-render from
// an SSE snapshot never blows away a half-typed form. Directory input backed by
// a <datalist> of the distinct cwds in the current snapshot; optional first
// prompt; submit POSTs /api/sessions/new and the session appears on its own via
// SessionStart. The header "+ session" button opens it blank; a dir group's "+"
// prefills that cwd.

function closeNewSession() {
  $modal.hidden = true;
  $modal.textContent = "";
}

function openNewSession(prefillCwd) {
  $modal.textContent = "";
  const panel = el("div", "nspanel");
  panel.append(el("div", "nstitle", "new session"));

  const dirRow = el("label", "nsfield");
  dirRow.append(el("span", "nslabel", "directory"));
  const dir = el("input", "nsinput");
  dir.type = "text";
  dir.spellcheck = false;
  dir.placeholder = "/path/to/project";
  dir.value = prefillCwd || "";
  dir.setAttribute("list", "ns-cwds");
  const dl = el("datalist");
  dl.id = "ns-cwds";
  for (const cwd of [...new Set(S.sessions.map(r => r.cwd).filter(Boolean))]) {
    const opt = el("option");
    opt.value = cwd;
    dl.append(opt);
  }
  dirRow.append(dir, dl);

  // model + effort side by side; "" = the CLI's own default (no flag sent)
  const pick = (label, opts) => {
    const row = el("label", "nsfield");
    row.append(el("span", "nslabel", label));
    const sel = el("select", "nsinput");
    for (const [v, txt] of opts) {
      const o = el("option", "", txt);
      o.value = v;
      sel.append(o);
    }
    row.append(sel);
    return [row, sel];
  };
  const [modelRow, model] = pick("model", [
    ["", "default"], ["fable", "fable"], ["opus", "opus"],
    ["sonnet", "sonnet"], ["haiku", "haiku"],
  ]);
  const [effortRow, effort] = pick("effort", [
    ["", "default"], ["low", "low"], ["medium", "medium"],
    ["high", "high"], ["xhigh", "xhigh"], ["max", "max"],
  ]);
  const split = el("div", "nssplit");
  split.append(modelRow, effortRow);

  const promptRow = el("label", "nsfield");
  promptRow.append(el("span", "nslabel", "first prompt (optional)"));
  const prompt = el("textarea", "nsinput nsprompt");
  prompt.rows = 3;
  prompt.spellcheck = false;
  prompt.placeholder = "what should Claude start on?";
  promptRow.append(prompt);

  const actions = el("div", "nsactions");
  const cancel = el("button", "nsbtn", "cancel");
  const submit = el("button", "nsbtn primary", "launch");
  actions.append(cancel, submit);

  const go = () => {
    const cwd = dir.value.trim();
    if (!cwd) { dir.focus(); return; }
    submit.disabled = true;
    const body = { cwd };
    if (model.value) body.model = model.value;
    if (effort.value) body.effort = effort.value;
    if (prompt.value.trim()) body.prompt = prompt.value.trim();
    postJSON("/api/sessions/new", body)
      .then(() => { armJump(cwd); closeNewSession(); toast("done", "launching…", cwd); })
      .catch(e => { submit.disabled = false; toast("ask", "launch failed", (e && e.error) || ""); });
  };
  submit.onclick = go;
  cancel.onclick = closeNewSession;
  dir.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } };

  panel.append(dirRow, split, promptRow, actions);
  const back = el("div", "nsback");
  back.onclick = (e) => { if (e.target === back) closeNewSession(); };
  back.append(panel);
  $modal.append(back);
  $modal.hidden = false;
  dir.focus();
}

$newbtn.onclick = () => openNewSession("");
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$modal.hidden) closeNewSession();
});

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
  l1.append(el("span", "proj",
               meta.title || (meta.cwd ? proj(meta) : shortSid(S.cur))));
  const badge = el("span", "badge");
  ses.badge = badge;
  setBadge(badge, meta.tab || "");
  l1.append(badge);
  l1.append(el("span", "chip2 " + (meta.live ? "live" : "parked"),
               meta.live ? "live" : "parked"));
  if (meta.cwd) l1.append(el("span", "sid", meta.cwd));
  l1.append(el("span", "sid", shortSid(S.cur)));
  head.append(l1);
  const sr = el("div", "statsrow");
  ses.statsRow = sr;
  head.append(sr);
  const rr = el("div", "runrow");
  ses.runRibbon = rr;
  head.append(rr);
  $view.append(head);
  updateStatsRow();
  updateRunning();

  const tabs = el("div", "tabs");
  const mk = (key, label, count) => {
    const a = el("a", key === tab ? "on" : "");
    a.href = "#/s/" + encodeURIComponent(S.cur) + (key === "mirror" ? "" : "/" + key);
    a.append(document.createTextNode(label));
    if (count) a.append(el("span", "count", String(count)));
    tabs.append(a);
    return a;
  };
  mk("mirror", "mirror");
  mk("activity", "activity");
  mk("agents", "agents", (ses.agents || []).length);
  ses.errTab = mk("errors", "errors", meta.error_count || 0);   // live ⚠ count patches it
  $view.append(tabs);

  const body = el("div");
  ses.body = body;
  $view.append(body);

  if (tab === "mirror") {
    body.append(buildComposer());
    body.append(buildFilterBar());
    const split = el("div", "split");
    split.append(ses.stream);
    const rail = el("div", "rail");
    ses.rail = rail;
    split.append(rail);
    body.append(split);
    updateAgents();
    updateMoreBtn();                      // the load-older affordance at the bottom
    applyFilter();                        // re-filter items already in the stream
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

/* Live ⚠ error badge — the web sibling of the scorebar's errwatch chip
   (count-only on the fast path; full tracebacks stay behind the errors tab).
   Patches the stats-row chip and the errors-tab count in place (no full
   re-render), and re-fetches the errors list only when that tab is open and
   the count grew. */
function updateErrCount(n) {
  const ses = S.ses;
  if (!ses) return;
  const prev = (ses.meta && ses.meta.error_count) || 0;
  if (ses.meta) ses.meta.error_count = n;
  updateStatsRow();
  setTabCount(ses.errTab, n);
  if (ses.tab === "errors" && n > prev && ses.body) renderErrorsInto(ses.body);
}

function setTabCount(a, n) {
  if (!a) return;
  let c = a.querySelector(".count");
  if (n) {
    if (!c) { c = el("span", "count"); a.append(c); }
    c.textContent = String(n);
  } else if (c) {
    c.remove();
  }
}

/* The "running now" ribbon — one chip per alive `live`-table slot row
   (sessionapi.running(), grouped by kind), hidden when nothing is running.
   Live-updated by the `running` SSE event. */
function updateRunning() {
  const ses = S.ses;
  if (!ses || !ses.runRibbon) return;
  const run = ses.running || {};
  const rr = ses.runRibbon;
  rr.textContent = "";
  const kinds = RUN_ORDER.concat(
    Object.keys(run).filter(k => !RUN_ORDER.includes(k)));
  let any = false;
  for (const kind of kinds) {
    const rows = run[kind];
    if (!rows || !rows.length) continue;
    const [glyph, label] = RUN_GLYPH[kind] || ["•", kind];
    for (let i = 0; i < rows.length; i++) {
      any = true;
      const chip = el("span", "rchip rk-" + kind.replace(".", "-"));
      chip.append(el("span", "rg", glyph), document.createTextNode(" " + label));
      rr.append(chip);
    }
  }
  rr.style.display = any ? "" : "none";
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
  const name = a.desc || a.agent_id;      // the Task description IS the name
  card.append(el("div", "aid", (a.kind === "teammate" ? "👥 " : "◇ ") + name));
  if (a.desc) card.append(el("div", "desc", a.agent_id));
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
  closeAgentStream();                       // switching agents / re-entering
  S.ses.tab = "agent:" + aid;
  const ses = S.ses;
  $view.querySelectorAll(".tabs a").forEach(a => a.classList.remove("on"));
  if (ses.body) {
    ses.body.textContent = "";
    const rec = (ses.agents || []).find(a => a.agent_id === aid);
    // a running agent's page grows live; a parked one (ended_at set) is
    // fetch-once — its transcript won't grow, so don't open a stream.
    const live = !!rec && rec.ended_at == null;
    renderTimelineInto(ses.body,
                       "/api/session/" + encodeURIComponent(sid) + "/agent/" + encodeURIComponent(aid),
                       (rec && rec.desc) || aid,
                       live ? { sid: sid, aid: aid } : null);
  }
}

function renderTimelineInto(container, apiUrl, title, live) {
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
    // LIVE agents: resume the SSE at the byte cursor the REST read stopped at
    // (d.pos — additive; absent for a provider with no incremental support,
    // e.g. codex, so the drill-down simply stays fetch-once).
    if (live && d.pos != null) connectAgentStream(live.sid, live.aid, d.pos, list);
  }).catch(() => {
    if (!container.isConnected) return;
    container.textContent = "";
    container.append(el("div", "empty", "no transcript available for " + title));
  });
}

/* The live agent timeline stream: appends new increment `entries` at the
   bottom (the timeline reads chronological top-down) and applies `resolve`
   events — a tool_result that arrived in a later increment than its tool_use —
   by finding the tool entry via its data-tool-id and filling in the result.
   Reconnects (like the per-session stream) resume at the latest byte cursor so
   nothing repeats. */
function connectAgentStream(sid, aid, pos, list) {
  let cur = pos;
  const es = new EventSource("/events/agent/" + encodeURIComponent(sid)
                             + "/" + encodeURIComponent(aid) + "?pos=" + cur);
  S.ses.agentEs = es;
  es.addEventListener("entries", (e) => {
    const d = JSON.parse(e.data);
    if (d.pos != null) cur = d.pos;
    const empty = list.querySelector(".empty");
    if (empty) empty.remove();
    for (const ent of d.entries || []) list.append(timelineEntry(ent));
  });
  es.addEventListener("resolve", (e) => {
    const d = JSON.parse(e.data);
    if (d.pos != null) cur = d.pos;
    for (const r of d.resolutions || []) applyResolution(list, r);
  });
  es.onerror = () => {
    es.close();
    if (!S.ses || S.ses.agentEs !== es) return;   // navigated away
    S.ses.agentEs = null;
    setTimeout(() => {
      if (S.ses && S.ses.tab === "agent:" + aid && list.isConnected)
        connectAgentStream(sid, aid, cur, list);
    }, 1500);
  };
}

// A resolution tuple [tool_use_id, output, failed] fills in a tool entry whose
// tool_result arrived in a later increment; no matching entry (a genuine
// orphan we never rendered) is a no-op.
function applyResolution(list, r) {
  const box = list.querySelector('[data-tool-id="' + cssq(r[0]) + '"]');
  if (!box) return;
  const region = toolResultRegion({ output: r[1], failed: !!r[2] });
  const old = box.querySelector(".tout");
  if (old) old.replaceWith(region);
  else { const bd = box.querySelector(".bd"); if (bd) bd.append(region); }
  const k = box.querySelector(".k");
  if (k && r[2]) { k.classList.remove("k-tool"); k.classList.add("k-toolfail"); }
  box.dataset.open = "1";                   // surface the freshly-arrived result
}

function cssq(s) {
  s = String(s);
  return (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/["\\]/g, "\\$&");
}

function closeAgentStream() {
  if (S.ses && S.ses.agentEs) { S.ses.agentEs.close(); S.ses.agentEs = null; }
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
    bd.append(mdOrPre(ent.html, ent.text));
  } else if (ent.t === "teammsg") {
    kcls = "k-teammsg"; ktxt = "✉ " + (ent.sender || "team");
    sum = firstLine(ent.body); open = false;
    bd.append(mdOrPre(ent.html, ent.body));
  } else if (ent.t === "message") {
    kcls = ent.final ? "k-final" : "k-message";
    ktxt = ent.final ? "result" : "message";
    sum = firstLine(ent.text); open = !!ent.final;
    bd.append(mdOrPre(ent.html, ent.text));
  } else if (ent.t === "compact") {
    kcls = "k-compact"; ktxt = "compact";
    sum = "context compacted"; open = false;
    bd.append(pre(JSON.stringify(ent.meta || {}, null, 2)));
  } else if (ent.t === "tool") {
    kcls = ent.failed ? "k-toolfail" : "k-tool";
    ktxt = ent.tool || "tool";
    sum = firstLine(inputSummary(ent.input)); open = false;
    if (ent.input_html != null) {
      bd.append(el("div", "lbl", "input"));
      bd.append(htmlFrag(ent.input_html));
    } else if (ent.input && Object.keys(ent.input).length) {
      bd.append(el("div", "lbl", "input"));
      bd.append(pre(JSON.stringify(ent.input, null, 2)));
    }
    bd.append(toolResultRegion(ent));
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
  // tool entries carry their tool_use id so a later `resolve` SSE event (a
  // tool_result that landed in a subsequent increment) can find and fill them.
  if (ent.t === "tool" && ent.id) box.dataset.toolId = ent.id;
  hd.onclick = () => { box.dataset.open = box.dataset.open === "1" ? "0" : "1"; };
  box.append(hd, bd);
  return box;
}

// The tool entry's result region (its own .tout block so a `resolve` event can
// replace just it, leaving the input section intact). No output yet -> "no
// result recorded"; the live resolve renders the output as a plain <pre> (the
// lightweight resolution tuple carries no output_html — a rich re-render lands
// on the next full fetch).
function toolResultRegion(ent) {
  const tout = el("div", "tout");
  if (ent.output != null) {
    const lbl = el("div", "lbl", ent.failed ? "output · failed" : "output");
    if (ent.failed) lbl.classList.add("fail");
    tout.append(lbl);
    tout.append(ent.output_html != null ? htmlFrag(ent.output_html) : pre(ent.output));
  } else {
    tout.append(el("div", "lbl", "no result recorded"));
  }
  return tout;
}

function pre(text) { const p = el("pre"); p.textContent = text == null ? "" : String(text); return p; }
// Server-rendered markdown (opshtml.md_html — escaped there, the same
// neutralize() analog as op HTML) is the only thing set via innerHTML here;
// with no `html` field (older provider) fall back to plain textContent.
function mdOrPre(html, text) {
  if (html == null) return pre(text);
  const d = el("div", "md");
  d.innerHTML = html;
  return d;
}
// Server-rendered tool input/output HTML (opshtml.tool_html / tool_output_html
// — escaped there like op HTML). Same innerHTML-is-safe-by-construction basis
// as mdOrPre; a bare wrapper carries the structured blocks (.oc / .tdiff /
// .tdl) without the .md markdown styling.
function htmlFrag(html) {
  const d = el("div", "thtml");
  d.innerHTML = html;
  return d;
}

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
renderAttention();
