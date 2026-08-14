"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.

/* The Memory tab's dashboard-owned presentation. Harness-native capture and
   storage stay behind the typed HarnessMemory port. */

/* Is the memory tab showing an open note rather than its grid? */
function noteOpen() {
  const sessionView = S.sessionView;
  return !!(sessionView && sessionView.noteTrail && sessionView.noteTrail.length);
}

/* ---------- memory tab (the memory-wiki notes a session touched) ---------- */

/* Paint the memory tab body: the search cards + the note tree, or the note viewer
   when a note (or a followed [[wikilink]]) is open. */
function paintMemory() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.body || sessionView.tab !== "memory") return;
  resetBody();
  if (sessionView.memory === null) {
    sessionView.body.append(el("div", "empty", "loading memory…"));
    return;
  }
  if (sessionView.noteTrail && sessionView.noteTrail.length) { renderNoteView(); return; }
  const searches = el("div", "memsearches");
  sessionView.memSearchWrap = searches;
  sessionView.body.append(searches);
  renderMemorySearches();
  const wrap = el("div", "memtree");
  sessionView.memWrap = wrap;
  sessionView.body.append(wrap);
  renderMemoryTree();
}

/* ---------- the search cards (docs/dashboard.md *Memory searches*) ----------
   A vault SEARCH is the other half of recall, and it opens no note — so it can't
   appear in the tree below however the tree is built. Each card is one question
   the session asked memory (`qmd query|search|vsearch`), collapsed to its query
   line, expanding to the answer it got back: the ranked note hits with their
   titles, scores and matched passages, plus (for a `query`) the lex/vec/hyde
   lines the LLM expanded it into. Newest first, like the mirror's own feed.

   Collapsed BY DEFAULT, deliberately: a search's answer is five multi-line
   passages, and three of those open at once bury the note tree that is the tab's
   other half. The query alone is the useful index — you open the one you want. */
function renderMemorySearches() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.memSearchWrap) return;
  const host = sessionView.memSearchWrap;
  host.textContent = "";
  const rows = sessionView.memSearch || [];
  if (!rows.length) return;                 // no header over nothing
  const head = el("div", "memsechead");
  head.append(el("span", "n", rows.length + (rows.length === 1 ? " search" : " searches")));
  host.append(head);
  rows.forEach((s, i) => host.append(memSearchCard(s, i)));
}

function memSearchCard(s, idx) {
  const open = memSearchOpen().has(searchKey(s, idx));
  const card = el("div", "memsearch" + (open ? " open" : ""));
  const head = el("div", "mshead");
  head.append(el("span", "mstw", open ? "▾" : "▸"));
  head.append(el("span", "mskind", (s.command_name || "qmd") + " " + (s.command_action || "query")));
  head.append(el("span", "msq", s.query || ""));
  const meta = el("span", "msmeta");
  const hits = (s.hits || []).length;
  if (hits) meta.append(el("span", "mshits", hits + " hits"));
  if (s.actor_name) meta.append(el("span", "memagent", "⇢ " + s.actor_name));
  if (s.search_count > 1) meta.append(el("span", "memcount", "×" + s.search_count));
  head.append(meta);
  head.title = (open ? "collapse" : "expand") + " — " + (s.query || "");
  head.onclick = () => toggleMemSearch(searchKey(s, idx));
  card.append(head);
  if (open) card.append(memSearchBody(s));
  return card;
}

function memSearchBody(s) {
  const body = el("div", "msbody");
  if (s.expanded_queries && s.expanded_queries.length) {
    const exp = el("div", "msexp");
    exp.append(el("div", "lbl", "expanded to"));
    for (const line of s.expanded_queries) exp.append(el("div", "msexpl", line));
    body.append(exp);
  }
  const hits = s.hits || [];
  if (!hits.length) {
    // A search whose answer we don't have: qmd's output was truncated past its
    // results, or the command ran several searches at once (memcmd.record won't
    // file one query's results under another). The question is still the record.
    body.append(el("div", "empty", "no results captured for this search"));
    return body;
  }
  for (const h of hits) body.append(memSearchHit(h));
  return body;
}

function memSearchHit(h) {
  const row = el("div", "mshit" + (h.viewable ? " live" : ""));
  const top = el("div", "mshtop");
  if (h.score) top.append(el("span", "msscore", h.score));
  top.append(el("span", "mshname", h.name || h.relative_path || "?"));
  if (h.line_number) top.append(el("span", "mshline", ":" + h.line_number));
  row.append(top);
  if (h.title) row.append(el("div", "mshtitle", h.title));
  if (h.relative_path) row.append(el("div", "mshrel", h.relative_path));
  if (h.snippet) row.append(el("pre", "mshsnip", h.snippet));
  if (h.viewable) {
    // Same gesture as a tree row: the hit opens the note in the viewer, which is
    // the whole point of showing which notes answered — the passage is a taste.
    top.title = "open " + (h.relative_path || h.name);
    row.onclick = () => openNoteRef({ path: h.path }, true);
  }
  return row;
}

/* A stable identity for the open/closed set: the query itself (what the card IS),
   with the index as the tiebreak for the pathological same-query-twice case the
   kv's own (kind, sub, query) dedup should already have folded. */
function searchKey(s, idx) {
  return (s.command_name || "") + "|" + (s.command_action || "") + "|" + (s.query || "") + "|" + idx;
}

/* Which cards are expanded. Per session and kept across repaints, for exactly the
   reason the folder folds are (memShut): the tab reloads on every `memory` SSE
   tick, and a card that snapped shut under your hand each time the session
   touched a note would be worse than one that never opened. */
function memSearchOpen() {
  const sessionView = S.sessionView;
  if (!sessionView.memSearchShown) sessionView.memSearchShown = new Set();
  return sessionView.memSearchShown;
}

function toggleMemSearch(key) {
  const open = memSearchOpen();
  if (open.has(key)) open.delete(key);
  else open.add(key);
  renderMemorySearches();
}

/* The touched notes in the VAULT's own folder structure (server-built —
   read/mirror.memory_tree), so the tab answers "did we work on platform, or on
   providers (and which), or on tooling" at a glance instead of listing note
   names in touch order. Folders are open by default and collapse on click. */
function renderMemoryTree() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.memWrap) return;
  sessionView.memWrap.textContent = "";
  const root = sessionView.memTree;
  if (!root || !root.note_count) {
    // Say "notes" specifically: with search cards above, a bare "no memory" over a
    // tab that visibly HAS memory in it would read as a bug.
    if (!(sessionView.memSearch || []).length)
      sessionView.memWrap.append(el("div", "empty", "no memory notes touched in this session"));
    else
      sessionView.memWrap.append(el("div", "empty", "no notes opened — only searched"));
    return;
  }
  sessionView.memWrap.append(memSummary(root));
  memChildren(sessionView.memWrap, root, 0);
}

/* A node's rows: its sub-folders first, then the notes filed on it directly —
   the order the server froze (folders before notes, each alphabetical). */
function memChildren(host, node, depth) {
  for (const d of node.directories || []) memDir(host, d, depth);
  for (const n of node.notes || []) host.append(memNote(n, depth));
}

function memSummary(root) {
  const sum = el("div", "memsum");
  sum.append(el("span", "n", root.note_count + (root.note_count === 1 ? " note" : " notes")));
  if (root.write_count)
    sum.append(el("span", "w", "✎ " + root.write_count + " written"));
  return sum;
}

/* One folder row + (unless collapsed) everything under it. The rows are a FLAT
   list indented by depth rather than nested containers — a collapse is a
   repaint, and nesting only buys an animation we don't have. */
function memDir(host, node, depth) {
  const open = !memShut().has(node.path);
  const row = el("div", "memdir" + (depth ? "" : " top"));
  row.style.paddingLeft = memPad(depth);
  row.append(el("span", "mtw", open ? "▾" : "▸"));
  row.append(el("span", "mdname", node.name));
  const meta = el("span", "mdmeta");
  meta.append(el("span", "mdcount", String(node.note_count)));
  if (node.write_count) meta.append(el("span", "mdwrites", "✎" + node.write_count));
  row.append(meta);
  row.title = (open ? "collapse " : "expand ") + node.path;
  row.onclick = () => toggleMemDir(node.path);
  host.append(row);
  if (open) memChildren(host, node, depth + 1);
}

/* One note row — the same chips the flat cards carried (verb colour, the
   subagent that touched it, a ×N repeat count), under its folder. */
function memNote(n, depth) {
  const row = el("div", "memnote");
  row.style.paddingLeft = memPad(depth);
  const verb = (n.action || "Read").toLowerCase();
  row.append(el("span", "vchip v-" + verb, verb));
  row.append(el("span", "memname", n.label || n.name || "?"));
  if (n.actor_name) row.append(el("span", "memagent", "⇢ " + n.actor_name));
  if (n.access_count > 1) row.append(el("span", "memcount", "×" + n.access_count));
  row.onclick = () => openNoteRef({ path: n.path }, true);
  return row;
}

function memPad(depth) { return (6 + depth * 16) + "px"; }

/* Which folders are collapsed, by vault-relative path. Per session and kept
   across repaints: the tab reloads on every `memory` SSE tick, and a fold that
   sprang back open under your hand each time a note was touched would be worse
   than no fold at all. */
function memShut() {
  const sessionView = S.sessionView;
  if (!sessionView.memShut) sessionView.memShut = new Set();
  return sessionView.memShut;
}

function toggleMemDir(path) {
  const shut = memShut();
  if (shut.has(path)) shut.delete(path);
  else shut.add(path);
  renderMemoryTree();
}

/* Open a note by absolute path (a grid row) or bare stem (a followed
   [[wikilink]]). `reset` starts a fresh breadcrumb trail (a grid click);
   following a link pushes onto it. */
function openNoteRef(ref, reset) {
  const sessionView = S.sessionView, sessionId = S.currentSessionId;
  if (!sessionView || !sessionId) return;
  const q = ref.path ? ("path=" + encodeURIComponent(ref.path))
                     : ("stem=" + encodeURIComponent(ref.stem || ""));
  fetch("/api/sessions/" + encodeURIComponent(sessionId) + "/memory/documents?" + q)
    .then(r => r.json())
    .then(d => {
      if (S.currentSessionId !== sessionId || !S.sessionView) return;
      if (reset || !S.sessionView.noteTrail) S.sessionView.noteTrail = [];
      S.sessionView.noteTrail.push(d);
      S.sessionView.noteFocus = d.path || d.name;
      paintMemory();
      // start the newly-opened note from its top — following a link deep in one
      // note shouldn't land you mid-way down the next (the page scrolls the
      // window; the sticky header stays pinned)
      window.scrollTo(0, 0);
    })
    .catch(() => {});
}

function renderNoteView() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.body) return;
  const trail = sessionView.noteTrail || [];
  const d = trail[trail.length - 1];
  resetBody();
  sessionView.body.append(noteCrumbs(trail));
  if (!d) return;
  const wrap = el("div", "note");
  if (d.missing) {
    wrap.append(el("div", "empty", "note not found: " + (d.name || "?")));
    sessionView.body.append(wrap);
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
  sessionView.body.append(wrap);
}

/* The note breadcrumb — ❖ memory (back to the grid) › note › followed note … */
function noteCrumbs(trail) {
  const nav = el("div", "crumbs");
  const back = el("a", "crumb");
  back.href = "#/s/" + encodeURIComponent(S.currentSessionId) + "/memory";
  back.title = "back to the memory list";
  back.append(el("span", "cg", "❖"), tnode(" memory"));
  back.onclick = (e) => {
    e.preventDefault();
    S.sessionView.noteTrail = []; S.sessionView.noteFocus = null; paintMemory();
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
      a.onclick = (e) => { e.preventDefault(); S.sessionView.noteTrail = trail.slice(0, i + 1); paintMemory(); };
      nav.append(a);
    }
  });
  return nav;
}

function initializeMemoryState(sessionView) {
  Object.assign(sessionView, {
    memory: null, memTree: null, memSearch: null, memShut: new Set(),
    memSearchShown: new Set(), noteTrail: null, noteFocus: null,
  });
}

function showMemoryBody(sessionView) {
  paintMemory();
  if (sessionView.memory === null) loadMemory(false);
}

SECTIONS.memory = {
    api: "memory", list: "memory", tabEl: "memoryTab",
    countField: "memory_count", label: "memory", scoped: false,
    // the FOLDER TREE and the SEARCH cards, both server-computed, beside the flat
    // note list in the same payload (docs/dashboard.md *Memory tab* / *Memory
    // searches*)
    stash: (sessionView, d) => {
      sessionView.memTree = d.tree || null;
      sessionView.memSearch = d.searches || [];
    },
    // the badge counts notes AND searches — the same sum the server's
    // memory_count serves, so the eager number and the fetched one agree
    count: (sessionView) => (sessionView.memory || []).length + (sessionView.memSearch || []).length,
    // the tree repaints only when it is the thing on screen — an open note
    // viewer stays put while the list refreshes underneath it
    repaint: () => { if (!noteOpen()) paintMemory(); },
    showing: () => !noteOpen(),
};

function loadMemory(force) {
  const sessionView = S.sessionView, sessionId = S.currentSessionId;
  if (!sessionView || !sessionId || (!force && sessionView.memory !== null)) return;
  fetch("/api/sessions/" + encodeURIComponent(sessionId) + "/memory")
    .then(response => response.json())
    .then(snapshot => {
      if (S.currentSessionId !== sessionId || !S.sessionView) return;
      S.sessionView.memory = snapshot.notes || [];
      S.sessionView.memTree = snapshot.tree || null;
      S.sessionView.memSearch = snapshot.searches || [];
      if (!noteOpen()) paintMemory();
    })
    .catch(() => {});
}
