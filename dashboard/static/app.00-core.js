"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.12-init.js for the boot/init sequence.

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
const $statsbtn = document.getElementById("statsbtn");
const $modal = document.getElementById("modal");
const $accounts = document.getElementById("accounts");

const S = {
  sessions: [],          // last global snapshot
  stats: null,           // last /api/stats payload (Stats page); refetched on show
  statsWindow: "7d",     // selected Pulse period toggle (persists across renders)
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

// NO EMOJI (docs/dashboard.md, *No emoji*): a few of the symbols this UI paints
// are EMOJI-CAPABLE codepoints (⚠ ⚙ ✉ ⏱ ▶ …) — text glyphs by default, but a
// browser whose page fonts lack one falls back to the colour-emoji font (the ☀
// wake button did exactly that, which is why its sun is now an inline SVG).
// U+FE0E (variation selector-15) is the standard "render as text" request; every
// string that becomes page text goes through tp(), so no glyph can turn colour.
// Twin of opshtml.text_presentation (mirror-op text takes that path instead).
const EMOJI_CAPABLE =
  /[\u203c\u2049\u2194\u21a9\u21aa\u2328\u23f1\u23f2\u25aa\u25ab\u25b6\u25c0\u2600\u2601\u260e\u2611\u2618\u2699\u26a0\u26d3\u2702\u2709\u2714\u2716\u2733\u2734\u2744\u2747\u27a1](?![\ufe0e\ufe0f])/g;
function tp(s) { return s.replace(EMOJI_CAPABLE, "$&\ufe0e"); }
// every text node the app builds goes through tp() — el() below and this, the
// document.createTextNode replacement (glyph + label pairs are built that way)
function tnode(s) { return document.createTextNode(typeof s === "string" ? tp(s) : s); }

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = typeof text === "string" ? tp(text) : text;
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

// This DEVICE's stable identity (unlike per-load CLIENT_ID) — persisted in
// localStorage so it survives reloads and is the SAME across every tab on this
// machine. Sent with the push subscription and the presence beat so the server
// can route the on-device notification to the ONE device you're working on
// (docs/dashboard.md *Device routing*). localStorage can throw (Safari private
// mode) → fall back to a per-load id.
const DEVICE_ID = (() => {
  try {
    let id = localStorage.getItem("baqylau-device");
    if (!id) {
      id = Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem("baqylau-device", id);
    }
    return id;
  } catch (_) { return CLIENT_ID; }
})();
// A friendly label for this device (best-effort, capped server-side) — shown in
// audit rows so a push endpoint is legible ("which device is that?").
const DEVICE_LABEL = ((navigator.userAgentData && navigator.userAgentData.platform)
  || navigator.platform || "device").slice(0, 60);

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
  "fg": ["⚙", "fg"], "bg": ["◷", "bg"], "monitor": ["◉", "monitor"],
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


