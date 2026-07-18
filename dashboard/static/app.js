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
const $accounts = document.getElementById("accounts");

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

// iPad detection for the message boxes' Enter behavior. Since iPadOS 13
// Safari masquerades as desktop Safari — identical User-Agent, "MacIntel"
// platform — so the ONE tell left is touch: Macs report 0 maxTouchPoints,
// iPads 5. (The /iPad/ UA test still catches the non-default "Request
// Mobile Website" mode.) On an iPad the on-screen keyboard's return key is
// the only Enter there is, so Enter must insert a newline and the send
// button is the sole way to send; a hardware keyboard follows the same rule
// for consistency. Detection is client-side by necessity — the server never
// sees a distinguishing header (Safari sends no UA client hints).
const IS_IPAD = /iPad/.test(navigator.userAgent)
  || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

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
// "claude-opus-4-8" → "opus-4.8" — display twin of model.short_model (the
// Python side is the authority; this only styles the model button's label):
// drop "claude-", join short numeric version parts with ".", skip 8-digit
// date suffixes, drop "[1m]".
function shortModel(m) {
  let s = String(m || "").toLowerCase().replace("[1m]", "").trim();
  if (!s) return "";
  if (s.startsWith("claude-")) s = s.slice(7);
  const parts = s.split("-");
  const ver = [];
  for (const p of parts.slice(1)) {
    if (/^\d{1,2}$/.test(p)) ver.push(p);
    else break;
  }
  return parts[0] + (ver.length ? "-" + ver.join(".") : "");
}
function copySid(sid) {
  navigator.clipboard.writeText(sid).then(
    () => toast("done", "copied session id", sid),
    () => toast("ask", "copy failed", "clipboard permission?"));
}

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

/* ---------- account usage strip (top of every page) ---------- */
// A slim strip under the header showing each subscription account's latest
// 5-hour / 7-day rate-limit usage (GET /api/accounts — aggregated per account
// from the status-line capture, docs/dashboard.md). Polled on a slow timer
// (usage moves slowly, and it's ambient); hidden entirely when no account has
// any usage captured yet. The default account is labeled "default"; others by
// their switcher label (c2 · claude-01).

const ACCOUNTS_POLL_MS = 60000;

function usagePct(u, key) {
  const v = u && u[key];
  return typeof v === "number" ? v : null;
}

// Effective 5h-used % for the new-session form's load-balancing default: a
// snapshot whose reset time has passed (or, when resets_at is unknown, one
// older than the 5h window itself) means the window rolled over → 0 used;
// an account with no snapshot at all has had no recent traffic → also 0.
function fiveHourUsed(a) {
  const u = a.usage, pct = usagePct(u, "five_hour");
  if (pct == null) return 0;
  const now = Date.now() / 1000;
  const rolled = u.five_hour_reset ? u.five_hour_reset <= now
                                   : u.ts && now - u.ts > 5 * 3600;
  return rolled ? 0 : pct;
}

function acctPill(a) {
  const u = a.usage;
  const pill = el("div", "acct");
  const name = a.slug ? a.slug + " · " + a.label : a.label;
  pill.append(el("span", "aname", name));
  const fh = usagePct(u, "five_hour"), sd = usagePct(u, "seven_day");
  if (fh == null && sd == null) {
    pill.append(el("span", "adim", "no usage yet"));
    return pill;
  }
  const bar = (label, pct, resetKey) => {
    const seg = el("span", "ubar" + (pct >= 90 ? " hot" : pct >= 70 ? " warn" : ""));
    seg.append(el("span", "ulabel", label));
    const track = el("span", "utrack");
    const fill = el("span", "ufill");
    fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
    track.append(fill);
    seg.append(track, el("span", "upct", pct + "%"));
    const reset = u && u[resetKey];
    if (reset) seg.append(el("span", "ureset", "resets " + resetAgo(reset)));
    return seg;
  };
  if (fh != null) pill.append(bar("5h", fh, "five_hour_reset"));
  if (sd != null) pill.append(bar("7d", sd, "seven_day_reset"));
  return pill;
}

function resetAgo(epochS) {
  const s = epochS - Date.now() / 1000;
  if (s <= 0) return "now";
  if (s < 60) return "in <1m";
  const d = s / 86400 | 0, h = s % 86400 / 3600 | 0, m = s % 3600 / 60 | 0;
  const parts = d ? [d + "d", h + "h"] : h ? [h + "h", m + "m"] : [m + "m"];
  return "in " + parts.filter(p => !p.startsWith("0")).join(" ");
}

function renderAccounts(list) {
  if (!$accounts) return;
  const withUsage = (list || []).filter(a => a.usage);
  $accounts.hidden = !withUsage.length;
  $accounts.textContent = "";
  for (const a of withUsage) $accounts.append(acctPill(a));
}

function refreshAccounts() {
  fetch("/api/accounts").then(r => r.json())
    .then(renderAccounts).catch(() => {});
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
    const t2 = d.title || (asking ? "Claude is asking a question" : "finished — your turn");
    toast(asking ? "ask" : "done", t1, t2, () => { location.hash = "#/s/" + d.sid; });
    osNotify(t1, t2, d.sid);
  });
  // hello carries the server's boot id: the EventSource reconnects on a
  // server restart, and a CHANGED boot id means this open page's JS may be
  // stale (a redeploy happened underneath) — twice a stale open page ran old
  // handlers against a new server and the mismatch read as a product bug.
  es.addEventListener("hello", (e) => {
    const boot = (JSON.parse(e.data) || {}).boot;
    if (!S.boot) { S.boot = boot; return; }
    if (boot !== S.boot) {
      S.boot = boot;
      toast("ask", "dashboard updated",
            "refresh the page to load the latest UI",
            () => location.reload());
    }
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
  a.dataset.tab = row.tab || "";        // state tint (style.css --state wash)
  a.href = "#/s/" + encodeURIComponent(row.sid);
  const st = row.stats || {};
  a.append(el("div", "proj", row.title || proj(row)));
  a.append(el("div", "sid", row.sid));
  // no "live" chip — the state tint + badge already say it; only the
  // inactive states (parked/gone) need explaining. A live windowed session
  // gets the ✕ close in the same corner slot instead — the header's close
  // reachable straight from the list.
  const corner = el("div", "corner");
  if (!row.live)
    corner.append(el("span", "chip2 parked", row.parked ? "parked" : "gone"));
  else if (row.kitty_window_id)
    corner.append(cardClose(row.sid));
  if (corner.childNodes.length) a.append(corner);
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
  if (row.git) r.append(gitChip(row.git));
  a.append(r);
  if (row.ctx) a.append(ctxBar(row.ctx));
  return a;
}
// the list-card ✕ — POST /api/session/<sid>/stop, the same graceful tab
// close as the session header's "✕ close" (kitty HUPs the tab, Claude Code
// exits, SessionEnd parks the mirror), with the same two-step confirm: first
// click arms ("close?") for 4 s, second fires. Lives inside the card <a>, so
// clicks must not bubble into a navigation. No hash change on success — the
// card demotes to parked on its own via the SSE sessions push.
function cardClose(sid) {
  const btn = el("button", "xclose", "✕");
  btn.title = "close this session's terminal tab";
  let armed = null;
  const disarm = () => {
    armed = null;
    btn.textContent = "✕";
    btn.classList.remove("arm");
  };
  btn.onclick = (e) => {
    e.preventDefault(); e.stopPropagation();
    if (!armed) {
      btn.textContent = "close?";
      btn.classList.add("arm");
      armed = setTimeout(disarm, 4000);
      return;
    }
    clearTimeout(armed);
    disarm();
    btn.disabled = true;
    postJSON("/api/session/" + encodeURIComponent(sid) + "/stop", {})
      .then(() => toast("done", "session closed", "terminal tab closed"))
      .catch(err => {
        btn.disabled = false;
        toast("ask", "close failed", (err && err.error) || "");
      });
  };
  return btn;
}

function seg(text) { const s = el("span"); s.append(el("span", "v", text)); return s; }
function segc(text, cls) { const s = el("span"); s.append(el("span", cls, text)); return s; }
// git chip — "⎇ branch" plus "⋔ worktree" when the session's checkout is a
// linked worktree (git worktree add / EnterWorktree). A trailing "*" marks
// uncommitted changes, the status-line convention (dirty is true/false/null —
// null = unknown, no marker). Fill an existing span (the header's live chip)
// or make one (session cards).
function setGitChip(chip, g) {
  chip.textContent = "";
  chip.hidden = !g;
  if (!g) return;
  chip.append(el("span", "gb", "⎇ " + g.branch + (g.dirty ? "*" : "")));
  if (g.worktree) chip.append(el("span", "gw", "⋔ " + g.worktree));
}
function gitChip(g) {
  const s = el("span", "gitchip");
  setGitChip(s, g);
  return s;
}

// context-saturation bar — the account-limit bar's (acctPill's ubar) bigger
// sibling, one full row wherever it appears: session cards, the session
// header (big=true), agent cards. Accent fill, amber ≥70%, red ≥90%.
function ctxBar(cx, big) {
  const bar = el("div", "cbar" + (cx.pct >= 90 ? " hot" : cx.pct >= 70 ? " warn" : "")
                        + (big ? " big" : ""));
  bar.append(el("span", "clabel", "ctx"));
  const track = el("span", "ctrack");
  const fill = el("span", "cfill");
  fill.style.width = Math.max(0, Math.min(100, cx.pct)) + "%";
  track.append(fill);
  bar.append(track, el("span", "cpct", cx.pct + "%"));
  bar.append(el("span", "cdetail", kfmt(cx.used) + " / " + kfmt(cx.window)));
  return bar;
}

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
              agents: [], costs: null, ctx: null, running: {}, meta: null, es: null, agentEs: null,
              timer: null, poll: null, blocks: new Map(), moreEl: null,
              loadingOlder: false, queue: [],
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
        S.ses.ctx = d.ctx || null;
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
  es.addEventListener("ctx", (e) => { S.ses.ctx = JSON.parse(e.data).ctx; updateStatsRow(); });
  es.addEventListener("git", (e) => {
    const g = JSON.parse(e.data).git || null;
    if (!S.ses) return;
    if (S.ses.meta) S.ses.meta.git = g;
    if (S.ses.gitChip) setGitChip(S.ses.gitChip, g);
  });
  es.addEventListener("title", (e) => {
    // a web rename or a fresh auto ai-title — retitle the header in place,
    // but never clobber an inline rename edit in progress
    const t = JSON.parse(e.data).title || "";
    if (!S.ses) return;
    if (S.ses.meta) S.ses.meta.title = t;
    if (t && S.ses.projEl && !S.ses.projEl.querySelector("input"))
      S.ses.projEl.textContent = t;
  });
  es.addEventListener("effort", (e) => {
    if (S.ses && S.ses.meta) {
      S.ses.meta.effort = JSON.parse(e.data).effort;
      if (S.ses.effortBtn) setEffortBtn(S.ses.effortBtn);
    }
  });
  es.addEventListener("running", (e) => { S.ses.running = JSON.parse(e.data); updateRunning(); });
  es.addEventListener("errors", (e) => { updateErrCount(JSON.parse(e.data).count | 0); });
  es.addEventListener("ask", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    if (S.ses.meta) S.ses.meta.ask = d.ask || null;
    renderAsk();
  });
  es.addEventListener("plan", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    if (S.ses.meta) S.ses.meta.plan = d.plan || null;
    renderPlan();
  });
  es.addEventListener("tab", (e) => {
    const d = JSON.parse(e.data);
    if (S.ses && S.ses.badge) setBadge(S.ses.badge, d.tab || "");
    if (S.ses && S.ses.composerMode) S.ses.composerMode(d.tab || "");
    if (S.ses && S.ses.cancelMode) S.ses.cancelMode(d.tab || "");
    if (S.ses && S.ses.quickMode) S.ses.quickMode(d.tab || "");
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
  drainQueue(items);
  enforceWindow();
  while (st.childElementCount > 3000) {
    let last = st.lastElementChild;
    if (last === S.ses.moreEl) last = last.previousElementSibling;  // the load-older
    if (!last) break;                          //   affordance stays pinned at the bottom
    if (last.classList.contains("blk"))        // evict a trimmed block card, or later
      for (const [g, b] of S.ses.blocks)       //   ops for its group would render into
        if (b.root === last) { S.ses.blocks.delete(g); break; }   // a detached node
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

/* ---------- the "/" command menu (composer + new-session prompt) ---------- */
// Claude-Code-style completion: a leading "/" with no whitespace yet opens a
// menu over GET /api/commands?cwd=… (built-ins + that directory's .claude
// commands/skills). ↑/↓ move, Tab completes, Esc closes; Enter completes —
// except with {enterSends: true} an EXACT token falls through to the caller's
// send (so a fully-typed "/compact" sends on one Enter; both boxes pass
// !IS_IPAD, since on an iPad Enter never sends). The TUI stays
// authoritative — sending just types the command into the terminal and Claude
// Code's own palette executes it. The menu drops BELOW its host box (never up
// over the stats row); `host` must be position:relative.
// Wiring contract: the helper listens to input/blur itself; the caller keeps
// its own oninput (autoGrow) and calls sm.key(e) FIRST in onkeydown — a true
// return means the menu consumed the key.

function cmdsFor(cwd, cache, key) {
  if (!cache[key])
    cache[key] = fetch("/api/commands?cwd=" + encodeURIComponent(cwd || ""))
      .then(r => r.ok ? r.json() : [])
      .catch(() => []);
  return cache[key];
}

function slashMenu(ta, host, getCmds, opts) {
  const enterSends = !!(opts && opts.enterSends);
  const menu = el("div", "cmenu");
  menu.hidden = true;
  host.append(menu);
  let items = [], sel = 0;

  // the "/" token being completed, or null when the menu shouldn't show
  // (no leading slash, or whitespace = arguments underway)
  const token = () => {
    const v = ta.value;
    if (!v.startsWith("/")) return null;
    const head = v.slice(1);
    return /\s/.test(head) ? null : head;
  };
  const close = () => { menu.hidden = true; items = []; };
  const complete = (c) => {
    ta.value = "/" + c.name + " ";
    close();
    ta.focus();
    ta.dispatchEvent(new Event("input"));   // caller's autoGrow, if any
  };
  const render = () => {
    menu.textContent = "";
    items.forEach((c, i) => {
      const row = el("div", "cmi" + (i === sel ? " sel" : ""));
      row.append(el("span", "cmname", "/" + c.name));
      if (c.desc) row.append(el("span", "cmdesc", c.desc));
      if (c.src && c.src !== "built-in") row.append(el("span", "cmsrc", c.src));
      row.onmousedown = (e) => { e.preventDefault(); complete(c); };
      menu.append(row);
    });
    menu.hidden = !items.length;
    if (items.length && menu.children[sel])
      menu.children[sel].scrollIntoView({ block: "nearest" });
  };
  const refresh = () => {
    const tok = token();
    if (tok === null) { close(); return; }
    getCmds().then(cmds => {
      if (!ta.isConnected || token() !== tok) return;   // view/input moved on
      sel = 0;
      const q = tok.toLowerCase();
      items = cmds.filter(c => c.name.toLowerCase().startsWith(q)).slice(0, 30);
      render();
    });
  };
  const key = (e) => {
    if (menu.hidden || !items.length) return false;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      sel = (sel + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
      render();
      return true;
    }
    if (e.key === "Tab") { e.preventDefault(); complete(items[sel]); return true; }
    if (e.key === "Escape") { e.stopPropagation(); close(); return true; }
    if (e.key === "Enter" && !e.shiftKey) {
      if (enterSends && token() === items[sel].name) {
        close();                            // exact token: fall through to send
        return false;
      }
      e.preventDefault();
      complete(items[sel]);
      return true;
    }
    return false;
  };
  ta.addEventListener("input", refresh);
  ta.addEventListener("blur", () => setTimeout(close, 150));   // menu clicks
  //                     preventDefault (never blur); this catches clicks away
  return { key };
}

/* ---------- queued messages (Claude Code's mid-turn queue) ---------- */
// Claude Code natively QUEUES a message typed while a turn is running and
// delivers it when the turn ends — the composer rides exactly that (send_text
// types into the TUI either way). The /message response says which happened
// (`queued: true` when the send landed mid-turn — the server's QUEUE_TABS
// verdict is the authority; the client QUEUE_TABS below only styles the send
// button). A queued message would otherwise VANISH from the page until
// delivery (it reaches the transcript only when the turn ends), so it shows
// as a ⧗ chip under the composer until its prompt record actually arrives in
// the stream (drainQueue — matched by exact text; tab transitions are useless
// as a delivery signal since green flips busy again the instant a queued
// prompt starts processing). ✕ only hides a chip — the message is already in
// the TUI's queue and the web can't unqueue it.

const QUEUE_TABS = ["thinking", "working", "executing"];

function buildQueueBar() {
  const q = el("div", "cqueue");
  S.ses.queueEl = q;
  renderQueue();
  return q;
}

function renderQueue() {
  const ses = S.ses;
  if (!ses || !ses.queueEl) return;
  const q = ses.queueEl;
  q.textContent = "";
  q.hidden = !ses.queue.length;
  ses.queue.forEach((m, i) => {
    const c = el("span", "qchip");
    c.title = "queued in the terminal — delivers when this turn ends";
    c.append(el("span", "qg", "⧗"), document.createTextNode(
      m.text.length > 70 ? m.text.slice(0, 70) + "…" : m.text));
    const x = el("button", "qx", "✕");
    x.title = "hide this chip (the message stays queued in the terminal)";
    x.onclick = () => { ses.queue.splice(i, 1); renderQueue(); };
    c.append(x);
    q.append(c);
  });
}

function drainQueue(items) {
  const ses = S.ses;
  if (!ses || !ses.queue || !ses.queue.length) return;
  let hit = false;
  for (const it of items) {
    if (it.t !== "msg" || it.kind !== "prompt") continue;
    const i = ses.queue.findIndex(m => m.text === (it.text || "").trim());
    if (i >= 0) { ses.queue.splice(i, 1); hit = true; }
  }
  if (hit) renderQueue();
}

/* ---------- the ask card (AskUserQuestion from the web) ---------- */
// While Claude's question dialog is up in the terminal, the session SSE
// carries the pending ask (the PreToolUse stash — plugins/claude_code/
// ask_fmt.py) and this card mirrors it above the composer: option buttons
// (radio marks + "pick one" for single-select, checkbox marks + "pick any"
// for multiSelect — visually distinct so the mode is legible at a glance),
// a free-text "type your own" per question (the dialog's "Type something"
// row), a submit row (ALWAYS explicit — no auto-submit on a lone
// single-select click; the web card favors review-before-send over the
// TUI's one-keystroke feel), and "chat about this" (the dialog's own
// decline-and-discuss).
// Answers POST /answer, where the server drives the REAL dialog with
// screen-verified key events (dashboard/askdialog.py). The card clears via
// the SSE `ask` event when the answer's PostToolUse drops the stash.

function buildAskCard() {
  const wrap = el("div", "askwrap");
  S.ses.askEl = wrap;
  renderAsk();
  return wrap;
}

function renderAsk() {
  const ses = S.ses;
  if (!ses || !ses.askEl) return;
  const wrap = ses.askEl;
  wrap.textContent = "";
  const ask = ses.meta && ses.meta.ask;
  wrap.hidden = !ask;
  if (!ask) return;
  const qs = ask.questions || [];
  // per-ask draft state, keyed by tool_use_id so a NEW ask resets it
  if (!ses.askState || ses.askState.id !== ask.tool_use_id)
    ses.askState = { id: ask.tool_use_id,
                     answers: qs.map(() => ({ selected: [], other: "" })) };
  const st = ses.askState;
  const card = el("div", "askcard");
  const head = el("div", "askhead");
  head.append(el("span", "asktitle",
                 "claude is asking" + (qs.length > 1 ? " — " + qs.length + " questions" : "")));
  const chatB = el("button", "askchat", "chat about this");
  chatB.title = "dismiss the questions and discuss in the chat instead";
  chatB.onclick = () => submitAsk(ask, null, true);
  head.append(chatB);
  card.append(head);
  const sub = el("button", "asksubmit",
                 qs.length > 1 ? "submit answers" : "submit answer");
  const syncSubmit = () => {
    sub.disabled = !st.answers.every(a => a.selected.length || a.other.trim());
  };
  qs.forEach((q, qi) => {
    const qbox = el("div", "askq");
    const qhead = el("div", "askqhead");
    if (q.header) qhead.append(el("span", "askhdr", q.header));
    qhead.append(el("span", "askqtext", q.question || ""));
    qhead.append(el("span", "askpick" + (q.multiSelect ? " multi" : ""),
                    q.multiSelect ? "pick any" : "pick one"));
    qbox.append(qhead);
    const opts = el("div", "askopts" + (q.multiSelect ? " multi" : ""));
    const paintAll = () => [...opts.children].forEach(c =>
      c.classList.toggle("on", st.answers[qi].selected.includes(c.dataset.label)));
    (q.options || []).forEach(o => {
      const b = el("button", "askopt");
      b.dataset.label = o.label || "";
      b.append(el("span", "amark"));
      const txt = el("span", "aotxt");
      txt.append(el("span", "aol", o.label || ""));
      if (o.description) txt.append(el("span", "aod", o.description));
      b.append(txt);
      b.onclick = () => {
        const a = st.answers[qi];
        if (q.multiSelect) {
          a.selected = a.selected.includes(o.label)
            ? a.selected.filter(x => x !== o.label)
            : [...a.selected, o.label];
        } else {
          a.selected = [o.label];
          a.other = "";
          if (other) other.value = "";
        }
        paintAll();
        syncSubmit();
      };
      opts.append(b);
    });
    qbox.append(opts);
    const other = el("input", "askother");
    other.type = "text";
    other.spellcheck = false;
    other.placeholder = q.multiSelect
      ? "add your own answer…" : "or type your own answer…";
    other.value = st.answers[qi].other;
    other.oninput = () => {
      st.answers[qi].other = other.value;
      if (!q.multiSelect && other.value.trim()) {
        st.answers[qi].selected = [];
        paintAll();
      }
      syncSubmit();
    };
    other.onkeydown = (e) => {
      e.stopPropagation();                  // keep Esc/gestures out of typing
      if (e.key === "Enter" && other.value.trim() && !sub.disabled)
        submitAsk(ask, st.answers, false);
    };
    qbox.append(other);
    paintAll();
    card.append(qbox);
  });
  const foot = el("div", "askfoot");
  foot.append(sub);
  sub.onclick = () => submitAsk(ask, st.answers, false);
  card.append(foot);
  syncSubmit();
  wrap.append(card);
}

function submitAsk(ask, answers, chat) {
  const ses = S.ses;
  if (!ses || !S.cur) return;
  const body = { tool_use_id: ask.tool_use_id || "" };
  if (chat) body.chat = true;
  else body.answers = (answers || []).map(a =>
    ({ selected: a.selected, other: (a.other || "").trim() }));
  if (ses.askEl)
    ses.askEl.querySelectorAll("button,input").forEach(x => x.disabled = true);
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/answer", body)
    .then(() => {
      if (chat) {
        toast("done", "over to chat",
              "questions dismissed — type your message below");
        if (ses.composer) ses.composer.focus();
      } else {
        toast("done", "answered", "answers submitted to the session");
      }
      // optimistic hide — the SSE `ask` event (stash cleared by the answer's
      // PostToolUse) is the real confirmation
      if (ses.meta) ses.meta.ask = null;
      renderAsk();
    })
    .catch(e => {
      toast("ask", "answer failed", (e && e.error) || "");
      renderAsk();                           // re-enable for a retry
    });
}

/* ---------- the plan card (ExitPlanMode approval from the web) ---------- */
// While Claude's plan-approval dialog is up in the terminal, the session SSE
// carries the pending plan (the PreToolUse stash — plan markdown rendered
// server-side as plan_html) and this card mirrors it above the composer.
// The DECISION buttons come from the live screen (POST /plan-options —
// their labels vary with the session's permission mode), a feedback box
// mirrors the dialog's "Tell Claude what to change" row, and "keep planning"
// is the dialog's own Esc. Decisions POST /plan-decision, where the server
// drives the real dialog screen-verified (dashboard/plandialog.py).

function buildPlanCard() {
  const wrap = el("div", "planwrap");
  S.ses.planEl = wrap;
  renderPlan();
  return wrap;
}

function renderPlan() {
  const ses = S.ses;
  if (!ses || !ses.planEl) return;
  const wrap = ses.planEl;
  wrap.textContent = "";
  const plan = ses.meta && ses.meta.plan;
  wrap.hidden = !plan;
  if (!plan) return;
  const card = el("div", "plancard");
  const head = el("div", "askhead");
  head.append(el("span", "plantitle", "claude has a plan — proceed?"));
  const dis = el("button", "askchat", "keep planning");
  dis.title = "reject the plan and stay in plan mode (the dialog's Esc)";
  dis.onclick = () => submitPlan(plan, { dismiss: true }, "plan dismissed",
                                 "Claude keeps planning");
  head.append(dis);
  card.append(head);
  const body = el("div", "planbody md");
  body.innerHTML = plan.plan_html || "";
  card.append(body);
  const btns = el("div", "planbtns");
  btns.append(el("span", "plandim", "loading options…"));
  card.append(btns);
  const fb = el("div", "planfb");
  const fbIn = el("input", "askother");
  fbIn.type = "text";
  fbIn.spellcheck = false;
  fbIn.placeholder = "tell Claude what to change…";
  fbIn.onkeydown = (e) => {
    e.stopPropagation();
    if (e.key === "Enter" && fbIn.value.trim())
      submitPlan(plan, { feedback: fbIn.value.trim() }, "feedback sent",
                 "Claude will revise the plan");
  };
  const fbB = el("button", "askchat", "send feedback");
  fbB.onclick = () => {
    if (fbIn.value.trim())
      submitPlan(plan, { feedback: fbIn.value.trim() }, "feedback sent",
                 "Claude will revise the plan");
  };
  fb.append(fbIn, fbB);
  card.append(fb);
  wrap.append(card);
  // the decision buttons come from the LIVE dialog (labels vary with the
  // session's permission mode) — fetched once per card render
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/plan-options",
           { tool_use_id: plan.tool_use_id || "" })
    .then(r => {
      btns.textContent = "";
      (r.options || []).forEach(o => {
        if (o.feedback) return;            // the feedback row is the box above
        const b = el("button", "planopt", o.label);
        b.onclick = () => submitPlan(plan, { digit: o.digit, label: o.label },
                                     "decided", o.label);
        btns.append(b);
      });
    })
    .catch(e => {
      btns.textContent = "";
      btns.append(el("span", "plandim",
                     "options unavailable — " + ((e && e.error) || "") +
                     " (decide in the terminal, or send feedback below)"));
    });
}

function submitPlan(plan, body, okTitle, okDetail) {
  const ses = S.ses;
  if (!ses || !S.cur) return;
  body.tool_use_id = plan.tool_use_id || "";
  if (ses.planEl)
    ses.planEl.querySelectorAll("button,input").forEach(x => x.disabled = true);
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/plan-decision", body)
    .then(() => {
      toast("done", okTitle, okDetail);
      if (ses.meta) ses.meta.plan = null;   // optimistic — SSE confirms
      renderPlan();
    })
    .catch(e => {
      toast("ask", "plan decision failed", (e && e.error) || "");
      renderPlan();                          // re-enable for a retry
    });
}

/* ---------- control plane: the message composer ---------- */
// A textarea above the mirror feed that types a message into the session's
// kitty window (POST /message). Enter sends, Shift+Enter is a newline — except
// on an iPad (IS_IPAD), where Enter is a newline and only the button sends. Disabled
// with a hint when the session isn't live or has no window (a headless/daemon
// session — the /message endpoint would 409). The sent text surfaces in the
// stream on its own via the conversation tail, so we only clear + toast —
// unless the response says it QUEUED (see above), which adds a ⧗ chip.

// Both message boxes (the composer and the form's first prompt) grow with
// their content, capped at a viewport fraction so a long paste can't swallow
// the page (the CSS max-height mirrors this cap as 40vh).
const GROW_CAP = 0.4;
function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, Math.round(innerHeight * GROW_CAP)) + "px";
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
    ? (IS_IPAD ? "message this session…"
               : "message this session…  (Enter to send · Shift+Enter for newline)")
    : (meta.live ? "no terminal window — can't message a headless session"
                 : "session is not live");
  const btn = el("button", "csend", "send");
  btn.disabled = !canSend;
  ses.composer = ta;
  const send = () => {
    const text = ta.value.trim();
    if (!text || ta.disabled) return;
    ta.disabled = true; btn.disabled = true;
    // after a mid-turn cancel-edit the TUI holds the restored draft, so this
    // edited send must replace it (server: Ctrl+U/K then bracketed paste)
    const msg = { text };
    if (ses.clearDraftNext) { msg.clear_draft = true; ses.clearDraftNext = false; }
    postJSON("/api/session/" + encodeURIComponent(S.cur) + "/message", msg)
      .then(d => {
        ta.value = ""; autoGrow(ta);
        if (d && d.queued) {
          ses.queue.push({ text });
          renderQueue();
          toast("done", "message queued", "delivers when this turn ends");
        } else {
          toast("done", "message sent", "");
        }
      })
      .catch(e => toast("ask", "send failed", (e && e.error) || ""))
      .finally(() => {
        if (ses.composer === ta) { ta.disabled = !canSend; btn.disabled = !canSend; ta.focus(); }
      });
  };
  // cosmetic busy hint: the send button reads "queue" while a turn is running
  // (kept fresh by the `tab` SSE event; the server's verdict stays authoritative)
  ses.composerMode = (tab) => {
    btn.textContent = canSend && QUEUE_TABS.includes(tab) ? "queue" : "send";
  };
  ses.composerMode(((S.sessions.find(r => r.sid === S.cur) || {}).tab)
                   || (meta.tab || ""));
  // the "/" menu — commands for THIS session's cwd, fetched once per view
  const sm = slashMenu(ta, wrap,
    () => cmdsFor(meta.cwd, ses, "cmds"),
    { enterSends: !IS_IPAD });
  ta.oninput = () => autoGrow(ta);
  ta.onkeydown = (e) => {
    if (sm.key(e)) return;
    if (!IS_IPAD && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  };
  btn.onclick = send;
  wrap.append(ta, btn);
  return wrap;
}

/* ---------- jump to a freshly launched session ---------- */
// A web launch can't know its session id up front — the server deliberately
// returns no synthetic row; the session appears through its own SessionStart.
// So the launch stashes what we already know and every following global
// snapshot is checked — the first match navigates there. What counts as the
// launched session depends on the start mode:
//   fresh     — a sid we've never seen, live, in the launched cwd;
//   resume    — THAT sid coming (back) to life: SessionStart fires under the
//               OLD sid and restores its parked DB (the fork to a new sid
//               only happens at the first event after, so "new sid" alone
//               never matches — this shipped broken once);
//   continue  — some already-known sid in that cwd flipping parked→live
//               (which one the CLI picks is its own history's business).
// Hence the liveAtArm set: a hit is a live cwd-row that is either brand-new
// OR wasn't live when we armed. Cancelled when the user opens any session
// themselves (route() clears the watch on user navigation) or by the
// timeout: a launch that never produces a session (claude failed to start)
// must not yank the browser somewhere minutes later.
const JUMP_TIMEOUT_MS = 120000;

function armJump(cwd, resumeSid) {
  S.jump = { cwd, resumeSid: resumeSid || "",
             known: new Set(S.sessions.map(r => r.sid)),
             liveAtArm: new Set(S.sessions.filter(r => r.live).map(r => r.sid)),
             until: Date.now() + JUMP_TIMEOUT_MS };
}

function checkJump() {
  const j = S.jump;
  if (!j) return;
  if (Date.now() > j.until) { S.jump = null; return; }
  // the resumed sid itself wins (its cwd may differ from the launch dir);
  // otherwise any cwd-row that is brand-new or freshly parked→live
  const row = (j.resumeSid
               && S.sessions.find(r => r.live && r.sid === j.resumeSid))
    || S.sessions.find(r => r.live && r.cwd === j.cwd
                       && (!j.known.has(r.sid) || !j.liveAtArm.has(r.sid)));
  if (!row) return;
  S.jump = null;                       // clear FIRST — route() treats an armed
  //                                      watch on a session hash as user intent
  location.hash = "#/s/" + encodeURIComponent(row.sid);
  toast("done", "session started", row.title || proj(row));
}

/* ---------- control plane: the new-session form ---------- */
// Lives in the persistent #modal host (outside #view) so a list re-render from
// an SSE snapshot never blows away a half-typed form. Directory input backed by
// suggest() over the distinct cwds in the current snapshot; optional first
// prompt; submit POSTs /api/sessions/new and the session appears on its own via
// SessionStart. The header "+ session" button opens it blank; a dir group's "+"
// prefills that cwd.

function closeNewSession() {
  $modal.hidden = true;
  $modal.textContent = "";
  document.body.classList.remove("modal-open");   // release the scroll lock
}

// Custom dropdown replacing the form's native <select>s — Safari ignores most
// select styling and always opens the native white macOS popup, which clashes
// with the theme. This renders both the closed control and the open list in
// the dashboard's own language (the cmenu pattern). API shaped for the call
// sites: value get/set, fill() (rebuild, keep the current value if it
// survives, else fall back to the first option), has()/add() for the
// resumeSid injection.
function dropdown() {
  const root = el("div", "nsdrop");
  const btn = el("button", "nsinput nsdropbtn");
  btn.type = "button";
  const lab = el("span", "nsdroplab");
  btn.append(lab, el("span", "nsdropcaret", "▾"));
  const menu = el("div", "nsdropmenu");
  menu.hidden = true;
  root.append(btn, menu);

  let items = [];                      // [{v, txt}]
  let val = "";
  let hi = -1;                         // highlighted index while open
  const label = () => {
    const it = items.find(i => i.v === val);
    lab.textContent = it ? it.txt : "";
  };
  const paint = () => {
    menu.textContent = "";
    items.forEach((it, i) => {
      const row = el("div", "nsdropitem" + (i === hi ? " sel" : ""), it.txt);
      row.onmousedown = (e) => e.preventDefault();   // keep btn focus → no blur
      // preventDefault: the .nsfield wrapper is a <label>, and a click's
      // default action forwards label activation to the button — which would
      // re-toggle the menu open right after choose() closed it
      row.onclick = (e) => { e.preventDefault(); choose(i); };
      menu.append(row);
    });
  };
  const nudge = () => {
    const sel = menu.querySelector(".sel");
    if (sel) sel.scrollIntoView({ block: "nearest" });
  };
  const close = () => { menu.hidden = true; };
  const open = () => {
    hi = Math.max(0, items.findIndex(i => i.v === val));
    paint();
    menu.hidden = false;
    nudge();
    btn.focus();   // Safari doesn't focus a clicked <button> — without this a
    //                mouse-open menu gets no keyboard nav and Escape falls
    //                through to the document handler, closing the whole modal
  };
  const choose = (i) => {
    if (items[i]) { val = items[i].v; label(); if (api.onpick) api.onpick(val); }
    close();
  };
  btn.onclick = () => (menu.hidden ? open() : close());
  btn.onblur = close;
  btn.onkeydown = (e) => {
    if (menu.hidden) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        open();
      }
      return;
    }
    if (e.key === "ArrowDown") { e.preventDefault(); hi = Math.min(items.length - 1, hi + 1); paint(); nudge(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); hi = Math.max(0, hi - 1); paint(); nudge(); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(hi); }
    // stopPropagation: the document-level Escape closes the whole modal
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
  };

  const api = {
    el: root,
    onpick: null,   // called with the value on a USER pick (not on fill/set)
    fill(pairs) {
      items = pairs.map(([v, txt]) => ({ v, txt }));
      if (!items.some(i => i.v === val)) val = items[0] ? items[0].v : "";
      label();
      if (!menu.hidden) { hi = Math.max(0, items.findIndex(i => i.v === val)); paint(); }
    },
    add(v, txt) { items.push({ v, txt }); },
    has: (v) => items.some(i => i.v === v),
    get value() { return val; },
    set value(v) { val = v; label(); },
  };
  return api;
}

// Last-used launch prefs (directory/model/effort) — preselected the next time
// the form opens (launches are usually the same project on the same settings).
// Written only on a successful launch; an explicit prefill (a dir group's "+",
// a resume button) still wins over the remembered directory.
const NS_LAST_KEY = "claude-dash:ns-last";
const nsLast = () => {
  try { return JSON.parse(localStorage.getItem(NS_LAST_KEY)) || {}; }
  catch { return {}; }
};
const nsRemember = (prefs) => {
  try { localStorage.setItem(NS_LAST_KEY, JSON.stringify(prefs)); } catch {}
};

// Freeform text input + picker menu — replaces the directory field's
// <datalist>, which Safari renders in the system style AND pops open on
// focus (the "somehow already clicked" look). Same visual language as
// dropdown() (.nsdropmenu/.nsdropitem). A click opens the menu: with the
// current value blank or an exact known entry it lists EVERYTHING (the
// picker look, current value highlighted); while typing it filters by
// substring. ↑/↓ move, Enter picks the highlighted row — unless that row IS
// already the value, where it closes and falls through to the caller's
// Enter = launch — Esc closes the menu only. Caller calls sug.key(e) FIRST
// in onkeydown.
function suggest(input, all) {
  const menu = el("div", "nsdropmenu");
  menu.hidden = true;
  let items = [], hi = -1, squelch = false;
  const close = () => { menu.hidden = true; hi = -1; };
  const paint = () => {
    menu.textContent = "";
    items.forEach((v, i) => {
      const row = el("div", "nsdropitem" + (i === hi ? " sel" : ""), v);
      row.onmousedown = (e) => e.preventDefault();   // keep input focus
      row.onclick = (e) => { e.preventDefault(); pickRow(i); };
      menu.append(row);
    });
    const sel = menu.querySelector(".sel");
    if (sel) sel.scrollIntoView({ block: "nearest" });
  };
  const open = () => {
    const cur = input.value.trim();
    const exact = !cur || all.includes(cur);
    items = exact ? all.slice()
                  : all.filter(v => v.toLowerCase().includes(cur.toLowerCase()));
    if (!items.length) { close(); return; }
    hi = items.indexOf(cur);            // -1 unless the value is a known entry
    paint();
    menu.hidden = false;
  };
  const pickRow = (i) => {
    input.value = items[i];
    squelch = true;                     // the input event below must not reopen
    input.dispatchEvent(new Event("input"));
    squelch = false;
    close();
  };
  // deliberately NO focus→open: the form auto-focuses this field when blank,
  // and a menu that pops without a pointer action reads as "already clicked".
  // Open only on an actual click, typing, or ArrowDown.
  input.addEventListener("input", () => { if (!squelch) open(); });
  input.addEventListener("click", () => { if (menu.hidden) open(); });
  input.addEventListener("blur", close);
  const key = (e) => {
    if (menu.hidden) {
      if (e.key === "ArrowDown") { e.preventDefault(); open(); return true; }
      return false;
    }
    if (e.key === "ArrowDown") { e.preventDefault(); hi = Math.min(items.length - 1, hi + 1); paint(); return true; }
    if (e.key === "ArrowUp") { e.preventDefault(); hi = Math.max(0, hi - 1); paint(); return true; }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); return true; }
    if (e.key === "Enter") {
      if (hi >= 0 && items[hi] !== input.value.trim()) {
        e.preventDefault();
        pickRow(hi);
        return true;
      }
      close();
      return false;                     // fall through to the caller's launch
    }
    return false;
  };
  return { el: menu, key };
}

function openNewSession(prefillCwd, resumeSid) {
  $modal.textContent = "";
  const last = nsLast();
  const panel = el("div", "nspanel");
  panel.append(el("div", "nstitle", "new session"));

  // every picker/input row is a DIV, not a <label>: label activation forwards
  // any click on the row (title included) into the field — focusing it (or
  // toggling a dropdown) and making it impossible to defocus by clicking
  // beside the field; only the prompt row keeps the <label> (focusing a
  // textarea from its title is harmless and standard)
  const dirRow = el("div", "nsfield");
  dirRow.append(el("span", "nslabel", "directory"));
  const dir = el("input", "nsinput");
  dir.type = "text";
  dir.spellcheck = false;
  dir.placeholder = "/path/to/project";
  dir.value = prefillCwd || last.cwd || "";
  const sug = suggest(dir, [...new Set(S.sessions.map(r => r.cwd).filter(Boolean))]);
  dirRow.append(dir, sug.el);

  // start from: a fresh conversation (default), `claude --continue` (the
  // directory's most recent conversation), or `claude --resume <sid>` — the
  // resume options are this directory's known sessions from the snapshot,
  // rebuilt when the directory changes. A resumed conversation forks to a
  // new sid; the adopt machinery and the jump watch handle that on their own.
  const startRow = el("div", "nsfield");
  startRow.append(el("span", "nslabel", "start from"));
  const start = dropdown();
  startRow.append(start.el);
  const fillStart = () => {
    const cwd = dir.value.trim();
    const items = [["", "a fresh conversation"],
                   ["continue", "continue the most recent conversation"]];
    for (const row of S.sessions.filter(r => r.cwd === cwd).slice(0, 15))
      items.push(["resume:" + row.sid,
                  "resume · " + (row.title || shortSid(row.sid))
                  + (row.started_at ? " · " + ago(row.started_at) : "")]);
    start.fill(items);
  };
  fillStart();
  dir.oninput = fillStart;
  if (resumeSid) {
    const v = "resume:" + resumeSid;
    if (!start.has(v)) start.add(v, "resume · " + shortSid(resumeSid));
    start.value = v;
  }

  // model + effort side by side — concrete values only, no "default" entry
  // (the user always launches with explicit flags; the remembered last-used
  // value is the preselection, with a fixed first-ever fallback)
  const pick = (label, opts) => {
    const row = el("div", "nsfield");
    row.append(el("span", "nslabel", label));
    const sel = dropdown();
    sel.fill(opts);
    row.append(sel.el);
    return [row, sel];
  };
  // account picker — the subscription to launch under (a switcher alias like
  // c1/c2). Populated from /api/accounts (cached in S.accts); each option shows
  // the account's latest usage inline when known. No "default" option: the
  // plain-claude login duplicates one of these accounts. The row hides when
  // there is no switcher (empty list → the launch just runs plain claude).
  // The DEFAULT selection load-balances: the account with the most 5h headroom
  // (fiveHourUsed; ties → registry order), refined again when the fresh
  // /api/accounts fetch lands — unless the user already picked one by hand.
  const [acctRow, acct] = pick("account", []);
  acctRow.style.display = "none";
  let acctPicked = false;
  acct.onpick = () => { acctPicked = true; };
  const fillAccts = (list) => {
    acctRow.style.display = list.length ? "" : "none";
    acct.fill(list.map(a => {
      const u = a.usage;
      const usage = u && (typeof u.five_hour === "number" || typeof u.seven_day === "number")
        ? "  (5h " + (u.five_hour ?? "–") + "% · 7d " + (u.seven_day ?? "–") + "%)" : "";
      return [a.slug, a.slug + " · " + a.label + usage];
    }));
    if (!acctPicked && list.length)
      acct.value = list.reduce((b, a) => fiveHourUsed(a) < fiveHourUsed(b) ? a : b).slug;
  };
  if (S.accts) fillAccts(S.accts);
  fetch("/api/accounts").then(r => r.json())
    .then(list => { S.accts = list; fillAccts(list); }).catch(() => {});

  const [modelRow, model] = pick("model", [
    ["fable", "fable"], ["opus", "opus"],
    ["sonnet", "sonnet"], ["haiku", "haiku"],
  ]);
  model.value = model.has(last.model) ? last.model : "fable";
  const [effortRow, effort] = pick("effort", [
    ["low", "low"], ["medium", "medium"],
    ["high", "high"], ["xhigh", "xhigh"], ["max", "max"],
  ]);
  effort.value = effort.has(last.effort) ? last.effort : "high";
  const split = el("div", "nssplit");
  split.append(modelRow, effortRow);
  const split2 = el("div", "nssplit");
  split2.append(acctRow);

  const promptRow = el("label", "nsfield");
  promptRow.append(el("span", "nslabel", "first prompt (optional)"));
  const prompt = el("textarea", "nsinput nsprompt");
  prompt.rows = 3;
  prompt.spellcheck = false;
  prompt.placeholder = IS_IPAD
    ? "what should Claude start on?"
    : "what should Claude start on?  (Enter to launch · Shift+Enter for newline)";
  promptRow.append(prompt);
  // "/" completion here too — cwd-keyed to whatever directory is currently
  // typed (cached per dir, so flipping between dirs doesn't refetch)
  const cmdCache = {};
  const spm = slashMenu(prompt, promptRow,
    () => { const c = dir.value.trim(); return cmdsFor(c, cmdCache, c); },
    { enterSends: !IS_IPAD });
  // composer UX: grow with the message, Enter launches, Shift+Enter newline
  // (on an iPad Enter is a newline and only the launch button launches)
  prompt.oninput = () => autoGrow(prompt);
  prompt.onkeydown = (e) => {
    if (spm.key(e)) return;
    if (!IS_IPAD && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); go(); }
  };

  const actions = el("div", "nsactions");
  const cancel = el("button", "nsbtn", "cancel");
  const submit = el("button", "nsbtn primary", "launch");
  actions.append(cancel, submit);

  const go = () => {
    const cwd = dir.value.trim();
    if (!cwd) { dir.focus(); return; }
    submit.disabled = true;
    const body = { cwd };
    if (start.value === "continue") body.continue = true;
    else if (start.value.startsWith("resume:")) body.resume = start.value.slice(7);
    if (acct.value) body.account = acct.value;
    if (model.value) body.model = model.value;
    if (effort.value) body.effort = effort.value;
    if (prompt.value.trim()) body.prompt = prompt.value.trim();
    postJSON("/api/sessions/new", body)
      .then(() => {
        nsRemember({ cwd, model: model.value, effort: effort.value });
        armJump(cwd, body.resume); closeNewSession(); toast("done", "launching…", cwd);
      })
      .catch(e => { submit.disabled = false; toast("ask", "launch failed", (e && e.error) || ""); });
  };
  submit.onclick = go;
  cancel.onclick = closeNewSession;
  dir.onkeydown = (e) => {
    if (sug.key(e)) return;
    if (e.key === "Enter") { e.preventDefault(); go(); }
  };

  panel.append(dirRow, startRow, split2, split, promptRow, actions);
  const back = el("div", "nsback");
  back.onclick = (e) => { if (e.target === back) closeNewSession(); };
  back.append(panel);
  $modal.append(back);
  $modal.hidden = false;
  document.body.classList.add("modal-open");      // scroll-lock the page behind
  // a known directory (remembered/prefilled) means the next thing you type is
  // the prompt — focusing the dir field there just pops its suggestion look
  (dir.value.trim() ? prompt : dir).focus();
}

$newbtn.onclick = () => openNewSession("");

/* ---------- readline-style editing keys (kitty-like) ---------- */
// ⌃W deletes the word left of the cursor, ⌃A jumps to line start, ⌃E to line
// end — the kitty/shell editing keys, in every dashboard text box (composer,
// first prompt, directory, filter). One delegated listener: element handlers
// (slash menu, suggest, filter-Esc) run first and none of them claim ⌃-keys.
// Safe to preventDefault on macOS — the browser's own accelerators live on
// ⌘, not ⌃ (and this beats the Cocoa text bindings only where behavior
// differs anyway). Match on e.code so a non-QWERTY layout can't move the
// keys. ⌃W dispatches an input event so autoGrow / the suggest and filter
// oninput hooks see the edit.
document.addEventListener("keydown", (e) => {
  if (!e.ctrlKey || e.altKey || e.metaKey || e.shiftKey) return;
  const t = e.target;
  if (!t || (t.tagName !== "TEXTAREA" &&
             !(t.tagName === "INPUT" && t.type === "text"))) return;
  const v = t.value, s = t.selectionStart, se = t.selectionEnd;
  if (e.code === "KeyW") {          // delete word (or the selection) leftward
    let a = s, b = se;
    if (a === b) {
      while (a > 0 && /\s/.test(v[a - 1])) a--;
      while (a > 0 && !/\s/.test(v[a - 1])) a--;
    }
    t.value = v.slice(0, a) + v.slice(b);
    t.setSelectionRange(a, a);
    t.dispatchEvent(new Event("input"));
  } else if (e.code === "KeyA") {   // start of the current line
    const p = s === 0 ? 0 : v.lastIndexOf("\n", s - 1) + 1;
    t.setSelectionRange(p, p);
  } else if (e.code === "KeyE") {   // end of the current line
    const p = v.indexOf("\n", se);
    t.setSelectionRange(p < 0 ? v.length : p, p < 0 ? v.length : p);
  } else return;
  e.preventDefault();
});

/* ---------- ⌃⇧←/→ cycle through live sessions (kitty's tab keys) ---------- */
// Mirrors kitty's next/previous-tab shortcuts: step through the LIVE sessions
// oldest-first (creation order, like the tab bar), wrapping at the ends. From
// the list view or a parked session — nowhere in the cycle — → enters at the
// first (oldest) live session and ← at the last. Deliberately not gated on
// input focus: macOS claims ⌃←/→ (Spaces) but nothing claims ⌃⇧←/→, and in a
// text box it shadows only a selection gesture that already lives on ⌥⇧/⌘⇧.
document.addEventListener("keydown", (e) => {
  if (!e.ctrlKey || !e.shiftKey || e.altKey || e.metaKey) return;
  const dir = e.code === "ArrowRight" ? 1 : e.code === "ArrowLeft" ? -1 : 0;
  if (!dir) return;
  e.preventDefault();
  const live = S.sessions.filter(r => r.live)
    .sort((a, b) => (a.started_at || 0) - (b.started_at || 0));
  if (!live.length) return;
  const at = live.findIndex(r => r.sid === S.cur);
  const to = at < 0 ? (dir > 0 ? 0 : live.length - 1)
                    : (at + dir + live.length) % live.length;
  if (live[to].sid !== S.cur)
    location.hash = "#/s/" + encodeURIComponent(live[to].sid);
});

// Esc in a live session view = interrupt the agent (the terminal's own Esc,
// via /interrupt → Frontend.send_key). Every overlay Escape (modal below,
// slash menu, filter, dropdowns) either runs first here or stopPropagation()s
// before the document level, so this is the fallback meaning of Esc.
function interruptSession() {
  const meta = (S.ses && S.ses.meta) || {};
  if (!S.cur || !meta.live || !meta.kitty_window_id) return;
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/interrupt", {})
    .then(r => {
      if (BUSY_TABS.includes(r && r.tab))
        toast("done", "interrupted", "Esc sent to the session");
      else
        toast("done", "Esc sent", "double-press Esc for rewind");
    })
    .catch(e => toast("ask", "interrupt failed", (e && e.error) || ""));
}

// The double-Esc gesture — its MEANING is the TUI's, decided by the tab
// state at gesture time: mid-turn it cancels the work and restores the last
// message for editing (the cancelEdit POST — two real Escapes server-side);
// idle it means REWIND, which the web now does fully itself (rewind picking
// mode below — no more "go open the kitty tab").
function rewindSession() {
  const meta = (S.ses && S.ses.meta) || {};
  if (!S.cur || !meta.live || !meta.kitty_window_id) return;
  if (CANCEL_TABS.includes(liveTab())) return cancelEdit();
  rewindPickMode(true);
}

// The live tab state of the open session (the SSE `tab` event patches the
// row; meta.tab is the initial fallback).
function liveTab() {
  return ((S.sessions.find(r => r.sid === S.cur) || {}).tab)
      || ((S.ses && S.ses.meta && S.ses.meta.tab) || "");
}

// Tab states in which a turn is running, so Claude Code's mid-turn double-Esc
// means CANCEL (not the rewind menu). Matches the server's BUSY_TABS — the
// cancel button gates on this so an idle click never opens the rewind menu.
const CANCEL_TABS = ["thinking", "working", "executing", "awaiting-bg",
                     "awaiting-command"];

// The Cancel button: Claude Code's mid-turn double-Esc — cancel the running
// turn and restore your message into the composer for editing. Distinct from
// ■ stop (a plain interrupt that keeps the partial work) and ↶ rewind (the
// checkpoint menu). Only meaningful mid-turn; the button disables when idle,
// and this guard is the belt-and-braces (an idle /rewind would type the
// rewind command, not cancel).
function cancelEdit() {
  const meta = (S.ses && S.ses.meta) || {};
  if (!S.cur || !meta.live || !meta.kitty_window_id) return;
  if (!CANCEL_TABS.includes(liveTab())) {
    toast("done", "nothing to cancel", "no turn is running");
    return;
  }
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/rewind", {})
    .then(r => {
      if (r && r.mode === "cancel-edit") {
        applyCancelEdit(r.restored || "");
        toast("done", "cancelled", "message restored below — edit and resend");
      } else {
        toast("done", "nothing to cancel", "no turn is running");
      }
    })
    .catch(e => toast("ask", "cancel failed", (e && e.error) || ""));
}

// Mirror Claude Code's mid-turn cancel-edit fully on the web — no jumping to
// the kitty tab: the restored message (the last user prompt) goes into the
// composer for editing, the cancelled prompt bubble is dropped from the feed
// (abandoned — kitty un-renders it too), and the NEXT composer send clears the
// TUI's restored draft and resends as an atomic paste (clear_draft → the
// server's Ctrl+U/K + bracketed paste, which is the ONLY reliable way to
// replace the draft: a raw send drops leading bytes after a cancel). Prefill
// only an EMPTY composer — never clobber text you were already typing.
function applyCancelEdit(restored) {
  const ses = S.ses;
  if (!ses) return;
  // drop the cancelled prompt bubble (newest .msg.prompt — items prepend, so
  // it's the FIRST in the feed). Optimistic: the transcript keeps the record
  // (verified — a mid-turn cancel doesn't rewrite the file), so a full reload
  // re-shows it; within this view the append-only feed keeps it hidden.
  const feed = ses.stream;
  const bubble = feed && feed.querySelector(".msg.prompt");
  if (bubble) bubble.remove();
  prefillComposer(restored);
}

// The shared tail of cancel-edit and web rewind: the TUI now holds the
// restored prompt as its input draft, so prefill OUR composer with the same
// text (only when empty — never clobber what you were typing) and make the
// next send replace the TUI draft (clear_draft) instead of appending to it.
function prefillComposer(restored) {
  const ses = S.ses;
  if (!ses) return;
  const ta = ses.composer;
  if (ta && restored && !ta.value.trim()) {
    ta.value = restored;
    autoGrow(ta);
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  }
  ses.clearDraftNext = true;
}

/* ---------- quick commands (docs/dashboard.md, *Web quick commands*) ----------
   The scoreboard's SECOND action row (under stop/cancel/rewind/close):
   compact + the model and effort pickers. Each sends one of the TUI's OWN
   slash commands through POST /command (fixed vocabulary server-side, never
   free text); mid-turn it queues like any typed input (`queued` in the
   reply), and a red asking-you tab disables the row — pasted text would land
   in the open dialog (the server 409s as the backstop). */

// The picker choices. Model aliases match the new-session form's list (the
// CLI resolves them); effort matches the server's EFFORTS levels.
const MODEL_CHOICES = [["fable", "fable"], ["opus", "opus"],
                       ["sonnet", "sonnet"], ["haiku", "haiku"]];
const EFFORT_CHOICES = [["low", "low"], ["medium", "medium"],
                        ["high", "high"], ["xhigh", "xhigh"], ["max", "max"]];

function closeQuickMenu() {
  document.querySelectorAll(".qcmenu").forEach(m => m.remove());
}

function sendQuickCmd(cmd, arg) {
  if (!S.cur) return;
  const label = "/" + cmd + (arg ? " " + arg : "");
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/command",
           arg ? { cmd, arg } : { cmd })
    .then(r => {
      // `confirm`: the server auto-answers the TUI's switch-confirm menu
      // when /model // /effort opens one (the prompt-cache warning)
      const sub = r.queued ? "queued — runs when the turn ends"
        : r.confirm === "failed"
          ? "sent — answer the confirm dialog in the terminal"
          : r.confirm === "confirmed" ? "switched (dialog confirmed)" : "sent";
      toast(r.confirm === "failed" ? "ask" : "done", label, sub);
      if (!r.queued && r.confirm !== "failed") applyQuickSwitch(cmd, arg);
    })
    .catch(e => toast("ask", label + " failed", (e && e.error) || ""));
}

// A dropdown anchored inside the button's .qcwrap, in the SAME visual
// language as the new-session form's dropdown() (.nsdropmenu/.nsdropitem —
// the dashboard's one picker look); `cur` marks the current value's row .sel
// like dropdown() does. A second click on the same button toggles it closed;
// the document click-away handler below closes it from anywhere outside the
// wrap, Esc via the document keydown handler.
function openQuickMenu(wrap, cmd, choices, cur) {
  const again = wrap.querySelector(".qcmenu");
  closeQuickMenu();
  if (again) return;
  const menu = el("div", "nsdropmenu qcmenu");
  for (const [val, label] of choices) {
    const row = el("div", "nsdropitem" + (val === cur ? " sel" : ""), label);
    row.onclick = () => { closeQuickMenu(); sendQuickCmd(cmd, val); };
    menu.append(row);
  }
  wrap.append(menu);
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".qcwrap")) closeQuickMenu();
});

// Optimistic button refresh after an APPLIED switch (not queued, confirm
// menu not stuck): the ctx probe only learns a model change on the next
// assistant turn, and a settings write reaches the SSE `effort` push on the
// slow cadence — the successful click itself is the freshest signal.
function applyQuickSwitch(cmd, arg) {
  const ses = S.ses;
  if (!ses) return;
  if (cmd === "model") {
    ses.pendingModel = arg.replace("[1m]", "");
    if (ses.modelBtn) setModelBtn(ses.modelBtn);
  } else if (cmd === "effort") {
    if (ses.meta) ses.meta.effort = arg;
    if (ses.effortBtn) setEffortBtn(ses.effortBtn);
  }
}

// The session's current model FAMILY (a MODEL_CHOICES value) when the ctx
// probe knows it — shortModel's leading word ("opus-4.8" → "opus").
function curModelFamily() {
  const ses = S.ses;
  if (ses && ses.pendingModel) return ses.pendingModel;
  const cx = (ses && (ses.ctx || (ses.meta && ses.meta.ctx))) || null;
  return (shortModel(cx && cx.model) || "").split("-")[0];
}

// The model button's label carries the session's CURRENT model when the ctx
// probe knows it (meta/SSE `ctx` — the transcript tail's last assistant
// record), so the row doubles as a live model indicator. A just-switched
// model shows as pendingModel until the probe's family confirms it (the
// ctx model stays stale until the next assistant turn).
function setModelBtn(btn) {
  const ses = S.ses;
  const cx = (ses && (ses.ctx || (ses.meta && ses.meta.ctx))) || null;
  const m = shortModel(cx && cx.model);
  if (ses && ses.pendingModel) {
    if ((m || "").split("-")[0] === ses.pendingModel) ses.pendingModel = null;
    else { btn.textContent = "✦ " + ses.pendingModel + " ▾"; return; }
  }
  btn.textContent = "✦ " + (m || "model") + " ▾";
}

// The effort button's label carries the SAVED effort level (meta/SSE
// `effort` — the settings' effortLevel, which every applied /effort writes
// through); bare "effort" when unknown.
function setEffortBtn(btn) {
  const meta = (S.ses && S.ses.meta) || {};
  btn.textContent = "⚡ " + (meta.effort || "effort") + " ▾";
}

/* ---------- full web rewind (docs/dashboard.md, *Web rewind*) ---------- */
// The feed's prompt bubbles ARE the checkpoint list: every user prompt is a
// checkpoint in Claude Code, so "rewind to a specific message" is a click on
// its bubble — a ↶ button each .msg.prompt carries (hover-revealed; pick mode
// reveals them all). The chosen mode POSTs /rewind-to, where the server
// drives Claude Code's own rewind menu in the session's window with screen-
// verified key events (dashboard/rewindmenu.py) — nothing to do in kitty.

// Picking mode: the idle meaning of ↶ rewind / double-Esc. Reveals every
// bubble's ↶ and waits for a click; Esc or a second toggle leaves.
function rewindPickMode(on) {
  const ses = S.ses;
  if (!ses || !ses.stream) return;
  const want = on === undefined ? !ses.stream.classList.contains("rwpick") : !!on;
  ses.stream.classList.toggle("rwpick", want);
  if (want)
    toast("done", "rewind", "pick a message to rewind to (Esc to leave)");
  else closeRewindMenu();
}

function inRewindPick() {
  const st = S.ses && S.ses.stream;
  return !!(st && st.classList.contains("rwpick"));
}

function closeRewindMenu() {
  // :not(.qcmenu) — the quick-command pickers once reused the .rwmenu class
  // and the feed delegation handler below (which calls this on ANY click)
  // removed them in the same click that opened them; they are .nsdropmenu-
  // styled now, but keep the exclusion so a future .rwmenu-classed menu with
  // its own lifecycle can't regress the same way
  document.querySelectorAll(".rwmenu:not(.qcmenu)").forEach(m => m.remove());
}

// The per-message mode menu — Claude Code's own confirm options, minus the
// summarize pair (a web summarize would need the composer anyway). The
// labels match rewindmenu.MODE_LABELS server-side.
const RW_MODES = [
  ["both", "restore code and conversation"],
  ["conversation", "restore conversation"],
  ["code", "restore code"],
];

function openRewindMenu(bubble) {
  closeRewindMenu();
  const menu = el("div", "rwmenu");
  menu.append(el("div", "rwhead", "rewind to before this message?"));
  for (const [mode, label] of RW_MODES) {
    const b = el("button", "rwopt", label);
    b.onclick = (e) => { e.stopPropagation(); doRewindTo(bubble, mode, menu); };
    menu.append(b);
  }
  const x = el("button", "rwopt rwx", "never mind");
  x.onclick = (e) => { e.stopPropagation(); closeRewindMenu(); };
  menu.append(x);
  bubble.append(menu);
}

function doRewindTo(bubble, mode, menu) {
  const meta = (S.ses && S.ses.meta) || {};
  if (!S.cur || !meta.live || !meta.kitty_window_id) return;
  if (CANCEL_TABS.includes(liveTab())) {
    toast("ask", "session is busy", "stop or cancel the turn first");
    return;
  }
  const text = bubble.dataset.txt || "";
  if (!text.trim()) return;
  // the jump hint: the target's `up`-press distance from the menu's
  // "(current)" cursor start = newer prompts + 1. Newer bubbles precede it
  // in the feed (newest-first); a stale count only slows the server's
  // text-verified scan, never mis-selects.
  let ups = 1;
  for (let n = bubble.previousElementSibling; n; n = n.previousElementSibling)
    if (n.classList && n.classList.contains("prompt")) ups++;
  menu.querySelectorAll("button").forEach(b => b.disabled = true);
  toast("done", "rewinding…", "driving the checkpoint menu");
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/rewind-to",
           { text, mode, ups })
    .then(r => {
      rewindPickMode(false);
      if (r && r.restored) {
        applyRewind(bubble, r.restored);
        // degraded: "both" at a no-code-change checkpoint — the code was
        // already in that state, so only the conversation had to move
        toast("done", "rewound", r.degraded
              ? "no code changes there — conversation restored, edit below"
              : mode === "both"
              ? "code + conversation restored — edit and resend below"
              : "conversation restored — edit and resend below");
      } else {
        closeRewindMenu();
        toast("done", "code restored", "conversation kept");
      }
    })
    .catch(e => {
      menu.querySelectorAll("button").forEach(b => b.disabled = false);
      toast("ask", "rewind failed", (e && e.error) || "");
    });
}

// A conversation restore un-renders everything from the target prompt on —
// kitty's TUI does the same. Optimistic like applyCancelEdit: the transcript
// still holds the dead branch (a rewind writes nothing until the next send
// forks it), so a full reload re-shows it; this view matches the terminal.
function applyRewind(bubble, restored) {
  while (bubble.previousElementSibling) bubble.previousElementSibling.remove();
  bubble.remove();
  prefillComposer(restored);
}

// Feed delegation: ↶ on a prompt bubble (hover or pick mode) opens the mode
// menu; in pick mode the whole bubble is a target.
document.addEventListener("click", (e) => {
  const rw = e.target.closest && e.target.closest(".msg.prompt .rw");
  if (rw) { e.preventDefault(); return openRewindMenu(rw.closest(".msg.prompt")); }
  if (e.target.closest && e.target.closest(".rwmenu")) return;
  if (inRewindPick()) {
    const bubble = e.target.closest && e.target.closest(".msg.prompt");
    if (bubble) return openRewindMenu(bubble);
    rewindPickMode(false);            // clicked elsewhere — leave pick mode
  } else closeRewindMenu();           // click-away closes a hover-opened menu
});

// The Esc GESTURE is atomic — hold a lone press for ESC_DOUBLE_MS, then
// classify: single press → one /interrupt (an Escape key event), rapid
// double → ONLY /rewind (the typed command), with NO Escape sent at all.
// Streaming the first press immediately shipped and corrupted the rewind:
// its in-flight Escape raced the /rewind text through two server threads
// and once landed MID-TEXT — the input cleared after "/rewi", the "nd"
// tail re-typed into the empty box, and the Enter submitted "nd" into the
// chat. Nothing streams until the gesture is decided, so nothing can
// interleave. The hold delays a real interrupt by 450ms — imperceptible
// next to the HTTP+kitten pipeline. Residual accepted mismatch: a SLOW
// double-press (>450ms) is two interrupts to us, but the TUI's own flaky
// double-Esc detection may still open the panel on those two Escapes.
const ESC_DOUBLE_MS = 450;
let escHold = null;
const BUSY_TABS = ["thinking", "working", "executing", "awaiting-bg"];
function escGesture() {
  const meta = (S.ses && S.ses.meta) || {};
  if (!S.cur || !meta.live || !meta.kitty_window_id) return;
  if (escHold) {
    clearTimeout(escHold);
    escHold = null;           // a third rapid press starts a fresh gesture
    rewindSession();
    return;
  }
  escHold = setTimeout(() => {
    escHold = null;
    interruptSession();
  }, ESC_DOUBLE_MS);
}
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$modal.hidden) return closeNewSession();
  if (document.querySelector(".qcmenu")) return closeQuickMenu();
  if (document.querySelector(".rwmenu")) return closeRewindMenu();
  if (inRewindPick()) return rewindPickMode(false);
  escGesture();
});

function setBadge(badge, tab) {
  badge.dataset.tab = tab;
  badge.replaceChildren(el("span", "st"),
                        document.createTextNode(TAB_LABEL[tab] || tab || "no tab"));
  // the whole session header (the web scoreboard) washes with the state hue
  const head = badge.closest(".shead");
  if (head) head.dataset.tab = tab;
}

function startRenameHeader() {
  // inline rename: swap the header title span for an input; Enter submits,
  // Esc/blur cancels. The server cleans the name (control-strip + cap) and
  // replies the stored title, which also rides the `title` SSE push.
  const ses = S.ses;
  if (!ses || !ses.projEl || ses.projEl.querySelector("input")) return;
  const span = ses.projEl;
  const old = span.textContent;
  const inp = el("input", "renamein");
  inp.value = (ses.meta && ses.meta.title) || "";
  inp.maxLength = 120;                  // mirrors the server's RENAME_MAX
  let done = false;
  const restore = (txt) => span.replaceChildren(document.createTextNode(txt));
  const cancel = () => { if (!done) { done = true; restore(old); } };
  const submit = () => {
    if (done) return;
    const name = inp.value.trim();
    if (!name) return cancel();
    done = true;
    inp.disabled = true;
    postJSON("/api/session/" + encodeURIComponent(S.cur) + "/rename", {name})
      .then((d) => {
        if (ses.meta) ses.meta.title = d.title || name;
        restore(d.title || name);
        toast("done", "renamed",
              d.tab_retitled ? "picker + tab" : "picker (tab on next resume)");
      })
      .catch((e) => {
        restore(old);
        toast("ask", "rename failed", (e && e.error) || "");
      });
  };
  inp.onkeydown = (e) => {
    // stopPropagation is load-bearing: the document-level keydown handler
    // reads Escape as the interrupt/rewind-pick gesture — typing in the
    // rename box must never leak there
    e.stopPropagation();
    if (e.key === "Enter") submit();
    else if (e.key === "Escape") cancel();
  };
  inp.onblur = () => cancel();          // a stray click = cancel, same as Esc
  span.replaceChildren(inp);
  inp.focus();
  inp.select();
}

function renderSessionChrome(tab) {
  const ses = S.ses;
  if (!ses) return;
  const meta = ses.meta || {};
  $view.textContent = "";

  const head = el("div", "shead");
  head.dataset.tab = meta.tab || "";    // state tint; live via setBadge()
  const l1 = el("div", "l1");
  const projSpan = el("span", "proj",
                      meta.title || (meta.cwd ? proj(meta) : shortSid(S.cur)));
  ses.projEl = projSpan;                // the `title` SSE + inline rename target
  l1.append(projSpan);
  const badge = el("span", "badge");
  ses.badge = badge;
  setBadge(badge, meta.tab || "");
  l1.append(badge);
  // "live" goes unsaid (state tint + badge carry it); parked still shows
  if (!meta.live) l1.append(el("span", "chip2 parked", "parked"));
  if (meta.cwd) l1.append(el("span", "sid", meta.cwd));
  const sidChip = el("span", "sid copysid", shortSid(S.cur));
  sidChip.title = "click to copy the full session id";
  sidChip.onclick = () => copySid(S.cur);
  l1.append(sidChip);
  // the checkout this session runs in — live via the `git` SSE event
  const gitc = el("span", "gitchip");
  ses.gitChip = gitc;
  setGitChip(gitc, meta.git);
  l1.append(gitc);
  // which subscription account this chat runs under (◈ c2 · claude-01)
  const acc = meta.account || {};
  if (acc.slug || acc.label) {
    const chip = el("span", "acctchip");
    chip.append(el("span", "ag", "◈"), document.createTextNode(
      " " + (acc.slug ? acc.slug + " · " + acc.label : acc.label)));
    const u = meta.usage;
    if (u) {
      const parts = [];
      if (typeof u.five_hour === "number") parts.push("5h " + u.five_hour + "%");
      if (typeof u.seven_day === "number") parts.push("7d " + u.seven_day + "%");
      if (parts.length) chip.append(el("span", "ausage", parts.join(" · ")));
    }
    l1.append(chip);
  }
  // the action buttons live on their OWN row (actrow) — inside l1 they
  // floated to wherever the title/chips left room, moving with title width
  const act = el("div", "actrow");
  // rename: deliberately OUTSIDE the live gate — it works for live AND parked
  // sessions (the server appends the agent-name naming record to the
  // transcript; a live kitty tab also retitles in place — docs/dashboard.md
  // "Web rename")
  const ren = el("button", "sstop", "✎ rename");
  ren.title = "rename this session (resume picker + tab)";
  ren.onclick = () => startRenameHeader();
  act.append(ren);
  if (meta.live && meta.kitty_window_id) {
    // stop: interrupt the agent in place — an Escape key press in the
    // session's window (the TUI's own interrupt; Esc here does the same).
    // Immediate, no confirm: it matches pressing Esc in the terminal.
    const stop = el("button", "sstop", "■ stop");
    stop.title = "interrupt the agent (Esc)";
    stop.onclick = () => interruptSession();
    act.append(stop);
    // cancel: mid-turn double-Esc — cancel the running turn and restore your
    // message into the composer for editing. Enabled only while a turn runs.
    const cancel = el("button", "sstop", "⊘ cancel");
    cancel.title = "cancel this turn and edit your message (mid-turn double-Esc)";
    cancel.onclick = () => cancelEdit();
    ses.cancelMode = (tab) => { cancel.disabled = !CANCEL_TABS.includes(tab); };
    ses.cancelMode(liveTab());
    act.append(cancel);
    // rewind: Claude Code's double-Esc — mid-turn it cancels for editing;
    // idle it enters picking mode: click a message below, choose what to
    // restore, and the server drives the TUI's own checkpoint menu
    const rew = el("button", "sstop", "↶ rewind");
    rew.title = "rewind: pick a message to restore to (mid-turn: cancel + edit)";
    // stopPropagation is load-bearing: the ENABLING click must not bubble to
    // the document click-away handler, which reads any non-bubble click in
    // picking mode as "leave" — without it the mode self-cancelled in the
    // same event (toast shown, buttons never revealed)
    rew.onclick = (e) => { e.stopPropagation(); rewindSession(); };
    act.append(rew);
    // close: closes the session's kitty tab — a graceful stop (Claude Code
    // exits on the HUP and SessionEnd runs the normal lifecycle).
    // Two-step confirm: first click arms for 4s, second click fires.
    const cls = el("button", "sstop", "✕ close");
    cls.title = "close this session's terminal tab";
    let armed = null;
    const disarm = () => {
      armed = null;
      cls.textContent = "✕ close";
      cls.classList.remove("arm");
    };
    cls.onclick = () => {
      if (!armed) {
        cls.textContent = "close session?";
        cls.classList.add("arm");
        armed = setTimeout(disarm, 4000);
        return;
      }
      clearTimeout(armed);
      disarm();
      cls.disabled = true;
      const sid = S.cur;
      postJSON("/api/session/" + encodeURIComponent(sid) + "/stop", {})
        .then(() => {
          toast("done", "session closed", "terminal tab closed");
          // the session just ended — back to the list, unless the user
          // already navigated elsewhere while the POST was in flight
          if (S.cur === sid) location.hash = "#/";
        })
        .catch(e => {
          cls.disabled = false;
          toast("ask", "close failed", (e && e.error) || "");
        });
    };
    act.append(cls);
  }
  // resume (parked, with a cwd): reopen the new-session form preset to
  // `claude --resume <this sid>` in this session's directory
  if (!meta.live && meta.cwd) {
    const res = el("button", "sresume", "↻ resume");
    res.title = "start a new tab resuming this conversation";
    res.onclick = () => openNewSession(meta.cwd, S.cur);
    act.append(res);
  }
  // quick commands on their OWN second row under the action buttons: compact
  // + the model/effort pickers, each typing the TUI's own slash command into
  // the session (docs/dashboard.md, *Web quick commands*). Live-only like
  // stop/cancel — there is no window to type into otherwise.
  const act2 = el("div", "actrow");
  if (meta.live && meta.kitty_window_id) {
    // compact: two-step confirm like close — a misclick summarizes the whole
    // conversation out from under you, so it arms first
    const cpt = el("button", "sstop", "⊜ compact");
    cpt.title = "compact the conversation (/compact)";
    let cptArmed = null;
    const cptDisarm = () => {
      cptArmed = null;
      cpt.textContent = "⊜ compact";
      cpt.classList.remove("arm");
    };
    cpt.onclick = () => {
      if (!cptArmed) {
        cpt.textContent = "compact now?";
        cpt.classList.add("arm");
        cptArmed = setTimeout(cptDisarm, 4000);
        return;
      }
      clearTimeout(cptArmed);
      cptDisarm();
      sendQuickCmd("compact");
    };
    act2.append(cpt);
    // model: dropdown picker; the label shows the ctx probe's current model
    // (live via the `ctx` SSE event → updateStatsRow)
    const mwrap = el("span", "qcwrap");
    const mdl = el("button", "sstop");
    ses.modelBtn = mdl;
    setModelBtn(mdl);
    mdl.title = "switch the model (/model — also saves as your new-session default)";
    mdl.onclick = () => openQuickMenu(mwrap, "model", MODEL_CHOICES,
                                      curModelFamily());
    mwrap.append(mdl);
    act2.append(mwrap);
    // effort: dropdown picker (current effort is config-only — not readable
    // from any transcript, see plugins/claude_code/model.py — so no label)
    const ewrap = el("span", "qcwrap");
    const eff = el("button", "sstop");
    ses.effortBtn = eff;
    setEffortBtn(eff);
    eff.title = "set the reasoning effort (/effort — also saves as your new-session default)";
    eff.onclick = () => openQuickMenu(ewrap, "effort", EFFORT_CHOICES,
                                      (ses.meta && ses.meta.effort) || "");
    ewrap.append(eff);
    act2.append(ewrap);
    // a red tab = a modal dialog is up — pasted text would land IN it (the
    // server 409s too; disabling just says so up front). Live via the same
    // SSE tab event as cancelMode.
    ses.quickMode = (tab) => {
      const block = tab === "awaiting-command";
      for (const b of [cpt, mdl, eff]) b.disabled = block;
    };
    ses.quickMode(liveTab());
  }
  head.append(l1);
  if (act.childElementCount) head.append(act);
  if (act2.childElementCount) head.append(act2);
  const sr = el("div", "statsrow");
  ses.statsRow = sr;
  head.append(sr);
  const cr = el("div", "ctxrow");     // the main thread's ctx bar, its own row
  ses.ctxRow = cr;
  head.append(cr);
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
    body.append(buildPlanCard());           // pending plan approval …
    body.append(buildAskCard());            // … and question, above the composer
    body.append(buildComposer());
    // type right away on open — no click needed. After append (focus() on a
    // detached node is a no-op), and only when the box can send (a disabled
    // parked/headless composer takes no input anyway). The document-level
    // gestures (Esc, ⌃-keys, ⌃⇧←/→) are focus-independent, so this only
    // redirects plain typing.
    if (!ses.composer.disabled) ses.composer.focus();
    body.append(buildQueueBar());
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
  // the main thread's ctx bar on its own row — live via the `ctx` SSE event
  // the model quick-button's label follows the same ctx probe
  if (ses.modelBtn) setModelBtn(ses.modelBtn);
  if (ses.ctxRow) {
    ses.ctxRow.textContent = "";
    const cx = ses.ctx;
    if (cx && cx.used) ses.ctxRow.append(ctxBar(cx, true));
    ses.ctxRow.style.display = cx && cx.used ? "" : "none";
  }
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
  const [sttxt, stcls] = agentStatus(a);
  const card = el("a", "acard" + (isHusk(a) ? " husk" : ""));
  card.dataset.st = stcls;              // state tint keyed off agent status
  card.href = "#/s/" + encodeURIComponent(S.cur) + "/a/" + encodeURIComponent(a.agent_id);
  const name = a.desc || a.agent_id;      // the Task description IS the name
  card.append(el("div", "aid", (a.kind === "teammate" ? "👥 " : "◇ ") + name));
  if (a.desc) card.append(el("div", "desc", a.agent_id));
  const m = el("div", "meta");
  m.append(el("span", stcls, sttxt));
  if (a.tools != null) m.append(el("span", "", a.tools + " events"));
  if (a.started_at && a.ended_at)
    m.append(el("span", "", dur(a.ended_at - a.started_at)));
  else if (a.started_at)
    m.append(el("span", "", ago(a.started_at)));
  card.append(m);
  if (a.ctx) card.append(ctxBar(a.ctx));
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
refreshAccounts();
setInterval(refreshAccounts, ACCOUNTS_POLL_MS);
