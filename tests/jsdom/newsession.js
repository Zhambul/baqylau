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
// It is ALSO the pin on where the form's vocabulary comes from: every model,
// effort level, default and account-row decision is read out of the fake
// /api/hosts payload below, so a THIRD host gets its own menus (or empty ones)
// instead of the default host's — the failure the deleted host-name-keyed
// tables made structural.
//
// Usage: node tests/jsdom/newsession.js dashboard/static/app.10-control.js \
//                                       dashboard/static/app.09-newsession.js
// SKIPPED when `node` is absent — it is never a build requirement
// (docs/testing.md).
"use strict";
const fs = require("fs");
const vm = require("vm");

const { El, domGlobals } = require("./domshim");

const posted = [];
const clientEvents = [];
const toasts = [];
const cmdAsks = [];          // every "/" menu vocabulary request the box made
let slashSrc = null;         // the first-prompt box's own "/" source callback
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
  // THE /api/hosts PAYLOAD, as the server serves it (plugins.hosts): every
  // menu, default, flag and match rule the form is built from now comes from
  // here, and the form holds no per-host table of its own. The form's own
  // /api/hosts fetch never settles (fetch stub), so this cache IS the
  // vocabulary — which is the point: swap a row and the form changes.
  //
  // Three LAUNCHABLE hosts: the two real ones (verbatim values, so this doubles
  // as the "nothing moved for either" pin) plus an UNKNOWN third, whose whole
  // vocabulary is its own — different models, different levels, its own
  // defaults, no account switcher. The old form would have handed it Claude
  // Code's four models, Claude's five levels and `fable`/`high`
  // (`toolOpts = (tbl, t) => tbl[t] || tbl.claude_code`).
  // one SWITCHABLE account row, so "is the account picker offered" is decided
  // by the picked host's `accounts` flag rather than by there being no accounts
  S: { sessions: [], nsPrefs: {}, nsDrafts: {}, currentSessionId: null,
       usageRows: [{ harness: "claude_code", account_id: "c1",
                     display_name: "one", switchable: true, windows: [],
                     scheduling_score: "0", scheduling_allowed: true,
                     limit: null, authentication_error: null }],
       sessionView: null, jump: null, pendingUI: false,
       hosts: [
         { name: "claude_code", label: "Claude Code", launchable: true,
           default: true, accounts: true, attach: true,
           model_choices: ["fable", "opus", "sonnet", "haiku"],
           effort_choices: ["low", "medium", "high", "xhigh", "max"],
           model_default: "fable", effort_default: "high" },
         { name: "codex", label: "Codex", launchable: true,
           default: false, accounts: false, attach: false,
           model_choices: ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.5"],
           effort_choices: ["low", "medium", "high", "xhigh", "max", "ultra"],
           model_default: "gpt-5.6-sol", effort_default: "low" },
         { name: "opencode", label: "OpenCode", launchable: true,
           default: false, accounts: false, attach: false,
           model_choices: ["oc-large", "oc-small"],
           effort_choices: [], model_default: "oc-small",
           effort_default: "" },
       ] },
  IS_IPAD: false,
  $modal: modal,
  $newbtn: new El("button"), $statsbtn: new El("button"),
  $notifytoggle: new El("button"), $view: new El("div"),
  clog: (sessionId, name) => clientEvents.push({ sessionId, name }),
  toast: (kind, title) => toasts.push({ kind, title }), route: () => {},
  // the launch now arms the jump watch + tears the form down SYNCHRONOUSLY, on
  // the click (the waiting room must not wait for the POST — docs/dashboard.md
  // *The pending view*), so go() reaches these two other-part names before it
  // ever posts; they were unreachable while all of that lived in the .then
  JUMP_TIMEOUT_MS: 120000, stopDictation: () => {},
  // the snapshot taken DURING the request is the point: a launch must have
  // already armed the jump watch and entered the waiting room by the time it
  // posts, since that is the only window in which the user is staring at
  // something (docs/dashboard.md *The pending view*)
  postJSON: (url, body) => {
    posted.push({ url, body, armed: !!sandbox.S.jump,
                  hash: sandbox.location.hash });
    return Promise.resolve({});
  },
  autoGrow: () => {}, dictation: () => ({ btn: new El("button"), stop() {} }),
  attachTray: () => ({ strip: new El("div"), pending: () => false, paths: () => [] }),
  wireAttach: () => new El("button"),
  // keep the box's own vocabulary CALLBACK so the harness can ask what the "/"
  // menu would fetch at any moment (the real slashMenu calls it on "/")
  slashMenu: (box, host, src) => { slashSrc = src; return { key: () => false }; },
  // the "/" menu's fetch, recorded: the new-session box must ask for the
  // vocabulary of the TOOL the picker is on (bug 13 — it used to pass neither a
  // sessionId nor a tool, so the server answered with the default host's commands)
  cmdsFor: (workingDirectory, cache, key, sessionId, tool) => {
    cmdAsks.push({ workingDirectory, key, sessionId: sessionId || "", tool: tool || "" });
    return [];
  },
  groupKey: (r) => r.workingDirectory || "",
  sessionId: (row) => row.session.session_id,
  sessionWorkingDirectory: (row) => row.session.working_directory || "",
  sessionIsLive: (row) => !!row.terminal.window_id,
  sessionWindowId: (row) => row.terminal.window_id || "",
  // app.00-core's shortModel is a PASS-THROUGH now (the server serves each id
  // in its owning host's spelling), so the stub is the real function
  shortModel: (m) => String(m || "").trim(),
  kfmt: (n) => String(n), usd: (c) => String(c),
  ago: () => "", dur: () => "", proj: () => "", shortSid: (s) => s,
  armConfirm: () => {}, lastActive: () => 0,
  acctPill: () => new El("span"), limitPct: () => 0,
  // the usage-strip vocabulary fillAccts words its option rows with (server
  // -supplied everywhere real) — stubbed to nothing: what this harness asks of
  // the account row is WHETHER it shows, which is the host's `accounts` flag
  usageWindows: () => [], windowLabel: (k) => k, limitLabel: () => "",
  schedScore: () => 0,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.document.body = new El("body");
sandbox.document.getElementById = () => new El("div");
sandbox.document.addEventListener = () => {};
sandbox.document.documentElement = new El("html");

vm.createContext(sandbox);
for (const src of process.argv.slice(2))
  vm.runInContext(fs.readFileSync(src, "utf8"), sandbox, { filename: src });

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

// 3. the TOOL picker (nsPickers): the row is present + visible (two launchable
//    hosts), defaults to claude_code, and picking codex flows through the
//    dropdown's onpick → syncTool (which re-fills model/effort + hides the
//    account row). Reaches nsPickers' tool/syncTool/fillTools hand-offs.
step("tool", () => {
  const toolRow = modal.querySelectorAll(".nstoolrow")[0];
  if (!toolRow) throw new Error("no tool row built");
  out.tool_visible = toolRow.style.display !== "none";
  const lab = toolRow.querySelectorAll(".nsdroplab")[0];
  out.tool_default = lab ? lab.textContent : "";
  const btn = toolRow.querySelectorAll(".nsdropbtn")[0];
  if (!btn || !btn.onclick) throw new Error("no tool dropdown button");
  btn.onclick();                            // open the menu → paints its options
  const items = toolRow.querySelectorAll(".nsdropitem");
  const codex = items.find((i) => i.textContent.indexOf("Codex") >= 0);
  if (!codex) throw new Error("no codex option");
  codex.onclick({ preventDefault() {} });   // pick codex → tool.onpick → syncTool
  out.tool_after = lab ? lab.textContent : "";
  // syncTool repaints the first-prompt placeholder to name the picked host — it
  // must say "Codex", never the hardcoded "Claude" (the de-Claude fix)
  const pbox = modal.querySelectorAll(".nsprompt")[0];
  out.prompt_placeholder = pbox ? pbox.placeholder : "";
});

/* The two option pickers, read off the DOM: which rows the menu offers and
   which one is selected. dropdown() paints its rows on the first open and
   leaves them in the (hidden) menu, so one click is enough. */
function fieldByLabel(name) {
  return modal.querySelectorAll(".nsfield").find(
    (f) => ((f.querySelectorAll(".nslabel")[0] || {}).textContent === name));
}
function pickerState(name) {
  const f = fieldByLabel(name);
  if (!f) throw new Error("no " + name + " field");
  const btn = f.querySelectorAll(".nsdropbtn")[0];
  if (btn && btn.onclick) btn.onclick();          // open → paint the rows
  const opts = f.querySelectorAll(".nsdropitem").map((i) => i.textContent);
  if (btn && btn.onclick) btn.onclick();          // …and close again
  return { opts, cur: (f.querySelectorAll(".nsdroplab")[0] || {}).textContent };
}
function pickTool(label) {
  const toolRow = modal.querySelectorAll(".nstoolrow")[0];
  const btn = toolRow.querySelectorAll(".nsdropbtn")[0];
  btn.onclick();
  const row = toolRow.querySelectorAll(".nsdropitem")
                     .find((i) => i.textContent === label);
  if (!row) throw new Error("no " + label + " option");
  row.onclick({ preventDefault() {} });
}
function toolShape() {
  const acct = fieldByLabel("account");
  // …and what the "/" menu would ask the server for RIGHT NOW: slashMenu's
  // source callback is the box's own, so calling it is exactly what typing "/"
  // does (bug 13 — it used to name neither a sessionId nor a tool, so the server
  // answered every new-session menu with the DEFAULT host's commands).
  cmdAsks.length = 0;
  if (slashSrc) slashSrc();
  return { model: pickerState("model"), effort: pickerState("effort"),
           account_row: !!acct && acct.style.display !== "none",
           placeholder: (modal.querySelectorAll(".nsprompt")[0] || {}).placeholder,
           slash: cmdAsks[0] || null };
}

// 3b. every menu the form shows is THAT host's own: codex is on screen from the
//     step above, then the UNKNOWN third host (its own models, NO effort levels
//     at all, no account row), then back to the default. The old tables would
//     have answered all three with Claude Code's.
step("vocabulary", () => {
  out.codex = toolShape();
  pickTool("OpenCode");
  out.third = toolShape();
  pickTool("Claude Code");
  out.claude = toolShape();
  pickTool("Codex");        // …and back, so the launch below is codex's (the
  //                           tool switch must also be REPEATABLE: each pick
  //                           re-fills both menus from that host's own lists)
});

// 4. the launch: nsActions' go(), which reads dir/fresh/picker/prompt/pdic/
//    nsTray/acct/model/effort/tool — six earlier phases at once.
step("launch", () => {
  const btns = modal.querySelectorAll(".nsbtn");
  const submit = btns.find((b) => b._cls().includes("primary"));
  if (!submit) throw new Error("no launch button built");
  submit.onclick();
});
out.posted = posted.map((p) => p.url);
const launch = posted.find((p) => p.url === "/api/sessions") || {};
out.launch_cwd = (launch.body && launch.body.working_directory) || "";
// the tool routed to the launch body — "codex" after the tool step's switch, and
// (codex has no switcher) no account rides along
out.launch_tool = (launch.body && launch.body.harness) || "";
out.launch_account = (launch.body && launch.body.account_id) || "";
// codex's model/effort defaults must be EXPLICIT + supported — never the empty
// "codex default" pseudo-option, never the ChatGPT-unsupported gpt-5-codex
out.launch_model = (launch.body && launch.body.model_id) || "";
out.launch_effort = (launch.body && launch.body.effort) || "";
// the optimistic hand-off, as it stood WHILE the launch request was open
out.launch_armed = !!launch.armed;
out.launch_hash = launch.hash || "";

// A process can create its canonical session and exit before a live terminal
// snapshot reaches the browser. That is still a resolved launch, not two
// minutes of "starting session" followed by "never appeared".
step("ended launch", () => {
  sandbox.S.sessions = [{
    session: { session_id: "ended-session", working_directory: "/tmp/proj",
               title: null, state: "finished" },
    terminal: { window_id: null },
  }];
  sandbox.checkJump();
});
out.ended_launch_hash = sandbox.location.hash;
out.ended_launch_event = clientEvents.find((event) => event.name === "launch.ended") || null;
out.ended_launch_toast = toasts.find((toast) => toast.title.indexOf("exited") >= 0) || null;

console.log(JSON.stringify(out));
