"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.

function initNotifBtn() {
  if (!("Notification" in window)) return;
  if (Notification.permission === "default") {
    $notifbtn.hidden = false;
    $notifbtn.onclick = () =>
      Notification.requestPermission().then((perm) => {
        $notifbtn.hidden = true;
        // the same grant opts this device into on-device Web Push (the only
        // path that reaches an iPad when the app is closed) — the request had
        // to come from this user gesture on iOS anyway.
        if (perm === "granted") ensureSubscribed();
      });
  }
}

/* ---------- web push (on-device notifications, esp. the iPad home-screen app) ---
   The in-page toast only fires while a page is OPEN and focused — useless for
   the main case, an installed iPad app that's closed when a session needs you.
   Real system notifications there require Web Push: a service worker the SERVER
   can wake, targeted at the device you most recently used (see *Device routing*)
   (dashboard/webpush.py sends; this registers the worker + manages the
   subscription). iOS exposes Notification/PushManager ONLY in an installed
   standalone app, so on a plain Safari tab this all no-ops. docs/dashboard.md
   *Web push*. */
let swReg = null;

function urlB64ToUint8(b64) {
  // a VAPID public key arrives as pad-stripped base64url; PushManager's
  // applicationServerKey wants raw bytes.
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const s = (b64 + pad).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(s);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function ensureSubscribed() {
  if (!swReg || !("Notification" in window) || Notification.permission !== "granted") return;
  let cfg;
  try {
    cfg = await fetch("/api/application/push-configuration").then((r) => r.json());
  } catch (_) { return; }
  if (!cfg || !cfg.enabled || !cfg.key) return;      // feature off / no server key
  try {
    let sub = await swReg.pushManager.getSubscription();
    if (!sub) {
      sub = await swReg.pushManager.subscribe({
        userVisibleOnly: true,                       // required on iOS/Chrome
        applicationServerKey: urlB64ToUint8(cfg.key),
      });
    }
    await postJSON("/api/application/push-subscriptions",
                   { subscription: sub.toJSON(), device_id: DEVICE_ID,
                     device_label: DEVICE_LABEL },
                   { audit: "push-sub" });
  } catch (e) {
    clog("", "push.fail", { error: String((e && e.message) || e) });
  }
}

async function initPush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
  try {
    await navigator.serviceWorker.register("/sw.js");
    swReg = await navigator.serviceWorker.ready;
  } catch (_) { return; }
  // a returning device whose permission is already granted: refresh the
  // subscription silently (endpoints rotate, and the server may have restarted
  // with a fresh subscription store) — no button, no gesture needed.
  if ("Notification" in window && Notification.permission === "granted") ensureSubscribed();
  // re-arm the stale-banner sweep every time the app goes away, so the next
  // foreground visit runs exactly one (renderAttention fires it — see sweepStale).
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") sweepArmed = true;
  });
}

/* ---------- stale-banner sweep --------------------------------------------
   The client half of retraction (docs/dashboard.md, *Alert retraction*). The
   server takes an alert back by pushing a resolve to the service worker, but
   that push can be refused, dropped, or switched off (BAQYLAU_DASHBOARD_RESOLVE_PUSH),
   and the handles it needs live only in the running server's memory — a restart
   forgets them. So the page also clears up after itself: on coming to the
   foreground it closes every banner whose session no longer needs you.

   Once per foreground visit, and only from renderAttention — i.e. only with a
   REAL sessions snapshot in hand. Sweeping off a boot-empty S.sessions would
   read "nothing needs you" and close banners that are still true. */
let sweepArmed = true;

async function sweepStale() {
  if (!swReg || !swReg.getNotifications) return;
  const needs = new Set(needsYouRows().map(sessionId));
  let ns;
  try { ns = await swReg.getNotifications(); } catch (_) { return; }
  // A banner with no sessionId can't be attributed to a session — that's either a
  // legacy alert or the placeholder WebKit substitutes for a push that showed
  // nothing. Both are ours and both are stale, so both go.
  for (const n of ns) {
    const sessionId = (n.data && n.data.session_id) || "";
    if (!needs.has(sessionId)) n.close();
  }
  updateBadge();
}

/* ---------- installed-app polish (badge · wake lock) -------------------------
   Extras that only make sense for the home-screen app (docs/dashboard.md
   *Installed-app polish*). All feature-detected — a plain browser tab silently
   gets none, and nothing here needs a standalone-mode test. */

// The app-icon badge = how many LIVE sessions need you (red asking + green
// done) — the glanceable count without opening the app. Rides the same
// sessions snapshot the attention strip does (updateBadge is called from
// renderAttention), and the push service worker sets it while the app is
// closed. Cleared to nothing at 0 so the icon has no stray dot.
function needsYouRows(sessions) {
  return (sessions || S.sessions || []).filter(
    row => sessionIsLive(row)
      && (sessionTabState(row) === "awaiting_attention"
          || sessionTabState(row) === "awaiting_response"));
}
function needsYouCount(sessions) {
  return needsYouRows(sessions).length;
}
function updateBadge(sessions) {
  if (!("setAppBadge" in navigator)) return;
  const n = needsYouCount(sessions);
  try { n ? navigator.setAppBadge(n) : navigator.clearAppBadge(); } catch (_) { /* best-effort */ }
}

// Screen Wake Lock: keep the iPad awake while you watch a run (the ☀ header
// button). The lock auto-releases when the tab hides, so re-acquire it on
// re-show while the toggle is ON. Pure client state — no persistence, no audit.
let wakeLock = null;
let wakeWanted = false;
async function acquireWake() {
  if (!("wakeLock" in navigator) || !wakeWanted || wakeLock) return;
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    wakeLock.addEventListener("release", () => { wakeLock = null; });
  } catch (_) { /* denied / not visible — retried on next visibility */ }
}
async function toggleWake() {
  wakeWanted = !wakeWanted;
  if (wakeWanted) { await acquireWake(); }
  else if (wakeLock) { try { await wakeLock.release(); } catch (_) {} wakeLock = null; }
  const b = document.getElementById("wakebtn");
  if (b) { b.classList.toggle("on", wakeWanted); b.title = wakeWanted ? "screen stays awake" : "keep screen awake"; }
}
function initWakeBtn() {
  const b = document.getElementById("wakebtn");
  if (!b || !("wakeLock" in navigator)) return;   // unsupported → stays hidden
  b.hidden = false;
  b.onclick = toggleWake;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") acquireWake();   // re-arm after hide
  });
}

// There is deliberately NO in-app back button. A standalone app has no browser
// chrome, but the header BRAND is already a link to `#/` — a ‹ next to it did
// the same one thing (leave the session view for the list) in a second place,
// so it went. The router is a hash SPA, so the platform back gesture (and
// history.back()) still works wherever the OS offers one.

/* ---------- persistent session strip ---------- */
// The standing complement to the transient toasts: a slim bar under the header,
// on every view, listing EVERY live session as a jump pill — needs-you states
// lead (asking red + pulse, your-turn green), then busy (magenta), running
// (blue), idle (grey, quietest) — hidden entirely when nothing is live. Inside
// a session view it doubles as the chat switcher. Fed from the global
// S.sessions snapshots the app already holds, plus the open session's `tab`
// SSE event (which patches its row in place so the bar reacts before the next
// snapshot). Within a state group pills sort by label+sessionId, NOT recency: the
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
  + "<circle contextWindow='100' cy='100' r='82' fill='none' stroke='#E9B949' stroke-width='8'/>"
  + "<g fill='#E9B949'>"
  + "<circle contextWindow='100' cy='18' r='9'/><circle contextWindow='157.98' cy='42.02' r='9'/>"
  + "<circle contextWindow='182' cy='100' r='9'/><circle contextWindow='157.98' cy='157.98' r='9'/>"
  + "<circle contextWindow='100' cy='182' r='9'/><circle contextWindow='42.02' cy='157.98' r='9'/>"
  + "<circle contextWindow='18' cy='100' r='9'/><circle contextWindow='42.02' cy='42.02' r='9'/>"
  + "</g>"
  + "<circle contextWindow='100' cy='100' r='16' fill='none' stroke='#9aa7b0' stroke-width='6'/>"
  + "<circle contextWindow='100' cy='100' r='8' fill='#E9B949'/>";
const favData = (extra) =>
  "data:image/svg+xml," + encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'>"
    + FAV_GLYPH + (extra || "") + "</svg>");
const FAVICON = favData("");
const FAVICON_ASK = favData("<circle contextWindow='168' cy='32' r='30' fill='#e06c75'/>");

// tab state → pill class (the dot/ring color, mirroring the kitty tab
// palette) + its needs-you-first sort rank. Anything unmapped — idle, or ""
// for a tabless headless/daemon session — is the grey idle pill.
const ATTN_CLASS = {
  "awaiting_attention": "ask", "awaiting_response": "done",
  "thinking": "busy", "working": "busy",
  "executing": "run", "awaiting_background": "run",
};
const ATTN_RANK = { ask: 0, done: 1, busy: 2, run: 3, idle: 4 };

function attnPill(row) {
  const cls = ATTN_CLASS[sessionTabState(row)] || "idle";
  const a = el("a", "attn-pill " + cls + (sessionId(row) === S.currentSessionId ? " self" : ""));
  a.href = "#/s/" + encodeURIComponent(sessionId(row));
  a.append(el("span", "adot"));
  a.append(el("span", "alabel", row.session.title || proj(row)));
  a.title = (TAB_LABEL[sessionTabState(row)] || sessionTabState(row) || "no tab") + " · " + sessionId(row);
  return a;
}

function renderAttention() {
  if (!$attn) return;
  const live = S.sessions.filter(sessionIsLive);
  live.sort((a, b) =>
    ATTN_RANK[ATTN_CLASS[sessionTabState(a)] || "idle"]
      - ATTN_RANK[ATTN_CLASS[sessionTabState(b)] || "idle"]
    || (a.session.title || proj(a)).localeCompare(b.session.title || proj(b))
    || (sessionId(a) < sessionId(b) ? -1 : 1));
  const asking = live.filter(
    row => sessionTabState(row) === "awaiting_attention").length;
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
  updateBadge();   // app-icon badge = red+green needs-you count (installed app)
  // one stale-banner sweep per foreground visit, now that a real snapshot is in
  if (sweepArmed && document.visibilityState === "visible") {
    sweepArmed = false;
    sweepStale();
  }
}

/* ---------- account usage strip (top of every page) ---------- */
// A slim strip under the header showing each subscription account's latest
// 5-hour / 7-day rate-limit usage from the typed global application snapshot.
// Hidden entirely when no account has any usage captured yet.
// The default account is labeled "default"; others by their switcher label
// (c2 · claude-01).

// The window keys of a Claude usage snapshot DICT, in the server's serve order
// (the account-wide 5h/7d pair first, then model-scoped windows like
// seven_day_fable — plugins/claude_code/usage.usage_windows owns that rule; the
// served dict is already built in that order and JSON preserves it): numeric
// used-%, never the ts stamp or a *_reset sibling. Read by the new-session
// ACCOUNT PICKER, which works off that per-account snapshot; the usage STRIP
// reads the served `windows` list instead — one vocabulary, every host.
// The new-session picker's weekly-quota PERISHABILITY (higher = burn first).
// SERVER-computed (core/sessionapi.sched_score, the single owner of the
// scheduling arithmetic); missing → 0 (no snapshot / no urgency).
function schedScore(a) {
  const score = Number(a.scheduling_score);
  return Number.isFinite(score) ? score : 0;
}

// The limit-hit chip/marker text: "fable limit hit" for a model-scoped stamp
// (limit_hit.model, parsed server-side by relimit.limit_model), "limit hit"
// for an account-wide one.
function limitLabel(hit) {
  return (hit.model_id ? hit.model_id + " " : "") + "limit hit";
}

// The account name as shown: "c2 · claude-01" (the default account has no slug).
function acctName(a) {
  return a.account_id ? a.account_id + " · " + a.display_name : a.display_name;
}

// A window's COLUMN SLOT — its DURATION in minutes, which is what the whole
// strip is laid out by, so codex's weekly bar lands directly under Claude's
// (docs/dashboard.md *Row alignment*). It cannot be the `key`: the same 10080
// minutes is `seven_day` to Claude and `w10080` to codex, and keying on that
// gave the two hosts two different columns for one duration. A window with no
// readable duration can share a column with nothing, so it stands on its key.
function winSlot(w) {
  return typeof w.duration_minutes === "number" && w.duration_minutes > 0
    ? "m" + w.duration_minutes : "k" + w.key;
}
function slotMins(slot) {
  return slot[0] === "m" ? parseInt(slot.slice(1), 10) : Number.MAX_SAFE_INTEGER;
}

// One row's windows bucketed by slot, in SERVED order. A host may report several
// windows of one duration — Claude's account-wide `seven_day` plus a per-model
// `seven_day_fable` cap are both 10080 — and they keep the row's own order,
// taking consecutive columns inside that duration's block. So the shared 7d bars
// still line up and the per-model extras hang off the end of the same block,
// which is the "keep today's within-row order, just anchor the shared columns"
// rule.
function rowSlots(a) {
  const m = new Map();
  for (const w of a.windows || []) {
    const k = winSlot(w);
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(w);
  }
  return m;
}

// THE COLUMN LAYOUT, computed once for the WHOLE strip: one column per
// (duration, position-within-that-duration), ordered by duration so the strip
// reads short-window-first whatever order the hosts were served in. Each column
// remembers `hosts` — which hosts report a window there — because that is what
// decides how a row with nothing to put in it renders (acctPill).
function stripColumns(rows) {
  const order = [], depth = new Map(), meta = new Map();
  for (const a of rows) {
    for (const [k, ws] of rowSlots(a)) {
      if (!depth.has(k)) { order.push(k); depth.set(k, 0); meta.set(k, []); }
      depth.set(k, Math.max(depth.get(k), ws.length));
      const at = meta.get(k);
      ws.forEach((w, i) => {
        // the first row to claim a position names the column; with the labels
        // now shared per DURATION (plugins.window_label) the rows agree on it
        if (!at[i]) at[i] = { label: w.label, scope: w.scope, hosts: new Set() };
        at[i].hosts.add(a.harness);
      });
    }
  }
  order.sort((x, y) => slotMins(x) - slotMins(y));
  const cols = [];
  for (const slot of order)
    for (let i = 0; i < depth.get(slot); i++)
      cols.push(Object.assign({ slot, i }, meta.get(slot)[i]));
  return cols;
}

// WHERE each part of a row sits in the strip-wide GRID — 1-based CSS
// `grid-column` values, computed once for the whole strip (docs/dashboard.md
// *Row alignment*):
//
//   1                    the account NAME
//   2                    the ⚠ logged-out badge — only when SOME row carries one
//   …                    one track per duration column (stripColumns), in order
//   last                 the trailing limit-hit chip
//
// The tracks are the same for every row and each cell NAMES the one it goes in,
// which is what makes the strip a stack rather than a coincidence: same-duration
// bars sit in the same track whatever a row's label is, however many windows it
// reports, and whether or not it wraps a badge in front of them. Placing by
// ORDER alone (the previous cut) only aligned the rows while every part before a
// bar happened to be the same WIDTH in each of them — a per-row flex line has no
// notion of a column, so the strip drifted the moment one row's name, badge or
// label measured differently from its neighbour's.
function stripTracks(cols, anyOut) {
  const bar0 = 2 + (anyOut ? 1 : 0);
  return { name: 1, badge: anyOut ? 2 : 0, bar0, tail: bar0 + cols.length,
           n: bar0 + cols.length };
}

// Put a cell in its track. One helper so no call site spells the property.
function place(node, col) {
  node.style.gridColumn = String(col);
  return node;
}

// `cols` is the strip-wide column layout (stripColumns), `tr` its track map
// (stripTracks) and `anyOut` whether ANY row on the strip is logged out — all
// computed once by renderAccounts, so every row places its cells in the same
// tracks and the rows STACK (docs/dashboard.md *Row alignment*). A row missing a
// window from its own host still renders a ghost; a foreign-host-only window
// emits no item because the grid already owns its track. A window whose reset
// rolled over still reserves its reset column; a fine row still reserves the ⚠
// badge's slot.
function acctPill(a, cols, anyOut, tr) {
  const slots = rowSlots(a);
  const pill = el("div", "acct");
  pill.style.gridColumn = "1 / -1";        // the row spans every track (subgrid)
  pill.append(place(el("span", "aname", acctName(a)), tr.name));
  // LOGGED OUT: the account's OAuth login was revoked/expired — a session on it
  // died on error='authentication_failed' (server flag a.logged_out, cleared on
  // the next successful session). Warn outright and up front: the usage bars are
  // stale, and a launch here dies immediately. (docs/dashboard.md.)
  // The badge sits BEFORE the bars, so a healthy row keeps its slot (hidden, not
  // absent) or the one dead account would shove its own bars out of column.
  if (anyOut) {
    const chip = el("span", "uauth" + (a.authentication_error ? "" : " ghost"), "⚠ logged out");
    if (a.authentication_error)
      chip.title = a.authentication_error;
    else chip.setAttribute("aria-hidden", "true");
    pill.append(place(chip, tr.badge));
  }
  if (!(a.windows || []).length) {
    // nothing to lay out in the bar tracks: the notice spans all of them (a
    // one-track cell would leave the row's chip ending at the first column)
    if (!a.authentication_error) {
      const dim = el("span", "adim", "no usage yet");
      dim.style.gridColumn = tr.bar0 + " / -1";
      pill.append(dim);
    }
    return pill;
  }
  // A model-scoped weekly window ("7d fable") resets on the SAME clock as the
  // account-wide `seven_day` bar right above it, so its own "resets in …" was
  // pure duplication — and a model the account hasn't touched has no reset to
  // show at all (effective_usage drops a rolled-over one), so the column read
  // blank exactly where the duplicate would have been. Drop the reset column
  // for those windows entirely: it is dropped for the SAME key on every row,
  // so the stack still aligns (docs/dashboard.md *Row alignment*). Which
  // windows those are is the SERVER's call — the `scope` field of the usage
  // vocabulary (plugins.usage_strip), since only the host knows whether a
  // window is account-wide or a per-model cap under another one.
  const bar = (label, pct, reset, showReset) => {
    const has = typeof pct === "number";       // false → this account has no
    const seg = el("span", "ubar" + (!has ? " ghost"    // snapshot for the window
      : pct >= 90 ? " hot" : pct >= 70 ? " warn" : ""));
    seg.append(el("span", "ulabel", label));
    const track = el("span", "utrack");
    const fill = el("span", "ufill");
    fill.style.width = (has ? Math.max(0, Math.min(100, pct)) : 0) + "%";
    track.append(fill);
    seg.append(track, el("span", "upct", has ? pct + "%" : "—"));
    // The reset column is ALWAYS present, even when the window carries no reset
    // (effective_usage DROPS it once the window has rolled over — an idle
    // account's 5h reads 0% with no reset). Absent, the bar would be 17ch
    // narrower than the other account's and everything after it would slide.
    if (showReset) {
      const box = el("span", "ureset");
      if (has && reset) {
        // dim "resets in" prefix, keep the duration (4h 12m) at full weight
        const txt = resetAgo(reset);        // "in 4h 12m" | "in <1m" | "now"
        const hasIn = txt.startsWith("in ");
        box.append(el("span", "rlbl", hasIn ? "resets in " : "resets "));
        box.append(el("span", "rval", hasIn ? txt.slice(3) : txt));
      }
      seg.append(box);
    }
    return seg;
  };
  // one bar per column of the strip-wide layout (stripColumns): the 5h block,
  // then the 7d block — Claude's account-wide 7d and codex's weekly bar in the
  // SAME column, any model-scoped 7d cap right after it.
  //
  // A row with nothing to put in a column owned by ITS host still renders a
  // ghost: that says the account has no reading for a window a sibling account
  // reports. A column owned only by ANOTHER host is different: the shared grid
  // already reserves and sizes that track from the rows that own it, and every
  // real bar names its exact grid column. Emitting an invisible box there is
  // redundant — and made iPad Safari give the Codex row a second line even
  // though the box painted nothing. So foreign-host columns emit no DOM item.
  //
  // The distinction is:
  //   * a window a SIBLING ROW OF THE SAME HOST reports is a missing READING —
  //     ghost it (label + empty track + "—"): this account has no snapshot;
  //   * a column belonging entirely to ANOTHER host is a window this host does
  //     not HAVE, and "—" would claim a reading it was never going to make. It
  //     emits nothing; the explicit grid placement leaves that track empty.
  //
  // Each bar NAMES its track (place → grid-column), so the column a duration
  // owns is the same one in every row by construction rather than by every
  // preceding cell measuring the same.
  cols.forEach((c, i) => {
    const w = (slots.get(c.slot) || [])[c.i];
    if (!w && !c.hosts.has(a.harness)) return;
    const seg = bar(c.label, w ? Number(w.used_percent) : undefined,
                    w && w.resets_at, c.scope === "account");
    // Separator follows the GRID column, not DOM adjacency. A foreign-host
    // column before this one now emits no sibling, but this bar must keep the
    // same inset/divider as the same column in rows that do fill the earlier
    // track.
    if (i) seg.classList.add("usep");
    pill.append(place(seg, tr.bar0 + i));
  });
  // The account is BLOCKED right now (a session on it died on error=
  // rate_limit — the `limit-hit` stamp, served only while still active):
  // say so outright; the frozen usage bar alone reads ~95% at exactly the
  // moment the account stops working (the status line never reports 100%
  // once requests are rejected — docs/relimit.md).
  //
  // It rides ONE trailing cell (`.utail`) in the last track, chip and reset
  // together: two loose spans would be two cells competing for one column, and
  // giving each its own track would widen the strip for every other row.
  if (a.limit) {
    // model-scoped stamps ("fable limit hit" — only that model is blocked,
    // relimit.limit_model) name the model; account-wide ones stay bare
    const tail = place(el("span", "utail"), tr.tail);
    const chip = el("span", "ulimit", limitLabel(a.limit));
    if (a.limit.message) chip.title = a.limit.message;
    tail.append(chip);
    if (a.limit.resets_at)
      tail.append(el("span", "ureset", "resets " + resetAgo(a.limit.resets_at)));
    pill.append(tail);
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

// The name column's width, in monospace characters — the widest account name
// across the strip (never narrower than the historical 14ch, so a single short
// name doesn't make the bars jump left). Fixed columns are what let the rows
// stack; the font is `--mono`, so `ch` is exact.
const ANAME_MIN_CH = 14;

// THE ONE usage-strip painter, over every host's rows (GET /api/accounts →
// plugins.usage_strip). It knows no host by name.
//
// The whole strip is read as a STACK — c1's 5h bar above c2's above codex's 7d
// — so the columns are decided ONCE for every row, keyed by window DURATION
// (stripColumns / winSlot). Duration, not `key`: Claude calls 10080 minutes
// `seven_day` and codex calls it `w10080`, and while the columns were unioned
// PER HOST those were two separate layouts that only happened to look stacked
// when both hosts reported the same shape. A codex account with no 5h window
// then slid its weekly bar left into Claude's 5h column, which is the report.
//
// Rows stay GROUPED by host in render order (a host's rows belong together, and
// the logged-out badge is a Claude fact), but the columns span the groups. What
// per-host grouping still buys is the missing-reading distinction in acctPill:
// a sibling-host window becomes a ghost, while a foreign-host window is absent.
//
// And the columns are a REAL GRID, not a per-row agreement to emit the same
// boxes in the same order: `#accounts` owns the tracks and every `.acct` is a
// `subgrid` of them, each cell naming the track it goes in (stripTracks/place).
// The ordered-boxes cut before it aligned only while every cell BEFORE a bar
// measured the same in each row — a flex row knows nothing about its neighbour,
// so the name column's `ch` arithmetic, the badge's width and each label's own
// width were three separate ways for one row to start its bars a few pixels off
// its neighbour's, and the strip still read as ragged (the re-report). A track
// is measured across ALL rows at once, so none of those can shift a column
// again, and the rows come out the same WIDTH as a bonus (each spans 1 / -1).
function renderAccounts(list) {
  if (!$accounts) return;
  // show a row with any usage window OR a logged-out warning (a dead account
  // may have no fresh usage, but the ⚠ still needs to surface)
  const shown = (list || []).filter(a => (a.windows || []).length || a.authentication_error);
  $accounts.hidden = !shown.length;
  $accounts.textContent = "";
  // The name column is sized across the WHOLE strip so every host's rows share
  // one left edge — as, now, do their bar columns.
  const nameCh = shown.reduce((n, a) => Math.max(n, acctName(a).length),
                              ANAME_MIN_CH);
  $accounts.style.setProperty("--aname-w", nameCh + "ch");
  const cols = stripColumns(shown);
  // strip-wide too: the ⚠ badge sits BEFORE the bars, so one logged-out account
  // anywhere means EVERY row reserves the slot, or the bars of the rows that
  // don't start a badge-width to the left of the ones that do.
  const anyOut = shown.some(a => a.authentication_error);
  const tr = stripTracks(cols, anyOut);
  // THE tracks, on the container every row subgrids into. All `max-content`:
  // each track is as wide as the widest cell any row puts in it, measured by
  // the layout engine over the whole strip — which is the one measurement that
  // cannot disagree with what is painted (the `ch` arithmetic it replaces was a
  // character COUNT standing in for a width, and a mono font is only exactly
  // that wide for the glyphs it actually covers).
  $accounts.style.setProperty("--acct-tracks",
                              new Array(tr.n).fill("max-content").join(" "));
  const hosts = [];
  for (const a of shown) if (!hosts.includes(a.harness)) hosts.push(a.harness);
  for (const h of hosts)
    for (const a of shown)
      if (a.harness === h) $accounts.append(acctPill(a, cols, anyOut, tr));
}
