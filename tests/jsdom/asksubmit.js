// tests/jsdom/asksubmit.js — drives the REAL submitAsk (dashboard/static/
// app.07-dialogs.js) over the shared DOM shim and prints one JSON verdict
// object, which tests/test_l0_dash_dialogs.py asserts on.
//
// Why this exists: submitAsk decides what the ask card actually SENDS, and it
// can silently send less than you answered. It shipped a bug where one typed
// word discarded every option you had picked: the escalate-to-chat test was
// `askHasPreview(ask)` — true if ANY option ANYWHERE in the ask carried a
// preview — and a chat escalation sends no `answers` array at all. So a preview
// on question 1 plus typed text on question 4 threw away questions 1-3's real
// selections, and the tool saw "no answer provided". Four rounds of answers
// were lost to it (2026-07-26) before the audit rows (`web-answer chat:true`
// next to a 7-char `web-send`) gave it away.
//
// A grep cannot catch that: the bug is in which branch a compound condition
// takes and what the branch omits from the body. So this EXECUTES the function
// and reports the POST body for each interesting shape of answer.
//
// Usage: node tests/jsdom/asksubmit.js dashboard/static/app.07-dialogs.js
// SKIPPED when `node` is absent (docs/testing.md) — never a build requirement.
"use strict";
const fs = require("fs");
const vm = require("vm");
const { El, domGlobals } = require("./domshim");

const posts = [];

const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number, RegExp, Promise, Array,
  setTimeout: () => 1, clearTimeout: () => {},
  ...domGlobals(),
  encodeURIComponent,
  // the collaborators submitAsk calls that live in other SPA parts
  toast: () => {}, clog: () => {}, optPending: () => ({ live: true }),
  renderAsk: () => {}, autoGrow: () => {}, saveComposerDraft: () => {},
  askDraftClear: () => {}, closeAskMenu: () => {},
  canonicalControl: (controlName, fields) => {
    posts.push({ body: { control_name: controlName, ...fields } });
    return Promise.resolve({});
  },
  ASK_DRAFT_DEBOUNCE_MS: 400,
  CLIENT_ID: "test-client",
  IS_IPAD: false,
  $view: new El("div"),
  S: { currentSessionId: "sid1", sessionView: null },
};
sandbox.document.addEventListener = () => {};
sandbox.document.activeElement = null;
sandbox.window = sandbox;
sandbox.window.addEventListener = () => {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox,
                { filename: process.argv[2] });

/* One submitAsk run: returns the body it POSTed. */
function submit(ask, answers, chat) {
  posts.length = 0;
  sandbox.S.sessionView = { meta: {}, askEl: new El("div"), composer: new El("textarea") };
  sandbox.submitAsk(ask, answers, chat);
  return (posts[0] || {}).body || null;
}

const OPT = (label) => ({ label });
const PREV = (label) => ({ label, preview: "mock" });

// Q1 has previews, Q2 does not, Q3 is multiSelect without previews — the exact
// shape that lost answers: a preview on ONE question, typed text on ANOTHER.
const MIXED = {
  tool_use_id: "t1",
  questions: [
    { header: "Dock", options: [PREV("top"), PREV("bottom")] },
    { header: "Colour", options: [OPT("white"), OPT("state")] },
    { header: "Extras", multiSelect: true, options: [OPT("sessionId"), OPT("vmodes")] },
  ],
};
// every question preview-laid-out, and the typed text is on a preview question:
// escalation here is legitimate (the TUI dialog has no free-text row)
const ALL_PREVIEW = {
  tool_use_id: "t2",
  questions: [{ header: "Dock", options: [PREV("top"), PREV("bottom")] }],
};
const NO_PREVIEW = {
  tool_use_id: "t3",
  questions: [{ header: "Colour", options: [OPT("white"), OPT("state")] }],
};

const A = (selected, other) => ({ selected: selected || [], other: other || "" });

const out = {
  // THE REGRESSION: picks on every question, plus typed text on the NON-preview
  // multiSelect. Must submit answers — the preview on Q1 is none of Q3's business.
  typed_on_plain_question: submit(MIXED,
    [A(["top"]), A(["white"]), A(["sessionId"], "testing")]),
  // picks only, mixed ask: plainly a normal answer submission
  picks_only: submit(MIXED, [A(["top"]), A(["white"]), A(["sessionId"])]),
  // typed text ON the preview question: escalation is correct, but the message
  // must still carry the OTHER questions' picks rather than dropping them
  typed_on_preview_question: submit(MIXED,
    [A([], "my own dock"), A(["white"]), A(["sessionId", "vmodes"])]),
  // single question, all preview, typed: escalates, message is the typed text
  all_preview_typed: submit(ALL_PREVIEW, [A([], "custom")]),
  // no previews anywhere: typed text has always ridden as `other`
  no_preview_typed: submit(NO_PREVIEW, [A([], "freeform")]),
  // a single-select option WINS over dormant typed text (other:"" is sent)
  option_beats_dormant_text: submit(NO_PREVIEW, [A(["white"], "leftover")]),
  // explicit "chat about this" is untouched: no answers, no message
  explicit_chat: submit(MIXED, null, true),
};
process.stdout.write(JSON.stringify(out, null, 1));
