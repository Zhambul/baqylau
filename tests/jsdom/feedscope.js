// tests/jsdom/feedscope.js — drives the REAL feed scope
// (dashboard/static/app.05-session.js `feedScopeActorId` + `inFeedScope`) and
// prints one JSON verdict object, which tests/test_dashboard_dom.py asserts on.
//
// Why this exists: a session's feed must default to the LEAD actor's own
// entries — a launched agent's messages and commands stay out of sight until a
// reader opens that agent's own `?agent=` scope. The main dashboard once
// dropped this default (a rewrite left every entry-loading path unfiltered),
// so the feed showed every actor at once; the terminal pane, which has no
// scope at all, must keep showing everything.
//
// Usage: node tests/jsdom/feedscope.js dashboard/static/app.05-session.js
// SKIPPED when `node` is absent, like every harness here (docs/testing.md).
"use strict";
const fs = require("fs");
const vm = require("vm");

const { domGlobals } = require("./domshim");

const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number,
  setInterval: () => 1, clearInterval: () => {},
  setTimeout: () => 1, clearTimeout: () => {},
  performance: { now: () => 0 },
  ...domGlobals(),
  encodeURIComponent,
  S: null,
  // app.06-clientlog.js is not loaded here; collect the loud-failure codes so
  // the test can assert an unknown lead is REPORTED, not silently painted over.
  loudCodes: [],
  failLoudly: function (sessionId, code) { sandboxLoudCodes.push(code); },
};
const sandboxLoudCodes = sandbox.loudCodes;
sandbox.window = sandbox;
vm.createContext(sandbox);
for (const file of process.argv.slice(2))
  vm.runInContext(fs.readFileSync(file, "utf8"), sandbox);

const leadEntry = { actor_id: "lead" };
const childEntry = { actor_id: "child" };

// No scope chosen: the feed falls back to the session's own lead actor.
sandbox.S = { sessionView: { agent: "", sessionFacts: { lead_actor_id: "lead" } } };
const defaultScope = sandbox.feedScopeActorId();

// A scope chosen (the reader opened one agent's own view): that agent wins
// over the lead, exactly like `scopedActor` already picked for the aggregate.
sandbox.S = { sessionView: { agent: "child", sessionFacts: { lead_actor_id: "lead" } } };
const chosenScope = sandbox.feedScopeActorId();

// No session facts known yet (a caller mid-load): an unknown lead is an empty
// scope. `inFeedScope` reports it loudly and paints everything, so the feed
// is not blank while the problem is on the screen and in the audit.
sandbox.S = { sessionView: { agent: "", sessionFacts: null } };
const unknownScope = sandbox.feedScopeActorId();

process.stdout.write(JSON.stringify({
  defaultScope,
  chosenScope,
  unknownScope,
  leadInDefaultScope: sandbox.inFeedScope(leadEntry, "lead"),
  childInDefaultScope: sandbox.inFeedScope(childEntry, "lead"),
  childInChosenScope: sandbox.inFeedScope(childEntry, "child"),
  leadInChosenScope: sandbox.inFeedScope(leadEntry, "child"),
  everythingInUnknownScope: sandbox.inFeedScope(leadEntry, "") && sandbox.inFeedScope(childEntry, ""),
  unknownScopeReportedLoudly: sandbox.loudCodes.includes("feed.scope.unknown_lead"),
}));
