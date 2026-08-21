// tests/jsdom/liveness.js — drives the REAL meta assembly
// (dashboard/static/app.05-session.js `canonicalSessionMeta` +
// `applyCanonicalSnapshotRefresh`) and prints one JSON verdict object, which
// tests/test_dashboard_dom.py asserts on.
//
// Why this exists: `live` — "a terminal window is attached" — is the boolean
// every session gesture is gated on, and it is assembled by the browser rather
// than read straight off one field. It had TWO owners once (this payload and
// the preferences route's window_id), they disagreed by load order, and every
// live session's composer went dead. It now has ONE owner, which puts the whole
// weight on two rules a grep cannot check:
//   - the canonical payload DECIDES it, and
//   - a payload that does not CARRY it may not WRITE it — the refresh path
//     rebuilds meta from a synthetic {session, actors} with no liveness in it,
//     and a merge that wrote `false` there would park a running session
//     (re-disabling the composer a second after it came up: the original bug,
//     wearing a different hat).
//
// Usage: node tests/jsdom/liveness.js dashboard/static/app.05-session.js
// SKIPPED when `node` is absent, like every harness here (docs/testing.md).
"use strict";
const fs = require("fs");
const vm = require("vm");

const { domGlobals } = require("./domshim");

const out = { errors: [] };
const ck = (name, got, want) => {
  out[name] = got === undefined ? null : JSON.parse(JSON.stringify(got));
  if (want !== undefined && JSON.stringify(got) !== JSON.stringify(want))
    out.errors.push(`${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
};

const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number, Object, Array, Boolean,
  Promise, RegExp,
  setInterval: () => 1, clearInterval: () => {},
  setTimeout: () => 1, clearTimeout: () => {},
  performance: { now: () => 0 },
  ...domGlobals(),
  encodeURIComponent,
  fetch: () => new Promise(() => {}),
  // the neighbours meta assembly calls, each stubbed to the smallest thing that
  // keeps the assembly honest — what they render is not the subject
  liveTab: () => "",
  postJSON: () => Promise.resolve({}),
  clog: () => {},
  renderAsk: () => {},
  renderPlan: () => {},
  renderTasks: () => {},
  renderGoal: () => {},
  renderAttention: () => {},
  renderSessionChrome: () => {},
  updateStatsRow: () => {},
  updateAgents: () => {},
  updateRunning: () => {},
  updateErrCount: () => {},
  applyViewMode: () => {},
  applySuggestion: () => {},
  drainPending: () => {},
  renderQueue: () => {},
  saveQueue: () => {},
  loadSection: () => {},
  directoryName: () => "",
  shortSid: value => value,
  dur: sec => Math.max(0, sec | 0) + "s",
  tp: s => s,
  BUSY_TABS: [],
  sessionTabState: () => "",
  sessionUsage: () => ({ tokens: {}, cost_in_usd: null }),
  // app.00-core.js is not loaded (it reads the whole page out of the document at
  // load time); this is the one core accessor under test here, verbatim.
  sessionIsLive: row => !!row.live,
  S: null,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
for (const file of process.argv.slice(2))
  vm.runInContext(fs.readFileSync(file, "utf8"), sandbox);

const SESSION = {
  session_id: "s1",
  harness: "claude_code",
  title: "t",
  working_directory: "/w",
  lead_actor_id: "a1",
  state: "running",
};
const ACTORS = [{ actor_id: "a1", model: "opus", statistics: {} }];

function view() {
  sandbox.S = {
    currentSessionId: "s1",
    sessions: [],
    sessionView: { meta: {}, sessionFacts: SESSION, actorRows: ACTORS,
                   shells: new Map() },
  };
  return sandbox.S.sessionView;
}
const meta = data => vm.runInContext("canonicalSessionMeta", sandbox)(data);
const refresh = () => vm.runInContext("applyCanonicalSnapshotRefresh", sandbox)();

/* ---- the payload decides ------------------------------------------------- */
view();
ck("live_from_payload", meta({ session: SESSION, actors: ACTORS, live: true }).live,
   true);
ck("parked_from_payload", meta({ session: SESSION, actors: ACTORS, live: false }).live,
   false);

/* ---- a payload without the fact does not write it ----------------------- */
// the KEY is absent, not false: the merge downstream must leave what it finds
ck("synthetic_omits_live",
   "live" in meta({ session: SESSION, actors: ACTORS }), false);

/* ---- so the refresh path preserves a live session ----------------------- */
{
  const sessionView = view();
  sessionView.meta = Object.assign({}, meta({ session: SESSION, actors: ACTORS,
                                              live: true }));
  ck("before_refresh", sessionView.meta.live, true);
  refresh();                     // rebuilds meta from {session, actors} only
  ck("after_refresh", sessionView.meta.live, true);
}
/* ...and a parked one, which is the same rule in the other direction ------ */
{
  const sessionView = view();
  sessionView.meta = Object.assign({}, meta({ session: SESSION, actors: ACTORS,
                                              live: false }));
  refresh();
  ck("after_refresh_parked", sessionView.meta.live, false);
}

out.ok = out.errors.length === 0;
console.log(JSON.stringify(out, null, 1));
process.exit(out.ok ? 0 : 1);
