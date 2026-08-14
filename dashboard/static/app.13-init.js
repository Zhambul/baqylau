"use strict";
// The boot/init sequence — loaded LAST. Opens with the global event-listener
// registration + boot audit record RELOCATED from the old router section (it
// references route()/clog() which are hoisted across the whole SPA, so it must
// run after every part has loaded), then the original startup calls.

window.addEventListener("hashchange", route);
// Flush the frontend-audit buffer as the tab goes away (navigation, tab close,
// backgrounding) — via sendBeacon here, the one place beacon is the right tool
// and a lost tail is acceptable. Both events fire on mobile Safari where an
// unload alone is unreliable.
window.addEventListener("pagehide", () => flushClog());
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") flushClog();
});
// Uncaught client errors — a handler throwing and leaving NO trace is exactly the
// blind spot this audit closes (a broken render reads as a silent product bug).
// First stack frame is enough to locate it; capped, best-effort.
window.addEventListener("error", (e) => {
  clog(S.currentSessionId || "", "js.error", {
    msg: (e && e.message || "").slice(0, 200), src: apiEp(e && e.filename || ""),
    line: (e && e.lineno) || 0, col: (e && e.colno) || 0 });
});
window.addEventListener("unhandledrejection", (e) => {
  const r = e && e.reason;
  clog(S.currentSessionId || "", "js.reject",
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
  // dlabel maps this device's routing id (the batch-level `device`) to a
  // human platform once per load, so a notify-route `target` is legible.
  dlabel: DEVICE_LABEL,
  online: navigator.onLine !== false,
  w: screen.width, h: screen.height, dpr: window.devicePixelRatio || 1 });


if (/[?&#]vpdiag/.test(location.search + location.hash)) {
  // an orientation flip reports the OLD viewport until the rotate settles, so
  // the overlay re-measures once after it
  const ROTATE_SETTLE_MS = 400;
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
  addEventListener("orientationchange", () => setTimeout(upd, ROTATE_SETTLE_MS));
  if (window.visualViewport) window.visualViewport.addEventListener("resize", upd);
  document.body.append(box);
}

/* ---------- boot ---------- */

initNotifBtn();
initPush();   // register the push service worker + (re)subscribe if already granted
initWakeBtn();   // ☀ keep-screen-awake toggle (installed-app polish)
// …and the HOST vocabulary (/api/hosts): the new-session form's tool picker,
// both option menus, their defaults and the account row are ALL built from it,
// so priming it here means the first form open is fully populated on the frame
// it appears. The form refetches if this hasn't landed, and until it does it
// shows an EMPTY picker rather than a fabricated default host — a launch that
// names no tool is routed to the server's own default anyway.
loadCanonicalHosts().catch(() => {});
// The global application stream seeds sessions, usage, notifications, launch
// preferences, drafts, hidden directories, and limits in one complete snapshot.
connectGlobal();
// A deep link from a Telegram/off-device notification lands as ?s=<sessionId> (a
// query param, NOT a #fragment — Telegram's auto-linker drops the fragment, so
// the sessionId must ride the query). Translate it into the hash route the router
// speaks, and strip ?s= from the URL so a later reload/share carries a clean
// hash link. A pre-existing hash wins (an explicit #/... in the same URL).
(function deepLinkFromQuery() {
  // ?new=1 / ?attn=1 are the manifest `shortcuts` (long-press icon on
  // Android/desktop; iOS ignores them) — land on the list, and for `new` pop
  // the new-session form after the router paints. `?s=<sessionId>` is the notif deep
  // link. Any of them: strip the query so a later reload/share is clean.
  const q = location.search;
  const s = /[?&]s=([^&]+)/.exec(q);
  if (!location.hash && (s || /[?&](new|attn)=1/.test(q)))
    history.replaceState(null, "", location.pathname);
  if (s && !location.hash)
    location.hash = "#/s/" + encodeURIComponent(decodeURIComponent(s[1]));
  else if (/[?&]new=1/.test(q))
    setTimeout(() => openNewSession(""), 0);   // after route() paints the list
})();
route();
renderAttention();
setInterval(() => { if (!S.currentSessionId) renderList(true); }, LIST_REFRESH_MS);

// --- presence heartbeat -------------------------------------------------------
// Tell the server, while the page is VISIBLE + FOCUSED, (a) that THIS DEVICE is
// in use right now (its stable DEVICE_ID), so the on-device notification routes
// to the device you most recently used (docs/dashboard.md *Device routing*),
// and (b) if you're inside a session, that you're LOOKING at it (S.currentSessionId), so the
// deferred alert suppresses while you watch — the web analog of the kitty tab
// being frontmost (*Telegram alerts*). Both ride ONE beat to /api/presence.
// Sent from ANY view (device presence must be recorded even from the list, not
// only a session). hasFocus() rules out a visible-but-unfocused window;
// visibilityState rules out a backgrounded/minimised tab. UN-audited (no `audit`
// tag → no web-client rows; it would flood at this rate) and best-effort.
function presenceBeat() {
  if (document.visibilityState !== "visible") return;
  if (document.hasFocus && !document.hasFocus()) return;
  postJSON("/api/application/presence",
           { device_id: DEVICE_ID, session_id: S.currentSessionId || null })
    .catch(() => {});                              // presence is best-effort
}

// ...and the other half: say so the INSTANT presence ends. A beat means "I was
// here within view_ttl_s", which the alert path has to read as "here now" — but
// the gates above are instant, so from the moment you click into another app
// this page stops toasting while the server keeps suppressing the off-device
// push for up to a full TTL. That window swallowed alerts through no channel at
// all (docs/dashboard.md *Presence ends when the page says so*). Only the page
// knows when it ended, so it reports it: blur and hide, best-effort, and a
// `focus` beat is already wired below to re-establish presence at once.
function presenceAway() {
  postJSON("/api/application/presence",
           { device_id: DEVICE_ID, session_id: S.currentSessionId || null, away: true })
    .catch(() => {});
}
window.addEventListener("blur", presenceAway);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") presenceAway();
});

// The cadence is DERIVED from the server's presence TTL (LIMITS.view_ttl_s, the
// env-overridable BAQYLAU_DASHBOARD_VIEW_TTL_S) rather than a matching literal: a beat
// every TTL/2.5 leaves room for one to be lost/late and still not lapse, which
// is what the alert suppression rests on — a lapsed beat is read as "nobody is
// watching" and fires the off-device alert while you sit looking at the session.
// A fixed 8s beat did that silently for any TTL under ~8s. Floored at 2s so a
// tiny/mis-set knob can't turn the beat into a request loop, and re-armed when
// the fetched limits land (armBeat is idempotent).
const VIEW_BEAT_FLOOR_MS = 2000;
const VIEW_BEAT_SHARE = 2.5;
let beatTimer = null;
let beatMs = 0;
function armBeat() {
  if (LIMITS.view_ttl_s === null) return;
  const ms = Math.max(VIEW_BEAT_FLOOR_MS,
                      Math.round(LIMITS.view_ttl_s * 1000 / VIEW_BEAT_SHARE));
  if (ms === beatMs) return;
  if (beatTimer) clearInterval(beatTimer);
  beatMs = ms;
  beatTimer = setInterval(presenceBeat, ms);
}

// Beat immediately when you (re)focus / reveal the page or open a session, so
// presence is re-established at once rather than up to one interval late.
window.addEventListener("focus", presenceBeat);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") presenceBeat();
});
presenceBeat();
