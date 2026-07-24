"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.12-init.js for the boot/init sequence.

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
    // Only the device you're LOOKING AT shows the immediate in-page toast; a
    // backgrounded / other device is reached by the server's device-targeted
    // deferred push (+ Telegram escalation), so no more cross-device buzz — the
    // idle iPad no longer pops an OS notification while you work on the Mac
    // (docs/dashboard.md *Device routing*).
    const vis = document.visibilityState === "visible";
    const focus = !document.hasFocus || document.hasFocus();
    const shown = vis && focus;
    // Audit whether THIS device received the toast and whether it showed it —
    // the frontend side of "did device X get notified" (a gated recv explains a
    // missing toast: you weren't looking at this device).
    clog(d.sid || "", "notify.recv",
         { kind: d.kind, shown: shown, vis: vis, focus: focus });
    if (!shown) return;
    const asking = d.kind === "asking";
    const t1 = (d.project || d.sid) + (asking ? " needs you" : " is done");
    const t2 = d.title || (asking ? "Claude is asking a question" : "finished — your turn");
    toast(asking ? "ask" : "done", t1, t2, () => { location.hash = "#/s/" + d.sid; });
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


function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  // hide the c1/c2 account strip once we're inside a particular session
  document.body.classList.toggle("in-session", parts[0] === "s");
  showBack(parts[0] === "s");     // the standalone-app ‹ back button (installed)
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
  if (parts[0] === "stats") { S.pendingUI = false; return showStats(); }
  S.pendingUI = false;
  showList();
}

/* ---------- stats / insights page (GitHub-Insights-inspired) ----------
   All numbers are server-computed (dashboard/server.stats_payload); this only
   renders. Charts are hand-rolled SVG (no chart library, matching micIcon's
   createElementNS idiom) + the CSS bar idiom — contribution heatmap, day×hour
   punch card, per-window Pulse summary, per-project cards. */
const SVGNS = "http://www.w3.org/2000/svg";
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

