// tests/jsdom/newsession.js — drives the REAL new-session form
// (dashboard/static/app.09-newsession.js) over the shared DOM shim and prints
// one JSON verdict object, which tests/test_l0_dash_viewmode.py asserts on.
//
// Why this exists: openNewSession builds the whole modal — seven field rows,
// their cross-wiring, the draft machinery, the launch — and it is now split
// into named PHASES that hand each other a context object `F` instead of
// sharing one 344-line closure scope. A grep cannot tell you whether a phase
// still sees every name it reads: in a single scope every local was reachable
// from every closure, and after the split a missed hand-off is a ReferenceError
// that only fires when a user opens the form. `node --check` won't see it
// either — it is scope, not syntax.
//
// So this executes it: build the form, then drive the two gestures that reach
// ACROSS phases (the fresh/resume toggle, which the pickers phase calls back
// into; and the launch, which reads from five earlier phases). Anything a phase
// forgot to publish throws here.
//
// Usage: node tests/jsdom/newsession.js dashboard/static/app.09-newsession.js
// SKIPPED when `node` is absent — it is never a build requirement
// (docs/testing.md).
"use strict";
const fs = require("fs");
const vm = require("vm");

const { El, domGlobals } = require("./domshim");

const posted = [];
const modal = new El("div");

/* The app globals app.09 calls that live in OTHER parts (each shimmed to the
   smallest thing that keeps the form honest), plus the browser surface. */
const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number, Object, Array, Boolean,
  Promise, RegExp, encodeURIComponent, decodeURIComponent, parseInt, isNaN,
  setTimeout: (f) => { return 1; }, clearTimeout: () => {},
  setInterval: () => 1, clearInterval: () => {},
  performance: { now: () => 0 },
  // never settles: the harness verifies the PHASE HAND-OFFS, not the picker's
  // or the draft's network paths, and a stub response would only exercise how
  // well the stub was guessed. A pending promise keeps nothing alive.
  fetch: () => new Promise(() => {}),
  location: { hash: "" },
  navigator: { userAgent: "node", onLine: true },
  ...domGlobals(),
  // -- page state + helpers from the other parts --
  S: { sessions: [], accts: null, nsPrefs: {}, nsDrafts: {}, cur: null,
       ses: null, jump: null, pendingUI: false },
  IS_IPAD: false,
  $modal: modal,
  $newbtn: new El("button"), $statsbtn: new El("button"),
  $notifytoggle: new El("button"), $view: new El("div"),
  clog: () => {}, toast: () => {}, route: () => {},
  postJSON: (url, body) => { posted.push({ url, body }); return Promise.resolve({}); },
  autoGrow: () => {}, dictation: () => ({ btn: new El("button"), stop() {} }),
  attachTray: () => ({ strip: new El("div"), pending: () => false, paths: () => [] }),
  wireAttach: () => new El("button"),
  slashMenu: () => ({ key: () => false }),
  cmdsFor: () => [], groupKey: (r) => r.cwd || "",
  shortModel: (m) => m, kfmt: (n) => String(n), usd: (c) => String(c),
  ago: () => "", dur: () => "", proj: () => "", shortSid: (s) => s,
  armConfirm: () => {}, lastActive: () => 0,
  acctPill: () => new El("span"), limitPct: () => 0,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.document.body = new El("body");
sandbox.document.getElementById = () => new El("div");
sandbox.document.addEventListener = () => {};
sandbox.document.documentElement = new El("html");

vm.createContext(sandbox);
const src = fs.readFileSync(process.argv[2], "utf8");
vm.runInContext(src, sandbox, { filename: "app.09-newsession.js" });

const out = { ok: true, errors: [] };
function step(name, fn) {
  try { fn(); } catch (e) { out.ok = false; out.errors.push(name + ": " + e.message); }
}

// 1. build the form — every phase runs, every hand-off is exercised
step("open", () => sandbox.openNewSession("/tmp/proj"));

// what the phases must have produced, by class, anywhere under the modal
const has = (cls) => modal.querySelectorAll(cls).length;
out.rows = has(".nsfield");
out.panel = has(".nspanel");
out.prompt = has(".nsprompt");
out.actions = has(".nsactions");

// 2. the toggle: nsConversation's syncFresh, CALLED FROM nsPickers' phase and
//    from the checkbox — it reads the picker and the resume row, both published
//    by an earlier phase.
step("toggle", () => {
  const box = modal.querySelectorAll(".nsswitch")[0];
  const input = box && box.children.find((c) => c.tag === "input");
  if (!input) throw new Error("no fresh toggle built");
  input.checked = false;
  if (input.onchange) input.onchange();
  input.checked = true;
  if (input.onchange) input.onchange();
});

// 3. the launch: nsActions' go(), which reads dir/fresh/picker/prompt/pdic/
//    nsTray/acct/model/effort — five earlier phases at once.
step("launch", () => {
  const btns = modal.querySelectorAll(".nsbtn");
  const submit = btns.find((b) => b._cls().includes("primary"));
  if (!submit) throw new Error("no launch button built");
  submit.onclick();
});
out.posted = posted.map((p) => p.url);
out.launch_cwd = (posted[0] && posted[0].body && posted[0].body.cwd) || "";

console.log(JSON.stringify(out));
