// tests/jsdom/expand.js — drives the REAL block-expand click handler
// (dashboard/static/app.05-session.js `bindDashboardBlock`) and the render-time
// invariant check (`reportUnboundBlocks`) over the shared DOM shim, and prints
// one JSON verdict object, which tests/test_dashboard_dom.py asserts on.
//
// Why this exists: a feed block's body shows or hides through one attribute,
// `data-open`, set by a click on its header. Nothing but a real click on the
// real header, on a real block built by the real renderer, can tell "the click
// works" from "the click handler was never attached" — and nothing but the
// real audit call can tell "a broken click reports itself" from "a broken
// click vanishes without a trace".
//
// Usage: node tests/jsdom/expand.js <markup.js> <entries.js> <session.js>
// SKIPPED when `node` is absent, like every harness here (docs/testing.md).
"use strict";
const fs = require("fs");
const vm = require("vm");

const { El, domGlobals } = require("./domshim");

// Every `failLoudly` call this run makes, in order — the one thing every
// scenario below reads back. A real `toast`/`clog` pair would only hide what
// code fired; the audit call itself is the fact under test.
const loudCalls = [];

const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number,
  setInterval: () => 1, clearInterval: () => {},
  setTimeout: () => 1, clearTimeout: () => {},
  performance: { now: () => 0 },
  ...domGlobals(),
  encodeURIComponent,
  dur: sec => Math.max(0, sec | 0) + "s",
  tp: s => s,
  BUSY_TABS: ["thinking", "working", "executing", "awaiting-bg"],
  liveTab: () => "",
  postJSON: () => Promise.resolve({}),
  clog: () => {},
  loadOlder: () => {},
  agentQ: () => "",
  agentStatus: a => ["", (a && a.st) || ""],
  renderAttention: () => {},
  drainPending: () => {},
  promptMatches: (a, b) => a === b,
  renderQueue: () => {},
  saveQueue: () => {},
  renderSessionChrome: () => {},
  sessionTabState: () => "",
  sessionUsage: () => ({ tokens: {}, cost_in_usd: null }),
  updateStatsRow: () => {},
  updateAgents: () => {},
  updateRunning: () => {},
  renderAsk: () => {},
  renderPlan: () => {},
  renderTasks: () => {},
  renderGoal: () => {},
  loadSection: () => {},
  applySuggestion: () => {},
  directoryName: () => "",
  shortSid: value => value,
  navigator: { clipboard: null },
  toast: () => {},
  failLoudly: (sessionId, code, detail) => {
    loudCalls.push({ sessionId, code, detail: detail || {} });
  },
  S: null,
};
sandbox.window = sandbox;
sandbox.document.addEventListener = () => {};
vm.createContext(sandbox);
for (const file of process.argv.slice(2))
  vm.runInContext(fs.readFileSync(file, "utf8"), sandbox);

const text = value => ({ text: value, media_type: "text/plain" });

// A finished shell command: the ordinary case a user expects to open — a
// block with real body content (its captured output).
const shell = () => ([
  {
    entry_id: "start", type: "shell_started", cursor: 1, actor_id: "lead",
    parent_actor_id: null, turn_id: null, occurred_at: 1, summary: null,
    body: { shell_id: "sh1", command: text("echo hi"), execution: "foreground" },
  },
  {
    entry_id: "finish", type: "shell_finished", cursor: 2, actor_id: "lead",
    parent_actor_id: null, turn_id: null, occurred_at: 2, summary: null,
    body: { shell_id: "sh1", state: "succeeded", exit_code: 0, result: text("hi\n") },
  },
]);

function scene() {
  const stream = new El("div", "stream");
  sandbox.S = { currentSessionId: "sessionId", sessions: [], sessionView: {
    stream, view: "default", viewOpen: new Set(), viewSeq: 0, viewTimer: null,
    viewFill: 0, oldest: 0, itemNodes: new Map(), meta: {},
    loadingOlder: false, moreEl: null, agents: [], queue: [],
    shells: new Map(), actorRows: [], actorsById: {}, attentionEntries: [],
    unboundReported: new Set(),
  } };
  return sandbox.S.sessionView;
}

/* ---------- 1. the ordinary click: opens, closes, reports nothing --------- */
const sessionView = scene();
sandbox.appendEntries(shell());
const block = sessionView.stream.children.find(child => child.classList.contains("blk"));
const before = block.dataset.open;
const header = block.querySelector(".bhead");
header.onclick({ target: header });
const afterFirstClick = block.dataset.open;
header.onclick({ target: header });
const afterSecondClick = block.dataset.open;
const normalClickLoudCalls = loudCalls.slice();

/* ---------- 2. a throwing click reports feed.block.toggle.fail ------------ */
loudCalls.length = 0;
const throwingEvent = { get target() { throw new Error("boom"); } };
header.onclick(throwingEvent);
const toggleFailure = loudCalls[0] || null;

/* ---------- 3. a block missing its body reports feed.block.unbound, once - */
loudCalls.length = 0;
const corruptScene = scene();
sandbox.appendEntries(shell());
const corruptBlock = corruptScene.stream.children.find(c => c.classList.contains("blk"));
corruptBlock.querySelector(".bbody").remove();   // simulate the render regression
const finishEntry = { entry_id: "finish", type: "shell_finished" };
sandbox.reportUnboundBlocks([finishEntry]);
const unboundFirstPass = loudCalls.slice();
loudCalls.length = 0;                             // isolate the SECOND call's own report
sandbox.reportUnboundBlocks([finishEntry]);       // same entry again: no repeat
const unboundSecondPass = loudCalls.slice();

process.stdout.write(JSON.stringify({
  hasHeader: !!header,
  hasHandler: typeof (header && header.onclick) === "function",
  before,
  afterFirstClick,
  afterSecondClick,
  normalClickLoudCalls,
  toggleFailure,
  unboundFirstPass,
  unboundSecondPass,
}));
