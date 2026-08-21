"use strict";
const fs = require("fs");
const vm = require("vm");
const { domGlobals } = require("./domshim");

const closeEvents = [];
const retryCallbacks = [];
const sandbox = {
  console,
  Date,
  Math,
  Set,
  Map,
  JSON,
  String,
  Number,
  encodeURIComponent,
  ...domGlobals(),
  performance: { now: () => 25 },
  setTimeout: callback => { retryCallbacks.push(callback); return 1; },
  clearTimeout: () => {},
  setInterval: () => 1,
  clearInterval: () => {},
  fetch: () => Promise.reject(new Error("connection reset")),
  // A failed aggregate read still opens the stream: the feed is how a session
  // recovers, and giving up on it would leave the view frozen for good.
  EventSource: class { addEventListener() {} close() {} },
  sessionId: row => row.session.session_id,
  sessionIsLive: row => !!row.live,
  clog: (sessionId, name, details) => closeEvents.push({ sessionId, name, details }),
  // the loud-failure path records like clog does; the assertions read the same list
  failLoudly: (sessionId, name, details) => closeEvents.push({ sessionId, name, details }),
  closeSettle: (sessionId, phase) => closeEvents.push({ sessionId, phase }),
  S: {
    currentSessionId: "new-session",
    sessionView: {},
    closePend: { "closed-session": { t0: 10 } },
    sessions: [{
      session: { session_id: "closed-session", state: "finished" },
      actors: [], live: false, repository: null,
    }],
    cards: new Map(),
  },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), sandbox);

sandbox.reconcileCloses();
// The aggregate read is the first thing opening a session does, and a transport
// failure there must land in the audit ONCE and schedule no retry loop.
sandbox.S.sessionView = { stream: sandbox.document.createElement("div"), tab: "mirror" };
sandbox.loadCanonicalSession("new-session");

setImmediate(() => {
  process.stdout.write(JSON.stringify({
    closeReconciled: closeEvents.some(event =>
      event.sessionId === "closed-session" && event.phase === "reconciled"),
    metadataFailure: closeEvents.find(event => event.name === "session.load.fail") || null,
    retryScheduled: retryCallbacks.length === 1,
  }));
});
