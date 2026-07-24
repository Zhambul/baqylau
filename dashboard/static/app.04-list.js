"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.12-init.js for the boot/init sequence.

function showList() {
  leaveSession();
  renderList();
  if (!S.sessions.length)
    fetch("/api/sessions").then(r => r.json())
      .then(d => { S.sessions = d; renderList(); renderAttention(); });
}

function renderList(force) {
  if (S.cur || S.pendingUI || onStats()) return;   // stats owns #view on its route
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
  // folds. The group key is the server's `group_dir`: the session's frozen
  // ORIGINAL cwd resolved to its linked-worktree owner. So N agents fanned out
  // over worktrees of one repo file under the main checkout (the per-card ⋔
  // chip tells them apart), AND a mid-session `cd` never moves a card between
  // groups — group_dir ignores the live cwd entirely (row.cwd is the fallback
  // for legacy/parked rows with no group_dir).
  const groups = new Map();
  for (const row of rows) {
    const k = row.group_dir || row.cwd || "";
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
    .map(([cwd, grows]) => ({
      cwd, count: grows.length,
      active: grows.filter(r => r.live),
      parked: grows.filter(r => !r.live && !old(r)),
      archived: grows.filter(r => !r.live && old(r)),
    }));
}

// A directory the ✕ hid (S.hidden holds its hide time) stays hidden only while
// NONE of its sessions started after that time — the moment a newer session
// appears (a fresh launch, terminal or dashboard, or a resume that re-stamps
// started_at) it re-shows. Purely client-side over the wire rows' started_at;
// the server only stores the {key: hidden_at} stamp.
function dirHidden(key, rows) {
  const t = S.hidden[key];
  if (t == null) return false;
  // never hidden while it has a LIVE session (matches the server's hide guard —
  // a directory with an active session can't be hidden, and one that GAINS a
  // live session re-shows at once) or one started after the hide stamp (a fresh
  // launch / resume re-shows it).
  return !rows.some(r => r.live || (r.started_at || 0) > t);
}

// The ✕ on a dir header: hide it from the list. Optimistic (stamp now, re-render
// so it vanishes immediately), then POST — the server stamps its OWN time.time()
// and returns the full map, which we adopt as truth. On failure the optimistic
// stamp is dropped and the group returns. Non-destructive: nothing is closed or
// removed; the group re-appears on the next session started there.
function hideDir(key) {
  S.hidden[key] = Date.now() / 1000;
  renderList(true);
  postJSON("/api/dirs/hide", { cwd: key })
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
    g.cwd, g.active.map(r => r.sid),
    g.parked.length,
    S.folds.has(g.cwd + "|parked") ? g.parked.map(r => r.sid) : 0,
    g.archived.length,
    S.folds.has(g.cwd + "|archived") ? g.archived.map(r => r.sid) : 0,
  ]));
}

function renderDirGroups(groups) {
  for (const g of groups) {
    const hd = el("div", "dirhead");
    hd.append(el("span", "dirname", g.cwd ? g.cwd.split("/").filter(Boolean).pop() : "no project"));
    if (g.cwd) hd.append(el("span", "dirpath", g.cwd));
    hd.append(el("span", "dircount", g.count + (g.count === 1 ? " session" : " sessions")));
    if (g.cwd) {                        // "+" only where a launch has a cwd
      const add = el("button", "dirnew", "+");
      add.title = "new session in " + g.cwd;
      add.onclick = () => openNewSession(g.cwd);
      hd.append(add);
    }
    // ✕ hides ANY group, including the projectless aggregate (g.cwd === "") —
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
      hide.title = g.cwd
        ? "hide this directory from the list (re-appears when a new session starts here)"
        : "hide the projectless sessions from the list (re-appears when a new one starts)";
    }
    hide.onclick = () => hideDir(g.cwd);
    hd.append(hide);
    $view.append(hd);
    if (g.active.length) {
      const grid = el("div", "sgrid");
      for (const row of g.active) grid.append(mountCard(row));
      $view.append(grid);
    }
    fold(g.cwd, "parked", g.parked);
    fold(g.cwd, "archived", g.archived);
  }
}

function mountCard(row) {
  const c = sessionCard(row);
  S.cards.set(row.sid, c);
  S.rowPrev.set(row.sid, JSON.stringify(row));
  return c;
}

// In-place update: same shape, so every visible row already has a card —
// rebuild the innards of just the cards whose row data changed. The card
// <a> itself survives, so scroll position, :hover, and the rest of the
// list's layout stay put.
function patchCards() {
  for (const row of S.sessions) {
    const card = S.cards.get(row.sid);
    if (!card) continue;
    const enc = JSON.stringify(row);
    if (enc === S.rowPrev.get(row.sid)) continue;
    S.rowPrev.set(row.sid, enc);
    const fresh = sessionCard(row);
    card.dataset.tab = fresh.dataset.tab;
    card.replaceChildren(...fresh.childNodes);
  }
}

// The REAL confirmation of an optimistic close: the sessions snapshot now shows
// the sid gone (or demoted to not-live) — the tab actually parked. Beacon the
// reconcile, drop the in-flight state, and un-grey the (about-to-be-rebuilt)
// card. Called on every sessions/-delta update, BEFORE the re-render so the
// rebuilt card shows the parked chip, not a stale 'closing…'.
function reconcileCloses() {
  for (const sid of Object.keys(S.closePend)) {
    const row = S.sessions.find(r => r.sid === sid);
    if (row && row.live) continue;             // still live — close hasn't landed
    clog(sid, "close.reconciled",
         { ms: Math.round(performance.now() - S.closePend[sid].t0) });
    S.closePend[sid].settle("reconciled");
    delete S.closePend[sid];
    S.closing.delete(sid);
    const card = S.cards.get(sid);
    if (card) card.classList.remove("closing");
  }
}

function fold(cwd, kind, rows) {
  if (!rows.length) return;
  const key = cwd + "|" + kind;
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
  a.dataset.tab = row.tab || "";        // state tint (style.css --state wash)
  a.href = "#/s/" + encodeURIComponent(row.sid);
  const st = row.stats || {};
  a.append(el("div", "proj", row.title || proj(row)));
  a.append(el("div", "sid", row.sid));
  // no "live" chip — the state tint + badge already say it; only the
  // inactive states (parked/gone) need explaining. A live windowed session
  // gets the ✕ close in the same corner slot instead — the header's close
  // reachable straight from the list.
  const corner = el("div", "corner");
  if (!row.live)
    corner.append(el("span", "chip2 parked", row.parked ? "parked" : "gone"));
  else if (S.closing.has(row.sid)) {           // optimistic close in flight
    a.classList.add("closing");                // greyed until the sessions poll parks it
    corner.append(el("span", "chip2 closing", "closing…"));
  } else if (row.kitty_window_id)
    corner.append(cardClose(row.sid));
  if (corner.childNodes.length) a.append(corner);
  const r = el("div", "row");
  const badge = el("span", "badge");
  badge.dataset.tab = row.tab || "";
  badge.append(el("span", "st"), tnode(TAB_LABEL[row.tab || ""] || row.tab));
  r.append(badge);
  if (st.commands) r.append(seg(st.commands + " cmds"));
  const tok = (st.tk_in | 0) + (st.tk_out | 0) + (st.tk_read | 0) + (st.tk_create | 0);
  if (tok) r.append(seg(kfmt(tok) + " tok"));
  if (st.cost) r.append(segc(usd(st.cost), "cost"));
  // recency, not age: started_at here read as staleness — a live session an
  // hour into its work showed "1h ago" while actively streaming
  if (lastActive(row)) r.append(seg(ago(lastActive(row))));
  if (row.git) r.append(gitChip(row.git));
  a.append(r);
  if (row.ctx) a.append(ctxBar(row.ctx));
  return a;
}
// the list-card ✕ — POST /api/session/<sid>/stop, the same graceful tab
// close as the session header's "✕ close" (kitty HUPs the tab, Claude Code
// exits, SessionEnd parks the mirror), with the same two-step confirm: first
// click arms ("close?") for 4 s, second fires. Lives inside the card <a>, so
// clicks must not bubble into a navigation. No hash change on success — the
// card demotes to parked on its own via the SSE sessions push.
// Deliberately different from the header ✕ (which keeps closure-local arm
// state): the arm + in-flight state live in S (S.armClose/S.closing, keyed
// by sid, the arm as a DEADLINE not a timer handle) because the per-tick
// sessions push rebuilds a changed card wholesale (patchCards
// replaceChildren) — and a live card's row changes every tick, so
// closure/DOM-held state died within ~1s of arming: the confirm reverted
// before it could be clicked. The constructor re-derives both states, so a
// rebuilt button resumes the arm with the REMAINING window; stale timers
// from replaced predecessors are neutered by the sid+deadline check.
function cardClose(sid) {
  const btn = el("button", "xclose", "✕");
  btn.title = "close this session's terminal tab";
  btn.disabled = S.closing.has(sid);
  const armed = () =>
    S.armClose && S.armClose.sid === sid && Date.now() < S.armClose.until;
  const disarm = () => {
    if (S.armClose && S.armClose.sid === sid && Date.now() >= S.armClose.until)
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
      S.armClose = { sid, until: Date.now() + ARM_MS };
      showArmed();
      return;
    }
    S.armClose = null;
    btn.classList.remove("arm");
    btn.disabled = true;
    // optimistic: grey THIS card + swap the ✕ to 'closing…' at once (a rebuild
    // from the sessions poll may lag a tick), and beacon the `close` lifecycle
    // (web-hint op=close). reconcileCloses swaps it to the parked chip when the
    // snapshot shows the sid go not-live; a failed POST reverts.
    S.closing.add(sid);
    S.closePend[sid] = optPending(sid, "close");
    btn.textContent = "closing…";
    const a = btn.closest(".scard");
    if (a) a.classList.add("closing");
    closeSession(sid, "card")
      .then(() => toast("done", "session closed", "terminal tab closed"))
      .catch(err => {
        S.closing.delete(sid);
        if (S.closePend[sid]) {
          S.closePend[sid].settle("dropped", { reason: "failed" });
          delete S.closePend[sid];
        }
        btn.disabled = false;
        btn.textContent = "✕";
        if (a) a.classList.remove("closing");
        clientFail(sid, "close", err);   // a lost/rejected /stop the audit can't see
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

// context-saturation bar — the account-limit bar's (acctPill's ubar) bigger
// sibling, one full row wherever it appears: session cards, the session
// header (big=true), agent cards. Accent fill, amber ≥70%, red ≥90%.
function ctxBar(cx, big) {
  const bar = el("div", "cbar" + (cx.pct >= 90 ? " hot" : cx.pct >= 70 ? " warn" : "")
                        + (big ? " big" : ""));
  bar.append(el("span", "clabel", "ctx"));
  const track = el("span", "ctrack");
  const fill = el("span", "cfill");
  fill.style.width = Math.max(0, Math.min(100, cx.pct)) + "%";
  track.append(fill);
  bar.append(track, el("span", "cpct", cx.pct + "%"));
  bar.append(el("span", "cdetail", kfmt(cx.used) + " / " + kfmt(cx.window)));
  return bar;
}

function updateHeadFromList() {
  const row = S.sessions.find(r => r.sid === S.cur);
  if (!row || !S.ses) return;
  if (S.ses.badge) setBadge(S.ses.badge, row.tab || "");
  // Keep the header's live/window state honest against the authoritative global
  // snapshot. `meta` is fetched ONCE at session-open, so a session opened during
  // its startup tag-race — the launch jumps straight to the new sid, but its
  // kitty pane isn't tagged claude_session=<sid> yet, so the server momentarily
  // reports it not-live (or live-but-window-not-yet-resolved during the grace) —
  // would otherwise FREEZE on that reading: the parked chip stuck on and every
  // live-gated action (stop/cancel/rewind/close/quick-commands) missing, so the
  // user can't even close the session (the reported bug). A later live↔parked
  // flip (kill, crash, resume) has the same staleness. Re-render the chrome ONLY
  // on a real change — not every per-tick tab change (that reflows the header
  // each second) — and not while drilled into a subagent (renderSessionChrome
  // clears agentFocus; the ← session rebuild picks it up on the way back) or mid
  // inline-rename. The window compare is gated on row.live: meta's
  // kitty_window_id is the live-RESOLVED id (blank until the pane is tagged)
  // while the list row's is the RAW audit id, so an unconditional compare would
  // spuriously rebuild a parked session's header (blank vs a stale raw id).
  const m = S.ses.meta;
  const winMoved = row.live
    && (m && m.kitty_window_id || "") !== (row.kitty_window_id || "");
  if (m && (!!m.live !== !!row.live || winMoved)) {
    m.live = row.live;
    m.kitty_window_id = row.kitty_window_id;
    m.parked = row.parked;
    const renaming = S.ses.projEl && S.ses.projEl.querySelector("input");
    if (!S.ses.agentFocus && !S.ses.monitorFocus && !S.ses.jobFocus && !renaming)
      renderSessionChrome(S.ses.tab);
  }
}

/* ---------- session view ---------- */

