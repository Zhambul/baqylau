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
  sessionId: row => row.session.session_id,
  sessionIsLive: row => row.session.state === "running",
  clog: (sessionId, name, details) => closeEvents.push({ sessionId, name, details }),
  closeSettle: (sessionId, phase) => closeEvents.push({ sessionId, phase }),
  S: {
    currentSessionId: "new-session",
    sessionView: {},
    closePend: { "closed-session": { t0: 10 } },
    sessions: [{ session: { session_id: "closed-session", state: "finished" } }],
    cards: new Map(),
  },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), sandbox);

sandbox.reconcileCloses();
sandbox.loadCanonicalSessionSnapshot("new-session", "");

setImmediate(() => {
  process.stdout.write(JSON.stringify({
    closeReconciled: closeEvents.some(event =>
      event.sessionId === "closed-session" && event.phase === "reconciled"),
    metadataFailure: closeEvents.find(event => event.name === "meta.fail") || null,
    retryScheduled: retryCallbacks.length === 1,
  }));
});
