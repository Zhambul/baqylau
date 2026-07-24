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
  // dlabel maps this device's routing id (the batch-level `device`) to a
  // human platform once per load, so a notify-route `target` is legible.
  dlabel: DEVICE_LABEL,
  online: navigator.onLine !== false,
  w: screen.width, h: screen.height, dpr: window.devicePixelRatio || 1 });


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
initPush();   // register the push service worker + (re)subscribe if already granted
initWakeBtn();   // ☀ keep-screen-awake toggle (installed-app polish)
initBackBtn();   // ‹ in-app back (standalone only)
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
  // ?new=1 / ?attn=1 are the manifest `shortcuts` (long-press icon on
  // Android/desktop; iOS ignores them) — land on the list, and for `new` pop
  // the new-session form after the router paints. `?s=<sid>` is the notif deep
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
refreshAccounts();
setInterval(refreshAccounts, ACCOUNTS_POLL_MS);
setInterval(() => { if (!S.cur) renderList(true); }, LIST_REFRESH_MS);

// --- presence heartbeat -------------------------------------------------------
// Tell the server, while the page is VISIBLE + FOCUSED, (a) that THIS DEVICE is
// in use right now (its stable DEVICE_ID), so the on-device notification routes
// to the device you most recently used (docs/dashboard.md *Device routing*),
// and (b) if you're inside a session, that you're LOOKING at it (S.cur), so the
// deferred alert suppresses while you watch — the web analog of the kitty tab
// being frontmost (*Telegram alerts*). Both ride ONE beat to /api/presence.
// Sent from ANY view (device presence must be recorded even from the list, not
// only a session). hasFocus() rules out a visible-but-unfocused window;
// visibilityState rules out a backgrounded/minimised tab. Cadence is well under
// the server's CLAUDE_DASH_VIEW_TTL_S (20s) so a watched session's presence
// never lapses between beats. UN-audited (no `audit` tag → no web-client rows;
// it would flood at this rate) and best-effort.
const VIEW_HEARTBEAT_MS = 8000;
function presenceBeat() {
  if (document.visibilityState !== "visible") return;
  if (document.hasFocus && !document.hasFocus()) return;
  postJSON("/api/presence", { device: DEVICE_ID, sid: S.cur || "" })
    .catch(() => {});                              // presence is best-effort
}
setInterval(presenceBeat, VIEW_HEARTBEAT_MS);
// Beat immediately when you (re)focus / reveal the page or open a session, so
// presence is re-established at once rather than up to one interval late.
window.addEventListener("focus", presenceBeat);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") presenceBeat();
});
presenceBeat();

