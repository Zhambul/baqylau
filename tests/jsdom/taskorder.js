// tests/jsdom/taskorder.js — drives the REAL feed builder
// (dashboard/static/app.05-session.js `appendEntries`) over the shared DOM shim
// and prints one JSON verdict object, which tests/test_dashboard_dom.py asserts on.
//
// Why this exists: the feed is newest-TOP, so a plain prepend puts an
// `Agent finished` card ABOVE the answer it contributed to whenever that answer
// arrived on an earlier tick and is already on screen. Nothing but the browser
// can fix that — the entry stream is in commit order and knows nothing of what
// is painted — and a grep cannot tell "inserted under the right bubble" from
// "inserted anywhere".
//
// Usage: node tests/jsdom/taskorder.js <markup.js> <entries.js> <session.js>
// SKIPPED when `node` is absent, like every harness here (docs/testing.md).
"use strict";
const fs = require("fs");
const vm = require("vm");

const { El, domGlobals } = require("./domshim");

/* ---------- the app globals appendItems' neighbours call (everything else is in
   the file under test) */
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
  // app.08-composer's half of the live feed — a stub here: this harness measures
  // WHERE an item lands, and the optimistic-bubble swap has its own coverage
  drainPending: () => {},
  promptMatches: (a, b) => a === b,
  renderQueue: () => {},
  saveQueue: () => {},
  renderSessionChrome: () => {},
  // The session-row accessors live in app.00-core.js, which this harness does
  // not load: it measures WHERE an entry lands, not how a row reads.
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
  S: null,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
for (const file of process.argv.slice(2))
  vm.runInContext(fs.readFileSync(file, "utf8"), sandbox);

/* ---------- fixtures: the two entries the whole reconcile is about */

const TURN = "019fb66b-13a6";          // the measured session's parent turn
const ASSIGNMENT = "019fb66b-31de#019fb66b-325d";

const text = value => ({ text: value, media_type: "text/plain" });

// the parent turn's FINAL answer bubble
const answer = () => ({
  entry_id: "answer", type: "message", cursor: 1, actor_id: "lead",
  parent_actor_id: null, turn_id: TURN, occurred_at: 1, summary: null,
  body: {
    message_id: "answer", role: "assistant", phase: "end_turn",
    content: text("Bali: a shower, 29C."), recipient_actor_id: null, reply_to: null,
  },
});

// …and a child's result card: an assignment END naming that turn
const result = (g, turn = TURN, step = "end") => ([{
  entry_id: g, type: step === "end" ? "assignment_finished" : "assignment_started",
  cursor: 2, actor_id: "lead", parent_actor_id: null, turn_id: turn || null,
  occurred_at: step === "end" ? 3 : 2, summary: null,
  body: step === "end"
    ? { assignment_id: ASSIGNMENT, state: "succeeded", result: text("Denpasar: shower, 29C.") }
    : { assignment_id: ASSIGNMENT, assigned_actor_name: "Explore", prompt: null },
}]);

// an ordinary command block — the control: nothing about it is an assignment
// endpoint, so it must still land at the TOP however many answers are on screen
const command = entryId => ([{
  entry_id: entryId, type: "shell_started", cursor: 4, actor_id: "lead",
  parent_actor_id: null, turn_id: null, occurred_at: 4, summary: null,
  body: { shell_id: entryId, command: text("echo hi"), execution: "foreground" },
}]);

function scene() {
  const stream = new El("div", "stream");
  sandbox.S = { currentSessionId: "sessionId", sessions: [], sessionView: {
    stream, view: "default", viewOpen: new Set(), viewSeq: 0, viewTimer: null,
    viewFill: 0, oldest: 0, itemNodes: new Map(), meta: {},
    loadingOlder: false, moreEl: null, agents: [], queue: [],
    shells: new Map(), actorRows: [], actorsById: {}, attentionEntries: [],
  } };
  return sandbox.S.sessionView;
}

// The feed top->bottom, i.e. NEWEST first — which is what "before" means here: a
// child's result must read BELOW the answer it precedes. The view mode's own
// collapse rows (`.vsum`, inserted by applyViewMode at the end of every append)
// are not feed items and are skipped.
function order(sessionView) {
  return sessionView.stream.children
    .filter(c => !c.classList.contains("vsum"))
    .map(c => (
      c.dataset.final === "1" ? "answer"
        : c.dataset.actorAssignmentPhase
          ? "task:" + (c.dataset.actorAssignmentPhase === "finished" ? "end" : "start")
          : c.dataset.itemGroup || "other"));
}

const out = {};

// 1. THE BUG: the answer arrived on an earlier tick, the completion follows.
{
  const sessionView = scene();
  sandbox.appendEntries([answer()]);
  sandbox.appendEntries(result("g1"));
  out.late = order(sessionView);
  // the card is ONE block (its body filled the same card, not a second row)
  out.lateBlocks = order(sessionView).length;
}

// 2. …and both in ONE tick: the server already ordered them (task_order), so the
//    page must simply lay them down in the order it was given.
{
  const sessionView = scene();
  sandbox.appendEntries([...result("g2"), answer()]);
  out.together = order(sessionView);
}

// 3. CONTROL — an ordinary block still lands at the top of the feed.
{
  const sessionView = scene();
  sandbox.appendEntries([answer()]);
  sandbox.appendEntries(command("g3"));
  out.control = order(sessionView);
}

// 4. A task END whose parent turn is not this answer's is NOT reconciled (a
//    different turn, and every Claude agent, whose tasks name no turn at all).
{
  const sessionView = scene();
  sandbox.appendEntries([answer()]);
  sandbox.appendEntries(result("g4", "some-other-turn"));
  out.otherTurn = order(sessionView);
  const ses2 = scene();
  sandbox.appendEntries([answer()]);
  sandbox.appendEntries(result("g5", ""));
  out.noTurn = order(ses2);
}

// 5. The task's START endpoint is not an anchor either: a launch card is emitted
//    while the turn is still running, so it belongs at the top like any other op.
{
  const sessionView = scene();
  sandbox.appendEntries([answer()]);
  sandbox.appendEntries(result("g6", TURN, "start"));
  out.startEndpoint = order(sessionView);
}

// 6. The canonical actor projection includes the lead because it describes the
// whole session. The dashboard's Agents surfaces present only child actors.
{
  const sessionView = scene();
  sandbox.applyCanonicalSnapshot({
    session: { lead_actor_id: "lead" },
    actors: [
      { actor_id: "lead", role: "lead", state: "running" },
      { actor_id: "child", parent_actor_id: "lead", role: "subagent", state: "running" },
      { actor_id: "grandchild", parent_actor_id: "child", role: "subagent", state: "running" },
    ],
    background_work: {}, usage: {}, statistics: {}, context: {},
  });
  out.agentIds = sessionView.agents.map(actor => actor.agent_id);
}

// 7. Copying a block reads the text it already holds: content is embedded in an
// entry now, so there is nothing to fetch and one target to copy.
{
  const sessionView = scene();
  sandbox.appendEntries(command("copyable"));
  const block = sessionView.stream.children.find(child => child.dataset.itemGroup);
  out.copyLink = !!(block && block.querySelector("[data-copy-block]"));
}

process.stdout.write(JSON.stringify(out, null, 1));
