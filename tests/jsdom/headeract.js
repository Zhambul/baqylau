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
  // NB openQuickMenu / setModelBtn / setEffortBtn / curModelFamily are the REAL
  // ones — app.10-control.js is loaded below and its declarations win. Only
  // app.00-core's shortModel is stubbed, as the PASS-THROUGH it now is (the
  // server serves every id in its owning host's spelling).
  shortModel: (m) => String(m || "").trim(),
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

/* The ✦ model button's LABEL and which menu row it marks as current — both
   read off the served vocabulary now: the display spelling of the running model
   (ctx.model_short, the owning host's own, since the page no longer shortens an
   id) reduced to a menu row by that host's declared match rule
   (meta.model_match). Driven separately from bar() because a session with a
   known model relabels the button, which the row-order matrix keys on. */
function modelBtn(meta, pending) {
  const ses = { meta: meta, agentFocus: null, agents: [] };
  sandbox.S.ses = ses;
  sandbox.S.sessions = [{ sid: "sid1", tab: "" }];
  if (pending) ses.pendingModel = pending;
  sandbox.mountHeaderActions(ses, meta);
  const b = sessact.querySelectorAll(".sstop")
                   .find((x) => x.textContent.indexOf("✦") === 0);
  return { label: b ? b.textContent.trim() : "", cur: sandbox.curModelFamily(),
           pending: ses.pendingModel || "" };
}

// The DEFAULT host's session, as the server serves it: its quick commands with
// their measured refusal floors (compact needs 2 of your prompts, the argless
// rename 1) and its rewind menu. The page holds neither table any more.
const CC_CMDS = [{ cmd: "compact", min_prompts: 2 }, { cmd: "model" },
                 { cmd: "effort" }, { cmd: "rename", min_prompts: 1 }];
const CC_RW = [{ mode: "both", label: "restore code and conversation" },
               { mode: "conversation", label: "restore conversation" },
               { mode: "code", label: "restore code" }];
const LIVE = { live: true, kitty_window_id: "7", cwd: "/w", prompts: 9,
               quick_commands: CC_CMDS, rewind_modes: CC_RW,
               model_match: "family" };
// …and a session owned by a DIFFERENT host: it compacts and switches model or
// effort, but it cannot name itself (no `rename` row — its autoname gesture is
// inert) and cannot rewind (the cap), and it declares no refusal floors at all.
const OTHER = { ...LIVE, model_match: "exact", rewind_modes: [],
                caps: { interrupt: true, send: true, rename: true,
                        rewind: false, migrate: false, compact: true,
                        model: true, effort: true, ask: true, plan: true },
                quick_commands: [{ cmd: "compact" }, { cmd: "model" },
                                 { cmd: "effort" }] };
const out = {
  idle: bar(LIVE, ""),
  running: bar(LIVE, "working"),
  asking: bar(LIVE, "awaiting-command"),
  // one prompt in: the host's own compact floor (2) is not met
  fresh: bar({ ...LIVE, prompts: 1 }, ""),
  // no count to be had (a transcript no parser speaks) — never a reason to grey
  unknown: bar({ ...LIVE, prompts: null }, ""),
  parked: bar({ live: false, cwd: "/w", prompts: 9,
                quick_commands: CC_CMDS, rewind_modes: CC_RW }, ""),
  // a parked row with no directory recorded: ↻ resume has nowhere to go, so it
  // GREYS with the reason instead of not being built (the bar's own rule)
  nodir: bar({ live: false, cwd: "", prompts: 9,
               quick_commands: CC_CMDS }, ""),
  // the other host, at ONE prompt: its ⊜ compact stays reachable (it declares
  // no floor — Claude Code's 2 was a client constant applied to everyone) while
  // ↶ rewind greys on the cap
  other: bar({ ...OTHER, prompts: 1 }, ""),
  // ✦ auto (inline rename): clickable live, greyed with the reason when a
  // dialog is up (pasted text would land IN it) or the session is parked
  autorename: {
    idle: autoRename(LIVE, ""),
    asking: autoRename(LIVE, "awaiting-command"),
    parked: autoRename({ live: false, cwd: "/w", prompts: 9,
                         quick_commands: CC_CMDS }, ""),
    // an EMPTY conversation: bare /rename bounces ("no conversation context
    // yet"), so the button says so; an unknown count never greys (⊜'s rule)
    empty: autoRename({ ...LIVE, prompts: 0 }, ""),
    unknown: autoRename({ ...LIVE, prompts: null }, ""),
    // a host that renames but cannot NAME ITSELF (no `rename` quick command):
    // the button greys as unsupported instead of firing a 409
    other: autoRename(OTHER, ""),
  },
  // WHICH MENU ROW the session is on — the host's own match rule over the
  // server's display spelling of the running model. Both hosts pinned: a family
  // rule reduces `opus-4.8` to the `opus` row, an exact one keeps the whole id
  // (and a model its menu does not offer, `gpt-5.4-codex`, matches NOTHING
  // rather than being filed under `gpt-5.4` by a family compare).
  model: {
    claude: modelBtn({ ...LIVE, ctx: { model: "claude-opus-4-8",
                                       model_short: "opus-4.8" } }),
    other: modelBtn({ ...OTHER, ctx: { model: "gpt-5.6-terra",
                                       model_short: "gpt-5.6-terra" } }),
    unlisted: modelBtn({ ...OTHER, ctx: { model: "gpt-5.4-codex",
                                          model_short: "gpt-5.4-codex" } }),
    // a just-clicked switch shows optimistically until the probe CONFIRMS it —
    // by the host's rule, so Claude's `opus` row clears against a running
    // `opus-4.8` (an equality compare never would) and a stale one holds
    pending_confirmed: modelBtn({ ...LIVE, ctx: { model: "claude-opus-4-8",
                                                  model_short: "opus-4.8" } },
                                "opus"),
    pending_waiting: modelBtn({ ...LIVE, ctx: { model: "claude-opus-4-8",
                                                model_short: "opus-4.8" } },
                              "sonnet"),
  },
  // the bar is emptied when you leave the session view
  cleared: (() => { sandbox.clearHeaderActions();
                    return { n: sessact.childElementCount,
                             hidden: !!sessact.hidden }; })(),
};
process.stdout.write(JSON.stringify(out, null, 1));
