// tests/jsdom/memtree.js — drives the REAL memory-tab TREE renderer
// (renderMemoryTree/memDir/memNote/toggleMemDir in dashboard/static/
// app.11-chrome.js) over the shared DOM shim, and prints one JSON verdict
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
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox,
                { filename: process.argv[2] });
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

process.stdout.write(JSON.stringify(out, null, 1));
