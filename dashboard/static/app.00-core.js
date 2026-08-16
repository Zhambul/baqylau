"use strict";
// dashboard/static/app.00-core.js — the SPA's core: page state (S), the DOM and
// formatting helpers, the postJSON spine, toasts.
//
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.
//
// Server-rendered op HTML (dashboard/opshtml/ — escaped there, the
// neutralize() analog) is the ONLY thing inserted via innerHTML; everything
// built from JSON (timelines, stats, session rows) goes through el() /
// textContent, so transcript text can never become markup.

const $view = document.getElementById("view");
const $toasts = document.getElementById("toasts");
const $conn = document.getElementById("conn");
const $notifbtn = document.getElementById("notifbtn");
const $attn = document.getElementById("attn");
const $favicon = document.getElementById("favicon");
const $newbtn = document.getElementById("newbtn");
const $notifytoggle = document.getElementById("notifytoggle");
const $statsbtn = document.getElementById("statsbtn");
const $sessact = document.getElementById("sessact");   // the header action bar
const $modal = document.getElementById("modal");
const $accounts = document.getElementById("accounts");

const S = {
  sessions: [],          // last global snapshot
  usageRows: [],         // current typed usage rows from the global snapshot
  stats: null,           // last /api/insights snapshot; refetched on show
  statsWindow: "last_seven_days", // selected Pulse period toggle
  currentSessionId: null,
  sessionView: null,             // per-session state {es, lastId, stream, stats, agents, costs, meta, timer}
  esGlobal: null,
  globalApplication: null,
  folds: new Set(),      // open parked/archived subdivisions ("<workingDirectory>|parked") —
                         // survives the list re-renders SSE snapshots trigger
  jump: null,            // pending jump-to-new-session watch ({workingDirectory, resumeSid,
                         // win, show, quiet, armedAt, known, liveAtArm, until}
                         // — armJump; quiet = user navigated away mid-wait, so
                         // resolution toasts instead of yanking)
  jumpDone: null,        // "#/s/<sessionId>" of a QUIETLY resolved launch — lets a
                         // return to #/launching forward to the session that
                         // arrived while the user was away (consumed once)
  pendingUI: false,      // the #/launching "starting session…" view is mounted
                         // (renderList must not clobber it — same role as
                         // S.currentSessionId for the session view)
  cards: new Map(),      // sessionId -> mounted list-card element (the patch targets)
  rowPrev: new Map(),    // sessionId -> JSON of the row the mounted card shows
  listKey: null,         // listShape() of the last full list render
  armClose: null,        // {sessionId, until} — the one armed card-✕ confirm; a
                         // DEADLINE held here (not in the button) so it
                         // survives the per-tick card rebuilds (patchCards)
  closing: new Set(),    // sids with a close POST in flight (card ✕ disabled)
  closePend: {},         // sessionId -> optPending handle for a close in flight (the
                         // web-hint lifecycle + reconcile). MUST be an object:
                         // reconcileCloses does Object.keys(S.closePend) on every
                         // sessions tick and closeBegin does S.closePend[sessionId]=…
                         // — an undefined here threw a TypeError BEFORE closeSession
                         // ran, so /stop never fired (THE "still not closing" bug,
                         // caught by the js.error frontend-audit row at app.js:878).
                         // Mutated ONLY via closeBegin/closeSettle below; every
                         // other site reads it
  hidden: {},            // {group_key: hidden_at_epoch} — directories the ✕
                         // hid from the list (global application preferences).
                         // A group stays hidden only while it has no session
                         // started AFTER hidden_at, so a new session there
                         // re-shows it (groupSessions filters, dirHidden)
  nsPrefs: {},           // the new-session form's last-used {workingDirectory, model, effort}
                         // — the global application preferences cache, so
                         // nsLast() reads it synchronously
  hosts: null,           // the HOST vocabulary (GET /api/hosts, fetched at
                         // boot): one row per registered tool with its menus,
                         // defaults, match rule and account/attach flags — the
                         // new-session form is built entirely out of it, and
                         // null means "not yet known", never "assume Claude"
                         // (docs/dashboard.md, *Tool picker*)
  nsDrafts: {},          // its UNSENT first prompts, {workingDirectory: {text, sequence}} — one
                         // per directory, cached so openNewSession and a directory switch
                         // seed the box synchronously; an accidental close must
                         // not lose a half-typed prompt, and two projects must
                         // not share one (docs/dashboard.md, *New-session
                         // draft*)
};

const ARCHIVE_S = 3 * 86400;   // sessions older than this fold into "archived"
const ARM_MS = 4000;   // two-step-confirm window (card ✕ / header ✕ / compact)
// A just-launched session's terminal pane isn't tagged claude_session=<sessionId> for a
// moment, so /api/session reports live:true with a blank terminal_window_id — the
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

// The SERVER's numbers that the page has to agree with, carried by
// the global application snapshot — the owners are
// dashboard/config.py (upload/rename caps) and dashboard/notify/presence.py
// (the presence TTL), and this is the one place the page keeps them:
//
//   upload_max  — post_upload's cap; the attach path rejects a bigger file
//                 CLIENT-side so you get a named toast instead of a 413.
//   rename_max  — the rename input's maxLength (the server cleans + caps too).
//   view_ttl_s  — how long a /api/presence beat keeps a session "watched"; the
//                 heartbeat cadence is DERIVED from it (viewBeatMs).
//
const LIMITS = {
  upload_max: null,
  rename_max: null,
  view_ttl_s: null,
};

// The in-flight state of an optimistic close, in ONE place. Two maps have to
// move together — S.closing (the greyed card / disabled ✕) and S.closePend (the
// optPending web-hint handle) — and the handle must settle EXACTLY once: leak it
// and its 20s watchdog beacons a bogus `stale` row (the stuck-greyed-state bug
// signal) for a close that in fact resolved. Three call sites hand-rolled that
// pairing — the card ✕ (app.04-list.js), the header ✕ (app.11-chrome.js) and
// reconcileCloses — in two files, which is one edit away from a map that keeps a
// sessionId the other dropped (a ✕ disabled forever, or a card stuck grey).
function closeBegin(sessionId) {
  S.closing.add(sessionId);
  S.closePend[sessionId] = optPending(sessionId, "close");
}

// End it: `phase` is the web-hint lifecycle transition — "reconciled" (the
// sessions snapshot shows the tab actually parked) or "dropped" (the POST
// failed, and the caller reverts its own button/card chrome). Safe to call for a
// sessionId with nothing in flight (a close begun in a previous page load).
function closeSettle(sessionId, phase, extra) {
  S.closing.delete(sessionId);
  const pend = S.closePend[sessionId];
  if (!pend) return;
  delete S.closePend[sessionId];
  pend.settle(phase, extra);
}

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

// The two repeated row/grid appenders. Each was an identical inline closure in
// two builders, and the pair is exactly the kind of markup shape that drifts
// when only one copy gets a tweak.
// chipAdder: one SCOREBOARD chip into `row` — `<span>label <span class=cls|v>
// value</span></span>`. The session scoreboard and the drilled-in agent
// scoreboard build the same chip into their own row.
function chipAdder(row) {
  return (label, value, cls) => {
    const s = el("span");
    if (label) s.append(tnode(label + " "));
    s.append(el("span", cls || "v", value));
    row.append(s);
  };
}
// metaAdder: one key/value pair into an `mmeta` grid, SKIPPING empty values (so
// a builder can list every field it might have and absent ones just vanish) —
// the monitor and background-job detail cards.
function metaAdder(grid) {
  return (k, v) => {
    if (v == null || v === "") return;
    grid.append(el("span", "mk", k), el("span", "mv", String(v)));
  };
}

/* Two-step confirm on a button: the first click ARMS it (label swaps to `ask`,
   .arm styling) for ARM_MS, a second click inside that window fires, and the
   timeout disarms. Returns disarm, for a caller that has to cancel the arm from
   elsewhere.

   The header's ✕ close and ⊜ compact each hand-rolled this — the same timer
   handle, the same label swap, the same clearTimeout-then-fire — 60 lines apart
   in one function, which is how one of them ends up with a fix the other misses.
   The gesture is one rule ("a misclick here costs you the conversation, so ask
   once"), so it gets one implementation.

   The list card's ✕ deliberately stays out (see cardClose): its arm must survive
   the per-tick card REBUILD, so it keeps a deadline in S rather than a closure —
   a different problem with a different answer, not a third copy of this one. */
function armConfirm(btn, label, ask, fire) {
  let t = null;
  const disarm = () => {
    t = null;
    btn.textContent = label;
    btn.classList.remove("arm");
  };
  btn.onclick = () => {
    if (!t) {
      btn.textContent = ask;
      btn.classList.add("arm");
      t = setTimeout(disarm, ARM_MS);
      return;
    }
    clearTimeout(t);
    disarm();
    fire();
  };
  return disarm;
}

// The compact endpoint label for the frontend audit — the path minus the /api/
// prefix and the (already-separately-logged) sessionId, so `/api/session/<sessionId>/stop`
// → `session/stop`. Purely for readable `web-client` rows.
function apiEp(url) {
  return String(url || "").replace(/^\/?api\//, "")
    .replace(/session\/[^/]+\//, "session/").replace(/\?.*$/, "");
}

// The control-plane write: every POST carries the JSON content type AND the
// custom X-Baqylau header the server's _post_guard demands (both force a
// CORS preflight a cross-origin page can't pass). Resolves to the parsed JSON
// on success, rejects with the server's {error} on a 4xx/5xx.
// opts (optional): { keepalive, timeout, audit, sessionId, auditData }.
//   keepalive — send via the browser's keepalive pool (the sendBeacon infra),
//     which is NOT starved by the page's long-lived SSE EventSource streams. On
//     an HTTP/1.1 origin (this server) the ~6-connections/origin cap is eaten by
//     /api/stream + /api/sessions/<id>/stream — a plain fetch for a control POST can
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
//     request the server may never have seen. `sessionId` scopes the rows; `auditData`
//     adds gesture-specific fields to the begin row. NEVER tag the telemetry
//     endpoints themselves (/clientlog, /hint-audit, /client-fail) — that recurses.
function postJSON(url, body, opts) {
  opts = opts || {};
  const tag = opts.audit;
  const sessionId = opts.sessionId || (tag ? S.currentSessionId : "") || "";
  const t0 = performance.now();
  if (tag) {
    const info = connInfo();
    // es (SSE streams held open at send time) + online are the per-gesture
    // connection facts; the batch's `conn` snapshot carries the rest. No `conn`
    // key here — it would collide with that batch dict server-side.
    clog(sessionId, tag + ".begin", Object.assign(
      { ep: apiEp(url), es: info.es, online: info.online }, opts.auditData || {}));
  }
  const init = {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Baqylau": "1" },
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
        if (!r.ok && d && !d.error && d.reason) d.error = d.reason;
        if (tag) clog(sessionId, r.ok ? tag + ".ok" : tag + ".fail", {
          ms: Math.round(performance.now() - t0), status: r.status,
          kind: r.ok ? undefined : "http",
          error: r.ok ? undefined : (d && d.error) || "" });
        return r.ok ? d : Promise.reject(
          Object.assign({ status: r.status }, d || { error: "request failed" }));
      }),
    // never reached / no response — a transport failure (network, tunnel drop,
    // our own AbortController timeout). THE case the server can't see.
    err => {
      if (tag) clog(sessionId, tag + ".fail", {
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

                              // (EventSource.onerror re-fires each reconnect attempt)

function kfmt(n) {
  n = +n || 0;
  if (n >= 999500) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 1000) return Math.round(n / 1000) + "k";
  return String(n);
}
// The Σ token-breakdown chip — the web twin of core.ops.token_parts(), which is
// the ONE owner of that display on the terminal side (per-site re-encoding of the
// token split is banned there, docs/styleguide.md). Same rule here: the session
// scoreboard and the drilled-in agent scoreboard both show it, and they get their
// four counters under DIFFERENT names from the server (stats `tk_in/tk_out/
// tk_read/tk_create` vs an agent's usage `in/out/cache/create`), so the caller
// maps its fields and this owns the arithmetic + the wording. Nothing is added
// when nothing has been counted yet. `add` is a chipAdder.
function sigmaChip(add, t) {
  const tin = t.in | 0, tout = t.out | 0, tread = t.cache | 0, tcre = t.create | 0;
  const tot = tin + tout + tread + tcre;
  if (!tot) return;
  add("Σ", kfmt(tot) + " (" + kfmt(tin) + " in · " + kfmt(tout) + " out · "
      + kfmt(tread) + " cache · " + kfmt(tcre) + " write)");
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
function sessionId(row) { return row.session.session_id; }
function sessionWorkingDirectory(row) { return row.session.working_directory || ""; }
function sessionWindowId(row) { return row.terminal.window_id || ""; }
function sessionIsLive(row) { return !!row.terminal.window_id; }
function sessionIsParked(row) { return row.session.state === "finished"; }
function sessionTabState(row) { return row.tab_state || ""; }
function lastActive(row) {
  return row.session.finished_at || row.session.started_at || 0;
}
function orderKey(row) { return row.session.started_at || lastActive(row); }
function groupKey(row) { return row.project_directory; }
function directoryName(path) {
  return String(path || "").split("/").filter(Boolean).pop() || "";
}
function proj(row) {
  return directoryName(sessionWorkingDirectory(row)) || sessionId(row).slice(0, 18);
}
/* The `?agent=<id>` suffix every scoped read carries — "" outside agent scope,
   so an unscoped URL is byte-identical to what it was. `sep` is the separator
   this call site needs ("?" for a bare path, "&" after an existing query). */
function agentQ(sep) {
  const a = (S.sessionView && S.sessionView.agent) || "";
  return a ? (sep || "?") + "agent=" + encodeURIComponent(a) : "";
}

function shortSid(sessionId) { return (sessionId || "").length > 20 ? sessionId.slice(0, 8) + "…" + sessionId.slice(-4) : sessionId; }
// A model id in its host's DISPLAY spelling — now a pass-through, because the
// SERVER does the shortening: every payload that carries a model id carries the
// owning host's own spelling of it beside it (`model_short` on ctx and the
// resume rows, `from_short`/`to_short` on the fallback record; an agent row's
// `model` is already short), resolved through HostControl.model_short by the
// host that owns the file the id came out of.
//
// What was here was the grammar of TWO hosts, branched inline: strip "claude-",
// join short numeric version parts with ".", skip 8-digit date suffixes, drop
// "[1m]" — but return a `gpt-`/`gpt5` id untouched, because that same parse
// turns "gpt-5.6-terra" into a useless "gpt". A model id carries no reliable
// ownership claim, so the third host's ids would have been read through
// whichever branch their spelling happened to fall in. The function survives as
// the one place the page COERCES a served display string (null/number → ""), so
// call sites keep reading the same.
function shortModel(m) { return String(m || "").trim(); }
function copySid(sessionId) {
  // navigator.clipboard is undefined in a NON-secure context (a plain-http
  // remote tunnel); calling .writeText on it throws synchronously. 127.0.0.1 is
  // a secure context, so localhost is unaffected — this only guards the remote
  // http case (docs/remote.md) from an uncaught TypeError.
  if (!navigator.clipboard) return toast("ask", "copy failed", "needs https");
  navigator.clipboard.writeText(sessionId).then(
    () => toast("done", "copied session id", sessionId),
    () => toast("ask", "copy failed", "clipboard permission?"));
}

// Does a DELIVERED transcript prompt carry what the composer sent? The one
// match rule behind both reconcilers — drainQueue (the ⧗ queued chips) and
// drainPending (the greyed optimistic bubbles). Deliberate twin of the server's
// dashboard/read/session.chip_delivered (which reconciles the persisted chips
// against the transcript), since JS can't import it — keep the two in step.
//
// A SUFFIX match, not exact: what we sent can arrive with anything prepended,
// and both known prefixes are real — attachments prepend `@path` mentions +
// "\n", and text ALREADY IN THE TUI INPUT BOX is glued on with NO separator (a
// terminal-side Escape can hand the previous message back there and the
// page can't know, so the paste lands after it: `testing` + the sent text
// arrived as ONE prompt and the old "\n"-only tolerance missed, pinning the chip
// forever — session bdeca061, 2026-07-25). Empty `sent` never matches (it would
// match every prompt).
function promptMatches(real, sent) {
  return !!sent && (real || "").endsWith(sent);
}

const TAB_LABEL = {
  "": "no tab", "idle": "idle", "thinking": "busy", "working": "busy",
  "executing": "running", "awaiting_background": "running",
  "awaiting_attention": "asking you", "awaiting_response": "your turn",
};

/* The "running now" ribbon: glyph + short label per live `live`-table slot kind
   (sessionapi.running() — fg command, bg jobs, monitors, streaming agents). */
const RUN_APPEARANCE = {
  operation: ["⚙", "fg"],
  background: ["◷", "bg"],
  monitor: ["◉", "monitor"],
};
const RUN_ORDER = ["operation", "background", "monitor"];

/* ---------- toasts + OS notifications ---------- */

// How long a toast stays up. Long by web-notification standards on purpose: a
// red/green transition toast is the thing you may be across the room from, and
// re-firing it is impossible (the tab-diff already consumed the transition).
const TOAST_MS = 7000;

function toast(kind, t1, t2, onclick) {
  const n = el("div", "toast " + (kind || ""));
  n.append(el("div", "t1", t1));
  if (t2) n.append(el("div", "t2", t2));
  n.onclick = () => { n.remove(); onclick && onclick(); };
  $toasts.append(n);
  setTimeout(() => n.remove(), TOAST_MS);
}
