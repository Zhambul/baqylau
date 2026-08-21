"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.

function showList() {
  leaveSession();
  renderList();
  if (!S.sessions.length) loadSessionDataList();
}

function applyCanonicalSessions(sessions) {
  S.sessions = sessions || [];
  reconcileCloses();
  if (!S.currentSessionId) renderList();
  else updateHeadFromList();
  renderAttention();
  checkJump();
}

function renderList(force) {
  if (S.currentSessionId || S.pendingUI || onStats()) return;   // stats owns #view on its route
  if (!S.sessions.length) {
    S.listKey = null;
    $view.textContent = "";
    $view.append(el("div", "empty", "no sessions recorded yet"));
    return;
  }
  // Same shape as the last full render (and its DOM is still mounted — a
  // session view wipes $view, so a stale card map must not be patched
  // blind) → update changed cards in place instead of rebuilding: the SSE
  // pushes a fresh snapshot every tick while anything is active, and a full
  // teardown per second lost hover/scroll state and burned layout for rows
  // that hadn't changed.
  const groups = groupSessions(S.sessions);
  const shape = listShape(groups);
  const anchor = S.cards.values().next().value;
  if (!force && shape === S.listKey && anchor && anchor.isConnected)
    return patchCards();
  $view.textContent = "";
  S.cards.clear();
  S.rowPrev.clear();
  renderDirGroups(groups);
  S.listKey = shape;
}

function groupSessions(rows) {
  // one group per PROJECT directory (ordered by its newest session); inside
  // each: active cards visible, parked / archived (>3d) as click-to-open
  // folds. The canonical project directory is frozen from the session's
  // initial working directory and resolves linked worktrees to their owner.
  const groups = new Map();
  for (const row of rows) {
    const k = groupKey(row);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(row);
  }
  const ordered = [...groups.entries()].sort((a, b) =>
    Math.max(...b[1].map(orderKey))
    - Math.max(...a[1].map(orderKey)));
  const now = Date.now() / 1000;
  // recency, not age: a week-old session touched yesterday isn't archived
  const old = r => !lastActive(r) || now - lastActive(r) > ARCHIVE_S;
  return ordered
    .filter(([k, grows]) => !dirHidden(k, grows))
    .map(([workingDirectory, groupRows]) => ({
      workingDirectory, count: groupRows.length,
      active: groupRows.filter(sessionIsLive),
      parked: groupRows.filter(row => !sessionIsLive(row) && !old(row)),
      archived: groupRows.filter(row => !sessionIsLive(row) && old(row)),
    }));
}

// A directory the ✕ hid (S.hidden holds its hide time) stays hidden only while
// NONE of its sessions started after that time — the moment a newer session
// appears (a fresh launch, terminal or dashboard, or a resume that re-stamps
// started_at) it re-shows. Purely client-side over the HTTP-boundary rows' started_at;
// the server only stores the {key: hidden_at} stamp.
function dirHidden(key, rows) {
  const t = S.hidden[key];
  if (t == null) return false;
  // never hidden while it has a LIVE session (matches the server's hide guard —
  // a directory with an active session can't be hidden, and one that GAINS a
  // live session re-shows at once) or one started after the hide stamp (a fresh
  // launch / resume re-shows it).
  return !rows.some(row => sessionIsLive(row) || row.session.started_at > t);
}

// The ✕ on a dir header: hide it from the list. Optimistic (stamp now, re-render
// so it vanishes immediately), then POST — the server stamps its OWN time.time()
// and returns the full map, which we adopt as truth. On failure the optimistic
// stamp is dropped and the group returns. Non-destructive: nothing is closed or
// removed; the group re-appears on the next session started there.
function hideDir(key) {
  S.hidden[key] = Date.now() / 1000;
  renderList(true);
  postJSON("/api/application/hidden-directories", { working_directory: key })
    .then(d => { if (d && d.hidden) { S.hidden = d.hidden; renderList(true); } })
    .catch(err => {
      delete S.hidden[key];
      renderList(true);
      toast("ask", "hide failed", (err && err.error) || "");
    });
}

// What makes the list's SHAPE: group order, which cards are VISIBLE (active +
// open folds), fold counts/open state. Rows changing in place don't move the
// shape (they patch); anything here changing forces the full rebuild — so a
// live↔parked flip, a new session, or a fold toggle re-lays the list, while
// a stats tick only touches its own card.
function listShape(groups) {
  return JSON.stringify(groups.map(g => [
    g.workingDirectory, g.active.map(sessionId),
    g.parked.length,
    S.folds.has(g.workingDirectory + "|parked") ? g.parked.map(sessionId) : 0,
    g.archived.length,
    S.folds.has(g.workingDirectory + "|archived") ? g.archived.map(sessionId) : 0,
  ]));
}

function renderDirGroups(groups) {
  for (const g of groups) {
    const hd = el("div", "dirhead");
    hd.append(el("span", "dirname", g.workingDirectory ? g.workingDirectory.split("/").filter(Boolean).pop() : "no project"));
    if (g.workingDirectory) hd.append(el("span", "dirpath", g.workingDirectory));
    hd.append(el("span", "dircount", g.count + (g.count === 1 ? " session" : " sessions")));
    if (g.workingDirectory) {
      const add = el("button", "dirnew", "+");
      add.title = "new session in " + g.workingDirectory;
      add.onclick = () => openNewSession(g.workingDirectory);
      hd.append(add);
    }
    // ✕ hides ANY group, including the projectless aggregate (g.workingDirectory === "") —
    // its group key is the empty string, which hideDir/the server accept.
    // DISABLED while the group has an active session: you can't hide a directory
    // you're actively working in (the server 409s too — this is just the visible
    // affordance + reason). The tooltip explains why rather than vanishing.
    const hide = el("button", "dirhide", "✕");
    if (g.active.length) {
      hide.disabled = true;
      hide.title = "can't hide — " + g.active.length
        + (g.active.length === 1 ? " active session here" : " active sessions here");
    } else {
      hide.title = g.workingDirectory
        ? "hide this directory from the list (re-appears when a new session starts here)"
        : "hide the projectless sessions from the list (re-appears when a new one starts)";
    }
    hide.onclick = () => hideDir(g.workingDirectory);
    hd.append(hide);
    $view.append(hd);
    if (g.active.length) {
      const grid = el("div", "sgrid");
      for (const row of g.active) grid.append(mountCard(row));
      $view.append(grid);
    }
    fold(g.workingDirectory, "parked", g.parked);
    fold(g.workingDirectory, "archived", g.archived);
  }
}

function mountCard(row) {
  const c = sessionCard(row);
  S.cards.set(sessionId(row), c);
  S.rowPrev.set(sessionId(row), JSON.stringify(row));
  return c;
}

// In-place update: same shape, so every visible row already has a card —
// rebuild the innards of just the cards whose row data changed. The card
// <a> itself survives, so scroll position, :hover, and the rest of the
// list's layout stay put.
function patchCards() {
  for (const row of S.sessions) {
    const card = S.cards.get(sessionId(row));
    if (!card) continue;
    const enc = JSON.stringify(row);
    if (enc === S.rowPrev.get(sessionId(row))) continue;
    S.rowPrev.set(sessionId(row), enc);
    const fresh = sessionCard(row);
    card.dataset.tab = fresh.dataset.tab;
    card.replaceChildren(...fresh.childNodes);
  }
}

// The REAL confirmation of an optimistic close: the sessions snapshot now shows
// the sessionId gone (or demoted to not-live) — the tab actually parked. Beacon the
// reconcile, drop the in-flight state, and un-grey the (about-to-be-rebuilt)
// card. Called on every sessions/-delta update, BEFORE the re-render so the
// rebuilt card shows the parked chip, not a stale 'closing…'.
function reconcileCloses() {
  for (const closingSessionId of Object.keys(S.closePend)) {
    const row = S.sessions.find(item => sessionId(item) === closingSessionId);
    if (row && sessionIsLive(row)) continue;             // still live — close hasn't landed
    clog(closingSessionId, "close.reconciled",
         { ms: Math.round(performance.now() - S.closePend[closingSessionId].t0) });
    closeSettle(closingSessionId, "reconciled");
    const card = S.cards.get(closingSessionId);
    if (card) card.classList.remove("closing");
  }
}

function fold(workingDirectory, kind, rows) {
  if (!rows.length) return;
  const key = workingDirectory + "|" + kind;
  const open = S.folds.has(key);
  const btn = el("button", "fold" + (open ? " open" : ""),
                 (open ? "▾ " : "▸ ") + kind + " · " + rows.length);
  btn.onclick = () => {
    S.folds.has(key) ? S.folds.delete(key) : S.folds.add(key);
    renderList(true);
  };
  $view.append(btn);
  if (open) {
    const grid = el("div", "sgrid folded");
    for (const row of rows) grid.append(mountCard(row));
    $view.append(grid);
  }
}

// Relative "ago" labels and the 3d archived boundary depend on the CLOCK,
// not on data — and with the server's paused-blind diff an idle list gets no
// sessions events at all, so nothing would ever re-run them. One full render
// a minute (registered at boot) keeps them honest for free.
const LIST_REFRESH_MS = 60000;

function sessionCard(row) {
  const a = el("a", "scard");
  a.dataset.tab = sessionTabState(row) || "";        // state tint (style.css --state wash)
  a.href = "#/s/" + encodeURIComponent(sessionId(row));
  a.append(el("div", "proj", row.session.title || proj(row)));
  a.append(el("div", "sessionId", sessionId(row)));
  // no "live" chip — the state tint + badge already say it; only the
  // inactive states (parked/gone) need explaining. A live windowed session
  // gets the ✕ close in the same corner slot instead — the header's close
  // reachable straight from the list.
  const corner = el("div", "corner");
  if (!sessionIsLive(row))
    corner.append(el("span", "chip2 parked", sessionIsParked(row) ? "parked" : "gone"));
  else if (S.closing.has(sessionId(row))) {           // optimistic close in flight
    a.classList.add("closing");                // greyed until the sessions poll parks it
    corner.append(el("span", "chip2 closing", "closing…"));
  } else if (sessionIsLive(row))
    corner.append(cardClose(sessionId(row)));
  if (corner.childNodes.length) a.append(corner);
  const r = el("div", "row");
  const badge = el("span", "badge");
  badge.dataset.tab = sessionTabState(row) || "";
  badge.append(el("span", "st"), tnode(TAB_LABEL[sessionTabState(row) || ""] || sessionTabState(row)));
  r.append(badge);
  const commandCount = sessionStatistics(row).shell_command_count || 0;
  if (commandCount) r.append(seg(commandCount + " cmds"));
  const usage = sessionUsage(row);
  const tokens = usage.tokens;
  const tokenCount = tokens.input_tokens + tokens.output_tokens
    + tokens.cache_read_tokens + tokens.cache_write_tokens
    + tokens.one_hour_cache_write_tokens;
  if (tokenCount) r.append(seg(kfmt(tokenCount) + " tok"));
  if (usage.cost_in_usd != null)
    r.append(segc(usd(Number(usage.cost_in_usd)), "cost"));
  // recency, not age: started_at here read as staleness — a live session an
  // hour into its work showed "1h ago" while actively streaming
  if (lastActive(row)) r.append(seg(ago(lastActive(row))));
  if (row.repository) r.append(gitChip(row.repository));
  a.append(r);
  const context = sessionContext(row);
  if (context.window_tokens) {
    const usedTokens = context.used_tokens || 0;
    const windowTokens = context.window_tokens || 0;
    a.append(contextBar({
      used: usedTokens,
      window: windowTokens,
      pct: windowTokens ? Math.round(usedTokens * 100 / windowTokens) : 0,
      // The context window belongs to a MODEL, and the model is one display
      // name now — the id the picker would send back comes from the catalog.
      model_short: sessionModel(row),
    }, false, { key: sessionId(row) }));
  }
  return a;
}
// the list-card ✕ — POST /api/session/<sessionId>/stop, the same graceful tab
// close as the session header's "✕ close" (the terminal HUPs the tab, Claude Code
// exits, SessionEnd parks the mirror), with the same two-step confirm: first
// click arms ("close?") for 4 s, second fires. Lives inside the card <a>, so
// clicks must not bubble into a navigation. No hash change on success — the
// card demotes to parked on its own via the SSE sessions push.
// Deliberately different from the header ✕ (which keeps closure-local arm
// state): the arm + in-flight state live in S (S.armClose/S.closing, keyed
// by sessionId, the arm as a DEADLINE not a timer handle) because the per-tick
// sessions push rebuilds a changed card wholesale (patchCards
// replaceChildren) — and a live card's row changes every tick, so
// closure/DOM-held state died within ~1s of arming: the confirm reverted
// before it could be clicked. The constructor re-derives both states, so a
// rebuilt button resumes the arm with the REMAINING window; stale timers
// from replaced predecessors are neutered by the sessionId+deadline check.
function cardClose(sessionId) {
  const btn = el("button", "xclose", "✕");
  btn.title = "close this session's terminal tab";
  btn.disabled = S.closing.has(sessionId);
  const armed = () =>
    S.armClose && S.armClose.sessionId === sessionId && Date.now() < S.armClose.until;
  const disarm = () => {
    if (S.armClose && S.armClose.sessionId === sessionId && Date.now() >= S.armClose.until)
      S.armClose = null;
    if (!armed()) { btn.textContent = "✕"; btn.classList.remove("arm"); }
  };
  const showArmed = () => {
    btn.textContent = "close?";
    btn.classList.add("arm");
    setTimeout(disarm, S.armClose.until - Date.now());
  };
  if (armed()) showArmed();          // rebuilt mid-arm: restore the confirm
  btn.onclick = (e) => {
    e.preventDefault(); e.stopPropagation();
    if (!armed()) {
      S.armClose = { sessionId, until: Date.now() + ARM_MS };
      showArmed();
      return;
    }
    S.armClose = null;
    btn.classList.remove("arm");
    btn.disabled = true;
    // optimistic: grey THIS card + swap the ✕ to 'closing…' at once (a rebuild
    // from the sessions poll may lag a tick), and beacon the `close` lifecycle
    // (web-hint op=close). reconcileCloses swaps it to the parked chip when the
    // snapshot shows the sessionId go not-live; a failed POST reverts.
    closeBegin(sessionId);
    btn.textContent = "closing…";
    const a = btn.closest(".scard");
    if (a) a.classList.add("closing");
    closeSession(sessionId, "card")
      .then(() => toast("done", "session closed", "terminal tab closed"))
      .catch(err => {
        closeSettle(sessionId, "dropped", { reason: "failed" });
        btn.disabled = false;
        btn.textContent = "✕";
        if (a) a.classList.remove("closing");
        clientFail(sessionId, "close", err);   // a lost/rejected /stop the audit can't see
        toast("ask", "close failed", (err && err.error) || "");
      });
  };
  return btn;
}

function seg(text) { const s = el("span"); s.append(el("span", "v", text)); return s; }
function segc(text, cls) { const s = el("span"); s.append(el("span", cls, text)); return s; }
// git chip — "⎇ branch" plus "⋔ worktree" when the session's checkout is a
// linked worktree (git worktree add / EnterWorktree). A trailing "*" marks
// uncommitted changes, the status-line convention (dirty is true/false/null —
// null = unknown, no marker). Fill an existing span (the header's live chip)
// or make one (session cards).
function setGitChip(chip, g) {
  chip.textContent = "";
  chip.hidden = !g;
  if (!g) return;
  chip.append(el("span", "gb", "⎇ " + g.branch + (g.dirty ? "*" : "")));
  if (g.worktree) chip.append(el("span", "gw", "⋔ " + g.worktree));
}
function gitChip(g) {
  const s = el("span", "gitchip");
  setGitChip(s, g);
  return s;
}

// Last width each ctx bar was PAINTED at, keyed by the bar's identity (`key`).
// The drain animation's memory: contextBar builds a FRESH node every repaint, and a
// fresh node has no previous width to transition FROM — it just appears at its
// final size, which is why .ubar's `transition: width` has never actually
// animated anything. So the width the bar last showed is remembered here, the
// new node is painted at THAT width, and a rAF moves it to the real one — which
// is a real style change on a live node, so the CSS transition runs. First
// sight of a key seeds without animating (a page load should not slide every
// bar up from zero). Bounded: a key is one session or one agent, and the map is
// swept when it outgrows CONTEXT_WIDTH_LIMIT.
const contextWidths = new Map();
const CONTEXT_WIDTH_LIMIT = 400;

function contextWidthFor(key, pct) {
  if (!key) return pct;                       // unkeyed caller: no animation
  const prev = contextWidths.get(key);
  if (contextWidths.size > CONTEXT_WIDTH_LIMIT) contextWidths.clear();
  contextWidths.set(key, pct);
  return prev === undefined ? pct : prev;
}

// context-saturation bar — the account-limit bar's (acctPill's ubar) bigger
// sibling, one full row wherever it appears: session cards, the session
// header (big=true), agent cards. Accent fill, amber ≥70%, red ≥90%.
//
// `opts.comp` = the session's `compacting` record ({since, trigger}) → the bar
// BREATHES: the geometry is frozen at the current occupancy and only the fill's
// brightness moves, a slow 3s fade. Everything that says WHAT is happening is static — the violet tint,
// the ⟳, the "compacting…" detail — so the motion has to carry nothing but
// "still going", and the quietest thing that can say that is light.
// `opts.key` identifies the bar across repaints so the post-compaction drop
// eases instead of jumping (see contextWidths).
function contextBar(contextWindow, big, opts) {
  opts = opts || {};
  const comp = opts.comp;
  const bar = el("div", "cbar" + (contextWindow.pct >= 90 ? " hot" : contextWindow.pct >= 70 ? " warn" : "")
                        + (big ? " big" : "") + (comp ? " compacting" : ""));
  const label = el("span", "clabel");
  // the ⟳ is its OWN span so it can spin without taking the word with it
  if (comp) { label.append(el("span", "cspin", "⟳"), document.createTextNode(" ")); }
  label.append(document.createTextNode("ctx"));
  bar.append(label);
  const track = el("span", "ctrack");
  const fill = el("span", "cfill");
  const pct = Math.max(0, Math.min(100, contextWindow.pct));
  // A compacting bar is painted at its REAL width and left there — the breath
  // is a CSS opacity animation on this one node, so there is nothing extra to
  // build. (An earlier cut animated the width instead and needed a second
  // "ghost" segment to hold the true occupancy while the fill moved; nothing
  // moves now, so there is nothing to hold.) The width is still fed through the
  // comp branch rather than contextWidthFor so a compacting repaint does not
  // consume the key's memory — the drain that follows must start from the
  // PRE-compaction width.
  const from = comp ? pct : contextWidthFor(opts.key, pct);
  fill.style.width = from + "%";
  if (from !== pct)
    requestAnimationFrame(() => { fill.style.width = pct + "%"; });
  track.append(fill);
  bar.append(track, el("span", "cpct", contextWindow.pct + "%"));
  bar.append(el("span", "cdetail", comp
    ? "compacting…" : kfmt(contextWindow.used) + " / " + kfmt(contextWindow.window)));
  return bar;
}

function updateHeadFromList() {
  const row = S.sessions.find(item => sessionId(item) === S.currentSessionId);
  if (!row || !S.sessionView) return;
  if (S.sessionView.badge) setBadge(S.sessionView.badge, sessionTabState(row) || "");
  // Keep the header's live/window state honest against the authoritative global
  // snapshot. `meta` is fetched ONCE at session-open, so a session opened during
  // its startup tag-race — the launch jumps straight to the new sessionId, but its
  // terminal pane isn't tagged claude_session=<sessionId> yet, so the server momentarily
  // reports it not-live (or live-but-window-not-yet-resolved during the grace) —
  // would otherwise FREEZE on that reading: the parked chip stuck on and every
  // live-gated action (stop/cancel/rewind/close/quick-commands) missing, so the
  // user can't even close the session (the reported bug). A later live↔parked
  // flip (kill, crash, resume) has the same staleness. Re-render the chrome ONLY
  // on a real change — not every per-tick tab change (that reflows the header
  // each second) — and not while drilled into a subagent (renderSessionChrome
  // clears agentFocus; the ← session rebuild picks it up on the way back) or mid
  // inline-rename. LIVENESS is the whole comparison now: the window id used to
  // ride along, and a MOVE between windows rebuilt the header too — but the id
  // is deliberately never served any more (a frontend needs to know whether a
  // session is attended, not the handle it is attended through), so a move is
  // invisible here and costs one stale header until something else redraws it.
  const m = S.sessionView.meta;
  if (m && !!m.live !== !!sessionIsLive(row)) {
    m.live = sessionIsLive(row);
    m.parked = sessionIsParked(row);
    const renaming = S.sessionView.projEl && S.sessionView.projEl.querySelector("input");
    if (!S.sessionView.agentFocus && !S.sessionView.monitorFocus && !S.sessionView.jobFocus && !renaming)
      renderSessionChrome(S.sessionView.tab);
  }
}

/* ---------- session view ---------- */
