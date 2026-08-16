// tests/jsdom/sections.js — drives the REAL secondary-tab engine
// (the SECTIONS table + loadSection/renderSectionGrid/showSection/… in
// dashboard/static/app.11-chrome.js) over the shared DOM shim, and prints one
// JSON verdict object that test_l0_dashboard.py asserts on.
//
// It verifies both canonical snapshot-backed sections still render their
// existing cards, order, empty labels, breadcrumbs, and badges. It also proves
// the browser no longer starts a secondary fetch or polling timer.
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
  agentQ: (sep) => { const a = (sandbox.S && sandbox.S.sessionView && sandbox.S.sessionView.agent) || "";
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
  S: { currentSessionId: "sid1", sessionView: null },
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
vm.runInContext(process.argv.slice(2).map(p => fs.readFileSync(p, "utf8")).join("\n")
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
};

function freshSes() {
  const sessionView = {
    tab: null, body: new El("div"),
    monitors: null, monitorFocus: null, monPoll: null,
    jobs: null, jobFocus: null, jobPoll: null,
    monitorsGrid: null, jobsGrid: null,
    // the REAL setTabBadge runs (the source's own declaration shadows any
    // stub), so give it the meta map and the three tab anchors it patches
    meta: {}, monTab: new El("a"), jobTab: new El("a"),
  };
  sandbox.S.sessionView = sessionView;
  return sessionView;
}

/* Render one section's GRID and report what came out. */
function grid(kind) {
  const sec = sandbox.SECTIONS[kind];
  const sessionView = freshSes();
  sessionView.tab = kind;
  const wrap = new El("div", "sgrid");
  wrap.isConnected = true;
  sessionView[sec.grid] = wrap;
  sessionView[sec.list] = FIXTURES[kind];
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
  const sessionView = freshSes();
  sessionView.tab = kind;
  const wrap = new El("div", "sgrid");
  wrap.isConnected = true;
  sessionView[sec.grid] = wrap;
  sessionView[sec.list] = [];
  sandbox.renderSectionGrid(kind);
  return wrap.textContent;
}

function crumbs(kind) {
  const item = FIXTURES[kind][0];
  const nav = sandbox.sectionCrumbs(kind, "sid1", item);
  return { back: nav.children[0].href, text: nav.textContent };
}



function loaded(kind) {
  const sec = sandbox.SECTIONS[kind];
  const sessionView = freshSes();
  sessionView.tab = kind;
  const wrap = new El("div", "sgrid");
  wrap.isConnected = true;
  sessionView[sec.grid] = wrap;
  sessionView[sec.list] = FIXTURES[kind];
  sandbox.__fetched = [];
  sandbox.loadSection(kind);
  const count = sessionView[sec.tabEl].querySelector(".count");
  return {
    count: count ? count.textContent : "",
    cards: wrap.children.length,
    fetched: sandbox.__fetched.slice(),
  };
}

const out = {
  kinds: Object.keys(sandbox.SECTIONS),
  grids: { monitors: grid("monitors"), jobs: grid("jobs") },
  empty: { monitors: emptyGrid("monitors"), jobs: emptyGrid("jobs") },
  crumbs: { monitors: crumbs("monitors"), jobs: crumbs("jobs") },
  loaded: { monitors: loaded("monitors"), jobs: loaded("jobs") },
  timers,
};

const errors = [];
if (out.grids.monitors.cards !== 2) errors.push("monitor cards");
if (out.grids.jobs.cards !== 2) errors.push("job cards");
if (!out.empty.monitors.includes("no monitors")) errors.push("monitor empty text");
if (!out.empty.jobs.includes("no background jobs")) errors.push("job empty text");
if (out.loaded.monitors.fetched.length || out.loaded.jobs.fetched.length)
  errors.push("canonical sections must not fetch");
if (timers.set || timers.cleared) errors.push("canonical sections must not poll");
out.ok = errors.length === 0;
out.errors = errors;
process.stdout.write(JSON.stringify(out, null, 1));
if (errors.length) process.exitCode = 1;
