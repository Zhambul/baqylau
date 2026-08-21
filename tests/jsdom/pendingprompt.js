// Drives the real optimistic-prompt reconciler in app.08-composer.js. A prompt
// sent after an earlier turn must lose its grey stand-in when the delivered
// message arrives; feed ENTRIES are the only accepted input.
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
  // The one shape question the reconciler asks, answered by the module that owns
  // it in the browser (app.05-session.js).
  deliveredPromptText: entry => {
    const body = entry.body || {};
    if (entry.type !== "message" || body.role !== "user" || body.phase !== "prompt")
      return null;
    return ((body.content && body.content.text) || "").trim();
  },
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

function promptEntry(text) {
  return {
    entry_id: "entry-" + text, type: "message", cursor: 1, actor_id: "actor-one",
    body: {
      message_id: "message-" + text, role: "user", phase: "prompt",
      content: { text: text, media_type: "text/plain" },
    },
  };
}

// An earlier prompt is unrelated to the two outstanding stand-ins.
sandbox.drainPending([promptEntry("first message")]);
const afterUnrelated = sandbox.S.sessionView.pending.map(item => item.text);

// The second delivered prompt reconciles exactly one matching stand-in.
sandbox.drainPending([promptEntry("run ls and say hi again")]);

process.stdout.write(JSON.stringify({
  afterUnrelated,
  remaining: sandbox.S.sessionView.pending.map(item => item.text),
  removed,
}));
