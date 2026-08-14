// tests/jsdom/taskorder.js — drives the REAL feed builder
// (dashboard/static/app.05-session.js `appendItems`) over the shared DOM shim and
// prints one JSON verdict object, which test_l0_dash_viewmode.py asserts on.
//
// Why this exists: the SERVER orders a actor assignment's result and the parent turn's
// final answer whenever both are in one payload (dashboard/read/mirror.task_order
// — pinned from Python). The case only the BROWSER can fix is the one where the
// answer went out on an earlier SSE tick and is already on screen: the feed is
// newest-TOP, so a plain prepend puts the `Agent finished` card ABOVE the answer
// it contributed to. That reconcile lives entirely in the page, and a grep cannot
// tell "inserted under the right bubble" from "inserted anywhere".
//
// Usage: node tests/jsdom/taskorder.js dashboard/static/app.05-session.js
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
  S: null,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);

/* ---------- fixtures: the two items the whole reconcile is about */

const TURN = "019fb66b-13a6";          // the measured session's parent turn
const ASSIGNMENT = "019fb66b-31de#019fb66b-325d";

// the parent turn's FINAL answer bubble, as read/mirror.conv_items serves it
const answer = () => ({
  item_id: "answer", item_type: "message", summary_kind: "message",
  actor_id: "lead", state: null,
  html: "<div class=\"msg message\">Bali: a shower, 29C.</div>",
  plain_text: "Bali: a shower, 29C.", content_reference: null,
  conversation_kind: "message", turn_id: TURN, final: true,
  started_at: 1,
});

// …and a child's ⇠ result CARD (header + body, one copy group), as
// opshtml.op_items serves it: a task END naming that turn
const result = (g, turn = TURN, step = "end") => ([
  { item_id: g, item_type: "actor_assignment", summary_kind: "actor_assignment",
    actor_id: "lead", state: step === "start" ? "running" : "succeeded",
    html: "<div class=\"blk\"><div class=\"bhead\"></div><div class=\"bbody\">Denpasar: shower, 29C.</div></div>",
    plain_text: "Denpasar: shower, 29C.", content_reference: null,
    actor_assignment_id: ASSIGNMENT, actor_assignment_phase: step === "end" ? "finished" : "started",
    turn_id: turn || null, started_at: 2, finished_at: step === "end" ? 3 : null },
]);

// an ordinary command block — the control: nothing about it is a task endpoint,
// so it must still land at the TOP however many answers are on screen
const command = itemId => ([{
  item_id: itemId, item_type: "operation", summary_kind: "shell",
  actor_id: "lead", state: "running",
  html: "<div class=\"blk\"><div class=\"bhead\"></div><div class=\"bbody\"></div></div>",
  plain_text: "echo hi", content_reference: null, started_at: 4,
}]);

function scene() {
  const stream = new El("div", "stream");
  sandbox.S = { currentSessionId: "sessionId", sessions: [], sessionView: {
    stream, view: "default", viewOpen: new Set(), viewSeq: 0, viewTimer: null,
    viewFill: 0, oldest: 0, itemNodes: new Map(), meta: {},
    loadingOlder: false, moreEl: null, agents: [], queue: [],
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
  sandbox.appendItems([answer()]);
  sandbox.appendItems(result("g1"));
  out.late = order(sessionView);
  // the card is ONE block (its body filled the same card, not a second row)
  out.lateBlocks = order(sessionView).length;
}

// 2. …and both in ONE tick: the server already ordered them (task_order), so the
//    page must simply lay them down in the order it was given.
{
  const sessionView = scene();
  sandbox.appendItems([...result("g2"), answer()]);
  out.together = order(sessionView);
}

// 3. CONTROL — an ordinary block still lands at the top of the feed.
{
  const sessionView = scene();
  sandbox.appendItems([answer()]);
  sandbox.appendItems(command("g3"));
  out.control = order(sessionView);
}

// 4. A task END whose parent turn is not this answer's is NOT reconciled (a
//    different turn, and every Claude agent, whose tasks name no turn at all).
{
  const sessionView = scene();
  sandbox.appendItems([answer()]);
  sandbox.appendItems(result("g4", "some-other-turn"));
  out.otherTurn = order(sessionView);
  const ses2 = scene();
  sandbox.appendItems([answer()]);
  sandbox.appendItems(result("g5", ""));
  out.noTurn = order(ses2);
}

// 5. The task's START endpoint is not an anchor either: a launch card is emitted
//    while the turn is still running, so it belongs at the top like any other op.
{
  const sessionView = scene();
  sandbox.appendItems([answer()]);
  sandbox.appendItems(result("g6", TURN, "start"));
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

// 7. Operation copy actions remain separate: command and output are distinct
// canonical facts and therefore distinct clipboard targets.
{
  const links = sandbox.canonicalOperationContentLinks({
    command_reference: "start:operation_command",
    output_reference: "finish:operation_output",
  });
  out.copyLinks = links.children.map(link => ({
    label: link.textContent,
    reference: link.dataset.contentReference,
  }));
}

process.stdout.write(JSON.stringify(out, null, 1));
