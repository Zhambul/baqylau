"use strict";
// tests/jsdom/globalstreamsequence.js — drives the REAL boot sequence
// (dashboard/static/app.02-router.js `connectGlobal`) and proves the ordering
// the page-load fetch-burst bug depended on: the global SSE stream must not
// open until `GET /sessionData` has answered, and it must open FROM the
// cursor that answer reports.
//
// The bug: the stream used to open at the same time as the list fetch, from
// cursor 0. Its first frame then carried the WHOLE backlog, and if it beat the
// list's reply, every session in it looked unknown to the client —
// `adoptStreamedSession` fired once per session, a burst of
// `GET /sessionData/<id>` for a page that had only one active session.
//
// Usage: node tests/jsdom/globalstreamsequence.js dashboard/static/app.02-router.js
// SKIPPED when `node` is absent, like every harness here (docs/testing.md).
const fs = require("fs");
const vm = require("vm");
const { domGlobals } = require("./domshim");

const out = { errors: [], fetches: [], eventSources: [] };
const ck = (name, got, want) => {
  out[name] = got === undefined ? null : JSON.parse(JSON.stringify(got));
  if (want !== undefined && JSON.stringify(got) !== JSON.stringify(want))
    out.errors.push(`${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
};

// The list fetch resolves only when the test says so — that gap is exactly
// where the burst used to happen (the stream opened during it, from 0).
let resolveList;
const listAnswered = new Promise(resolve => { resolveList = resolve; });

class FakeEventSource {
  constructor(url) {
    this.url = url;
    out.eventSources.push(url);
  }
  addEventListener() {}
}

const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number, Object, Array, Boolean,
  Promise, RegExp,
  setInterval: () => 1, clearInterval: () => {},
  setTimeout: () => 1, clearTimeout: () => {},
  performance: { now: () => 0 },
  ...domGlobals(),
  encodeURIComponent,
  location: { reload: () => { out.errors.push("unexpected location.reload"); } },
  fetch: (url) => {
    out.fetches.push(url);
    if (url === "/api/application")
      return Promise.resolve({ json: () => Promise.resolve({}) });
    if (url === "/sessionData")
      return listAnswered.then(body => ({ json: () => Promise.resolve(body) }));
    out.errors.push("unexpected fetch " + url);
    return Promise.reject(new Error("unexpected fetch " + url));
  },
  EventSource: FakeEventSource,
  $conn: { dataset: {} },
  LIMITS: {},
  // The neighbours a real boot wires in from other app.NN files — stubbed to
  // the smallest thing that keeps `connectGlobal`'s OWN sequencing honest,
  // which is the one thing under test here.
  sseMark: () => {},
  clog: () => {},
  failLoudly: (sessionId, code, detail) => out.errors.push(`failLoudly ${code} ${JSON.stringify(detail)}`),
  renderList: () => {},
  reconcileCloses: () => {},
  renderAttention: () => {},
  checkJump: () => {},
  renderAccounts: () => {},
  notifyOn: true,
  // The real applyCanonicalSessions (app.04-list.js) does more than this, but
  // "assign the rows to S.sessions" is the one part `loadSessionDataList`
  // depends on here.
  applyCanonicalSessions: (sessions) => { sandbox.S.sessions = sessions || []; },
  S: { sessions: [], usageRows: [], currentSessionId: null, esGlobal: null, boot: null },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
for (const file of process.argv.slice(2))
  vm.runInContext(fs.readFileSync(file, "utf8"), sandbox);

vm.runInContext("connectGlobal()", sandbox);

// Still waiting on the list: no stream yet, whatever else fired meanwhile.
ck("fetched_list_first", out.fetches.includes("/sessionData"), true);
ck("stream_before_list_answers", out.eventSources.length, 0);

resolveList({
  cursor: 4242,
  sessions: [{ session: { session_id: "s1" }, actors: [], live: true, repository: null }],
});

setImmediate(() => {
  setImmediate(() => {
    try {
      ck("stream_opened_after_list", out.eventSources.length, 1);
      ck("stream_opened_from_lists_cursor",
         out.eventSources[0] === "/sessionData/stream?after_cursor=4242", true);
      ck("list_rows_applied", (sandbox.S.sessions || []).map(row => row.session.session_id), ["s1"]);
    } catch (err) {
      out.errors.push(String(err));
    }

    out.ok = out.errors.length === 0;
    console.log(JSON.stringify(out, null, 1));
    process.exit(out.ok ? 0 : 1);
  });
});
