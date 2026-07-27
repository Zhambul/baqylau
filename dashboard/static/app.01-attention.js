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
  try { cfg = await fetch("/api/push/config").then((r) => r.json()); } catch (_) { return; }
  if (!cfg || !cfg.enabled || !cfg.key) return;      // feature off / no server key
  try {
    let sub = await swReg.pushManager.getSubscription();
    if (!sub) {
      sub = await swReg.pushManager.subscribe({
        userVisibleOnly: true,                       // required on iOS/Chrome
        applicationServerKey: urlB64ToUint8(cfg.key),
      });
    }
    await postJSON("/api/push/subscribe",
                   { subscription: sub.toJSON(), device: DEVICE_ID, label: DEVICE_LABEL },
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
   that push can be refused, dropped, or switched off (CLAUDE_DASH_RESOLVE_PUSH),
   and the handles it needs live only in the running server's memory — a restart
   forgets them. So the page also clears up after itself: on coming to the
   foreground it closes every banner whose session no longer needs you.

   Once per foreground visit, and only from renderAttention — i.e. only with a
   REAL sessions snapshot in hand. Sweeping off a boot-empty S.sessions would
   read "nothing needs you" and close banners that are still true. */
let sweepArmed = true;

async function sweepStale() {
  if (!swReg || !swReg.getNotifications) return;
  const needs = new Set(needsYouRows().map(r => r.sid));
  let ns;
  try { ns = await swReg.getNotifications(); } catch (_) { return; }
  // A banner with no sid can't be attributed to a session — that's either a
  // legacy alert or the placeholder WebKit substitutes for a push that showed
  // nothing. Both are ours and both are stale, so both go.
  for (const n of ns) {
    const sid = (n.data && n.data.sid) || "";
    if (!needs.has(sid)) n.close();
  }
  updateBadge();
}

/* ---------- installed-app polish (badge · wake lock · back) ------------------
   Extras that only make sense for the home-screen app (docs/dashboard.md
   *Installed-app polish*). All feature-detected — a plain browser tab silently
   gets none. IS_STANDALONE gates the ones that assume no browser chrome. */
const IS_STANDALONE =
  (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches)
  || navigator.standalone === true;   // navigator.standalone is the iOS tell

// The app-icon badge = how many LIVE sessions need you (red asking + green
// done) — the glanceable count without opening the app. Rides the same
// sessions snapshot the attention strip does (updateBadge is called from
// renderAttention), and the push service worker sets it while the app is
// closed. Cleared to nothing at 0 so the icon has no stray dot.
function needsYouRows(sessions) {
  return (sessions || S.sessions || []).filter(
    r => r.live && (r.tab === "awaiting-command" || r.tab === "awaiting-response"));
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

// In-app back: a standalone app has no browser back button. The router is a
// hash SPA and every navigation pushes a history entry, so history.back()
// works; showBack() reveals the ‹ only in standalone mode inside a session
// view (updateHeadChrome calls it on every route change).
function initBackBtn() {
  const b = document.getElementById("backbtn");
  if (!b || !IS_STANDALONE) return;
  b.onclick = () => { if (history.length > 1) history.back(); else location.hash = "#/"; };
}
function showBack(inSession) {
  const b = document.getElementById("backbtn");
  if (b && IS_STANDALONE) b.hidden = !inSession;
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
  updateBadge();   // app-icon badge = red+green needs-you count (installed app)
  // one stale-banner sweep per foreground visit, now that a real snapshot is in
  if (sweepArmed && document.visibilityState === "visible") {
    sweepArmed = false;
    sweepStale();
  }
}

/* ---------- account usage strip (top of every page) ---------- */
// A slim strip under the header showing each subscription account's latest
// 5-hour / 7-day rate-limit usage (GET /api/accounts — aggregated per account
// from the status-line capture, docs/dashboard.md). Painted by the boot
// fetch, then kept live by the `accounts` SSE push (app.02-router.js); the
// slow poll below survives as the SSE-down fallback (usage moves slowly, and
// it's ambient). Hidden entirely when no account has any usage captured yet.
// The default account is labeled "default"; others by their switcher label
// (c2 · claude-01).

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

// The account name as shown: "c2 · claude-01" (the default account has no slug).
function acctName(a) {
  return a.slug ? a.slug + " · " + a.label : a.label;
}

// `cols` is the UNION of window keys across every rendered account and `anyOut`
// whether ANY of them is logged out — both computed once by renderAccounts, so
// every row lays out the same columns in the same order and the rows STACK
// (docs/dashboard.md *Row alignment*). A row missing a window still renders it,
// as a ghost; a window whose reset was dropped (rolled over) still reserves its
// reset column; a row that is fine still reserves the ⚠ badge's slot.
function acctPill(a, cols, anyOut) {
  const u = a.usage;
  const pill = el("div", "acct");
  pill.append(el("span", "aname", acctName(a)));
  // LOGGED OUT: the account's OAuth login was revoked/expired — a session on it
  // died on error='authentication_failed' (server flag a.logged_out, cleared on
  // the next successful session). Warn outright and up front: the usage bars are
  // stale, and a launch here dies immediately. (docs/dashboard.md.)
  // The badge sits BEFORE the bars, so a healthy row keeps its slot (hidden, not
  // absent) or the one dead account would shove its own bars out of column.
  if (anyOut) {
    const chip = el("span", "uauth" + (a.logged_out ? "" : " ghost"), "⚠ logged out");
    if (a.logged_out)
      chip.title = a.logged_out_msg || "run /login — the account's login was revoked";
    else chip.setAttribute("aria-hidden", "true");
    pill.append(chip);
  }
  const wins = usageWindows(u);
  if (!wins.length) {
    if (!a.logged_out) pill.append(el("span", "adim", "no usage yet"));
    return pill;
  }
  const bar = (label, pct, resetKey) => {
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
    const reset = has && u && u[resetKey];
    const box = el("span", "ureset");
    if (reset) {
      // dim "resets in" prefix, keep the duration (4h 12m) at full weight
      const txt = resetAgo(reset);          // "in 4h 12m" | "in <1m" | "now"
      const hasIn = txt.startsWith("in ");
      box.append(el("span", "rlbl", hasIn ? "resets in " : "resets "));
      box.append(el("span", "rval", hasIn ? txt.slice(3) : txt));
    }
    seg.append(box);
    return seg;
  };
  // one bar per column — the 5h/7d pair plus any model-scoped window the CLI
  // reports (e.g. "7d fable"), in the served order (renderAccounts' union)
  cols.forEach(k => pill.append(bar(windowLabel(k), u ? u[k] : undefined,
                                    k + "_reset")));
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

// The name column's width, in monospace characters — the widest account name
// across the strip (never narrower than the historical 14ch, so a single short
// name doesn't make the bars jump left). Fixed columns are what let the rows
// stack; the font is `--mono`, so `ch` is exact.
const ANAME_MIN_CH = 14;

function renderAccounts(list) {
  if (!$accounts) return;
  // show an account with a usage snapshot OR a logged-out warning (a dead
  // account may have no fresh usage, but the ⚠ still needs to surface)
  const shown = (list || []).filter(a => a.usage || a.logged_out);
  $accounts.hidden = !shown.length;
  $accounts.textContent = "";
  // The rows are read as a STACK — c1's 5h bar directly above c2's 5h bar — so
  // the column set is decided ONCE for the whole strip, not per row: the union
  // of every account's windows in served order (an account whose model-window
  // fetch didn't match has no `seven_day_fable`, and dropping the column from
  // that row alone would shift nothing on it but everything after it on the
  // others). docs/dashboard.md *Row alignment*.
  const cols = [];
  for (const a of shown)
    for (const k of usageWindows(a.usage)) if (!cols.includes(k)) cols.push(k);
  const anyOut = shown.some(a => a.logged_out);
  const nameCh = shown.reduce((n, a) => Math.max(n, acctName(a).length),
                              ANAME_MIN_CH);
  $accounts.style.setProperty("--aname-w", nameCh + "ch");
  for (const a of shown) $accounts.append(acctPill(a, cols, anyOut));
}
