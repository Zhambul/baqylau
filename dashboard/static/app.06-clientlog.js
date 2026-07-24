"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.12-init.js for the boot/init sequence.

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

// The close POST rides the plain-fetch channel (postJSON — X-Claude-Dash header,
// JSON body, a CLOSE_POST_MS timeout), tagged `audit:"close"` so its whole
// transport lifecycle lands in the frontend audit (close.begin/ok/fail). This is
// the transport PROVEN to traverse the tunnel (baqylau/dash.zhambyl.top): the
// click's own /hint-audit beacon and the composer /message ride it and always
// land, and every morning-era close (plain fetch) succeeded. navigator.sendBeacon
// was tried instead and REGRESSED close — it returns true (queued) so we resolved
// ok optimistically, but the queued beacon was then silently dropped by the
// tunnel: no `web-stop`, no `web-reject`, just the 20s `web-hint … stale`. The
// timeout turns a genuine upstream stall into a VISIBLE, retryable, audited
// failure (close.fail transport + web-clientfail) instead of a silent hang.
function closeSession(sid, via) {
  const url = "/api/session/" + encodeURIComponent(sid) + "/stop";
  return postJSON(url, {}, { timeout: CLOSE_POST_MS, audit: "close", sid,
                             auditData: { via: via || "" } });
}

// The .md body of a not-yet-delivered prompt bubble (the optimistic stand-in and
// the pinned queued bubble): the text with hard line breaks, textContent only —
// never innerHTML, since an undelivered prompt must never interpret markup.
function promptMd(text) {
  const md = el("div", "md");
  const p = el("p");
  (text || "").split("\n").forEach((line, i) => {
    if (i) p.append(el("br"));
    p.append(tnode(line));
  });
  md.append(p);
  return md;
}

// Plain-text bubble mirroring opshtml.msg_html's .msg.prompt shape, minus the
// rewind ↶ (a not-yet-delivered prompt isn't re-runnable).
function pendingBubble(text) {
  const d = el("div", "msg prompt pending");
  d.append(el("span", "who", "you"));
  d.append(promptMd(text));
  return d;
}

// Create + track the optimistic stand-in for a send; returns its pend handle.
function addPending(ses, text) {
  const node = pendingBubble(text);
  const w = ses.stream.querySelector(".waiting");
  if (w) w.remove();
  ses.stream.insertBefore(node, ses.stream.firstChild);
  const pend = { text, node, ses, sid: S.cur, t0: performance.now(), timer: null };
  ses.pending.push(pend);
  hintAudit(pend, "shown");
  // watchdog: a stand-in still unreconciled after STALE_HINT_MS is a stuck
  // grey bubble — the failure this audit exists to catch. Fire the beacon once
  // and KEEP the node (the user is still staring at grey; the row is the
  // breadcrumb). Cleared by settlePending / leaveSession on a clean outcome.
  pend.timer = setTimeout(() => {
    pend.timer = null;
    if (pend.ses.pending.indexOf(pend) >= 0) hintAudit(pend, "stale");
  }, STALE_HINT_MS);
  return pend;
}

// Tear a stand-in down (matched | queued | send-failed) and audit the outcome.
function settlePending(pend, phase, extra) {
  if (!pend) return;
  if (pend.timer) { clearTimeout(pend.timer); pend.timer = null; }
  const i = pend.ses.pending.indexOf(pend);
  if (i >= 0) pend.ses.pending.splice(i, 1);
  if (pend.node) pend.node.remove();
  hintAudit(pend, phase, extra);
}

function drainPending(items) {
  const ses = S.ses;
  if (!ses || !ses.pending || !ses.pending.length) return;
  for (const it of items) {
    if (it.t !== "msg" || it.kind !== "prompt") continue;
    const real = (it.text || "").trim();
    // exact match, or (attachments prepend leading @path mentions +\n) the
    // real text ends with the typed suffix — server._with_attachments order
    const i = ses.pending.findIndex(p =>
      real === p.text || real.endsWith("\n" + p.text));
    if (i >= 0) settlePending(ses.pending[i], "reconciled");
  }
}

/* ---------- the ask card (AskUserQuestion from the web) ---------- */
// While Claude's question dialog is up in the terminal, the session SSE
// carries the pending ask (the PreToolUse stash — plugins/claude_code/
// ask_fmt.py) and this card mirrors it above the composer: option buttons
// (radio marks + "pick one" for single-select, checkbox marks + "pick any"
// for multiSelect — visually distinct so the mode is legible at a glance),
// a free-text "type your own" per question (the dialog's "Type something"
// row), a submit row (ALWAYS explicit — no auto-submit on a lone
// single-select click; the web card favors review-before-send over the
// TUI's one-keystroke feel), and "chat about this" (the dialog's own
// decline-and-discuss).
// Answers POST /answer, where the server drives the REAL dialog with
// screen-verified key events (dashboard/askdialog.py). The card clears via
// the SSE `ask` event when the answer's PostToolUse drops the stash.

// ---- the pinned goal card (docs/dashboard.md, *Web goal*) -------------------
// Claude Code's `/goal <condition>` built-in puts the session into autonomous
// mode toward a completion condition. No hook fires for it, so the server scans
// the transcript tail (session_goal → plugins.goal → transcript.goal_probe) and
// pushes {condition, met} on the `goal` SSE event. Pinned at the very top of the
// mirror tab (above tasks), amber while working and green "✓ achieved" once the
// checker confirms; hidden when there is no active goal. Read-only — the goal is
// set/cleared at the terminal (or via the composer's `/goal`), never here.

function buildGoalCard() {
  const wrap = el("div", "goalwrap");
  S.ses.goalEl = wrap;
  renderGoal();
  return wrap;
}

function renderGoal() {
  const ses = S.ses;
  if (!ses || !ses.goalEl) return;
  const wrap = ses.goalEl;
  wrap.textContent = "";
  const goal = (ses.meta && ses.meta.goal) || null;
  wrap.hidden = !goal || !goal.condition;
  if (wrap.hidden) return;
  const met = !!goal.met;
  const card = el("div", "goalcard" + (met ? " met" : ""));
  const head = el("div", "goalhead");
  head.append(el("span", "goalmark", met ? "✓" : "◎"));
  head.append(el("span", "goaltitle", "goal"));
  head.append(el("span", "goalstate", met ? "achieved" : "active"));
  card.append(head);
  card.append(el("div", "goalcond", goal.condition));
  wrap.append(card);
}

// ---- the pinned tasks card (docs/dashboard.md, *Web tasks*) -----------------
// The session's native task list (TaskCreate/TaskUpdate), pinned at the very
// top of the mirror tab — fed by the `tasks` kv snapshot task_fmt.py re-reads
// from Claude Code's on-disk task dir on every task-touching hook, so it works
// live AND parked (the on-disk files are deleted at session end; the stash is
// the only surviving record). Read-only: unlike ask/plan there is no dialog to
// drive — the TUI has no modal to answer. Completed tasks render struck-through
// and dimmed; the in_progress one carries the accent and shows its activeForm.

function buildTasksCard() {
  const wrap = el("div", "taskswrap");
  S.ses.tasksEl = wrap;
  renderTasks();
  return wrap;
}

function renderTasks() {
  const ses = S.ses;
  if (!ses || !ses.tasksEl) return;
  const wrap = ses.tasksEl;
  wrap.textContent = "";
  const tasks = (ses.meta && ses.meta.tasks) || null;
  wrap.hidden = !tasks || !tasks.length;
  if (wrap.hidden) return;
  const done = tasks.filter(t => t.status === "completed").length;
  const card = el("div", "taskscard");
  const head = el("div", "taskshead");
  head.append(el("span", "taskstitle", "tasks"));
  head.append(el("span", "taskscount", done + "/" + tasks.length + " done"));
  card.append(head);
  const list = el("div", "tasklist");
  tasks.forEach(t => {
    const st = t.status === "completed" ? "done"
             : t.status === "in_progress" ? "active" : "pend";
    const row = el("div", "taskrow " + st);
    row.append(el("span", "taskmark",
                  st === "done" ? "✓" : st === "active" ? "▸" : "○"));
    row.append(el("span", "taskid", "#" + (t.id || "?")));
    const subj = el("span", "tasksubj", t.subject || "");
    if (t.description) subj.title = t.description;
    row.append(subj);
    // the spinner label the TUI shows while a task runs
    if (st === "active" && t.activeForm && t.activeForm !== t.subject)
      row.append(el("span", "taskactive", t.activeForm + "…"));
    if ((t.blockedBy || []).length)
      row.append(el("span", "taskblocked",
                    "⛓ " + t.blockedBy.map(b => "#" + b).join(" ")));
    list.append(row);
  });
  card.append(list);
  wrap.append(card);
}

