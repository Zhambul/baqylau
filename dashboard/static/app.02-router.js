"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.

// The complete application snapshot owns sessions, usage, preferences and
// notifications. There is no secondary fetch, field event or polling path.
/* ---------- global event stream ---------- */

function connectGlobal() {
  const es = new EventSource("/api/stream");
  S.esGlobal = es;
  es.onopen = () => { $conn.dataset.on = "1"; sseMark("global", true); };
  es.onerror = () => { $conn.dataset.on = "0"; sseMark("global", false); };
  es.addEventListener("application", (event) => {
    const snapshot = JSON.parse(event.data) || {};
    const previous = S.globalApplication;
    const notifications = snapshot.notifications;
    const preferences = snapshot.preferences;
    const newSession = preferences.new_session;
    const limits = preferences.limits;
    const previousNotifications = previous && previous.notifications
      ? previous.notifications : {};
    applyCanonicalSessions(snapshot.sessions);
    S.nsPrefs = {
      workingDirectory: newSession.working_directory || "",
      tool: newSession.harness || "",
      model: newSession.model || "",
      effort: newSession.effort || "",
    };
    S.nsDrafts = {};
    for (const draft of preferences.new_session_drafts)
      S.nsDrafts[draft.working_directory] = {
        text: draft.text,
        sequence: draft.sequence,
      };
    S.hidden = preferences.hidden_directories;
    LIMITS.upload_max = limits.upload_bytes;
    LIMITS.rename_max = limits.rename_characters;
    LIMITS.view_ttl_s = limits.presence_seconds;
    if (typeof armBeat === "function") armBeat();
    S.usageRows = snapshot.usage_rows;
    renderAccounts(S.usageRows);
    notifyOn = notifications.enabled;
    if (typeof paintNotify === "function") paintNotify();
    if (previous && notifications.latest
        && (!previousNotifications.latest
            || previousNotifications.latest.revision !== notifications.latest.revision)) {
      const d = notifications.latest;
      // Only the device you're LOOKING AT shows the immediate in-page toast.
      const vis = document.visibilityState === "visible";
      const focus = !document.hasFocus || document.hasFocus();
      const shown = vis && focus;
      clog(d.session_id || "", "notify.recv",
           { kind: d.kind, shown: shown, vis: vis, focus: focus });
      const asking = d.kind === "asking";
      const t1 = (d.project || d.session_id) + (asking ? " needs you" : " is done");
      const t2 = d.title || (asking ? "a question is waiting" : "finished — your turn");
      if (shown) {
        toast(asking ? "ask" : "done", t1, t2,
              () => { location.hash = "#/s/" + d.session_id; });
      }
    }
    S.globalApplication = snapshot;
    if (!S.currentSessionId) renderList(true);
  });
  // hello carries the server's boot id: the EventSource reconnects on a
  // server restart, and a CHANGED boot id means this open page's JS may be
  // stale (a redeploy happened underneath) — twice a stale open page ran old
  // handlers against a new server and the mismatch read as a product bug.
  es.addEventListener("ready", (e) => {
    const boot = (JSON.parse(e.data) || {}).boot_id;
    if (!S.boot) {
      S.boot = boot;
      // anchor this client to the server build it FIRST connected to — so a later
      // "the page behaved like old code" report is checkable (compare against the
      // stale row below and the boot record's loaded-build)
      clog("", "hello", { boot: boot || "" });
      return;
    }
    if (boot !== S.boot) {
      // The server redeployed under an open page. Old handlers cannot safely
      // operate against the new API; drafts already live on the backend, so
      // reload immediately instead of leaving correctness behind an optional
      // toast that can be missed while the page is hidden.
      clog("", "stale", { was: S.boot || "", now: boot || "" });
      S.boot = boot;
      location.reload();
    }
  });
}

/* ---------- router ---------- */


function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  // hide the c1/c2 account strip once we're inside a particular session
  document.body.classList.toggle("in-session", parts[0] === "s");
  // the header's top-right corner belongs to the open session (its action bar,
  // mounted by renderSessionChrome) — hand it back to the list's own buttons on
  // every other route. Only OFF a session route: a drill-down (…/m/<task>)
  // re-enters showSection without rebuilding the chrome, so clearing here
  // unconditionally would leave the header empty for the rest of the visit.
  // A switch to ANOTHER session re-mounts (mountHeaderActions clears first).
  if (parts[0] !== "s" && typeof clearHeaderActions === "function")
    clearHeaderActions();
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
    const sessionId = decodeURIComponent(parts[1]);
    // AGENT SCOPE: the whole session view re-pointed at one agent, with its own
    // tab in the URL (#/s/<sessionId>/a/<actorId>/<tab>) so a scoped page is linkable and
    // survives a reload (docs/dashboard.md *Agent scope*). A monitor/job detail
    // nests INSIDE it (…/a/<actorId>/m/<task>) — reached unscoped, the task simply
    // isn't in the lead's list.
    if (parts[2] === "a" && parts[3]) {
      const actorId = decodeURIComponent(parts[3]);
      if (parts[4] === "m" && parts[5])
        return showSection("monitors", sessionId, decodeURIComponent(parts[5]), actorId);
      if (parts[4] === "j" && parts[5])
        return showSection("jobs", sessionId, decodeURIComponent(parts[5]), actorId);
      return showSession(sessionId, parts[4] || "mirror", actorId);
    }
    // the two drill-downs route through the one section engine (SECTIONS)
    if (parts[2] === "m" && parts[3])
      return showSection("monitors", sessionId, decodeURIComponent(parts[3]));
    if (parts[2] === "j" && parts[3])
      return showSection("jobs", sessionId, decodeURIComponent(parts[3]));
    return showSession(sessionId, parts[2] || "mirror");
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
   All numbers are server-computed from application insights; this only
   renders. Charts are hand-rolled SVG (no chart library, matching micIcon's
   createElementNS idiom) + the CSS bar idiom — contribution heatmap, day×hour
   punch card, per-window Pulse summary, per-project cards. */
const SVGNS = "http://www.w3.org/2000/svg";
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
