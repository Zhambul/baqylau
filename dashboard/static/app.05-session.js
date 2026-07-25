"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.12-init.js for the boot/init sequence.

// Two SEPARATE retries that happen to share a delay — deliberately not one
// constant. META_RETRY_MS re-fetches the session meta after a failed GET (the
// view is unusable without it: no composer, no title, and global snapshots
// never repair it); SES_RECONNECT_MS re-opens the per-session SSE after the
// browser drops it. Same beat today, different failures, tunable apart.
const META_RETRY_MS = 1500;
const SES_RECONNECT_MS = 1500;
// A menu that closes on blur must let the CLICK land first: the mousedown blurs
// the textarea before the click event reaches the row, so closing synchronously
// would swallow every pick. (The rows themselves preventDefault on mousedown;
// this delay is what catches a click AWAY from the menu.)
const MENU_BLUR_MS = 150;

function showSession(sid, tab) {
  // unknown / retired tab (e.g. an old #/…/activity bookmark) → the mirror
  if (!["mirror", "agents", "monitors", "jobs", "memory", "errors"].includes(tab)) tab = "mirror";
  if (S.cur !== sid) {
    leaveSession();
    S.cur = sid;
    S.ses = { lastId: 0, mpos: 0, oldest: 0, stream: el("div", "stream"), stats: {},
              agents: [], costs: null, ctx: null, running: {}, meta: null, es: null, agentEs: null,
              fgRun: null, fgTimer: null, fgEnded: null, fgChipAt: null,  // live fg elapsed
              timer: null, poll: null, blocks: new Map(), moreEl: null,
              monitors: null, monitorFocus: null, monPoll: null,
              jobs: null, jobFocus: null, jobPoll: null,
              memory: null, noteTrail: null, noteFocus: null,
              loadingOlder: false, queue: [], pending: [],
              askPend: null, planPend: null,   // in-flight optimistic ask/plan decisions
              filter: { kind: "all" },            // cleared per session (new S.ses)
              // the view mode + its derived state: `view` is seeded from the
              // session's durable pref when meta lands, and until then from the
              // same default the server would serve — so the first paint doesn't
              // flash the wrong density. `viewOpen` holds the runs the user
              // expanded, `viewSeq` names items, `viewFill` bounds the auto-load
              view: VIEW_DEFAULT, viewOpen: new Set(), viewSeq: 0,
              viewTimer: null, viewFill: 0 };
    S.ses.stream.append(el("div", "waiting", "waiting for activity…"));
    // meta (live/kitty_window_id/title/…) comes ONLY from this fetch — global
    // snapshots never repair it (updateHeadFromList no-ops while meta is null),
    // so a transient failure left the whole view stuck unusable (composer
    // disabled, no title) until a reload. Retry while still on this session and
    // still unpopulated; the guards make a late retry after a leave a harmless
    // no-op.
    let resolveTries = 0;
    const loadMeta = () => fetch("/api/session/" + encodeURIComponent(sid))
      .then(r => r.json())
      .then(d => {
        if (S.cur !== sid || !S.ses) return;
        S.ses.meta = d;
        S.ses.stats = d.stats || {};
        S.ses.agents = d.agents || [];
        S.ses.costs = d.costs || null;
        S.ses.ctx = d.ctx || null;
        S.ses.running = d.running || {};
        // the durable per-session view mode (dashboard/prefs.py) — seeded before
        // the chrome is built, so the filter bar renders with the right segment
        // lit and the backlog collapses on first paint rather than flashing
        // verbose first
        if (VIEW_MODES.includes(d.view_mode)) S.ses.view = d.view_mode;
        renderSessionChrome(tab);
        applyViewMode();
        // a page opened MID-command ticks from the real start (the SSE `fgrun`
        // only fires on CHANGE, so without this seed a reload would show no
        // elapsed until the next command)
        setFgRun(d.fg_running || null);
        // startup TAG-RACE self-heal: a just-launched session momentarily
        // reports live:true with a BLANK kitty_window_id (its kitty pane isn't
        // tagged claude_session=<sid> yet, so session_payload can't resolve the
        // window). That partial meta fails BOTH composer gates — canSend
        // (live && window) AND canResume (!live) — so the box locks and the
        // live-gated ✕ close button never renders (the reported "no close
        // button + can't type, fixed only by reload"). Re-fetch until the pane
        // tags: authoritative and self-healing where the fragile global-poll
        // heal (updateHeadFromList, raw-vs-resolved window id spaces) misses.
        // Bounded — a truly headless session never resolves a window.
        if (d.live && !d.kitty_window_id && resolveTries < LAUNCH_RESOLVE_TRIES) {
          resolveTries++;
          setTimeout(loadMeta, LAUNCH_RESOLVE_MS);
        } else if (d.live && !d.kitty_window_id) {
          // the tag-race NEVER resolved — the composer + ✕ close stay dead. The
          // "no close button, can't type, only a reload fixes it" report, now a
          // row instead of a mystery (vs a truly headless session, which never
          // has a window and is EXPECTED to land here).
          clog(sid, "meta.stuck", { tries: resolveTries });
        } else if (d.live && d.kitty_window_id && resolveTries > 0) {
          clog(sid, "meta.resolved", { tries: resolveTries });   // self-heal worked
        }
      })
      .catch(() => {
        clog(sid, "meta.fail", {});   // the session-view meta GET rejected
        if (S.cur === sid && S.ses && !S.ses.meta) setTimeout(loadMeta, META_RETRY_MS);
      });
    loadMeta();
    // Initial stream content over a plain GET, NOT the SSE fresh-connect
    // backlog: _send gzips this HTML 8-9x, while SSE frames are never
    // compressed — on a remote/tunnel connection that difference IS the
    // "waiting for activity…" wait. The SSE then connects with the returned
    // cursors and only streams increments (the same no-gap resume contract a
    // reconnect uses); on any fetch failure it connects with zero cursors
    // and the server-side SSE backlog covers us like before.
    fetch("/api/session/" + encodeURIComponent(sid) + "/backlog")
      .then(r => r.json())
      .then(d => {
        if (S.cur !== sid || !S.ses) return;
        S.ses.lastId = Math.max(S.ses.lastId, d.last | 0);
        S.ses.mpos = Math.max(S.ses.mpos, d.mpos | 0);
        if (d.oldest != null) { S.ses.oldest = d.oldest | 0; updateMoreBtn(); }
        if (d.items && d.items.length) appendItems(d.items);
      })
      .catch(() => { clog(sid, "backlog.fail", {}); })   // stream may read empty
      .finally(() => { if (S.cur === sid) connectSession(sid); });
  }
  closeAgentStream();                       // leaving any agent drill-down view
  S.ses.tab = tab;
  renderSessionChrome(tab);
}

function connectSession(sid) {
  if (!S.ses || S.cur !== sid) return;
  // Never leak a prior EventSource. Two backlog fetches can race a leave/return
  // to the SAME sid (on a slow/tunnel link the backlog is deliberately a plain
  // GET), and each fires this from its .finally; the onerror reconnect re-enters
  // too. Without closing first, the earlier ES is orphaned — never closed, still
  // streaming, and its overlapping ops double-append into the feed.
  if (S.ses.es) { try { S.ses.es.close(); } catch (e) { /* already closed */ } }
  const es = new EventSource("/events/session/" + encodeURIComponent(sid)
                             + "?after=" + S.ses.lastId + "&mpos=" + S.ses.mpos);
  S.ses.es = es;
  // ops AND main-thread conversation arrive on this ONE event, already
  // interleaved oldest->newest by ts server-side (merge_live) — sending them as
  // two arrival-order events prepended a turn's text ABOVE its command in the
  // newest-top feed (the "messages come after commands" inversion).
  es.addEventListener("ops", (e) => {
    const d = JSON.parse(e.data);
    if (d.last <= S.ses.lastId && !d.items.length) return;
    S.ses.lastId = Math.max(S.ses.lastId, d.last);
    if (d.mpos != null) S.ses.mpos = Math.max(S.ses.mpos, d.mpos);
    // the initial (fresh-connection) backlog carries `oldest` — the smallest
    // op id painted; >0 means older blocks exist to lazy-load downward.
    if (d.oldest != null) { S.ses.oldest = d.oldest | 0; updateMoreBtn(); }
    appendItems(d.items);
  });
  es.addEventListener("stats", (e) => { if (!S.ses) return; S.ses.stats = JSON.parse(e.data); updateStatsRow(); });
  es.addEventListener("agents", (e) => { if (!S.ses) return; S.ses.agents = JSON.parse(e.data); updateAgents(); });
  es.addEventListener("costs", (e) => { if (!S.ses) return; S.ses.costs = JSON.parse(e.data); updateStatsRow(); });
  es.addEventListener("ctx", (e) => { if (!S.ses) return; S.ses.ctx = JSON.parse(e.data).ctx; updateStatsRow(); });
  es.addEventListener("git", (e) => {
    const g = JSON.parse(e.data).git || null;
    if (!S.ses) return;
    if (S.ses.meta) S.ses.meta.git = g;
    if (S.ses.gitChip) setGitChip(S.ses.gitChip, g);
  });
  es.addEventListener("title", (e) => {
    // a web rename or a fresh auto ai-title — retitle the header in place,
    // but never clobber an inline rename edit in progress
    const t = JSON.parse(e.data).title || "";
    if (!S.ses) return;
    if (S.ses.meta) S.ses.meta.title = t;
    if (t && S.ses.projEl && !S.ses.projEl.querySelector("input"))
      S.ses.projEl.textContent = t;
  });
  es.addEventListener("effort", (e) => {
    if (S.ses && S.ses.meta) {
      S.ses.meta.effort = JSON.parse(e.data).effort;
      if (S.ses.effortBtn) setEffortBtn(S.ses.effortBtn);
    }
  });
  es.addEventListener("running", (e) => { if (!S.ses) return; S.ses.running = JSON.parse(e.data); updateRunning(); });
  es.addEventListener("fgrun", (e) => { setFgRun((JSON.parse(e.data) || {}).fg || null); });
  es.addEventListener("errors", (e) => { updateErrCount(JSON.parse(e.data).count | 0); });
  es.addEventListener("monitors", (e) => { updateMonCount(JSON.parse(e.data).count | 0); });
  es.addEventListener("jobs", (e) => { updateJobCount(JSON.parse(e.data).count | 0); });
  es.addEventListener("memory", (e) => { updateMemCount(JSON.parse(e.data).count | 0); });
  es.addEventListener("ask", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    const newAsk = d.ask || null;
    // the REAL confirmation of an optimistic answer: the stash we submitted
    // against is gone (cleared, or replaced by a different ask) — swap the
    // greyed card away and beacon the reconcile latency
    const pend = S.ses.askPend;
    if (pend && pend.live && (!newAsk || newAsk.tool_use_id !== pend.id)) {
      pend.settle("reconciled");
      S.ses.askPend = null;
    }
    if (S.ses.meta) S.ses.meta.ask = newAsk;
    renderAsk();
  });
  es.addEventListener("ask-draft", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    applyAskDraft(d.draft);
  });
  es.addEventListener("composer-draft", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    applyComposerDraft(d.draft);
  });
  es.addEventListener("composer-queue", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    applyComposerQueue(d.queue);
  });
  es.addEventListener("suggestion", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    applySuggestion(d.suggestion);
  });
  es.addEventListener("plan", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    const newPlan = d.plan || null;
    // real confirmation of an optimistic plan decision — the stash dropped
    const pend = S.ses.planPend;
    if (pend && pend.live && (!newPlan || newPlan.tool_use_id !== pend.id)) {
      pend.settle("reconciled");
      S.ses.planPend = null;
    }
    if (S.ses.meta) S.ses.meta.plan = newPlan;
    renderPlan();
  });
  es.addEventListener("tasks", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    if (S.ses.meta) S.ses.meta.tasks = d.tasks || null;
    renderTasks();
  });
  es.addEventListener("goal", (e) => {
    const d = JSON.parse(e.data);
    if (!S.ses) return;
    if (S.ses.meta) S.ses.meta.goal = d.goal || null;
    renderGoal();
  });
  es.addEventListener("tab", (e) => {
    const d = JSON.parse(e.data);
    // while drilled into a subagent the badge/wash belong to that agent's
    // status (setBadgeAgent) — a session tab event must not repaint them
    // (same focus guard as updateRunning/updateStatsRow).
    if (S.ses && S.ses.badge && !S.ses.agentFocus) setBadge(S.ses.badge, d.tab || "");
    if (S.ses && S.ses.composerMode) S.ses.composerMode(d.tab || "");
    if (S.ses && S.ses.stopMode) S.ses.stopMode(d.tab || "");
    if (S.ses && S.ses.quickMode) S.ses.quickMode(d.tab || "");
    // patch the open session's row so the session strip reacts before the
    // next global snapshot lands (item 4: react to the per-session tab event)
    const row = S.sessions.find(r => r.sid === S.cur);
    if (row) row.tab = d.tab || "";
    renderAttention();
    // a settled tab retires the newest run's grey dot + ticking elapsed (the
    // turn is over, so nothing in that run is still going)
    applyViewMode();
  });
  es.onopen = () => { $conn.dataset.on = "1"; sseMark("session", true, { sid }); };
  es.onerror = () => {
    sseMark("session", false, { sid });
    es.close();
    if (S.cur !== sid) return;
    S.ses.timer = setTimeout(() => connectSession(sid), SES_RECONNECT_MS);
  };
}

// Stream items ({g, t, html}) fold into collapsible BLOCK cards by copy-group
// id: label ops become the block's summary chips (start chip, then the
// finished/duration chip), everything else goes to the fold-away body. The
// LAST `KEEP_OPEN` blocks stay expanded (the recent-activity tail you're
// actually reading); anything older folds to its one-line summary as new
// blocks push it out of the window — unless the user toggled it themselves,
// which always wins. Ungrouped items (messages, file-op one-liners) stay
// inline.
const KEEP_OPEN = 5;
const HISTORY_FETCH = 40;      // blocks per lazy-backlog /history page

function enforceWindow() {
  const blocks = [...S.ses.blocks.values()];
  const cut = blocks.length - KEEP_OPEN;
  blocks.forEach((b, i) => {
    if (i < cut && !b.userSet && b.root.dataset.open === "1")
      b.root.dataset.open = "0";
  });
}

// The stream is a FEED: newest on top. Items arrive oldest→newest and each
// is inserted at the top, so the batch lands newest-first; a block keeps the
// position of its first op (its body still reads top-down) and new blocks
// appear above it.
// A collapsible block card (root/head/chips/sum/body + fold-toggle handler),
// unplaced — the caller inserts .root and decides tracking. Shared by the live
// top-prepend path (appendItems) and the older-history bottom-append path
// (appendOlder).
function createBlock() {
  const root = el("div", "blk");
  root.dataset.open = "1";                       // enforceWindow folds elders
  root.dataset.kind = "commands";                // refineBlockKind upgrades to "agents"
  const head = el("div", "bhead");
  const chips = el("span", "bchips");
  const sum = el("span", "bsum");
  const body = el("div", "bbody");
  head.append(chips, sum);
  root.append(head, body);
  const b = { root, chips, sum, body, userSet: false, kindLocked: false };
  head.onclick = (e) => {
    if (e.target.closest("a")) return;           // ⧉ links keep working
    b.userSet = true;
    root.dataset.open = root.dataset.open === "1" ? "0" : "1";
  };
  return b;
}

/* The live elapsed chip on the IN-FLIGHT foreground command (docs/dashboard.md,
   *Live command elapsed*). The server says WHICH block is running and since
   when (`fgrun` = sessionapi.fg_running — the fg-live hand-off's tool_use_id,
   which IS the block's copy-group id, plus its start); the seconds are counted
   HERE, so the number advances on a 1s local tick instead of costing an event
   per second. The chip lives in the block's `.bchips` summary row (so a folded
   block still shows it) right after the `▶ foreground` chip, and is retired the
   moment the block's own finish chip ("■ finished · 3.2s") lands — that chip is
   the authoritative duration, and a ticking twin beside it would only disagree.
   Foreground only: a bg job / monitor / subagent has its own card with a
   "running for" line, and the fg block is the one the eye is on. */
const FG_TICK_MS = 1000;

function fgClearChip(g) {
  const b = g && S.ses.blocks.get(g);
  const c = b && b.chips.querySelector(".blive");
  if (c) c.remove();
}

function tickFgElapsed() {
  const ses = S.ses;
  if (!ses) return;
  const fg = ses.fgRun;
  if (ses.fgChipAt && (!fg || ses.fgChipAt !== fg.g)) {
    fgClearChip(ses.fgChipAt);              // the command it belonged to ended
    ses.fgChipAt = null;
  }
  if (!fg) {
    if (ses.fgTimer) { clearInterval(ses.fgTimer); ses.fgTimer = null; }
    return;
  }
  const b = ses.blocks.get(fg.g);
  if (!b) return;              // the block's ops haven't landed yet — next tick
  let c = b.chips.querySelector(".blive");
  if (!c) { c = el("span", "chip blive"); b.chips.append(c); }
  ses.fgChipAt = fg.g;
  c.textContent = "⏱ " + dur(Date.now() / 1000 - fg.start_ts);
}

function setFgRun(fg) {
  const ses = S.ses;
  if (!ses) return;
  // the finish chip beat the `fgrun` clear (both ride the same 0.6s tick, in no
  // fixed order) — don't resurrect a ticker on a command already reported done
  if (fg && fg.g === ses.fgEnded) fg = null;
  ses.fgRun = fg;
  if (fg && !ses.fgTimer) ses.fgTimer = setInterval(tickFgElapsed, FG_TICK_MS);
  tickFgElapsed();
  // a collapsed run showing this command re-anchors its elapsed on the command's
  // real start (and drops it once the command is done) — the same hand-off the
  // ⏱ chip above uses, applied to the summary line standing in for the block
  applyViewMode();
}

// A single copy-group's body is capped: a long-lived group (a bg stream, a
// monitor, `tail -f`, a subagent) keeps emitting line/code/gut ops that all
// share ONE block id, and the `.stream` child cap in appendItems() only counts
// top-level cards — never the ops nested inside one — so without this a
// continuous stream grows the DOM without bound (one node per op, forever).
const MAX_BLOCK_BODY = 800;

// Add one grouped item to a block: label ops become summary chips, everything
// else appends to the body (and seeds the one-line summary). Body always reads
// oldest->newest (top-down), matching arrival order.
function fillBlock(b, it) {
  if (it.t === "label") {
    // A further label op on the block carrying the live elapsed chip IS this
    // command's finish chip ("■ finished · 3.2s") — retire the ticker here
    // rather than wait for the `fgrun` clear, so the counting number can never
    // be seen still running next to the final duration.
    if (S.ses && S.ses.fgRun && S.ses.fgRun.g === it.g) {
      fgClearChip(it.g);
      S.ses.fgEnded = it.g;
      S.ses.fgRun = null;
    }
    b.chips.insertAdjacentHTML("beforeend", it.html);
  } else {
    b.body.insertAdjacentHTML("beforeend", it.html);
    while (b.body.childElementCount > MAX_BLOCK_BODY)
      b.body.firstElementChild.remove();       // trim oldest (top) — arrival order
    if (!b.sum.textContent && b.body.lastElementChild) {
      const line = (b.body.lastElementChild.textContent || "")
        .trim().split("\n").find(l => l.trim());
      if (line) b.sum.textContent = line.slice(0, 160);
    }
  }
}

function appendItems(items) {
  const st = S.ses.stream;
  const w = st.querySelector(".waiting");
  if (w) w.remove();
  for (const it of items) {
    if (!it.g) {
      st.insertAdjacentHTML("afterbegin", it.html);
      const elem = st.firstElementChild;
      if (elem) stampItem(elem, it);
      continue;
    }
    let b = S.ses.blocks.get(it.g);
    if (!b) {
      b = createBlock();
      st.prepend(b.root);
      S.ses.blocks.set(it.g, b);
      b.root.dataset.vk = String(++S.ses.viewSeq);
      b.root.dataset.vt = String(Date.now() / 1000);
    }
    fillBlock(b, it);
    refineBlockKind(b, it);
    applyFilterTo(b.root);
  }
  drainQueue(items);
  drainPending(items);
  dropSuperseded(items);
  enforceWindow();
  while (st.childElementCount > 3000) {
    let last = st.lastElementChild;
    if (last === S.ses.moreEl) last = last.previousElementSibling;  // the load-older
    if (!last) break;                          //   affordance stays pinned at the bottom
    if (last.classList.contains("blk"))        // evict a trimmed block card, or later
      for (const [g, b] of S.ses.blocks)       //   ops for its group would render into
        if (b.root === last) { S.ses.blocks.delete(g); break; }   // a detached node
    last.remove();
  }
  applyViewMode();               // re-cut the collapsed runs over the final DOM
  updateFilterCount();
}

// The lazy-backlog downward path (item 3): a chunk of OLDER items (server order
// oldest->newest) appended at the BOTTOM of the feed — the feed is newest-top,
// so older loads downward, and each successive page is older still, going lower.
// Blocks born in this chunk start FOLDED and are NOT tracked in the live
// S.ses.blocks map or the KEEP_OPEN window (they are history, not the live
// tail). A group that STRADDLES the load boundary (already live in the map) has
// its older ops appended into the existing card body at the end — acceptable;
// older ops trail the newer ones (docs/dashboard.md).
function appendOlder(items) {
  const st = S.ses.stream;
  const local = new Map();                       // g -> block, for this chunk only
  const frag = document.createDocumentFragment();
  for (const it of items) {
    if (!it.g) {
      const tmp = el("div");
      tmp.innerHTML = it.html;
      const elem = tmp.firstElementChild;
      if (elem) { stampItem(elem, it); frag.append(elem); }
      continue;
    }
    const live = S.ses.blocks.get(it.g);         // straddling group: fold in-place
    if (live) {
      fillBlock(live, it);
      refineBlockKind(live, it);
      applyFilterTo(live.root);
      continue;
    }
    let b = local.get(it.g);
    if (!b) {
      b = createBlock();
      b.root.dataset.open = "0";                 // history blocks arrive folded
      b.root.dataset.vk = String(++S.ses.viewSeq);
      b.root.dataset.vt = String(Date.now() / 1000);
      local.set(it.g, b);
      frag.append(b.root);
    }
    fillBlock(b, it);
    refineBlockKind(b, it);
    applyFilterTo(b.root);
  }
  if (S.ses.moreEl) st.insertBefore(frag, S.ses.moreEl);
  else st.append(frag);
  applyViewMode();
  updateFilterCount();
}

// Render a self-contained mirror snapshot into an ARBITRARY container (the
// resume-picker's preview panel), not the live #stream. Same server items
// ({g,t,html}, oldest->newest) and same block grouping as appendOlder, but into
// a throwaway local map (never S.ses), no filters/eviction. Blocks render FOLDED
// (like history) — a compact scannable peek: command/file/agent blocks collapse
// to their one-line summary, while conversation messages (ungrouped items) show
// inline in full; a click on any block header expands it.
function renderPreview(container, items) {
  container.textContent = "";
  const local = new Map();                        // g -> block, this render only
  for (const it of items) {
    if (!it.g) {
      const tmp = el("div");
      tmp.innerHTML = it.html;
      const elem = tmp.firstElementChild;
      if (elem) container.append(elem);
      continue;
    }
    let b = local.get(it.g);
    if (!b) {
      b = createBlock();
      b.root.dataset.open = "0";                  // previews start folded (compact)
      local.set(it.g, b);
      container.append(b.root);
    }
    fillBlock(b, it);
    refineBlockKind(b, it);
  }
  if (!container.childElementCount)
    container.append(el("div", "nspreview-empty", "no mirror history"));
}

// The "load older" affordance: a button pinned at the BOTTOM of the feed (a
// child of the stream, so appendItems' top-prepends never disturb it), shown
// while older blocks remain (S.ses.oldest > 0) and hidden once /history is
// exhausted (oldest 0). Each click fetches the previous page and appends it
// downward via appendOlder; filters apply to those items in appendOlder.
function ensureMoreEl() {
  const ses = S.ses;
  if (!ses) return null;
  if (ses.moreEl && ses.moreEl.isConnected) return ses.moreEl;
  const b = el("button", "loadmore");
  b.hidden = true;
  b.onclick = () => loadOlder();
  ses.moreEl = b;
  ses.stream.append(b);                          // bottom of the feed
  return b;
}

function updateMoreBtn() {
  const ses = S.ses;
  if (!ses) return;
  const b = ensureMoreEl();
  if (!b) return;
  const has = (ses.oldest | 0) > 0;
  b.hidden = !has;
  if (has && !ses.loadingOlder)
    // "blocks" only in verbose, where a block IS what appears. In default/focus
    // the promise is kept in VISIBLE items (see loadOlder) — most of the blocks
    // fetched to satisfy it collapse into the summary lines, so promising
    // "blocks" there would be promising the wrong noun.
    b.textContent = "load older · " + HISTORY_FETCH
      + (ses.view === "verbose" ? " more blocks…" : " more…");
}

// What the reader can actually SEE right now: unhidden stream items plus the
// collapsed-run summary lines standing in for the rest.
function visibleCount() {
  const ses = S.ses;
  if (!ses || !ses.stream) return 0;
  return streamItems().filter(elem => !itemHidden(elem)).length
    + ses.stream.querySelectorAll(".vsum").length;
}

const OLDER_TRIES = 6;        // /history requests one fill may spend
const OLDER_PAGE_MAX = 400;   // blocks per request ceiling

// How big to make the NEXT page, given what this one cost: `blocks` fetched
// yielded `gained` visible items against a target of `want`. Aim the next
// request at the remaining shortfall at the observed yield — a page that
// collapsed almost entirely (or merged wholly into an existing run, gaining
// nothing) reaches straight for the ceiling. Converges in 2-3 requests instead
// of creeping 40 at a time, which matters because every /history call re-merges
// the session's whole op+conversation history server-side.
function olderPageSize(want, gained, blocks) {
  const per = gained > 0 ? blocks / gained : OLDER_PAGE_MAX;
  const next = Math.ceil((want - gained) * per);
  return Math.max(HISTORY_FETCH, Math.min(next, OLDER_PAGE_MAX));
}

// Load older history until `want` MORE VISIBLE items have landed (default: the
// HISTORY_FETCH the button promises), history is exhausted, or the request
// budget runs out.
//
// It loops because the server counts BLOCKS and the modes hide most of them: one
// 40-block page can collapse to two summary lines — or to nothing at all, when
// every block in it merges into the run already at the boundary. That was the
// "load older 40 blocks doesn't give me 40" report; the fix has to live here,
// since only the client knows what its current mode leaves visible.
function loadOlder(want) {
  const ses = S.ses;
  if (!ses || ses.loadingOlder || (ses.oldest | 0) <= 0) return;
  const target = want || HISTORY_FETCH;
  const start = visibleCount();
  const sid = S.cur;
  let tries = 0, blocks = HISTORY_FETCH;
  ses.loadingOlder = true;
  if (ses.moreEl) ses.moreEl.textContent = "loading…";

  const step = () => fetch("/api/session/" + encodeURIComponent(sid)
                           + "/history?before=" + (ses.oldest | 0)
                           + "&blocks=" + blocks)
    .then(r => r.json())
    .then(d => {
      if (S.cur !== sid || !S.ses) return;         // navigated away mid-fetch
      tries++;
      appendOlder(d.items || []);
      ses.oldest = d.oldest | 0;
      const gained = visibleCount() - start;
      if (gained >= target || (ses.oldest | 0) <= 0 || tries >= OLDER_TRIES) return;
      blocks = olderPageSize(target, gained, blocks);
      if (ses.moreEl) ses.moreEl.textContent = "loading… " + gained + "/" + target;
      return step();
    });

  step().catch(() => {}).then(() => {
    if (S.cur !== sid || !S.ses) return;
    ses.loadingOlder = false;
    updateMoreBtn();
  });
}

/* ---------- stream search + kind filters ---------- */
// Every top-level stream child carries a data-kind (commands · files · agents ·
// messages) so the filter bar can hide non-matching items via a CSS class
// (never removing them — SSE keeps appending, and folded bodies stay
// textContent-searchable). data-kind is stamped once at creation (`stampItem`),
// never re-derived per filter pass, and it is derived from the SERVED activity
// class rather than from the rendered chip text — the sniffing this file used to
// do now has one owner, server-side (dashboard/opshtml/actclass.py).

// Which filter kind each served ACTIVITY CLASS belongs to. The `act` stamp
// (dashboard/opshtml/actclass.py — one owner, server-side) replaced the glyph
// regex the page used to run over the block-opening chip text: same answer, but
// classified where the structured op is, not re-sniffed out of rendered HTML.
const ACT_KIND = {
  bash: "commands", bg: "commands", monitor: "commands", warn: "commands",
  agent: "agents", read: "files", edit: "files", write: "files",
  msg: "messages",
};

function refineBlockKind(b, it) {
  if (b.root.dataset.kind === "agents") return;        // agent wins, monotonic
  if (it.g) b.root.dataset.g = it.g;                   // the run pass reads it
  if (/class="og"/.test(it.html)) {                    // outer gutter == nested subagent job
    b.root.dataset.kind = "agents";
    b.root.dataset.act = "agent";
    return;
  }
  if (it.bad) b.root.dataset.bad = "1";                // any failing op reddens the block
  if (it.act && !b.kindLocked) {                       // the block-opening chip
    b.root.dataset.kind = ACT_KIND[it.act] || "commands";
    b.root.dataset.act = it.act;
    b.kindLocked = true;
  }
}

function ungroupedKind(it, elem) {
  if (it.t === "msg") return "messages";
  // memory-wiki file ops carry data-mem (❖) — their own kind, checked before
  // the generic files test (a memory op is also a data-v file op).
  if (elem.matches("[data-mem]") || elem.querySelector("[data-mem]")) return "memory";
  // file-op one-liners carry the click-to-view id as data-v (.opl / gut ops)
  if (elem.matches("[data-v]") || elem.querySelector("[data-v]")) return "files";
  return "commands";
}

// Stamp one freshly-created top-level stream child with everything the view-mode
// pass reads off the DOM: its filter kind, its served activity class + failure
// flag, the conversation kind (focus mode narrows on it), a monotonic key that
// names the item for as long as it lives, and its arrival time (the fallback
// anchor for the live elapsed on a run with no running command in it).
function stampItem(elem, it) {
  elem.dataset.kind = ungroupedKind(it, elem);
  if (it.act) elem.dataset.act = it.act;
  if (it.bad) elem.dataset.bad = "1";
  if (it.add) elem.dataset.add = String(it.add);   // a mutation's line counts, for
  if (it.rem) elem.dataset.rem = String(it.rem);   //   focus mode's edit summary
  if (it.kind) elem.dataset.msg = it.kind;
  if (it.meta) elem.dataset.injected = "1";   // a prompt Claude Code injected,
  //                                             not one the human typed
  elem.dataset.vk = String(++S.ses.viewSeq);
  elem.dataset.vt = String(Date.now() / 1000);
  applyFilterTo(elem);
}

function streamItems() {
  return [...S.ses.stream.children].filter(el => el.dataset && el.dataset.kind);
}

function matchesFilter(elem) {
  const f = (S.ses && S.ses.filter) || { kind: "all" };
  if (f.kind !== "all" && elem.dataset.kind !== f.kind) return false;
  return true;
}

// Hidden by EITHER control: `.fhide` is the kind filter's, `.vhide` the view
// mode's. Deliberately two classes over two independent axes — one shared class
// would make whichever pass ran last un-hide the other's items.
function itemHidden(elem) {
  return elem.classList.contains("fhide") || elem.classList.contains("vhide");
}

function applyFilterTo(elem) {
  if (!elem || !elem.dataset || !elem.dataset.kind) return;
  elem.classList.toggle("fhide", !matchesFilter(elem));
}

function applyFilter() {
  if (!S.ses) return;
  for (const elem of streamItems()) applyFilterTo(elem);
  updateFilterCount();
}

function updateFilterCount() {
  const ses = S.ses;
  if (!ses || !ses.countEl || !ses.countEl.isConnected) return;
  const items = streamItems();
  const shown = items.filter(elem => !itemHidden(elem)).length;
  ses.countEl.textContent = shown + " of " + items.length + " shown";
}

const FILTER_KINDS = ["all", "commands", "files", "memory", "agents", "messages"];

/* ---------- view modes: verbose · default · focus ---------- */
// Claude Code's three transcript densities, over the web mirror (docs/
// dashboard.md, *View modes*). This changes only what the BROWSER paints — it
// never touches Claude Code's own `viewMode` setting, and the kitty mirror keeps
// painting everything.
//
//   verbose — every block; nothing is ever hidden
//   default — runs of adjacent read/command/agent activity collapse into ONE
//             clickable summary line; file MUTATIONS stay expanded, so an edit
//             always breaks the run and is always visible
//   focus   — your prompts, ONE message per turn (the one it ends on) and one
//             line of edits; every intermediate step folds away. That message is
//             greyed while the turn is still running (it is provisional) and
//             full weight once the turn settles (it is the result)
//
// Both non-verbose modes also drop INJECTED prompts — user-shaped turns Claude
// Code wrote itself (a Stop hook's feedback, a loaded skill's body, a resume
// nudge; `data-injected`, from the transcript's isMeta). Verbose keeps them: it
// shows the transcript as it is, and they are genuinely in it.
//
// Must match dashboard/prefs.py VIEW_MODES / VIEW_DEFAULT (grep-tested). The
// list is in CONTROL order (densest to sparsest); the default is NOT its first
// entry — a session nobody switched opens at "default", like the TUI.
const VIEW_MODES = ["verbose", "default", "focus"];
const VIEW_DEFAULT = "default";

// Which activity classes each mode folds into a summary. Everything not listed
// stays its own visible block, which is also what an unclassified item gets —
// a classification gap fails toward SHOWING content, never toward hiding it.
const VIEW_FOLD = {
  verbose: [],
  default: ["bash", "read", "agent"],
  focus: ["bash", "read", "agent", "bg", "monitor", "edit", "write"],
};

// THE SUMMARY VOCABULARY — Claude Code's own, extracted from the 2.1.220 binary
// (docs/dashboard.md, *View modes* records the full table and how it was read):
// [counter, active verb, done verb, singular unit, plural unit], in Claude
// Code's own emission ORDER. Each fragment is "<verb> <n> <unit>"; the FIRST
// fragment is capitalized and the rest are not; they join with ", "; and while
// the run is still running the participle form is used and the line ends in "…".
// The keys are actclass.ACTS tokens (plus the two memory flavours), so a new act
// with no row here would be counted into nothing (grep-tested both ways).
const VIEW_FRAGMENTS = [
  ["edit", "editing", "edited", "file", "files"],
  ["read", "reading", "read", "file", "files"],
  ["agent", "running", "ran", "agent", "agents"],
  ["bash", "running", "ran", "shell command", "shell commands"],
  ["bg", "running", "ran", "background job", "background jobs"],
  ["monitor", "watching", "watched", "monitor", "monitors"],
  ["mem-read", "recalling", "recalled", "memory", "memories"],
  ["mem-write", "writing", "wrote", "memory", "memories"],
];

// Claude Code counts a Write as an edit (one `editFileCount` over its whole
// edit-tool set), so the two share a fragment here too — the mirror still shows
// them as distinct Update/Write one-liners when expanded.
const VIEW_COUNTER = { write: "edit" };

// Don't show a run's elapsed until it has actually been running a moment —
// Claude Code's own threshold for the same chip, and it keeps a fast run from
// flashing "· 0s".
const VIEW_ELAPSED_MIN_S = 2;
// Collapsing can leave the screen nearly empty (focus over a command-heavy tail
// hides almost everything), so a mode switch tops the feed up to VIEW_FILL_MIN
// visible items. VIEW_FILL_TRIES bounds how many TIMES that may fire per switch
// — each one runs loadOlder(), which has its own OLDER_TRIES request budget, so
// no fill can walk a long session's whole backlog.
const VIEW_FILL_TRIES = 3;
const VIEW_FILL_MIN = 6;

// Which counter one item feeds: its activity class, with memory-wiki file ops
// (the ❖ ops, `data-mem`) routed to the memory fragments — Claude Code words
// those as "recalled"/"wrote memories" rather than file reads and edits.
function viewCounter(elem) {
  const act = elem.dataset.act || "";
  const mem = elem.dataset.kind === "memory";
  if (mem && act === "read") return "mem-read";
  if (mem && (act === "edit" || act === "write")) return "mem-write";
  return VIEW_COUNTER[act] || act;
}

// The one-line summary of a run, as nodes: "Read 3 files, ran 2 shell commands"
// (done) / "Reading 3 files, running 2 shell commands…" (still going). `counts`
// is a counter->n map carrying optional `add`/`rem` line totals for the edit
// fragment, whose diffstat Claude Code prints right after "N files".
function viewSummaryNodes(counts, running) {
  const out = [];
  for (const [key, active, done, one, many] of VIEW_FRAGMENTS) {
    const n = counts[key] | 0;
    if (!n) continue;
    let verb = running ? active : done;
    if (!out.length) verb = verb[0].toUpperCase() + verb.slice(1);
    else out.push(tnode(", "));
    out.push(tnode(verb + " "), el("b", "", String(n)),
             tnode(" " + (n === 1 ? one : many)));
    if (key === "edit" && (counts.add || counts.rem)) {
      out.push(tnode(" "));
      if (counts.add) out.push(el("span", "dadd", "+" + counts.add));
      if (counts.add && counts.rem) out.push(tnode(" "));
      if (counts.rem) out.push(el("span", "drem", "-" + counts.rem));
    }
  }
  if (running) out.push(tnode("…"));
  return out;
}

// A run's collapsed stand-in. `members` are the folded items it speaks for (its
// key is the OLDEST member's — runs grow at the newest end, so that key is
// stable as the run absorbs new items and the user's expansion survives).
function buildRunSummary(key, members, running, anchor, bad, open) {
  const row = el("div", "vsum");
  row.dataset.run = key;
  row.dataset.open = open ? "1" : "0";
  row.append(el("span", "vdot" + (running ? "" : bad ? " bad" : " done")));
  const text = el("span", "vtext");
  const counts = { add: 0, rem: 0 };
  for (const m of members) {
    const c = viewCounter(m);
    if (c) counts[c] = (counts[c] | 0) + 1;
    counts.add += +(m.dataset.add || 0);       // served per item (actclass.diffstat)
    counts.rem += +(m.dataset.rem || 0);
  }
  text.append(...viewSummaryNodes(counts, running));
  row.append(text);
  const timer = el("span", "vtimer");
  row.append(timer);
  // the summary stays PUT when expanded (▾) — it is the only way back to
  // collapsed, and it keeps naming what the revealed blocks below it are
  row.append(el("span", "vcaret", open ? "▾" : "▸"));
  if (running && anchor) {
    row.dataset.anchor = String(anchor);
    paintRunTimer(row);
  }
  row.onclick = () => {
    const open = S.ses.viewOpen;
    if (open.has(key)) open.delete(key);
    else open.add(key);
    applyViewMode();
  };
  return row;
}

// The ticking " · 12s" on a still-running run — the same live-elapsed idea as the
// foreground command's ⏱ chip (the server says since WHEN, the browser counts),
// reusing that chip's anchor when the run contains the running command.
function paintRunTimer(row) {
  const anchor = +row.dataset.anchor || 0;
  const timer = row.querySelector(".vtimer");
  if (!timer) return;
  const secs = Date.now() / 1000 - anchor;
  timer.textContent = (anchor && secs >= VIEW_ELAPSED_MIN_S)
    ? " · " + dur(secs) : "";
}

function tickRunTimers() {
  const ses = S.ses;
  if (!ses) return;
  const rows = [...ses.stream.querySelectorAll(".vsum[data-anchor]")];
  if (!rows.length) {
    clearInterval(ses.viewTimer);
    ses.viewTimer = null;
    return;
  }
  rows.forEach(paintRunTimer);
}

// The whole pass: decide each item's disposition, cut maximal runs of foldable
// ones, and put a summary line where each run sits. Derived entirely from the
// DOM + the current mode, so it is safe to re-run after any append — which is
// how a live stream keeps its collapse correct as blocks arrive.
// Undo everything a previous pass stamped on the items (hidden flag + the
// expanded-run rail). Both exits of applyViewMode go through it: a mark left
// behind would draw a rail under a run that is no longer open.
function clearViewMarks(items) {
  for (const it of items)
    it.classList.remove("vhide", "vdim", "vrun", "vrun-last");
}

function applyViewMode() {
  const ses = S.ses;
  if (!ses || !ses.stream) return;
  const mode = VIEW_MODES.includes(ses.view) ? ses.view : VIEW_DEFAULT;
  const items = streamItems();
  if (mode === "verbose") {
    if (ses.viewSig === "verbose") return;      // already plain — nothing to undo
    for (const old of [...ses.stream.children])
      if (old.classList.contains("vsum")) old.remove();
    clearViewMarks(items);
    ses.viewSig = "verbose";
    updateFilterCount();
    return;
  }

  const fold = VIEW_FOLD[mode] || [];
  // DOM order is newest -> oldest, so "the first reply seen since the last
  // prompt" IS that turn's final one — which is the only assistant prose focus
  // mode keeps. A prompt closes the turn: items below it are the older one's.
  const fgg = (ses.fgRun && ses.fgRun.g) || "";
  const busy = typeof BUSY_TABS !== "undefined" && BUSY_TABS.includes(liveTab());
  let sawReply = false;
  // Still inside the NEWEST turn: the feed is newest-top and a turn reads
  // [replies … activity … prompt], so everything above the first prompt we meet
  // belongs to the turn in progress.
  let inNewestTurn = true;
  const disp = items.map(elem => {
    const kind = elem.dataset.kind;
    if (kind === "messages") {
      const mk = elem.dataset.msg || "";
      // An INJECTED prompt (Claude Code's isMeta — a Stop hook's feedback, a
      // loaded skill's whole SKILL.md body, a resume nudge) is not something
      // you said, so neither non-verbose mode shows it, and it does NOT close
      // the turn: the reply that follows it belongs to the prompt you actually
      // typed, and treating it as a boundary would surface a second "final"
      // reply per hook firing.
      if (elem.dataset.injected) return "hide";
      if (mk === "prompt") { sawReply = false; inNewestTurn = false; return "show"; }
      if (mode === "focus" && mk === "message") {
        // Exactly ONE message survives per turn — the newest, which is the one
        // the turn ends on. The rest are the running commentary and stay hidden.
        //
        // While the turn is STILL GOING that newest message is PROVISIONAL: more
        // prose (and the actual result) is still coming, so it is greyed rather
        // than presented as the answer. Once the turn settles it is the result,
        // and it goes to full weight beside the one-line summary of what the turn
        // did. Greying it only while in flight is the difference between "this is
        // the answer" and "this is where it's got to".
        const newest = !sawReply;
        sawReply = true;
        if (!newest) return "hide";
        return (busy && inNewestTurn) ? "dim" : "show";
      }
      return "show";
    }
    return fold.includes(elem.dataset.act || "") ? "fold" : "show";
  });

  // PLAN first, mutate second: the runs are computed into a list, and the DOM is
  // only rebuilt when the plan actually differs from the painted one (the
  // signature below). Without that guard every SSE tick tore down and re-created
  // every summary line — which reflows the feed under a reader who has scrolled
  // back, and drops an in-progress text selection. Same reasoning, and the same
  // shape, as `statsSig` for the header.
  const plan = [];
  const strays = [];                   // hidden-but-uncounted (an injected prompt)
  const dims = items.filter((_e, k) => disp[k] === "dim");
  const isDim = new Set(dims);
  // "dim" is a PAINT, not a placement: it continues a run exactly as "hide" did,
  // so greying mid-turn prose leaves the collapse semantics untouched — the runs
  // either side of it still merge into one summary. Only "show" ends a run.
  const inRun = d => d === "fold" || d === "hide" || d === "dim";
  // …and a dimmed item is never hidden, wherever it falls: inside a collapsed
  // run's span, or trailing it as a stray.
  const hideIt = elem => { if (!isDim.has(elem)) elem.classList.add("vhide"); };
  let i = 0;
  while (i < items.length) {
    if (!inRun(disp[i])) { i++; continue; }
    let j = i, last = i;
    while (j < items.length && inRun(disp[j])) {
      if (disp[j] === "fold") last = j;
      j++;
    }
    const span = items.slice(i, last + 1);
    const members = span.filter((_, k) => disp[i + k] === "fold");
    // hidden items TRAILING the run's last folded member aren't part of it (they
    // hide on their own); a span with no folded member at all is all-strays
    strays.push(...items.slice(members.length ? last + 1 : i, j));
    i = j;
    if (!members.length) continue;
    const key = span[span.length - 1].dataset.vk || "";
    const running = members.some(m => m.dataset.g && m.dataset.g === fgg)
      || (span[0] === items[0] && busy);
    plan.push({
      key, span, members, running,
      open: ses.viewOpen.has(key),
      bad: members.some(m => m.dataset.bad === "1"),
      anchor: running
        ? ((ses.fgRun && members.some(m => m.dataset.g === fgg))
           ? ses.fgRun.start_ts : +(span[span.length - 1].dataset.vt || 0))
        : 0,
    });
  }
  // Everything the painted lines DEPEND on — deliberately not the elapsed
  // seconds, which the 1s timer owns (a signature carrying the clock would
  // rebuild the DOM every second, the very thing this avoids).
  const sig = mode + "!" + strays.map(s => s.dataset.vk).join(",")
    + "!" + dims.map(s => s.dataset.vk).join(",") + "!"
    + plan.map(p => [p.key, p.open ? 1 : 0, p.running ? 1 : 0, p.bad ? 1 : 0,
                     p.anchor, p.members.map(m => m.dataset.vk).join(".")].join(":"))
        .join(";");
  if (sig === ses.viewSig) return;
  ses.viewSig = sig;

  for (const old of [...ses.stream.children])
    if (old.classList.contains("vsum")) old.remove();
  clearViewMarks(items);
  for (const s of strays) hideIt(s);
  for (const s of dims) s.classList.add("vdim");
  for (const p of plan) {
    if (p.open) {
      // An EXPANDED run keeps its summary as the group's HEADER and marks the
      // blocks it revealed, so it is visible at a glance which actions belong to
      // it: `.vrun` draws the shared left rail (and closes the vertical gaps, so
      // the rail is continuous), `.vrun-last` rounds it off at the oldest member.
      // Marking beats re-parenting into a wrapper: SSE inserts by position, the
      // block map holds live references and the eviction sweep walks top-level
      // children — moving items would break all three.
      for (const m of p.span) m.classList.add("vrun");
      p.span[p.span.length - 1].classList.add("vrun-last");
    } else {
      for (const m of p.span) hideIt(m);
    }
    ses.stream.insertBefore(
      buildRunSummary(p.key, p.members, p.running, p.anchor, p.bad, p.open),
      p.span[0]);
    if (p.running && !ses.viewTimer)
      ses.viewTimer = setInterval(tickRunTimers, FG_TICK_MS);
  }
  updateFilterCount();
  viewAutoFill();
}

// Collapsing can leave the window almost empty (80 blocks of commands become two
// summary lines). Pull the next history page or two so there is something to
// read, bounded by VIEW_FILL_TRIES per mode switch.
function viewAutoFill() {
  const ses = S.ses;
  if (!ses || ses.view === "verbose" || ses.loadingOlder) return;
  if ((ses.viewFill | 0) >= VIEW_FILL_TRIES || (ses.oldest | 0) <= 0) return;
  if (visibleCount() >= VIEW_FILL_MIN) return;
  ses.viewFill = (ses.viewFill | 0) + 1;
  loadOlder(VIEW_FILL_MIN);      // the same loader, aimed at a smaller target —
  //                                two independent pagers would fight over
  //                                `loadingOlder` and double-fetch the boundary
}

function setViewMode(mode) {
  const ses = S.ses;
  if (!ses || !VIEW_MODES.includes(mode)) return;
  ses.view = mode;
  ses.viewOpen.clear();          // expansions belong to the mode that made them
  ses.viewFill = 0;
  if (ses.meta) ses.meta.view_mode = mode;
  applyViewMode();
  // Durable + per-session (dashboard/prefs.py): re-opening this session — on
  // this device or another — comes back at the mode you left it in.
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/viewmode",
           { mode }, { audit: "viewmode" });
}

function buildFilterBar() {
  const ses = S.ses;
  const f = ses.filter;
  const bar = el("div", "fbar");

  // the view-mode control, left of the kind chips: both act on this stream, and
  // this one is the coarser cut, so it reads first
  const modes = el("div", "vmodes");
  const mbtns = new Map();
  for (const key of VIEW_MODES) {
    const c = el("button", "vmode" + (ses.view === key ? " on" : ""), key);
    c.onclick = () => {
      setViewMode(key);
      mbtns.forEach((cc, k) => cc.classList.toggle("on", k === ses.view));
    };
    mbtns.set(key, c);
    modes.append(c);
  }

  const chipwrap = el("div", "fchips");
  const chips = new Map();
  for (const key of FILTER_KINDS) {
    const c = el("button", "fchip" + (f.kind === key ? " on" : ""), key);
    c.onclick = () => {
      f.kind = key;
      chips.forEach((cc, k) => cc.classList.toggle("on", k === key));
      applyFilter();
    };
    chips.set(key, c);
    chipwrap.append(c);
  }

  const count = el("span", "fcount");
  ses.countEl = count;
  bar.append(modes, chipwrap, count);
  ses.filterBar = bar;
  return bar;
}

/* ---------- the "/" command menu (composer + new-session prompt) ---------- */
// Claude-Code-style completion: a leading "/" with no whitespace yet opens a
// menu over GET /api/commands?cwd=… (built-ins + that directory's .claude
// commands/skills), matched by SUBSTRING (`cmdMatches`, prefix hits ranked
// first) — the memorable middle of a name is enough, no prefix needed.
// ↑/↓ move, Tab completes, Esc closes; Enter completes —
// except with {enterSends: true} an EXACT token falls through to the caller's
// send (so a fully-typed "/compact" sends on one Enter; both boxes pass
// !IS_IPAD, since on an iPad Enter never sends). The TUI stays
// authoritative — sending just types the command into the terminal and Claude
// Code's own palette executes it. The menu drops BELOW its host box (never up
// over the stats row); `host` must be position:relative.
// Wiring contract: the helper listens to input/blur itself; the caller keeps
// its own oninput (autoGrow) and calls sm.key(e) FIRST in onkeydown — a true
// return means the menu consumed the key.

function cmdsFor(cwd, cache, key) {
  if (!cache[key])
    cache[key] = fetch("/api/commands?cwd=" + encodeURIComponent(cwd || ""))
      .then(r => r.ok ? r.json() : [])
      .catch(() => []);
  return cache[key];
}

// how many rows the menu shows at most (it scrolls past ~9)
const MENU_MAX = 30;

// The match rule: CONTAINS, case-insensitively, over the command NAME — typing
// the memorable middle of one is enough ("/commit" finds `gh:commit`, "/debug"
// finds `audit-debug`), which is what the namespaced/plugin names need since
// their prefix is the namespace you don't remember. Prefix hits still rank
// FIRST (typing the head of a name means that name), and each group keeps the
// server's own order — built-ins first, then nearest-first (`slashcmds.py`) —
// so no scoring heuristic re-litigates the shadowing rules the server settled.
// Descriptions are deliberately NOT searched: a word like "run" appears in
// dozens of them, and a menu you complete against must stay predictable.
function cmdMatches(cmds, tok) {
  const q = tok.toLowerCase();
  const pre = [], mid = [];
  for (const c of cmds) {
    const at = c.name.toLowerCase().indexOf(q);
    if (at === 0) pre.push(c);            // (an empty token: every row, in order)
    else if (at > 0) mid.push(c);
  }
  return pre.concat(mid);
}

// The picked command reads TINTED inside the box (Claude Code's TUI paints the
// selected command/skill the same way). A <textarea> cannot style a range, so a
// mirror div is positioned OVER the box (pointer-events:none, its own text
// transparent) and only the leading "/name" token carries a translucent --exec
// background — a tint the textarea's own glyphs show through, like a selection.
// The mirror's metrics are COPIED from the live box (getComputedStyle), never
// re-declared in CSS: the textarea stays the single owner of its font/padding
// (the iPad ≥16px override among them), so the two can't drift out of alignment.
const HL_METRICS = ["fontFamily", "fontSize", "fontWeight", "fontStyle",
                    "lineHeight", "letterSpacing", "wordSpacing", "tabSize",
                    "textIndent", "paddingTop", "paddingRight", "paddingBottom",
                    "paddingLeft", "borderTopWidth", "borderRightWidth",
                    "borderBottomWidth", "borderLeftWidth", "borderRadius"];

function cmdHighlight(ta, host, isCmd) {
  const hl = el("div", "cmhl");
  hl.hidden = true;
  host.append(hl);
  let queued = false;
  // the mirror tracks the box's live geometry: it grows with autoGrow, moves as
  // the attachment strip wraps, and reflows on a resize/rotation
  const place = () => {
    const r = ta.getBoundingClientRect(), h = host.getBoundingClientRect();
    hl.style.left = (r.left - h.left) + "px";
    hl.style.top = (r.top - h.top) + "px";
    hl.style.width = r.width + "px";
    hl.style.height = r.height + "px";
    const cs = getComputedStyle(ta);
    for (const k of HL_METRICS) hl.style[k] = cs[k];
  };
  // Painted a frame LATE on purpose: the caller's own oninput (autoGrow) resizes
  // the box on the same event, and the mirror must match the SETTLED geometry.
  const paint = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      if (!ta.isConnected) return;
      const v = ta.value;
      // the leading token only, and only while it names a real command — editing
      // it into something else (or sending) drops the tint by itself
      const m = /^\/(\S+)(?=\s|$)/.exec(v);
      const name = m && isCmd(m[1]) ? m[1] : null;
      if (!name || ta.disabled) { hl.hidden = true; hl.textContent = ""; return; }
      place();
      hl.textContent = "";
      hl.append(el("span", "cmhlt", "/" + name),
                document.createTextNode(v.slice(name.length + 1)));
      hl.scrollTop = ta.scrollTop;      // the rest of the text is only there to
      hl.hidden = false;                // wrap/scroll the token like the box does
    });
  };
  ta.addEventListener("input", paint);
  ta.addEventListener("scroll", paint);
  // autoGrow is the one hook every PROGRAMMATIC value change already goes
  // through (draft restore, an SSE draft from another device, a cleared send),
  // none of which fire `input` — see the autoGrow call in app.08-composer.js
  ta.cmdPaint = paint;
  const onResize = () => {
    if (!ta.isConnected) { removeEventListener("resize", onResize); return; }
    paint();
  };
  addEventListener("resize", onResize);
  // the box can MOVE with no value change and no window resize — the attachment
  // strip appearing above it wraps the composer's flex row (paint places the
  // mirror, but nothing else would call it). Safe from feedback: the mirror is
  // absolutely positioned, so painting it never resizes what we observe.
  if (window.ResizeObserver) {
    const ro = new ResizeObserver(() => {
      if (!ta.isConnected) { ro.disconnect(); return; }
      paint();
    });
    ro.observe(ta);
    ro.observe(host);
  }
  return paint;
}

function slashMenu(ta, host, getCmds, opts) {
  const enterSends = !!(opts && opts.enterSends);
  const menu = el("div", "cmenu");
  menu.hidden = true;
  host.append(menu);
  let items = [], sel = 0;
  // every command name this box has seen, so the tint can recognise one without
  // a fetch of its own (a pick adds its name; a menu refresh adds the batch)
  const known = new Set();
  const hlPaint = cmdHighlight(ta, host, (name) => known.has(name));

  // the "/" token being completed, or null when the menu shouldn't show
  // (no leading slash, or whitespace = arguments underway)
  const token = () => {
    const v = ta.value;
    if (!v.startsWith("/")) return null;
    const head = v.slice(1);
    return /\s/.test(head) ? null : head;
  };
  const close = () => { menu.hidden = true; items = []; };
  const complete = (c) => {
    known.add(c.name);                      // …so it paints tinted right away
    ta.value = "/" + c.name + " ";
    close();
    ta.focus();
    ta.dispatchEvent(new Event("input"));   // caller's autoGrow, if any
  };
  const render = () => {
    menu.textContent = "";
    items.forEach((c, i) => {
      const row = el("div", "cmi" + (i === sel ? " sel" : ""));
      row.append(el("span", "cmname", "/" + c.name));
      if (c.desc) row.append(el("span", "cmdesc", c.desc));
      if (c.src && c.src !== "built-in") row.append(el("span", "cmsrc", c.src));
      row.onmousedown = (e) => { e.preventDefault(); complete(c); };
      menu.append(row);
    });
    menu.hidden = !items.length;
    if (items.length && menu.children[sel])
      menu.children[sel].scrollIntoView({ block: "nearest" });
  };
  const learn = (cmds) => {
    cmds.forEach(c => known.add(c.name));
    hlPaint();                              // a name we just learned may be typed
    return cmds;
  };
  const refresh = () => {
    const tok = token();
    if (tok === null) { close(); return; }
    getCmds().then(learn).then(cmds => {
      if (!ta.isConnected || token() !== tok) return;   // view/input moved on
      sel = 0;
      items = cmdMatches(cmds, tok).slice(0, MENU_MAX);
      render();
    });
  };
  const key = (e) => {
    if (menu.hidden || !items.length) return false;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      sel = (sel + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
      render();
      return true;
    }
    if (e.key === "Tab") { e.preventDefault(); complete(items[sel]); return true; }
    if (e.key === "Escape") { e.stopPropagation(); close(); return true; }
    if (e.key === "Enter" && !e.shiftKey) {
      if (enterSends && token() === items[sel].name) {
        close();                            // exact token: fall through to send
        return false;
      }
      e.preventDefault();
      complete(items[sel]);
      return true;
    }
    return false;
  };
  ta.addEventListener("input", refresh);
  ta.addEventListener("blur", () => setTimeout(close, MENU_BLUR_MS));
  // a RESTORED draft can already hold a picked command with nothing typed since
  // (so no menu fetch would ever happen) — learn the names once, for the tint
  if (ta.value.startsWith("/")) getCmds().then(learn);
  return { key };
}

/* ---------- queued messages (Claude Code's mid-turn queue) ---------- */
// Claude Code natively QUEUES a message typed while a turn is running and
// delivers it when the turn ends — the composer rides exactly that (send_text
// types into the TUI either way). The /message response says which happened
// (`queued: true` when the send landed mid-turn — the server's verdict is the
// authority: a QUEUE_TABS tab colour VERIFIED against a live screen, since a
// terminal-side cancel can freeze the colour mid-turn and a colour-only verdict
// pinned chips no delivery would ever drain; the client QUEUE_TABS below only
// styles the send button). A queued message would otherwise VANISH from the page until
// delivery (it reaches the transcript only when the turn ends), so it shows as
// an amber "⧗ queued" prompt bubble PINNED at the top of the transcript — above
// the newest-first stream, so incoming activity never buries it — until its
// prompt record actually arrives in the stream (drainQueue — matched by text;
// tab transitions are useless as a delivery signal since green flips busy again
// the instant a queued prompt starts processing). At that point drainQueue drops
// the pinned bubble and the delivered prompt appears in the stream itself. ✕
// only removes the marker — the message is already in the TUI's queue and the
// web can't unqueue it.

const QUEUE_TABS = ["thinking", "working", "executing"];

function buildQueuePin() {
  const q = el("div", "pinq");
  q.hidden = true;
  S.ses.queueEl = q;
  // restore the pinned queued messages persisted server-side (composer-queue kv)
  // so a reload / device switch keeps showing what the TUI still holds unqueued —
  // seed only when the in-memory queue is empty (a live session already has its
  // entries); drainQueue reconciles them out as their prompts arrive.
  const cq = S.ses.meta && S.ses.meta.composer_queue;
  if (cq && Array.isArray(cq.items) && !S.ses.queue.length)
    S.ses.queue = cq.items.map(it => ({ text: (it && it.text) || "" }));
  renderQueue();
  return q;
}

// Persist the WHOLE current chip list to the server (composer-queue kv) so it
// survives a reload; called on every queue mutation (queued-send, delivery
// drain, ✕-hide). Best-effort — a failed write just retries on the next
// change. meta is kept in sync so our own SSE echo is a no-op.
function saveQueue(ses) {
  ses = ses || S.ses;
  if (!ses || !S.cur) return;
  const items = ses.queue.map(m => ({ text: m.text }));
  if (ses.meta)
    ses.meta.composer_queue = items.length ? { items, origin: CLIENT_ID } : null;
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/composer-queue",
           { items, origin: CLIENT_ID }).catch(() => {});
}

// A peer device's (or our own reload's) queue update arrived over SSE — adopt
// it, ignoring our OWN echo (same origin) so a local drain isn't clobbered.
function applyComposerQueue(q) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.composer_queue = q || null;
  if (q && q.origin && q.origin === CLIENT_ID) return;   // our own write
  ses.queue = ((q && q.items) || []).map(it => ({ text: (it && it.text) || "" }));
  renderQueue();
}

// Paint the queued messages as amber "⧗ queued" prompt bubbles, pinned at the
// top of the transcript until each is delivered (drainQueue removes it, and the
// real prompt bubble then arrives in the stream). Mirrors opshtml.msg_html's
// .msg.prompt shape (minus the rewind ↶ — a not-yet-delivered prompt isn't
// re-runnable), plus a ⧗ badge and a ✕ to drop a stale marker.
function renderQueue() {
  const ses = S.ses;
  if (!ses || !ses.queueEl) return;
  const q = ses.queueEl;
  q.textContent = "";
  q.hidden = !ses.queue.length;
  ses.queue.forEach((m, i) => {
    const d = el("div", "msg prompt queued");
    d.title = "queued in the terminal — delivers when this turn ends";
    const who = el("span", "who");
    who.append(tnode("you"), el("span", "qbadge", "⧗ queued"));
    d.append(who);
    const x = el("button", "qx", "✕");
    x.title = "remove this queued marker (the message stays queued in the terminal)";
    x.onclick = () => { ses.queue.splice(i, 1); renderQueue(); saveQueue(ses); };
    d.append(x);
    d.append(promptMd(m.text));
    q.append(d);
  });
}

function drainQueue(items) {
  const ses = S.ses;
  if (!ses || !ses.queue || !ses.queue.length) return;
  let hit = false;
  for (const it of items) {
    if (it.t !== "msg" || it.kind !== "prompt") continue;
    const real = (it.text || "").trim();
    // suffix match (promptMatches — the one rule, shared with drainPending and
    // mirrored server-side): the delivered prompt may carry attachment mentions
    // OR a terminal-restored draft in front of what we sent.
    const i = ses.queue.findIndex(m => promptMatches(real, m.text));
    if (i >= 0) { ses.queue.splice(i, 1); hit = true; }
  }
  if (hit) { renderQueue(); saveQueue(ses); }
}

// A prompt the terminal DISCARDED (Esc-Esc right after sending, or a rewind)
// is never deleted from the transcript — Claude Code just re-parents around it,
// so the next prompt arrives carrying the SAME data-par and the dead one is
// simply orphaned. The server prunes that on any full read, but a live feed has
// already painted the bubble, so drop it here the moment its replacement shows
// up (docs/dashboard.md, *Discarded prompts*). Newest-top feed ⇒ the survivor
// is the first match in DOM order; only server-rendered bubbles carry data-par,
// so the optimistic .pending / ⧗ .queued stand-ins are untouched.
function dropSuperseded(items) {
  const st = S.ses && S.ses.stream;
  if (!st) return;
  for (const it of items) {
    if (it.t !== "msg" || it.kind !== "prompt" || !it.par) continue;
    let live = false;
    for (const el of st.querySelectorAll(".msg.prompt[data-par]")) {
      if (el.dataset.par !== it.par) continue;
      if (!live) { live = true; continue; }        // keep the newest
      el.remove();
    }
  }
}

/* ---------- optimistic prompt bubbles (the composer's own send) ---------- */
// A sent message reaches the transcript only once Claude Code writes its user
// prompt record and the server pushes the `msgs` SSE event — a visible gap
// after the paste lands. To close it, send() prepends a GREYED stand-in bubble
// (.msg.prompt.pending) the instant it POSTs; drainPending removes it when the
// matching REAL prompt arrives (the server-rendered bubble takes its place), or
// send() removes it directly on failure / when the send was queued (the pinned
// ⧗ queued bubble owns that case). DOM-only + in-memory (ses.pending) — a reload
// replays from the real transcript, so nothing is persisted and stand-ins can't leak.
//
// The stand-in's whole lifecycle is client-only, so the SERVER can't see it —
// a stuck grey bubble (shown, never reconciled) leaves no trace by default. So
// each transition beacons a `web-hint` audit row (hintAudit → POST /hint-audit,
// server-side A.state_file): `shown` on create, `reconciled` on the swap
// (carrying wait_ms — the swap latency), `dropped` on queued/send-failed, and
// `stale` from a watchdog when a stand-in outlives STALE_HINT_MS unreconciled
// (THE bug signal). Audit-only, best-effort, never blocks or toasts.

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

// A greyed "…" stand-in shown in place of the interactive ask/plan card while
// an optimistic decision is in flight — the card analog of the composer's
// greyed prompt bubble. Cleared when the SSE reconcile drops the stash (or on
// failure, which re-renders the live card). `cls` = askcard | plancard.
function pendingCard(cls, title, note) {
  const card = el("div", cls + " pending");
  const head = el("div", "askhead");
  head.append(el("span", cls === "plancard" ? "plantitle" : "asktitle", title));
  card.append(head);
  card.append(el("div", "plandim", note));
  return card;
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

// Append one frontend-audit event and schedule a batched flush. `ev` is a dotted
// name (close.begin | close.ok | close.fail | close.reconciled …); `data` is a
// small flat bag of scalars. Ring-capped so a delivery outage can't grow it.
// SELF-GUARDING: the audit must never throw into the page — an exception here
// would fire window.onerror → clog → … a feedback loop, and this very channel is
// what CATCHES uncaught errors, so it must be the one thing that can't raise one.
