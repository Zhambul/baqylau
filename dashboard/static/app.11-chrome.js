"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.12-init.js for the boot/init sequence.

function renderSessionChrome(tab) {
  const ses = S.ses;
  if (!ses) return;
  ses.agentFocus = null;      // a full session/tab render is never agent-focused
  ses.monitorFocus = null;    // …nor monitor-focused (a drill-down sets it again)
  ses.jobFocus = null;        // …nor background-job-focused
  clearMonitorPoll();         // leaving the monitors tab stops its live poll
  clearJobPoll();
  const meta = ses.meta || {};
  // the Memory tab only exists for in-scope (aggregator-adapters) sessions — a
  // deep-link / stale bookmark to it elsewhere falls back to the mirror
  if (tab === "memory" && !meta.memory_scope) tab = "mirror";
  $view.textContent = "";

  const head = el("div", "shead");
  head.dataset.tab = meta.tab || "";    // state tint; live via setBadge()
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
  // the action buttons live on their OWN row (actrow) — inside l1 they
  // floated to wherever the title/chips left room, moving with title width
  const act = el("div", "actrow");
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
  if (meta.live && meta.kitty_window_id) {
    // stop: interrupt the agent in place — an Escape key press in the
    // session's window (the TUI's own interrupt; Esc here does the same).
    // Immediate, no confirm: it matches pressing Esc in the terminal.
    const stop = el("button", "sstop actstop", "■ stop");
    stop.title = "interrupt the agent (Esc)";
    stop.onclick = () => lockDuring(stop, interruptSession,
                                    () => ses.stopMode(liveTab()));
    // gated to the working states like ⊘ cancel — an interrupt only applies
    // while a turn is running, and an Esc when idle can clear queued input, so
    // it greys out otherwise (the resting state re-derives from the tab, never
    // a blind re-enable). Same CANCEL_TABS set (thinking/working/executing/
    // awaiting-bg) — NOT red awaiting-command, where an Esc declines the open
    // dialog instead of interrupting (interruptSession bails there too).
    ses.stopMode = (tab) => { stop.disabled = !CANCEL_TABS.includes(tab); };
    ses.stopMode(liveTab());
    act.append(stop);
    // cancel: mid-turn double-Esc — cancel the running turn and restore your
    // message into the composer for editing. Enabled only while a turn runs.
    const cancel = el("button", "sstop actses", "⊘ cancel");
    cancel.title = "cancel this turn and edit your message (mid-turn double-Esc)";
    // resting state re-derives from the tab (idle → stays disabled) rather
    // than a blind re-enable, matching ses.cancelMode's own gate
    cancel.onclick = () => lockDuring(cancel, cancelEdit,
                                      () => ses.cancelMode(liveTab()));
    ses.cancelMode = (tab) => { cancel.disabled = !CANCEL_TABS.includes(tab); };
    ses.cancelMode(liveTab());
    act.append(cancel);
    // rewind: Claude Code's double-Esc — mid-turn it cancels for editing;
    // idle it enters picking mode: click a message below, choose what to
    // restore, and the server drives the TUI's own checkpoint menu
    const rew = el("button", "sstop actses", "↶ rewind");
    rew.title = "rewind: pick a message to restore to (mid-turn: cancel + edit)";
    // stopPropagation is load-bearing: the ENABLING click must not bubble to
    // the document click-away handler, which reads any non-bubble click in
    // picking mode as "leave" — without it the mode self-cancelled in the
    // same event (toast shown, buttons never revealed)
    rew.onclick = (e) => { e.stopPropagation(); rewindSession(); };
    act.append(rew);
    // close: closes the session's kitty tab — a graceful stop (Claude Code
    // exits on the HUP and SessionEnd runs the normal lifecycle).
    // Two-step confirm: first click arms for 4s, second click fires.
    const cls = el("button", "sstop actses", "✕ close");
    cls.title = "close this session's terminal tab";
    let armed = null;
    const disarm = () => {
      armed = null;
      cls.textContent = "✕ close";
      cls.classList.remove("arm");
    };
    cls.onclick = () => {
      if (!armed) {
        cls.textContent = "close session?";
        cls.classList.add("arm");
        armed = setTimeout(disarm, ARM_MS);
        return;
      }
      clearTimeout(armed);
      disarm();
      cls.disabled = true;
      cls.textContent = "closing…";
      const sid = S.cur;
      // optimistic close: beacon the `close` lifecycle (web-hint op=close) and
      // navigate back to the list on the POST ack — the list card shows greyed
      // 'closing…' (S.closing) until reconcileCloses parks it from the poll.
      S.closing.add(sid);
      S.closePend[sid] = optPending(sid, "close");
      closeSession(sid, "header")
        .then(() => {
          toast("done", "session closed", "terminal tab closed");
          // the session just ended — back to the list, unless the user
          // already navigated elsewhere while the POST was in flight
          if (S.cur === sid) location.hash = "#/";
        })
        .catch(e => {
          S.closing.delete(sid);
          if (S.closePend[sid]) {
            S.closePend[sid].settle("dropped", { reason: "failed" });
            delete S.closePend[sid];
          }
          cls.disabled = false;
          cls.textContent = "✕ close";
          clientFail(sid, "close", e);   // a lost/rejected /stop the audit can't see
          toast("ask", "close failed", (e && e.error) || "");
        });
    };
    act.append(cls);
  }
  // resume (parked, with a cwd): reopen the new-session form preset to
  // `claude --resume <this sid>` in this session's directory
  if (!meta.live && meta.cwd) {
    const res = el("button", "sresume actses", "↻ resume");
    res.title = "start a new tab resuming this conversation";
    res.onclick = () => openNewSession(meta.cwd, S.cur);
    act.append(res);
  }
  // quick commands on their OWN second row under the action buttons: compact
  // + the model/effort pickers, each typing the TUI's own slash command into
  // the session (docs/dashboard.md, *Web quick commands*). Live-only like
  // stop/cancel — there is no window to type into otherwise.
  const act2 = el("div", "actrow");
  if (meta.live && meta.kitty_window_id) {
    // compact: two-step confirm like close — a misclick summarizes the whole
    // conversation out from under you, so it arms first
    const cpt = el("button", "sstop actses", "⊜ compact");
    cpt.title = "compact the conversation (/compact)";
    let cptArmed = null;
    const cptDisarm = () => {
      cptArmed = null;
      cpt.textContent = "⊜ compact";
      cpt.classList.remove("arm");
    };
    cpt.onclick = () => {
      if (!cptArmed) {
        cpt.textContent = "compact now?";
        cpt.classList.add("arm");
        cptArmed = setTimeout(cptDisarm, ARM_MS);
        return;
      }
      clearTimeout(cptArmed);
      cptDisarm();
      sendQuickCmd("compact");
    };
    act2.append(cpt);
    // model: dropdown picker; the label shows the ctx probe's current model
    // (live via the `ctx` SSE event → updateStatsRow)
    const mwrap = el("span", "qcwrap actses");
    const mdl = el("button", "sstop");
    ses.modelBtn = mdl;
    setModelBtn(mdl);
    mdl.title = "switch the model (/model — also saves as your new-session default)";
    mdl.onclick = () => openQuickMenu(mwrap, "model", MODEL_CHOICES,
                                      curModelFamily());
    mwrap.append(mdl);
    act2.append(mwrap);
    // effort: dropdown picker (current effort is config-only — not readable
    // from any transcript, see plugins/claude_code/model.py — so no label)
    const ewrap = el("span", "qcwrap actses");
    const eff = el("button", "sstop");
    ses.effortBtn = eff;
    setEffortBtn(eff);
    eff.title = "set the reasoning effort (/effort — also saves as your new-session default)";
    eff.onclick = () => openQuickMenu(ewrap, "effort", EFFORT_CHOICES,
                                      (ses.meta && ses.meta.effort) || "");
    ewrap.append(eff);
    act2.append(ewrap);
    // a red tab = a modal dialog is up — pasted text would land IN it (the
    // server 409s too; disabling just says so up front). Live via the same
    // SSE tab event as cancelMode.
    ses.quickMode = (tab) => {
      const block = tab === "awaiting-command";
      for (const b of [cpt, mdl, eff]) b.disabled = block;
    };
    ses.quickMode(liveTab());
  }
  head.append(l1);
  if (act.childElementCount) head.append(act);
  if (act2.childElementCount) head.append(act2);
  const sr = el("div", "statsrow");
  ses.statsRow = sr;
  ses._statsSig = null;      // fresh (empty) row — force the next paint through
  head.append(sr);
  const cr = el("div", "ctxrow");     // the main thread's ctx bar, its own row
  ses.ctxRow = cr;
  head.append(cr);
  const rr = el("div", "runrow");
  ses.runRibbon = rr;
  head.append(rr);
  $view.append(head);
  updateStatsRow();
  updateRunning();

  const tabs = el("div", "tabs");
  const mk = (key, label, count) => {
    const a = el("a", key === tab ? "on" : "");
    a.href = "#/s/" + encodeURIComponent(S.cur) + (key === "mirror" ? "" : "/" + key);
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
  // ❖ memory-wiki notes touched — actual list length once fetched, else the
  // cheap eager count. SCOPED: only sessions inside the enabled project
  // (aggregator-adapters, meta.memory_scope) get the tab at all.
  if (meta.memory_scope)
    ses.memTab = mk("memory", "memory",
                    ses.memory ? ses.memory.length : (meta.memory_count || 0));
  ses.errTab = mk("errors", "errors", meta.error_count || 0);   // live ⚠ count patches it
  $view.append(tabs);

  const body = el("div");
  ses.body = body;
  $view.append(body);

  if (tab === "mirror") {
    body.append(buildGoalCard());           // the active /goal, pinned at the very top
    body.append(buildTasksCard());          // the session's task list, pinned first
    body.append(buildPlanCard());           // pending plan approval …
    body.append(buildAskCard());            // … and question, above the composer
    body.append(buildComposer());
    // type right away on open — no click needed. After append (focus() on a
    // detached node is a no-op), and only when the box can send (a disabled
    // parked/headless composer takes no input anyway). The document-level
    // gestures (Esc, ⌃-keys, ⌃⇧←/→) are focus-independent, so this only
    // redirects plain typing. Not on an iPad: an unasked-for focus pops the
    // on-screen keyboard over the stream on every session open (and focus is
    // what triggers Safari's page auto-zoom — see style.css touch section).
    if (!ses.composer.disabled && !IS_IPAD) ses.composer.focus();
    body.append(buildFilterBar());
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
    applyFilter();                        // re-filter items already in the stream
  } else if (tab === "agents") {
    const wrap = el("div", "sgrid");
    ses.agentsGrid = wrap;
    body.append(wrap);
    updateAgents();
  } else if (tab === "monitors") {
    const wrap = el("div", "sgrid");
    ses.monitorsGrid = wrap;
    body.append(wrap);
    if (ses.monitors) renderMonitorsGrid();   // cached from a prior fetch
    else wrap.append(el("div", "empty", "loading monitors…"));
    loadMonitors();                            // (re)fetch fresh + start the live poll
  } else if (tab === "jobs") {
    const wrap = el("div", "sgrid");
    ses.jobsGrid = wrap;
    body.append(wrap);
    if (ses.jobs) renderJobsGrid();
    else wrap.append(el("div", "empty", "loading jobs…"));
    loadJobs();
  } else if (tab === "memory") {
    if (!ses.memory) body.append(el("div", "empty", "loading memory…"));
    paintMemory();                        // grid, or the note viewer if one is open
    loadMemory();
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
    const d = f.data || {};
    const rec = (ses.agents || []).find(a => a.agent_id === f.aid) || {};
    return "A|" + [f.aid, rec.kind, rec.desc, rec.ended_at, rec.started_at,
      rec.tools, rec.model, rec.effort, rec.end_reason, rec.done,
      d.tools, d.cost, d.model].join(",")
      + "|" + JSON.stringify(d.usage || {}) + "|" + JSON.stringify(rec.ctx || {});
  }
  const st = ses.stats || {};
  const cost = (ses.costs && ses.costs.total_usd) || st.cost;
  return "S|" + [st.commands, st.failed, st.start, st.paused, st.files,
    st.added, st.removed, st.tk_in, st.tk_out, st.tk_read, st.tk_create, cost,
    st.msg_delivered, st.msg_read, (ses.meta && ses.meta.error_count) || 0,
    ses.meta && ses.meta.model].join(",")
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
  const add = (label, value, cls) => {
    const s = el("span");
    if (label) s.append(tnode(label + " "));
    s.append(el("span", cls || "v", value));
    sr.append(s);
  };
  if (st.commands) {
    add("", st.commands + " cmds");
    if (st.failed) add("", "(" + st.failed + "✗)", "neg");
  }
  if (st.start)
    add("⏱", dur(Date.now() / 1000 - st.start - (+st.paused || 0)));
  if (st.files) add("", st.files + " files");
  if (st.added) add("", "+" + st.added, "pos");
  if (st.removed) add("", "−" + st.removed, "neg");
  const tin = st.tk_in | 0, tout = st.tk_out | 0, tread = st.tk_read | 0, tcre = st.tk_create | 0;
  const tot = tin + tout + tread + tcre;
  if (tot)
    add("Σ", kfmt(tot) + " (" + kfmt(tin) + " in · " + kfmt(tout) + " out · "
        + kfmt(tread) + " cache · " + kfmt(tcre) + " write)");
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
  if (ses.ctxRow) {
    ses.ctxRow.textContent = "";
    const cx = ses.ctx;
    if (cx && cx.used) ses.ctxRow.append(ctxBar(cx, true));
    ses.ctxRow.style.display = cx && cx.used ? "" : "none";
  }
}

/* Header-action visibility for the agent-focus state (docs/dashboard.md,
   *Subagent scoreboard swap*). While a subagent scoreboard is showing, the
   session-only actions (`.actses` — rename / migrate / cancel / rewind / close /
   resume / compact / model / effort) don't apply to a subagent, so they hide;
   ■ stop (`.actstop`) stays ONLY while the focused subagent is still running
   (interrupting the session is the one way to stop it). An action row left with
   nothing visible collapses so it leaves no gap. A full renderSessionChrome
   rebuild (going back) restores everything, agentFocus already cleared. */
function applyAgentActionVis() {
  const ses = S.ses;
  if (!ses) return;
  const focused = !!ses.agentFocus;
  $view.querySelectorAll(".actses").forEach(b => { b.style.display = focused ? "none" : ""; });
  const stop = $view.querySelector(".actstop");
  if (stop) {
    let show = true;
    if (focused) {
      const rec = (ses.agents || []).find(a => a.agent_id === ses.agentFocus.aid);
      show = !!(rec && agentStatus(rec)[1] === "st-run");
    }
    stop.style.display = show ? "" : "none";
  }
  $view.querySelectorAll(".shead .actrow").forEach(row => {
    const any = [...row.children].some(c => c.style.display !== "none");
    row.style.display = any ? "" : "none";
  });
}

/* The scoreboard for a drilled-into subagent — replaces the session totals with
   THAT agent's own numbers (docs/dashboard.md, *Subagent scoreboard swap*). It
   resolves the freshest agent row from ses.agents each render (so an `agents`
   SSE that finishes the agent updates the status here too) and reads tokens/cost
   from focus.data, the agent drill-down fetch (`/agent/<aid>` → usage + the
   server-priced `cost`). The prominent header NAME becomes the subagent's; the
   stats row leads with a "← session" link that restores the session view, and
   the ctx row repaints from the agent's own ctx bar. */
function renderAgentScoreboard(sr, focus) {
  const ses = S.ses;
  const rec = (ses.agents || []).find(a => a.agent_id === focus.aid) || {};
  const d = focus.data || {};
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
  const add = (label, value, cls) => {
    const s = el("span");
    if (label) s.append(tnode(label + " "));
    s.append(el("span", cls || "v", value));
    sr.append(s);
  };
  add("", sttxt, stcls);
  const model = rec.model || (d.model ? String(d.model) : "");
  if (model) add("", model + (rec.effort ? "·" + rec.effort : ""), "amodel");
  const ev = d.tools != null ? d.tools : rec.tools;
  if (ev != null) add("", ev + " events");
  if (rec.started_at)
    add("⏱", rec.ended_at ? dur(rec.ended_at - rec.started_at) : ago(rec.started_at));
  const u = d.usage || {};
  const tin = u.in | 0, tout = u.out | 0, tread = u.cache | 0, tcre = u.create | 0;
  const tot = tin + tout + tread + tcre;
  if (tot)
    add("Σ", kfmt(tot) + " (" + kfmt(tin) + " in · " + kfmt(tout) + " out · "
        + kfmt(tread) + " cache · " + kfmt(tcre) + " write)");
  if (d.cost) add("≈", usd(d.cost), "cost");
  if (ses.ctxRow) {
    ses.ctxRow.textContent = "";
    const cx = rec.ctx;
    if (cx && cx.used) ses.ctxRow.append(ctxBar(cx, true));
    ses.ctxRow.style.display = cx && cx.used ? "" : "none";
  }
}

/* Live ⚠ error badge — the web sibling of the scorebar's errwatch chip
   (count-only on the fast path; full tracebacks stay behind the errors tab).
   Patches the stats-row chip and the errors-tab count in place (no full
   re-render), and re-fetches the errors list only when that tab is open and
   the count grew. */
function updateErrCount(n) {
  const ses = S.ses;
  if (!ses) return;
  const prev = (ses.meta && ses.meta.error_count) || 0;
  if (ses.meta) ses.meta.error_count = n;
  updateStatsRow();
  setTabCount(ses.errTab, n);
  if (ses.tab === "errors" && n > prev && ses.body) renderErrorsInto(ses.body);
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
  card.href = "#/s/" + encodeURIComponent(S.cur) + "/a/" + encodeURIComponent(a.agent_id);
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

function sortedMonitors(mons) {
  // live first, then most-recently-started on top
  return [...mons].sort((x, y) => (!!y.live - !!x.live)
    || ((y.started_at || 0) - (x.started_at || 0)));
}

function monitorCard(m) {
  const [sttxt, stcls] = monitorStatus(m);
  const card = el("a", "acard");
  card.dataset.st = stcls;
  card.href = "#/s/" + encodeURIComponent(S.cur) + "/m/" + encodeURIComponent(m.task);
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

function renderMonitorsGrid() {
  const ses = S.ses;
  if (!(ses && ses.tab === "monitors" && ses.monitorsGrid && ses.monitorsGrid.isConnected))
    return;
  ses.monitorsGrid.textContent = "";
  const mons = ses.monitors || [];
  if (!mons.length) {
    ses.monitorsGrid.append(el("div", "empty", "no monitors in this session"));
    return;
  }
  for (const m of sortedMonitors(mons)) ses.monitorsGrid.append(monitorCard(m));
}

function loadMonitors() {
  const ses = S.ses, sid = S.cur;
  if (!ses || !sid) return;
  fetch("/api/session/" + encodeURIComponent(sid) + "/monitors")
    .then(r => r.json())
    .then(d => {
      if (S.cur !== sid || !S.ses) return;
      S.ses.monitors = d.monitors || [];
      setMonCount(S.ses.monitors.length);
      if (S.ses.monitorFocus) repaintMonitorDetail();
      else renderMonitorsGrid();
      scheduleMonitorPoll();
    })
    .catch(() => {});
}

function scheduleMonitorPoll() {
  clearMonitorPoll();
  const ses = S.ses;
  if (!ses) return;
  const live = (ses.monitors || []).some(m => m.live);
  // keep the list / detail fresh while a monitor is still firing events
  if (live && (ses.tab === "monitors" || ses.monitorFocus))
    ses.monPoll = setInterval(loadMonitors, 4000);
}

function clearMonitorPoll() {
  if (S.ses && S.ses.monPoll) { clearInterval(S.ses.monPoll); S.ses.monPoll = null; }
}

// the ◉ monitors tab badge, live — the cheap `monitors` SSE count (a new launch
// bumps it) OR the exact list length once /monitors is fetched (setMonCount).
function updateMonCount(n) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.monitor_count = n;
  setTabCount(ses.monTab, n);
  // a new monitor arrived while the tab is open -> refresh the list
  if (ses.tab === "monitors") loadMonitors();
}

function setMonCount(n) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.monitor_count = n;
  setTabCount(ses.monTab, n);
}

function showMonitor(sid, task) {
  if (S.cur !== sid) showSession(sid, "monitors");
  const ses = S.ses;
  if (!ses) return;
  closeAgentStream();
  clearMonitorPoll();
  ses.tab = "monitor:" + task;
  ses.monitorFocus = task;
  // no tab-bar entry is "monitor:<task>", so light the `monitors` tab (the same
  // "you are here" cue the agents drill-down restores on its tab)
  $view.querySelectorAll(".tabs a").forEach(a =>
    a.classList.toggle("on", /\/monitors$/.test(a.getAttribute("href") || "")));
  updateRunning();
  if (ses.monitors) repaintMonitorDetail();
  else loadMonitors();          // direct navigation / reload — fetch then paint
}

function repaintMonitorDetail() {
  const ses = S.ses;
  if (!ses || !ses.monitorFocus || !ses.body) return;
  const task = ses.monitorFocus;
  const m = (ses.monitors || []).find(x => x.task === task);
  ses.body.textContent = "";
  ses.body.append(monitorCrumbs(S.cur, m || { task: task }));
  const wrap = el("div");
  ses.body.append(wrap);
  if (!m) { wrap.append(el("div", "empty", "monitor not found")); return; }
  renderMonitorDetail(wrap, m);
  scheduleMonitorPoll();        // live monitor -> keep its detail refreshing
}

/* The monitor breadcrumb — ◉ monitors (back to the list) › this monitor. */
function monitorCrumbs(sid, m) {
  const nav = el("div", "crumbs");
  const back = el("a", "crumb");
  back.href = "#/s/" + encodeURIComponent(sid) + "/monitors";
  back.title = "back to the monitors list";
  back.append(el("span", "cg", "◉"), tnode(" monitors"));
  const cur = el("span", "crumb cur");
  cur.append(el("span", "cg", "◉"),
             tnode(" " + (m.description || m.command || m.task)));
  nav.append(back, el("span", "csep", "›"), cur);
  return nav;
}

function renderMonitorDetail(container, m) {
  const [sttxt, stcls] = monitorStatus(m);
  const info = el("div", "mdetail");
  const h = el("div", "mdhead");
  h.append(el("span", "k k-monitor", "◉ monitor"), el("span", stcls, sttxt));
  info.append(h);
  if (m.description) info.append(el("div", "mdesc", m.description));
  if (m.command) {
    info.append(el("div", "lbl", m.source === "ws" ? "websocket" : "command"));
    info.append(pre(m.command));
  }
  const grid = el("div", "mmeta");
  const add = (k, v) => {
    if (v == null || v === "") return;
    grid.append(el("span", "mk", k), el("span", "mv", String(v)));
  };
  add("task", m.task);
  add("lifetime", m.persistent ? "persistent"
    : (m.timeout_ms ? "≤" + dur(m.timeout_ms / 1000) : "—"));
  add("events", m.event_count);
  if (m.started_at) add("started", new Date(m.started_at * 1000).toLocaleString());
  if (m.ended_at) add("ended", new Date(m.ended_at * 1000).toLocaleString());
  if (m.started_at && m.ended_at) add("duration", dur(m.ended_at - m.started_at));
  else if (m.started_at && m.live) add("running for", ago(m.started_at));
  add("end reason", m.end_reason);
  info.append(grid);
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

function sortedJobs(jobs) {
  return [...jobs].sort((x, y) => (!!y.live - !!x.live)
    || ((y.started_at || 0) - (x.started_at || 0)));
}

function jobCard(j) {
  const [sttxt, stcls] = jobStatus(j);
  const card = el("a", "acard");
  card.dataset.st = stcls;
  card.href = "#/s/" + encodeURIComponent(S.cur) + "/j/" + encodeURIComponent(j.task);
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

function renderJobsGrid() {
  const ses = S.ses;
  if (!(ses && ses.tab === "jobs" && ses.jobsGrid && ses.jobsGrid.isConnected)) return;
  ses.jobsGrid.textContent = "";
  const jobs = ses.jobs || [];
  if (!jobs.length) {
    ses.jobsGrid.append(el("div", "empty", "no background jobs in this session"));
    return;
  }
  for (const j of sortedJobs(jobs)) ses.jobsGrid.append(jobCard(j));
}

function loadJobs() {
  const ses = S.ses, sid = S.cur;
  if (!ses || !sid) return;
  fetch("/api/session/" + encodeURIComponent(sid) + "/jobs")
    .then(r => r.json())
    .then(d => {
      if (S.cur !== sid || !S.ses) return;
      S.ses.jobs = d.jobs || [];
      setJobCount(S.ses.jobs.length);
      if (S.ses.jobFocus) repaintJobDetail();
      else renderJobsGrid();
      scheduleJobPoll();
    })
    .catch(() => {});
}

function scheduleJobPoll() {
  clearJobPoll();
  const ses = S.ses;
  if (!ses) return;
  const live = (ses.jobs || []).some(j => j.live);
  if (live && (ses.tab === "jobs" || ses.jobFocus))
    ses.jobPoll = setInterval(loadJobs, 4000);
}

function clearJobPoll() {
  if (S.ses && S.ses.jobPoll) { clearInterval(S.ses.jobPoll); S.ses.jobPoll = null; }
}

function updateJobCount(n) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.job_count = n;
  setTabCount(ses.jobTab, n);
  if (ses.tab === "jobs") loadJobs();     // a new job arrived with the tab open
}

function setJobCount(n) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.job_count = n;
  setTabCount(ses.jobTab, n);
}

/* ---------- memory tab (the memory-wiki notes a session touched) ---------- */

function loadMemory() {
  const ses = S.ses, sid = S.cur;
  if (!ses || !sid) return;
  fetch("/api/session/" + encodeURIComponent(sid) + "/memory")
    .then(r => r.json())
    .then(d => {
      if (S.cur !== sid || !S.ses) return;
      S.ses.memory = d.memory || [];
      setMemCount(S.ses.memory.length);
      // repaint the grid only when it's showing (a note viewer stays put)
      if (S.ses.tab === "memory" && !(S.ses.noteTrail && S.ses.noteTrail.length))
        paintMemory();
    })
    .catch(() => {});
}

function setMemCount(n) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.memory_count = n;
  setTabCount(ses.memTab, n);
}

function updateMemCount(n) {          // live SSE badge patch
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.memory_count = n;
  setTabCount(ses.memTab, n);
  if (ses.tab === "memory" && !(ses.noteTrail && ses.noteTrail.length))
    loadMemory();                     // a new note was touched with the grid open
}

/* Paint the memory tab body: the note grid, or the note viewer when a note
   (or a followed [[wikilink]]) is open. */
function paintMemory() {
  const ses = S.ses;
  if (!ses || !ses.body || ses.tab !== "memory") return;
  ses.body.textContent = "";
  if (ses.noteTrail && ses.noteTrail.length) { renderNoteView(); return; }
  const wrap = el("div", "sgrid memgrid");
  ses.memGrid = wrap;
  ses.body.append(wrap);
  renderMemoryGrid();
}

function renderMemoryGrid() {
  const ses = S.ses;
  if (!ses || !ses.memGrid) return;
  ses.memGrid.textContent = "";
  const mem = ses.memory || [];
  if (!mem.length) {
    ses.memGrid.append(el("div", "empty", "no memory notes touched in this session"));
    return;
  }
  for (const m of mem) ses.memGrid.append(memCard(m));
}

function memCard(m) {
  const card = el("div", "memcard");
  const verb = (m.verb || "Read").toLowerCase();
  card.append(el("span", "vchip v-" + verb, verb));
  card.append(el("span", "memname", m.name || "?"));
  if (m.agent) card.append(el("span", "memagent", "⇢ " + m.agent));
  if (m.count > 1) card.append(el("span", "memcount", "×" + m.count));
  card.onclick = () => openNoteRef({ path: m.path }, true);
  return card;
}

/* Open a note by absolute path (a grid row) or bare stem (a followed
   [[wikilink]]). `reset` starts a fresh breadcrumb trail (a grid click);
   following a link pushes onto it. */
function openNoteRef(ref, reset) {
  const ses = S.ses, sid = S.cur;
  if (!ses || !sid) return;
  const q = ref.path ? ("path=" + encodeURIComponent(ref.path))
                     : ("stem=" + encodeURIComponent(ref.stem || ""));
  fetch("/api/session/" + encodeURIComponent(sid) + "/note?" + q)
    .then(r => r.json())
    .then(d => {
      if (S.cur !== sid || !S.ses) return;
      if (reset || !S.ses.noteTrail) S.ses.noteTrail = [];
      S.ses.noteTrail.push(d);
      S.ses.noteFocus = d.path || d.name;
      paintMemory();
      // start the newly-opened note from its top — following a link deep in one
      // note shouldn't land you mid-way down the next (the page scrolls the
      // window; the sticky header stays pinned)
      window.scrollTo(0, 0);
    })
    .catch(() => {});
}

function renderNoteView() {
  const ses = S.ses;
  if (!ses || !ses.body) return;
  const trail = ses.noteTrail || [];
  const d = trail[trail.length - 1];
  ses.body.textContent = "";
  ses.body.append(noteCrumbs(trail));
  if (!d) return;
  const wrap = el("div", "note");
  if (d.missing) {
    wrap.append(el("div", "empty", "note not found: " + (d.name || "?")));
    ses.body.append(wrap);
    return;
  }
  if (d.frontmatter && d.frontmatter.length) {
    const fm = el("div", "note-fm");
    for (const [k, v] of d.frontmatter) { fm.append(el("span", "fk", k), el("span", "fv", v)); }
    wrap.append(fm);
  }
  const bodyEl = el("div", "note-body");
  bodyEl.innerHTML = d.html || "";        // server-rendered, escape-first (opshtml/notehtml)
  wrap.append(bodyEl);
  if (d.backlinks && d.backlinks.length) {
    const bl = el("div", "note-backlinks");
    bl.append(el("div", "lbl", "backlinks"));
    for (const stem of d.backlinks) {
      const a = el("a", "wl", stem);
      a.dataset.note = stem;
      bl.append(a);
    }
    wrap.append(bl);
  }
  // follow a [[wikilink]] / backlink. DIRECT per-anchor onclick, NOT delegation:
  // these anchors have no href, and mobile Safari won't dispatch a bubbled click
  // from a tap on such an element to a container listener — the same reason the
  // grid cards (which DO open on the phone) use a direct onclick. Covers both the
  // body links and the backlinks (both live under `wrap`); dead links get none.
  wrap.querySelectorAll("a.wl").forEach(a => {
    if (a.classList.contains("dead")) return;
    a.onclick = (ev) => { ev.preventDefault(); openNoteRef({ stem: a.dataset.note }); };
  });
  ses.body.append(wrap);
}

/* The note breadcrumb — ❖ memory (back to the grid) › note › followed note … */
function noteCrumbs(trail) {
  const nav = el("div", "crumbs");
  const back = el("a", "crumb");
  back.href = "#/s/" + encodeURIComponent(S.cur) + "/memory";
  back.title = "back to the memory list";
  back.append(el("span", "cg", "❖"), tnode(" memory"));
  back.onclick = (e) => {
    e.preventDefault();
    S.ses.noteTrail = []; S.ses.noteFocus = null; paintMemory();
  };
  nav.append(back);
  trail.forEach((d, i) => {
    nav.append(el("span", "csep", "›"));
    if (i === trail.length - 1) {
      const cur = el("span", "crumb cur");
      cur.append(el("span", "cg", "❖"), tnode(" " + (d.name || "?")));
      nav.append(cur);
    } else {
      const a = el("a", "crumb");
      a.href = "javascript:void 0";
      a.append(tnode(d.name || "?"));
      a.onclick = (e) => { e.preventDefault(); S.ses.noteTrail = trail.slice(0, i + 1); paintMemory(); };
      nav.append(a);
    }
  });
  return nav;
}

function showJob(sid, task) {
  if (S.cur !== sid) showSession(sid, "jobs");
  const ses = S.ses;
  if (!ses) return;
  closeAgentStream();
  clearJobPoll();
  ses.tab = "job:" + task;
  ses.jobFocus = task;
  $view.querySelectorAll(".tabs a").forEach(a =>
    a.classList.toggle("on", /\/jobs$/.test(a.getAttribute("href") || "")));
  updateRunning();
  if (ses.jobs) repaintJobDetail();
  else loadJobs();
}

function repaintJobDetail() {
  const ses = S.ses;
  if (!ses || !ses.jobFocus || !ses.body) return;
  const task = ses.jobFocus;
  const j = (ses.jobs || []).find(x => x.task === task);
  ses.body.textContent = "";
  ses.body.append(jobCrumbs(S.cur, j || { task: task }));
  const wrap = el("div");
  ses.body.append(wrap);
  if (!j) { wrap.append(el("div", "empty", "job not found")); return; }
  renderJobDetail(wrap, j);
  scheduleJobPoll();
}

/* The job breadcrumb — ◷ jobs (back to the list) › this job. */
function jobCrumbs(sid, j) {
  const nav = el("div", "crumbs");
  const back = el("a", "crumb");
  back.href = "#/s/" + encodeURIComponent(sid) + "/jobs";
  back.title = "back to the jobs list";
  back.append(el("span", "cg", "◷"), tnode(" jobs"));
  const cur = el("span", "crumb cur");
  cur.append(el("span", "cg", "◷"),
             tnode(" " + (firstLine(j.command) || j.task)));
  nav.append(back, el("span", "csep", "›"), cur);
  return nav;
}

function renderJobDetail(container, j) {
  const [sttxt, stcls] = jobStatus(j);
  const info = el("div", "mdetail");
  const h = el("div", "mdhead");
  h.append(el("span", "k k-job", "◷ background"), el("span", stcls, sttxt));
  info.append(h);
  if (j.command) {
    info.append(el("div", "lbl", "command"));
    info.append(pre(j.command));
  }
  const grid = el("div", "mmeta");
  const add = (k, v) => {
    if (v == null || v === "") return;
    grid.append(el("span", "mk", k), el("span", "mv", String(v)));
  };
  add("task", j.task);
  add("lines", j.lines);
  if (j.started_at) add("started", new Date(j.started_at * 1000).toLocaleString());
  if (j.ended_at) add("ended", new Date(j.ended_at * 1000).toLocaleString());
  if (j.started_at && j.ended_at) add("duration", dur(j.ended_at - j.started_at));
  else if (j.started_at && j.live) add("running for", ago(j.started_at));
  add("end reason", j.end_reason);
  info.append(grid);
  container.append(info);

  // output lives in the ops, not the transcript — fetch it from the copy endpoint
  const outwrap = el("div", "mevents");
  outwrap.append(el("div", "mhead", "output"));
  const box = el("div", "joutput");
  box.append(el("div", "empty", "loading output…"));
  outwrap.append(box);
  container.append(outwrap);
  fetch("/api/session/" + encodeURIComponent(S.cur) + "/copy/"
        + encodeURIComponent(j.task) + "/out")
    .then(r => r.text())
    .then(t => {
      if (!box.isConnected) return;
      box.textContent = "";
      box.append(t.trim() ? pre(t) : el("div", "empty",
        j.live ? "no output yet" : "(no output)"));
    })
    .catch(() => { if (box.isConnected) { box.textContent = ""; box.append(el("div", "empty", "output unavailable")); } });
}

/* ---------- timeline (activity / agent drill-down) ---------- */

function showAgent(sid, aid) {
  if (S.cur !== sid) showSession(sid, "agents");
  closeAgentStream();                       // switching agents / re-entering
  S.ses.tab = "agent:" + aid;
  const ses = S.ses;
  // no tab-bar entry is "agent:<id>", so light the `agents` tab — a drill-down
  // is a descent INTO agents, and this restores the "you are here" cue the
  // breadcrumb also carries (previously every tab went dark here).
  $view.querySelectorAll(".tabs a").forEach(a =>
    a.classList.toggle("on", /\/agents$/.test(a.getAttribute("href") || "")));
  // swap the top scoreboard to this agent — first from the enriched agents row
  // we already have (status/model/events/ctx/duration), then the fetch below
  // fills in tokens + cost.
  ses.agentFocus = { aid: aid, data: null };
  updateStatsRow();
  updateRunning();
  applyAgentActionVis();     // the session-only header actions don't apply here
  if (ses.body) {
    ses.body.textContent = "";
    const rec = (ses.agents || []).find(a => a.agent_id === aid);
    ses.body.append(agentCrumbs(sid, aid, rec));   // back-up-the-hierarchy trail
    const tlWrap = el("div");                       // renderTimelineInto clears its
    ses.body.append(tlWrap);                        // container, so keep it off the crumbs
    // a running agent's page grows live; a parked one (ended_at set) is
    // fetch-once — its transcript won't grow, so don't open a stream.
    const live = !!rec && rec.ended_at == null;
    // newest-first, matching the main agent's mirror stream (which prepends —
    // appendItems), so a subagent's most recent message reads at the top
    renderTimelineInto(tlWrap,
                       "/api/session/" + encodeURIComponent(sid) + "/agent/" + encodeURIComponent(aid),
                       (rec && rec.desc) || aid,
                       live ? { sid: sid, aid: aid } : null, true,
                       // feed the agent's tokens/cost rollup up into the scoreboard
                       (d) => { if (ses.agentFocus && ses.agentFocus.aid === aid) {
                                  ses.agentFocus.data = d; updateStatsRow(); } });
  }
}

/* The agent-hierarchy breadcrumb on a subagent drill-down — the MAIN agent →
   this subagent (docs/dashboard.md, *Breadcrumbs*). Just the two agent nodes
   (the hierarchy is one level deep — a session's flat agent list): the main
   agent is a link back to its mirror (#/s/<sid>), labelled by the session's
   title; the current subagent is the highlighted end node. Icons: ◆ the main
   agent, ◇/◈ the subagent. Clicking the main node is how you go back. */
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

function renderTimelineInto(container, apiUrl, title, live, newestFirst, onData) {
  container.append(el("div", "empty", "loading " + title + "…"));
  fetch(apiUrl).then(r => r.json()).then(d => {
    if (onData) onData(d);            // hand the payload (usage/cost) to the caller
    if (!container.isConnected) return;
    container.textContent = "";
    container.append(timelineHead(d, title));
    const list = el("div", "tl");
    const entries = d.entries || [];
    if (!entries.length) list.append(el("div", "empty", "no recorded activity"));
    // newestFirst reverses the chronological entries so the most recent reads at
    // the top (the subagent drill-down, matching the main mirror); the head stays
    // above regardless. The live SSE below then prepends new increments to match.
    const ordered = newestFirst ? entries.slice().reverse() : entries;
    for (const ent of ordered) list.append(timelineEntry(ent));
    container.append(list);
    // LIVE agents: resume the SSE at the byte cursor the REST read stopped at
    // (d.pos — additive; absent for a provider with no incremental support,
    // e.g. codex, so the drill-down simply stays fetch-once).
    if (live && d.pos != null) connectAgentStream(live.sid, live.aid, d.pos, list, newestFirst);
  }).catch(() => {
    if (!container.isConnected) return;
    container.textContent = "";
    container.append(el("div", "empty", "no transcript available for " + title));
  });
}

/* The live agent timeline stream: adds new increment `entries` (at the bottom
   for oldest-first, or the TOP when newestFirst — matching the initial render's
   order) and applies `resolve` events — a tool_result that arrived in a later
   increment than its tool_use — by finding the tool entry via its data-tool-id
   and filling in the result. Reconnects (like the per-session stream) resume at
   the latest byte cursor so nothing repeats. */
function connectAgentStream(sid, aid, pos, list, newestFirst) {
  let cur = pos;
  const es = new EventSource("/events/agent/" + encodeURIComponent(sid)
                             + "/" + encodeURIComponent(aid) + "?pos=" + cur);
  S.ses.agentEs = es;
  es.onopen = () => sseMark("agent", true, { sid });
  es.addEventListener("entries", (e) => {
    const d = JSON.parse(e.data);
    if (d.pos != null) cur = d.pos;
    const empty = list.querySelector(".empty");
    if (empty) empty.remove();
    // newestFirst: prepend each increment entry in chronological order, so the
    // increment's newest ends topmost and the whole increment sits above older
    // ones (the reverse of the append path).
    for (const ent of d.entries || []) {
      const node = timelineEntry(ent);
      if (newestFirst) list.prepend(node); else list.append(node);
    }
  });
  es.addEventListener("resolve", (e) => {
    const d = JSON.parse(e.data);
    if (d.pos != null) cur = d.pos;
    for (const r of d.resolutions || []) applyResolution(list, r);
  });
  es.onerror = () => {
    sseMark("agent", false, { sid });
    es.close();
    if (!S.ses || S.ses.agentEs !== es) return;   // navigated away
    S.ses.agentEs = null;
    setTimeout(() => {
      if (S.ses && S.ses.tab === "agent:" + aid && list.isConnected)
        connectAgentStream(sid, aid, cur, list, newestFirst);
    }, 1500);
  };
}

// A resolution tuple [tool_use_id, output, failed] fills in a tool entry whose
// tool_result arrived in a later increment; no matching entry (a genuine
// orphan we never rendered) is a no-op.
function applyResolution(list, r) {
  const box = list.querySelector('[data-tool-id="' + cssq(r[0]) + '"]');
  if (!box) return;
  const region = toolResultRegion({ output: r[1], failed: !!r[2] });
  const old = box.querySelector(".tout");
  if (old) old.replaceWith(region);
  else { const bd = box.querySelector(".bd"); if (bd) bd.append(region); }
  const k = box.querySelector(".k");
  if (k && r[2]) { k.classList.remove("k-tool"); k.classList.add("k-toolfail"); }
  box.dataset.open = "1";                   // surface the freshly-arrived result
}

function cssq(s) {
  s = String(s);
  return (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/["\\]/g, "\\$&");
}

function closeAgentStream() {
  if (S.ses && S.ses.agentEs) { S.ses.agentEs.close(); S.ses.agentEs = null; }
}

function timelineHead(d, title) {
  const h = el("div", "tlhead");
  const add = (label, value) => {
    const s = el("span");
    s.append(tnode(label + " "));
    s.append(el("span", "v", value));
    h.append(s);
  };
  add("◈", title);
  if (d.model) add("model", d.model);
  if (d.tools) add("tools", String(d.tools));
  const u = d.usage || {};
  const tot = (u.in | 0) + (u.out | 0) + (u.cache | 0) + (u.create | 0);
  if (tot)
    add("Σ", kfmt(tot) + " (" + kfmt(u.in) + " in · " + kfmt(u.out) + " out · "
        + kfmt(u.cache) + " cache · " + kfmt(u.create) + " write)");
  if (d.bad_lines) add("⚠", d.bad_lines + " bad lines");
  return h;
}

function inputSummary(input) {
  if (!input || typeof input !== "object") return "";
  const parts = [];
  for (const [k, v] of Object.entries(input)) {
    const vs = typeof v === "string" ? v : JSON.stringify(v);
    parts.push(k + ": " + vs);
  }
  return parts.join("  ·  ");
}

function firstLine(s, n) {
  s = (s || "").trim();
  const nl = s.indexOf("\n");
  if (nl >= 0) s = s.slice(0, nl);
  return s.length > (n || 160) ? s.slice(0, n || 160) + "…" : s;
}

function timelineEntry(ent) {
  const box = el("div", "ent");
  const hd = el("div", "hd");
  const bd = el("div", "bd");
  let kcls = "k-message", ktxt = ent.t, sum = "", open = false;

  if (ent.t === "prompt") {
    kcls = "k-prompt"; ktxt = "prompt"; sum = firstLine(ent.text); open = true;
    bd.append(mdOrPre(ent.html, ent.text));
  } else if (ent.t === "teammsg") {
    kcls = "k-teammsg"; ktxt = "✉ " + (ent.sender || "team");
    sum = firstLine(ent.body); open = false;
    bd.append(mdOrPre(ent.html, ent.body));
  } else if (ent.t === "message") {
    kcls = ent.final ? "k-final" : "k-message";
    ktxt = ent.final ? "result" : "message";
    sum = firstLine(ent.text); open = !!ent.final;
    bd.append(mdOrPre(ent.html, ent.text));
  } else if (ent.t === "compact") {
    kcls = "k-compact"; ktxt = "compact";
    sum = "context compacted"; open = false;
    bd.append(pre(JSON.stringify(ent.meta || {}, null, 2)));
  } else if (ent.t === "recap") {
    // Claude Code's away-summary recap — one-line summary of what happened
    // while you were away (auto after idle, or on-demand /recap).
    kcls = "k-recap"; ktxt = "↩ recap";
    sum = firstLine(ent.text); open = false;
    bd.append(mdOrPre(ent.html, ent.text));
  } else if (ent.t === "monitor") {
    // A Monitor tool event (or its stream-ended `status`). The launch itself is
    // a separate `tool` entry (name "Monitor"); these are the events it fired.
    kcls = "k-monitor";
    ktxt = ent.status ? ("◉ monitor " + ent.status) : "◉ monitor event";
    const body = ent.event || ent.summary || "";
    sum = firstLine(body); open = false;
    if (body) bd.append(pre(body));
  } else if (ent.t === "tool") {
    kcls = ent.failed ? "k-toolfail" : "k-tool";
    ktxt = ent.tool || "tool";
    sum = firstLine(inputSummary(ent.input)); open = false;
    if (ent.input_html != null) {
      bd.append(el("div", "lbl", "input"));
      bd.append(htmlFrag(ent.input_html));
    } else if (ent.input && Object.keys(ent.input).length) {
      bd.append(el("div", "lbl", "input"));
      bd.append(pre(JSON.stringify(ent.input, null, 2)));
    }
    bd.append(toolResultRegion(ent));
  } else if (ent.t === "orphan-result") {
    kcls = "k-orphan"; ktxt = "result";
    sum = firstLine(ent.output); open = false;
    bd.append(pre(ent.output));
  } else {
    sum = JSON.stringify(ent).slice(0, 160);
    bd.append(pre(JSON.stringify(ent, null, 2)));
  }

  hd.append(el("span", "k " + kcls, ktxt));
  const sspan = el("span", "sum");
  sspan.append(tnode(sum || ""));
  hd.append(sspan);
  box.dataset.open = open ? "1" : "0";
  // tool entries carry their tool_use id so a later `resolve` SSE event (a
  // tool_result that landed in a subsequent increment) can find and fill them.
  if (ent.t === "tool" && ent.id) box.dataset.toolId = ent.id;
  hd.onclick = () => { box.dataset.open = box.dataset.open === "1" ? "0" : "1"; };
  box.append(hd, bd);
  return box;
}

// The tool entry's result region (its own .tout block so a `resolve` event can
// replace just it, leaving the input section intact). No output yet -> "no
// result recorded"; the live resolve renders the output as a plain <pre> (the
// lightweight resolution tuple carries no output_html — a rich re-render lands
// on the next full fetch).
function toolResultRegion(ent) {
  const tout = el("div", "tout");
  if (ent.output != null) {
    const lbl = el("div", "lbl", ent.failed ? "output · failed" : "output");
    if (ent.failed) lbl.classList.add("fail");
    tout.append(lbl);
    tout.append(ent.output_html != null ? htmlFrag(ent.output_html) : pre(ent.output));
  } else {
    tout.append(el("div", "lbl", "no result recorded"));
  }
  return tout;
}

function pre(text) { const p = el("pre"); p.textContent = text == null ? "" : String(text); return p; }
// Server-rendered markdown (opshtml.md_html — escaped there, the same
// neutralize() analog as op HTML) is the only thing set via innerHTML here;
// with no `html` field (older provider) fall back to plain textContent.
function mdOrPre(html, text) {
  if (html == null) return pre(text);
  const d = el("div", "md");
  d.innerHTML = html;
  return d;
}
// Server-rendered tool input/output HTML (opshtml.tool_html / tool_output_html
// — escaped there like op HTML). Same innerHTML-is-safe-by-construction basis
// as mdOrPre; a bare wrapper carries the structured blocks (.oc / .tdiff /
// .tdl) without the .md markdown styling.
function htmlFrag(html) {
  const d = el("div", "thtml");
  d.innerHTML = html;
  return d;
}

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
