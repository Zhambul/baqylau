"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.12-init.js for the boot/init sequence.

function showSession(sid, tab) {
  // unknown / retired tab (e.g. an old #/…/activity bookmark) → the mirror
  if (!["mirror", "agents", "monitors", "jobs", "memory", "errors"].includes(tab)) tab = "mirror";
  if (S.cur !== sid) {
    leaveSession();
    S.cur = sid;
    S.ses = { lastId: 0, mpos: 0, oldest: 0, stream: el("div", "stream"), stats: {},
              agents: [], costs: null, ctx: null, running: {}, meta: null, es: null, agentEs: null,
              timer: null, poll: null, blocks: new Map(), moreEl: null,
              monitors: null, monitorFocus: null, monPoll: null,
              jobs: null, jobFocus: null, jobPoll: null,
              memory: null, noteTrail: null, noteFocus: null,
              loadingOlder: false, queue: [], pending: [],
              askPend: null, planPend: null,   // in-flight optimistic ask/plan decisions
              filter: { kind: "all" } };          // cleared per session (new S.ses)
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
        renderSessionChrome(tab);
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
        if (S.cur === sid && S.ses && !S.ses.meta) setTimeout(loadMeta, 1500);
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
    if (S.ses && S.ses.cancelMode) S.ses.cancelMode(d.tab || "");
    if (S.ses && S.ses.stopMode) S.ses.stopMode(d.tab || "");
    if (S.ses && S.ses.quickMode) S.ses.quickMode(d.tab || "");
    // patch the open session's row so the session strip reacts before the
    // next global snapshot lands (item 4: react to the per-session tab event)
    const row = S.sessions.find(r => r.sid === S.cur);
    if (row) row.tab = d.tab || "";
    renderAttention();
  });
  es.onopen = () => { $conn.dataset.on = "1"; sseMark("session", true, { sid }); };
  es.onerror = () => {
    sseMark("session", false, { sid });
    es.close();
    if (S.cur !== sid) return;
    S.ses.timer = setTimeout(() => connectSession(sid), 1500);
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
      if (elem) { elem.dataset.kind = ungroupedKind(it, elem); applyFilterTo(elem); }
      continue;
    }
    let b = S.ses.blocks.get(it.g);
    if (!b) {
      b = createBlock();
      st.prepend(b.root);
      S.ses.blocks.set(it.g, b);
    }
    fillBlock(b, it);
    refineBlockKind(b, it);
    applyFilterTo(b.root);
  }
  drainQueue(items);
  drainPending(items);
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
      if (elem) { elem.dataset.kind = ungroupedKind(it, elem); applyFilterTo(elem); frag.append(elem); }
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
      local.set(it.g, b);
      frag.append(b.root);
    }
    fillBlock(b, it);
    refineBlockKind(b, it);
    applyFilterTo(b.root);
  }
  if (S.ses.moreEl) st.insertBefore(frag, S.ses.moreEl);
  else st.append(frag);
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
    b.textContent = "load older · " + HISTORY_FETCH + " more blocks…";
}

function loadOlder() {
  const ses = S.ses;
  if (!ses || ses.loadingOlder || (ses.oldest | 0) <= 0) return;
  ses.loadingOlder = true;
  const sid = S.cur, before = ses.oldest;
  if (ses.moreEl) ses.moreEl.textContent = "loading…";
  fetch("/api/session/" + encodeURIComponent(sid) + "/history?before=" + before
        + "&blocks=" + HISTORY_FETCH)
    .then(r => r.json())
    .then(d => {
      ses.loadingOlder = false;
      if (S.cur !== sid) return;                 // navigated away mid-fetch
      appendOlder(d.items || []);
      ses.oldest = d.oldest | 0;
      updateMoreBtn();
    })
    .catch(() => { ses.loadingOlder = false; updateMoreBtn(); });
}

/* ---------- stream search + kind filters ---------- */
// Every top-level stream child carries a data-kind (commands · files · agents ·
// messages) so the filter bar can hide non-matching items via a CSS class
// (never removing them — SSE keeps appending, and folded bodies stay
// textContent-searchable). data-kind is stamped once at creation: glyph/who
// sniffing (below) is stable against the exact chip text, which drifts, so we
// prefer a stamped attribute over re-sniffing the DOM on every filter pass.

// Main-session command/monitor/bg/fg blocks OPEN with one of these glyphs;
// subagent/teammate/codex blocks open with a who-prefix (agent label or
// "codex") before their glyph — that's the command-vs-agent tell.
const CMD_GLYPH = /^\s*[▶▷◉■]/;

function refineBlockKind(b, it) {
  if (b.root.dataset.kind === "agents") return;        // agent wins, monotonic
  if (/class="og"/.test(it.html)) {                    // outer gutter == nested subagent job
    b.root.dataset.kind = "agents";
    return;
  }
  if (it.t === "label" && !b.kindLocked) {
    const txt = (b.chips.textContent || "").trim();    // the block-opening chip
    if (txt) {
      b.root.dataset.kind = CMD_GLYPH.test(txt) ? "commands" : "agents";
      b.kindLocked = true;
    }
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

function streamItems() {
  return [...S.ses.stream.children].filter(el => el.dataset && el.dataset.kind);
}

function matchesFilter(elem) {
  const f = (S.ses && S.ses.filter) || { kind: "all" };
  if (f.kind !== "all" && elem.dataset.kind !== f.kind) return false;
  return true;
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
  const shown = items.filter(elem => !elem.classList.contains("fhide")).length;
  ses.countEl.textContent = shown + " of " + items.length + " shown";
}

const FILTER_KINDS = ["all", "commands", "files", "memory", "agents", "messages"];

function buildFilterBar() {
  const ses = S.ses;
  const f = ses.filter;
  const bar = el("div", "fbar");

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
  bar.append(chipwrap, count);
  ses.filterBar = bar;
  return bar;
}

/* ---------- the "/" command menu (composer + new-session prompt) ---------- */
// Claude-Code-style completion: a leading "/" with no whitespace yet opens a
// menu over GET /api/commands?cwd=… (built-ins + that directory's .claude
// commands/skills). ↑/↓ move, Tab completes, Esc closes; Enter completes —
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

function slashMenu(ta, host, getCmds, opts) {
  const enterSends = !!(opts && opts.enterSends);
  const menu = el("div", "cmenu");
  menu.hidden = true;
  host.append(menu);
  let items = [], sel = 0;

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
  const refresh = () => {
    const tok = token();
    if (tok === null) { close(); return; }
    getCmds().then(cmds => {
      if (!ta.isConnected || token() !== tok) return;   // view/input moved on
      sel = 0;
      const q = tok.toLowerCase();
      items = cmds.filter(c => c.name.toLowerCase().startsWith(q)).slice(0, 30);
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
  ta.addEventListener("blur", () => setTimeout(close, 150));   // menu clicks
  //                     preventDefault (never blur); this catches clicks away
  return { key };
}

/* ---------- queued messages (Claude Code's mid-turn queue) ---------- */
// Claude Code natively QUEUES a message typed while a turn is running and
// delivers it when the turn ends — the composer rides exactly that (send_text
// types into the TUI either way). The /message response says which happened
// (`queued: true` when the send landed mid-turn — the server's QUEUE_TABS
// verdict is the authority; the client QUEUE_TABS below only styles the send
// button). A queued message would otherwise VANISH from the page until
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
    // exact match, or (attachments prepend leading @path mentions +\n) the real
    // text ends with the queued suffix — same tolerant match as drainPending,
    // since a queued message with attachments arrives as `@path\n<text>`.
    const i = ses.queue.findIndex(m => real === m.text || real.endsWith("\n" + m.text));
    if (i >= 0) { ses.queue.splice(i, 1); hit = true; }
  }
  if (hit) { renderQueue(); saveQueue(ses); }
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
