// tests/jsdom/sections.js — drives the REAL secondary-tab engine
// (the SECTIONS table + loadSection/renderSectionGrid/showSection/… in
// dashboard/static/app.11-chrome.js) over the shared DOM shim, and prints one
// JSON verdict object that test_l0_dashboard.py asserts on.
//
// Why this exists: monitors and jobs used to be fourteen near-identical
// function pairs 200 lines apart. Folding them onto one descriptor is only safe
// if BOTH still render what they rendered — and nothing in the Python suite
// executes this file, it can only grep it. A grep cannot catch "the jobs grid
// now says 'no monitors in this session'", or a breadcrumb pointing at the
// wrong list, or a poll that keeps ticking for a section with nothing live.
//
// Usage: node tests/jsdom/sections.js dashboard/static/app.11-chrome.js
// SKIPPED when `node` is absent (docs/testing.md) — never a build requirement.
"use strict";
const fs = require("fs");
const vm = require("vm");
const { El, domGlobals } = require("./domshim");

const timers = { set: 0, cleared: 0 };

/* The app globals the engine calls that live in other SPA parts. */
const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number, RegExp, Promise,
  setInterval: () => { timers.set += 1; return timers.set; },
  clearInterval: () => { timers.cleared += 1; },
  setTimeout: () => 1, clearTimeout: () => {},
  ...domGlobals(),
  encodeURIComponent,
  dur: sec => Math.max(0, sec | 0) + "s",
  ago: () => "just now",
  pre: s => new El("pre", "", s),
  firstLine: s => String(s || "").split("\n")[0],
  metaAdder: (grid) => (k, v) => { if (v != null) grid.append(new El("div", "m", k)); },
  chipAdder: () => () => {},
  // `agentQ` is the agent-scope query suffix (app.00-core.js) — a cross-part
  // global like clog/pre, stubbed with its real 3-line body so the URLs this
  // harness records are the ones the browser would send
  agentQ: (sep) => { const a = (sandbox.S && sandbox.S.ses && sandbox.S.ses.agent) || "";
                     return a ? (sep || "?") + "agent=" + encodeURIComponent(a) : ""; },
  sigmaChip: () => new El("span", "chip"),
  paintCtxRow: () => {},
  updateRunning: () => {},
  showSession: () => {}, renderErrorsInto: () => {},
  renderNoteView: () => {}, openNoteRef: () => {},
  buildGoalCard: () => new El("div"), buildTasksCard: () => new El("div"),
  buildPlanCard: () => new El("div"), buildAskCard: () => new El("div"),
  buildComposer: () => new El("div"), buildViewBar: () => new El("div"),
  buildQueuePin: () => new El("div"), updateAgents: () => {},
  updateMoreBtn: () => {}, updateShownCount: () => {}, closeMonitorStream: () => {},
  IS_IPAD: false,
  $view: new El("div"),
  S: { cur: "sid1", ses: null },
  __fetched: [],
  fetch: (url) => {
    sandbox.__fetched.push(url);
    const kind = url.split("/").pop();
    return Promise.resolve({ json: () => Promise.resolve({ [kind]: FIXTURES[kind] }) });
  },
};
// the file registers a document-level click delegate at load; the harness only
// calls functions, so a no-op listener sink is enough
sandbox.document.addEventListener = () => {};
sandbox.window = sandbox;
sandbox.window.addEventListener = () => {};
vm.createContext(sandbox);
// `const SECTIONS = …` is a LEXICAL top-level binding, so it never becomes a
// property of the vm global the way a `function` declaration does. Evaluate the
// source and the export in ONE script so they share that scope.
vm.runInContext(fs.readFileSync(process.argv[2], "utf8")
                + "\n;globalThis.SECTIONS = SECTIONS;",
                sandbox, { filename: process.argv[2] });

const FIXTURES = {
  monitors: [
    { task: "m-old", description: "watcher", command: "tail -f x", live: false,
      started_at: 100, ended_at: 200, event_count: 3, end_reason: "silent" },
    { task: "m-live", command: "tail -f y", live: true, started_at: 150,
      event_count: 1, persistent: true },
  ],
  jobs: [
    { task: "j-live", command: "make build\nmore", live: true, started_at: 300,
      lines: 12 },
    { task: "j-done", command: "npm test", live: false, started_at: 50,
      ended_at: 90, lines: 4, end_reason: "writer gone" },
  ],
  memory: [{ path: "/w/a.md", name: "a", verb: "Read", count: 1, ts: 5 }],
};

function freshSes() {
  const ses = {
    tab: null, body: new El("div"),
    monitors: null, monitorFocus: null, monPoll: null,
    jobs: null, jobFocus: null, jobPoll: null,
    memory: null, noteTrail: null, memGrid: null,
    monitorsGrid: null, jobsGrid: null,
    // the REAL setTabBadge runs (the source's own declaration shadows any
    // stub), so give it the meta map and the three tab anchors it patches
    meta: {}, monTab: new El("a"), jobTab: new El("a"), memTab: new El("a"),
  };
  sandbox.S.ses = ses;
  return ses;
}

/* Render one section's GRID and report what came out. */
function grid(kind) {
  const sec = sandbox.SECTIONS[kind];
  const ses = freshSes();
  ses.tab = kind;
  const wrap = new El("div", "sgrid");
  wrap.isConnected = true;
  ses[sec.grid] = wrap;
  ses[sec.list] = FIXTURES[kind];
  sandbox.renderSectionGrid(kind);
  return {
    cards: wrap.children.length,
    // live-first, then newest-started — the order both grids owe
    order: wrap.children.map(c => (c.href || "").split("/").pop()),
    text: wrap.textContent,
  };
}

function emptyGrid(kind) {
  const sec = sandbox.SECTIONS[kind];
  const ses = freshSes();
  ses.tab = kind;
  const wrap = new El("div", "sgrid");
  wrap.isConnected = true;
  ses[sec.grid] = wrap;
  ses[sec.list] = [];
  sandbox.renderSectionGrid(kind);
  return wrap.textContent;
}

function crumbs(kind) {
  const item = FIXTURES[kind][0];
  const nav = sandbox.sectionCrumbs(kind, "sid1", item);
  return { back: nav.children[0].href, text: nav.textContent };
}

/* The poll only runs while something in the section is LIVE and that tab (or
   its drill-down) is what you are looking at. */
function pollRuns(kind, opts) {
  const sec = sandbox.SECTIONS[kind];
  const ses = freshSes();
  ses.tab = opts.tab;
  ses[sec.focus] = opts.focus || null;
  ses[sec.list] = opts.live ? FIXTURES[kind]
    : FIXTURES[kind].map(x => ({ ...x, live: false }));
  const before = timers.set;
  sandbox.scheduleSectionPoll(kind);
  const started = timers.set > before;
  sandbox.clearSectionPoll(kind);
  return started;
}

const out = {
  kinds: Object.keys(sandbox.SECTIONS),
  grids: { monitors: grid("monitors"), jobs: grid("jobs") },
  empty: { monitors: emptyGrid("monitors"), jobs: emptyGrid("jobs") },
  crumbs: { monitors: crumbs("monitors"), jobs: crumbs("jobs") },
  poll: {
    liveOnTab: pollRuns("monitors", { tab: "monitors", live: true }),
    liveOnDrill: pollRuns("jobs", { tab: "job:j-live", focus: "j-live", live: true }),
    liveElsewhere: pollRuns("jobs", { tab: "mirror", live: true }),
    deadOnTab: pollRuns("jobs", { tab: "jobs", live: false }),
  },
  badges: {},
  fetched: {},
};

/* loadSection: one fetch per kind, the badge patched from the list length, the
   grid painted. Memory takes the same fetch + badge path but repaints through
   its own paintMemory (a grid OR an open note viewer) and has no drill-down. */
const seq = ["monitors", "jobs", "memory"];
let chain = Promise.resolve();
for (const kind of seq) {
  chain = chain.then(() => {
    const sec = sandbox.SECTIONS[kind];
    const ses = freshSes();
    ses.tab = kind;
    if (sec.grid) {
      const wrap = new El("div", "sgrid");
      wrap.isConnected = true;
      ses[sec.grid] = wrap;
    }
    sandbox.__fetched = [];
    sandbox.loadSection(kind);
    return new Promise(r => {
      let n = 0;
      const tick = () => (++n < 30 ? Promise.resolve().then(tick) : r());
      tick();
    }).then(() => {
      // the badge is the tab anchor's `.count` text AND the cached meta field —
      // both patched from the fetched list length
      const a = ses[sec.tabEl];
      const c = a.querySelector(".count");
      out.badges[kind] = { count: c ? c.textContent : "",
                           meta: ses.meta[sec.countField],
                           painted: sec.grid ? ses[sec.grid].children.length
                                             : null };
      out.fetched[kind] = sandbox.__fetched.slice();
    });
  });
}

chain.then(() => process.stdout.write(JSON.stringify(out, null, 1)));
