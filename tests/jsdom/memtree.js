// tests/jsdom/memtree.js — drives the REAL memory-tab renderers (the TREE:
// renderMemoryTree/memDir/memNote/toggleMemDir; and the SEARCH CARDS:
// renderMemorySearches/memSearchCard/toggleMemSearch — dashboard/static/
// app.11-ext-memory.js) over the shared DOM shim, and prints one JSON verdict
// object that test_l0_dash_viewmode.py asserts on.
//
// Why this exists: the memory tab used to be a flat card list, and its render
// path had NO executing test at all — only greps and the loadSection/badge half
// in sections.js. The tree replaced that list with rows whose whole job is
// STRUCTURE: indent by depth, folders before notes, a collapse that survives
// the repaint the `memory` SSE fires on every touched note. A grep cannot catch
// "the twisty says ▾ while the subtree is gone", an indent that ignores depth,
// or a fold that springs back open — each of which renders a correct server
// tree unreadable.
//
// Usage: node tests/jsdom/memtree.js dashboard/static/app.11-chrome.js
// SKIPPED when `node` is absent (docs/testing.md) — never a build requirement.
"use strict";
const fs = require("fs");
const vm = require("vm");
const { El, domGlobals } = require("./domshim");

const opened = [];

/* The app globals the renderer's file calls that live in other SPA parts. */
const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number, RegExp, Promise,
  setInterval: () => 1, clearInterval: () => {},
  setTimeout: () => 1, clearTimeout: () => {},
  ...domGlobals(),
  encodeURIComponent,
  dur: sec => Math.max(0, sec | 0) + "s",
  ago: () => "just now",
  pre: s => new El("pre", "", s),
  firstLine: s => String(s || "").split("\n")[0],
  metaAdder: () => () => {}, chipAdder: () => () => {},
  agentQ: () => "",
  sigmaChip: () => new El("span", "chip"),
  paintCtxRow: () => {}, updateRunning: () => {},
  showSession: () => {}, renderErrorsInto: () => {},
  renderNoteView: () => {},
  // the one thing a note row must do: open THAT note, from a fresh trail
  openNoteRef: (ref, reset) => { opened.push({ path: ref.path, reset: !!reset }); },
  buildGoalCard: () => new El("div"), buildTasksCard: () => new El("div"),
  buildPlanCard: () => new El("div"), buildAskCard: () => new El("div"),
  buildComposer: () => new El("div"), buildViewBar: () => new El("div"),
  buildQueuePin: () => new El("div"), updateAgents: () => {},
  updateMoreBtn: () => {}, updateShownCount: () => {}, closeMonitorStream: () => {},
  IS_IPAD: false,
  $view: new El("div"),
  S: { cur: "sid1", ses: null },
  fetch: () => Promise.resolve({ json: () => Promise.resolve({}) }),
};
sandbox.document.addEventListener = () => {};
sandbox.window = sandbox;
sandbox.window.addEventListener = () => {};
vm.createContext(sandbox);
// every app part on argv, concatenated into ONE script (the chrome part
// defines extRegister/SECTIONS; app.11-ext-memory.js holds the renderers
// under test and registers into them)
vm.runInContext(process.argv.slice(2).map(p => fs.readFileSync(p, "utf8")).join("\n"),
                sandbox, { filename: process.argv[2] });
// the source declares its OWN openNoteRef (a top-level `function` becomes a
// property of the vm global), so the stub above is overwritten by the real one
// at evaluation — re-point it AFTER, or a note click fetches instead of being
// observed
sandbox.openNoteRef = (ref, reset) => { opened.push({ path: ref.path, reset: !!reset }); };

/* A server-shaped tree (dashboard/read/mirror.memory_tree): a compressed chain
   (platform/concepts), a real fork (providers → egt + hacksaw), a folded leaf
   dir riding a note LABEL (egt's concepts/), and a vault-root note. */
const TREE = {
  name: "", path: "", count: 6, writes: 3,
  dirs: [
    { name: "platform/concepts", path: "platform/concepts", count: 2, writes: 1,
      dirs: [],
      notes: [
        { path: "/w/platform/concepts/architecture.md", label: "architecture.md",
          verb: "Update", count: 2 },
        { path: "/w/platform/concepts/networking.md", label: "networking.md",
          verb: "Read", count: 1 },
      ] },
    { name: "providers", path: "providers", count: 3, writes: 2, notes: [],
      dirs: [
        { name: "egt", path: "providers/egt", count: 2, writes: 2, dirs: [],
          notes: [
            { path: "/w/providers/egt/concepts/acl.md", label: "concepts/acl.md",
              verb: "Write", count: 1, agent: "note-writer" },
            { path: "/w/providers/egt/egt.md", label: "egt.md",
              verb: "Update", count: 1 },
          ] },
        { name: "hacksaw", path: "providers/hacksaw", count: 1, writes: 0,
          dirs: [],
          notes: [{ path: "/w/providers/hacksaw/hacksaw.md", label: "hacksaw.md",
                    verb: "Read", count: 3 }] },
      ] },
  ],
  notes: [{ path: "/w/index.md", label: "index.md", verb: "Read", count: 1 }],
};

function freshSes(tree) {
  const wrap = new El("div", "memtree");
  const ses = { tab: "memory", body: new El("div"), memWrap: wrap,
                memTree: tree, memShut: new Set(), memory: [], noteTrail: null };
  sandbox.S.ses = ses;
  return ses;
}

/* Every painted row, in order: what it is, how deep it sits, what it says. */
function rows(ses) {
  return ses.memWrap.children.map(c => ({
    cls: c.className,
    pad: c.style.paddingLeft || "",
    text: c.textContent,
  }));
}

const ses = freshSes(TREE);
sandbox.renderMemoryTree();
const out = { open: rows(ses) };

// collapse `providers` — the subtree goes, the row stays (with its rollup)
sandbox.toggleMemDir("providers");
out.collapsed = rows(ses);
out.shut = [...ses.memShut];

// …and a repaint (what the `memory` SSE fires on every touched note) must NOT
// spring it back open
sandbox.renderMemoryTree();
out.afterRepaint = rows(ses);

// re-expanding restores exactly the first paint
sandbox.toggleMemDir("providers");
out.reopened = rows(ses);

// a note row opens THAT note, on a fresh breadcrumb trail
const note = ses.memWrap.children.find(c => c.className === "memnote");
note.onclick();
out.opened = opened.slice();

// nothing touched → the empty line, not an empty panel
const bare = freshSes({ name: "", path: "", count: 0, writes: 0, dirs: [], notes: [] });
sandbox.renderMemoryTree();
out.empty = rows(bare);

// …and the same when the payload carried no tree at all (an old server)
const none = freshSes(null);
sandbox.renderMemoryTree();
out.noTree = rows(none);


/* ---------- the SEARCH cards (docs/dashboard.md *Memory searches*) ----------
   A search opens no note, so it can never appear in the tree above — the cards
   are the only surface it has. What is not greppable: a card starts COLLAPSED
   (three open answers bury the tree), the expanded set survives the repaint the
   `memory` SSE fires on every touched note, and a hit whose note is gone must
   render as a row rather than a dead link. */
const SEARCHES = [
  { kind: "qmd", sub: "query", query: "how does rscheck answer getstatus",
    count: 1, agent: null,
    expanded: ["lex: how rscheck returns", "vec: getstatus in rscheck"],
    hits: [
      { rel: "platform/concepts/rscheck-healthcheck.md", name: "rscheck-healthcheck",
        line: 13, score: "86%", title: "rscheck — what answers /getstatus:81",
        snippet: "Documented in the internal docs repo.", path: "/w/a.md",
        viewable: true },
      { rel: "platform/concepts/gone.md", name: "gone", line: null, score: "41%",
        title: "", snippet: "", path: "", viewable: false },
    ] },
  { kind: "qmd", sub: "search", query: "manifest started healthcheck",
    count: 2, agent: "note-writer", expanded: [], hits: [] },
];

function freshSearchSes(searches) {
  const wrap = new El("div", "memsearches");
  const ses = { tab: "memory", body: new El("div"),
                memWrap: new El("div", "memtree"), memTree: null,
                memSearchWrap: wrap, memSearch: searches,
                memSearchShown: new Set(), memShut: new Set(),
                memory: [], noteTrail: null };
  sandbox.S.ses = ses;
  return ses;
}

/* Each card as (class, its head line, the body lines under it) — a collapsed
   card has no body at all, which is the thing being asserted. */
function cards(ses) {
  return ses.memSearchWrap.children.slice(1).map(c => ({
    cls: c.className,
    head: c.children[0].textContent,
    body: c.children.length > 1
      ? c.children[1].children.map(x => ({ cls: x.className, text: x.textContent }))
      : null,
  }));
}

const sses = freshSearchSes(SEARCHES);
sandbox.renderMemorySearches();
out.searchHead = sses.memSearchWrap.children[0].textContent;
out.searchCollapsed = cards(sses);

// open the first card — the question's ANSWER appears under it
sandbox.toggleMemSearch(sandbox.searchKey(SEARCHES[0], 0));
out.searchOpen = cards(sses);

// …and the `memory` SSE repaint must not snap it shut
sandbox.renderMemorySearches();
out.searchAfterRepaint = cards(sses);

// a hit whose note still exists opens it; the vanished one has no handler
const card = sses.memSearchWrap.children[1];
const hits = card.children[1].children.filter(x => x.className.startsWith("mshit"));
out.hitClasses = hits.map(h => h.className);
opened.length = 0;
hits[0].onclick();
out.hitOpened = opened.slice();
out.deadHitHasHandler = !!hits[1].onclick;

// a search with no captured answer says so rather than showing an empty card
sandbox.toggleMemSearch(sandbox.searchKey(SEARCHES[1], 1));
out.searchNoHits = cards(sses)[1].body;

// no searches at all → no header over nothing
const nosearch = freshSearchSes([]);
sandbox.renderMemorySearches();
out.noSearches = nosearch.memSearchWrap.children.length;

// the tree's empty line changes wording when there ARE searches — a bare "no
// memory" over a tab that visibly has memory in it reads as a bug
const onlySearched = freshSearchSes(SEARCHES);
sandbox.renderMemoryTree();
out.treeEmptyWithSearches = rows(onlySearched)[0].text;

process.stdout.write(JSON.stringify(out, null, 1));
