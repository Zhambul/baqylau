"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.

/* ---------- the two streams the page lives on --------------------------------

   The list is fed by SessionData: `GET /sessionData` once, then
   `/sessionData/stream` for whatever changes. What the PAGE owns — the launch
   form, the hidden directories, the notification toggle, the fuel gauges — is a
   different thing from what a harness reported, so it comes from its own route
   (`GET /api/application`) and is re-read when the page is brought back rather
   than pushed on a stream.

   No polling anywhere: the stream carries a cursor, and a reconnect resumes from
   it. */

function loadApplicationPreferences() {
  return fetch("/api/application")
    .then(response => response.json())
    .then(applyApplicationPreferences)
    .catch(error => failLoudly("", "preferences.load.fail", { error: String(error) }));
}

function applyApplicationPreferences(snapshot) {
  const previous = S.globalApplication;
  const preferences = snapshot.preferences || {};
  const newSession = preferences.new_session || {};
  const limits = preferences.limits || {};
  const notifications = snapshot.notifications || {};
  const previousNotifications = (previous && previous.notifications) || {};
  S.nsPrefs = {
    workingDirectory: newSession.working_directory || "",
    tool: newSession.harness || "",
    model: newSession.model || "",
    effort: newSession.effort || "",
  };
  S.nsDrafts = {};
  for (const draft of preferences.new_session_drafts || [])
    S.nsDrafts[draft.working_directory] = {
      text: draft.text,
      sequence: draft.sequence,
    };
  S.hidden = preferences.hidden_directories || {};
  LIMITS.upload_max = limits.upload_bytes;
  LIMITS.rename_max = limits.rename_characters;
  LIMITS.view_ttl_s = limits.presence_seconds;
  if (typeof armBeat === "function") armBeat();
  S.usageRows = snapshot.usage_rows || [];
  renderAccounts(S.usageRows);
  notifyOn = notifications.enabled;
  if (typeof paintNotify === "function") paintNotify();
  if (previous && notifications.latest
      && (!previousNotifications.latest
          || previousNotifications.latest.revision !== notifications.latest.revision)) {
    announceNotice(notifications.latest);
  }
  S.globalApplication = snapshot;
  if (!S.currentSessionId) renderList(true);
}

// A notice the notifier published while this page was open. Only the device you
// are LOOKING AT shows the immediate in-page toast.
function announceNotice(notice) {
  const visible = document.visibilityState === "visible";
  const focused = !document.hasFocus || document.hasFocus();
  const shown = visible && focused;
  clog(notice.session_id || "", "notify.recv",
       { kind: notice.kind, shown: shown, vis: visible, focus: focused });
  const asking = notice.kind === "asking";
  const heading = (notice.project || notice.session_id) + (asking ? " needs you" : " is done");
  const detail = notice.title || (asking ? "a question is waiting" : "finished — your turn");
  if (shown) {
    toast(asking ? "ask" : "done", heading, detail,
          () => { location.hash = "#/s/" + notice.session_id; });
  }
}

function connectGlobal() {
  loadApplicationPreferences();
  loadSessionDataList().then(openGlobalStream);
}

// Opened only once `/sessionData` has answered, and only from the cursor that
// answer reports: the list and the stream share ONE high-water mark, so the
// stream carries just what committed after the list the client already
// holds. Opened from 0 before the list lands, the stream's first frame
// carries the WHOLE backlog and can beat the list reply — every session in it
// then looks unknown to the client and adoptStreamedSession fires once per
// session (a GET /sessionData/<id> burst on every page load, when there was
// only ever one active session). adoptStreamedSession itself stays the right
// path for a session genuinely born after this point.
function openGlobalStream(afterCursor) {
  const es = new EventSource("/sessionData/stream?after_cursor=" + (afterCursor || 0));
  S.esGlobal = es;
  es.onopen = () => { $conn.dataset.on = "1"; sseMark("global", true); };
  es.onerror = () => { $conn.dataset.on = "0"; sseMark("global", false); };
  es.addEventListener("sessionData", (event) => {
    applySessionDataDelta(JSON.parse(event.data) || {});
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

// Resolves to the list's cursor, so `connectGlobal` can open the stream from
// it. Also used to just refresh the rows (showList, refreshWhenVisible), which
// ignore the resolved value. A failed read is reported loudly rather than
// retried here — a caller waiting on the cursor still gets `undefined`
// (`openGlobalStream` then opens from 0, and adoptStreamedSession recovers the
// rows one at a time, the same path a genuinely new session takes).
function loadSessionDataList() {
  return fetch("/sessionData")
    .then(response => response.json())
    .then(list => {
      applyCanonicalSessions(list.sessions);
      return list.cursor;
    })
    .catch(error => failLoudly("", "sessions.load.fail", { error: String(error) }));
}

/* One frame of the global stream: the aggregate rows that changed, across every
   session. Rows and not whole sessions — a session whose one actor changed does
   not re-send the other nine — so this merges by id.

   `live` and `repository` are NOT in a frame: they are what the world looked
   like when a route measured it, and no stored fact changes them. They refresh
   on a full list read, which is why `refreshWhenVisible` exists below. */
function applySessionDataDelta(frame) {
  const rows = S.sessions.slice();
  const index = new Map(rows.map((row, position) => [row.session.session_id, position]));
  for (const session of frame.sessions || []) {
    const position = index.get(session.session_id);
    // A session born after this page loaded — a launch, or a resume. The
    // frame carries its facts, but `live` and `repository` are read-time
    // fields only a route measures, so the ONE new session is read by id.
    // This is the designed arrival path, not a recovery.
    if (position === undefined) { adoptStreamedSession(session.session_id); continue; }
    rows[position] = Object.assign({}, rows[position], { session: session });
  }
  for (const actor of frame.actors || []) {
    const position = index.get(actor.session_id);
    if (position === undefined) {
      // The actor's session is being adopted right now (its row arrives with
      // every actor included), or it rode in the same frame as its session's
      // birth. Anything else means the stream and the list disagree — say so.
      // NEVER silently re-read the world here: that fallback hid a broken
      // frame shape (actors without session_id) for weeks.
      if (_adoptingSessions.has(actor.session_id)) continue;
      if ((frame.sessions || []).some(s => s.session_id === actor.session_id)) continue;
      failLoudly(actor.session_id, "stream.actor.unknown_session",
                 { actor_id: actor.actor_id });
      continue;
    }
    const row = rows[position];
    const actors = (row.actors || []).slice();
    const existing = actors.findIndex(known => known.actor_id === actor.actor_id);
    if (existing >= 0) actors[existing] = actor;
    else actors.push(actor);
    rows[position] = Object.assign({}, row, { actors: actors });
  }
  applyCanonicalSessions(rows);
}

// One in-flight single-session read per newly streamed session. The set keeps
// a burst of frames from starting duplicate reads while the first is in flight.
const _adoptingSessions = new Set();
function adoptStreamedSession(sessionId) {
  if (_adoptingSessions.has(sessionId)) return;
  _adoptingSessions.add(sessionId);
  fetch("/sessionData/" + encodeURIComponent(sessionId))
    .then(response => {
      if (!response.ok) throw new Error("session data " + response.status);
      return response.json();
    })
    .then(row => {
      _adoptingSessions.delete(sessionId);
      if (S.sessions.some(known => known.session.session_id === sessionId)) return;
      applyCanonicalSessions(S.sessions.concat([row]));
    })
    .catch(error => {
      _adoptingSessions.delete(sessionId);
      failLoudly(sessionId, "stream.adopt.fail", { error: String(error) });
    });
}

// The read-time fields go stale while the page is hidden — a terminal tab closed
// behind your back leaves a row claiming to be live. Coming back re-reads them.
// Not a poll: it fires when you look, and not otherwise.
function refreshWhenVisible() {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    loadApplicationPreferences();
    loadSessionDataList();
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
    // survives a reload. A monitor/job detail
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
