"use strict";
const fs = require("fs");
const vm = require("vm");
const { El, domGlobals } = require("./domshim");

const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number, Promise,
  setInterval: () => 1, clearInterval: () => {},
  setTimeout: () => 1, clearTimeout: () => {},
  performance: { now: () => 0 },
  ...domGlobals(),
  encodeURIComponent,
  dur: seconds => Math.max(0, seconds | 0) + "s",
  tp: text => text,
  BUSY_TABS: ["thinking", "working", "executing", "awaiting_background"],
  liveTab: () => sandbox.currentTabState,
  postJSON: () => Promise.resolve({}),
  clog: () => {}, loadOlder: () => {}, renderAttention: () => {},
  agentStatus: actor => ["", (actor && actor.stateClass) || ""],
  agentQ: () => "",
  S: null,
  currentTabState: "",
  fetch: () => Promise.resolve({ json: () => Promise.resolve({
    items: [], oldest_cursor: 0, has_more: false,
  }) }),
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);

let viewSequence = 0;
function activity(summaryKind, options) {
  const settings = options || {};
  const element = new El("div", settings.itemGroup === "messages" ? "msg" : "blk");
  element.dataset.itemGroup = settings.itemGroup || "commands";
  element.dataset.summaryKind = summaryKind;
  if (settings.conversationKind)
    element.dataset.conversationKind = settings.conversationKind;
  if (settings.failed) element.dataset.bad = "1";
  if (settings.linesAdded) element.dataset.add = String(settings.linesAdded);
  if (settings.linesRemoved) element.dataset.rem = String(settings.linesRemoved);
  if (settings.actorAssignmentId) element.dataset.actorAssignmentId = settings.actorAssignmentId;
  if (settings.messageId) element.dataset.messageId = settings.messageId;
  if (settings.state) element.dataset.state = settings.state;
  element.dataset.viewKey = String(++viewSequence);
  element.dataset.activityTime = String(Date.now() / 1000 - 30);
  return element;
}

const prompt = () => activity("message", {
  itemGroup: "messages", conversationKind: "prompt",
});
const reply = () => activity("message", {
  itemGroup: "messages", conversationKind: "message",
});

function scene(mode, oldestFirst, tabState) {
  viewSequence = 0;
  const stream = new El("div", "stream");
  for (const item of oldestFirst)
    stream.insertBefore(item, stream.children[0]);
  sandbox.currentTabState = tabState || "";
  sandbox.S = {
    currentSessionId: "session-one",
    sessions: [],
    sessionView: {
      stream, view: mode, viewOpen: new Set(), viewSeq: viewSequence,
      viewTimer: null, viewFill: 0, oldest: 0, itemNodes: new Map(),
      meta: {}, loadingOlder: false, moreEl: null,
    },
  };
  sandbox.applyViewMode();
  return sandbox.S.sessionView;
}

function summaries(sessionView) {
  return sessionView.stream.children
    .filter(element => element.classList.contains("vsum"))
    .map(element => ({
      text: element.querySelector(".vtext").textContent,
      failed: element.querySelector(".vdot").classList.contains("bad"),
      open: element.dataset.open,
    }));
}

function shownCount(sessionView) {
  return sessionView.stream.children.filter(
    element => element.dataset.itemGroup && !element.classList.contains("vhide")
  ).length;
}

const defaultView = scene("default", [
  prompt(),
  activity("file_read", { itemGroup: "files" }),
  activity("shell"),
  reply(),
]);
const focusView = scene("focus", [
  prompt(),
  activity("actor_assignment", { itemGroup: "agents", actorAssignmentId: "child-one" }),
  activity("file_edit", { itemGroup: "files", linesAdded: 12, linesRemoved: 3 }),
  activity("shell", { failed: true }),
  reply(),
]);
const focusSummary = summaries(focusView)[0];
const summaryRow = focusView.stream.children.find(
  element => element.classList.contains("vsum"));
summaryRow.onclick();
const openState = summaries(focusView)[0].open;

const finishedAgentView = scene("focus", [
  prompt(),
  activity("actor_assignment", {
    itemGroup: "agents", actorAssignmentId: "finished-child", state: "running",
  }),
  activity("actor_assignment", {
    itemGroup: "agents", actorAssignmentId: "finished-child", state: "succeeded",
  }),
  reply(),
], "working");

const expansionView = scene("default", [activity("background")]);
const expansionBlock = expansionView.stream.children.find(
  element => element.classList.contains("blk"));
expansionBlock.dataset.open = "0";
expansionBlock.dataset.userset = "1";
sandbox.setViewMode("verbose");
const verboseBlockOpen = expansionBlock.dataset.open;
const verboseUserState = expansionBlock.dataset.userset;
sandbox.setViewMode("focus");
const focusBlockOpen = expansionBlock.dataset.open;

process.stdout.write(JSON.stringify({
  default: { summaries: summaries(defaultView), shown: shownCount(defaultView) },
  focus: {
    summary: focusSummary,
    shownExpanded: shownCount(focusView),
    open: openState,
  },
  finishedAgent: summaries(finishedAgentView)[0],
  blockExpansion: {
    verbose: verboseBlockOpen,
    focus: focusBlockOpen,
    userStateAfterSwitch: verboseUserState || null,
  },
}));
