// tests/jsdom/composergate.js — runs the REAL buildComposer()
// (dashboard/static/app.08-composer.js) over the states a session can be in and
// prints one JSON verdict object, which tests/test_dashboard_dom.py asserts on.
//
// Why this exists: "can I type to this session right now" is a boolean the page
// derives, and it shipped false for EVERY live session — the gate also required
// meta.terminal_window_id, an id the canonical wire deliberately never serves
// (`live` already means "a terminal window is attached"). The box was dead with
// "no terminal window — can't message a headless session", and no grep and no
// Python test can see that: it is one boolean, read off a meta the browser
// assembles from two different sources.
//
// So the subject here is exactly the gate — the textarea's disabled flag, the
// send button's label and the placeholder — over LIVE / PARKED / capability-off,
// with NO window id anywhere in the fixtures, which is the shape the server
// actually sends.
//
// Usage: node tests/jsdom/composergate.js dashboard/static/app.07-dialogs.js \
//                                          dashboard/static/app.08-composer.js
// SKIPPED when `node` is absent — it is never a build requirement
// (docs/testing.md).
"use strict";
const fs = require("fs");
const vm = require("vm");

const { El, domGlobals } = require("./domshim");

const out = { errors: [] };
const ck = (name, got, want) => {
  out[name] = got === undefined ? null : JSON.parse(JSON.stringify(got));
  if (want !== undefined && JSON.stringify(got) !== JSON.stringify(want))
    out.errors.push(`${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
};

const sandbox = {
  console, Math, JSON, Date, Object, Array, Number, String, Boolean,
  Promise, Set, Map, RegExp, ArrayBuffer, Float32Array, Int16Array,
  encodeURIComponent, decodeURIComponent, parseInt, isNaN,
  setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: () => {},
  performance: { now: () => Date.now() },
  Event: class { constructor(t) { this.type = t; } },
  Blob: class { constructor(parts) { this.parts = parts; } },
  URL: { createObjectURL: () => "blob:x" },
  navigator: { mediaDevices: { getUserMedia: () => new Promise(() => {}) } },
  addEventListener: () => {},
  fetch: () => new Promise(() => {}),
  toast: () => {},
  clog: () => {},
  postJSON: () => new Promise(() => {}),
  IS_IPAD: false,
  // the session list the composer reads its busy label from — empty, so the
  // label comes from meta.tab
  S: { currentSessionId: "s1", sessions: [], sessionView: null },
  // collaborators that are not the subject: the "/" menu, the command catalog
  // and the capability map each have their own coverage. app.00-core.js is not
  // loaded (it reads the whole page out of the document at load time), so the
  // handful of core helpers the composer calls are shimmed, as in the other
  // harnesses here.
  slashMenu: () => ({ key: () => false }),
  cmdsFor: () => [],
  sessionId: (row) => row.session.session_id,
  sessionTabState: () => "",
  QUEUE_TABS: ["thinking", "working", "executing"],
  capOk: (meta, key) => !(meta.caps_off || []).includes(key),
  frag: (...kids) => {
    const f = new El("#frag");
    kids.forEach(kid => kid && f.append(kid));
    return f;
  },
};
Object.assign(sandbox, domGlobals());
sandbox.document.createElementNS = () => ({ setAttribute() {}, append() {} });
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of process.argv.slice(2))
  vm.runInContext(fs.readFileSync(f, "utf8"), sandbox);
// dictAvailable() probes the server; the mic's visibility is not the subject
vm.runInContext("dictProbe = Promise.resolve(false);", sandbox);

/* Build the composer for one meta and report what the user can do with it. */
function composer(meta) {
  const sessionView = { meta, shells: new Map(), cmds: {} };
  sandbox.S.sessionView = sessionView;
  const wrap = vm.runInContext("buildComposer", sandbox)();
  const ta = wrap.children.find(child => child.tag === "textarea");
  const btn = wrap.children.find(child => child.className === "csend");
  return {
    typable: !ta.disabled,
    sendable: !btn.disabled,
    label: btn._text,
    placeholder: ta.placeholder,
  };
}

// The wire's shape: liveness, no window id. A LIVE session is one with a
// terminal window attached — that is what the server resolved `live` against.
const LIVE = { live: true, workingDirectory: "/w", harness: "claude_code" };

ck("live", composer(LIVE), {
  typable: true,
  sendable: true,
  label: "send",
  placeholder: "message this session…  (Enter to send · Shift+Enter for newline)",
});
ck("parked", composer({ ...LIVE, live: false }), {
  typable: true,
  sendable: true,
  label: "resume & send",
  placeholder: "message this parked session — sending resumes it  "
    + "(Enter to resume & send)",
});
// LIVENESS NOT YET RESOLVED (a cold open, before /sessionData answers): the box
// is disabled and there is NO resume button. `resume & send` relaunches the
// session, so offering it on uncertainty would restart a session that was
// running the whole time — the guard is KNOWN-parked, not "not known live".
ck("liveness_unknown", composer({ ...LIVE, live: undefined }), {
  typable: false,
  sendable: false,
  label: "send",
  placeholder: "session is not live",
});
// no directory to resume in: nothing left to offer
ck("parked_nowhere", composer({ ...LIVE, live: false, workingDirectory: "" }), {
  typable: false,
  sendable: false,
  label: "send",
  placeholder: "session is not live",
});
// a host whose `send` gesture is inert takes no message at all
ck("send_capability_off", composer({ ...LIVE, caps_off: ["send"] }), {
  typable: false,
  sendable: false,
  label: "send",
  placeholder: "this session's tool can't be messaged from here",
});

out.ok = out.errors.length === 0;
console.log(JSON.stringify(out, null, 1));
process.exit(out.ok ? 0 : 1);
