"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.

/* The session view's chrome, as NAMED PHASES (the styleguide's shape for a long
   builder — small functions named for what they build, one visible order):
   identity chips → action buttons → quick-command row → the live rows the SSE
   patchers fill → the tab strip → the open tab's body. Each phase returns its
   element and parks on `sessionView` whatever the patchers reach for later.

   It was one 350-line function, which is a poor place to look for any single one
   of those six jobs: the ✕ close button sat 130 lines below the identity chips it
   shares nothing with, and "does the effort picker exist when parked?" meant
   scrolling for the live gate rather than reading one signature. */
function renderSessionChrome(tab) {
  const sessionView = S.sessionView;
  if (!sessionView) return;
  // In AGENT SCOPE the header keeps showing that agent (its own scoreboard,
  // name and status); outside it there is nothing focused. Derived from the
  // scope rather than cleared, so a tab switch inside scope stays in scope.
  sessionView.agentFocus = sessionView.agent ? { actorId: sessionView.agent, data: null } : null;
  sessionView.monitorFocus = null;    // …nor monitor-focused (a drill-down sets it again)
  sessionView.jobFocus = null;        // …nor background-job-focused
  const meta = sessionView.meta || {};
  $view.textContent = "";

  const head = el("div", "shead");
  head.dataset.tab = meta.tab || "";    // state tint; live via setBadge()
  head.append(chromeIdentity(sessionView, meta));
  // the action buttons are mounted in the PAGE HEADER (mountHeaderActions), not
  // here — the top-right corner the list page fills with ▦ stats / ⛶ / +
  // session is dead space inside a session, and those gestures aren't about the
  // session you're reading.
  mountHeaderActions(sessionView, meta);
  head.append(...chromeLiveRows(sessionView));
  $view.append(head);
  updateStatsRow();
  updateRunning();

  $view.append(chromeTabs(sessionView, meta, tab));
  const body = el("div");
  sessionView.body = body;
  $view.append(body);
  resetBody();                // the way out of agent scope, above every tab
  chromeBody(sessionView, tab, body);
  applyAgentActionVis();      // session-only header actions don't apply in scope
}

/* l1: who this session IS — title, state badge, directory, sessionId, checkout,
   account. Every chip is static except the three parked on `sessionView`, which the
   `title` / `tab` / `git` SSE events patch in place. */
function chromeIdentity(sessionView, meta) {
  const l1 = el("div", "l1");
  const projSpan = el("span", "proj",
                      meta.title || directoryName(meta.workingDirectory)
                                 || shortSid(S.currentSessionId));
  sessionView.projEl = projSpan;                // the `title` SSE + inline rename target
  l1.append(projSpan);
  const badge = el("span", "badge");
  sessionView.badge = badge;
  setBadge(badge, meta.tab || "");
  l1.append(badge);
  // "live" goes unsaid (state tint + badge carry it); parked still shows
  if (!meta.live) l1.append(el("span", "chip2 parked", "parked"));
  if (meta.workingDirectory) {
    // just the directory name (basename) — the full path rides the tooltip
    const cwdChip = el("span", "sessionId", meta.workingDirectory.split("/").filter(Boolean).pop());
    cwdChip.title = meta.workingDirectory;
    l1.append(cwdChip);
  }
  const sidChip = el("span", "sessionId copysid", shortSid(S.currentSessionId));
  sidChip.title = "click to copy the full session id";
  sidChip.onclick = () => copySid(S.currentSessionId);
  l1.append(sidChip);
  // the checkout this session runs in — live via the `git` SSE event
  const gitc = el("span", "gitchip");
  sessionView.gitChip = gitc;
  setGitChip(gitc, meta.git);
  l1.append(gitc);
  // which account this chat runs under (◈ c2 · claude-01), and where its rate
  // limits stood when it last reported them (5h 12% · 7d 40%). Both come from
  // the session's OWNING host: a host with no account switcher names itself and
  // its plan instead of a slug (◈ codex · plus), and states its windows in the
  // same served vocabulary — so the chip reads the `windows` list rather than
  // Claude's flat five_hour/seven_day keys, which no other host has. Only
  // ACCOUNT-wide windows are shown: a per-model weekly cap belongs on the strip,
  // not in a one-line header chip.
  const acc = meta.account || {};
  if (acc.slug || acc.label) {
    const chip = el("span", "acctchip");
    chip.append(el("span", "ag", "◈"), tnode(
      " " + (acc.slug ? acc.slug + " · " + acc.label : acc.label)));
    const wins = ((meta.usage || {}).windows || []).filter(
      w => w.scope === "account" && typeof w.used_pct === "number");
    if (wins.length)
      chip.append(el("span", "ausage",
                     wins.map(w => w.label + " " + w.used_pct + "%").join(" · ")));
    l1.append(chip);
  }
  return l1;
}

/* ---------- the header action bar ---------- */
/* The session's own gestures live in the PAGE HEADER's top-right (docs/
   dashboard.md *Header action bar*), where the list page keeps ▦ stats / ⛶ /
   ＋session — those are gestures about the CROWD of sessions and are hidden in a
   session view (style.css `body.in-session`), so the corner is free for the
   ones that are about the session you are actually reading.

   Two `.actrow`s side by side, ordered by how often you reach for them rather
   than by what they do to the session: the QUICK COMMANDS lead (✦ model ✧
   effort ⊜ compact — the knobs you turn mid-conversation), then the session
   gestures (✎ rename ⇆ migrate ◉ alerts ↶ rewind ■ stop ✕ close), ending on the
   two most destructive so a fumbled click lands on nothing worse than a rewind
   arm. Mounted here rather than built inline so there is ONE owner of "the
   header belongs to this session" — and one place to empty it when you leave
   (clearHeaderActions, called by the router for every non-session route). */
function mountHeaderActions(sessionView, meta) {
  if (!$sessact) return;
  clearHeaderActions();
  for (const row of [chromeQuickCmds(sessionView, meta), chromeActions(sessionView, meta)])
    if (row.childElementCount) $sessact.append(row);
  $sessact.hidden = !$sessact.childElementCount;
}

function clearHeaderActions() {
  if (!$sessact) return;
  $sessact.textContent = "";
  $sessact.hidden = true;
}

/* "This action doesn't apply right now, and here is why" — disabled plus a
   title that NAMES the reason, the working tooltip (`data-tip`) coming back the
   moment it applies again.

   The rule the header follows: a button that doesn't apply GREYS, it does not
   vanish. Buttons that come and go move every other button under the cursor
   between one tab state and the next, and the corner is small enough that ✕
   close would land where ■ stop was. So the set is the same for a live session,
   a busy one and a parked one — only the reachable subset changes. */
function gate(btn, ok, why) {
  btn.disabled = !ok;
  btn.title = ok ? (btn.dataset.tip || "") : why;
}

// Why a button is out because this session's TOOL can't do it (a codex /
// copilot / opencode host that leaves the gesture inert — meta.caps[key] false).
// The server's _caps_guard 409s the same gesture; this just says so up front.
const CAP_OFF = "not supported by this session's tool";

// Does the owning harness support gesture `key`? Missing capability state is a
// denial; the current contract never guesses support.
function capOk(meta, key) {
  const caps = meta && meta.caps;
  return !!(caps && caps[key]);
}

// The QUICK-COMMAND vocabulary of the session's owning host: meta.quick_commands
// is [{cmd, min_prompts}] — which of the closed set (compact/model/effort/the
// argless rename) this host actually implements, each with the REFUSAL FLOOR it
// measured. Both facts used to be client constants that spelled Claude Code's
// answer and applied it to every host: a `COMPACT_MIN_PROMPTS = 2` and an inline
// `prompts < 1` for ✦ auto.
//
// A missing command is unsupported; the browser never borrows another
// harness's command vocabulary.
function cmdOffered(meta, cmd) {
  const list = meta && meta.quick_commands;
  return Array.isArray(list) && list.some(c => c && c.cmd === cmd);
}

// How many of YOUR prompts (meta.prompts, capped server-side) this host needs
// before it will accept `cmd`. 0 when it declares no floor — and an UNKNOWN
// prompt count never greys anything: the gate only ever argues for disabling, so
// it must not act on a number it doesn't have.
function cmdFloor(meta, cmd) {
  const list = (meta && meta.quick_commands) || [];
  const row = Array.isArray(list) ? list.find(c => c && c.cmd === cmd) : null;
  return (row && row.min_prompts) || 0;
}

function tooThin(meta, cmd) {
  const n = meta && meta.prompts;
  return typeof n === "number" && n < cmdFloor(meta, cmd);
}

// Why every terminal-typing action is out for a parked session: it has no
// window to type into (the server rejects them too — this just says so first).
const NO_WINDOW = "this session is parked — there is no terminal to type into";

/* The header bar's SECOND row: the session-level gestures. rename / migrate /
   alerts work live AND parked (they touch the transcript or a dashboard pref,
   not the terminal); rewind / stop / close need a window to type into and close
   the row on its destructive end, and resume is the parked-only counterpart. */
function chromeActions(sessionView, meta) {
  const act = el("div", "actrow");
  const windowed = !!(meta.live && meta.terminal_window_id);
  // rename: deliberately OUTSIDE the live gate — it works for live AND parked
  // sessions (the server appends the agent-name naming record to the
  // transcript; a live terminal tab also retitles in place — docs/dashboard.md
  // "Web rename")
  const ren = el("button", "sstop actses", "✎ rename");
  ren.dataset.tip = "rename this session (resume picker + tab)";
  ren.onclick = () => startRenameHeader();
  gate(ren, capOk(meta, "rename"), CAP_OFF);   // a tool that can't rename greys it
  act.append(ren);
  // migrate: hand this session to the other subscription account — the same
  // detached migrator as the automatic rate-limit path (docs/relimit.md
  // *Manual migrate*): live → the tab swaps (close, park, resume under the
  // other alias); parked → it just relaunches there. Immediate, no confirm
  // (like ■ stop), and like rename it works live AND parked.
  const mig = el("button", "sstop actses", "⇆ migrate");
  mig.dataset.tip = "migrate this session to another account";
  mig.onclick = () => lockDuring(mig, migrateSession);
  gate(mig, capOk(meta, "migrate"), CAP_OFF);   // the server 409s it too
  act.append(mig);
  // ◉ alerts / ○ muted: opt this session in/out of the DEFERRED Telegram
  // alert (docs/dashboard.md *Telegram alerts*) — the off-device notification
  // that fires when a chat sits red/green unattended past the grace window.
  // Deliberately OUTSIDE the live gate (like rename): the opt-out is a
  // dashboard pref, not session state, so it works live AND parked.
  const notif = el("button", "sstop actses");
  const paintNotif = (muted) => {
    notif.textContent = muted ? "○ muted" : "◉ alerts";
    notif.title = muted
      ? "Telegram alerts muted for this session — click to unmute"
      : "Telegram alerts on — click to mute this session";
  };
  paintNotif(meta.notify_muted);
  notif.onclick = () => {
    const next = !meta.notify_muted;
    postJSON("/api/sessions/" + encodeURIComponent(S.currentSessionId)
             + "/application/notifications-muted",
             { muted: next })
      .then(() => {
        meta.notify_muted = next;
        paintNotif(next);
        toast("done", next ? "alerts muted" : "alerts on",
              next ? "no Telegram for this session"
                   : "Telegram alerts re-enabled");
      })
      .catch(e => toast("ask", "mute toggle failed", (e && e.error) || ""));
  };
  act.append(notif);
  // stop: THE one stop gesture — an Escape key press in the session's window.
  // What it does is the terminal's call, not ours: interrupt a turn that has
  // already done work and the work is kept; interrupt one early enough and
  // Claude Code takes the message back, handing it to the input box (the
  // response's `restored`, which applyTakeBack mirrors into the composer).
  // There used to be a second ⊘ cancel button for that second outcome, on the
  // theory that it needed a DOUBLE Escape — it doesn't (measured 2026-07-25),
  // so the two buttons were one gesture with two labels.
  // Immediate, no confirm: it matches pressing Esc in the terminal.
  const stop = el("button", "sstop actstop", "■ stop");
  stop.dataset.tip = "stop the turn (Esc) — takes your message back if nothing ran yet";
  stop.onclick = () => lockDuring(stop, interruptSession,
                                  () => sessionView.stopMode(liveTab()));
  // rewind: idle-only picking mode — click a message below, choose what to
  // restore, and the server drives the TUI's own checkpoint menu
  const rew = el("button", "sstop actses", "↶ rewind");
  rew.dataset.tip = "rewind: pick a message to restore to (idle only)";
  // stopPropagation is load-bearing: the ENABLING click must not bubble to
  // the document click-away handler, which reads any non-bubble click in
  // picking mode as "leave" — without it the mode self-cancelled in the
  // same event (toast shown, buttons never revealed)
  rew.onclick = (e) => { e.stopPropagation(); rewindSession(); };
  // Both are tab-state gated, and re-derived from the tab on every SSE `tab`
  // event — never blindly re-enabled. ■ stop applies only while a turn is
  // RUNNING (an Esc when idle can clear queued input instead), and NOT on a red
  // awaiting_attention, where an Esc declines the open dialog. ↶ rewind is the
  // exact complement: it drives the TUI's checkpoint menu, which needs an idle
  // session — rewindSession bails on a busy or red tab with a toast, and the
  // button now says so before the click rather than after it.
  sessionView.stopMode = (t) => {
    const iCap = capOk(meta, "interrupt"), rCap = capOk(meta, "rewind");
    gate(stop, iCap && windowed && BUSY_TABS.includes(t),
         !iCap ? CAP_OFF
           : !windowed ? NO_WINDOW
           : t === "awaiting_attention" ? "a question is waiting — answer it in the card"
           : "nothing is running to stop");
    gate(rew, rCap && windowed && !BUSY_TABS.includes(t) && t !== "awaiting_attention",
         !rCap ? CAP_OFF
           : !windowed ? NO_WINDOW
           : t === "awaiting_attention" ? "a question is waiting — answer it first"
           : "a turn is running — stop it first, then rewind");
  };
  sessionView.stopMode(liveTab());
  act.append(rew, stop);       // rewind before stop — the row ends destructive
  // close: closes the session's terminal tab — a graceful stop (Claude Code
  // exits on the HUP and SessionEnd runs the normal lifecycle).
  // Two-step confirm: first click arms for 4s, second click fires.
  const cls = el("button", "sstop actses", "✕ close");
  cls.dataset.tip = "close this session's terminal tab";
  gate(cls, windowed, NO_WINDOW);   // nothing to close once it's parked
  armConfirm(cls, "✕ close", "close session?", () => {
    cls.disabled = true;
    cls.textContent = "closing…";
    const sessionId = S.currentSessionId;
    // optimistic close: beacon the `close` lifecycle (web-hint op=close) and
    // navigate back to the list on the POST ack — the list card shows greyed
    // 'closing…' (S.closing) until snapshot reconciliation parks it.
    closeBegin(sessionId);
    closeSession(sessionId, "header")
      .then(() => {
        toast("done", "session closed", "terminal tab closed");
        // the session just ended — back to the list, unless the user
        // already navigated elsewhere while the POST was in flight
        if (S.currentSessionId === sessionId) location.hash = "#/";
      })
      .catch(e => {
        closeSettle(sessionId, "dropped", { reason: "failed" });
        gate(cls, true);
        cls.textContent = "✕ close";
        clientFail(sessionId, "close", e);   // a lost/rejected /stop the audit can't see
        toast("ask", "close failed", (e && e.error) || "");
      });
  });
  act.append(cls);
  // resume: reopen the new-session form preset to this conversation, in this
  // session's directory (the OWNING host's own resume argv, composed server-side
  // — `--resume` or `resume`). The parked-only counterpart of ✕ close, so the
  // two swap: a LIVE session has nothing to resume, and it is the one button the
  // bar still builds conditionally rather than greying.
  //
  // It does grey for the other refusal, though — a parked session with no workingDirectory
  // (an old row whose directory was never recorded) has nowhere to launch, and
  // the form would open on an empty folder field. That used to be half of the
  // same `if`, i.e. a button that silently wasn't there.
  if (!meta.live) {
    const res = el("button", "sresume actses", "↻ resume");
    res.dataset.tip = "start a new tab resuming this conversation";
    res.onclick = () => openNewSession(meta.workingDirectory, S.currentSessionId);
    gate(res, !!meta.workingDirectory,
         "no directory recorded for this session — nowhere to resume it");
    act.append(res);
  }
  return act;
}

/* The header bar's LEADING row: the quick commands — the model/effort pickers +
   compact, each typing the TUI's own slash command into the session
   (docs/dashboard.md, *Web quick commands*). First because these are the knobs
   you reach for mid-conversation. Every one needs a window to type into, so on a
   parked session they are all greyed rather than absent (see gate()). */
function chromeQuickCmds(sessionView, meta) {
  const act2 = el("div", "actrow");
  const windowed = !!(meta.live && meta.terminal_window_id);
  // compact: two-step confirm like close — a misclick summarizes the whole
  // conversation out from under you, so it arms first. Built first, APPENDED
  // last: the two pickers lead the row (see the append order below).
  const cpt = el("button", "sstop actses", "⊜ compact");
  cpt.dataset.tip = "compact the conversation (/compact)";
  armConfirm(cpt, "⊜ compact", "compact now?", () => sendQuickCmd("compact"));
  // model: dropdown picker; the label shows the ctx probe's current model
  // (live via the `ctx` SSE event → updateStatsRow)
  const mwrap = el("span", "qcwrap actses");
  const mdl = el("button", "sstop");
  sessionView.modelBtn = mdl;
  setModelBtn(mdl);
  mdl.dataset.tip = "switch the model (/model — also saves as your new-session default)";
  mdl.onclick = () => openQuickMenu(mwrap, "model", hostChoices("model"),
                                    curModelFamily());
  mwrap.append(mdl);
  // effort: dropdown picker (current effort is config-only — not readable
  // from any transcript, see plugins/claude_code/model.py — so no label)
  const ewrap = el("span", "qcwrap actses");
  const eff = el("button", "sstop");
  sessionView.effortBtn = eff;
  setEffortBtn(eff);
  eff.dataset.tip = "set the reasoning effort (/effort — also saves as your new-session default)";
  eff.onclick = () => openQuickMenu(ewrap, "effort", hostChoices("effort"),
                                    (sessionView.meta && sessionView.meta.effort) || "");
  ewrap.append(eff);
  act2.append(mwrap, ewrap, cpt);
  // a red tab = a modal dialog is up — pasted text would land IN it (the
  // server 409s too; disabling just says so up front). Live via the same
  // SSE tab event as stopMode.
  //
  // ⊜ compact carries one gate of its own: a host that refuses to compact a
  // conversation that has barely started declares the floor (quick_commands'
  // `min_prompts` — Claude Code's 2), and a session under it greys the button
  // instead of typing a command the TUI will bounce (`prompts`, patched live by
  // its SSE event).
  sessionView.quickMode = (t) => {
    const dialog = t === "awaiting_attention";
    const base = windowed && !dialog;
    const baseWhy = !windowed ? NO_WINDOW
                              : "a question is waiting — answer it in the card";
    // each command has its own host capability (a tool may compact but not
    // switch model, say); a false cap greys that one button with CAP_OFF
    gate(mdl, capOk(meta, "model") && base, !capOk(meta, "model") ? CAP_OFF : baseWhy);
    gate(eff, capOk(meta, "effort") && base, !capOk(meta, "effort") ? CAP_OFF : baseWhy);
    const cCap = capOk(meta, "compact") && cmdOffered(meta, "compact");
    const thin = tooThin(sessionView.meta, "compact");
    gate(cpt, cCap && base && !thin,
         !cCap ? CAP_OFF : !base ? baseWhy
               : "not enough conversation to compact yet");
  };
  sessionView.quickMode(liveTab());
  return act2;
}

/* The three header rows that start EMPTY and are filled by the patchers
   (updateStatsRow / the ctx bar / updateRunning), in paint order. */
function chromeLiveRows(sessionView) {
  const sr = el("div", "statsrow");
  sessionView.statsRow = sr;
  sessionView._statsSig = null;      // fresh (empty) row — force the next paint through
  const contextRow = el("div", "ctxrow");
  sessionView.ctxRow = contextRow;
  const runningRow = el("div", "runrow");
  sessionView.runRibbon = runningRow;
  return [sr, contextRow, runningRow];
}

/* The tab strip. Each count is the canonical snapshot list's length once it is
   present, otherwise the global snapshot's summary count. */
function chromeTabs(sessionView, meta, tab) {
  const tabs = el("div", "tabs");
  const scoped = sessionView.agent || "";
  const mk = (key, label, count) => {
    const a = el("a", key === tab ? "on" : "");
    // in agent scope every tab stays in scope (docs/dashboard.md *Agent
    // scope*) — the `agents` tab is the one that must not, since a list of the
    // SESSION's agents is what you navigate between them with
    a.href = (scoped && key !== "agents")
      ? agentHref(S.currentSessionId, scoped, key)
      : "#/s/" + encodeURIComponent(S.currentSessionId) + (key === "mirror" ? "" : "/" + key);
    a.append(tnode(label));
    if (count) a.append(el("span", "count", String(count)));
    tabs.append(a);
    return a;
  };
  mk("mirror", "mirror");
  mk("agents", "agents", (sessionView.agents || []).length);
  // Prefer the focused list; the global summary count is available before open.
  sessionView.monTab = mk("monitors", "monitors",
                  sessionView.monitors ? sessionView.monitors.length : (meta.monitor_count || 0));
  // Background jobs follow the same snapshot-backed count rule.
  sessionView.jobTab = mk("jobs", "jobs",
                  sessionView.jobs ? sessionView.jobs.length : (meta.job_count || 0));
  sessionView.errTab = mk("errors", "errors", meta.error_count || 0);   // live ⚠ count patches it
  // errors have no agent dimension (an error is a script's), so in agent scope
  // the tab still shows the SESSION's — said out loud rather than left ambiguous.
  if (scoped && tab === "errors")
    tabs.append(el("span", "tabnote", "session-wide"));
  return tabs;
}

/* The open tab's body. The mirror tab is the composite one (cards → composer →
   view bar → the stream/rail split); the rest render snapshot-backed grids. */
function chromeBody(sessionView, tab, body) {
  if (tab === "mirror") {
    // The pinned cards and the composer are the SESSION's — its goal, its task
    // list, its pending dialogs, its input box. In agent scope they would all
    // be lies about what you're looking at (worst of all the composer, which
    // types to the lead, not the agent you drilled into), so the scoped mirror
    // is the stream and its view bar alone.
    if (!sessionView.agent) {
      body.append(buildGoalCard());         // the active /goal, pinned at the very top
      body.append(buildTasksCard());        // the session's task list, pinned first
      body.append(buildPlanCard());         // pending plan approval …
      body.append(buildAskCard());          // … and question, above the composer
      body.append(buildComposer());
      // type right away on open — no click needed. After append (focus() on a
      // detached node is a no-op), and only when the box can send (a disabled
      // parked/headless composer takes no input anyway). The document-level
      // gestures (Esc, ⌃-keys, ⌃⇧←/→) are focus-independent, so this only
      // redirects plain typing. Not on an iPad: an unasked-for focus pops the
      // on-screen keyboard over the stream on every session open (and focus is
      // what triggers Safari's page auto-zoom — see style.css touch section).
      if (!sessionView.composer.disabled && !IS_IPAD) sessionView.composer.focus();
    }
    body.append(buildViewBar());
    const split = el("div", "split");
    // the transcript column: queued messages pinned ABOVE the newest-first
    // stream (so incoming activity never buries them) until they're delivered
    const scol = el("div", "scol");
    scol.append(buildQueuePin());
    scol.append(sessionView.stream);
    split.append(scol);
    const rail = el("div", "rail");
    sessionView.rail = rail;
    split.append(rail);
    body.append(split);
    updateAgents();
    updateMoreBtn();                      // the load-older affordance at the bottom
    updateShownCount();                   // count items already in the stream
  } else if (tab === "agents") {
    const wrap = el("div", "sgrid");
    sessionView.agentsGrid = wrap;
    body.append(wrap);
    updateAgents();
  } else if (tab === "monitors" || tab === "jobs") {
    // The two grid sections share one snapshot-backed rendering machine.
    const sec = SECTIONS[tab];
    const wrap = el("div", "sgrid");
    sessionView[sec.grid] = wrap;
    body.append(wrap);
    if (sessionView[sec.list]) renderSectionGrid(tab);
    else wrap.append(el("div", "empty", "loading " + sec.label + "…"));
    loadSection(tab);
  } else if (tab === "errors") {
    renderErrorsInto(body);
  }
}

// A content signature of everything the stats row + ctx row RENDER, EXCLUDING
// the live ⏱ elapsed (Date.now-derived) — so a tick that only advances the
// clock, or a costs/ctx/running SSE that leaves the shown numbers unchanged,
// does NOT tear down and rebuild the row. The teardown (sr.textContent = "")
// reflows the header, which on iPad Safari drops an in-progress text selection
// (the "selection vanishes after ~1s" report, 2026-07-19). The clock still
// advances whenever any real datum changes (constant during active work).
function statsSig(sessionView) {
  const f = sessionView.agentFocus;
  if (f) {
    const d = agentUsage(sessionView);
    const rec = (sessionView.agents || []).find(a => a.agent_id === f.actorId) || {};
    return "A|" + [f.actorId, rec.kind, rec.desc, rec.ended_at, rec.started_at,
      rec.tools, rec.model, rec.effort, rec.end_reason, rec.done,
      d.cost, d.model].join(",")
      + "|" + JSON.stringify(d.usage || {}) + "|" + JSON.stringify(rec.contextWindow || {});
  }
  const st = sessionView.stats || {};
  const cost = (sessionView.costs && sessionView.costs.total_usd) || st.cost;
  return "S|" + [st.commands, st.failed, st.start, st.paused, st.files,
    st.added, st.removed, st.tk_in, st.tk_out, st.tk_read, st.tk_create, cost,
    st.msg_delivered, st.msg_read, (sessionView.meta && sessionView.meta.error_count) || 0,
    sessionView.meta && sessionView.meta.model,
    sessionView.meta && sessionView.meta.effort,
    // compaction is a ctx-ROW state, not a stats number, but it lives on the
    // same signature: without it the row never rebuilds when compaction
    // starts (nothing else about the session changes for those ~2 minutes —
    // that is the whole point) and the animation would never appear. `since`
    // is constant while one compaction runs, so this adds no extra rebuilds.
    (sessionView.compacting && sessionView.compacting.since) || 0].join(",")
    + "|" + JSON.stringify(sessionView.contextWindow || {});
}

function updateStatsRow() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.statsRow) return;
  const sig = statsSig(sessionView);
  if (sig === sessionView._statsSig) return;   // nothing the row shows changed — skip
  sessionView._statsSig = sig;                 // the teardown (preserves iPad selection)
  const sr = sessionView.statsRow;
  sr.textContent = "";
  // drilled into a subagent → the scoreboard shows THAT agent, not the session
  // (the "swap scoreboard on click" behaviour). SSE stats/costs/ctx events still
  // land here, but this branch keeps them from clobbering the agent view.
  if (sessionView.agentFocus) { renderAgentScoreboard(sr, sessionView.agentFocus); return; }
  const st = sessionView.stats || {};
  const add = chipAdder(sr);
  if (st.commands) {
    add("", st.commands + " cmds");
    if (st.failed) add("", "(" + st.failed + "✗)", "neg");
  }
  if (st.start)
    add("⏱", dur(Date.now() / 1000 - st.start - (+st.paused || 0)));
  if (st.files) add("", st.files + " files");
  if (st.added) add("", "+" + st.added, "pos");
  if (st.removed) add("", "−" + st.removed, "neg");
  sigmaChip(add, { in: st.tk_in, out: st.tk_out,
                   cache: st.tk_read, create: st.tk_create });
  const cost = (sessionView.costs && sessionView.costs.total_usd) || st.cost;
  if (cost) add("≈", usd(cost), "cost");
  if (st.msg_delivered)
    add("✉", st.msg_delivered + " msgs" +
        (st.msg_read ? " · " + st.msg_read + " read" : ""));
  const errn = (sessionView.meta && sessionView.meta.error_count) || 0;
  if (errn) add("", "⚠ " + errn, "warn");
  // the main thread's ctx bar on its own row — live via the `ctx` SSE event
  // the model quick-button's label follows the same ctx probe
  if (sessionView.modelBtn) setModelBtn(sessionView.modelBtn);
  // the effort quick-button has no probe of its own (see setEffortBtn) but
  // still needs a live refresh: without one, whatever it showed at the LAST
  // full renderSessionChrome sticks even after `effort.changed` lands on a
  // later SSE snapshot — e.g. the launch fact translating a beat after the
  // page's first render (reported: stuck on the bare "effort" label until a
  // reload forced a fresh renderSessionChrome).
  if (sessionView.effortBtn) setEffortBtn(sessionView.effortBtn);
  paintCtxRow(sessionView.contextWindow);
}

/* Header-action visibility for the agent-focus state (docs/dashboard.md,
   *Subagent scoreboard swap*). While a subagent scoreboard is showing, the
   session-only actions (`.actses` — rename / migrate / rewind / close /
   resume / compact / model / effort) don't apply to a subagent, so they hide;
   ■ stop (`.actstop`) stays ONLY while the focused subagent is still running
   (interrupting the session is the one way to stop it). An action row left with
   nothing visible collapses so it leaves no gap. A full renderSessionChrome
   rebuild (going back) restores everything, agentFocus already cleared.
   Scoped to the header bar (`$sessact`), which is where those buttons now
   live — the one query root, so this can't drift from where they are mounted. */
function applyAgentActionVis() {
  const sessionView = S.sessionView;
  if (!sessionView || !$sessact) return;
  const focused = !!sessionView.agentFocus;
  $sessact.querySelectorAll(".actses").forEach(b => { b.style.display = focused ? "none" : ""; });
  const stop = $sessact.querySelector(".actstop");
  if (stop) {
    let show = true;
    if (focused) {
      const rec = (sessionView.agents || []).find(a => a.agent_id === sessionView.agentFocus.actorId);
      show = !!(rec && agentStatus(rec)[1] === "st-run");
    }
    stop.style.display = show ? "" : "none";
  }
  $sessact.querySelectorAll(".actrow").forEach(row => {
    const any = [...row.children].some(c => c.style.display !== "none");
    row.style.display = any ? "" : "none";
  });
}

/* The scoreboard for a drilled-into subagent — replaces the session totals with
   THAT agent's own numbers (docs/dashboard.md, *Subagent scoreboard swap*). It
   resolves the freshest agent row from sessionView.agents each render (so an `agents`
   SSE that finishes the agent updates the status here too) and reads tokens/cost
   from the session payload's `agent_usage` (served whenever a `?agent=` scope is
   in play, so this needs no request of its own). The prominent header NAME becomes
   the agent's; the stats row leads with a "← session" link that leaves scope, and
   the ctx row repaints from the agent's own ctx bar. */
/* The scoped agent's own token rollup + priced cost — served on the session
   payload when a `?agent=` is in play (read/session.agent_usage), so the
   scoreboard needs no request of its own. {} before meta lands, or for an agent
   with no transcript to fold (a codex run prices itself). */
function agentUsage(sessionView) {
  return ((sessionView && sessionView.meta) || {}).agent_usage || {};
}

function renderAgentScoreboard(sr, focus) {
  const sessionView = S.sessionView;
  const rec = (sessionView.agents || []).find(a => a.agent_id === focus.actorId) || {};
  const d = agentUsage(sessionView);
  const [sttxt, stcls] = agentStatus(rec);
  // the header badge/dot + .shead wash follow THIS agent's status, not the
  // session tab (the session pill said "busy" over a finished subagent).
  setBadgeAgent(sessionView.badge, sttxt, stcls);
  // the big header name updates to the subagent (the session title returns when
  // renderSessionChrome rebuilds on the way back). Skip during an inline rename.
  if (sessionView.projEl && !sessionView.projEl.querySelector("input"))
    sessionView.projEl.textContent =
      (rec.kind === "teammate" ? "◈ " : "◇ ") + (rec.desc || focus.actorId);
  const back = el("a", "backses", "← session");
  back.href = "#/s/" + encodeURIComponent(S.currentSessionId);   // the mirror = the main agent
  sr.append(back);
  const add = chipAdder(sr);
  add("", sttxt, stcls);
  const model = rec.model || (d.model ? String(d.model) : "");
  if (model) add("", model + (rec.effort ? "·" + rec.effort : ""), "amodel");
  const ev = rec.tools;
  if (ev != null) add("", ev + " events");
  if (rec.started_at)
    add("⏱", rec.ended_at ? dur(rec.ended_at - rec.started_at) : ago(rec.started_at));
  sigmaChip(add, d.usage || {});     // the agent shape IS in/out/cache/create
  if (d.cost) add("≈", usd(d.cost), "cost");
  paintCtxRow(rec.contextWindow, focus.actorId);   // the agent's own saturation, same row
}

/* The ctx-saturation row under the scoreboard — its own row, shown only while
   there is an occupancy figure to show (docs/dashboard.md, *Context
   saturation*). One owner for both scoreboards: the session's ctx comes from the
   `ctx` SSE event, a drilled-in agent's from its own record, and the row is
   REPLACED (not appended) on every repaint.

   `actorId` names a drilled-in agent. It decides BOTH extras: the compaction
   animation is the SESSION's (compaction folds the main thread's conversation —
   an agent has none of its own to compact, and painting the lead's rehearsal
   over an agent's bar would attribute it to the wrong context), and the
   drain's identity key keeps each agent's bar animating from its own last
   width rather than from whichever bar this row showed before. */
function paintCtxRow(contextWindow, actorId) {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.ctxRow) return;
  sessionView.ctxRow.textContent = "";
  if (contextWindow && contextWindow.used)
    sessionView.ctxRow.append(contextBar(contextWindow, true, {
      comp: actorId ? null : sessionView.compacting,
      // S.currentSessionId, NOT a field on `sessionView` — the session object carries no sessionId, so
      // `sessionView.sessionId` would key every session as "s:undefined" and switching
      // sessions would drain the new bar out of the old one's width
      key: (actorId ? "a:" + actorId : "s:" + S.currentSessionId),
    }));
  sessionView.ctxRow.style.display = contextWindow && contextWindow.used ? "" : "none";
}

/* Live ⚠ error badge. The selected-session application snapshot contains both
   the count and the complete error rows. */
function updateErrCount(n) {
  const prev = (S.sessionView && S.sessionView.meta && S.sessionView.meta.error_count) || 0;   // pre-patch
  const sessionView = setTabBadge("error_count", "errTab", n);
  if (!sessionView) return;
  updateStatsRow();                  // the ⚠ chip lives in the scoreboard row too
  if (sessionView.tab === "errors" && n > prev && sessionView.body) renderErrorsInto(sessionView.body);
}

/* Patch a tab's count badge AND the cached meta it is rebuilt from, together —
   the shared body of the monitors / jobs / errors counters. Both halves
   are needed: setTabCount paints the badge now, sessionView.meta[field] is what a later
   renderSessionChrome rebuilds it from (drop that and the badge reverts on the
   next rebuild). Returns the session (null when there's none), so a caller can
   chain its own "…and refresh the list if that tab is open" tail.

   Snapshot reconciliation calls the exact-list and summary-count paths at
   different times, so both update the same cached count and badge. */
function setTabBadge(field, tabKey, n) {
  const sessionView = S.sessionView;
  if (!sessionView) return null;
  if (sessionView.meta) sessionView.meta[field] = n;
  setTabCount(sessionView[tabKey], n);
  return sessionView;
}

function setTabCount(a, n) {
  if (!a) return;
  let c = a.querySelector(".count");
  if (n) {
    if (!c) { c = el("span", "count"); a.append(c); }
    c.textContent = String(n);
  } else if (c) {
    c.remove();
  }
}

/* The "running now" ribbon — one chip per alive `live`-table slot row
   (canonical operation projections grouped by execution kind), hidden when
   nothing is running.
   Live-updated by the `running` SSE event. */
function updateRunning() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.runRibbon) return;
  const run = sessionView.running || {};
  const rr = sessionView.runRibbon;
  rr.textContent = "";
  // the running ribbon is session-scoped; hide it while a subagent scoreboard
  // is showing (the header is about that one agent then, not the session)
  if (sessionView.agentFocus) { rr.style.display = "none"; return; }
  const kinds = RUN_ORDER.concat(
    Object.keys(run).filter(k => !RUN_ORDER.includes(k)));
  let any = false;
  for (const kind of kinds) {
    const count = Number(run[kind]) || 0;
    if (!count) continue;
    const [glyph, label] = RUN_APPEARANCE[kind] || ["•", kind];
    for (let index = 0; index < count; index++) {
      any = true;
      const chip = el("span", "rchip rk-" + kind.replace(".", "-"));
      chip.append(el("span", "rg", glyph), tnode(" " + label));
      rr.append(chip);
    }
  }
  rr.style.display = any ? "" : "none";
}

function agentStatus(a) {
  if (a.ended_at == null && !a.done && a.started_at) return ["running", "st-run"];
  const er = a.end_reason || "";
  if (!er && a.ended_at == null) return ["unknown", "st-warn"];
  if (er.startsWith("stop-sentinel") || er.startsWith("state-db-parked")) return ["done", "st-ok"];
  if (er.includes("cancel") || er.includes("rejected")) return ["cancelled", "st-bad"];
  if (er === "crash" || er.includes("timeout")) return [er, "st-bad"];
  return [er || "done", "st-ok"];
}

function isHusk(a) {
  return !a.kind && !a.desc;
}

function sortedAgents(agents) {
  return [...agents].sort((x, y) => (isHusk(x) - isHusk(y))
    || ((x.started_at || 0) - (y.started_at || 0)));
}

function agentCard(a) {
  const [sttxt, stcls] = agentStatus(a);
  const card = el("a", "acard" + (isHusk(a) ? " husk" : ""));
  card.dataset.st = stcls;              // state tint keyed off agent status
  card.href = agentHref(S.currentSessionId, a.agent_id, "mirror");   // into AGENT SCOPE
  const name = a.desc || a.agent_id;      // the Task description IS the name
  card.append(el("div", "actorId", (a.kind === "teammate" ? "◈ " : "◇ ") + name));
  if (a.desc) card.append(el("div", "desc", a.agent_id));
  const m = el("div", "meta");
  m.append(el("span", stcls, sttxt));
  // model·effort — the web echo of the terminal mirror's op tag (opus-4.8·high)
  if (a.model) m.append(el("span", "amodel",
    a.model + (a.effort ? "·" + a.effort : "")));
  if (a.tools != null) m.append(el("span", "", a.tools + " events"));
  if (a.started_at && a.ended_at)
    m.append(el("span", "", dur(a.ended_at - a.started_at)));
  else if (a.started_at)
    m.append(el("span", "", ago(a.started_at)));
  card.append(m);
  if (a.contextWindow) card.append(contextBar(a.contextWindow));
  return card;
}

function updateAgents() {
  const sessionView = S.sessionView;
  if (!sessionView) return;
  // a focused subagent finishing (running → done) must drop the ■ stop button
  // AND flip its scoreboard status/badge/wash (renderAgentScoreboard reads the
  // fresh agents row) — an `agents` SSE doesn't move statsSig, so re-render here
  // rather than via updateStatsRow's change-gate.
  if (sessionView.agentFocus) {
    applyAgentActionVis();
    if (sessionView.statsRow) {
      sessionView.statsRow.textContent = "";
      renderAgentScoreboard(sessionView.statsRow, sessionView.agentFocus);
    }
  }
  const agents = sortedAgents(sessionView.agents || []);
  if (sessionView.tab === "mirror" && sessionView.rail && sessionView.rail.isConnected) {
    sessionView.rail.textContent = "";
    if (agents.length) sessionView.rail.append(el("div", "mhead", "agents"));
    for (const a of agents) sessionView.rail.append(agentCard(a));
  }
  if (sessionView.tab === "agents" && sessionView.agentsGrid && sessionView.agentsGrid.isConnected) {
    sessionView.agentsGrid.textContent = "";
    if (!agents.length) sessionView.agentsGrid.append(el("div", "empty", "no subagents in this session"));
    for (const a of agents) sessionView.agentsGrid.append(agentCard(a));
  }
  // …and the mirror's agent NOTES carry the same outcome on their dot (they read
  // agentStatus above, so this event is what turns a launch note green when its
  // agent ends — no op is written for that)
  tintAgentNotes();
}

/* ---------- monitors (list tab + drill-down) ---------- */
/* The monitors tab mirrors the agents tab: canonical monitor activities render
   as cards and drill into a per-monitor detail on click. */

function monitorStatus(m) {
  if (m.live) return ["running", "st-run"];
  const er = m.end_reason || "";
  if (!er && m.ended_at == null) return ["unknown", "st-warn"];
  if (er.indexOf("no output") >= 0 || er.indexOf("silent") >= 0)
    return ["ended · no output", "st-ok"];
  if (er.indexOf("not-found") >= 0 || er.indexOf("never found") >= 0)
    return ["ended · not found", "st-warn"];
  if (er.indexOf("parked") >= 0) return ["ended (session end)", "st-ok"];
  return ["ended", "st-ok"];
}

function monitorCard(m) {
  const [sttxt, stcls] = monitorStatus(m);
  const card = el("a", "acard");
  card.dataset.st = stcls;
  card.href = sectionHref(S.currentSessionId, "m", m.task);
  const name = m.description || m.command || m.task;
  card.append(el("div", "actorId", "◉ " + name));
  // subtitle: the command when the name is the description, else the task id
  const sub = (m.description && m.command) ? m.command : m.task;
  if (sub) card.append(el("div", "desc", sub));
  const meta = el("div", "meta");
  meta.append(el("span", stcls, sttxt));
  if (m.persistent) meta.append(el("span", "amodel", "persistent"));
  else if (m.timeout_ms) meta.append(el("span", "amodel", "≤" + dur(m.timeout_ms / 1000)));
  meta.append(el("span", "", (m.event_count || 0) + " events"));
  if (m.started_at && m.ended_at) meta.append(el("span", "", dur(m.ended_at - m.started_at)));
  else if (m.started_at) meta.append(el("span", "", ago(m.started_at)));
  card.append(meta);
  return card;
}

/* Shared snapshot-backed list mechanics for monitors and jobs. */
function sectionCount(sec, sessionView) {
  if (sec.count) return sec.count(sessionView);
  return (sessionView[sec.list] || []).length;
}

const SECTIONS = {
  monitors: {
    api: "monitors", list: "monitors", grid: "monitorsGrid",
    focus: "monitorFocus", tabEl: "monTab",
    countField: "monitor_count", route: "m", glyph: "◉", label: "monitors",
    scoped: true,          // follows agent scope (the lead's own by default)
    empty: "no monitors in this session", missing: "monitor not found",
    name: (m) => m.description || m.command || m.task,
    card: (m) => monitorCard(m), detail: (wrap, m) => renderMonitorDetail(wrap, m),
  },
  jobs: {
    api: "jobs", list: "jobs", grid: "jobsGrid",
    focus: "jobFocus", tabEl: "jobTab",
    countField: "job_count", route: "j", glyph: "◷", label: "jobs",
    scoped: true,          // …as do background jobs
    empty: "no background jobs in this session", missing: "job not found",
    name: (j) => firstLine(j.command) || j.task,
    card: (j) => jobCard(j), detail: (wrap, j) => renderJobDetail(wrap, j),
  },
};

/* Drop the cached rows of every section that FOLLOWS AGENT SCOPE, on a scope
   change. Their contents belong to the selected actor scope, and the tab
   badge prefers a cached list's LENGTH to the served count (the list is the
   authority once you've opened the tab) — so entering an agent kept showing the
   previous scope's numbers until you opened that tab yourself, which is the one
   thing the badge exists to save you from ("the counter for the monitors and the
   background jobs are not getting properly updated when I go to subagents").
   Table-driven, so a future scoped section is covered by its own `scoped` flag;
   the cache never outlives its scope. */
function resetScopedSections() {
  const sessionView = S.sessionView;
  if (!sessionView) return;
  for (const [kind, sec] of Object.entries(SECTIONS)) {
    if (!sec.scoped) continue;
    sessionView[sec.list] = null;
    sessionView[sec.focus] = null;
  }
  sessionView.jobOut = null;                       // the output cache is the scope's too
}

/* live-first, then most-recently-started on top — the order every section
   grid uses */
function sortedItems(items) {
  return [...items].sort((x, y) => (!!y.live - !!x.live)
    || ((y.started_at || 0) - (x.started_at || 0)));
}

function loadSection(kind) {
  const sec = SECTIONS[kind], sessionView = S.sessionView, sessionId = S.currentSessionId;
  if (!sessionView || !sessionId) return;
  setSectionCount(kind, sectionCount(sec, sessionView));
  if (sec.repaint) { sec.repaint(); return; }
  if (sessionView[sec.focus]) repaintSectionDetail(kind);
  else renderSectionGrid(kind);
}

function renderSectionGrid(kind) {
  const sec = SECTIONS[kind], sessionView = S.sessionView;
  if (!(sessionView && sessionView.tab === kind && sessionView[sec.grid] && sessionView[sec.grid].isConnected))
    return;
  sessionView[sec.grid].textContent = "";
  const items = sessionView[sec.list] || [];
  if (!items.length) {
    sessionView[sec.grid].append(el("div", "empty", sec.empty));
    return;
  }
  for (const it of sortedItems(items)) sessionView[sec.grid].append(sec.card(it));
}

/* The tab badge uses the global summary count or the focused snapshot's exact
   list length. */
function setSectionCount(kind, n) {
  const sec = SECTIONS[kind];
  return setTabBadge(sec.countField, sec.tabEl, n);
}

function updateSectionCount(kind, n) {
  const sessionView = setSectionCount(kind, n);
  // is false while a note viewer is open) — don't refresh under it
  const sec = SECTIONS[kind];
  const showing = sec.showing ? sec.showing() : true;
  if (sessionView && sessionView.tab === kind && showing) loadSection(kind);
}

/* Open one item's drill-down (router #/s/<sessionId>/<route>/<task>). */
function showSection(kind, sessionId, task, agent) {
  const sec = SECTIONS[kind];
  // a scoped detail (…/a/<actorId>/m/<task>) enters that agent's scope first, so
  // the list this task is looked up in is the agent's, not the lead's
  if (S.currentSessionId !== sessionId || (agent || "") !== ((S.sessionView && S.sessionView.agent) || ""))
    showSession(sessionId, kind, agent);
  const sessionView = S.sessionView;
  if (!sessionView) return;
  sessionView.tab = kind.slice(0, -1) + ":" + task;      // "monitor:<task>" / "job:<task>"
  sessionView[sec.focus] = task;
  // no tab-bar entry is "<kind>:<task>", so light the section's own tab (the
  // same "you are here" cue the agents drill-down restores on its tab)
  const re = new RegExp("\\/" + kind + "$");
  $view.querySelectorAll(".tabs a").forEach(a =>
    a.classList.toggle("on", re.test(a.getAttribute("href") || "")));
  updateRunning();
  if (sessionView[sec.list]) repaintSectionDetail(kind);
  else loadSection(kind);        // direct navigation / reload
}

function repaintSectionDetail(kind) {
  const sec = SECTIONS[kind], sessionView = S.sessionView;
  if (!sessionView || !sessionView[sec.focus] || !sessionView.body) return;
  const task = sessionView[sec.focus];
  const item = (sessionView[sec.list] || []).find(x => x.task === task);
  resetBody();
  sessionView.body.append(sectionCrumbs(kind, S.currentSessionId, item || { task: task }));
  const wrap = el("div");
  sessionView.body.append(wrap);
  if (!item) { wrap.append(el("div", "empty", sec.missing)); return; }
  sec.detail(wrap, item);
}

/* The drill-down breadcrumb — <glyph> <label> (back to the list) › this item. */
function sectionCrumbs(kind, sessionId, item) {
  const sec = SECTIONS[kind];
  const nav = el("div", "crumbs");
  const back = el("a", "crumb");
  back.href = (S.sessionView && S.sessionView.agent)
    ? agentHref(sessionId, S.sessionView.agent, kind)          // back to the SCOPED list
    : "#/s/" + encodeURIComponent(sessionId) + "/" + kind;
  back.title = "back to the " + sec.label + " list";
  back.append(el("span", "cg", sec.glyph), tnode(" " + sec.label));
  const cur = el("span", "crumb cur");
  cur.append(el("span", "cg", sec.glyph), tnode(" " + sec.name(item)));
  nav.append(back, el("span", "csep", "›"), cur);
  return nav;
}

/* The IDENTITY panel of a monitor / background-job drill-down: kind pill +
   status, the description, the COMMAND, then the per-kind meta grid.

   ONE builder, because the two tabs are one machine (SECTIONS) showing one kind
   of thing — a long-running command with a lifecycle — and the two hand-written
   panels had drifted into presenting the same facts differently ("they kinda
   look totally different … make the job the same"). `pill`/`rows` are all that
   genuinely differs. The COMMAND is the same block in both, and it is now the
   same block the MIRROR paints: highlighted and pretty-printed by the dashboard
   presenter. `command` still rides along as text for the card titles and
   the crumb, which must stay single-line. */
function detailInfo(item, pill, status, rows) {
  const [sttxt, stcls] = status;
  const info = el("div", "mdetail");
  const h = el("div", "mdhead");
  h.append(el("span", "k " + pill[0], pill[1]), el("span", stcls, sttxt));
  info.append(h);
  if (item.description) info.append(el("div", "mdesc", item.description));
  info.append(el("div", "lbl", item.source === "ws" ? "websocket" : "command"));
  if (item.cmd_html && item.source !== "ws") {
    // server-rendered (escaped at its leaf, like every other served block)
    const box = el("div", "jcmd");
    box.innerHTML = item.cmd_html;
    info.append(box);
  } else if (item.command) {
    info.append(pre(item.command));          // a ws URL / a pre-field row
  } else {
    // the field is shown even when EMPTY: "no command recorded" is a fact about
    // the job (a Ctrl+B conversion paints its command in the foreground group),
    // and a section that silently disappears reads as a different layout
    info.append(el("div", "empty", "(command not recorded)"));
  }
  const grid = el("div", "mmeta");
  const add = metaAdder(grid);
  rows(add);
  info.append(grid);
  return info;
}

function renderMonitorDetail(container, m) {
  const info = detailInfo(m, ["k-monitor", "◉ monitor"], monitorStatus(m), (add) => {
    add("task", m.task);
    add("lifetime", m.persistent ? "persistent"
      : (m.timeout_ms ? "≤" + dur(m.timeout_ms / 1000) : "—"));
    add("events", m.event_count);
    if (m.started_at) add("started", new Date(m.started_at * 1000).toLocaleString());
    if (m.ended_at) add("ended", new Date(m.ended_at * 1000).toLocaleString());
    if (m.started_at && m.ended_at) add("duration", dur(m.ended_at - m.started_at));
    else if (m.started_at && m.live) add("running for", ago(m.started_at));
    add("end reason", m.end_reason);
  });
  container.append(info);

  const evwrap = el("div", "mevents");
  const evs = m.events || [];
  const label = m.events_truncated
    ? "events (recent " + evs.length + " of " + m.event_count + ")" : "events";
  evwrap.append(el("div", "mhead", label));
  if (!evs.length)
    evwrap.append(el("div", "empty", m.live ? "no events yet — waiting" : "no events fired"));
  for (const e of evs.slice().reverse()) evwrap.append(monitorEventRow(e));   // newest first
  container.append(evwrap);
}

function monitorEventRow(e) {
  const row = el("div", "mev" + (e.status ? " mev-status" : ""));
  if (e.ts) row.append(el("span", "mts", new Date(e.ts * 1000).toLocaleTimeString()));
  const txt = e.status
    ? ("stream " + e.status + (e.summary ? " · " + e.summary : ""))
    : (e.event || "");
  row.append(el("span", "mtxt", txt));
  return row;
}

/* ---------- background jobs (list tab + drill-down) ---------- */
/* Background operations render as job cards with canonical lifecycle, command,
   and output facts. Each card drills into the complete snapshot-backed detail. */

function jobStatus(j) {
  if (j.live) return ["running", "st-run"];
  const er = j.end_reason || "";
  if (er.indexOf("parked") >= 0) return ["ended (session end)", "st-ok"];
  if (er.indexOf("backstop") >= 0 || er.indexOf("timeout") >= 0)
    return ["ended · timed out", "st-warn"];
  if (!er && j.ended_at == null) return ["unknown", "st-warn"];
  return ["finished", "st-ok"];   // writer-gone / vanished = normal completion
}

function jobCard(j) {
  const [sttxt, stcls] = jobStatus(j);
  const card = el("a", "acard");
  card.dataset.st = stcls;
  card.href = sectionHref(S.currentSessionId, "j", j.task);
  const name = firstLine(j.command) || j.task;
  card.append(el("div", "actorId", "◷ " + name));
  card.append(el("div", "desc", j.task));
  const meta = el("div", "meta");
  meta.append(el("span", stcls, sttxt));
  if (j.lines != null) meta.append(el("span", "", j.lines + " lines"));
  if (j.started_at && j.ended_at) meta.append(el("span", "", dur(j.ended_at - j.started_at)));
  else if (j.started_at) meta.append(el("span", "", ago(j.started_at)));
  card.append(meta);
  return card;
}

function renderJobDetail(container, j) {
  // …the SAME identity panel a monitor gets (detailInfo): same pill row, same
  // command block, same meta grid — only the rows and the payload below differ.
  const info = detailInfo(j, ["k-job", "◷ background"], jobStatus(j), (add) => {
    add("task", j.task);
    add("lines", j.lines);
    if (j.started_at) add("started", new Date(j.started_at * 1000).toLocaleString());
    if (j.ended_at) add("ended", new Date(j.ended_at * 1000).toLocaleString());
    if (j.started_at && j.ended_at) add("duration", dur(j.ended_at - j.started_at));
    else if (j.started_at && j.live) add("running for", ago(j.started_at));
    add("end reason", j.end_reason);
  });
  container.append(info);

  // Output is part of the canonical job projection in the focused snapshot.
  const outwrap = el("div", "mevents");
  outwrap.append(el("div", "mhead", "output"));
  const box = el("div", "joutput");
  // Seed from the last snapshot text so a repaint or navigation never flashes
  // over output that was just on screen; only a first-ever open shows it
  const sessionView = S.sessionView;
  const cached = sessionView && sessionView.jobOut && sessionView.jobOut.task === j.task
    ? sessionView.jobOut.text : null;
  if (cached != null) paintJobOutput(box, j, cached);
  else box.append(el("div", "empty", "loading output…"));
  outwrap.append(box);
  container.append(outwrap);
  updateJobOutput(j, box);
}

function paintJobOutput(box, j, t) {
  box.textContent = "";
  box.append(t.trim() ? pre(t) : el("div", "empty",
    j.live ? "no output yet" : "(no output)"));
}

/* Swap snapshot output into `box` only when the text changed, preserving DOM
   selection and scroll position while the projection is quiet. */
function updateJobOutput(j, box) {
  const sessionView = S.sessionView, sessionId = S.currentSessionId;
  if (!box.isConnected || S.currentSessionId !== sessionId || S.sessionView !== sessionView) return;
  const text = j.output || "";
  const previous = sessionView.jobOut && sessionView.jobOut.task === j.task
    ? sessionView.jobOut.text : null;
  sessionView.jobOut = { task: j.task, text };
  if (text !== previous) paintJobOutput(box, j, text);
}

/* ---------- agent scope ---------- */

/* The href of one tab in agent scope — the scoped route
   (#/s/<sessionId>/a/<actorId>/<tab>) that showSession's router entry reads back. Kept
   beside the crumbs because the tab bar and the "← session" link are the only
   places that spell the scoped URL. */
function agentHref(sessionId, actorId, tab) {
  return "#/s/" + encodeURIComponent(sessionId) + "/a/" + encodeURIComponent(actorId)
    + (tab && tab !== "mirror" ? "/" + tab : "");
}

/* The href of ONE monitor/job detail, keeping whatever scope its card was
   listed under: `#/s/<sessionId>/m/<task>` for the lead's, nested under the agent's
   route for a scoped one, so a reload/share lands on the same list. */
function sectionHref(sessionId, route, task) {
  const a = (S.sessionView && S.sessionView.agent) || "";
  const base = "#/s/" + encodeURIComponent(sessionId)
    + (a ? "/a/" + encodeURIComponent(a) : "");
  return base + "/" + route + "/" + encodeURIComponent(task);
}

/* Empty the tab body and lay down what belongs ABOVE every tab's content: in
   agent scope, the way back out of it. THE one door for clearing the body, so
   that "the scope crumb is always there" is a property of the reset rather than
   a line each painter has to remember.

   It didn't, and that was the bug: the crumbs were appended once by
   renderSessionChrome, and every painter that later replaced the body wholesale
   — a monitor/job drill-down — wiped them and put
   only its OWN crumb back. So opening one of an agent's background jobs left you
   inside that agent with nothing on screen saying so and no way up but the
   browser's Back ("when I click on monitors or jobs on a subagent, I still want
   to have that breadcrumb — it never goes away"). */
function resetBody() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.body) return;
  sessionView.body.textContent = "";
  if (sessionView.agent)
    sessionView.body.append(agentCrumbs(S.currentSessionId, sessionView.agent,
                                (sessionView.agents || []).find(a => a.agent_id === sessionView.agent)));
}

/* The agent-hierarchy breadcrumb in agent scope — the MAIN agent → this agent
   (docs/dashboard.md, *Breadcrumbs*). Just the two nodes (the hierarchy is one
   level deep — a session's flat agent list): the main agent is a link back to
   its own mirror (#/s/<sessionId>), labelled by the session's title; the current
   agent is the highlighted end node. Icons: ◆ the main agent, ◇/◈ the
   subagent/teammate. Clicking the main node is how you leave scope. */
function agentCrumbs(sessionId, actorId, rec) {
  const nav = el("div", "crumbs");
  const meta = (S.sessionView && S.sessionView.meta) || {};
  const sesName = meta.title || directoryName(meta.workingDirectory) || shortSid(sessionId);
  const main = el("a", "crumb");
  main.href = "#/s/" + encodeURIComponent(sessionId);       // the mirror = the main agent
  main.title = "back to the main agent";
  main.append(el("span", "cg", "◆"), tnode(" " + sesName));
  const cur = el("span", "crumb cur");
  cur.append(el("span", "cg", rec && rec.kind === "teammate" ? "◈" : "◇"),
             tnode(" " + ((rec && rec.desc) || actorId)));
  nav.append(main, el("span", "csep", "›"), cur);
  return nav;
}

function firstLine(s, n) {
  s = (s || "").trim();
  const nl = s.indexOf("\n");
  if (nl >= 0) s = s.slice(0, nl);
  return s.length > (n || 160) ? s.slice(0, n || 160) + "…" : s;
}

function pre(text) { const p = el("pre"); p.textContent = text == null ? "" : String(text); return p; }

/* ---------- errors tab ---------- */

function renderErrorsInto(container) {
  container.textContent = "";
  const rows = (S.sessionView && S.sessionView.errors) || [];
  const wrap = el("div", "errs");
  if (!rows.length) wrap.append(el("div", "empty", "no swallowed exceptions — clean session"));
  for (const row of rows) {
    const error = el("div", "err");
    error.append(el("div", "h", "⚠ " + (row.component || "?") + " · "
                    + (row.action || "?")
                    + (row.timestamp ? " · "
                       + new Date(row.timestamp * 1000).toLocaleString() : "")));
    if (row.traceback) error.append(pre(row.traceback));
    wrap.append(error);
  }
  container.append(wrap);
}

/* ---------- viewport diagnostics (?vpdiag) ----------
   A live readout of the numbers a remote device (an iPad) is actually
   rendering with — layout vs visual viewport, scale, screen, dpr — for
   debugging zoom/fit reports that headless WebKit can't reproduce. Doubles
   as a staleness probe: the overlay only exists in THIS build of app.js,
   so "no overlay" == the device is loading stale assets. */


// ---- the pinned goal card (docs/dashboard.md, *Web goal*) -------------------
// A harness goal puts the session into autonomous mode toward an objective.
// Canonical goal facts from the owning harness are projected into the session
// snapshot and pushed over the existing application SSE stream. Pinned at the
// mirror tab (above tasks), amber while working and green "✓ achieved" once the
// harness confirms; hidden when there is no current goal. Read-only.

function buildGoalCard() {
  const wrap = el("div", "goalwrap");
  S.sessionView.goalEl = wrap;
  renderGoal();
  return wrap;
}

function renderGoal() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.goalEl) return;
  const wrap = sessionView.goalEl;
  wrap.textContent = "";
  const goal = (sessionView.meta && sessionView.meta.goal) || null;
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
// The session's canonical task list, pinned at the very top of the mirror tab.
// Each harness owns its native task grammar and emits shared task facts; this
// page reads only the shared snapshot. Read-only as far as the TASKS go: unlike ask/plan
// there is no dialog to drive — the TUI has no modal to answer, and nothing here
// ever completes or deletes a task. Completed tasks render struck-through and
// dimmed; the in_progress one carries the accent and shows its activeForm.
//
// The one gesture is the header's ✕: DISMISS a finished list, so a card whose
// work is done stops taking the top of the mirror. Purely visual and stored
// server-side (dashboard/prefs.py `tasks-hidden`), so it holds across devices
// and across park — and offered only once EVERY task is completed (the button is
// disabled otherwise and the POST 409s). It needs no un-hide: the dismissal is
// stamped with that finished list's ids, so the next task Claude Code creates —
// or a completed one re-opened — brings the card straight back.

function buildTasksCard() {
  const wrap = el("div", "taskswrap");
  S.sessionView.tasksEl = wrap;
  renderTasks();
  return wrap;
}

// The ✕ that dismisses a finished tasks card. Two-step confirm (armConfirm, the
// same "a misclick here costs you something, so ask once" rule as ✕ close /
// ⊜ compact) — the arm dies if a `tasks` event rebuilds the card mid-confirm,
// which is correct: a list that just changed is a different list to dismiss.
function tasksHideBtn(tasks, allDone) {
  const btn = el("button", "taskshide", "✕");
  if (!allDone) {
    btn.disabled = true;
    btn.title = "hide this card once every task is completed";
    return btn;
  }
  btn.title = "hide this finished list (comes back with the next task)";
  armConfirm(btn, "✕", "hide?", () => {
    const sessionId = S.currentSessionId;
    const meta = S.sessionView && S.sessionView.meta;
    if (!meta) return;
    meta.tasks_hidden = true;      // optimistic — the card goes at once
    renderTasks();
    postJSON("/api/sessions/" + encodeURIComponent(sessionId)
             + "/application/tasks-hidden",
             { hidden: true },
             { audit: "tasks-hide", sessionId, auditData: { tasks: tasks.length } })
      .then(() => toast("done", "tasks hidden",
                        "the card returns with the next task"))
      .catch(e => {
        // the write never landed — put the card back rather than leave the page
        // showing a dismissal no other device will ever see
        if (S.currentSessionId === sessionId && S.sessionView && S.sessionView.meta) {
          S.sessionView.meta.tasks_hidden = false;
          renderTasks();
        }
        toast("ask", "hide failed", (e && e.error) || "");
      });
  });
  return btn;
}

function renderTasks() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.tasksEl) return;
  const wrap = sessionView.tasksEl;
  wrap.textContent = "";
  const tasks = (sessionView.meta && sessionView.meta.tasks) || null;
  // `tasks_hidden` is the SERVER's verdict (read/session.py tasks_hidden — the
  // dismissal AND whether it still applies to this list), never a local flag
  wrap.hidden = !tasks || !tasks.length || !!(sessionView.meta && sessionView.meta.tasks_hidden);
  if (wrap.hidden) return;
  const done = tasks.filter(t => t.status === "completed").length;
  const card = el("div", "taskscard");
  const head = el("div", "taskshead");
  head.append(el("span", "taskstitle", "tasks"));
  head.append(el("span", "taskscount", done + "/" + tasks.length + " done"));
  head.append(tasksHideBtn(tasks, done === tasks.length));
  card.append(head);
  const list = el("div", "tasklist");
  tasks.forEach(t => {
    const st = t.status === "completed" ? "done"
             : t.status === "in_progress" ? "active" : "pend";
    const row = el("div", "taskrow " + st);
    row.append(el("span", "taskmark",
                  st === "done" ? "✓" : st === "active" ? "▸" : "○"));
    row.append(el("span", "taskid", "#" + (t.label || t.id || "?")));
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
