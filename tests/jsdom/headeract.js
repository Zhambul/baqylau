// tests/jsdom/headeract.js — drives the REAL header action bar
// (mountHeaderActions/chromeActions/chromeQuickCmds/gate in
// dashboard/static/app.11-chrome.js) over the shared DOM shim, and prints one
// JSON verdict object that test_l0_dashboard.py asserts on.
//
// Why this exists: the bar's whole point is that a button which doesn't apply
// GREYS instead of vanishing (docs/dashboard.md *Header action bar*), so "is it
// clickable right now" is the feature. That answer is a matrix — live vs parked
// × idle / running / a modal dialog waiting × how much conversation there is —
// computed by three closures (`stopMode`, `quickMode`, the static gates) that a
// grep can only see the source of, never the result. Every one of the states
// below has been shipped wrong at least once: a stop that greys while a turn
// runs, a rewind that invites a click the server refuses, a compact that types
// a command Claude Code bounces with "not enough messages".
//
// The REAL app.10-control.js is loaded alongside, so BUSY_TABS and liveTab() are
// the page's own — the gates are exactly as honest as their real inputs.
//
// Usage: node tests/jsdom/headeract.js dashboard/static/app.10-control.js \
//                                      dashboard/static/app.11-chrome.js
// SKIPPED when `node` is absent (docs/testing.md) — never a build requirement.
"use strict";
const fs = require("fs");
const vm = require("vm");
const { El, domGlobals } = require("./domshim");

const sessact = new El("div");

const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number, RegExp, Promise,
  setTimeout: () => 1, clearTimeout: () => {},
  setInterval: () => 1, clearInterval: () => {},
  encodeURIComponent, decodeURIComponent,
  ...domGlobals(),
  location: { hash: "" },
  navigator: { userAgent: "node" },
  fetch: () => new Promise(() => {}),
  postJSON: () => new Promise(() => {}),
  toast: () => {}, clog: () => {}, clientFail: () => {},
  closeBegin: () => {}, closeSettle: () => {},
  closeSession: () => new Promise(() => {}),
  openNewSession: () => {}, sendQuickCmd: () => {},
  // the two-step confirm (app.00-core) only arms on CLICK — at build time it
  // just claims the handler, which is all this harness needs standing in
  armConfirm: (btn) => { btn.onclick = () => {}; },
  openQuickMenu: () => {}, setModelBtn: () => {}, setEffortBtn: () => {},
  curModelFamily: () => "", MODEL_CHOICES: [], EFFORT_CHOICES: [],
  shortModel: (m) => m || "",
  agentStatus: () => ["", ""], renderAgentScoreboard: () => {},
  setBadge: () => {}, setGitChip: () => {}, shortSid: (s) => s,
  proj: () => "p", copySid: () => {}, agentCrumbs: () => new El("div"),
  chipAdder: () => () => {}, sigmaChip: () => {}, paintCtxRow: () => {},
  dur: () => "", usd: () => "", kfmt: (n) => String(n), ago: () => "",
  LIMITS: { rename_max: 60 },
  IS_IPAD: false,
  $view: new El("div"), $sessact: sessact, $modal: new El("div"),
  S: { cur: "sid1", ses: null, sessions: [], closing: new Set(), closePend: {} },
};
sandbox.document.addEventListener = () => {};
sandbox.document.body = new El("body");
sandbox.window = sandbox;
sandbox.window.addEventListener = () => {};
vm.createContext(sandbox);
for (const src of process.argv.slice(2))
  vm.runInContext(fs.readFileSync(src, "utf8"), sandbox, { filename: src });

/* Mount the bar for one (meta, tab) and report every button's reachability —
   what the user sees: its label, whether it can be clicked, and the tooltip that
   has to explain a greyed one. */
function bar(meta, tab) {
  const ses = { meta: meta, agentFocus: null, agents: [] };
  sandbox.S.ses = ses;
  sandbox.S.sessions = [{ sid: "sid1", tab: tab }];
  sandbox.mountHeaderActions(ses, meta);
  const out = {};
  for (const btn of sessact.querySelectorAll(".sstop")
                          .concat(sessact.querySelectorAll(".sresume"))) {
    const label = btn.textContent.trim();
    out[label] = { disabled: !!btn.disabled, title: btn.title || "" };
  }
  return out;
}

/* The ✦ auto button INSIDE the inline-rename input (startRenameHeader,
   app.10-control.js): bare /rename over the quick-command channel, so it
   carries the same terminal-typing gate as the quick commands — but it is
   built on demand when ✎ rename opens the input, not by the bar mount, so it
   is driven separately here. */
function autoRename(meta, tab) {
  const ses = { meta: meta, projEl: new El("span"), agentFocus: null,
                agents: [] };
  ses.projEl.textContent = "old title";
  sandbox.S.ses = ses;
  sandbox.S.sessions = [{ sid: "sid1", tab: tab }];
  sandbox.startRenameHeader();
  const btn = ses.projEl.querySelector(".renameauto");
  return { present: !!btn, disabled: !!(btn && btn.disabled),
           title: (btn && btn.title) || "" };
}

const LIVE = { live: true, kitty_window_id: "7", cwd: "/w", prompts: 9 };
const out = {
  idle: bar(LIVE, ""),
  running: bar(LIVE, "working"),
  asking: bar(LIVE, "awaiting-command"),
  // one prompt in: Claude Code refuses to compact a conversation this short
  fresh: bar({ ...LIVE, prompts: 1 }, ""),
  // no count to be had (a transcript no parser speaks) — never a reason to grey
  unknown: bar({ ...LIVE, prompts: null }, ""),
  parked: bar({ live: false, cwd: "/w", prompts: 9 }, ""),
  // ✦ auto (inline rename): clickable live, greyed with the reason when a
  // dialog is up (pasted text would land IN it) or the session is parked
  autorename: {
    idle: autoRename(LIVE, ""),
    asking: autoRename(LIVE, "awaiting-command"),
    parked: autoRename({ live: false, cwd: "/w", prompts: 9 }, ""),
  },
  // the bar is emptied when you leave the session view
  cleared: (() => { sandbox.clearHeaderActions();
                    return { n: sessact.childElementCount,
                             hidden: !!sessact.hidden }; })(),
};
process.stdout.write(JSON.stringify(out, null, 1));
