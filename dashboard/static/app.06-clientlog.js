"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.
//
// THE frontend-audit channel, whole: the CLOG ring, the batched delivery, the
// SSE up/down marks, the connection snapshot every batch carries, and the
// optimistic-action beacons (optAudit / hintAudit / optPending / clientFail)
// that report a client state whose real confirmation arrives async over SSE.
//
// It used to be half a channel. `clog`/`flushClog`/`sseMark` lived here while
// `connInfo` — which `flushClog` calls on every batch — and the four beacons sat
// at the tail of app.05-session.js, 1500 lines into a file about the session
// VIEW; `clog`'s own doc comment was stranded there too, above nothing. So the
// audit module called back into the session module, none of the six functions
// was session code (`pendingCard` is used only by the dialogs, `clientFail` by
// four other files), and the file names lied about where the concern lived.
//
// docs/dashboard.md *Frontend audit (clientlog)*.

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
    // `device` (the stable per-DEVICE id) rides every batch so ANY frontend
    // audit row is attributable to a device — the frontend side of the
    // notification device-routing evidence (docs/dashboard.md *Device routing*).
    const payload = { client: CLIENT_ID, device: DEVICE_ID, conn: connInfo(),
                      events: batch };
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

/* ---------- optimistic-action beacons (the `web-hint` audit) ---------- */
// The page shows several OPTIMISTIC states whose real confirmation arrives async
// over SSE: the composer's greyed stand-in bubble (app.08-composer.js), a
// closing session card (app.10-control.js), a greyed ask/plan card
// (app.07-dialogs.js). Each is client-only, so the SERVER cannot see it — a
// stuck greyed state (shown, never reconciled) leaves no trace by default.
//
// So each transition beacons a `web-hint` audit row (→ POST /hint-audit,
// server-side A.state_file): `shown` on create, `reconciled` on the swap
// (carrying wait_ms — the latency), `dropped` on a failure, and `stale` from a
// watchdog when a stand-in outlives STALE_HINT_MS unreconciled (THE bug
// signal). Audit-only, best-effort, never blocks or toasts.

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
