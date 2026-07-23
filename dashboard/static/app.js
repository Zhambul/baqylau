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
  jump: null,            // pending jump-to-new-session watch ({cwd, resumeSid,
                         // win, show, quiet, armedAt, known, liveAtArm, until}
                         // — armJump; quiet = user navigated away mid-wait, so
                         // resolution toasts instead of yanking)
  jumpDone: null,        // "#/s/<sid>" of a QUIETLY resolved launch — lets a
                         // return to #/launching forward to the session that
                         // arrived while the user was away (consumed once)
  pendingUI: false,      // the #/launching "starting session…" view is mounted
                         // (renderList must not clobber it — same role as
                         // S.cur for the session view)
  cards: new Map(),      // sid -> mounted list-card element (the patch targets)
  rowPrev: new Map(),    // sid -> JSON of the row the mounted card shows
  listKey: null,         // listShape() of the last full list render
  armClose: null,        // {sid, until} — the one armed card-✕ confirm; a
                         // DEADLINE held here (not in the button) so it
                         // survives the per-tick card rebuilds (patchCards)
  closing: new Set(),    // sids with a close POST in flight (card ✕ disabled)
  closePend: {},         // sid -> optPending handle for a close in flight (the
                         // web-hint lifecycle + reconcile). MUST be an object:
                         // reconcileCloses does Object.keys(S.closePend) on every
                         // sessions tick and the ✕ handler does S.closePend[sid]=…
                         // — an undefined here threw a TypeError BEFORE closeSession
                         // ran, so /stop never fired (THE "still not closing" bug,
                         // caught by the js.error frontend-audit row at app.js:878)
  hidden: {},            // {group_key: hidden_at_epoch} — directories the ✕
                         // hid from the list (server prefs, /api/dirs/hidden).
                         // A group stays hidden only while it has no session
                         // started AFTER hidden_at, so a new session there
                         // re-shows it (groupSessions filters, dirHidden)
  nsPrefs: {},           // the new-session form's last-used {cwd, model, effort}
                         // — the backend prefs cache (GET/POST /api/ns-prefs;
                         // fetched at boot), so nsLast() reads it synchronously
};

const ARCHIVE_S = 3 * 86400;   // sessions older than this fold into "archived"
const ARM_MS = 4000;   // two-step-confirm window (card ✕ / header ✕ / compact)
// A just-launched session's kitty pane isn't tagged claude_session=<sid> for a
// moment, so /api/session reports live:true with a blank kitty_window_id — the
// startup tag-race. showSession re-fetches meta until the window resolves so the
// composer + ✕ close button don't stay stuck (docs/dashboard.md, *Launch tag-
// race*). Bounded — a truly headless session never tags a window.
const LAUNCH_RESOLVE_MS = 1000;
const LAUNCH_RESOLVE_TRIES = 12;
// Timeout for the ✕ close's fetch (closeSession → postJSON, the plain-fetch
// channel proven to traverse the tunnel). < the 20s optPending watchdog so a
// stalled close rejects visibly/retryably (→ close.fail + web-clientfail)
// instead of hanging silently (docs/dashboard.md *Close via the plain-fetch
// channel*).
const CLOSE_POST_MS = 12000;

// iPad detection — gates the message boxes' Enter behavior AND every
// non-user-initiated .focus() (view-open, form-open, post-send refocus:
// unasked-for focus pops the on-screen keyboard, and focusing a text control
// is what triggers Safari's page auto-zoom — style.css touch section has the
// full story). Since iPadOS 13
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

// The compact endpoint label for the frontend audit — the path minus the /api/
// prefix and the (already-separately-logged) sid, so `/api/session/<sid>/stop`
// → `session/stop`. Purely for readable `web-client` rows.
function apiEp(url) {
  return String(url || "").replace(/^\/?api\//, "")
    .replace(/session\/[^/]+\//, "session/").replace(/\?.*$/, "");
}

// The control-plane write: every POST carries the JSON content type AND the
// custom X-Claude-Dash header the server's _post_guard demands (both force a
// CORS preflight a cross-origin page can't pass). Resolves to the parsed JSON
// on success, rejects with the server's {error} on a 4xx/5xx.
// opts (optional): { keepalive, timeout, audit, sid, auditData }.
//   keepalive — send via the browser's keepalive pool (the sendBeacon infra),
//     which is NOT starved by the page's long-lived SSE EventSource streams. On
//     an HTTP/1.1 origin (this server) the ~6-connections/origin cap is eaten by
//     /events + /events/session (+ agent) — a plain fetch for a control POST can
//     then QUEUE behind them and never send, hanging with no resolve AND no
//     reject (so no .catch, no web-clientfail — an invisible stuck close, the
//     reported bug). Use for the tiny control POSTs (NOT uploads/messages — the
//     keepalive quota is 64KB across all inflight such requests).
//   timeout — abort (→ reject) after N ms so a hung request becomes a VISIBLE,
//     retryable, auditable failure (web-clientfail kind:transport) instead of a
//     silent pending forever.
//   audit — a gesture name (e.g. "close", "send"): when set, this POST's whole
//     transport lifecycle is mirrored into the frontend audit as `<audit>.begin`
//     (with a connection snapshot + optional auditData), `<audit>.ok` (ms +
//     status) and `<audit>.fail` (ms + kind http|transport + status/error). This
//     is the ONE place the browser records what actually happened to a control
//     request the server may never have seen. `sid` scopes the rows; `auditData`
//     adds gesture-specific fields to the begin row. NEVER tag the telemetry
//     endpoints themselves (/clientlog, /hint-audit, /client-fail) — that recurses.
function postJSON(url, body, opts) {
  opts = opts || {};
  const tag = opts.audit;
  const sid = opts.sid || (tag ? S.cur : "") || "";
  const t0 = performance.now();
  if (tag) {
    const info = connInfo();
    // es (SSE streams held open at send time) + online are the per-gesture
    // connection facts; the batch's `conn` snapshot carries the rest. No `conn`
    // key here — it would collide with that batch dict server-side.
    clog(sid, tag + ".begin", Object.assign(
      { ep: apiEp(url), es: info.es, online: info.online }, opts.auditData || {}));
  }
  const init = {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Claude-Dash": "1" },
    body: JSON.stringify(body || {}),
  };
  if (opts.keepalive) init.keepalive = true;
  let timer = null;
  if (opts.timeout && typeof AbortController !== "undefined") {
    const ctl = new AbortController();
    init.signal = ctl.signal;
    timer = setTimeout(() => ctl.abort(), opts.timeout);
  }
  return fetch(url, init).then(
    // reached the server (any status): parse, then resolve/reject on r.ok. A body
    // that isn't JSON is only expected on an error, so synthesize one there.
    r => r.json().catch(() => ({ error: "bad response", status: r.status }))
      .then(d => {
        if (tag) clog(sid, r.ok ? tag + ".ok" : tag + ".fail", {
          ms: Math.round(performance.now() - t0), status: r.status,
          kind: r.ok ? undefined : "http",
          error: r.ok ? undefined : (d && d.error) || "" });
        return r.ok ? d : Promise.reject(
          Object.assign({ status: r.status }, d || { error: "request failed" }));
      }),
    // never reached / no response — a transport failure (network, tunnel drop,
    // our own AbortController timeout). THE case the server can't see.
    err => {
      if (tag) clog(sid, tag + ".fail", {
        ms: Math.round(performance.now() - t0), kind: "transport",
        aborted: !!(err && err.name === "AbortError"),
        error: (err && err.message) || "" });
      throw err;
    })
    .finally(() => { if (timer) clearTimeout(timer); });
}

// This page's opaque identity — stamped on every ask-draft write so the SSE
// echo of our OWN change is ignored (a peer device's change has a different
// origin and IS applied). Per-load: two tabs are two peers, which is correct.
const CLIENT_ID = Math.random().toString(36).slice(2) + Date.now().toString(36);
const ASK_DRAFT_DEBOUNCE_MS = 350;      // coalesce typing before persisting

// The FRONTEND audit channel (clog → POST /api/clientlog → `web-client` state_files
// rows, docs/dashboard.md *Frontend audit (clientlog)*). The server can only ever
// see a control POST that ACTUALLY ARRIVED; a request the browser tried but that
// never reached the handler (dropped by the tunnel, starved of a connection, queued
// forever) is invisible server-side — the entire class of "still not closing" bugs
// where /stop left no trace. This channel is the browser reporting what IT did:
// each control gesture logs a begin/ok/fail lifecycle with timing + a connection
// snapshot, delivered over the plain-fetch channel that IS proven to traverse the
// tunnel (the same one /hint-audit and /message ride). Best-effort, batched,
// never surfaces to the user.
const CLOG = [];              // pending client-audit events (ring, oldest dropped)
const CLOG_MAX = 100;         // cap so a delivery outage can't grow it unbounded
const CLOG_FLUSH_MS = 500;    // debounce — coalesce a gesture's begin+ok into one POST
const CLOG_RETRY_MS = 4000;   // re-flush backoff after a failed delivery
let clogTimer = null;
let clogBusy = false;         // re-entrancy guard — clog() is a no-op while a flush
                              // is mid-build, so the audit can't recurse into itself
const SSE_UP = {};            // stream label -> last up? — clog SSE only on TRANSITIONS
                              // (EventSource.onerror re-fires each reconnect attempt)

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
// the session's recency: last_active (server-computed — transcript mtime,
// with ended_at / state-DB-mtime / started_at fallbacks) for everything
// time-flavored on the list — the card chip, the archive boundary, the
// resume dropdown. started_at survives for rows pushed by a
// not-yet-restarted server.
function lastActive(row) { return row.last_active || row.started_at || 0; }
// GROUP order sorts by this instead: started_at is fixed for the session's
// whole life, so the order only moves when a session starts/resumes
// somewhere — last_active is the transcript mtime, which grows on every
// stream write, and sorting groups on it made two concurrently-live projects
// leapfrog each other every SSE tick (order is part of listShape, so each
// flip forced a full list rebuild — the page visibly jolted).
function orderKey(row) { return row.started_at || lastActive(row); }
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
  // navigator.clipboard is undefined in a NON-secure context (a plain-http
  // remote tunnel); calling .writeText on it throws synchronously. 127.0.0.1 is
  // a secure context, so localhost is unaffected — this only guards the remote
  // http case (docs/remote.md) from an uncaught TypeError.
  if (!navigator.clipboard) return toast("ask", "copy failed", "needs https");
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

/* ---------- persistent session strip ---------- */
// The standing complement to the transient toasts: a slim bar under the header,
// on every view, listing EVERY live session as a jump pill — needs-you states
// lead (asking red + pulse, your-turn green), then busy (magenta), running
// (blue), idle (grey, quietest) — hidden entirely when nothing is live. Inside
// a session view it doubles as the chat switcher. Fed from the global
// S.sessions snapshots the app already holds, plus the open session's `tab`
// SSE event (which patches its row in place so the bar reacts before the next
// snapshot). Within a state group pills sort by label+sid, NOT recency: the
// bar re-renders every snapshot tick, and pills that shuffle under the cursor
// are a misclick trap.

const BASE_TITLE = "baqylau";
// the baqylau shanyrak — бақылау, "observation": the radial yurt-crown seen
// looking up into it, reworked as a control view — a central aperture (the
// observer) with spokes to gold nodes on a ring (the agents). Gold accent
// (#E9B949) on a neutral ink (#9aa7b0) that reads on a light OR dark browser
// tab; the asking state adds a red node top-right (favData's `extra`). 200
// viewBox = the design's own coordinates, strokes bumped for 16px legibility.
const FAV_GLYPH =
  "<g stroke='#9aa7b0' stroke-width='6' stroke-linecap='round'>"
  + "<line x1='100' y1='84' x2='100' y2='18'/>"
  + "<line x1='111.31' y1='88.69' x2='157.98' y2='42.02'/>"
  + "<line x1='116' y1='100' x2='182' y2='100'/>"
  + "<line x1='111.31' y1='111.31' x2='157.98' y2='157.98'/>"
  + "<line x1='100' y1='116' x2='100' y2='182'/>"
  + "<line x1='88.69' y1='111.31' x2='42.02' y2='157.98'/>"
  + "<line x1='84' y1='100' x2='18' y2='100'/>"
  + "<line x1='88.69' y1='88.69' x2='42.02' y2='42.02'/>"
  + "</g>"
  + "<circle cx='100' cy='100' r='82' fill='none' stroke='#E9B949' stroke-width='8'/>"
  + "<g fill='#E9B949'>"
  + "<circle cx='100' cy='18' r='9'/><circle cx='157.98' cy='42.02' r='9'/>"
  + "<circle cx='182' cy='100' r='9'/><circle cx='157.98' cy='157.98' r='9'/>"
  + "<circle cx='100' cy='182' r='9'/><circle cx='42.02' cy='157.98' r='9'/>"
  + "<circle cx='18' cy='100' r='9'/><circle cx='42.02' cy='42.02' r='9'/>"
  + "</g>"
  + "<circle cx='100' cy='100' r='16' fill='none' stroke='#9aa7b0' stroke-width='6'/>"
  + "<circle cx='100' cy='100' r='8' fill='#E9B949'/>";
const favData = (extra) =>
  "data:image/svg+xml," + encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'>"
    + FAV_GLYPH + (extra || "") + "</svg>");
const FAVICON = favData("");
const FAVICON_ASK = favData("<circle cx='168' cy='32' r='30' fill='#e06c75'/>");

// tab state → pill class (the dot/ring color, mirroring the kitty tab
// palette) + its needs-you-first sort rank. Anything unmapped — idle, or ""
// for a tabless headless/daemon session — is the grey idle pill.
const ATTN_CLASS = {
  "awaiting-command": "ask", "awaiting-response": "done",
  "thinking": "busy", "working": "busy",
  "executing": "run", "awaiting-bg": "run",
};
const ATTN_RANK = { ask: 0, done: 1, busy: 2, run: 3, idle: 4 };

function attnPill(row) {
  const cls = ATTN_CLASS[row.tab] || "idle";
  const a = el("a", "attn-pill " + cls + (row.sid === S.cur ? " self" : ""));
  a.href = "#/s/" + encodeURIComponent(row.sid);
  a.append(el("span", "adot"));
  a.append(el("span", "alabel", row.title || proj(row)));
  a.title = (TAB_LABEL[row.tab] || row.tab || "no tab") + " · " + row.sid;
  return a;
}

function renderAttention() {
  if (!$attn) return;
  const live = S.sessions.filter(r => r.live);
  live.sort((a, b) =>
    ATTN_RANK[ATTN_CLASS[a.tab] || "idle"] - ATTN_RANK[ATTN_CLASS[b.tab] || "idle"]
    || (a.title || proj(a)).localeCompare(b.title || proj(b))
    || (a.sid < b.sid ? -1 : 1));
  const asking = live.filter(r => r.tab === "awaiting-command").length;
  const show = live.length > 0;
  $attn.hidden = !show;
  document.body.classList.toggle("attn-on", show);
  $attn.textContent = "";
  if (show) {
    if (asking)
      $attn.append(el("span", "alead ask", asking + " asking"));
    for (const row of live) $attn.append(attnPill(row));
  }
  document.title = asking ? "(" + asking + ") " + BASE_TITLE : BASE_TITLE;
  if ($favicon) $favicon.href = asking ? FAVICON_ASK : FAVICON;
}

/* ---------- account usage strip (top of every page) ---------- */
// A slim strip under the header showing each subscription account's latest
// 5-hour / 7-day rate-limit usage (GET /api/accounts — aggregated per account
// from the status-line capture, docs/dashboard.md). Polled on a slow timer
// (usage moves slowly, and it's ambient); hidden entirely when no account has
// any usage captured yet. The default account is labeled "default"; others by
// their switcher label (c2 · claude-01).

const ACCOUNTS_POLL_MS = 60000;

// The window keys of a usage snapshot, in the server's serve order (the
// account-wide 5h/7d pair first, then model-scoped windows like
// seven_day_fable — core/sessionapi.usage_windows is the owner of this rule;
// the served dict is already built in that order and JSON preserves it):
// numeric used-%, never the ts stamp or a *_reset sibling.
function usageWindows(u) {
  return Object.keys(u || {}).filter(k =>
    typeof u[k] === "number" && k !== "ts" && !k.endsWith("_reset"));
}

// "five_hour" → "5h", "seven_day" → "7d", "seven_day_fable" → "7d fable"
function windowLabel(k) {
  return k.replace(/^five_hour/, "5h").replace(/^seven_day/, "7d")
    .replace(/_/g, " ").trim();
}

// Effective 5h-used % for the new-session form's load-balancing default —
// SERVER-computed (core/sessionapi.effective_five_hour, the single owner of
// the rolled-over→0 arithmetic; the rate-limit migration's target picker uses
// the same function). An account with no snapshot has had no traffic → 0.
function fiveHourUsed(a) {
  return typeof a.five_hour_eff === "number" ? a.five_hour_eff : 0;
}

// The new-session picker's weekly-quota PERISHABILITY (higher = burn first).
// SERVER-computed (core/sessionapi.sched_score, the single owner of the
// scheduling arithmetic); missing → 0 (no snapshot / no urgency).
function schedScore(a) {
  return typeof a.sched_score === "number" ? a.sched_score : 0;
}

// The limit-hit chip/marker text: "fable limit hit" for a model-scoped stamp
// (limit_hit.model, parsed server-side by relimit.limit_model), "limit hit"
// for an account-wide one.
function limitLabel(hit) {
  return (hit.model ? hit.model + " " : "") + "limit hit";
}

function acctPill(a) {
  const u = a.usage;
  const pill = el("div", "acct");
  const name = a.slug ? a.slug + " · " + a.label : a.label;
  pill.append(el("span", "aname", name));
  const wins = usageWindows(u);
  if (!wins.length) {
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
    if (reset) {
      // dim "resets in" prefix, keep the duration (4h 12m) at full weight
      const txt = resetAgo(reset);          // "in 4h 12m" | "in <1m" | "now"
      const box = el("span", "ureset");
      const hasIn = txt.startsWith("in ");
      box.append(el("span", "rlbl", hasIn ? "resets in " : "resets "));
      box.append(el("span", "rval", hasIn ? txt.slice(3) : txt));
      seg.append(box);
    }
    return seg;
  };
  // one bar per captured window — the 5h/7d pair plus any model-scoped
  // window the CLI reports (e.g. "7d fable"), same order as served
  wins.forEach(k => pill.append(bar(windowLabel(k), u[k], k + "_reset")));
  // The account is BLOCKED right now (a session on it died on error=
  // rate_limit — the `limit-hit` stamp, served only while still active):
  // say so outright; the frozen usage bar alone reads ~95% at exactly the
  // moment the account stops working (the status line never reports 100%
  // once requests are rejected — docs/relimit.md).
  if (a.limit_hit) {
    // model-scoped stamps ("fable limit hit" — only that model is blocked,
    // relimit.limit_model) name the model; account-wide ones stay bare
    const chip = el("span", "ulimit", limitLabel(a.limit_hit));
    if (a.limit_hit.msg) chip.title = a.limit_hit.msg;
    pill.append(chip);
    if (a.limit_hit.resets_at)
      pill.append(el("span", "ureset", "resets " + resetAgo(a.limit_hit.resets_at)));
  }
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
  es.onopen = () => { $conn.dataset.on = "1"; sseMark("global", true); };
  es.onerror = () => { $conn.dataset.on = "0"; sseMark("global", false); };
  es.addEventListener("sessions", (e) => {
    S.sessions = JSON.parse(e.data);
    reconcileCloses();
    if (!S.cur) renderList();
    else updateHeadFromList();
    renderAttention();
    checkJump();
  });
  // Only changed rows (the server sends the full `sessions` snapshot for
  // membership/order moves, so a delta always merges in place by sid) —
  // during activity this replaces the full 131-row resend every tick
  // (~2.2MB/min uncompressed over a tunnel) with a few hundred bytes.
  es.addEventListener("sessions-delta", (e) => {
    const rows = (JSON.parse(e.data) || {}).rows || [];
    if (!rows.length) return;
    const at = new Map(S.sessions.map((r, i) => [r.sid, i]));
    for (const row of rows) {
      const i = at.get(row.sid);
      if (i !== undefined) S.sessions[i] = row;
    }
    reconcileCloses();
    if (!S.cur) renderList();
    else updateHeadFromList();
    renderAttention();
    // a delta can carry the armed jump's hit too (e.g. a known row flipping
    // parked→live without an order move) — the watch must not depend on the
    // full-snapshot path alone
    checkJump();
  });
  // the launch-wake fast path: the server's _launch_wake watcher spotted the
  // session a web launch produced and named its sid — the page that armed the
  // matching jump navigates NOW, without waiting for the row to ride a
  // snapshot. Every open page receives every wake, so ownership is checked:
  // the launch's window id when both sides know it (exact), the resumed sid,
  // else the armed cwd (same heuristic the snapshot path uses).
  es.addEventListener("wake", (e) => {
    const d = JSON.parse(e.data) || {};
    const j = S.jump;
    if (!j || !d.sid) return;
    const mine = (j.win && d.win && j.win === d.win)
      || (j.resumeSid && j.resumeSid === d.sid)
      || (!j.win && !d.win && j.cwd === d.cwd);
    if (mine) jumpHit(d.sid, "");
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
    if (!S.boot) {
      S.boot = boot;
      // anchor this client to the server build it FIRST connected to — so a later
      // "the page behaved like old code" report is checkable (compare against the
      // stale row below and the boot record's loaded-build)
      clog("", "hello", { boot: boot || "" });
      return;
    }
    if (boot !== S.boot) {
      // the server redeployed under an OPEN page — its JS is now stale. This
      // caused "product bugs" that were really stale-code mismatches; the row
      // makes "was the user on old code?" answerable from the DB (the reload
      // toast is easily missed / dismissed).
      clog("", "stale", { was: S.boot || "", now: boot || "" });
      S.boot = boot;
      toast("ask", "dashboard updated",
            "refresh the page to load the latest UI",
            () => location.reload());
    }
  });
}

/* ---------- router ---------- */

window.addEventListener("hashchange", route);
// Flush the frontend-audit buffer as the tab goes away (navigation, tab close,
// backgrounding) — via sendBeacon here, the one place beacon is the right tool
// and a lost tail is acceptable. Both events fire on mobile Safari where an
// unload alone is unreliable.
window.addEventListener("pagehide", () => flushClog(true));
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") flushClog(true);
});
// Uncaught client errors — a handler throwing and leaving NO trace is exactly the
// blind spot this audit closes (a broken render reads as a silent product bug).
// First stack frame is enough to locate it; capped, best-effort.
window.addEventListener("error", (e) => {
  clog(S.cur || "", "js.error", {
    msg: (e && e.message || "").slice(0, 200), src: apiEp(e && e.filename || ""),
    line: (e && e.lineno) || 0, col: (e && e.colno) || 0 });
});
window.addEventListener("unhandledrejection", (e) => {
  const r = e && e.reason;
  clog(S.cur || "", "js.reject",
       { msg: String((r && (r.message || r.error)) || r || "").slice(0, 200) });
});
// One boot record per page load — anchors this client's event stream to a device
// + ORIGIN (127.0.0.1 vs the tunnel — the difference that mattered for the close
// bug) + the LOADED BUILD (the ?v=<BOOT_ID> the index stamped on THIS app.js;
// document.currentScript is that <script> during top-level eval). Compared with
// the server's boot id in the `hello` row, a mismatch = the browser is running
// stale cached JS — the "product bug that was really old code" case, now provable
// from the DB. Best-effort; sits in the buffer until the first flush.
clog("", "boot", {
  origin: location.origin, hash: location.hash, ipad: IS_IPAD,
  build: ((document.currentScript && document.currentScript.src || "")
          .match(/[?&]v=([^&]+)/) || [, ""])[1],
  plat: (navigator.platform || "").slice(0, 24),
  online: navigator.onLine !== false,
  w: screen.width, h: screen.height, dpr: window.devicePixelRatio || 1 });

function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  // hide the c1/c2 account strip once we're inside a particular session
  document.body.classList.toggle("in-session", parts[0] === "s");
  // A user-driven navigation while a launch watch is armed flips it QUIET:
  // the watch keeps running, but resolution becomes a clickable toast instead
  // of a navigation — yanking the browser away from wherever the user went is
  // the exact annoyance the pending view exists to remove. (This replaces the
  // old cancel-outright: peeking at another session mid-wait must not orphan
  // the launch.) jumpHit's own navigations never land here armed — it clears
  // S.jump BEFORE touching the hash. Re-entering #/launching un-quiets.
  if (S.jump && parts[0] !== "launching") S.jump.quiet = true;
  if (parts[0] === "s" && parts[1]) {
    S.pendingUI = false;
    const sid = decodeURIComponent(parts[1]);
    if (parts[2] === "a" && parts[3]) return showAgent(sid, decodeURIComponent(parts[3]));
    if (parts[2] === "m" && parts[3]) return showMonitor(sid, decodeURIComponent(parts[3]));
    if (parts[2] === "j" && parts[3]) return showJob(sid, decodeURIComponent(parts[3]));
    return showSession(sid, parts[2] || "mirror");
  }
  if (parts[0] === "launching") {
    // the optimistic post-launch view — back in the waiting room, so auto-jump
    // again on arrival
    if (S.jump) { S.jump.quiet = false; return showPending(); }
    // the launch resolved quietly while the user was away — forward them to
    // the session that arrived (consumed once; a later visit hits the list)
    if (S.jumpDone) {
      const to = S.jumpDone;
      S.jumpDone = null;
      return location.replace(to);
    }
    // a reload / stale bookmark has nothing to wait for
    return location.replace("#/");
  }
  S.pendingUI = false;
  showList();
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

function showList() {
  leaveSession();
  renderList();
  if (!S.sessions.length)
    fetch("/api/sessions").then(r => r.json())
      .then(d => { S.sessions = d; renderList(); renderAttention(); });
}

function renderList(force) {
  if (S.cur || S.pendingUI) return;
  if (!S.sessions.length) {
    S.listKey = null;
    $view.textContent = "";
    $view.append(el("div", "empty", "no sessions recorded yet"));
    return;
  }
  // Same shape as the last full render (and its DOM is still mounted — a
  // session view wipes $view, so a stale card map must not be patched
  // blind) → update changed cards in place instead of rebuilding: the SSE
  // pushes a fresh snapshot every tick while anything is active, and a full
  // teardown per second lost hover/scroll state and burned layout for rows
  // that hadn't changed.
  const groups = groupSessions(S.sessions);
  const shape = listShape(groups);
  const anchor = S.cards.values().next().value;
  if (!force && shape === S.listKey && anchor && anchor.isConnected)
    return patchCards();
  $view.textContent = "";
  S.cards.clear();
  S.rowPrev.clear();
  renderDirGroups(groups);
  S.listKey = shape;
}

function groupSessions(rows) {
  // one group per PROJECT directory (ordered by its newest session); inside
  // each: active cards visible, parked / archived (>3d) as click-to-open
  // folds. The group key is the server's `group_dir`: the session's frozen
  // ORIGINAL cwd resolved to its linked-worktree owner. So N agents fanned out
  // over worktrees of one repo file under the main checkout (the per-card ⋔
  // chip tells them apart), AND a mid-session `cd` never moves a card between
  // groups — group_dir ignores the live cwd entirely (row.cwd is the fallback
  // for legacy/parked rows with no group_dir).
  const groups = new Map();
  for (const row of rows) {
    const k = row.group_dir || row.cwd || "";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(row);
  }
  const ordered = [...groups.entries()].sort((a, b) =>
    Math.max(...b[1].map(orderKey))
    - Math.max(...a[1].map(orderKey)));
  const now = Date.now() / 1000;
  // recency, not age: a week-old session touched yesterday isn't archived
  const old = r => !lastActive(r) || now - lastActive(r) > ARCHIVE_S;
  return ordered
    .filter(([k, grows]) => !dirHidden(k, grows))
    .map(([cwd, grows]) => ({
      cwd, count: grows.length,
      active: grows.filter(r => r.live),
      parked: grows.filter(r => !r.live && !old(r)),
      archived: grows.filter(r => !r.live && old(r)),
    }));
}

// A directory the ✕ hid (S.hidden holds its hide time) stays hidden only while
// NONE of its sessions started after that time — the moment a newer session
// appears (a fresh launch, terminal or dashboard, or a resume that re-stamps
// started_at) it re-shows. Purely client-side over the wire rows' started_at;
// the server only stores the {key: hidden_at} stamp.
function dirHidden(key, rows) {
  const t = S.hidden[key];
  if (t == null) return false;
  // never hidden while it has a LIVE session (matches the server's hide guard —
  // a directory with an active session can't be hidden, and one that GAINS a
  // live session re-shows at once) or one started after the hide stamp (a fresh
  // launch / resume re-shows it).
  return !rows.some(r => r.live || (r.started_at || 0) > t);
}

// The ✕ on a dir header: hide it from the list. Optimistic (stamp now, re-render
// so it vanishes immediately), then POST — the server stamps its OWN time.time()
// and returns the full map, which we adopt as truth. On failure the optimistic
// stamp is dropped and the group returns. Non-destructive: nothing is closed or
// removed; the group re-appears on the next session started there.
function hideDir(key) {
  S.hidden[key] = Date.now() / 1000;
  renderList(true);
  postJSON("/api/dirs/hide", { cwd: key })
    .then(d => { if (d && d.hidden) { S.hidden = d.hidden; renderList(true); } })
    .catch(err => {
      delete S.hidden[key];
      renderList(true);
      toast("ask", "hide failed", (err && err.error) || "");
    });
}

// What makes the list's SHAPE: group order, which cards are VISIBLE (active +
// open folds), fold counts/open state. Rows changing in place don't move the
// shape (they patch); anything here changing forces the full rebuild — so a
// live↔parked flip, a new session, or a fold toggle re-lays the list, while
// a stats tick only touches its own card.
function listShape(groups) {
  return JSON.stringify(groups.map(g => [
    g.cwd, g.active.map(r => r.sid),
    g.parked.length,
    S.folds.has(g.cwd + "|parked") ? g.parked.map(r => r.sid) : 0,
    g.archived.length,
    S.folds.has(g.cwd + "|archived") ? g.archived.map(r => r.sid) : 0,
  ]));
}

function renderDirGroups(groups) {
  for (const g of groups) {
    const hd = el("div", "dirhead");
    hd.append(el("span", "dirname", g.cwd ? g.cwd.split("/").filter(Boolean).pop() : "no project"));
    if (g.cwd) hd.append(el("span", "dirpath", g.cwd));
    hd.append(el("span", "dircount", g.count + (g.count === 1 ? " session" : " sessions")));
    if (g.cwd) {                        // "+" only where a launch has a cwd
      const add = el("button", "dirnew", "+");
      add.title = "new session in " + g.cwd;
      add.onclick = () => openNewSession(g.cwd);
      hd.append(add);
    }
    // ✕ hides ANY group, including the projectless aggregate (g.cwd === "") —
    // its group key is the empty string, which hideDir/the server accept.
    // DISABLED while the group has an active session: you can't hide a directory
    // you're actively working in (the server 409s too — this is just the visible
    // affordance + reason). The tooltip explains why rather than vanishing.
    const hide = el("button", "dirhide", "✕");
    if (g.active.length) {
      hide.disabled = true;
      hide.title = "can't hide — " + g.active.length
        + (g.active.length === 1 ? " active session here" : " active sessions here");
    } else {
      hide.title = g.cwd
        ? "hide this directory from the list (re-appears when a new session starts here)"
        : "hide the projectless sessions from the list (re-appears when a new one starts)";
    }
    hide.onclick = () => hideDir(g.cwd);
    hd.append(hide);
    $view.append(hd);
    if (g.active.length) {
      const grid = el("div", "sgrid");
      for (const row of g.active) grid.append(mountCard(row));
      $view.append(grid);
    }
    fold(g.cwd, "parked", g.parked);
    fold(g.cwd, "archived", g.archived);
  }
}

function mountCard(row) {
  const c = sessionCard(row);
  S.cards.set(row.sid, c);
  S.rowPrev.set(row.sid, JSON.stringify(row));
  return c;
}

// In-place update: same shape, so every visible row already has a card —
// rebuild the innards of just the cards whose row data changed. The card
// <a> itself survives, so scroll position, :hover, and the rest of the
// list's layout stay put.
function patchCards() {
  for (const row of S.sessions) {
    const card = S.cards.get(row.sid);
    if (!card) continue;
    const enc = JSON.stringify(row);
    if (enc === S.rowPrev.get(row.sid)) continue;
    S.rowPrev.set(row.sid, enc);
    const fresh = sessionCard(row);
    card.dataset.tab = fresh.dataset.tab;
    card.replaceChildren(...fresh.childNodes);
  }
}

// The REAL confirmation of an optimistic close: the sessions snapshot now shows
// the sid gone (or demoted to not-live) — the tab actually parked. Beacon the
// reconcile, drop the in-flight state, and un-grey the (about-to-be-rebuilt)
// card. Called on every sessions/-delta update, BEFORE the re-render so the
// rebuilt card shows the parked chip, not a stale 'closing…'.
function reconcileCloses() {
  for (const sid of Object.keys(S.closePend)) {
    const row = S.sessions.find(r => r.sid === sid);
    if (row && row.live) continue;             // still live — close hasn't landed
    clog(sid, "close.reconciled",
         { ms: Math.round(performance.now() - S.closePend[sid].t0) });
    S.closePend[sid].settle("reconciled");
    delete S.closePend[sid];
    S.closing.delete(sid);
    const card = S.cards.get(sid);
    if (card) card.classList.remove("closing");
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
    renderList(true);
  };
  $view.append(btn);
  if (open) {
    const grid = el("div", "sgrid folded");
    for (const row of rows) grid.append(mountCard(row));
    $view.append(grid);
  }
}

// Relative "ago" labels and the 3d archived boundary depend on the CLOCK,
// not on data — and with the server's paused-blind diff an idle list gets no
// sessions events at all, so nothing would ever re-run them. One full render
// a minute (registered at boot) keeps them honest for free.
const LIST_REFRESH_MS = 60000;

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
  else if (S.closing.has(row.sid)) {           // optimistic close in flight
    a.classList.add("closing");                // greyed until the sessions poll parks it
    corner.append(el("span", "chip2 closing", "closing…"));
  } else if (row.kitty_window_id)
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
  // recency, not age: started_at here read as staleness — a live session an
  // hour into its work showed "1h ago" while actively streaming
  if (lastActive(row)) r.append(seg(ago(lastActive(row))));
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
// Deliberately different from the header ✕ (which keeps closure-local arm
// state): the arm + in-flight state live in S (S.armClose/S.closing, keyed
// by sid, the arm as a DEADLINE not a timer handle) because the per-tick
// sessions push rebuilds a changed card wholesale (patchCards
// replaceChildren) — and a live card's row changes every tick, so
// closure/DOM-held state died within ~1s of arming: the confirm reverted
// before it could be clicked. The constructor re-derives both states, so a
// rebuilt button resumes the arm with the REMAINING window; stale timers
// from replaced predecessors are neutered by the sid+deadline check.
function cardClose(sid) {
  const btn = el("button", "xclose", "✕");
  btn.title = "close this session's terminal tab";
  btn.disabled = S.closing.has(sid);
  const armed = () =>
    S.armClose && S.armClose.sid === sid && Date.now() < S.armClose.until;
  const disarm = () => {
    if (S.armClose && S.armClose.sid === sid && Date.now() >= S.armClose.until)
      S.armClose = null;
    if (!armed()) { btn.textContent = "✕"; btn.classList.remove("arm"); }
  };
  const showArmed = () => {
    btn.textContent = "close?";
    btn.classList.add("arm");
    setTimeout(disarm, S.armClose.until - Date.now());
  };
  if (armed()) showArmed();          // rebuilt mid-arm: restore the confirm
  btn.onclick = (e) => {
    e.preventDefault(); e.stopPropagation();
    if (!armed()) {
      S.armClose = { sid, until: Date.now() + ARM_MS };
      showArmed();
      return;
    }
    S.armClose = null;
    btn.classList.remove("arm");
    btn.disabled = true;
    // optimistic: grey THIS card + swap the ✕ to 'closing…' at once (a rebuild
    // from the sessions poll may lag a tick), and beacon the `close` lifecycle
    // (web-hint op=close). reconcileCloses swaps it to the parked chip when the
    // snapshot shows the sid go not-live; a failed POST reverts.
    S.closing.add(sid);
    S.closePend[sid] = optPending(sid, "close");
    btn.textContent = "closing…";
    const a = btn.closest(".scard");
    if (a) a.classList.add("closing");
    closeSession(sid, "card")
      .then(() => toast("done", "session closed", "terminal tab closed"))
      .catch(err => {
        S.closing.delete(sid);
        if (S.closePend[sid]) {
          S.closePend[sid].settle("dropped", { reason: "failed" });
          delete S.closePend[sid];
        }
        btn.disabled = false;
        btn.textContent = "✕";
        if (a) a.classList.remove("closing");
        clientFail(sid, "close", err);   // a lost/rejected /stop the audit can't see
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
  if (!row || !S.ses) return;
  if (S.ses.badge) setBadge(S.ses.badge, row.tab || "");
  // Keep the header's live/window state honest against the authoritative global
  // snapshot. `meta` is fetched ONCE at session-open, so a session opened during
  // its startup tag-race — the launch jumps straight to the new sid, but its
  // kitty pane isn't tagged claude_session=<sid> yet, so the server momentarily
  // reports it not-live (or live-but-window-not-yet-resolved during the grace) —
  // would otherwise FREEZE on that reading: the parked chip stuck on and every
  // live-gated action (stop/cancel/rewind/close/quick-commands) missing, so the
  // user can't even close the session (the reported bug). A later live↔parked
  // flip (kill, crash, resume) has the same staleness. Re-render the chrome ONLY
  // on a real change — not every per-tick tab change (that reflows the header
  // each second) — and not while drilled into a subagent (renderSessionChrome
  // clears agentFocus; the ← session rebuild picks it up on the way back) or mid
  // inline-rename. The window compare is gated on row.live: meta's
  // kitty_window_id is the live-RESOLVED id (blank until the pane is tagged)
  // while the list row's is the RAW audit id, so an unconditional compare would
  // spuriously rebuild a parked session's header (blank vs a stale raw id).
  const m = S.ses.meta;
  const winMoved = row.live
    && (m && m.kitty_window_id || "") !== (row.kitty_window_id || "");
  if (m && (!!m.live !== !!row.live || winMoved)) {
    m.live = row.live;
    m.kitty_window_id = row.kitty_window_id;
    m.parked = row.parked;
    const renaming = S.ses.projEl && S.ses.projEl.querySelector("input");
    if (!S.ses.agentFocus && !S.ses.monitorFocus && !S.ses.jobFocus && !renaming)
      renderSessionChrome(S.ses.tab);
  }
}

/* ---------- session view ---------- */

function showSession(sid, tab) {
  // unknown / retired tab (e.g. an old #/…/activity bookmark) → the mirror
  if (!["mirror", "agents", "monitors", "jobs", "memory", "errors"].includes(tab)) tab = "mirror";
  if (S.cur !== sid) {
    leaveSession();
    S.cur = sid;
    S.ses = { lastId: 0, mpos: 0, oldest: 0, stream: el("div", "stream"), stats: {},
              agents: [], costs: null, ctx: null, running: {}, meta: null, es: null, agentEs: null,
              timer: null, poll: null, blocks: new Map(), moreEl: null,
              monitors: null, monitorFocus: null, monPoll: null,
              jobs: null, jobFocus: null, jobPoll: null,
              memory: null, noteTrail: null, noteFocus: null,
              loadingOlder: false, queue: [], pending: [],
              askPend: null, planPend: null,   // in-flight optimistic ask/plan decisions
              filter: { kind: "all" } };          // cleared per session (new S.ses)
    S.ses.stream.append(el("div", "waiting", "waiting for activity…"));
    // meta (live/kitty_window_id/title/…) comes ONLY from this fetch — global
    // snapshots never repair it (updateHeadFromList no-ops while meta is null),
    // so a transient failure left the whole view stuck unusable (composer
    // disabled, no title) until a reload. Retry while still on this session and
    // still unpopulated; the guards make a late retry after a leave a harmless
    // no-op.
    let resolveTries = 0;
    const loadMeta = () => fetch("/api/session/" + encodeURIComponent(sid))
      .then(r => r.json())
      .then(d => {
        if (S.cur !== sid || !S.ses) return;
        S.ses.meta = d;
        S.ses.stats = d.stats || {};
        S.ses.agents = d.agents || [];
        S.ses.costs = d.costs || null;
        S.ses.ctx = d.ctx || null;
        S.ses.running = d.running || {};
        renderSessionChrome(tab);
        // startup TAG-RACE self-heal: a just-launched session momentarily
        // reports live:true with a BLANK kitty_window_id (its kitty pane isn't
        // tagged claude_session=<sid> yet, so session_payload can't resolve the
        // window). That partial meta fails BOTH composer gates — canSend
        // (live && window) AND canResume (!live) — so the box locks and the
        // live-gated ✕ close button never renders (the reported "no close
        // button + can't type, fixed only by reload"). Re-fetch until the pane
        // tags: authoritative and self-healing where the fragile global-poll
        // heal (updateHeadFromList, raw-vs-resolved window id spaces) misses.
        // Bounded — a truly headless session never resolves a window.
        if (d.live && !d.kitty_window_id && resolveTries < LAUNCH_RESOLVE_TRIES) {
          resolveTries++;
          setTimeout(loadMeta, LAUNCH_RESOLVE_MS);
        } else if (d.live && !d.kitty_window_id) {
          // the tag-race NEVER resolved — the composer + ✕ close stay dead. The
          // "no close button, can't type, only a reload fixes it" report, now a
          // row instead of a mystery (vs a truly headless session, which never
          // has a window and is EXPECTED to land here).
          clog(sid, "meta.stuck", { tries: resolveTries });
        } else if (d.live && d.kitty_window_id && resolveTries > 0) {
          clog(sid, "meta.resolved", { tries: resolveTries });   // self-heal worked
        }
      })
      .catch(() => {
        clog(sid, "meta.fail", {});   // the session-view meta GET rejected
        if (S.cur === sid && S.ses && !S.ses.meta) setTimeout(loadMeta, 1500);
      });
    loadMeta();
    // Initial stream content over a plain GET, NOT the SSE fresh-connect
    // backlog: _send gzips this HTML 8-9x, while SSE frames are never
    // compressed — on a remote/tunnel connection that difference IS the
    // "waiting for activity…" wait. The SSE then connects with the returned
    // cursors and only streams increments (the same no-gap resume contract a
    // reconnect uses); on any fetch failure it connects with zero cursors
    // and the server-side SSE backlog covers us like before.
    fetch("/api/session/" + encodeURIComponent(sid) + "/backlog")
      .then(r => r.json())
      .then(d => {
        if (S.cur !== sid || !S.ses) return;
        S.ses.lastId = Math.max(S.ses.lastId, d.last | 0);
        S.ses.mpos = Math.max(S.ses.mpos, d.mpos | 0);
        if (d.oldest != null) { S.ses.oldest = d.oldest | 0; updateMoreBtn(); }
        if (d.items && d.items.length) appendItems(d.items);
      })
      .catch(() => { clog(sid, "backlog.fail", {}); })   // stream may read empty
      .finally(() => { if (S.cur === sid) connectSession(sid); });
  }
  closeAgentStream();                       // leaving any agent drill-down view
  S.ses.tab = tab;
  renderSessionChrome(tab);
}

function connectSession(sid) {
  if (!S.ses || S.cur !== sid) return;
  // Never leak a prior EventSource. Two backlog fetches can race a leave/return
  // to the SAME sid (on a slow/tunnel link the backlog is deliberately a plain
  // GET), and each fires this from its .finally; the onerror reconnect re-enters
  // too. Without closing first, the earlier ES is orphaned — never closed, still
  // streaming, and its overlapping ops double-append into the feed.
  if (S.ses.es) { try { S.ses.es.close(); } catch (e) { /* already closed */ } }
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
  es.addEventListener("stats", (e) => { if (!S.ses) return; S.ses.stats = JSON.parse(e.data); updateStatsRow(); });
  es.addEventListener("agents", (e) => { if (!S.ses) return; S.ses.agents = JSON.parse(e.data); updateAgents(); });
  es.addEventListener("costs", (e) => { if (!S.ses) return; S.ses.costs = JSON.parse(e.data); updateStatsRow(); });
  es.addEventListener("ctx", (e) => { if (!S.ses) return; S.ses.ctx = JSON.parse(e.data).ctx; updateStatsRow(); });
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
  es.addEventListener("running", (e) => { if (!S.ses) return; S.ses.running = JSON.parse(e.data); updateRunning(); });
  es.addEventListener("errors", (e) => { updateErrCount(JSON.parse(e.data).count | 0); });
  es.addEventListener("monitors", (e) => { updateMonCount(JSON.parse(e.data).count | 0); });
  es.addEventListener("jobs", (e) => { updateJobCount(JSON.parse(e.data).count | 0); });
  es.addEventListener("memory", (e) => { updateMemCount(JSON.parse(e.data).count | 0); });
  es.addEventListener("ask", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    const newAsk = d.ask || null;
    // the REAL confirmation of an optimistic answer: the stash we submitted
    // against is gone (cleared, or replaced by a different ask) — swap the
    // greyed card away and beacon the reconcile latency
    const pend = S.ses.askPend;
    if (pend && pend.live && (!newAsk || newAsk.tool_use_id !== pend.id)) {
      pend.settle("reconciled");
      S.ses.askPend = null;
    }
    if (S.ses.meta) S.ses.meta.ask = newAsk;
    renderAsk();
  });
  es.addEventListener("ask-draft", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    applyAskDraft(d.draft);
  });
  es.addEventListener("composer-draft", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    applyComposerDraft(d.draft);
  });
  es.addEventListener("composer-queue", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    applyComposerQueue(d.queue);
  });
  es.addEventListener("suggestion", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    applySuggestion(d.suggestion);
  });
  es.addEventListener("plan", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    const newPlan = d.plan || null;
    // real confirmation of an optimistic plan decision — the stash dropped
    const pend = S.ses.planPend;
    if (pend && pend.live && (!newPlan || newPlan.tool_use_id !== pend.id)) {
      pend.settle("reconciled");
      S.ses.planPend = null;
    }
    if (S.ses.meta) S.ses.meta.plan = newPlan;
    renderPlan();
  });
  es.addEventListener("tasks", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    if (S.ses.meta) S.ses.meta.tasks = d.tasks || null;
    renderTasks();
  });
  es.addEventListener("goal", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    if (S.ses.meta) S.ses.meta.goal = d.goal || null;
    renderGoal();
  });
  es.addEventListener("tab", (e) => {
    const d = JSON.parse(e.data);
    // while drilled into a subagent the badge/wash belong to that agent's
    // status (setBadgeAgent) — a session tab event must not repaint them
    // (same focus guard as updateRunning/updateStatsRow).
    if (S.ses && S.ses.badge && !S.ses.agentFocus) setBadge(S.ses.badge, d.tab || "");
    if (S.ses && S.ses.composerMode) S.ses.composerMode(d.tab || "");
    if (S.ses && S.ses.cancelMode) S.ses.cancelMode(d.tab || "");
    if (S.ses && S.ses.stopMode) S.ses.stopMode(d.tab || "");
    if (S.ses && S.ses.quickMode) S.ses.quickMode(d.tab || "");
    // patch the open session's row so the session strip reacts before the
    // next global snapshot lands (item 4: react to the per-session tab event)
    const row = S.sessions.find(r => r.sid === S.cur);
    if (row) row.tab = d.tab || "";
    renderAttention();
  });
  es.onopen = () => { $conn.dataset.on = "1"; sseMark("session", true, { sid }); };
  es.onerror = () => {
    sseMark("session", false, { sid });
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
const PREVIEW_BLOCKS = 12;     // recent mirror blocks in the resume-picker preview

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

// A single copy-group's body is capped: a long-lived group (a bg stream, a
// monitor, `tail -f`, a subagent) keeps emitting line/code/gut ops that all
// share ONE block id, and the `.stream` child cap in appendItems() only counts
// top-level cards — never the ops nested inside one — so without this a
// continuous stream grows the DOM without bound (one node per op, forever).
const MAX_BLOCK_BODY = 800;

// Add one grouped item to a block: label ops become summary chips, everything
// else appends to the body (and seeds the one-line summary). Body always reads
// oldest->newest (top-down), matching arrival order.
function fillBlock(b, it) {
  if (it.t === "label") {
    b.chips.insertAdjacentHTML("beforeend", it.html);
  } else {
    b.body.insertAdjacentHTML("beforeend", it.html);
    while (b.body.childElementCount > MAX_BLOCK_BODY)
      b.body.firstElementChild.remove();       // trim oldest (top) — arrival order
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
  drainPending(items);
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

// Render a self-contained mirror snapshot into an ARBITRARY container (the
// resume-picker's preview panel), not the live #stream. Same server items
// ({g,t,html}, oldest->newest) and same block grouping as appendOlder, but into
// a throwaway local map (never S.ses) and with blocks OPEN — a read-only peek at
// a conversation's recent mirror transcript, so no folding/filters/eviction.
function renderPreview(container, items) {
  container.textContent = "";
  const local = new Map();                        // g -> block, this render only
  for (const it of items) {
    if (!it.g) {
      const tmp = el("div");
      tmp.innerHTML = it.html;
      const elem = tmp.firstElementChild;
      if (elem) container.append(elem);
      continue;
    }
    let b = local.get(it.g);
    if (!b) {
      b = createBlock();
      b.root.dataset.open = "1";                  // previews render expanded
      local.set(it.g, b);
      container.append(b.root);
    }
    fillBlock(b, it);
    refineBlockKind(b, it);
  }
  if (!container.childElementCount)
    container.append(el("div", "nspreview-empty", "no mirror history"));
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
  // memory-wiki file ops carry data-mem (🧠) — their own kind, checked before
  // the generic files test (a memory op is also a data-v file op).
  if (elem.matches("[data-mem]") || elem.querySelector("[data-mem]")) return "memory";
  // file-op one-liners carry the click-to-view id as data-v (.opl / gut ops)
  if (elem.matches("[data-v]") || elem.querySelector("[data-v]")) return "files";
  return "commands";
}

function streamItems() {
  return [...S.ses.stream.children].filter(el => el.dataset && el.dataset.kind);
}

function matchesFilter(elem) {
  const f = (S.ses && S.ses.filter) || { kind: "all" };
  if (f.kind !== "all" && elem.dataset.kind !== f.kind) return false;
  return true;
}

function applyFilterTo(elem) {
  if (!elem || !elem.dataset || !elem.dataset.kind) return;
  elem.classList.toggle("fhide", !matchesFilter(elem));
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

const FILTER_KINDS = ["all", "commands", "files", "memory", "agents", "messages"];

function buildFilterBar() {
  const ses = S.ses;
  const f = ses.filter;
  const bar = el("div", "fbar");

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

  const count = el("span", "fcount");
  ses.countEl = count;
  bar.append(chipwrap, count);
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
// delivery (it reaches the transcript only when the turn ends), so it shows as
// an amber "⧗ queued" prompt bubble PINNED at the top of the transcript — above
// the newest-first stream, so incoming activity never buries it — until its
// prompt record actually arrives in the stream (drainQueue — matched by text;
// tab transitions are useless as a delivery signal since green flips busy again
// the instant a queued prompt starts processing). At that point drainQueue drops
// the pinned bubble and the delivered prompt appears in the stream itself. ✕
// only removes the marker — the message is already in the TUI's queue and the
// web can't unqueue it.

const QUEUE_TABS = ["thinking", "working", "executing"];

function buildQueuePin() {
  const q = el("div", "pinq");
  q.hidden = true;
  S.ses.queueEl = q;
  // restore the pinned queued messages persisted server-side (composer-queue kv)
  // so a reload / device switch keeps showing what the TUI still holds unqueued —
  // seed only when the in-memory queue is empty (a live session already has its
  // entries); drainQueue reconciles them out as their prompts arrive.
  const cq = S.ses.meta && S.ses.meta.composer_queue;
  if (cq && Array.isArray(cq.items) && !S.ses.queue.length)
    S.ses.queue = cq.items.map(it => ({ text: (it && it.text) || "" }));
  renderQueue();
  return q;
}

// Persist the WHOLE current chip list to the server (composer-queue kv) so it
// survives a reload; called on every queue mutation (queued-send, delivery
// drain, ✕-hide). Best-effort — a failed write just retries on the next
// change. meta is kept in sync so our own SSE echo is a no-op.
function saveQueue(ses) {
  ses = ses || S.ses;
  if (!ses || !S.cur) return;
  const items = ses.queue.map(m => ({ text: m.text }));
  if (ses.meta)
    ses.meta.composer_queue = items.length ? { items, origin: CLIENT_ID } : null;
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/composer-queue",
           { items, origin: CLIENT_ID }).catch(() => {});
}

// A peer device's (or our own reload's) queue update arrived over SSE — adopt
// it, ignoring our OWN echo (same origin) so a local drain isn't clobbered.
function applyComposerQueue(q) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.composer_queue = q || null;
  if (q && q.origin && q.origin === CLIENT_ID) return;   // our own write
  ses.queue = ((q && q.items) || []).map(it => ({ text: (it && it.text) || "" }));
  renderQueue();
}

// Paint the queued messages as amber "⧗ queued" prompt bubbles, pinned at the
// top of the transcript until each is delivered (drainQueue removes it, and the
// real prompt bubble then arrives in the stream). Mirrors opshtml.msg_html's
// .msg.prompt shape (minus the rewind ↶ — a not-yet-delivered prompt isn't
// re-runnable), plus a ⧗ badge and a ✕ to drop a stale marker.
function renderQueue() {
  const ses = S.ses;
  if (!ses || !ses.queueEl) return;
  const q = ses.queueEl;
  q.textContent = "";
  q.hidden = !ses.queue.length;
  ses.queue.forEach((m, i) => {
    const d = el("div", "msg prompt queued");
    d.title = "queued in the terminal — delivers when this turn ends";
    const who = el("span", "who");
    who.append(document.createTextNode("you"), el("span", "qbadge", "⧗ queued"));
    d.append(who);
    const x = el("button", "qx", "✕");
    x.title = "remove this queued marker (the message stays queued in the terminal)";
    x.onclick = () => { ses.queue.splice(i, 1); renderQueue(); saveQueue(ses); };
    d.append(x);
    d.append(promptMd(m.text));
    q.append(d);
  });
}

function drainQueue(items) {
  const ses = S.ses;
  if (!ses || !ses.queue || !ses.queue.length) return;
  let hit = false;
  for (const it of items) {
    if (it.t !== "msg" || it.kind !== "prompt") continue;
    const real = (it.text || "").trim();
    // exact match, or (attachments prepend leading @path mentions +\n) the real
    // text ends with the queued suffix — same tolerant match as drainPending,
    // since a queued message with attachments arrives as `@path\n<text>`.
    const i = ses.queue.findIndex(m => real === m.text || real.endsWith("\n" + m.text));
    if (i >= 0) { ses.queue.splice(i, 1); hit = true; }
  }
  if (hit) { renderQueue(); saveQueue(ses); }
}

/* ---------- optimistic prompt bubbles (the composer's own send) ---------- */
// A sent message reaches the transcript only once Claude Code writes its user
// prompt record and the server pushes the `msgs` SSE event — a visible gap
// after the paste lands. To close it, send() prepends a GREYED stand-in bubble
// (.msg.prompt.pending) the instant it POSTs; drainPending removes it when the
// matching REAL prompt arrives (the server-rendered bubble takes its place), or
// send() removes it directly on failure / when the send was queued (the pinned
// ⧗ queued bubble owns that case). DOM-only + in-memory (ses.pending) — a reload
// replays from the real transcript, so nothing is persisted and stand-ins can't leak.
//
// The stand-in's whole lifecycle is client-only, so the SERVER can't see it —
// a stuck grey bubble (shown, never reconciled) leaves no trace by default. So
// each transition beacons a `web-hint` audit row (hintAudit → POST /hint-audit,
// server-side A.state_file): `shown` on create, `reconciled` on the swap
// (carrying wait_ms — the swap latency), `dropped` on queued/send-failed, and
// `stale` from a watchdog when a stand-in outlives STALE_HINT_MS unreconciled
// (THE bug signal). Audit-only, best-effort, never blocks or toasts.

const STALE_HINT_MS = 20000;

// Low-level optimistic-action audit beacon: ONE lifecycle transition of a
// client action whose REAL confirmation arrives async over SSE (op = composer
// bubble | close | answer | plan — docs/dashboard.md, *Optimistic UI & the
// web-hint audit*). A stuck greyed state is invisible server-side without this.
// Best-effort, never surfaces to the user.
function optAudit(sid, op, phase, t0, extra) {
  if (!sid) return;
  const body = Object.assign(
    { op, phase, wait_ms: Math.round(performance.now() - t0) }, extra || {});
  postJSON("/api/session/" + encodeURIComponent(sid) + "/hint-audit", body)
    .catch(() => {});   // a telemetry beacon must never surface to the user
}

// The composer bubble's beacon — op="composer", carries the message length.
function hintAudit(pend, phase, extra) {
  if (!pend || !pend.sid) return;
  optAudit(pend.sid, "composer", phase, pend.t0,
           Object.assign({ chars: (pend.text || "").length }, extra || {}));
}

// A tracked optimistic CARD action (close | answer | plan): beacons `shown` +
// arms a stale watchdog; the caller holds the handle and calls .settle(phase,
// extra) on the SSE reconcile (`reconciled`) or on failure (`dropped`). `id`
// is the tool_use_id / sid the confirmation is matched against; `note` is the
// greyed card's caption. Sibling of addPending (the composer bubble's own
// tracker), minus the DOM node — the card flows grey an existing element.
function optPending(sid, op, id, note) {
  const p = { sid, op, id: id || "", note: note || "",
              t0: performance.now(), timer: null, live: true };
  optAudit(sid, op, "shown", p.t0);
  p.timer = setTimeout(() => {
    p.timer = null;
    if (p.live) optAudit(sid, op, "stale", p.t0);   // stuck greyed — the bug signal
  }, STALE_HINT_MS);
  p.settle = (phase, extra) => {
    if (!p.live) return;
    p.live = false;
    if (p.timer) { clearTimeout(p.timer); p.timer = null; }
    optAudit(sid, op, phase, p.t0, extra);
  };
  return p;
}

// A greyed "…" stand-in shown in place of the interactive ask/plan card while
// an optimistic decision is in flight — the card analog of the composer's
// greyed prompt bubble. Cleared when the SSE reconcile drops the stash (or on
// failure, which re-renders the live card). `cls` = askcard | plancard.
function pendingCard(cls, title, note) {
  const card = el("div", cls + " pending");
  const head = el("div", "askhead");
  head.append(el("span", cls === "plancard" ? "plantitle" : "asktitle", title));
  card.append(head);
  card.append(el("div", "plandim", note));
  return card;
}

// Beacon a control-plane failure the PAGE saw (a "send failed" / "resume
// failed" toast) into the audit — a `web-clientfail` row. The server audits
// each gesture's outcome BEFORE its HTTP response returns, so a lost response
// (server restart, tunnel reset, dropped connection) rejects the fetch and
// toasts a failure even when the send SUCCEEDED — invisible to the audit
// otherwise (docs/dashboard.md, *Client-observed send failures*). `err` is a
// postJSON rejection: an HTTP-error body ({error}) → kind "http"; a raw
// fetch TypeError (no .error) → kind "transport" (the audit-blind case). The
// beacon rides the same tunnel that may have failed, so it's strictly
// best-effort — the toast is the user-facing signal, this is the breadcrumb.
function clientFail(sid, gesture, err, chars) {
  if (!sid) return;
  const http = !!(err && err.error);
  const body = { gesture, kind: http ? "http" : "transport",
                 error: (err && (err.error || err.message)) || "" };
  if (http && typeof err.status === "number") body.status = err.status;
  if (typeof chars === "number") body.chars = chars;
  postJSON("/api/session/" + encodeURIComponent(sid) + "/client-fail", body)
    .catch(() => {});   // a telemetry beacon must never surface to the user
}

// A snapshot of the page's connection health, stamped on every clog batch — the
// evidence for the connection-starvation theory (the page's long-lived SSE
// EventSource streams eating the HTTP/1.1 pool). `es` is the count of SSE streams
// we hold open right now (global always + the session view's own + the agent
// drill-down's), `conn` whether the global stream is currently connected, `online`
// / `vis` the browser's own network + tab-visibility state.
function connInfo() {
  return {
    online: navigator.onLine !== false,
    vis: document.visibilityState || "",
    view: S.cur ? "session" : (S.pendingUI ? "launching" : "list"),
    es: 1 + (S.cur ? 1 : 0) + (S.ses && S.ses.agentEs ? 1 : 0),
    conn: $conn && $conn.dataset.on === "1" ? 1 : 0,
  };
}

// Append one frontend-audit event and schedule a batched flush. `ev` is a dotted
// name (close.begin | close.ok | close.fail | close.reconciled …); `data` is a
// small flat bag of scalars. Ring-capped so a delivery outage can't grow it.
// SELF-GUARDING: the audit must never throw into the page — an exception here
// would fire window.onerror → clog → … a feedback loop, and this very channel is
// what CATCHES uncaught errors, so it must be the one thing that can't raise one.
function clog(sid, ev, data) {
  if (clogBusy) return;                 // re-entrancy: don't log from inside a flush
  try {
    CLOG.push(Object.assign({ t: Date.now(), sid: sid || "", ev }, data || {}));
    while (CLOG.length > CLOG_MAX) CLOG.shift();
    if (!clogTimer) clogTimer = setTimeout(flushClog, CLOG_FLUSH_MS);
  } catch (e) { /* swallow — a broken breadcrumb must not break the page */ }
}

// Deliver the buffered events as ONE POST over the plain-fetch channel proven to
// traverse the tunnel (NOT sendBeacon — the very transport that silently vanished
// the close). On a page-hide we do fall back to sendBeacon (a last-ditch flush as
// the tab goes away is exactly beacon's job, and losing the tail then is fine). A
// failed delivery re-queues (front, capped) and retries on a backoff so a blip
// doesn't lose the breadcrumb. Best-effort; wrapped so a throw in here (e.g. a
// timer callback) can never reach window.onerror and loop back through clog.
function flushClog(useBeacon) {
  if (clogTimer) { clearTimeout(clogTimer); clogTimer = null; }
  if (!CLOG.length) return;
  clogBusy = true;
  try {
    const batch = CLOG.splice(0, CLOG.length);
    const payload = { client: CLIENT_ID, conn: connInfo(), events: batch };
    if (useBeacon && navigator.sendBeacon) {
      try {
        navigator.sendBeacon("/api/clientlog",
          new Blob([JSON.stringify(payload)], { type: "application/json" }));
        return;
      } catch (e) { /* fall through to the fetch path */ }
    }
    postJSON("/api/clientlog", payload).catch(() => {
      for (let i = batch.length - 1; i >= 0 && CLOG.length < CLOG_MAX; i--)
        CLOG.unshift(batch[i]);   // re-queue at the front for the retry
      if (!clogTimer) clogTimer = setTimeout(flushClog, CLOG_RETRY_MS);
    });
  } catch (e) { /* never throw out of the audit */ }
  finally { clogBusy = false; }
}

// Log an SSE stream's up/down TRANSITION (open ↔ drop) — the direct read on the
// connection-pool health the control POSTs compete for. EventSource.onerror
// re-fires on every reconnect attempt, so gate on the last-known state.
function sseMark(label, up, extra) {
  if (SSE_UP[label] === up) return;
  SSE_UP[label] = up;
  clog((extra && extra.sid) || S.cur || "", up ? "sse.open" : "sse.drop",
       Object.assign({ s: label }, extra || {}));
}

// The close POST rides the plain-fetch channel (postJSON — X-Claude-Dash header,
// JSON body, a CLOSE_POST_MS timeout), tagged `audit:"close"` so its whole
// transport lifecycle lands in the frontend audit (close.begin/ok/fail). This is
// the transport PROVEN to traverse the tunnel (baqylau/dash.zhambyl.top): the
// click's own /hint-audit beacon and the composer /message ride it and always
// land, and every morning-era close (plain fetch) succeeded. navigator.sendBeacon
// was tried instead and REGRESSED close — it returns true (queued) so we resolved
// ok optimistically, but the queued beacon was then silently dropped by the
// tunnel: no `web-stop`, no `web-reject`, just the 20s `web-hint … stale`. The
// timeout turns a genuine upstream stall into a VISIBLE, retryable, audited
// failure (close.fail transport + web-clientfail) instead of a silent hang.
function closeSession(sid, via) {
  const url = "/api/session/" + encodeURIComponent(sid) + "/stop";
  return postJSON(url, {}, { timeout: CLOSE_POST_MS, audit: "close", sid,
                             auditData: { via: via || "" } });
}

// The .md body of a not-yet-delivered prompt bubble (the optimistic stand-in and
// the pinned queued bubble): the text with hard line breaks, textContent only —
// never innerHTML, since an undelivered prompt must never interpret markup.
function promptMd(text) {
  const md = el("div", "md");
  const p = el("p");
  (text || "").split("\n").forEach((line, i) => {
    if (i) p.append(el("br"));
    p.append(document.createTextNode(line));
  });
  md.append(p);
  return md;
}

// Plain-text bubble mirroring opshtml.msg_html's .msg.prompt shape, minus the
// rewind ↶ (a not-yet-delivered prompt isn't re-runnable).
function pendingBubble(text) {
  const d = el("div", "msg prompt pending");
  d.append(el("span", "who", "you"));
  d.append(promptMd(text));
  return d;
}

// Create + track the optimistic stand-in for a send; returns its pend handle.
function addPending(ses, text) {
  const node = pendingBubble(text);
  const w = ses.stream.querySelector(".waiting");
  if (w) w.remove();
  ses.stream.insertBefore(node, ses.stream.firstChild);
  const pend = { text, node, ses, sid: S.cur, t0: performance.now(), timer: null };
  ses.pending.push(pend);
  hintAudit(pend, "shown");
  // watchdog: a stand-in still unreconciled after STALE_HINT_MS is a stuck
  // grey bubble — the failure this audit exists to catch. Fire the beacon once
  // and KEEP the node (the user is still staring at grey; the row is the
  // breadcrumb). Cleared by settlePending / leaveSession on a clean outcome.
  pend.timer = setTimeout(() => {
    pend.timer = null;
    if (pend.ses.pending.indexOf(pend) >= 0) hintAudit(pend, "stale");
  }, STALE_HINT_MS);
  return pend;
}

// Tear a stand-in down (matched | queued | send-failed) and audit the outcome.
function settlePending(pend, phase, extra) {
  if (!pend) return;
  if (pend.timer) { clearTimeout(pend.timer); pend.timer = null; }
  const i = pend.ses.pending.indexOf(pend);
  if (i >= 0) pend.ses.pending.splice(i, 1);
  if (pend.node) pend.node.remove();
  hintAudit(pend, phase, extra);
}

function drainPending(items) {
  const ses = S.ses;
  if (!ses || !ses.pending || !ses.pending.length) return;
  for (const it of items) {
    if (it.t !== "msg" || it.kind !== "prompt") continue;
    const real = (it.text || "").trim();
    // exact match, or (attachments prepend leading @path mentions +\n) the
    // real text ends with the typed suffix — server._with_attachments order
    const i = ses.pending.findIndex(p =>
      real === p.text || real.endsWith("\n" + p.text));
    if (i >= 0) settlePending(ses.pending[i], "reconciled");
  }
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

// ---- the pinned goal card (docs/dashboard.md, *Web goal*) -------------------
// Claude Code's `/goal <condition>` built-in puts the session into autonomous
// mode toward a completion condition. No hook fires for it, so the server scans
// the transcript tail (session_goal → plugins.goal → transcript.goal_probe) and
// pushes {condition, met} on the `goal` SSE event. Pinned at the very top of the
// mirror tab (above tasks), amber while working and green "✓ achieved" once the
// checker confirms; hidden when there is no active goal. Read-only — the goal is
// set/cleared at the terminal (or via the composer's `/goal`), never here.

function buildGoalCard() {
  const wrap = el("div", "goalwrap");
  S.ses.goalEl = wrap;
  renderGoal();
  return wrap;
}

function renderGoal() {
  const ses = S.ses;
  if (!ses || !ses.goalEl) return;
  const wrap = ses.goalEl;
  wrap.textContent = "";
  const goal = (ses.meta && ses.meta.goal) || null;
  wrap.hidden = !goal || !goal.condition;
  if (wrap.hidden) return;
  const met = !!goal.met;
  const card = el("div", "goalcard" + (met ? " met" : ""));
  const head = el("div", "goalhead");
  head.append(el("span", "goalmark", met ? "✓" : "🎯"));
  head.append(el("span", "goaltitle", "goal"));
  head.append(el("span", "goalstate", met ? "achieved" : "active"));
  card.append(head);
  card.append(el("div", "goalcond", goal.condition));
  wrap.append(card);
}

// ---- the pinned tasks card (docs/dashboard.md, *Web tasks*) -----------------
// The session's native task list (TaskCreate/TaskUpdate), pinned at the very
// top of the mirror tab — fed by the `tasks` kv snapshot task_fmt.py re-reads
// from Claude Code's on-disk task dir on every task-touching hook, so it works
// live AND parked (the on-disk files are deleted at session end; the stash is
// the only surviving record). Read-only: unlike ask/plan there is no dialog to
// drive — the TUI has no modal to answer. Completed tasks render struck-through
// and dimmed; the in_progress one carries the accent and shows its activeForm.

function buildTasksCard() {
  const wrap = el("div", "taskswrap");
  S.ses.tasksEl = wrap;
  renderTasks();
  return wrap;
}

function renderTasks() {
  const ses = S.ses;
  if (!ses || !ses.tasksEl) return;
  const wrap = ses.tasksEl;
  wrap.textContent = "";
  const tasks = (ses.meta && ses.meta.tasks) || null;
  wrap.hidden = !tasks || !tasks.length;
  if (wrap.hidden) return;
  const done = tasks.filter(t => t.status === "completed").length;
  const card = el("div", "taskscard");
  const head = el("div", "taskshead");
  head.append(el("span", "taskstitle", "tasks"));
  head.append(el("span", "taskscount", done + "/" + tasks.length + " done"));
  card.append(head);
  const list = el("div", "tasklist");
  tasks.forEach(t => {
    const st = t.status === "completed" ? "done"
             : t.status === "in_progress" ? "active" : "pend";
    const row = el("div", "taskrow " + st);
    row.append(el("span", "taskmark",
                  st === "done" ? "✓" : st === "active" ? "▸" : "○"));
    row.append(el("span", "taskid", "#" + (t.id || "?")));
    const subj = el("span", "tasksubj", t.subject || "");
    if (t.description) subj.title = t.description;
    row.append(subj);
    // the spinner label the TUI shows while a task runs
    if (st === "active" && t.activeForm && t.activeForm !== t.subject)
      row.append(el("span", "taskactive", t.activeForm + "…"));
    if ((t.blockedBy || []).length)
      row.append(el("span", "taskblocked",
                    "⛓ " + t.blockedBy.map(b => "#" + b).join(" ")));
    list.append(row);
  });
  card.append(list);
  wrap.append(card);
}

function buildAskCard() {
  const wrap = el("div", "askwrap");
  S.ses.askEl = wrap;
  renderAsk();
  return wrap;
}

// A preview-layout question (any option carries a `preview`) renders the TUI's
// side-by-side dialog, which OMITS the numbered "Type something" free-text row
// — so a TYPED answer can't be driven (askdialog._require_type_row). The card
// routes typed answers on such asks through "Chat about this" instead
// (docs/dashboard.md, *Web ask*).
function askHasPreview(ask) {
  return (ask && ask.questions || []).some(
    q => (q.options || []).some(o => o && o.preview));
}

function renderAsk() {
  const ses = S.ses;
  if (!ses || !ses.askEl) return;
  const wrap = ses.askEl;
  wrap.textContent = "";
  const ask = ses.meta && ses.meta.ask;
  wrap.hidden = !ask;
  if (!ask) return;
  // an optimistic answer is in flight — show the card greyed until the SSE
  // `ask` reconcile drops the stash (or a failure clears askPend and rebuilds
  // the interactive card). Reasserted on every render so a stray draft/rebuild
  // can't resurrect the live controls mid-submit.
  if (ses.askPend && ses.askPend.live) {
    wrap.append(pendingCard("askcard", "submitting answer…", ses.askPend.note));
    return;
  }
  const qs = ask.questions || [];
  const preview = askHasPreview(ask);
  // per-ask draft state, keyed by tool_use_id so a NEW ask resets it —
  // SEEDED from the persisted `ask-draft` (ses.meta.ask_draft) so a device
  // switch / reopen restores whatever selections were made but not submitted
  if (!ses.askState || ses.askState.id !== ask.tool_use_id)
    ses.askState = { id: ask.tool_use_id,
                     answers: seedAskAnswers(qs, ses.meta && ses.meta.ask_draft,
                                             ask.tool_use_id) };
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
  // Claude's prose lead-in to the question (server-rendered md_html, escape-
  // first like op HTML) — the "why", which the terse dialog omits; shown
  // above the questions so the context rides ON the card, not just as a
  // detached stream bubble (docs/dashboard.md, *Web ask*). Empty when Claude
  // asked with no framing text.
  if (ask.preamble_html) {
    const pre = el("div", "askpreamble md");
    pre.innerHTML = ask.preamble_html;
    card.append(pre);
  }
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
          // single-select: the option becomes the answer, but KEEP any typed
          // custom text (it stays in the field, re-selectable on focus) — the
          // old clear here was silent data loss. Submit sends other:"" while an
          // option is selected, so the lingering text never hijacks the answer.
          a.selected = [o.label];
        }
        paintAll();
        paintOther();
        syncSubmit();
        saveAskDraft(ask, st);
      };
      opts.append(b);
    });
    qbox.append(opts);
    const other = el("input", "askother");
    other.type = "text";
    other.spellcheck = false;
    // A PREVIEW-layout question's TUI dialog has no free-text row, so a typed
    // answer can't be delivered as an option — but "Chat about this" IS
    // reachable (the _cursor_to two-❯ fix, 2026-07-20), so a typed answer here
    // is ROUTED through chat and delivered as a follow-up message (submitAsk).
    other.placeholder = preview
      ? "type a custom answer → sent via “chat about this”"
      : q.multiSelect
        ? "add your own answer…" : "or type your own answer…";
    other.value = st.answers[qi].other;
    // red border == this custom text IS the active answer. multiSelect: whenever
    // it holds text (additive to any checked options). single-select: only while
    // no option is selected (a clicked option is the answer then; the text stays
    // but sits dormant, borderless — derived, no extra state).
    const otherIsAnswer = () => !!other.value.trim()
      && (q.multiSelect || !st.answers[qi].selected.length);
    const paintOther = () => other.classList.toggle("on", otherIsAnswer());
    other.oninput = () => {
      st.answers[qi].other = other.value;
      if (!q.multiSelect && other.value.trim()) {
        st.answers[qi].selected = [];         // typing (re)claims the answer
        paintAll();
      }
      paintOther();
      syncSubmit();
      saveAskDraft(ask, st);
    };
    // clicking BACK into a non-empty custom field reclaims it as the answer
    // (deselects the option) — no retype needed, and the text was never lost
    other.onfocus = () => {
      if (!q.multiSelect && other.value.trim() && st.answers[qi].selected.length) {
        st.answers[qi].selected = [];
        paintAll();
        paintOther();
        syncSubmit();
        saveAskDraft(ask, st);
      }
    };
    other.onkeydown = (e) => {
      e.stopPropagation();                  // keep Esc/gestures out of typing
      if (e.key === "Enter" && other.value.trim() && !sub.disabled)
        submitAsk(ask, st.answers, false);
    };
    qbox.append(other);
    paintAll();
    paintOther();
    card.append(qbox);
  });
  const foot = el("div", "askfoot");
  foot.append(sub);
  sub.onclick = () => submitAsk(ask, st.answers, false);
  card.append(foot);
  syncSubmit();
  wrap.append(card);
}

// Build the per-question answer array, seeding from a persisted draft when it
// belongs to THIS ask (tool_use_id + question count match) — otherwise fresh.
function seedAskAnswers(qs, draft, tuid) {
  const blank = () => qs.map(() => ({ selected: [], other: "" }));
  if (!draft || draft.tool_use_id !== tuid
      || !Array.isArray(draft.answers) || draft.answers.length !== qs.length)
    return blank();
  return draft.answers.map(a => ({
    selected: Array.isArray(a && a.selected) ? a.selected.slice() : [],
    other: (a && a.other) || "",
  }));
}

// Persist the unsubmitted selections to the server (debounced) so a reopen on
// any device restores them. Best-effort — a failed save just retries on the
// next edit; the local card keeps its state regardless.
function saveAskDraft(ask, st) {
  const ses = S.ses;
  if (!ses || !S.cur || !ask || !ask.tool_use_id) return;
  const answers = st.answers.map(a =>
    ({ selected: a.selected.slice(), other: a.other || "" }));
  // keep meta in sync so a tab-switch rebuild seeds from what we just typed,
  // and so our own SSE echo (same origin) is a no-op against current state
  if (ses.meta)
    ses.meta.ask_draft = { tool_use_id: ask.tool_use_id, origin: CLIENT_ID,
                           answers };
  clearTimeout(ses._askDraftTimer);
  ses._askDraftTimer = setTimeout(() => {
    postJSON("/api/session/" + encodeURIComponent(S.cur) + "/ask-draft",
             { tool_use_id: ask.tool_use_id, origin: CLIENT_ID, answers })
      .catch(() => {});                       // draft save is best-effort
  }, ASK_DRAFT_DEBOUNCE_MS);
}

// A peer device's draft update arrived over SSE. Adopt it and repaint the card
// — but ignore our OWN echo (same origin), and stale drafts (wrong ask).
function applyAskDraft(draft) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.ask_draft = draft || null;   // for a later rebuild
  if (ses.askPend && ses.askPend.live) return;   // don't un-grey a submitting card
  const ask = ses.meta && ses.meta.ask;
  if (!draft || !ask || draft.tool_use_id !== ask.tool_use_id) return;
  if (draft.origin && draft.origin === CLIENT_ID) return;   // our own write
  if (!ses.askState || ses.askState.id !== ask.tool_use_id) return;
  // don't yank the card out from under an ACTIVE local edit: renderAsk()
  // rebuilds the DOM (wrap.textContent = ""), which would drop focus + caret
  // mid-keystroke on the device that's typing. Skip while the card holds
  // focus — ses.meta.ask_draft is already updated above, so the next remote
  // change (or a manual rebuild) applies it once the field blurs.
  if (ses.askEl && ses.askEl.contains(document.activeElement)) return;
  ses.askState.answers = (draft.answers || []).map(a =>
    ({ selected: Array.isArray(a && a.selected) ? a.selected.slice() : [],
       other: (a && a.other) || "" }));
  renderAsk();
}

function submitAsk(ask, answers, chat) {
  const ses = S.ses;
  if (!ses || !S.cur) return;
  // A TYPED answer on a preview-layout question has no free-text row in the TUI
  // dialog, so route it through "Chat about this" (now keyboard-reachable — the
  // _cursor_to two-❯ fix) and ride the typed text as `message`: the server
  // presses chat, waits for the dialog to close, then delivers the text as a
  // message so the custom answer reaches the session (docs/dashboard.md, *Web
  // ask*). Explicit "chat about this" (answers == null) is untouched.
  // the custom text counts as the answer only when it's ACTIVE: multiSelect
  // (additive) or single-select with no option chosen. A single-select option
  // wins → send other:"" so the (preserved-but-dormant) text can't override it
  // (askdialog._answer_question gives `other` precedence over `selected`).
  const qs = ask.questions || [];
  const effOther = (a, i) => {
    const t = (a.other || "").trim();
    return t && ((qs[i] && qs[i].multiSelect) || !(a.selected || []).length)
      ? t : "";
  };
  let message = "";
  if (!chat && answers && askHasPreview(ask)) {
    const typed = answers.map(effOther).filter(Boolean);
    if (typed.length) { chat = true; message = typed.join("\n"); }
  }
  const body = { tool_use_id: ask.tool_use_id || "" };
  if (chat) { body.chat = true; if (message) body.message = message; }
  else body.answers = (answers || []).map((a, i) =>
    ({ selected: a.selected, other: effOther(a, i) }));
  // optimistic: grey the card immediately (renderAsk shows the pending stand-in
  // while askPend is live) and keep it until the SSE `ask` reconcile drops the
  // stash — NOT the old hide-on-POST-return, which claimed done before the
  // answer had actually landed. The lifecycle is beaconed as `web-hint` op=answer.
  const note = chat
    ? (message ? "delivering your answer via chat…" : "dismissing the questions…")
    : "answer submitted — waiting for the session…";
  ses.askPend = optPending(S.cur, "answer", ask.tool_use_id || "", note);
  renderAsk();
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/answer", body,
           { audit: "answer" })
    .then(() => {
      if (chat) {
        if (message)
          toast("done", "answer sent via chat",
                "your typed answer was delivered as a message");
        else
          toast("done", "over to chat",
                "questions dismissed — type your message below");
        if (!message && ses.composer) ses.composer.focus();
      } else {
        toast("done", "answered", "answers submitted to the session");
      }
      // stay greyed — the SSE `ask` event (stash cleared by the answer's
      // PostToolUse) is the real confirmation that swaps the card away
    })
    .catch(e => {
      // a cursor/type bail means the driver couldn't steer the TUI dialog (a
      // Claude Code layout change, or a rare mis-read) — suggest the fallback
      // so the user isn't stuck on a bare "failed"
      const step = e && e.step;
      const hint = (step === "cursor" || step === "type")
        ? "couldn't drive the dialog — pick an option, or answer in the terminal"
        : (e && e.error) || "";
      if (ses.askPend) {
        ses.askPend.settle("dropped", { reason: step || "failed" });
        ses.askPend = null;
      }
      toast("ask", "answer failed", hint);
      renderAsk();                           // rebuild the interactive card to retry
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
  // an optimistic decision is in flight — grey the card until the SSE `plan`
  // reconcile drops the stash (or a failure clears planPend and rebuilds it)
  if (ses.planPend && ses.planPend.live) {
    wrap.append(pendingCard("plancard", "sending decision…", ses.planPend.note));
    return;
  }
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
  // optimistic: grey the card immediately and keep it until the SSE `plan`
  // reconcile drops the stash — not the old hide-on-POST-return. Beaconed as
  // `web-hint` op=plan.
  ses.planPend = optPending(S.cur, "plan", plan.tool_use_id || "", okDetail);
  renderPlan();
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/plan-decision", body,
           { audit: "plan" })
    .then(() => {
      toast("done", okTitle, okDetail);
      // stay greyed — the SSE `plan` event is the real confirmation
    })
    .catch(e => {
      if (ses.planPend) {
        ses.planPend.settle("dropped", { reason: (e && e.step) || "failed" });
        ses.planPend = null;
      }
      toast("ask", "plan decision failed", (e && e.error) || "");
      renderPlan();                          // rebuild the interactive card to retry
    });
}

/* ---------- dictation (mic → Deepgram → the textarea, live) ---------- */
// docs/dashboard.md *Web dictation*. Mic buttons render only when the server
// reports a configured Deepgram key (GET /api/dictate — probed once, cached).
// Flow: POST /api/dictate/token → ~30s grant JWT + the fully-assembled listen
// URL → the BROWSER opens wss straight to Deepgram (the stdlib server can't
// speak WebSocket and must never see audio) → an AudioWorklet ships
// Float32→Int16 PCM at the AudioContext's native rate (MediaRecorder is
// rejected: iPad Safari emits mp4/AAC, which Deepgram streaming refuses) →
// interim results splice into the textarea LIVE (visual validation is the
// point) and firm up in place when Deepgram finalizes them. One mic at a
// time, page-wide; view/modal teardown stops it (a mic must never outlive
// the box it feeds).

let dictProbe = null;              // the one /api/dictate probe (Promise<bool>)
function dictAvailable() {
  if (!dictProbe)
    dictProbe = fetch("/api/dictate").then(r => r.json())
      .then(d => !!(d && d.available)).catch(() => false);
  return dictProbe;
}

// Float32 → Int16 in the audio thread, batched to 4096-sample (~85ms @48k)
// chunks — bare 128-sample process() quanta would be ~375 tiny ws messages/s.
const DICT_WORKLET = `
class DictatePCM extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Int16Array(4096); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.buf.length) {
        this.port.postMessage(this.buf.slice(0).buffer);
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor("dictate-pcm", DictatePCM);`;
let dictWorkletURL = null;

function micIcon() {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  [["M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"],
   ["M19 10v2a7 7 0 0 1-14 0v-2"], ["M12 19v4"]].forEach(([d]) => {
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", d);
    svg.append(p);
  });
  return svg;
}

let dictActive = null;             // the page-wide single live dictation
function stopDictation() { if (dictActive) dictActive.stop(); }

function dictation(ta, getCwd) {
  // Per-textarea controller — returns {btn, stop}; callers place the button.
  // getCwd (optional, zero-arg): the directory that keys the PROJECT
  // vocabulary layer — read at mic-press time, so the new-session form's
  // typed dir is honored as-typed and a keyterms edit lands next press.
  const btn = el("button", "micbtn");
  btn.type = "button";
  btn.title = "dictate";
  btn.hidden = true;               // shown only when the server has a key
  btn.append(micIcon());
  dictAvailable().then(ok => { btn.hidden = !ok; });
  let live = null;

  async function start() {
    if (ta.disabled || live) return;
    stopDictation();               // one mic page-wide
    btn.classList.add("wait");
    // AudioContext FIRST, synchronously in the click's gesture chain — iOS
    // Safari creates gesture-less contexts suspended and keeps them so
    const ctx = new AudioContext();
    if (ctx.state === "suspended") ctx.resume();
    // Mic permission and token mint are independent (the sample rate comes
    // from the ctx, which exists already) — run them CONCURRENTLY to shave
    // ~300–500ms off every activation. allSettled, not all: if one leg fails
    // the other may still resolve later, and a granted-after-failure stream
    // must be released or the tab's mic indicator sticks on. (First-ever use
    // can sit >30s in the permission prompt and outlive the JWT — the ws
    // then fails its handshake and toasts; the retry has a warm permission.)
    const tokBody = { sample_rate: Math.round(ctx.sampleRate) };
    const cwd = getCwd && getCwd();
    if (cwd) tokBody.cwd = cwd;    // keys the project keyterms layer
    const [ms, mt] = await Promise.allSettled([
      navigator.mediaDevices.getUserMedia(
        { audio: { echoCancellation: true, noiseSuppression: true } }),
      postJSON("/api/dictate/token", tokBody),
    ]);
    if (ms.status === "rejected" || mt.status === "rejected") {
      if (ms.status === "fulfilled")
        ms.value.getTracks().forEach(t => t.stop());
      ctx.close();
      btn.classList.remove("wait");
      if (ms.status === "rejected")
        toast("ask", "microphone blocked",
              "allow mic access for this site and retry");
      else
        toast("ask", "dictation unavailable",
              (mt.reason && mt.reason.error) || "token mint failed");
      return;
    }
    const stream = ms.value, tok = mt.value;

    // The splice: everything before/after the caret at mic-start stays put;
    // dictated text grows between them as committed (finalized) + interim
    // (still firming up — REPLACED on every partial, so the box always shows
    // Deepgram's current best guess and corrections happen before your eyes).
    const at = ta.selectionStart != null ? ta.selectionStart : ta.value.length;
    const st = {
      prefix: ta.value.slice(0, at), suffix: ta.value.slice(at),
      committed: "", interim: "", skipFinal: false, painting: false,
      stopping: false, closed: false, lastPainted: null,
    };
    const paint = () => {
      // Once we're STOPPING, never RESURRECT text into a box that something
      // else rewrote after our last paint: `dic.stop()` sends CloseStream and
      // Deepgram flushes its final transcript ASYNC (~1s later, over the wire),
      // which lands after the composer's send already cleared the box — a bare
      // paint would refill the just-sent box AND (via the input event below)
      // re-persist the draft. Same guard finish() uses; scoped to stopping so
      // live-dictation edits (handled by onEdit) still paint normally.
      if (st.stopping && st.lastPainted != null && ta.value !== st.lastPainted)
        return;
      const head = st.prefix + st.committed + st.interim;
      st.painting = true;
      ta.value = st.lastPainted = head + st.suffix;
      ta.setSelectionRange(head.length, head.length);
      ta.dispatchEvent(new Event("input", { bubbles: true }));  // autoGrow &co
      st.painting = false;
    };
    // Typing mid-dictation re-anchors the splice to wherever the caret is:
    // any shown interim becomes plain text (and the final that would repeat
    // it is dropped), then dictation continues from the new anchor.
    const onEdit = () => {
      if (st.painting) return;
      const p = ta.selectionStart != null ? ta.selectionStart : ta.value.length;
      st.skipFinal = !!st.interim;
      st.prefix = ta.value.slice(0, p);
      st.suffix = ta.value.slice(p);
      st.committed = ""; st.interim = "";
    };
    ta.addEventListener("input", onEdit);

    const finish = () => {         // the ws is done, clean or not
      if (st.closed) return;
      st.closed = true;
      stream.getTracks().forEach(t => t.stop());   // tab mic indicator OFF
      if (ctx.state !== "closed") ctx.close();
      // commit a dangling interim — but never resurrect text into a box
      // something else (the composer's post-send clear) rewrote meanwhile
      if (st.interim && ta.value === st.lastPainted) {
        st.committed += st.interim; st.interim = "";
        paint();
      }
      ta.removeEventListener("input", onEdit);
      btn.classList.remove("rec", "wait");
      if (dictActive === live) dictActive = null;
      live = null;
    };

    let ws;
    try {
      ws = new WebSocket(tok.ws_url, ["bearer", tok.token]);
    } catch (e) {
      finish();
      toast("ask", "dictation failed", "could not reach Deepgram");
      return;
    }
    ws.onmessage = (ev) => {
      let d;
      try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (d.type !== "Results") return;
      const alt = d.channel && d.channel.alternatives
        && d.channel.alternatives[0];
      const text = (alt && alt.transcript) || "";
      if (d.is_final) {
        if (st.skipFinal) st.skipFinal = false;
        else if (text) st.committed += text + " ";
        st.interim = "";
      } else if (!st.skipFinal) {
        st.interim = text;
      }
      paint();
    };
    ws.onclose = () => {
      const dropped = !st.stopping && !st.closed;
      finish();
      if (dropped)
        toast("ask", "dictation ended", "connection to Deepgram closed");
    };
    ws.onopen = async () => {
      try {
        if (!dictWorkletURL)
          dictWorkletURL = URL.createObjectURL(
            new Blob([DICT_WORKLET], { type: "text/javascript" }));
        await ctx.audioWorklet.addModule(dictWorkletURL);
        const src = ctx.createMediaStreamSource(stream);
        const sink = new AudioWorkletNode(ctx, "dictate-pcm");
        sink.port.onmessage = (e) => {
          if (ws.readyState === 1 && !st.stopping) ws.send(e.data);
        };
        src.connect(sink);
        sink.connect(ctx.destination);   // pull the graph; outputs are silence
        btn.classList.remove("wait");
        btn.classList.add("rec");
      } catch (e) {
        try { ws.close(); } catch (e2) { /* already closed */ }
        finish();
        toast("ask", "dictation failed", "audio pipeline error");
      }
    };

    live = {
      stop() {
        if (st.stopping || st.closed) return;
        st.stopping = true;
        btn.classList.remove("rec");
        if (ws.readyState === 1) {
          // CloseStream makes Deepgram flush the last partial as a final
          // (painted by onmessage) and close; the timer is the failsafe
          try { ws.send('{"type":"CloseStream"}'); } catch (e) { finish(); }
          setTimeout(() => {
            try { ws.close(); } catch (e) { /* already closed */ }
            finish();
          }, 2000);
        } else {
          try { ws.close(); } catch (e) { /* never opened */ }
          finish();
        }
      },
    };
    dictActive = live;
  }

  btn.onclick = () => { if (live) live.stop(); else start(); };
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && live) { e.stopPropagation(); live.stop(); }
  });
  return { btn, stop: () => { if (live) live.stop(); } };
}

/* ---------- control plane: the message composer ---------- */
// A textarea above the mirror feed that types a message into the session's
// kitty window (POST /message). Enter sends, Shift+Enter is a newline — except
// on an iPad (IS_IPAD), where Enter is a newline and only the button sends. Disabled
// with a hint when the session isn't live or has no window (a headless/daemon
// session — the /message endpoint would 409). The sent text surfaces in the
// stream on its own via the conversation tail, so we only clear + toast —
// unless the response says it QUEUED (see above), which pins a ⧗ queued bubble.

// Both message boxes (the composer and the form's first prompt) grow with
// their content, capped at a viewport fraction so a long paste can't swallow
// the page (the CSS max-height mirrors this cap as 40vh).
const GROW_CAP = 0.4;
function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, Math.round(innerHeight * GROW_CAP)) + "px";
}

// Persist the unsent composer text to the server (debounced) so a reopen on any
// device — or a return to this session from another — restores it. Best-effort:
// a failed save just retries on the next edit. `ses`/`sid` are captured by the
// composer so a debounce that fires after a view switch still targets the right
// session (S.cur may have moved on). An empty box deletes the stash server-side.
function saveComposerDraft(ses, sid) {
  const ta = ses.composer;
  if (!ta) return;
  // never save while the box is disabled — that is the send-in-flight window
  // (send() disables it, then clearComposerDraft removes the stash): a trailing
  // dictation-final input event landing here would re-persist the just-sent
  // text before `.then` clears the box (the pre-clear half of the resurrection
  // race the paint() guard covers on the box side)
  if (ta.disabled) return;
  const text = ta.value;
  // keep meta in sync so a tab-switch rebuild seeds from what we just typed,
  // and so our own SSE echo (same origin) is a no-op against current state
  if (ses.meta)
    ses.meta.composer_draft = text.trim() ? { text, origin: CLIENT_ID } : null;
  clearTimeout(ses._composerDraftTimer);
  ses._composerDraftTimer = setTimeout(() => {
    // seq (wall-clock at DISPATCH) orders concurrent writes: a debounced save
    // in flight when send() fires its clear must NOT overwrite the clear if it
    // arrives later over the tunnel (the "draft didn't clear after send"
    // reorder, 2026-07-19). The server keeps only the highest seq.
    postJSON("/api/session/" + encodeURIComponent(sid) + "/composer-draft",
             { text, origin: CLIENT_ID, seq: Date.now() }).catch(() => {});
  }, ASK_DRAFT_DEBOUNCE_MS);
}

// Sending consumes the draft — clear it immediately (not debounced), both the
// cache and the server stash, so it never reappears after the message is on its
// way (and, on the resume path, so the adopted session doesn't re-show it).
function clearComposerDraft(ses, sid) {
  clearTimeout(ses._composerDraftTimer);
  if (ses.meta) ses.meta.composer_draft = null;
  // a later seq than any in-flight save, so the clear always wins the race
  // even if an earlier save's POST lands after it (see saveComposerDraft)
  postJSON("/api/session/" + encodeURIComponent(sid) + "/composer-draft",
           { text: "", origin: CLIENT_ID, seq: Date.now() }).catch(() => {});
}

// A peer device's composer draft arrived over SSE. Adopt it into the box — but
// ignore our OWN echo (same origin), and never yank text out from under an
// ACTIVE local edit (the box holding focus is being typed into; ses.meta is
// still updated so the next remote change applies once it blurs).
function applyComposerDraft(draft) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.composer_draft = draft || null;   // for a later rebuild
  if (draft && draft.origin && draft.origin === CLIENT_ID) return;   // our write
  const ta = ses.composer;
  if (!ta || ta === document.activeElement) return;
  const text = (draft && draft.text) || "";
  if (ta.value === text) return;
  ta.value = text;
  autoGrow(ta);
  syncSuggestion(ta);   // draft filled/emptied the box → toggle the ghost placeholder
}

// A live input-box ghost suggestion arrived over SSE — the faint "suggested
// answer" Claude Code pre-fills when a turn settles (docs/dashboard.md, *Web
// ghost suggestion*). We surface it as the composer's grey placeholder, shown
// only while the box is empty, accepted with → / Tab (the composer keydown) and
// replaced the instant the user types (a non-empty textarea hides its
// placeholder natively). Mirror only: accepting fills the WEB box; nothing is
// written back to the TUI.
function applySuggestion(text) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.suggestion = text || null;
  if (ses.composer) syncSuggestion(ses.composer);
}

// Borrow the placeholder slot for the ghost suggestion while the box is empty;
// restore the composer's own default placeholder otherwise (or when there's no
// suggestion). Idempotent — safe to call on every input/build/SSE update.
function syncSuggestion(ta) {
  const ses = S.ses;
  const sug = ses && ses.meta && ses.meta.suggestion;
  const ghost = !!(sug && !ta.value);
  ta.placeholder = ghost ? sug : (ta.dataset.defph || "");
  ta.classList.toggle("hasghost", ghost);
}

/* ---------- composer attachments (images/screenshots + files) ----------
   The browser captures a file (paste of a screenshot, a drag-drop, or the 📎
   picker), uploads its bytes to /api/upload, and the server stages it on disk
   and hands back an absolute path. On send, those paths ride the message as
   leading `@path` mentions — the TUI-native way to attach a file — so Claude
   Code itself reads/attaches them (docs/dashboard.md, *Web attachments*). */
const ATTACH_MAX = 14 * 1024 * 1024;      // mirrors the server's UPLOAD_MAX

// A File → base64 (no data: prefix), the JSON-transport shape /api/upload wants.
function fileToB64(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onerror = () => rej(r.error || new Error("read failed"));
    r.onload = () => {
      const s = String(r.result || "");
      const i = s.indexOf(",");          // "data:<mime>;base64,<data>"
      res(i >= 0 ? s.slice(i + 1) : s);
    };
    r.readAsDataURL(file);
  });
}

// The pending-attachment strip + upload plumbing, shared by the live composer
// and the new-session form. getSid() names the session to stage under ("" for
// the form → the server's shared "staging" bucket). onChange() fires whenever
// the set changes so the host can re-evaluate its send/launch button. Returns
// { strip, addFiles, paths, pending, count, clear }.
function attachTray(getSid, onChange) {
  const items = [];                       // {name, is_image, url, path, failed}
  const strip = el("div", "attach-strip");
  const notify = () => {
    strip.classList.toggle("has", items.length > 0);
    if (onChange) onChange();
  };
  const remove = (it) => {
    const i = items.indexOf(it);
    if (i < 0) return;
    if (it.url) URL.revokeObjectURL(it.url);
    items.splice(i, 1);
    draw(); notify();
  };
  function draw() {
    strip.textContent = "";
    for (const it of items) {
      const chip = el("div", "attach-chip"
        + (it.path ? "" : it.failed ? " failed" : " pending"));
      if (it.is_image && it.url) {
        const img = el("img", "attach-thumb");
        img.src = it.url;
        chip.append(img);
      } else {
        chip.append(el("span", "attach-icon", "📄"));
      }
      chip.append(el("span", "attach-name", it.name));
      const x = el("button", "attach-x", "✕");
      x.type = "button";
      x.title = "remove attachment";
      x.onclick = () => remove(it);
      chip.append(x);
      strip.append(chip);
    }
  }
  const add = (file) => {
    if (!file) return;
    if (file.size > ATTACH_MAX) {
      return toast("ask", "file too large",
                   (file.name || "file") + " exceeds the upload limit");
    }
    const is_image = /^image\//.test(file.type || "");
    const it = {
      name: file.name || (is_image ? "screenshot.png" : "attachment"),
      is_image, url: is_image ? URL.createObjectURL(file) : "",
      path: null, failed: false,
    };
    items.push(it);
    draw(); notify();
    fileToB64(file)
      .then((data) => postJSON("/api/upload", {
        sid: getSid() || "", name: it.name,
        mime: file.type || "application/octet-stream", data }))
      .then((d) => { it.path = d.path; it.is_image = !!d.is_image; draw(); notify(); })
      .catch((e) => {
        it.failed = true; draw(); notify();
        toast("ask", "attachment upload failed", (e && e.error) || "");
      });
  };
  return {
    strip,
    addFiles: (files) => { for (const f of files || []) add(f); },
    paths: () => items.filter((it) => it.path).map((it) => it.path),
    pending: () => items.some((it) => !it.path && !it.failed),
    count: () => items.filter((it) => it.path).length,
    clear: () => {
      for (const it of items) if (it.url) URL.revokeObjectURL(it.url);
      items.length = 0; draw(); notify();
    },
  };
}

// Wire the 📎 picker, clipboard paste (screenshots), and drag-drop onto a tray.
// `ta` is the paste target (textarea); `zone` the drop target (composer wrap /
// prompt box); enabled() gates every path (a parked/headless box takes none).
// Returns the picker button to place in the UI (the hidden <input> rides with
// it in a fragment).
// a paperclip glyph as an inline SVG (not the 📎 emoji): the emoji's own
// line-box metrics made the button a different height than the SVG mic beside
// it, so its icon sat misaligned — an SVG sized exactly like `.micbtn svg`
// (15px) lines up and matches the mic's monochrome style.
const CLIP_SVG =
  "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'"
  + " stroke-linecap='round' stroke-linejoin='round'><path d='M21.44 11.05l-9.19"
  + " 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1"
  + "-2.83-2.83l8.49-8.48'/></svg>";

function wireAttach(tray, ta, zone, enabled) {
  const btn = el("button", "cattach");
  btn.type = "button";
  btn.innerHTML = CLIP_SVG;
  btn.title = "attach image or file";
  const input = el("input", "attach-input");
  input.type = "file";
  input.multiple = true;
  input.onchange = () => { tray.addFiles(input.files); input.value = ""; };
  btn.onclick = () => { if (enabled()) input.click(); };
  ta.addEventListener("paste", (e) => {
    if (!enabled()) return;
    const files = [];
    for (const it of (e.clipboardData && e.clipboardData.items) || [])
      if (it.kind === "file") { const f = it.getAsFile(); if (f) files.push(f); }
    if (files.length) { e.preventDefault(); tray.addFiles(files); }
  });
  zone.addEventListener("dragover", (e) => {
    if (!enabled() || !e.dataTransfer || e.dataTransfer.types.indexOf("Files") < 0)
      return;
    e.preventDefault(); zone.classList.add("dropping");
  });
  zone.addEventListener("dragleave", (e) => {
    if (e.target === zone) zone.classList.remove("dropping");
  });
  zone.addEventListener("drop", (e) => {
    zone.classList.remove("dropping");
    if (!enabled()) return;
    const files = (e.dataTransfer && e.dataTransfer.files) || [];
    if (files.length) { e.preventDefault(); tray.addFiles(files); }
  });
  return frag(btn, input);
}

function buildComposer() {
  const ses = S.ses;
  const meta = ses.meta || {};
  const sid = S.cur;   // the session this composer is bound to (draft target)
  const wrap = el("div", "composer");
  const ta = el("textarea", "cinput");
  ta.rows = 1;
  ta.spellcheck = false;
  const canSend = !!(meta.live && meta.kitty_window_id);
  // RESUME MODE (docs/dashboard.md *Resume & send*): a parked session's
  // composer stays fully usable — typing, "/" menu, dictation — and the one
  // send button (relabeled "resume & send") is the single door from parked
  // to live: it relaunches the conversation through the existing
  // /api/sessions/new resume+prompt path, the message riding the LAUNCH
  // ARGV (never typed into a half-started TUI — no readiness race), under
  // the session's own account. Headless-live stays disabled — those aren't
  // asleep, they just have no window; resume is the wrong medicine.
  // a parked session whose transcript .jsonl is gone can't be resumed —
  // `claude --resume` would find nothing and the tab would die at once, so the
  // server 410s it. Disable the door and say why, don't offer a dead button.
  const gone = !meta.live && !!meta.cwd && !!meta.transcript_missing;
  const canResume = !meta.live && !!meta.cwd && !gone;
  const usable = canSend || canResume;
  ta.disabled = !usable;
  ta.placeholder = canSend
    ? (IS_IPAD ? "message this session…"
               : "message this session…  (Enter to send · Shift+Enter for newline)")
    : canResume
      ? (IS_IPAD ? "message this parked session — sending resumes it"
                 : "message this parked session — sending resumes it  "
                   + "(Enter to resume & send)")
      : gone ? "this session's transcript is gone — it can't be resumed"
      : (meta.live ? "no terminal window — can't message a headless session"
                   : "session is not live");
  // remember the composer's OWN placeholder — a live ghost suggestion borrows
  // the placeholder slot while the box is empty, and this is what it restores
  ta.dataset.defph = ta.placeholder;
  const btn = el("button", "csend", canResume ? "resume & send" : "send");
  btn.disabled = !usable;
  ses.composer = ta;
  // restore the persisted draft (a device switch / reopen / return-to-session
  // brings back the half-typed message) — only into a usable box. rAF the grow:
  // scrollHeight needs the textarea mounted, which the caller does after this.
  if (usable && meta.composer_draft && meta.composer_draft.text) {
    ta.value = meta.composer_draft.text;
    requestAnimationFrame(() => { if (ses.composer === ta) autoGrow(ta); });
  }
  syncSuggestion(ta);   // show a live ghost suggestion (if any) into the empty box
  const dic = dictation(ta, () => meta.cwd || "");
  dic.btn.disabled = !usable;    // an honest dead mic beats one that ignores you
  // attachments: staged under this session's id (live) or its own id for a
  // parked resume (the bytes are read once the revived session boots)
  const tray = attachTray(() => S.cur);
  const attachBtn = usable
    ? wireAttach(tray, ta, wrap, () => usable && !ta.disabled)
    : null;
  const send = () => {
    dic.stop();          // the visible (validated) text is what sends
    const text = ta.value.trim();
    const atts = tray.paths();
    if ((!text && !atts.length) || ta.disabled) return;
    if (tray.pending())    // an upload is still in flight — don't drop it
      return toast("ask", "attachment still uploading", "one moment…");
    ta.disabled = true; btn.disabled = true;
    clearComposerDraft(ses, sid);   // sending consumes the draft (both paths)
    if (canResume) {
      const body = { cwd: meta.cwd, resume: S.cur, prompt: text };
      if (atts.length) body.attachments = atts;
      const slug = meta.account && meta.account.slug;
      if (slug) body.account = slug;   // wake it under ITS account, silently
      postJSON("/api/sessions/new", body, { audit: "resume-send", sid: S.cur })
        .then(() => {
          // the revived session appears via its own SessionStart (then forks
          // sids — adopt); the armed jump follows it, same as a form resume.
          // But the launch POST succeeding is not the session arriving — if it
          // never boots, the composer stays disabled forever (the success path
          // has no finally). onfail revives it when the watch times out so the
          // typed message isn't trapped behind a dead box.
          armJump(meta.cwd, S.cur, { onfail: () => {
            if (S.ses !== ses || ses.composer !== ta) return;   // moved on
            ta.disabled = false; btn.disabled = false;
            saveComposerDraft(ses, sid);   // re-stash (send-start cleared it)
            toast("ask", "resume timed out",
                  "the session never came back — your message is kept; try again");
          } });
          tray.clear();
          toast("done", "resuming session", "your message starts the revived turn");
        })
        .catch(e => {
          // the draft survives in the box — nothing is lost on a failed wake
          // (re-persist it: send-start cleared the stash optimistically)
          toast("ask", "resume failed", (e && e.error) || "");
          clientFail(sid, "resume", e, text.length);
          ta.disabled = false; btn.disabled = false; ta.focus();
          saveComposerDraft(ses, sid);
        });
      return;
    }
    // after a mid-turn cancel-edit the TUI holds the restored draft, so this
    // edited send must replace it (server: Ctrl+U/K then bracketed paste)
    const msg = { text };
    if (atts.length) msg.attachments = atts;
    if (ses.clearDraftNext) { msg.clear_draft = true; ses.clearDraftNext = false; }
    // optimistic: show the message immediately (greyed) so there's no gap
    // before its real transcript prompt arrives over SSE — drainPending swaps
    // in the real bubble when it lands (see the optimistic-bubbles section).
    // Only for typed text (empty send = attachments only: nothing to preview).
    const pend = text ? addPending(ses, text) : null;
    postJSON("/api/session/" + encodeURIComponent(S.cur) + "/message", msg,
             { audit: "send", auditData: { chars: (text || "").length } })
      .then(d => {
        ta.value = ""; autoGrow(ta); tray.clear();
        if (d && d.queued) {
          // queued mid-turn — the pinned ⧗ queued bubble owns this until
          // delivery; drop the stand-in so the two representations don't double up
          if (pend) settlePending(pend, "dropped", { reason: "queued" });
          ses.queue.push({ text });
          renderQueue();
          saveQueue(ses);
          toast("done", "message queued", "delivers when this turn ends");
        } else {
          toast("done", "message sent", "");
        }
      })
      .catch(e => {
        // send-start cleared the stash optimistically; the box keeps its text,
        // so re-persist it — a reload mustn't lose an unsent message
        if (pend) settlePending(pend, "dropped", { reason: "send-failed" });
        toast("ask", "send failed", (e && e.error) || "");
        clientFail(sid, "send", e, text.length);
        saveComposerDraft(ses, sid);
      })
      .finally(() => {
        // refocus for the next message — except on an iPad, where it would
        // yank the on-screen keyboard back up after a button-tap send
        if (ses.composer === ta) {
          ta.disabled = !canSend; btn.disabled = !canSend;
          if (!IS_IPAD) ta.focus();
        }
      });
  };
  // cosmetic busy hint: the send button reads "queue" while a turn is running
  // (kept fresh by the `tab` SSE event; the server's verdict stays authoritative)
  ses.composerMode = (tab) => {
    if (!canSend) return;          // the parked/headless labels are fixed
    btn.textContent = QUEUE_TABS.includes(tab) ? "queue" : "send";
  };
  ses.composerMode(((S.sessions.find(r => r.sid === S.cur) || {}).tab)
                   || (meta.tab || ""));
  // the "/" menu — commands for THIS session's cwd, fetched once per view
  const sm = slashMenu(ta, wrap,
    () => cmdsFor(meta.cwd, ses, "cmds"),
    { enterSends: !IS_IPAD });
  ta.oninput = () => { autoGrow(ta); saveComposerDraft(ses, sid); syncSuggestion(ta); };
  ta.onkeydown = (e) => {
    if (sm.key(e)) return;
    // → / Tab on an EMPTY box accepts the ghost suggestion as real input (the
    // native "right-arrow to accept" affordance) — fills the WEB box only, then
    // send works normally. Typing instead just replaces it (a non-empty box
    // hides the placeholder). Skipped once the box holds text, so it never
    // steals → from caret movement / Tab from the "/" menu (both non-empty).
    if ((e.key === "ArrowRight" || e.key === "Tab") && !ta.value
        && ses.meta && ses.meta.suggestion) {
      e.preventDefault();
      ta.value = ses.meta.suggestion;
      autoGrow(ta); saveComposerDraft(ses, sid); syncSuggestion(ta);
      return;
    }
    if (!IS_IPAD && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  };
  btn.onclick = send;
  // order: [attachment strip (full-width, wraps to top)], textarea, 📎 attach,
  // 🎤 mic, send — the attach sits next to the mic, not stranded past send
  wrap.append(tray.strip, ta);
  if (attachBtn) wrap.append(attachBtn);
  wrap.append(dic.btn, btn);
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

function armJump(cwd, resumeSid, o) {
  o = o || {};
  S.jumpDone = null;               // a new launch supersedes a stale forward
  S.jump = { cwd, resumeSid: resumeSid || "",
             win: o.win || "",     // the launched tab's window id when the
             //                       terminal reported one — the exact match
             show: o.show || null, // what the #/launching pending view displays
             //                       ({mode, model, effort, account, prompt});
             //                       null = no pending view (composer resume)
             quiet: false,         // route() flips this on user navigation —
             //                       resolution toasts instead of navigating
             armedAt: Date.now(),  // the pending view's elapsed counter — must
             //                       survive the view unmounting/remounting
             onfail: o.onfail || null,  // called if the watch times out with no
             //                       pending view (a composer "resume & send",
             //                       whose disabled composer needs re-enabling)
             known: new Set(S.sessions.map(r => r.sid)),
             liveAtArm: new Set(S.sessions.filter(r => r.live).map(r => r.sid)),
             until: Date.now() + JUMP_TIMEOUT_MS };
  // the client half of the launch story (the server logs web-launch/-wake): when
  // we START waiting for the launched tab to appear on the list — paired with the
  // hit/timeout below it bounds "launched from web but never showed up".
  clog(resumeSid || "", "launch.arm", { win: o.win || "", resume: !!resumeSid });
}

function checkJump() {
  const j = S.jump;
  if (!j) return;
  if (Date.now() > j.until) { jumpFail(); return; }
  // the launch's window id wins when known (r.live gates out a row from a
  // previous terminal run whose window ids restarted from 1); then the
  // resumed sid itself (its cwd may differ from the launch dir); otherwise
  // any cwd-row that is brand-new or freshly parked→live
  const row = (j.win && S.sessions.find(
    r => r.live && String(r.kitty_window_id || "") === j.win))
    || (j.resumeSid && S.sessions.find(r => r.live && r.sid === j.resumeSid))
    || S.sessions.find(r => r.live && r.cwd === j.cwd
                       && (!j.known.has(r.sid) || !j.liveAtArm.has(r.sid)));
  if (!row) return;
  jumpHit(row.sid, row.title || proj(row));
}

function jumpHit(sid, title) {
  const quiet = !!(S.jump && S.jump.quiet);
  clog(sid, "launch.hit",             // the launched session appeared — with latency
       { ms: S.jump ? Date.now() - S.jump.armedAt : 0, quiet });
  S.jump = null;                       // clear FIRST — route() must never see
  //                                      this function's own hash change armed
  const to = "#/s/" + encodeURIComponent(sid);
  if (quiet) {
    // the user navigated away mid-wait — never yank them; a clickable toast
    // announces the arrival, and #/launching (browser back) forwards there
    S.jumpDone = to;
    if (S.cur === sid) return;         // they already found it themselves
    toast("done", "session started", title || "click to open",
          () => { location.hash = to; });
    return;
  }
  // from the pending view, replace: #/launching is a waiting room, not a
  // history entry worth returning to (back should land on the list)
  if (S.pendingUI) location.replace(to);
  else location.hash = to;
  toast("done", "session started", title || "");
}

function jumpFail() {
  const onfail = S.jump && S.jump.onfail;
  clog(S.jump && S.jump.resumeSid || "", "launch.timeout",   // never appeared in time
       { ms: S.jump ? Date.now() - S.jump.armedAt : 0 });
  S.jump = null;
  if (onfail) onfail();          // a composer resume: revive its dead composer
  if (S.pendingUI) showPendingFail();
}

/* ---------- the optimistic pending view (#/launching) ---------- */
// Mounted the instant a form launch POSTs ok, BEFORE the session exists
// anywhere (claude takes ~2s to boot before its SessionStart) — the wait gets
// a visible page instead of dead air on the list, and the arrival becomes a
// swap-in-place (jumpHit's location.replace) instead of a surprise yank.
// Torn down by whatever route() runs next; its ticker dies with the DOM.

const PEND_HINT_MS = 8000;             // "still waiting…" past this — claude
//                                        boot measured ~2s, so 8s is abnormal
const PEND_TICK_MS = 500;              // ticker cadence (hint + timeout watch)

function showPending() {
  leaveSession();
  S.pendingUI = true;
  const j = S.jump;
  const show = j.show || {};
  $view.textContent = "";
  const card = el("div", "pendcard");
  card.append(el("div", "pendspin"));
  const verb = show.mode === "resume" ? "resuming session"
    : show.mode === "continue" ? "continuing session" : "starting session";
  card.append(el("div", "pendtitle", verb));
  card.append(el("div", "penddir", j.cwd));
  const chips = el("div", "pendchips");
  [show.account, show.model, show.effort].filter(Boolean)
    .forEach(t => chips.append(el("span", "pendchip", t)));
  if (chips.childNodes.length) card.append(chips);
  if (show.prompt) card.append(el("div", "pendprompt", show.prompt));
  const hint = el("div", "pendhint",
                  "claude is booting in a new terminal tab — usually a couple of seconds");
  card.append(hint);
  $view.append(card);
  // the ticker only escalates the hint and fires the timeout during total
  // silence — the jump itself arrives via the SSE wake / snapshot watches.
  // Elapsed counts from armedAt (the launch), not the mount: leaving and
  // re-entering the waiting room must not reset the clock.
  const tick = setInterval(() => {
    if (!card.isConnected) { clearInterval(tick); return; }
    const jj = S.jump;
    if (!jj) { clearInterval(tick); return; }        // jumpHit navigated
    if (Date.now() > jj.until) { clearInterval(tick); jumpFail(); return; }
    const waited = Date.now() - jj.armedAt;
    if (waited > PEND_HINT_MS)
      hint.textContent = "still waiting… (" + Math.round(waited / 1000)
        + "s) — check the terminal tab if this goes on";
  }, PEND_TICK_MS);
}

function showPendingFail() {
  if (!S.pendingUI) return;
  $view.textContent = "";
  const card = el("div", "pendcard fail");
  card.append(el("div", "pendtitle", "✗ the session never appeared"));
  card.append(el("div", "pendhint",
                 "claude may have failed to start — check the terminal tab"));
  const back = el("button", "nsbtn", "back to sessions");
  back.onclick = () => { location.hash = "#/"; };
  card.append(back);
  $view.append(card);
}

/* ---------- control plane: the new-session form ---------- */
// Lives in the persistent #modal host (outside #view) so a list re-render from
// an SSE snapshot never blows away a half-typed form. Directory input backed by
// suggest() over the distinct cwds in the current snapshot; optional first
// prompt; submit POSTs /api/sessions/new and the session appears on its own via
// SessionStart. The header "+ session" button opens it blank; a dir group's "+"
// prefills that cwd.

function closeNewSession() {
  stopDictation();               // the form's mic dies with the form
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
// STORED ON THE BACKEND now (the durable global prefs DB, GET/POST /api/ns-prefs)
// instead of per-browser localStorage, so a launch on one device pre-selects on
// the next — S.nsPrefs is the in-memory cache (fetched once at boot, refreshed
// on every write) that keeps this read synchronous. The BEHAVIOUR is unchanged:
// written only on a successful launch; an explicit prefill (a dir group's "+",
// a resume button) still wins over the remembered directory.
const nsLast = () => S.nsPrefs || {};
const nsRemember = (p) => {
  S.nsPrefs = p;                                   // cache first, form is sync
  postJSON("/api/ns-prefs", p).catch(() => {});    // best-effort backend write
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

// The new-session resume picker (docs/dashboard.md *Resume picker*): a search
// box + a scrollable list of a directory's recent sessions (GET /api/resumable,
// up to RESUMABLE_MAX), each row carrying the session's model/effort/account. It
// replaces the old three-way "start from" dropdown's resume entries — no
// `--continue`, resuming the most-recent row IS "continue". Keyboard: ArrowDown
// from the search box drops into the list, ↑/↓ move between rows, Enter selects,
// and SPACE toggles an inline mirror-transcript preview of the highlighted row
// (renderPreview over /history). `onSelect(row)` fires on every selection so the
// caller can reuse the picked session's model+effort. `value()` is the chosen
// sid ("" = nothing picked).
function resumePicker() {
  const root = el("div", "nsresume");
  const search = el("input", "nsinput nsressearch");
  search.type = "text";
  search.spellcheck = false;
  search.placeholder = "search recent sessions…";
  const list = el("div", "nsreslist");
  const preview = el("div", "nspreview");
  preview.hidden = true;
  root.append(search, list, preview);

  let rows = [], selSid = "", pvSid = "", filter = "";
  const pvCache = new Map();
  const match = (r) => !filter
    || (r.title || "").toLowerCase().includes(filter)
    || (r.sid || "").toLowerCase().includes(filter);

  const paint = () => {
    list.textContent = "";
    const vis = rows.filter(match);
    if (!vis.length) {
      list.append(el("div", "nsresempty",
        rows.length ? "no match" : "no sessions to resume here"));
      return;
    }
    for (const r of vis) {
      const row = el("div", "nsresrow"
        + (r.sid === selSid ? " sel" : "") + (r.live ? " live" : ""));
      row.tabIndex = 0;
      row.dataset.sid = r.sid;
      row.append(el("div", "nsrestitle", r.title || shortSid(r.sid)));
      const meta = el("div", "nsresmeta");
      const fam = shortModel(r.model);
      if (fam) meta.append(el("span", "nsreschip", fam));
      if (r.effort) meta.append(el("span", "nsreschip", r.effort));
      if (r.account && r.account.label)
        meta.append(el("span", "nsreschip", r.account.label));
      if (r.live) meta.append(el("span", "nsreschip live", "live"));
      const when = lastActive(r) ? ago(lastActive(r)) : "";
      if (when) meta.append(el("span", "nsresago", when));
      row.append(meta);
      row.onclick = () => choose(r.sid);
      row.onkeydown = (e) => rowKey(e, r);
      list.append(row);
    }
  };

  const choose = (sid) => {
    selSid = sid;
    paint();
    const r = rows.find(x => x.sid === sid);
    if (!r) return;
    // audit the pick so a "resumed with the wrong model/effort/account" report
    // is reconstructible from the DB (docs/dashboard.md *Resume picker*): the sid
    // chosen + the model/effort/account it CARRIED (what onSelect reuses).
    clog(sid, "resume.pick", {
      model: r.model || "", effort: r.effort || "",
      account: (r.account && r.account.slug) || "", live: !!r.live });
    if (api.onSelect) api.onSelect(r);
  };

  const showPreview = (sid) => {
    if (!preview.hidden && pvSid === sid) {
      preview.hidden = true; pvSid = "";
      clog(sid, "resume.preview", { shown: 0 });
      return;
    }
    pvSid = sid;
    preview.hidden = false;
    clog(sid, "resume.preview", { shown: 1, cached: pvCache.has(sid) ? 1 : 0 });
    if (pvCache.has(sid)) { renderPreview(preview, pvCache.get(sid)); return; }
    preview.textContent = "";
    preview.append(el("div", "nspreview-empty", "loading…"));
    fetch("/api/session/" + encodeURIComponent(sid) + "/history?blocks=" + PREVIEW_BLOCKS)
      .then(r => r.json())
      .then(d => {
        const items = (d && d.items) || [];
        pvCache.set(sid, items);
        if (pvSid === sid && !preview.hidden) renderPreview(preview, items);
      })
      .catch(() => {
        clog(sid, "resume.preview.fail", {});
        if (pvSid !== sid) return;
        preview.textContent = "";
        preview.append(el("div", "nspreview-empty", "preview unavailable"));
      });
  };

  const rowKey = (e, r) => {
    const rowEl = e.currentTarget;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (rowEl.nextElementSibling) rowEl.nextElementSibling.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (rowEl.previousElementSibling) rowEl.previousElementSibling.focus();
      else search.focus();
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(r.sid);
    } else if (e.key === " ") {
      e.preventDefault();                          // space PREVIEWS, never scrolls
      showPreview(r.sid);
    } else if (e.key === "Escape" && !preview.hidden) {
      e.preventDefault();
      e.stopPropagation();                         // don't close the whole modal
      preview.hidden = true;
      pvSid = "";
    }
  };

  search.oninput = () => { filter = search.value.trim().toLowerCase(); paint(); };
  search.onkeydown = (e) => {
    const first = list.querySelector(".nsresrow");
    if (e.key === "ArrowDown" && first) { e.preventDefault(); first.focus(); }
    else if (e.key === "Enter" && first) { e.preventDefault(); choose(first.dataset.sid); }
  };

  const api = {
    el: root,
    onSelect: null,
    value: () => selSid,
    focus: () => search.focus(),
    // (re)load the directory's rows; `preferSid` preselects a specific session
    // (the ↻ resume target), else the current pick if still present, else the
    // most-recent row — so the default resume IS "continue the most recent".
    refresh(cwd, preferSid) {
      pvSid = "";
      preview.hidden = true;
      list.textContent = "";
      list.append(el("div", "nsresempty", "loading…"));
      fetch("/api/resumable?cwd=" + encodeURIComponent(cwd || ""))
        .then(r => r.json())
        .then(data => {
          rows = Array.isArray(data) ? data : [];
          // audit the load — a "picker was empty / didn't show my session"
          // report is answerable from the DB (cwd + row count + preselection).
          clog("", "resume.list", {
            cwd: cwd || "", n: rows.length, prefer: preferSid || "" });
          const want = (preferSid && rows.some(x => x.sid === preferSid)) ? preferSid
            : (selSid && rows.some(x => x.sid === selSid)) ? selSid
              : (rows[0] ? rows[0].sid : "");
          selSid = "";
          if (want) choose(want);                  // choose → onSelect + repaint
          else paint();
        })
        .catch(() => {
          clog("", "resume.list.fail", { cwd: cwd || "" });
          rows = []; selSid = ""; paint();
        });
    },
  };
  return api;
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

  // conversation: FRESH (a new conversation, the default) or RESUME one of this
  // directory's recent sessions. The old three-way "start from" dropdown is split
  // into a fresh toggle + a searchable, scrollable resume picker (resumePicker,
  // docs/dashboard.md *Resume picker*): there is no `--continue` — resuming the
  // most-recent row IS "continue". A resumed conversation forks to a new sid; the
  // adopt machinery and the jump watch handle that on their own. The picker rows
  // carry each session's model/effort/account (GET /api/resumable); selecting one
  // reuses its model+effort (the account still load-balances via autoAcct).
  const picker = resumePicker();
  const resumeRow = el("div", "nsfield nsresumerow");
  resumeRow.append(el("span", "nslabel", "resume"), picker.el);

  const freshRow = el("div", "nsfield");
  freshRow.append(el("span", "nslabel", "start"));
  const freshWrap = el("label", "nsswitch");
  const fresh = el("input");
  fresh.type = "checkbox";
  fresh.checked = !resumeSid;                 // ↻ resume opens straight to the picker
  const freshTxt = el("span", "nsswitchtxt");
  freshWrap.append(fresh, el("span", "nsslider"), freshTxt);
  freshRow.append(freshWrap);
  let pickerLoaded = false;
  const syncFresh = () => {
    freshTxt.textContent = fresh.checked
      ? "fresh conversation" : "resume a conversation";
    resumeRow.style.display = fresh.checked ? "none" : "";
    clog("", "resume.mode", { fresh: fresh.checked ? 1 : 0 });
    if (!fresh.checked && !pickerLoaded) {
      pickerLoaded = true;
      picker.refresh(dir.value.trim(), resumeSid || "");
    }
  };
  fresh.onchange = syncFresh;
  // reload the picker when the directory changes (debounced) — only while
  // resuming; suggest() keeps its own separate input listener (addEventListener).
  let dirTimer = 0;
  dir.oninput = () => {
    if (fresh.checked) return;
    clearTimeout(dirTimer);
    pickerLoaded = true;
    dirTimer = setTimeout(() => picker.refresh(dir.value.trim(), ""), 250);
  };

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
  // Track whether the user has touched a picker by hand — the resume prefill
  // below (async) must not clobber a deliberate choice made while it was in
  // flight (same discipline as acctPicked).
  let modelPicked = false, effortPicked = false;
  effort.onpick = () => { effortPicked = true; };

  // account picker — the subscription to launch under (a switcher alias like
  // c1/c2). Populated from /api/accounts (cached in S.accts); each option shows
  // the account's latest usage inline when known, plus its active limit-hit
  // marker. No "default" option: the plain-claude login duplicates one of
  // these accounts. The row hides when there is no switcher (empty list → the
  // launch just runs plain claude).
  // The DEFAULT selection burns PERISHABLE weekly quota first (objective (b) —
  // maximise total work per week; core/sessionapi.sched_score, docs/dashboard.md
  // *Default account*): among accounts not limit-blocked for the launch AND under
  // the 5h session-safety gate (sched_ok), pick the highest sched_score — quota
  // still left whose 7d window resets soonest. Ties → registry order. It SKIPS
  // any account whose active limit-hit applies to the launch — an account-wide
  // stamp always does, a model-scoped one (limit_hit.model, e.g. a fable-only
  // limit) only when that model is the one selected, so flipping the model picker
  // re-runs the choice (that's why the model picker is built above this block).
  // Gate empties the pool → fall back to any open account; all blocked → any at
  // all. Refined again when the fresh /api/accounts fetch lands — unless the user
  // already picked by hand. Higher per-session wall risk is by design: the
  // automigrate safety net (docs/relimit.md) catches it.
  const [acctRow, acct] = pick("account", []);
  acctRow.style.display = "none";
  let acctPicked = false, acctList = [];
  acct.onpick = () => { acctPicked = true; };
  const limitBlocks = (a) =>
    a.limit_hit && (!a.limit_hit.model || a.limit_hit.model === model.value);
  const autoAcct = () => {
    if (acctPicked || !acctList.length) return;
    const open = acctList.filter(a => !limitBlocks(a));
    const safe = open.filter(a => a.sched_ok);
    const pool = safe.length ? safe : (open.length ? open : acctList);
    acct.value = pool.reduce((b, a) => schedScore(a) > schedScore(b) ? a : b).slug;
  };
  model.onpick = () => { modelPicked = true; autoAcct(); };
  const fillAccts = (list) => {
    acctList = list;
    acctRow.style.display = list.length ? "" : "none";
    acct.fill(list.map(a => {
      // every captured window rides into the option text ("5h 40% · 7d 55%
      // · 7d fable 80%") — same enumeration as the usage strip's bars
      const wins = usageWindows(a.usage);
      const usage = wins.length
        ? "  (" + wins.map(k => windowLabel(k) + " " + a.usage[k] + "%").join(" · ") + ")"
        : "";
      const lim = a.limit_hit ? "  · " + limitLabel(a.limit_hit) : "";
      return [a.slug, a.slug + " · " + a.label + usage + lim];
    }));
    autoAcct();
  };
  if (S.accts) fillAccts(S.accts);
  fetch("/api/accounts").then(r => r.json())
    .then(list => { S.accts = list; fillAccts(list); }).catch(() => {});

  // Resuming should continue where the SESSION was, not where the launcher last
  // was: on every resume-row selection, reuse that session's own model (its
  // transcript-tail model from /api/resumable) and effort (its last-applied
  // /effort level), overriding the global last-used ns-prefs defaults set above —
  // unless the user has already hand-picked (modelPicked/effortPicked). The
  // account is DELIBERATELY not reused: autoAcct re-runs against the chosen model
  // so the launch still load-balances (docs/dashboard.md *Resume picker*).
  picker.onSelect = (r) => {
    const fam = (shortModel(r.model) || "").split("-")[0];
    if (!modelPicked && fam && model.has(fam)) model.value = fam;
    if (!effortPicked && r.effort && effort.has(r.effort)) effort.value = r.effort;
    autoAcct();
  };
  syncFresh();                       // initial visibility + (if resuming) load

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
  const pdic = dictation(prompt, () => dir.value.trim());
  // attachments for the initial prompt — staged under the shared "staging"
  // bucket (no sid yet); ride the launch argv as leading @-mentions
  const nsTray = attachTray(() => "");
  const nsAttach = wireAttach(nsTray, prompt, promptRow, () => true);
  const promptBox = el("div", "nsdictrow");
  promptBox.append(prompt, nsAttach, pdic.btn);
  promptRow.append(nsTray.strip, promptBox);
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
    pdic.stop();         // the visible (validated) prompt is what launches
    const cwd = dir.value.trim();
    if (!cwd) { dir.focus(); return; }
    // resuming needs a chosen conversation (no `--continue` fallback): if the
    // fresh toggle is off but nothing is selected, don't silently start fresh.
    const resumeSel = fresh.checked ? "" : picker.value();
    if (!fresh.checked && !resumeSel)
      return toast("ask", "pick a session",
                   "choose a conversation to resume, or switch to fresh");
    if (nsTray.pending())
      return toast("ask", "attachment still uploading", "one moment…");
    submit.disabled = true;
    const body = { cwd };
    const atts = nsTray.paths();
    if (atts.length) body.attachments = atts;
    if (resumeSel) body.resume = resumeSel;
    if (acct.value) body.account = acct.value;
    if (model.value) body.model = model.value;
    if (effort.value) body.effort = effort.value;
    if (prompt.value.trim()) body.prompt = prompt.value.trim();
    // Optimistic clear: the message is on its way — it rides the launch argv, so
    // empty the box NOW rather than leaving it looking un-sent through the
    // (kitten-slow) launch round-trip. The form tears down on success anyway;
    // this just guarantees the message never LINGERS in the input after you hit
    // launch. Restored verbatim only if the launch actually fails, so a retry
    // keeps your text (the "the draft stayed in the message input" fix).
    const sentPrompt = prompt.value;
    prompt.value = ""; autoGrow(prompt);
    postJSON("/api/sessions/new", body, { audit: "new", sid: "" })
      .then((d) => {
        nsRemember({ cwd, model: model.value, effort: effort.value });
        armJump(cwd, body.resume, {
          win: (d && d.win) || "",
          show: { mode: body.resume ? "resume" : "new",
                  model: model.value, effort: effort.value,
                  account: acct.value, prompt: body.prompt || "" },
        });
        closeNewSession();
        // the optimistic pending view — the jump swaps it for the session in
        // place. Explicit route() when the hash already IS #/launching (a
        // second launch from the header + while waiting): no hashchange fires,
        // but the view must rebuild around the new watch.
        if (location.hash === "#/launching") route();
        else location.hash = "#/launching";
      })
      .catch(e => {
        submit.disabled = false;
        prompt.value = sentPrompt; autoGrow(prompt);   // launch failed — keep it
        toast("ask", "launch failed", (e && e.error) || "");
      });
  };
  submit.onclick = go;
  cancel.onclick = closeNewSession;
  dir.onkeydown = (e) => {
    if (sug.key(e)) return;
    if (e.key === "Enter") { e.preventDefault(); go(); }
  };

  panel.append(dirRow, freshRow, resumeRow, split2, split, promptRow, actions);
  const back = el("div", "nsback");
  back.onclick = (e) => { if (e.target === back) closeNewSession(); };
  back.append(panel);
  $modal.append(back);
  $modal.hidden = false;
  document.body.classList.add("modal-open");      // scroll-lock the page behind
  // a known directory (remembered/prefilled) means the next thing you type is
  // the prompt — focusing the dir field there just pops its suggestion look.
  // Resuming (fresh off) → focus the picker's search so ↑/↓ pick immediately.
  // Not on an iPad: the unasked-for keyboard covers half the form (and focus
  // triggers Safari's page auto-zoom — see style.css touch section)
  if (!IS_IPAD) {
    if (!fresh.checked) picker.focus();
    else (dir.value.trim() ? prompt : dir).focus();
  }
}

$newbtn.onclick = () => openNewSession("");

/* ---------- fullscreen toggle ---------- */
// Header ⛶ button: browser Fullscreen API on the whole document, with the
// WebKit-prefixed fallback (iPadOS Safari ships only webkitRequestFullscreen).
// Hidden where neither exists (iPhone Safari). State syncs on the
// fullscreenchange event, not in the click handler, so Esc / the browser's
// own exit path keeps the button honest.
{
  const $fsbtn = document.getElementById("fsbtn");
  const root = document.documentElement;
  const req = root.requestFullscreen || root.webkitRequestFullscreen;
  const exit = document.exitFullscreen || document.webkitExitFullscreen;
  const cur = () => document.fullscreenElement || document.webkitFullscreenElement;
  if (!req) {
    $fsbtn.hidden = true;
  } else {
    $fsbtn.onclick = () => {
      const p = cur() ? exit.call(document) : req.call(root);
      if (p && p.catch) p.catch(() => {});   // e.g. permission denied — no-op
    };
    const sync = () => { $fsbtn.title = cur() ? "exit fullscreen" : "fullscreen"; };
    document.addEventListener("fullscreenchange", sync);
    document.addEventListener("webkitfullscreenchange", sync);
  }
}

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
// The header's ⇆ migrate button: resume this session under the other
// subscription account (the server picks it — least used, active limit-hit
// excluded, no % ceiling for a manual click; docs/relimit.md *Manual
// migrate*). The old tab closes and a new one opens; the sid forks on
// resume and the adopt machinery + jump watch carry the page over.
// Lock an immediate (no-confirm) control-plane action button for the duration
// of its POST so a double-tap can't fire the terminal write twice — ⇆ migrate
// would spawn two racing migrators, ■ stop/⊘ cancel would double-send Escape.
// `run` returns the POST promise; `rest` restores the button's resting state
// once it settles (default: re-enable; cancel re-derives from the tab). This
// lives on the buttons, not the functions, because the Esc-key gesture has its
// own escHold debounce and the functions are shared by both entry points.
function lockDuring(btn, run, rest) {
  btn.disabled = true;
  run().finally(rest || (() => { btn.disabled = false; }));
}

// Returns the POST promise so the button wiring can disable itself for the
// round-trip — a double-click on ⇆ migrate would otherwise spawn two racing
// migrators (each closing the tab, each picking a target). The guard path
// resolves so a caller's `.finally` re-enable still runs.
function migrateSession() {
  if (!S.cur) return Promise.resolve();
  return postJSON("/api/session/" + encodeURIComponent(S.cur) + "/migrate", {},
                  { audit: "migrate" })
    .then(r => toast("done", "migrating",
                     "resuming on " + ((r && r.to) || "another account")))
    .catch(e => toast("ask", "migrate failed", (e && e.error) || ""));
}

// Returns the POST promise for the same in-flight button lock (a double-tap
// mid round-trip would send Escape to the terminal twice).
function interruptSession() {
  const meta = (S.ses && S.ses.meta) || {};
  if (!S.cur || !meta.live || !meta.kitty_window_id) return Promise.resolve();
  // a red "asking you" tab means a MODAL DIALOG is open (ask/plan/permission).
  // An Esc there DECLINES the dialog, it doesn't interrupt a turn — sending one
  // once killed the answer the user was giving via the ask card. Respond
  // through the card instead (the server 409s as the backstop, but the toast is
  // the honest UX; docs/tab-colors.md).
  if (liveTab() === "awaiting-command") {
    toast("done", "a question is waiting",
          "answer it in the card above — Esc would decline it");
    return Promise.resolve();
  }
  return postJSON("/api/session/" + encodeURIComponent(S.cur) + "/interrupt", {},
                  { audit: "interrupt" })
    .then(r => {
      if (BUSY_TABS.includes(r && r.tab))
        toast("done", "interrupted", "Esc sent to the session");
      else
        toast("done", "Esc sent", "double-press Esc for rewind");
      // an interrupt ENDS the turn → it's your turn now. But Claude Code fires
      // NO hook on interrupt, so the tab can sit stale-busy (from EXECUTING not
      // even the escape-recheck spawns), leaving the composer button stuck on
      // "queue" when a plain "send" is what actually happens. Flip send/cancel/
      // quick out of the busy state NOW; a real tab change — the escape-recheck's
      // green, or the next prompt — reconciles, and if the turn somehow kept
      // going that next tab event flips it right back to "queue".
      const ses = S.ses;
      if (ses && BUSY_TABS.includes(r && r.tab)) {
        const yourTurn = "awaiting-response";   // green, not a QUEUE_TAB
        if (ses.composerMode) ses.composerMode(yourTurn);
        if (ses.cancelMode) ses.cancelMode(yourTurn);
        if (ses.stopMode) ses.stopMode(yourTurn);
        if (ses.quickMode) ses.quickMode(yourTurn);
      }
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
  // red "asking you" tab: a dialog is open — neither cancel-edit (Esc-Esc) nor
  // rewind (/rewind) belongs here; both would land in the dialog and dismiss or
  // corrupt it. Answer via the card instead.
  if (liveTab() === "awaiting-command") {
    toast("done", "a question is waiting",
          "answer it in the card above first");
    return;
  }
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
// cancel/stop buttons gate on this so an idle click never opens the rewind menu.
// awaiting-command (red) is DELIBERATELY excluded: red means a modal dialog is
// open (ask/plan/permission), where an Esc DECLINES the dialog rather than
// cancelling a turn — the stop/cancel buttons disable there and the gestures
// bail (see interruptSession/rewindSession), so the ask card stays the response
// path (docs/tab-colors.md; the "User declined to answer questions" fix).
const CANCEL_TABS = ["thinking", "working", "executing", "awaiting-bg"];

// The Cancel button: Claude Code's mid-turn double-Esc — cancel the running
// turn and restore your message into the composer for editing. Distinct from
// ■ stop (a plain interrupt that keeps the partial work) and ↶ rewind (the
// checkpoint menu). Only meaningful mid-turn; the button disables when idle,
// and this guard is the belt-and-braces (an idle /rewind would type the
// rewind command, not cancel).
function cancelEdit() {
  const meta = (S.ses && S.ses.meta) || {};
  if (!S.cur || !meta.live || !meta.kitty_window_id) return Promise.resolve();
  if (!CANCEL_TABS.includes(liveTab())) {
    toast("done", "nothing to cancel", "no turn is running");
    return Promise.resolve();
  }
  return postJSON("/api/session/" + encodeURIComponent(S.cur) + "/rewind", {},
                  { audit: "rewind" })
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
           arg ? { cmd, arg } : { cmd }, { audit: "command", auditData: { cmd } })
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
           { text, mode, ups }, { audit: "rewind-to", auditData: { mode, ups } })
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
  // a modal dialog is open (red asking-you tab) — an Esc here would DECLINE the
  // ask/plan/permission dialog, not interrupt or rewind a turn. Swallow the
  // gesture entirely (no interrupt hold-timer, no rewind) so a stray keypress
  // can't kill the answer the user is composing in the card.
  if (liveTab() === "awaiting-command") {
    clearTimeout(escHold);
    escHold = null;
    toast("done", "a question is waiting",
          "answer it in the card above — Esc would decline it");
    return;
  }
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
  badge.removeAttribute("data-st");     // drop any focused-subagent status stamp
  badge.dataset.tab = tab;
  badge.replaceChildren(el("span", "st"),
                        document.createTextNode(TAB_LABEL[tab] || tab || "no tab"));
  // the whole session header (the web scoreboard) washes with the state hue
  const head = badge.closest(".shead");
  if (head) { head.removeAttribute("data-st"); head.dataset.tab = tab; }
}

/* The header badge + .shead wash for a drilled-into subagent. A subagent has no
   tab of its own, so the pill text, its dot, and the header tint follow the
   agent STATUS (data-st from agentStatus) instead of the session tab — the CSS
   mirrors the agent cards. Symmetric with setBadge, which clears data-st (and
   renderSessionChrome rebuilds the header outright) on the way back. */
function setBadgeAgent(badge, sttxt, stcls) {
  if (!badge) return;
  badge.removeAttribute("data-tab");
  badge.dataset.st = stcls;
  badge.replaceChildren(el("span", "st"), document.createTextNode(sttxt));
  const head = badge.closest(".shead");
  if (head) { head.removeAttribute("data-tab"); head.dataset.st = stcls; }
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
    postJSON("/api/session/" + encodeURIComponent(S.cur) + "/rename", {name},
             { audit: "rename" })
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
  ses.agentFocus = null;      // a full session/tab render is never agent-focused
  ses.monitorFocus = null;    // …nor monitor-focused (a drill-down sets it again)
  ses.jobFocus = null;        // …nor background-job-focused
  clearMonitorPoll();         // leaving the monitors tab stops its live poll
  clearJobPoll();
  const meta = ses.meta || {};
  // the Memory tab only exists for in-scope (aggregator-adapters) sessions — a
  // deep-link / stale bookmark to it elsewhere falls back to the mirror
  if (tab === "memory" && !meta.memory_scope) tab = "mirror";
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
  if (meta.cwd) {
    // just the directory name (basename) — the full path rides the tooltip
    const cwdChip = el("span", "sid", meta.cwd.split("/").filter(Boolean).pop());
    cwdChip.title = meta.cwd;
    l1.append(cwdChip);
  }
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
  const ren = el("button", "sstop actses", "✎ rename");
  ren.title = "rename this session (resume picker + tab)";
  ren.onclick = () => startRenameHeader();
  act.append(ren);
  // migrate: hand this session to the other subscription account — the same
  // detached migrator as the automatic rate-limit path (docs/relimit.md
  // *Manual migrate*): live → the tab swaps (close, park, resume under the
  // other alias); parked → it just relaunches there. Immediate, no confirm
  // (like ■ stop), and like rename it works live AND parked.
  const mig = el("button", "sstop actses", "⇆ migrate");
  mig.title = "migrate this session to another account";
  mig.onclick = () => lockDuring(mig, migrateSession);
  act.append(mig);
  // 🔔 alerts / 🔕 muted: opt this session in/out of the DEFERRED Telegram
  // alert (docs/dashboard.md *Telegram alerts*) — the off-device notification
  // that fires when a chat sits red/green unattended past the grace window.
  // Deliberately OUTSIDE the live gate (like rename): the opt-out is a
  // dashboard pref, not session state, so it works live AND parked.
  const notif = el("button", "sstop actses");
  const paintNotif = (muted) => {
    notif.textContent = muted ? "🔕 muted" : "🔔 alerts";
    notif.title = muted
      ? "Telegram alerts muted for this session — click to unmute"
      : "Telegram alerts on — click to mute this session";
  };
  paintNotif(meta.notify_muted);
  notif.onclick = () => {
    const next = !meta.notify_muted;
    postJSON("/api/session/" + encodeURIComponent(S.cur) + "/notify",
             { muted: next })
      .then(() => {
        meta.notify_muted = next;
        paintNotif(next);
        toast("done", next ? "alerts muted" : "alerts on",
              next ? "no Telegram for this session"
                   : "Telegram alerts re-enabled");
      })
      .catch(e => toast("ask", "mute toggle failed", (e && e.error) || ""));
  };
  act.append(notif);
  if (meta.live && meta.kitty_window_id) {
    // stop: interrupt the agent in place — an Escape key press in the
    // session's window (the TUI's own interrupt; Esc here does the same).
    // Immediate, no confirm: it matches pressing Esc in the terminal.
    const stop = el("button", "sstop actstop", "■ stop");
    stop.title = "interrupt the agent (Esc)";
    stop.onclick = () => lockDuring(stop, interruptSession,
                                    () => ses.stopMode(liveTab()));
    // gated to the working states like ⊘ cancel — an interrupt only applies
    // while a turn is running, and an Esc when idle can clear queued input, so
    // it greys out otherwise (the resting state re-derives from the tab, never
    // a blind re-enable). Same CANCEL_TABS set (thinking/working/executing/
    // awaiting-bg) — NOT red awaiting-command, where an Esc declines the open
    // dialog instead of interrupting (interruptSession bails there too).
    ses.stopMode = (tab) => { stop.disabled = !CANCEL_TABS.includes(tab); };
    ses.stopMode(liveTab());
    act.append(stop);
    // cancel: mid-turn double-Esc — cancel the running turn and restore your
    // message into the composer for editing. Enabled only while a turn runs.
    const cancel = el("button", "sstop actses", "⊘ cancel");
    cancel.title = "cancel this turn and edit your message (mid-turn double-Esc)";
    // resting state re-derives from the tab (idle → stays disabled) rather
    // than a blind re-enable, matching ses.cancelMode's own gate
    cancel.onclick = () => lockDuring(cancel, cancelEdit,
                                      () => ses.cancelMode(liveTab()));
    ses.cancelMode = (tab) => { cancel.disabled = !CANCEL_TABS.includes(tab); };
    ses.cancelMode(liveTab());
    act.append(cancel);
    // rewind: Claude Code's double-Esc — mid-turn it cancels for editing;
    // idle it enters picking mode: click a message below, choose what to
    // restore, and the server drives the TUI's own checkpoint menu
    const rew = el("button", "sstop actses", "↶ rewind");
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
    const cls = el("button", "sstop actses", "✕ close");
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
        armed = setTimeout(disarm, ARM_MS);
        return;
      }
      clearTimeout(armed);
      disarm();
      cls.disabled = true;
      cls.textContent = "closing…";
      const sid = S.cur;
      // optimistic close: beacon the `close` lifecycle (web-hint op=close) and
      // navigate back to the list on the POST ack — the list card shows greyed
      // 'closing…' (S.closing) until reconcileCloses parks it from the poll.
      S.closing.add(sid);
      S.closePend[sid] = optPending(sid, "close");
      closeSession(sid, "header")
        .then(() => {
          toast("done", "session closed", "terminal tab closed");
          // the session just ended — back to the list, unless the user
          // already navigated elsewhere while the POST was in flight
          if (S.cur === sid) location.hash = "#/";
        })
        .catch(e => {
          S.closing.delete(sid);
          if (S.closePend[sid]) {
            S.closePend[sid].settle("dropped", { reason: "failed" });
            delete S.closePend[sid];
          }
          cls.disabled = false;
          cls.textContent = "✕ close";
          clientFail(sid, "close", e);   // a lost/rejected /stop the audit can't see
          toast("ask", "close failed", (e && e.error) || "");
        });
    };
    act.append(cls);
  }
  // resume (parked, with a cwd): reopen the new-session form preset to
  // `claude --resume <this sid>` in this session's directory
  if (!meta.live && meta.cwd) {
    const res = el("button", "sresume actses", "↻ resume");
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
    const cpt = el("button", "sstop actses", "⊜ compact");
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
        cptArmed = setTimeout(cptDisarm, ARM_MS);
        return;
      }
      clearTimeout(cptArmed);
      cptDisarm();
      sendQuickCmd("compact");
    };
    act2.append(cpt);
    // model: dropdown picker; the label shows the ctx probe's current model
    // (live via the `ctx` SSE event → updateStatsRow)
    const mwrap = el("span", "qcwrap actses");
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
    const ewrap = el("span", "qcwrap actses");
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
  ses._statsSig = null;      // fresh (empty) row — force the next paint through
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
  mk("agents", "agents", (ses.agents || []).length);
  // the ◉ monitors count: the actual list length once fetched, else the cheap
  // eager streams count (monitor_count) so the tab shows before the tab is opened
  ses.monTab = mk("monitors", "monitors",
                  ses.monitors ? ses.monitors.length : (meta.monitor_count || 0));
  // ⏳ background jobs — actual list length once fetched, else the cheap eager count
  ses.jobTab = mk("jobs", "jobs",
                  ses.jobs ? ses.jobs.length : (meta.job_count || 0));
  // 🧠 memory-wiki notes touched — actual list length once fetched, else the
  // cheap eager count. SCOPED: only sessions inside the enabled project
  // (aggregator-adapters, meta.memory_scope) get the tab at all.
  if (meta.memory_scope)
    ses.memTab = mk("memory", "memory",
                    ses.memory ? ses.memory.length : (meta.memory_count || 0));
  ses.errTab = mk("errors", "errors", meta.error_count || 0);   // live ⚠ count patches it
  $view.append(tabs);

  const body = el("div");
  ses.body = body;
  $view.append(body);

  if (tab === "mirror") {
    body.append(buildGoalCard());           // the active /goal, pinned at the very top
    body.append(buildTasksCard());          // the session's task list, pinned first
    body.append(buildPlanCard());           // pending plan approval …
    body.append(buildAskCard());            // … and question, above the composer
    body.append(buildComposer());
    // type right away on open — no click needed. After append (focus() on a
    // detached node is a no-op), and only when the box can send (a disabled
    // parked/headless composer takes no input anyway). The document-level
    // gestures (Esc, ⌃-keys, ⌃⇧←/→) are focus-independent, so this only
    // redirects plain typing. Not on an iPad: an unasked-for focus pops the
    // on-screen keyboard over the stream on every session open (and focus is
    // what triggers Safari's page auto-zoom — see style.css touch section).
    if (!ses.composer.disabled && !IS_IPAD) ses.composer.focus();
    body.append(buildFilterBar());
    const split = el("div", "split");
    // the transcript column: queued messages pinned ABOVE the newest-first
    // stream (so incoming activity never buries them) until they're delivered
    const scol = el("div", "scol");
    scol.append(buildQueuePin());
    scol.append(ses.stream);
    split.append(scol);
    const rail = el("div", "rail");
    ses.rail = rail;
    split.append(rail);
    body.append(split);
    updateAgents();
    updateMoreBtn();                      // the load-older affordance at the bottom
    applyFilter();                        // re-filter items already in the stream
  } else if (tab === "agents") {
    const wrap = el("div", "sgrid");
    ses.agentsGrid = wrap;
    body.append(wrap);
    updateAgents();
  } else if (tab === "monitors") {
    const wrap = el("div", "sgrid");
    ses.monitorsGrid = wrap;
    body.append(wrap);
    if (ses.monitors) renderMonitorsGrid();   // cached from a prior fetch
    else wrap.append(el("div", "empty", "loading monitors…"));
    loadMonitors();                            // (re)fetch fresh + start the live poll
  } else if (tab === "jobs") {
    const wrap = el("div", "sgrid");
    ses.jobsGrid = wrap;
    body.append(wrap);
    if (ses.jobs) renderJobsGrid();
    else wrap.append(el("div", "empty", "loading jobs…"));
    loadJobs();
  } else if (tab === "memory") {
    if (!ses.memory) body.append(el("div", "empty", "loading memory…"));
    paintMemory();                        // grid, or the note viewer if one is open
    loadMemory();
  } else if (tab === "errors") {
    renderErrorsInto(body);
  }
}

// A content signature of everything the stats row + ctx row RENDER, EXCLUDING
// the live ⏱ elapsed (Date.now-derived) — so a tick that only advances the
// clock, or a costs/ctx/running SSE that leaves the shown numbers unchanged,
// does NOT tear down and rebuild the row. The teardown (sr.textContent = "")
// reflows the header, which on iPad Safari drops an in-progress text selection
// (the "selection vanishes after ~1s" report, 2026-07-19). The clock still
// advances whenever any real datum changes (constant during active work).
function statsSig(ses) {
  const f = ses.agentFocus;
  if (f) {
    const d = f.data || {};
    const rec = (ses.agents || []).find(a => a.agent_id === f.aid) || {};
    return "A|" + [f.aid, rec.kind, rec.desc, rec.ended_at, rec.started_at,
      rec.tools, rec.model, rec.effort, rec.end_reason, rec.done,
      d.tools, d.cost, d.model].join(",")
      + "|" + JSON.stringify(d.usage || {}) + "|" + JSON.stringify(rec.ctx || {});
  }
  const st = ses.stats || {};
  const cost = (ses.costs && ses.costs.total_usd) || st.cost;
  return "S|" + [st.commands, st.failed, st.start, st.paused, st.files,
    st.added, st.removed, st.tk_in, st.tk_out, st.tk_read, st.tk_create, cost,
    st.msg_delivered, st.msg_read, (ses.meta && ses.meta.error_count) || 0,
    ses.meta && ses.meta.model].join(",")
    + "|" + JSON.stringify(ses.ctx || {});
}

function updateStatsRow() {
  const ses = S.ses;
  if (!ses || !ses.statsRow) return;
  const sig = statsSig(ses);
  if (sig === ses._statsSig) return;   // nothing the row shows changed — skip
  ses._statsSig = sig;                 // the teardown (preserves iPad selection)
  const sr = ses.statsRow;
  sr.textContent = "";
  // drilled into a subagent → the scoreboard shows THAT agent, not the session
  // (the "swap scoreboard on click" behaviour). SSE stats/costs/ctx events still
  // land here, but this branch keeps them from clobbering the agent view.
  if (ses.agentFocus) { renderAgentScoreboard(sr, ses.agentFocus); return; }
  const st = ses.stats || {};
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

/* Header-action visibility for the agent-focus state (docs/dashboard.md,
   *Subagent scoreboard swap*). While a subagent scoreboard is showing, the
   session-only actions (`.actses` — rename / migrate / cancel / rewind / close /
   resume / compact / model / effort) don't apply to a subagent, so they hide;
   ■ stop (`.actstop`) stays ONLY while the focused subagent is still running
   (interrupting the session is the one way to stop it). An action row left with
   nothing visible collapses so it leaves no gap. A full renderSessionChrome
   rebuild (going back) restores everything, agentFocus already cleared. */
function applyAgentActionVis() {
  const ses = S.ses;
  if (!ses) return;
  const focused = !!ses.agentFocus;
  $view.querySelectorAll(".actses").forEach(b => { b.style.display = focused ? "none" : ""; });
  const stop = $view.querySelector(".actstop");
  if (stop) {
    let show = true;
    if (focused) {
      const rec = (ses.agents || []).find(a => a.agent_id === ses.agentFocus.aid);
      show = !!(rec && agentStatus(rec)[1] === "st-run");
    }
    stop.style.display = show ? "" : "none";
  }
  $view.querySelectorAll(".shead .actrow").forEach(row => {
    const any = [...row.children].some(c => c.style.display !== "none");
    row.style.display = any ? "" : "none";
  });
}

/* The scoreboard for a drilled-into subagent — replaces the session totals with
   THAT agent's own numbers (docs/dashboard.md, *Subagent scoreboard swap*). It
   resolves the freshest agent row from ses.agents each render (so an `agents`
   SSE that finishes the agent updates the status here too) and reads tokens/cost
   from focus.data, the agent drill-down fetch (`/agent/<aid>` → usage + the
   server-priced `cost`). The prominent header NAME becomes the subagent's; the
   stats row leads with a "← session" link that restores the session view, and
   the ctx row repaints from the agent's own ctx bar. */
function renderAgentScoreboard(sr, focus) {
  const ses = S.ses;
  const rec = (ses.agents || []).find(a => a.agent_id === focus.aid) || {};
  const d = focus.data || {};
  const [sttxt, stcls] = agentStatus(rec);
  // the header badge/dot + .shead wash follow THIS agent's status, not the
  // session tab (the session pill said "busy" over a finished subagent).
  setBadgeAgent(ses.badge, sttxt, stcls);
  // the big header name updates to the subagent (the session title returns when
  // renderSessionChrome rebuilds on the way back). Skip during an inline rename.
  if (ses.projEl && !ses.projEl.querySelector("input"))
    ses.projEl.textContent =
      (rec.kind === "teammate" ? "👥 " : "◇ ") + (rec.desc || focus.aid);
  const back = el("a", "backses", "← session");
  back.href = "#/s/" + encodeURIComponent(S.cur);   // the mirror = the main agent
  sr.append(back);
  const add = (label, value, cls) => {
    const s = el("span");
    if (label) s.append(document.createTextNode(label + " "));
    s.append(el("span", cls || "v", value));
    sr.append(s);
  };
  add("", sttxt, stcls);
  const model = rec.model || (d.model ? String(d.model) : "");
  if (model) add("", model + (rec.effort ? "·" + rec.effort : ""), "amodel");
  const ev = d.tools != null ? d.tools : rec.tools;
  if (ev != null) add("", ev + " events");
  if (rec.started_at)
    add("⏱", rec.ended_at ? dur(rec.ended_at - rec.started_at) : ago(rec.started_at));
  const u = d.usage || {};
  const tin = u.in | 0, tout = u.out | 0, tread = u.cache | 0, tcre = u.create | 0;
  const tot = tin + tout + tread + tcre;
  if (tot)
    add("Σ", kfmt(tot) + " (" + kfmt(tin) + " in · " + kfmt(tout) + " out · "
        + kfmt(tread) + " cache · " + kfmt(tcre) + " write)");
  if (d.cost) add("≈", usd(d.cost), "cost");
  if (ses.ctxRow) {
    ses.ctxRow.textContent = "";
    const cx = rec.ctx;
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
  // the running ribbon is session-scoped; hide it while a subagent scoreboard
  // is showing (the header is about that one agent then, not the session)
  if (ses.agentFocus) { rr.style.display = "none"; return; }
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
  // model·effort — the web echo of the terminal mirror's op tag (opus-4.8·high)
  if (a.model) m.append(el("span", "amodel",
    a.model + (a.effort ? "·" + a.effort : "")));
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
  // a focused subagent finishing (running → done) must drop the ■ stop button
  // AND flip its scoreboard status/badge/wash (renderAgentScoreboard reads the
  // fresh agents row) — an `agents` SSE doesn't move statsSig, so re-render here
  // rather than via updateStatsRow's change-gate.
  if (ses.agentFocus) {
    applyAgentActionVis();
    if (ses.statsRow) {
      ses.statsRow.textContent = "";
      renderAgentScoreboard(ses.statsRow, ses.agentFocus);
    }
  }
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

/* ---------- monitors (list tab + drill-down) ---------- */
/* The monitors tab mirrors the agents tab: a grid of monitor cards (each a
   Monitor tool run with its lifecycle state) that drills into a per-monitor
   detail on click. Data comes from plugins.monitors(sid) — the MAIN transcript
   (command/description/events) merged with the audit streams lifecycle state
   (running/ended/duration). Loaded lazily on tab open (a transcript parse), then
   re-fetched on a light poll while any monitor is live — the count badge stays
   fresh live via the cheap `monitors` SSE (docs/dashboard.md, *Monitors tab*). */

function monitorStatus(m) {
  if (m.live) return ["running", "st-run"];
  const er = m.end_reason || "";
  if (!er && m.ended_at == null) return ["unknown", "st-warn"];
  if (er.indexOf("no output") >= 0 || er.indexOf("silent") >= 0)
    return ["ended · no output", "st-ok"];
  if (er.indexOf("not-found") >= 0 || er.indexOf("never found") >= 0)
    return ["ended · not found", "st-warn"];
  if (er.indexOf("parked") >= 0) return ["ended (session end)", "st-ok"];
  return ["ended", "st-ok"];
}

function sortedMonitors(mons) {
  // live first, then most-recently-started on top
  return [...mons].sort((x, y) => (!!y.live - !!x.live)
    || ((y.started_at || 0) - (x.started_at || 0)));
}

function monitorCard(m) {
  const [sttxt, stcls] = monitorStatus(m);
  const card = el("a", "acard");
  card.dataset.st = stcls;
  card.href = "#/s/" + encodeURIComponent(S.cur) + "/m/" + encodeURIComponent(m.task);
  const name = m.description || m.command || m.task;
  card.append(el("div", "aid", "◉ " + name));
  // subtitle: the command when the name is the description, else the task id
  const sub = (m.description && m.command) ? m.command : m.task;
  if (sub) card.append(el("div", "desc", sub));
  const meta = el("div", "meta");
  meta.append(el("span", stcls, sttxt));
  if (m.persistent) meta.append(el("span", "amodel", "persistent"));
  else if (m.timeout_ms) meta.append(el("span", "amodel", "≤" + dur(m.timeout_ms / 1000)));
  meta.append(el("span", "", (m.event_count || 0) + " events"));
  if (m.started_at && m.ended_at) meta.append(el("span", "", dur(m.ended_at - m.started_at)));
  else if (m.started_at) meta.append(el("span", "", ago(m.started_at)));
  card.append(meta);
  return card;
}

function renderMonitorsGrid() {
  const ses = S.ses;
  if (!(ses && ses.tab === "monitors" && ses.monitorsGrid && ses.monitorsGrid.isConnected))
    return;
  ses.monitorsGrid.textContent = "";
  const mons = ses.monitors || [];
  if (!mons.length) {
    ses.monitorsGrid.append(el("div", "empty", "no monitors in this session"));
    return;
  }
  for (const m of sortedMonitors(mons)) ses.monitorsGrid.append(monitorCard(m));
}

function loadMonitors() {
  const ses = S.ses, sid = S.cur;
  if (!ses || !sid) return;
  fetch("/api/session/" + encodeURIComponent(sid) + "/monitors")
    .then(r => r.json())
    .then(d => {
      if (S.cur !== sid || !S.ses) return;
      S.ses.monitors = d.monitors || [];
      setMonCount(S.ses.monitors.length);
      if (S.ses.monitorFocus) repaintMonitorDetail();
      else renderMonitorsGrid();
      scheduleMonitorPoll();
    })
    .catch(() => {});
}

function scheduleMonitorPoll() {
  clearMonitorPoll();
  const ses = S.ses;
  if (!ses) return;
  const live = (ses.monitors || []).some(m => m.live);
  // keep the list / detail fresh while a monitor is still firing events
  if (live && (ses.tab === "monitors" || ses.monitorFocus))
    ses.monPoll = setInterval(loadMonitors, 4000);
}

function clearMonitorPoll() {
  if (S.ses && S.ses.monPoll) { clearInterval(S.ses.monPoll); S.ses.monPoll = null; }
}

// the ◉ monitors tab badge, live — the cheap `monitors` SSE count (a new launch
// bumps it) OR the exact list length once /monitors is fetched (setMonCount).
function updateMonCount(n) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.monitor_count = n;
  setTabCount(ses.monTab, n);
  // a new monitor arrived while the tab is open -> refresh the list
  if (ses.tab === "monitors") loadMonitors();
}

function setMonCount(n) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.monitor_count = n;
  setTabCount(ses.monTab, n);
}

function showMonitor(sid, task) {
  if (S.cur !== sid) showSession(sid, "monitors");
  const ses = S.ses;
  if (!ses) return;
  closeAgentStream();
  clearMonitorPoll();
  ses.tab = "monitor:" + task;
  ses.monitorFocus = task;
  // no tab-bar entry is "monitor:<task>", so light the `monitors` tab (the same
  // "you are here" cue the agents drill-down restores on its tab)
  $view.querySelectorAll(".tabs a").forEach(a =>
    a.classList.toggle("on", /\/monitors$/.test(a.getAttribute("href") || "")));
  updateRunning();
  if (ses.monitors) repaintMonitorDetail();
  else loadMonitors();          // direct navigation / reload — fetch then paint
}

function repaintMonitorDetail() {
  const ses = S.ses;
  if (!ses || !ses.monitorFocus || !ses.body) return;
  const task = ses.monitorFocus;
  const m = (ses.monitors || []).find(x => x.task === task);
  ses.body.textContent = "";
  ses.body.append(monitorCrumbs(S.cur, m || { task: task }));
  const wrap = el("div");
  ses.body.append(wrap);
  if (!m) { wrap.append(el("div", "empty", "monitor not found")); return; }
  renderMonitorDetail(wrap, m);
  scheduleMonitorPoll();        // live monitor -> keep its detail refreshing
}

/* The monitor breadcrumb — ◉ monitors (back to the list) › this monitor. */
function monitorCrumbs(sid, m) {
  const nav = el("div", "crumbs");
  const back = el("a", "crumb");
  back.href = "#/s/" + encodeURIComponent(sid) + "/monitors";
  back.title = "back to the monitors list";
  back.append(el("span", "cg", "◉"), document.createTextNode(" monitors"));
  const cur = el("span", "crumb cur");
  cur.append(el("span", "cg", "◉"),
             document.createTextNode(" " + (m.description || m.command || m.task)));
  nav.append(back, el("span", "csep", "›"), cur);
  return nav;
}

function renderMonitorDetail(container, m) {
  const [sttxt, stcls] = monitorStatus(m);
  const info = el("div", "mdetail");
  const h = el("div", "mdhead");
  h.append(el("span", "k k-monitor", "◉ monitor"), el("span", stcls, sttxt));
  info.append(h);
  if (m.description) info.append(el("div", "mdesc", m.description));
  if (m.command) {
    info.append(el("div", "lbl", m.source === "ws" ? "websocket" : "command"));
    info.append(pre(m.command));
  }
  const grid = el("div", "mmeta");
  const add = (k, v) => {
    if (v == null || v === "") return;
    grid.append(el("span", "mk", k), el("span", "mv", String(v)));
  };
  add("task", m.task);
  add("lifetime", m.persistent ? "persistent"
    : (m.timeout_ms ? "≤" + dur(m.timeout_ms / 1000) : "—"));
  add("events", m.event_count);
  if (m.started_at) add("started", new Date(m.started_at * 1000).toLocaleString());
  if (m.ended_at) add("ended", new Date(m.ended_at * 1000).toLocaleString());
  if (m.started_at && m.ended_at) add("duration", dur(m.ended_at - m.started_at));
  else if (m.started_at && m.live) add("running for", ago(m.started_at));
  add("end reason", m.end_reason);
  info.append(grid);
  container.append(info);

  const evwrap = el("div", "mevents");
  const evs = m.events || [];
  const label = m.events_truncated
    ? "events (recent " + evs.length + " of " + m.event_count + ")" : "events";
  evwrap.append(el("div", "mhead", label));
  if (!evs.length)
    evwrap.append(el("div", "empty", m.live ? "no events yet — waiting" : "no events fired"));
  for (const e of evs.slice().reverse()) evwrap.append(monitorEventRow(e));   // newest first
  container.append(evwrap);
}

function monitorEventRow(e) {
  const row = el("div", "mev" + (e.status ? " mev-status" : ""));
  if (e.ts) row.append(el("span", "mts", new Date(e.ts * 1000).toLocaleTimeString()));
  const txt = e.status
    ? ("stream " + e.status + (e.summary ? " · " + e.summary : ""))
    : (e.event || "");
  row.append(el("span", "mtxt", txt));
  return row;
}

/* ---------- background jobs (list tab + drill-down) ---------- */
/* The jobs tab mirrors the monitors/agents tabs for `run_in_background` Bash
   jobs (and Ctrl+B conversions): a grid of job cards with lifecycle state, each
   drilling into command + full output. Data comes from sessionapi.jobs(sid) —
   the audit streams state (kind='bg') merged with the command from the mirror
   ops (copy-group). A job's OUTPUT is NOT in the transcript (it streams to the
   ops), so the drill-down fetches it from the same ops via /copy/<task>/out (the
   ⧉out copy endpoint). Loaded lazily on tab-open, re-fetched on a light poll
   while any job is live; the count badge stays fresh via the `jobs` SSE. */

function jobStatus(j) {
  if (j.live) return ["running", "st-run"];
  const er = j.end_reason || "";
  if (er.indexOf("parked") >= 0) return ["ended (session end)", "st-ok"];
  if (er.indexOf("backstop") >= 0 || er.indexOf("timeout") >= 0)
    return ["ended · timed out", "st-warn"];
  if (!er && j.ended_at == null) return ["unknown", "st-warn"];
  return ["finished", "st-ok"];   // writer-gone / vanished = normal completion
}

function sortedJobs(jobs) {
  return [...jobs].sort((x, y) => (!!y.live - !!x.live)
    || ((y.started_at || 0) - (x.started_at || 0)));
}

function jobCard(j) {
  const [sttxt, stcls] = jobStatus(j);
  const card = el("a", "acard");
  card.dataset.st = stcls;
  card.href = "#/s/" + encodeURIComponent(S.cur) + "/j/" + encodeURIComponent(j.task);
  const name = firstLine(j.command) || j.task;
  card.append(el("div", "aid", "⏳ " + name));
  card.append(el("div", "desc", j.task));
  const meta = el("div", "meta");
  meta.append(el("span", stcls, sttxt));
  if (j.lines != null) meta.append(el("span", "", j.lines + " lines"));
  if (j.started_at && j.ended_at) meta.append(el("span", "", dur(j.ended_at - j.started_at)));
  else if (j.started_at) meta.append(el("span", "", ago(j.started_at)));
  card.append(meta);
  return card;
}

function renderJobsGrid() {
  const ses = S.ses;
  if (!(ses && ses.tab === "jobs" && ses.jobsGrid && ses.jobsGrid.isConnected)) return;
  ses.jobsGrid.textContent = "";
  const jobs = ses.jobs || [];
  if (!jobs.length) {
    ses.jobsGrid.append(el("div", "empty", "no background jobs in this session"));
    return;
  }
  for (const j of sortedJobs(jobs)) ses.jobsGrid.append(jobCard(j));
}

function loadJobs() {
  const ses = S.ses, sid = S.cur;
  if (!ses || !sid) return;
  fetch("/api/session/" + encodeURIComponent(sid) + "/jobs")
    .then(r => r.json())
    .then(d => {
      if (S.cur !== sid || !S.ses) return;
      S.ses.jobs = d.jobs || [];
      setJobCount(S.ses.jobs.length);
      if (S.ses.jobFocus) repaintJobDetail();
      else renderJobsGrid();
      scheduleJobPoll();
    })
    .catch(() => {});
}

function scheduleJobPoll() {
  clearJobPoll();
  const ses = S.ses;
  if (!ses) return;
  const live = (ses.jobs || []).some(j => j.live);
  if (live && (ses.tab === "jobs" || ses.jobFocus))
    ses.jobPoll = setInterval(loadJobs, 4000);
}

function clearJobPoll() {
  if (S.ses && S.ses.jobPoll) { clearInterval(S.ses.jobPoll); S.ses.jobPoll = null; }
}

function updateJobCount(n) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.job_count = n;
  setTabCount(ses.jobTab, n);
  if (ses.tab === "jobs") loadJobs();     // a new job arrived with the tab open
}

function setJobCount(n) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.job_count = n;
  setTabCount(ses.jobTab, n);
}

/* ---------- memory tab (the memory-wiki notes a session touched) ---------- */

function loadMemory() {
  const ses = S.ses, sid = S.cur;
  if (!ses || !sid) return;
  fetch("/api/session/" + encodeURIComponent(sid) + "/memory")
    .then(r => r.json())
    .then(d => {
      if (S.cur !== sid || !S.ses) return;
      S.ses.memory = d.memory || [];
      setMemCount(S.ses.memory.length);
      // repaint the grid only when it's showing (a note viewer stays put)
      if (S.ses.tab === "memory" && !(S.ses.noteTrail && S.ses.noteTrail.length))
        paintMemory();
    })
    .catch(() => {});
}

function setMemCount(n) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.memory_count = n;
  setTabCount(ses.memTab, n);
}

function updateMemCount(n) {          // live SSE badge patch
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.memory_count = n;
  setTabCount(ses.memTab, n);
  if (ses.tab === "memory" && !(ses.noteTrail && ses.noteTrail.length))
    loadMemory();                     // a new note was touched with the grid open
}

/* Paint the memory tab body: the note grid, or the note viewer when a note
   (or a followed [[wikilink]]) is open. */
function paintMemory() {
  const ses = S.ses;
  if (!ses || !ses.body || ses.tab !== "memory") return;
  ses.body.textContent = "";
  if (ses.noteTrail && ses.noteTrail.length) { renderNoteView(); return; }
  const wrap = el("div", "sgrid memgrid");
  ses.memGrid = wrap;
  ses.body.append(wrap);
  renderMemoryGrid();
}

function renderMemoryGrid() {
  const ses = S.ses;
  if (!ses || !ses.memGrid) return;
  ses.memGrid.textContent = "";
  const mem = ses.memory || [];
  if (!mem.length) {
    ses.memGrid.append(el("div", "empty", "no memory notes touched in this session"));
    return;
  }
  for (const m of mem) ses.memGrid.append(memCard(m));
}

function memCard(m) {
  const card = el("div", "memcard");
  const verb = (m.verb || "Read").toLowerCase();
  card.append(el("span", "vchip v-" + verb, verb));
  card.append(el("span", "memname", m.name || "?"));
  if (m.agent) card.append(el("span", "memagent", "⇢ " + m.agent));
  if (m.count > 1) card.append(el("span", "memcount", "×" + m.count));
  card.onclick = () => openNoteRef({ path: m.path }, true);
  return card;
}

/* Open a note by absolute path (a grid row) or bare stem (a followed
   [[wikilink]]). `reset` starts a fresh breadcrumb trail (a grid click);
   following a link pushes onto it. */
function openNoteRef(ref, reset) {
  const ses = S.ses, sid = S.cur;
  if (!ses || !sid) return;
  const q = ref.path ? ("path=" + encodeURIComponent(ref.path))
                     : ("stem=" + encodeURIComponent(ref.stem || ""));
  fetch("/api/session/" + encodeURIComponent(sid) + "/note?" + q)
    .then(r => r.json())
    .then(d => {
      if (S.cur !== sid || !S.ses) return;
      if (reset || !S.ses.noteTrail) S.ses.noteTrail = [];
      S.ses.noteTrail.push(d);
      S.ses.noteFocus = d.path || d.name;
      paintMemory();
      // start the newly-opened note from its top — following a link deep in one
      // note shouldn't land you mid-way down the next (the page scrolls the
      // window; the sticky header stays pinned)
      window.scrollTo(0, 0);
    })
    .catch(() => {});
}

function renderNoteView() {
  const ses = S.ses;
  if (!ses || !ses.body) return;
  const trail = ses.noteTrail || [];
  const d = trail[trail.length - 1];
  ses.body.textContent = "";
  ses.body.append(noteCrumbs(trail));
  if (!d) return;
  const wrap = el("div", "note");
  if (d.missing) {
    wrap.append(el("div", "empty", "note not found: " + (d.name || "?")));
    ses.body.append(wrap);
    return;
  }
  if (d.frontmatter && d.frontmatter.length) {
    const fm = el("div", "note-fm");
    for (const [k, v] of d.frontmatter) { fm.append(el("span", "fk", k), el("span", "fv", v)); }
    wrap.append(fm);
  }
  const bodyEl = el("div", "note-body");
  bodyEl.innerHTML = d.html || "";        // server-rendered, escape-first (opshtml/notehtml)
  wrap.append(bodyEl);
  if (d.backlinks && d.backlinks.length) {
    const bl = el("div", "note-backlinks");
    bl.append(el("div", "lbl", "backlinks"));
    for (const stem of d.backlinks) {
      const a = el("a", "wl", stem);
      a.dataset.note = stem;
      bl.append(a);
    }
    wrap.append(bl);
  }
  // follow a [[wikilink]] / backlink. DIRECT per-anchor onclick, NOT delegation:
  // these anchors have no href, and mobile Safari won't dispatch a bubbled click
  // from a tap on such an element to a container listener — the same reason the
  // grid cards (which DO open on the phone) use a direct onclick. Covers both the
  // body links and the backlinks (both live under `wrap`); dead links get none.
  wrap.querySelectorAll("a.wl").forEach(a => {
    if (a.classList.contains("dead")) return;
    a.onclick = (ev) => { ev.preventDefault(); openNoteRef({ stem: a.dataset.note }); };
  });
  ses.body.append(wrap);
}

/* The note breadcrumb — 🧠 memory (back to the grid) › note › followed note … */
function noteCrumbs(trail) {
  const nav = el("div", "crumbs");
  const back = el("a", "crumb");
  back.href = "#/s/" + encodeURIComponent(S.cur) + "/memory";
  back.title = "back to the memory list";
  back.append(el("span", "cg", "🧠"), document.createTextNode(" memory"));
  back.onclick = (e) => {
    e.preventDefault();
    S.ses.noteTrail = []; S.ses.noteFocus = null; paintMemory();
  };
  nav.append(back);
  trail.forEach((d, i) => {
    nav.append(el("span", "csep", "›"));
    if (i === trail.length - 1) {
      const cur = el("span", "crumb cur");
      cur.append(el("span", "cg", "🧠"), document.createTextNode(" " + (d.name || "?")));
      nav.append(cur);
    } else {
      const a = el("a", "crumb");
      a.href = "javascript:void 0";
      a.append(document.createTextNode(d.name || "?"));
      a.onclick = (e) => { e.preventDefault(); S.ses.noteTrail = trail.slice(0, i + 1); paintMemory(); };
      nav.append(a);
    }
  });
  return nav;
}

function showJob(sid, task) {
  if (S.cur !== sid) showSession(sid, "jobs");
  const ses = S.ses;
  if (!ses) return;
  closeAgentStream();
  clearJobPoll();
  ses.tab = "job:" + task;
  ses.jobFocus = task;
  $view.querySelectorAll(".tabs a").forEach(a =>
    a.classList.toggle("on", /\/jobs$/.test(a.getAttribute("href") || "")));
  updateRunning();
  if (ses.jobs) repaintJobDetail();
  else loadJobs();
}

function repaintJobDetail() {
  const ses = S.ses;
  if (!ses || !ses.jobFocus || !ses.body) return;
  const task = ses.jobFocus;
  const j = (ses.jobs || []).find(x => x.task === task);
  ses.body.textContent = "";
  ses.body.append(jobCrumbs(S.cur, j || { task: task }));
  const wrap = el("div");
  ses.body.append(wrap);
  if (!j) { wrap.append(el("div", "empty", "job not found")); return; }
  renderJobDetail(wrap, j);
  scheduleJobPoll();
}

/* The job breadcrumb — ⏳ jobs (back to the list) › this job. */
function jobCrumbs(sid, j) {
  const nav = el("div", "crumbs");
  const back = el("a", "crumb");
  back.href = "#/s/" + encodeURIComponent(sid) + "/jobs";
  back.title = "back to the jobs list";
  back.append(el("span", "cg", "⏳"), document.createTextNode(" jobs"));
  const cur = el("span", "crumb cur");
  cur.append(el("span", "cg", "⏳"),
             document.createTextNode(" " + (firstLine(j.command) || j.task)));
  nav.append(back, el("span", "csep", "›"), cur);
  return nav;
}

function renderJobDetail(container, j) {
  const [sttxt, stcls] = jobStatus(j);
  const info = el("div", "mdetail");
  const h = el("div", "mdhead");
  h.append(el("span", "k k-job", "⏳ background"), el("span", stcls, sttxt));
  info.append(h);
  if (j.command) {
    info.append(el("div", "lbl", "command"));
    info.append(pre(j.command));
  }
  const grid = el("div", "mmeta");
  const add = (k, v) => {
    if (v == null || v === "") return;
    grid.append(el("span", "mk", k), el("span", "mv", String(v)));
  };
  add("task", j.task);
  add("lines", j.lines);
  if (j.started_at) add("started", new Date(j.started_at * 1000).toLocaleString());
  if (j.ended_at) add("ended", new Date(j.ended_at * 1000).toLocaleString());
  if (j.started_at && j.ended_at) add("duration", dur(j.ended_at - j.started_at));
  else if (j.started_at && j.live) add("running for", ago(j.started_at));
  add("end reason", j.end_reason);
  info.append(grid);
  container.append(info);

  // output lives in the ops, not the transcript — fetch it from the copy endpoint
  const outwrap = el("div", "mevents");
  outwrap.append(el("div", "mhead", "output"));
  const box = el("div", "joutput");
  box.append(el("div", "empty", "loading output…"));
  outwrap.append(box);
  container.append(outwrap);
  fetch("/api/session/" + encodeURIComponent(S.cur) + "/copy/"
        + encodeURIComponent(j.task) + "/out")
    .then(r => r.text())
    .then(t => {
      if (!box.isConnected) return;
      box.textContent = "";
      box.append(t.trim() ? pre(t) : el("div", "empty",
        j.live ? "no output yet" : "(no output)"));
    })
    .catch(() => { if (box.isConnected) { box.textContent = ""; box.append(el("div", "empty", "output unavailable")); } });
}

/* ---------- timeline (activity / agent drill-down) ---------- */

function showAgent(sid, aid) {
  if (S.cur !== sid) showSession(sid, "agents");
  closeAgentStream();                       // switching agents / re-entering
  S.ses.tab = "agent:" + aid;
  const ses = S.ses;
  // no tab-bar entry is "agent:<id>", so light the `agents` tab — a drill-down
  // is a descent INTO agents, and this restores the "you are here" cue the
  // breadcrumb also carries (previously every tab went dark here).
  $view.querySelectorAll(".tabs a").forEach(a =>
    a.classList.toggle("on", /\/agents$/.test(a.getAttribute("href") || "")));
  // swap the top scoreboard to this agent — first from the enriched agents row
  // we already have (status/model/events/ctx/duration), then the fetch below
  // fills in tokens + cost.
  ses.agentFocus = { aid: aid, data: null };
  updateStatsRow();
  updateRunning();
  applyAgentActionVis();     // the session-only header actions don't apply here
  if (ses.body) {
    ses.body.textContent = "";
    const rec = (ses.agents || []).find(a => a.agent_id === aid);
    ses.body.append(agentCrumbs(sid, aid, rec));   // back-up-the-hierarchy trail
    const tlWrap = el("div");                       // renderTimelineInto clears its
    ses.body.append(tlWrap);                        // container, so keep it off the crumbs
    // a running agent's page grows live; a parked one (ended_at set) is
    // fetch-once — its transcript won't grow, so don't open a stream.
    const live = !!rec && rec.ended_at == null;
    // newest-first, matching the main agent's mirror stream (which prepends —
    // appendItems), so a subagent's most recent message reads at the top
    renderTimelineInto(tlWrap,
                       "/api/session/" + encodeURIComponent(sid) + "/agent/" + encodeURIComponent(aid),
                       (rec && rec.desc) || aid,
                       live ? { sid: sid, aid: aid } : null, true,
                       // feed the agent's tokens/cost rollup up into the scoreboard
                       (d) => { if (ses.agentFocus && ses.agentFocus.aid === aid) {
                                  ses.agentFocus.data = d; updateStatsRow(); } });
  }
}

/* The agent-hierarchy breadcrumb on a subagent drill-down — the MAIN agent →
   this subagent (docs/dashboard.md, *Breadcrumbs*). Just the two agent nodes
   (the hierarchy is one level deep — a session's flat agent list): the main
   agent is a link back to its mirror (#/s/<sid>), labelled by the session's
   title; the current subagent is the highlighted end node. Icons: ◆ the main
   agent, ◇/👥 the subagent. Clicking the main node is how you go back. */
function agentCrumbs(sid, aid, rec) {
  const nav = el("div", "crumbs");
  const meta = (S.ses && S.ses.meta) || {};
  const sesName = meta.title || (meta.cwd ? proj(meta) : shortSid(sid));
  const main = el("a", "crumb");
  main.href = "#/s/" + encodeURIComponent(sid);       // the mirror = the main agent
  main.title = "back to the main agent";
  main.append(el("span", "cg", "◆"), document.createTextNode(" " + sesName));
  const cur = el("span", "crumb cur");
  cur.append(el("span", "cg", rec && rec.kind === "teammate" ? "👥" : "◇"),
             document.createTextNode(" " + ((rec && rec.desc) || aid)));
  nav.append(main, el("span", "csep", "›"), cur);
  return nav;
}

function renderTimelineInto(container, apiUrl, title, live, newestFirst, onData) {
  container.append(el("div", "empty", "loading " + title + "…"));
  fetch(apiUrl).then(r => r.json()).then(d => {
    if (onData) onData(d);            // hand the payload (usage/cost) to the caller
    if (!container.isConnected) return;
    container.textContent = "";
    container.append(timelineHead(d, title));
    const list = el("div", "tl");
    const entries = d.entries || [];
    if (!entries.length) list.append(el("div", "empty", "no recorded activity"));
    // newestFirst reverses the chronological entries so the most recent reads at
    // the top (the subagent drill-down, matching the main mirror); the head stays
    // above regardless. The live SSE below then prepends new increments to match.
    const ordered = newestFirst ? entries.slice().reverse() : entries;
    for (const ent of ordered) list.append(timelineEntry(ent));
    container.append(list);
    // LIVE agents: resume the SSE at the byte cursor the REST read stopped at
    // (d.pos — additive; absent for a provider with no incremental support,
    // e.g. codex, so the drill-down simply stays fetch-once).
    if (live && d.pos != null) connectAgentStream(live.sid, live.aid, d.pos, list, newestFirst);
  }).catch(() => {
    if (!container.isConnected) return;
    container.textContent = "";
    container.append(el("div", "empty", "no transcript available for " + title));
  });
}

/* The live agent timeline stream: adds new increment `entries` (at the bottom
   for oldest-first, or the TOP when newestFirst — matching the initial render's
   order) and applies `resolve` events — a tool_result that arrived in a later
   increment than its tool_use — by finding the tool entry via its data-tool-id
   and filling in the result. Reconnects (like the per-session stream) resume at
   the latest byte cursor so nothing repeats. */
function connectAgentStream(sid, aid, pos, list, newestFirst) {
  let cur = pos;
  const es = new EventSource("/events/agent/" + encodeURIComponent(sid)
                             + "/" + encodeURIComponent(aid) + "?pos=" + cur);
  S.ses.agentEs = es;
  es.onopen = () => sseMark("agent", true, { sid });
  es.addEventListener("entries", (e) => {
    const d = JSON.parse(e.data);
    if (d.pos != null) cur = d.pos;
    const empty = list.querySelector(".empty");
    if (empty) empty.remove();
    // newestFirst: prepend each increment entry in chronological order, so the
    // increment's newest ends topmost and the whole increment sits above older
    // ones (the reverse of the append path).
    for (const ent of d.entries || []) {
      const node = timelineEntry(ent);
      if (newestFirst) list.prepend(node); else list.append(node);
    }
  });
  es.addEventListener("resolve", (e) => {
    const d = JSON.parse(e.data);
    if (d.pos != null) cur = d.pos;
    for (const r of d.resolutions || []) applyResolution(list, r);
  });
  es.onerror = () => {
    sseMark("agent", false, { sid });
    es.close();
    if (!S.ses || S.ses.agentEs !== es) return;   // navigated away
    S.ses.agentEs = null;
    setTimeout(() => {
      if (S.ses && S.ses.tab === "agent:" + aid && list.isConnected)
        connectAgentStream(sid, aid, cur, list, newestFirst);
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
  } else if (ent.t === "recap") {
    // Claude Code's away-summary recap — one-line summary of what happened
    // while you were away (auto after idle, or on-demand /recap).
    kcls = "k-recap"; ktxt = "↩ recap";
    sum = firstLine(ent.text); open = false;
    bd.append(mdOrPre(ent.html, ent.text));
  } else if (ent.t === "monitor") {
    // A Monitor tool event (or its stream-ended `status`). The launch itself is
    // a separate `tool` entry (name "Monitor"); these are the events it fired.
    kcls = "k-monitor";
    ktxt = ent.status ? ("◉ monitor " + ent.status) : "◉ monitor event";
    const body = ent.event || ent.summary || "";
    sum = firstLine(body); open = false;
    if (body) bd.append(pre(body));
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
      // clipboard is undefined over a plain-http tunnel (non-secure context);
      // guard it so the ⧉ copy doesn't reject unhandled there.
      if (!navigator.clipboard) return toast("ask", "copy failed", "needs https");
      navigator.clipboard.writeText(text).then(
        () => toast("done", "copied " + (what === "cmd" ? "command" : what === "out" ? "output" : "block"),
                    text.length + " chars"),
        () => toast("ask", "copy failed", "clipboard permission?"));
    })
    .catch(() => toast("ask", "copy failed", "try again"));
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

/* ---------- viewport diagnostics (?vpdiag) ----------
   A live readout of the numbers a remote device (an iPad) is actually
   rendering with — layout vs visual viewport, scale, screen, dpr — for
   debugging zoom/fit reports that headless WebKit can't reproduce. Doubles
   as a staleness probe: the overlay only exists in THIS build of app.js,
   so "no overlay" == the device is loading stale assets. */
if (/[?&#]vpdiag/.test(location.search + location.hash)) {
  const box = el("div");
  box.style.cssText =
    "position:fixed;left:8px;bottom:8px;z-index:9999;padding:8px 10px;" +
    "background:#000c;color:#9f9;font:12px/1.5 monospace;border-radius:8px;" +
    "pointer-events:none;white-space:pre;max-width:95vw;overflow:hidden";
  const meta = document.querySelector("meta[name=viewport]");
  const upd = () => {
    const vv = window.visualViewport;
    box.textContent =
      `layout ${document.documentElement.clientWidth}×${document.documentElement.clientHeight}` +
      ` inner ${innerWidth}×${innerHeight}\n` +
      (vv ? `visual ${Math.round(vv.width)}×${Math.round(vv.height)} scale ${vv.scale.toFixed(3)}\n` : "") +
      `screen ${screen.width}×${screen.height} dpr ${devicePixelRatio}` +
      ` scrollW ${document.documentElement.scrollWidth}\n` +
      `meta ${(meta && meta.content) || "MISSING"}\nIS_IPAD ${IS_IPAD}`;
  };
  upd();
  addEventListener("resize", upd);
  addEventListener("orientationchange", () => setTimeout(upd, 400));
  if (window.visualViewport) window.visualViewport.addEventListener("resize", upd);
  document.body.append(box);
}

/* ---------- boot ---------- */

initNotifBtn();
// the new-session form's last-used prefs live on the backend now (cross-device)
// — prime the cache so the first form open reads them synchronously
fetch("/api/ns-prefs").then(r => r.json())
  .then(p => { S.nsPrefs = p || {}; }).catch(() => {});
// seed the hidden-directory set before the first list paint (the SSE snapshot
// carries the session rows, not this pref) — a failed fetch just leaves nothing
// hidden, never a broken list
fetch("/api/dirs/hidden").then(r => r.json())
  .then(d => { if (d && typeof d === "object") { S.hidden = d; if (!S.cur) renderList(true); } })
  .catch(() => {});
connectGlobal();
// A deep link from a Telegram/off-device notification lands as ?s=<sid> (a
// query param, NOT a #fragment — Telegram's auto-linker drops the fragment, so
// the sid must ride the query). Translate it into the hash route the router
// speaks, and strip ?s= from the URL so a later reload/share carries a clean
// hash link. A pre-existing hash wins (an explicit #/... in the same URL).
(function deepLinkFromQuery() {
  const m = /[?&]s=([^&]+)/.exec(location.search);
  if (!m || location.hash) return;
  history.replaceState(null, "", location.pathname);   // drop ?s=…
  location.hash = "#/s/" + encodeURIComponent(decodeURIComponent(m[1]));
})();
route();
renderAttention();
refreshAccounts();
setInterval(refreshAccounts, ACCOUNTS_POLL_MS);
setInterval(() => { if (!S.cur) renderList(true); }, LIST_REFRESH_MS);
