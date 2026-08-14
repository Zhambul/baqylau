// Drives the real optimistic-prompt reconciler in app.08-composer.js. A prompt
// sent after an earlier turn must lose its grey stand-in when the canonical
// message arrives; canonical DashboardItem fields are the only accepted input.
"use strict";
const fs = require("fs");
const vm = require("vm");

const removed = [];
const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number,
  setTimeout: () => 1, clearTimeout: () => {},
  setInterval: () => 1, clearInterval: () => {},
  performance: { now: () => 0 },
  promptMatches: (real, sent) => !!sent && (real || "").endsWith(sent),
  hintAudit: () => {},
  S: null,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);

function pending(text) {
  const handle = {
    text, timer: 1, sessionView: sandbox.S.sessionView,
    node: { remove: () => removed.push(text) },
  };
  sandbox.S.sessionView.pending.push(handle);
}

sandbox.S = { sessionView: { pending: [] } };
pending("say hi");
pending("run ls and say hi again");

// An earlier prompt is unrelated to the two outstanding stand-ins.
sandbox.drainPending([{
  item_type: "message", conversation_kind: "prompt", plain_text: "first message",
}]);
const afterUnrelated = sandbox.S.sessionView.pending.map(item => item.text);

// The second canonical prompt reconciles exactly one matching stand-in.
sandbox.drainPending([{
  item_type: "message", conversation_kind: "prompt",
  plain_text: "run ls and say hi again",
}]);

process.stdout.write(JSON.stringify({
  afterUnrelated,
  remaining: sandbox.S.sessionView.pending.map(item => item.text),
  removed,
}));
