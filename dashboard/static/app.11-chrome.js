"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.

/* The session view's chrome, as NAMED PHASES (the styleguide's shape for a long
   builder — small functions named for what they build, one visible order):
   identity chips → action buttons → quick-command row → the live rows the SSE
   patchers fill → the tab strip → the open tab's body. Each phase returns its
   element and parks on `ses` whatever the patchers reach for later.

   It was one 350-line function, which is a poor place to look for any single one
   of those six jobs: the ✕ close button sat 130 lines below the identity chips it
   shares nothing with, and "does the effort picker exist when parked?" meant
   scrolling for the live gate rather than reading one signature. */
function renderSessionChrome(tab) {
  const ses = S.ses;
  if (!ses) return;
  // In AGENT SCOPE the header keeps showing that agent (its own scoreboard,
  // name and status); outside it there is nothing focused. Derived from the
  // scope rather than cleared, so a tab switch inside scope stays in scope.
  ses.agentFocus = ses.agent ? { aid: ses.agent, data: null } : null;
  ses.monitorFocus = null;    // …nor monitor-focused (a drill-down sets it again)
  ses.jobFocus = null;        // …nor background-job-focused
  clearSectionPoll("monitors");   // leaving a secondary tab stops its live poll
  clearSectionPoll("jobs");
  const meta = ses.meta || {};
  // a SCOPED extension tab (memory: in-scope aggregator-adapters sessions
  // only) — a deep-link / stale bookmark to it elsewhere falls back to the mirror
  if (EXT[tab] && EXT[tab].scopeField && !meta[EXT[tab].scopeField]) tab = "mirror";
  $view.textContent = "";

  const head = el("div", "shead");
  head.dataset.tab = meta.tab || "";    // state tint; live via setBadge()
  head.append(chromeIdentity(ses, meta));
  // the action buttons are mounted in the PAGE HEADER (mountHeaderActions), not
  // here — the top-right corner the list page fills with ▦ stats / ⛶ / +
  // session is dead space inside a session, and those gestures aren't about the
  // session you're reading.
  mountHeaderActions(ses, meta);
  head.append(...chromeLiveRows(ses));
  $view.append(head);
  updateStatsRow();
  updateRunning();

  $view.append(chromeTabs(ses, meta, tab));
  const body = el("div");
  ses.body = body;
  $view.append(body);
  resetBody();                // the way out of agent scope, above every tab
  chromeBody(ses, tab, body);
  applyAgentActionVis();      // session-only header actions don't apply in scope
}

/* l1: who this session IS — title, state badge, directory, sid, checkout,
   account. Every chip is static except the three parked on `ses`, which the
   `title` / `tab` / `git` SSE events patch in place. */
function chromeIdentity(ses, meta) {
  const l1 = el("div", "l1");
  const projSpan = el("span", "proj",
                      meta.title || (meta.cwd ? proj(meta) : shortSid(S.cur)));
  ses.projEl = projSpan;                // the `title` SSE + inline rename target
  l1.append(projSpan);
  const badge = el("span", "badge");
  ses.badge = badge;
  setBadge(badge, meta.tab || "");
  l1.append(badge);
  // "live" goes unsaid (state tint + badge carry it); parked still shows
  if (!meta.live) l1.append(el("span", "chip2 parked", "parked"));
  if (meta.cwd) {
    // just the directory name (basename) — the full path rides the tooltip
    const cwdChip = el("span", "sid", meta.cwd.split("/").filter(Boolean).pop());
    cwdChip.title = meta.cwd;
    l1.append(cwdChip);
  }
  const sidChip = el("span", "sid copysid", shortSid(S.cur));
  sidChip.title = "click to copy the full session id";
  sidChip.onclick = () => copySid(S.cur);
  l1.append(sidChip);
  // the checkout this session runs in — live via the `git` SSE event
  const gitc = el("span", "gitchip");
  ses.gitChip = gitc;
  setGitChip(gitc, meta.git);
  l1.append(gitc);
  // which subscription account this chat runs under (◈ c2 · claude-01)
  const acc = meta.account || {};
  if (acc.slug || acc.label) {
    const chip = el("span", "acctchip");
    chip.append(el("span", "ag", "◈"), tnode(
      " " + (acc.slug ? acc.slug + " · " + acc.label : acc.label)));
    const u = meta.usage;
    if (u) {
      const parts = [];
      if (typeof u.five_hour === "number") parts.push("5h " + u.five_hour + "%");
      if (typeof u.seven_day === "number") parts.push("7d " + u.seven_day + "%");
      if (parts.length) chip.append(el("span", "ausage", parts.join(" · ")));
    }
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
function mountHeaderActions(ses, meta) {
  if (!$sessact) return;
  clearHeaderActions();
  for (const row of [chromeQuickCmds(ses, meta), chromeActions(ses, meta)])
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

// Claude Code refuses `/compact` until a conversation has something to
// summarize ("Not enough messages to compact"), so ⊜ compact greys out below
// this many of YOUR prompts (meta.prompts, capped server-side; None/absent =
// nothing to conclude → no gate). Deliberately the lowest floor that catches
// the reported case — you sent one message and compact bounced: past it the TUI
// stays the authority, since its exact rule is its own and unpublished.
const COMPACT_MIN_PROMPTS = 2;

// Why every terminal-typing action is out for a parked session: it has no
// window to type into (the server rejects them too — this just says so first).
const NO_WINDOW = "this session is parked — there is no terminal to type into";

/* The header bar's SECOND row: the session-level gestures. rename / migrate /
   alerts work live AND parked (they touch the transcript or a dashboard pref,
   not the terminal); rewind / stop / close need a window to type into and close
   the row on its destructive end, and resume is the parked-only counterpart. */
function chromeActions(ses, meta) {
  const act = el("div", "actrow");
  const windowed = !!(meta.live && meta.kitty_window_id);
  // rename: deliberately OUTSIDE the live gate — it works for live AND parked
  // sessions (the server appends the agent-name naming record to the
  // transcript; a live kitty tab also retitles in place — docs/dashboard.md
  // "Web rename")
  const ren = el("button", "sstop actses", "✎ rename");
  ren.title = "rename this session (resume picker + tab)";
  ren.onclick = () => startRenameHeader();
  act.append(ren);
  // migrate: hand this session to the other subscription account — the same
  // detached migrator as the automatic rate-limit path (docs/relimit.md
  // *Manual migrate*): live → the tab swaps (close, park, resume under the
  // other alias); parked → it just relaunches there. Immediate, no confirm
  // (like ■ stop), and like rename it works live AND parked.
  const mig = el("button", "sstop actses", "⇆ migrate");
  mig.title = "migrate this session to another account";
  mig.onclick = () => lockDuring(mig, migrateSession);
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
    postJSON("/api/session/" + encodeURIComponent(S.cur) + "/notify",
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
                                  () => ses.stopMode(liveTab()));
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
  // awaiting-command, where an Esc declines the open dialog. ↶ rewind is the
  // exact complement: it drives the TUI's checkpoint menu, which needs an idle
  // session — rewindSession bails on a busy or red tab with a toast, and the
  // button now says so before the click rather than after it.
  ses.stopMode = (t) => {
    gate(stop, windowed && BUSY_TABS.includes(t),
         !windowed ? NO_WINDOW
           : t === "awaiting-command" ? "a question is waiting — answer it in the card"
           : "nothing is running to stop");
    gate(rew, windowed && !BUSY_TABS.includes(t) && t !== "awaiting-command",
         !windowed ? NO_WINDOW
           : t === "awaiting-command" ? "a question is waiting — answer it first"
           : "a turn is running — stop it first, then rewind");
  };
  ses.stopMode(liveTab());
  act.append(rew, stop);       // rewind before stop — the row ends destructive
  // close: closes the session's kitty tab — a graceful stop (Claude Code
  // exits on the HUP and SessionEnd runs the normal lifecycle).
  // Two-step confirm: first click arms for 4s, second click fires.
  const cls = el("button", "sstop actses", "✕ close");
  cls.dataset.tip = "close this session's terminal tab";
  gate(cls, windowed, NO_WINDOW);   // nothing to close once it's parked
  armConfirm(cls, "✕ close", "close session?", () => {
    cls.disabled = true;
    cls.textContent = "closing…";
    const sid = S.cur;
    // optimistic close: beacon the `close` lifecycle (web-hint op=close) and
    // navigate back to the list on the POST ack — the list card shows greyed
    // 'closing…' (S.closing) until reconcileCloses parks it from the poll.
    closeBegin(sid);
    closeSession(sid, "header")
      .then(() => {
        toast("done", "session closed", "terminal tab closed");
        // the session just ended — back to the list, unless the user
        // already navigated elsewhere while the POST was in flight
        if (S.cur === sid) location.hash = "#/";
      })
      .catch(e => {
        closeSettle(sid, "dropped", { reason: "failed" });
        gate(cls, true);
        cls.textContent = "✕ close";
        clientFail(sid, "close", e);   // a lost/rejected /stop the audit can't see
        toast("ask", "close failed", (e && e.error) || "");
      });
  });
  act.append(cls);
  // resume (parked, with a cwd): reopen the new-session form preset to
  // `claude --resume <this sid>` in this session's directory
  if (!meta.live && meta.cwd) {
    const res = el("button", "sresume actses", "↻ resume");
    res.title = "start a new tab resuming this conversation";
    res.onclick = () => openNewSession(meta.cwd, S.cur);
    act.append(res);
  }
  return act;
}

/* The header bar's LEADING row: the quick commands — the model/effort pickers +
   compact, each typing the TUI's own slash command into the session
   (docs/dashboard.md, *Web quick commands*). First because these are the knobs
   you reach for mid-conversation. Every one needs a window to type into, so on a
   parked session they are all greyed rather than absent (see gate()). */
function chromeQuickCmds(ses, meta) {
  const act2 = el("div", "actrow");
  const windowed = !!(meta.live && meta.kitty_window_id);
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
  ses.modelBtn = mdl;
  setModelBtn(mdl);
  mdl.dataset.tip = "switch the model (/model — also saves as your new-session default)";
  mdl.onclick = () => openQuickMenu(mwrap, "model", MODEL_CHOICES,
                                    curModelFamily());
  mwrap.append(mdl);
  // effort: dropdown picker (current effort is config-only — not readable
  // from any transcript, see plugins/claude_code/model.py — so no label)
  const ewrap = el("span", "qcwrap actses");
  const eff = el("button", "sstop");
  ses.effortBtn = eff;
  setEffortBtn(eff);
  eff.dataset.tip = "set the reasoning effort (/effort — also saves as your new-session default)";
  eff.onclick = () => openQuickMenu(ewrap, "effort", EFFORT_CHOICES,
                                    (ses.meta && ses.meta.effort) || "");
  ewrap.append(eff);
  act2.append(mwrap, ewrap, cpt);
  // a red tab = a modal dialog is up — pasted text would land IN it (the
  // server 409s too; disabling just says so up front). Live via the same
  // SSE tab event as stopMode.
  //
  // ⊜ compact carries one gate of its own: Claude Code refuses to compact a
  // conversation that has barely started, so a session with fewer than
  // COMPACT_MIN_PROMPTS of your prompts greys it out instead of typing a
  // command the TUI will bounce (`prompts`, patched live by its SSE event).
  ses.quickMode = (t) => {
    const dialog = t === "awaiting-command";
    const ok = windowed && !dialog;
    const why = !windowed ? NO_WINDOW
                          : "a question is waiting — answer it in the card";
    for (const b of [mdl, eff]) gate(b, ok, why);
    const n = (ses.meta && ses.meta.prompts);
    const thin = typeof n === "number" && n < COMPACT_MIN_PROMPTS;
    gate(cpt, ok && !thin,
         !ok ? why : "not enough conversation to compact yet");
  };
  ses.quickMode(liveTab());
  return act2;
}

/* The three header rows that start EMPTY and are filled by the patchers
   (updateStatsRow / the ctx bar / updateRunning), in paint order. */
function chromeLiveRows(ses) {
  const sr = el("div", "statsrow");
  ses.statsRow = sr;
  ses._statsSig = null;      // fresh (empty) row — force the next paint through
  const cr = el("div", "ctxrow");     // the main thread's ctx bar, its own row
  ses.ctxRow = cr;
  const rr = el("div", "runrow");
  ses.runRibbon = rr;
  return [sr, cr, rr];
}

/* The tab strip. Each count is the fetched list's length once we have it, else
   the cheap eager count the overview payload carried — so a badge is right
   before its tab has ever been opened. The tabs whose badge is patched live are
   parked on `ses`. */
function chromeTabs(ses, meta, tab) {
  const tabs = el("div", "tabs");
  const scoped = ses.agent || "";
  const mk = (key, label, count) => {
    const a = el("a", key === tab ? "on" : "");
    // in agent scope every tab stays in scope (docs/dashboard.md *Agent
    // scope*) — the `agents` tab is the one that must not, since a list of the
    // SESSION's agents is what you navigate between them with
    a.href = (scoped && key !== "agents")
      ? agentHref(S.cur, scoped, key)
      : "#/s/" + encodeURIComponent(S.cur) + (key === "mirror" ? "" : "/" + key);
    a.append(tnode(label));
    if (count) a.append(el("span", "count", String(count)));
    tabs.append(a);
    return a;
  };
  mk("mirror", "mirror");
  mk("agents", "agents", (ses.agents || []).length);
  // the ◉ monitors count: the actual list length once fetched, else the cheap
  // eager streams count (monitor_count) so the tab shows before the tab is opened
  ses.monTab = mk("monitors", "monitors",
                  ses.monitors ? ses.monitors.length : (meta.monitor_count || 0));
  // ◷ background jobs — actual list length once fetched, else the cheap eager count
  ses.jobTab = mk("jobs", "jobs",
                  ses.jobs ? ses.jobs.length : (meta.job_count || 0));
  // …then every EXTENSION tab anchored here (memory — ❖ wiki notes touched,
  // gated by its scopeField so only in-scope sessions get the tab at all)
  for (const anchor of ["mirror", "agents", "monitors", "jobs"])
    extTabs(ses, meta, mk, anchor);
  ses.errTab = mk("errors", "errors", meta.error_count || 0);   // live ⚠ count patches it
  // errors and the unscoped extension tabs (memory) have no agent dimension (a
  // note is the team's, an error is a script's), so in agent scope they still
  // show the SESSION's — said out loud rather than left ambiguous.
  if (scoped && (tab === "errors" || (EXT[tab] && !EXT[tab].scoped)))
    tabs.append(el("span", "tabnote", "session-wide"));
  return tabs;
}

/* The open tab's body. The mirror tab is the composite one (cards → composer →
   view bar → the stream/rail split); the rest are a grid or a renderer plus
   the fetch that fills it. */
function chromeBody(ses, tab, body) {
  if (tab === "mirror") {
    // The pinned cards and the composer are the SESSION's — its goal, its task
    // list, its pending dialogs, its input box. In agent scope they would all
    // be lies about what you're looking at (worst of all the composer, which
    // types to the lead, not the agent you drilled into), so the scoped mirror
    // is the stream and its view bar alone.
    if (!ses.agent) {
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
      if (!ses.composer.disabled && !IS_IPAD) ses.composer.focus();
    }
    body.append(buildViewBar());
    const split = el("div", "split");
    // the transcript column: queued messages pinned ABOVE the newest-first
    // stream (so incoming activity never buries them) until they're delivered
    const scol = el("div", "scol");
    scol.append(buildQueuePin());
    scol.append(ses.stream);
    split.append(scol);
    const rail = el("div", "rail");
    ses.rail = rail;
    split.append(rail);
    body.append(split);
    updateAgents();
    updateMoreBtn();                      // the load-older affordance at the bottom
    updateShownCount();                   // count items already in the stream
  } else if (tab === "agents") {
    const wrap = el("div", "sgrid");
    ses.agentsGrid = wrap;
    body.append(wrap);
    updateAgents();
  } else if (tab === "monitors" || tab === "jobs") {
    // the two grid sections are one machine (SECTIONS) — same cached-or-
    // placeholder paint, same (re)fetch that also starts the live poll
    const sec = SECTIONS[tab];
    const wrap = el("div", "sgrid");
    ses[sec.grid] = wrap;
    body.append(wrap);
    if (ses[sec.list]) renderSectionGrid(tab);   // cached from a prior fetch
    else wrap.append(el("div", "empty", "loading " + sec.label + "…"));
    loadSection(tab);                     // (re)fetch fresh + start the live poll
  } else if (EXT[tab]) {
    EXT[tab].body(ses, body);             // the extension's own tab renderer
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
function statsSig(ses) {
  const f = ses.agentFocus;
  if (f) {
    const d = agentUsage(ses);
    const rec = (ses.agents || []).find(a => a.agent_id === f.aid) || {};
    return "A|" + [f.aid, rec.kind, rec.desc, rec.ended_at, rec.started_at,
      rec.tools, rec.model, rec.effort, rec.end_reason, rec.done,
      d.cost, d.model].join(",")
      + "|" + JSON.stringify(d.usage || {}) + "|" + JSON.stringify(rec.ctx || {});
  }
  const st = ses.stats || {};
  const cost = (ses.costs && ses.costs.total_usd) || st.cost;
  return "S|" + [st.commands, st.failed, st.start, st.paused, st.files,
    st.added, st.removed, st.tk_in, st.tk_out, st.tk_read, st.tk_create, cost,
    st.msg_delivered, st.msg_read, (ses.meta && ses.meta.error_count) || 0,
    ses.meta && ses.meta.model,
    // compaction is a ctx-ROW state, not a stats number, but it lives on the
    // same signature: without it the row never rebuilds when compaction
    // starts (nothing else about the session changes for those ~2 minutes —
    // that is the whole point) and the animation would never appear. `since`
    // is constant while one compaction runs, so this adds no extra rebuilds.
    (ses.compacting && ses.compacting.since) || 0].join(",")
    + "|" + JSON.stringify(ses.ctx || {});
}

function updateStatsRow() {
  const ses = S.ses;
  if (!ses || !ses.statsRow) return;
  const sig = statsSig(ses);
  if (sig === ses._statsSig) return;   // nothing the row shows changed — skip
  ses._statsSig = sig;                 // the teardown (preserves iPad selection)
  const sr = ses.statsRow;
  sr.textContent = "";
  // drilled into a subagent → the scoreboard shows THAT agent, not the session
  // (the "swap scoreboard on click" behaviour). SSE stats/costs/ctx events still
  // land here, but this branch keeps them from clobbering the agent view.
  if (ses.agentFocus) { renderAgentScoreboard(sr, ses.agentFocus); return; }
  const st = ses.stats || {};
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
  const cost = (ses.costs && ses.costs.total_usd) || st.cost;
  if (cost) add("≈", usd(cost), "cost");
  if (st.msg_delivered)
    add("✉", st.msg_delivered + " msgs" +
        (st.msg_read ? " · " + st.msg_read + " read" : ""));
  const errn = (ses.meta && ses.meta.error_count) || 0;
  if (errn) add("", "⚠ " + errn, "warn");
  // the main thread's ctx bar on its own row — live via the `ctx` SSE event
  // the model quick-button's label follows the same ctx probe
  if (ses.modelBtn) setModelBtn(ses.modelBtn);
  paintCtxRow(ses.ctx);
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
  const ses = S.ses;
  if (!ses || !$sessact) return;
  const focused = !!ses.agentFocus;
  $sessact.querySelectorAll(".actses").forEach(b => { b.style.display = focused ? "none" : ""; });
  const stop = $sessact.querySelector(".actstop");
  if (stop) {
    let show = true;
    if (focused) {
      const rec = (ses.agents || []).find(a => a.agent_id === ses.agentFocus.aid);
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
   resolves the freshest agent row from ses.agents each render (so an `agents`
   SSE that finishes the agent updates the status here too) and reads tokens/cost
   from the session payload's `agent_usage` (served whenever a `?agent=` scope is
   in play, so this needs no fetch of its own). The prominent header NAME becomes
   the agent's; the stats row leads with a "← session" link that leaves scope, and
   the ctx row repaints from the agent's own ctx bar. */
/* The scoped agent's own token rollup + priced cost — served on the session
   payload when a `?agent=` is in play (read/session.agent_usage), so the
   scoreboard needs no fetch of its own. {} before meta lands, or for an agent
   with no transcript to fold (a codex run prices itself). */
function agentUsage(ses) {
  return ((ses && ses.meta) || {}).agent_usage || {};
}

function renderAgentScoreboard(sr, focus) {
  const ses = S.ses;
  const rec = (ses.agents || []).find(a => a.agent_id === focus.aid) || {};
  const d = agentUsage(ses);
  const [sttxt, stcls] = agentStatus(rec);
  // the header badge/dot + .shead wash follow THIS agent's status, not the
  // session tab (the session pill said "busy" over a finished subagent).
  setBadgeAgent(ses.badge, sttxt, stcls);
  // the big header name updates to the subagent (the session title returns when
  // renderSessionChrome rebuilds on the way back). Skip during an inline rename.
  if (ses.projEl && !ses.projEl.querySelector("input"))
    ses.projEl.textContent =
      (rec.kind === "teammate" ? "◈ " : "◇ ") + (rec.desc || focus.aid);
  const back = el("a", "backses", "← session");
  back.href = "#/s/" + encodeURIComponent(S.cur);   // the mirror = the main agent
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
  paintCtxRow(rec.ctx, focus.aid);   // the agent's own saturation, same row
}

/* The ctx-saturation row under the scoreboard — its own row, shown only while
   there is an occupancy figure to show (docs/dashboard.md, *Context
   saturation*). One owner for both scoreboards: the session's ctx comes from the
   `ctx` SSE event, a drilled-in agent's from its own record, and the row is
   REPLACED (not appended) on every repaint.

   `aid` names a drilled-in agent. It decides BOTH extras: the compaction
   animation is the SESSION's (compaction folds the main thread's conversation —
   an agent has none of its own to compact, and painting the lead's rehearsal
   over an agent's bar would attribute it to the wrong context), and the
   drain's identity key keeps each agent's bar animating from its own last
   width rather than from whichever bar this row showed before. */
function paintCtxRow(cx, aid) {
  const ses = S.ses;
  if (!ses || !ses.ctxRow) return;
  ses.ctxRow.textContent = "";
  if (cx && cx.used)
    ses.ctxRow.append(ctxBar(cx, true, {
      comp: aid ? null : ses.compacting,
      // S.cur, NOT a field on `ses` — the session object carries no sid, so
      // `ses.sid` would key every session as "s:undefined" and switching
      // sessions would drain the new bar out of the old one's width
      key: (aid ? "a:" + aid : "s:" + S.cur),
    }));
  ses.ctxRow.style.display = cx && cx.used ? "" : "none";
}

/* Live ⚠ error badge — the web sibling of the scorebar's errwatch chip
   (count-only on the fast path; full tracebacks stay behind the errors tab).
   Patches the stats-row chip and the errors-tab count in place (no full
   re-render), and re-fetches the errors list only when that tab is open and
   the count grew. */
function updateErrCount(n) {
  const prev = (S.ses && S.ses.meta && S.ses.meta.error_count) || 0;   // pre-patch
  const ses = setTabBadge("error_count", "errTab", n);
  if (!ses) return;
  updateStatsRow();                  // the ⚠ chip lives in the scoreboard row too
  if (ses.tab === "errors" && n > prev && ses.body) renderErrorsInto(ses.body);
}

/* Patch a tab's count badge AND the cached meta it is rebuilt from, together —
   the shared body of the monitors / jobs / memory / errors counters. Both halves
   are needed: setTabCount paints the badge now, ses.meta[field] is what a later
   renderSessionChrome rebuilds it from (drop that and the badge reverts on the
   next rebuild). Returns the session (null when there's none), so a caller can
   chain its own "…and refresh the list if that tab is open" tail.

   That tail is exactly why each counter comes in two flavours and they must not
   be merged: setXCount is called BY the fetch that just loaded the list (an
   exact length — re-fetching there would loop), updateXCount by the cheap SSE
   count (a refetch is the point). */
function setTabBadge(field, tabKey, n) {
  const ses = S.ses;
  if (!ses) return null;
  if (ses.meta) ses.meta[field] = n;
  setTabCount(ses[tabKey], n);
  return ses;
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
   (sessionapi.running(), grouped by kind), hidden when nothing is running.
   Live-updated by the `running` SSE event. */
function updateRunning() {
  const ses = S.ses;
  if (!ses || !ses.runRibbon) return;
  const run = ses.running || {};
  const rr = ses.runRibbon;
  rr.textContent = "";
  // the running ribbon is session-scoped; hide it while a subagent scoreboard
  // is showing (the header is about that one agent then, not the session)
  if (ses.agentFocus) { rr.style.display = "none"; return; }
  const kinds = RUN_ORDER.concat(
    Object.keys(run).filter(k => !RUN_ORDER.includes(k)));
  let any = false;
  for (const kind of kinds) {
    const rows = run[kind];
    if (!rows || !rows.length) continue;
    const [glyph, label] = RUN_GLYPH[kind] || ["•", kind];
    for (let i = 0; i < rows.length; i++) {
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
  // a slot row with no kind/desc/transcript: an agent whose streamer never
  // ran (hidden auxiliary spawns) — shown dim, after the attributed ones
  return !a.kind && !a.desc && !a.transcript;
}

function sortedAgents(agents) {
  return [...agents].sort((x, y) => (isHusk(x) - isHusk(y))
    || ((x.started_at || 0) - (y.started_at || 0)));
}

function agentCard(a) {
  const [sttxt, stcls] = agentStatus(a);
  const card = el("a", "acard" + (isHusk(a) ? " husk" : ""));
  card.dataset.st = stcls;              // state tint keyed off agent status
  card.href = agentHref(S.cur, a.agent_id, "mirror");   // into AGENT SCOPE
  const name = a.desc || a.agent_id;      // the Task description IS the name
  card.append(el("div", "aid", (a.kind === "teammate" ? "◈ " : "◇ ") + name));
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
  if (a.ctx) card.append(ctxBar(a.ctx));
  return card;
}

function updateAgents() {
  const ses = S.ses;
  if (!ses) return;
  // a focused subagent finishing (running → done) must drop the ■ stop button
  // AND flip its scoreboard status/badge/wash (renderAgentScoreboard reads the
  // fresh agents row) — an `agents` SSE doesn't move statsSig, so re-render here
  // rather than via updateStatsRow's change-gate.
  if (ses.agentFocus) {
    applyAgentActionVis();
    if (ses.statsRow) {
      ses.statsRow.textContent = "";
      renderAgentScoreboard(ses.statsRow, ses.agentFocus);
    }
  }
  const agents = sortedAgents(ses.agents || []);
  if (ses.tab === "mirror" && ses.rail && ses.rail.isConnected) {
    ses.rail.textContent = "";
    if (agents.length) ses.rail.append(el("div", "mhead", "agents"));
    for (const a of agents) ses.rail.append(agentCard(a));
  }
  if (ses.tab === "agents" && ses.agentsGrid && ses.agentsGrid.isConnected) {
    ses.agentsGrid.textContent = "";
    if (!agents.length) ses.agentsGrid.append(el("div", "empty", "no subagents in this session"));
    for (const a of agents) ses.agentsGrid.append(agentCard(a));
  }
  // …and the mirror's agent NOTES carry the same outcome on their dot (they read
  // agentStatus above, so this event is what turns a launch note green when its
  // agent ends — no op is written for that)
  tintAgentNotes();
}

/* ---------- monitors (list tab + drill-down) ---------- */
/* The monitors tab mirrors the agents tab: a grid of monitor cards (each a
   Monitor tool run with its lifecycle state) that drills into a per-monitor
   detail on click. Data comes from plugins.monitors(sid) — the MAIN transcript
   (command/description/events) merged with the audit streams lifecycle state
   (running/ended/duration). Loaded lazily on tab open (a transcript parse), then
   re-fetched on a light poll while any monitor is live — the count badge stays
   fresh live via the cheap `monitors` SSE (docs/dashboard.md, *Monitors tab*). */

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
  card.href = sectionHref(S.cur, "m", m.task);
  const name = m.description || m.command || m.task;
  card.append(el("div", "aid", "◉ " + name));
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

/* ---------- secondary list tabs: the ONE engine ----------------------------

   Monitors and background jobs are the same machine, and it was written twice:
   fourteen near-identical function pairs 200 lines apart, several of them
   byte-identical apart from a parameter name (sortedMonitors/sortedJobs). Fetch
   a list from /api/session/<sid>/<api>, cache it on S.ses, render a card grid or
   one item's detail, poll while anything in it is live, keep a tab badge. The
   SECTIONS descriptor names the seven things that actually differ; everything
   below is generic over it. (The SECONDARY_POLL_MS constant had already been
   unified for exactly this reason — the constant, and then nothing else.)

   An EXTENSION's tab (extRegister below — memory is the first) is a member
   too, for its fetch + badge; it usually renders through its own painter
   (memory: a grid OR an open note viewer) and has no per-item drill-down —
   `repaint` and the absence of `detail` are what say so. */

/* ---------- dashboard EXTENSIONS (the JS half; docs/dashboard.md *Web
   extensions*) ----------

   The server half is dashboard/ext/<name>/ (same `name`). An extension ships
   as its own ordered classic part (app.NN-ext-<name>.js, listed in index.html
   AFTER this file — the manual list IS the registry order) and calls
   extRegister at load with a descriptor:

     name        the tab key = the server ext's NAME (SSE event, <name>_count)
     label       the tab strip label
     after       which built-in tab it follows ("jobs"); errors stays last
     scopeField  meta field gating the tab ("memory_scope"); absent = always on
     scoped      does the tab follow AGENT scope (false ⇒ "session-wide" note)
     init(ses)   stamp the extension's per-session state slots
     body(ses, body)   render the open tab into `body`
     section     merged into SECTIONS (stash/repaint/showing/… — the generic
                 fetch/badge/poll engine covers the rest by convention:
                 api=list=name, tabEl=<name>Tab, countField=<name>_count)
     page        optional top-level page {route, title, render(wrap)} → #/x/<route>

   Lives HERE rather than app.00 because registration MERGES into SECTIONS —
   the tab machinery this file owns; every reader below runs long after load,
   so parts that load earlier (the router, app.05's whitelist) can reach EXT
   at runtime. */
const EXT = {};

function extRegister(d) {
  EXT[d.name] = d;
  if (d.section)
    SECTIONS[d.name] = Object.assign(
      { api: d.name, list: d.name, tabEl: d.name + "Tab",
        countField: d.name + "_count", label: d.label || d.name,
        scoped: !!d.scoped },
      d.section);
}

function extList() { return Object.values(EXT); }

function extPage(route) {
  return extList().find(x => x.page && x.page.route === route) || null;
}

/* An extension's top-level page (#/x/<route> — the router's arm; nothing
   registers one yet, pinned by tests/jsdom/ext.js). */
function showExtPage(x) {
  document.title = x.page.title || x.label;
  $view.textContent = "";
  const wrap = el("div", "extpage");
  $view.append(wrap);
  x.page.render(wrap);
}

/* The extension tabs anchored after built-in tab `anchor` — chromeTabs calls
   this after each built-in it builds. The badge is the fetched list's length
   once we have it, else the eager served count, same as every section tab. */
function extTabs(ses, meta, mk, anchor) {
  for (const x of extList()) {
    if ((x.after || "jobs") !== anchor) continue;
    if (x.scopeField && !meta[x.scopeField]) continue;
    const sec = SECTIONS[x.name];
    ses[sec.tabEl] = mk(x.name, x.label || x.name,
                        ses[sec.list] ? ses[sec.list].length
                                      : (meta[sec.countField] || 0));
  }
}

const SECTIONS = {
  monitors: {
    api: "monitors", list: "monitors", grid: "monitorsGrid",
    focus: "monitorFocus", poll: "monPoll", tabEl: "monTab",
    countField: "monitor_count", route: "m", glyph: "◉", label: "monitors",
    scoped: true,          // follows agent scope (the lead's own by default)
    empty: "no monitors in this session", missing: "monitor not found",
    name: (m) => m.description || m.command || m.task,
    card: (m) => monitorCard(m), detail: (wrap, m) => renderMonitorDetail(wrap, m),
  },
  jobs: {
    api: "jobs", list: "jobs", grid: "jobsGrid",
    focus: "jobFocus", poll: "jobPoll", tabEl: "jobTab",
    countField: "job_count", route: "j", glyph: "◷", label: "jobs",
    scoped: true,          // …as do background jobs
    empty: "no background jobs in this session", missing: "job not found",
    name: (j) => firstLine(j.command) || j.task,
    card: (j) => jobCard(j), detail: (wrap, j) => renderJobDetail(wrap, j),
  },
};

/* Drop the cached rows of every section that FOLLOWS AGENT SCOPE, on a scope
   change. Their contents belong to the scope that fetched them, and the tab
   badge prefers a cached list's LENGTH to the served count (the list is the
   authority once you've opened the tab) — so entering an agent kept showing the
   previous scope's numbers until you opened that tab yourself, which is the one
   thing the badge exists to save you from ("the counter for the monitors and the
   background jobs are not getting properly updated when I go to subagents").
   Table-driven, so a future scoped section is covered by its own `scoped` flag;
   memory is session-wide and its cache survives. */
function resetScopedSections() {
  const ses = S.ses;
  if (!ses) return;
  for (const [kind, sec] of Object.entries(SECTIONS)) {
    if (!sec.scoped) continue;
    clearSectionPoll(kind);
    ses[sec.list] = null;
    ses[sec.focus] = null;
  }
}

/* live-first, then most-recently-started on top — the order every section
   grid uses */
function sortedItems(items) {
  return [...items].sort((x, y) => (!!y.live - !!x.live)
    || ((y.started_at || 0) - (x.started_at || 0)));
}

function loadSection(kind) {
  const sec = SECTIONS[kind], ses = S.ses, sid = S.cur;
  if (!ses || !sid) return;
  // monitors + jobs follow AGENT SCOPE (sec.scoped); memory is session-wide,
  // so it never carries the filter — docs/dashboard.md *Agent scope*.
  fetch("/api/session/" + encodeURIComponent(sid) + "/" + sec.api
        + (sec.scoped ? agentQ() : ""))
    .then(r => r.json())
    .then(d => {
      if (S.cur !== sid || !S.ses) return;
      S.ses[sec.list] = d[sec.api] || [];
      if (sec.stash) sec.stash(S.ses, d);
      setSectionCount(kind, S.ses[sec.list].length);
      if (sec.repaint) { sec.repaint(); return; }
      if (S.ses[sec.focus]) repaintSectionDetail(kind);
      else renderSectionGrid(kind);
      scheduleSectionPoll(kind);
    })
    .catch(() => {});
}

function renderSectionGrid(kind) {
  const sec = SECTIONS[kind], ses = S.ses;
  if (!(ses && ses.tab === kind && ses[sec.grid] && ses[sec.grid].isConnected))
    return;
  ses[sec.grid].textContent = "";
  const items = ses[sec.list] || [];
  if (!items.length) {
    ses[sec.grid].append(el("div", "empty", sec.empty));
    return;
  }
  for (const it of sortedItems(items)) ses[sec.grid].append(sec.card(it));
}

// The secondary tabs poll while something in them is still live — ONE cadence,
// not two: they are the same fact (a background list the SSE doesn't push,
// refreshed only while you're looking at it). Slow on purpose — these are GETs
// outside the SSE, and the tab is only polled while focused/live.
const SECONDARY_POLL_MS = 4000;

function scheduleSectionPoll(kind) {
  const sec = SECTIONS[kind], ses = S.ses;
  clearSectionPoll(kind);
  if (!ses) return;
  const live = (ses[sec.list] || []).some(x => x.live);
  // keep the list / detail fresh while something is still firing
  if (live && (ses.tab === kind || ses[sec.focus]))
    ses[sec.poll] = setInterval(() => loadSection(kind), SECONDARY_POLL_MS);
}

function clearSectionPoll(kind) {
  const sec = SECTIONS[kind], ses = S.ses;
  if (ses && ses[sec.poll]) { clearInterval(ses[sec.poll]); ses[sec.poll] = null; }
}

/* The tab badge: the cheap eager SSE count (a new launch bumps it) or the exact
   list length once fetched. updateSectionCount is the SSE half — it also
   refreshes the list when that tab is the one open. */
function setSectionCount(kind, n) {
  const sec = SECTIONS[kind];
  return setTabBadge(sec.countField, sec.tabEl, n);
}

function updateSectionCount(kind, n) {
  const ses = setSectionCount(kind, n);
  // a section may say its list is NOT the thing on screen (memory's `showing`
  // is false while a note viewer is open) — don't refresh under it
  const sec = SECTIONS[kind];
  const showing = sec.showing ? sec.showing() : true;
  if (ses && ses.tab === kind && showing) loadSection(kind);
}

/* Open one item's drill-down (router #/s/<sid>/<route>/<task>). */
function showSection(kind, sid, task, agent) {
  const sec = SECTIONS[kind];
  // a scoped detail (…/a/<aid>/m/<task>) enters that agent's scope first, so
  // the list this task is looked up in is the agent's, not the lead's
  if (S.cur !== sid || (agent || "") !== ((S.ses && S.ses.agent) || ""))
    showSession(sid, kind, agent);
  const ses = S.ses;
  if (!ses) return;
  clearSectionPoll(kind);
  ses.tab = kind.slice(0, -1) + ":" + task;      // "monitor:<task>" / "job:<task>"
  ses[sec.focus] = task;
  // no tab-bar entry is "<kind>:<task>", so light the section's own tab (the
  // same "you are here" cue the agents drill-down restores on its tab)
  const re = new RegExp("\\/" + kind + "$");
  $view.querySelectorAll(".tabs a").forEach(a =>
    a.classList.toggle("on", re.test(a.getAttribute("href") || "")));
  updateRunning();
  if (ses[sec.list]) repaintSectionDetail(kind);
  else loadSection(kind);        // direct navigation / reload — fetch then paint
}

function repaintSectionDetail(kind) {
  const sec = SECTIONS[kind], ses = S.ses;
  if (!ses || !ses[sec.focus] || !ses.body) return;
  const task = ses[sec.focus];
  const item = (ses[sec.list] || []).find(x => x.task === task);
  resetBody();
  ses.body.append(sectionCrumbs(kind, S.cur, item || { task: task }));
  const wrap = el("div");
  ses.body.append(wrap);
  if (!item) { wrap.append(el("div", "empty", sec.missing)); return; }
  sec.detail(wrap, item);
  scheduleSectionPoll(kind);     // still live -> keep its detail refreshing
}

/* The drill-down breadcrumb — <glyph> <label> (back to the list) › this item. */
function sectionCrumbs(kind, sid, item) {
  const sec = SECTIONS[kind];
  const nav = el("div", "crumbs");
  const back = el("a", "crumb");
  back.href = (S.ses && S.ses.agent)
    ? agentHref(sid, S.ses.agent, kind)          // back to the SCOPED list
    : "#/s/" + encodeURIComponent(sid) + "/" + kind;
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
   same block the MIRROR paints: highlighted and pretty-printed server-side
   (`cmd_html` — see opshtml.cmd_html for why the reflow happens there and not
   at op creation). `command` still rides along as text for the card titles and
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
/* The jobs tab mirrors the monitors/agents tabs for `run_in_background` Bash
   jobs (and Ctrl+B conversions): a grid of job cards with lifecycle state, each
   drilling into command + full output. Data comes from sessionapi.jobs(sid) —
   the audit streams state (kind='bg') merged with the command from the mirror
   ops (copy-group). A job's OUTPUT is NOT in the transcript (it streams to the
   ops), so the drill-down fetches it from the same ops via /copy/<task>/out (the
   ⧉out copy endpoint). Loaded lazily on tab-open, re-fetched on a light poll
   while any job is live; the count badge stays fresh via the `jobs` SSE. */

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
  card.href = sectionHref(S.cur, "j", j.task);
  const name = firstLine(j.command) || j.task;
  card.append(el("div", "aid", "◷ " + name));
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

  // Output lives in the ops, not the transcript — fetch it from the copy
  // endpoint, by the job's ops COPY GROUP. Which is the taskId for the lead's
  // own jobs (its tailer paints under it) but the tool_use_id for an AGENT's,
  // whose block the substream opened before any taskId existed — so the server
  // carries `group` for exactly this, and asking by `task` returned an empty
  // body for every subagent job ("I clicked on the background jobs of that
  // subagent and I cannot see the output it is making"). `task` stays the
  // fallback for a row that predates the field.
  const outwrap = el("div", "mevents");
  outwrap.append(el("div", "mhead", "output"));
  const box = el("div", "joutput");
  box.append(el("div", "empty", "loading output…"));
  outwrap.append(box);
  container.append(outwrap);
  fetch("/api/session/" + encodeURIComponent(S.cur) + "/copy/"
        + encodeURIComponent(j.group || j.task) + "/out")
    .then(r => r.text())
    .then(t => {
      if (!box.isConnected) return;
      box.textContent = "";
      box.append(t.trim() ? pre(t) : el("div", "empty",
        j.live ? "no output yet" : "(no output)"));
    })
    .catch(() => { if (box.isConnected) { box.textContent = ""; box.append(el("div", "empty", "output unavailable")); } });
}

/* ---------- agent scope ---------- */

/* The href of one tab in agent scope — the scoped route
   (#/s/<sid>/a/<aid>/<tab>) that showSession's router entry reads back. Kept
   beside the crumbs because the tab bar and the "← session" link are the only
   places that spell the scoped URL. */
function agentHref(sid, aid, tab) {
  return "#/s/" + encodeURIComponent(sid) + "/a/" + encodeURIComponent(aid)
    + (tab && tab !== "mirror" ? "/" + tab : "");
}

/* The href of ONE monitor/job detail, keeping whatever scope its card was
   listed under: `#/s/<sid>/m/<task>` for the lead's, nested under the agent's
   route for a scoped one, so a reload/share lands on the same list. */
function sectionHref(sid, route, task) {
  const a = (S.ses && S.ses.agent) || "";
  const base = "#/s/" + encodeURIComponent(sid)
    + (a ? "/a/" + encodeURIComponent(a) : "");
  return base + "/" + route + "/" + encodeURIComponent(task);
}

/* Empty the tab body and lay down what belongs ABOVE every tab's content: in
   agent scope, the way back out of it. THE one door for clearing the body, so
   that "the scope crumb is always there" is a property of the reset rather than
   a line each painter has to remember.

   It didn't, and that was the bug: the crumbs were appended once by
   renderSessionChrome, and every painter that later replaced the body wholesale
   — a monitor/job drill-down, the memory grid, an open note — wiped them and put
   only its OWN crumb back. So opening one of an agent's background jobs left you
   inside that agent with nothing on screen saying so and no way up but the
   browser's Back ("when I click on monitors or jobs on a subagent, I still want
   to have that breadcrumb — it never goes away"). */
function resetBody() {
  const ses = S.ses;
  if (!ses || !ses.body) return;
  ses.body.textContent = "";
  if (ses.agent)
    ses.body.append(agentCrumbs(S.cur, ses.agent,
                                (ses.agents || []).find(a => a.agent_id === ses.agent)));
}

/* The agent-hierarchy breadcrumb in agent scope — the MAIN agent → this agent
   (docs/dashboard.md, *Breadcrumbs*). Just the two nodes (the hierarchy is one
   level deep — a session's flat agent list): the main agent is a link back to
   its own mirror (#/s/<sid>), labelled by the session's title; the current
   agent is the highlighted end node. Icons: ◆ the main agent, ◇/◈ the
   subagent/teammate. Clicking the main node is how you leave scope. */
function agentCrumbs(sid, aid, rec) {
  const nav = el("div", "crumbs");
  const meta = (S.ses && S.ses.meta) || {};
  const sesName = meta.title || (meta.cwd ? proj(meta) : shortSid(sid));
  const main = el("a", "crumb");
  main.href = "#/s/" + encodeURIComponent(sid);       // the mirror = the main agent
  main.title = "back to the main agent";
  main.append(el("span", "cg", "◆"), tnode(" " + sesName));
  const cur = el("span", "crumb cur");
  cur.append(el("span", "cg", rec && rec.kind === "teammate" ? "◈" : "◇"),
             tnode(" " + ((rec && rec.desc) || aid)));
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
  container.append(el("div", "empty", "loading…"));
  fetch("/api/session/" + encodeURIComponent(S.cur) + "/errors")
    .then(r => r.json()).then(rows => {
      if (!container.isConnected) return;
      container.textContent = "";
      const wrap = el("div", "errs");
      if (!rows.length) wrap.append(el("div", "empty", "no swallowed exceptions — clean session"));
      for (const r of rows) {
        const e = el("div", "err");
        e.append(el("div", "h", "⚠ " + (r.script || "?") + " · " + (r.func || "?")
                    + (r.ts ? " · " + new Date(r.ts * 1000).toLocaleString() : "")));
        if (r.traceback) e.append(pre(r.traceback));
        wrap.append(e);
      }
      container.append(wrap);
    });
}

/* ---------- ⧉ copy / click-to-view (server-rendered .cc anchors) ---------- */

document.addEventListener("click", (e) => {
  const a = e.target.closest && e.target.closest("a.cc");
  if (!a) return;
  e.preventDefault();
  const cc = (a.dataset.cc || "").split("/");
  if (cc.length !== 3) return;
  const [key, gid, what] = cc;
  if (what === "view") return toggleView(a, key, gid);
  fetch("/api/session/" + encodeURIComponent(key) + "/copy/"
        + encodeURIComponent(gid) + "/" + encodeURIComponent(what))
    .then(r => r.text())
    .then(text => {
      if (!text.trim()) return toast("", "nothing to copy", "");
      // clipboard is undefined over a plain-http tunnel (non-secure context);
      // guard it so the ⧉ copy doesn't reject unhandled there.
      if (!navigator.clipboard) return toast("ask", "copy failed", "needs https");
      navigator.clipboard.writeText(text).then(
        () => toast("done", "copied " + (what === "cmd" ? "command" : what === "out" ? "output" : "block"),
                    text.length + " chars"),
        () => toast("ask", "copy failed", "clipboard permission?"));
    })
    .catch(() => toast("ask", "copy failed", "try again"));
});

function toggleView(anchor, key, gid) {
  const host = anchor.closest("[data-v]");
  if (!host) return;
  const next = host.nextElementSibling;
  if (next && next.classList.contains("view-block")) { next.remove(); return; }
  fetch("/api/session/" + encodeURIComponent(key) + "/view/" + encodeURIComponent(gid))
    .then(r => r.ok ? r.text() : null)
    .then(html => {
      if (html == null) return toast("", "nothing to show", "");
      host.insertAdjacentHTML("afterend", html);
    });
}

/* ---------- viewport diagnostics (?vpdiag) ----------
   A live readout of the numbers a remote device (an iPad) is actually
   rendering with — layout vs visual viewport, scale, screen, dpr — for
   debugging zoom/fit reports that headless WebKit can't reproduce. Doubles
   as a staleness probe: the overlay only exists in THIS build of app.js,
   so "no overlay" == the device is loading stale assets. */


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
// the only surviving record). Read-only as far as the TASKS go: unlike ask/plan
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
  S.ses.tasksEl = wrap;
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
    const sid = S.cur;
    const meta = S.ses && S.ses.meta;
    if (!meta) return;
    meta.tasks_hidden = true;      // optimistic — the card goes at once
    renderTasks();
    postJSON("/api/session/" + encodeURIComponent(sid) + "/tasks-hide",
             { hidden: true },
             { audit: "tasks-hide", sid, auditData: { tasks: tasks.length } })
      .then(() => toast("done", "tasks hidden",
                        "the card returns with the next task"))
      .catch(e => {
        // the write never landed — put the card back rather than leave the page
        // showing a dismissal no other device will ever see
        if (S.cur === sid && S.ses && S.ses.meta) {
          S.ses.meta.tasks_hidden = false;
          renderTasks();
        }
        toast("ask", "hide failed", (e && e.error) || "");
      });
  });
  return btn;
}

function renderTasks() {
  const ses = S.ses;
  if (!ses || !ses.tasksEl) return;
  const wrap = ses.tasksEl;
  wrap.textContent = "";
  const tasks = (ses.meta && ses.meta.tasks) || null;
  // `tasks_hidden` is the SERVER's verdict (read/session.py tasks_hidden — the
  // dismissal AND whether it still applies to this list), never a local flag
  wrap.hidden = !tasks || !tasks.length || !!(ses.meta && ses.meta.tasks_hidden);
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
