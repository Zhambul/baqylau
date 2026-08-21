// tests/jsdom/accounts.js — drives the REAL accounts strip renderer
// (dashboard/static/app.01-attention.js `renderAccounts`/`acctPill`) over the
// shared DOM shim and prints one JSON verdict object, which
// tests/test_dashboard_dom.py asserts on.
//
// Why this exists: the strip is READ AS A STACK — c1's 5h bar directly above
// c2's, the two "resets in …" tails in one line. That is a property of the two rows TOGETHER, and nothing in a
// single row's source states it: the misalignments all came from a row that
// legitimately had LESS to say (a rolled-over window whose reset the server
// dropped, an account the model-window fetch never matched, the ⚠ badge only
// the dead account carries) rendering fewer boxes than its neighbour. A grep
// can't compare two rows; only building both can. So this renders real-shaped
// payloads and compares the rows' STRUCTURE cell by cell.
//
// It also measures the widest `resetAgo()` output, which is the number the
// fixed reset column in style.css is sized from (17ch) — the "one account has
// days left, the other minutes, and they still don't line up" bug was that
// column being one character too narrow for the hours form.
//
// Usage: node tests/jsdom/accounts.js dashboard/static/app.01-attention.js
// SKIPPED when `node` is absent — it is never a build requirement
// (docs/testing.md).
"use strict";
const fs = require("fs");
const vm = require("vm");

const { El, domGlobals } = require("./domshim");

const $accounts = new El("div");

/* The app globals app.01 touches at load time / inside the strip (each shimmed
   to the smallest thing that keeps the renderer honest), plus the browser
   surface its module-level constants read. */
const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number, Object, Array, Boolean,
  Promise, RegExp, encodeURIComponent, decodeURIComponent, parseInt, isNaN,
  setTimeout: () => 1, clearTimeout: () => {},
  setInterval: () => 1, clearInterval: () => {},
  fetch: () => new Promise(() => {}),
  location: { hash: "" },
  navigator: { userAgent: "node", onLine: true, standalone: false },
  matchMedia: () => ({ matches: false }),
  ...domGlobals(),
  $accounts,
  $notifybtn: new El("button"), $wakebtn: new El("button"),
  $attn: new El("div"),
  S: { sessions: [], accts: null, currentSessionId: null },
  clog: () => {}, toast: () => {}, route: () => {},
  postJSON: () => Promise.resolve({}),
  ago: () => "", shortSid: (s) => s, proj: () => "",
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.document.body = new El("body");
sandbox.document.title = "";
sandbox.document.getElementById = () => new El("div");
sandbox.document.querySelector = () => null;
sandbox.document.addEventListener = () => {};
sandbox.document.documentElement = new El("html");

vm.createContext(sandbox);
const src = fs.readFileSync(process.argv[2], "utf8");
vm.runInContext(src, sandbox, { filename: "app.01-attention.js" });

/* ---------- structural signature of one rendered row ----------------------
   What must match across rows is the SEQUENCE OF BOXES, not their contents:
   same columns, same order, each window carrying its label, track, percent and
   reset cell. So the LAYOUT signature deliberately drops
     * the values (c1 reads 60%, c2 reads 0%; one has a reset, the other's was
       dropped — same boxes, and the boxes are fixed-width in `ch`),
     * the `ghost` marker (a placeholder is the same box with the ink turned
       down — that is the whole point of it),
     * the fill classes (`hot`/`warn` recolour, they don't resize),
   and keeps the window LABEL, which names the column and so must line up too.
   The ghosts are reported separately: WHICH cells are placeholders is the
   other half of the story. */
function sig(row) {
  return row.children.map(c => {
    const kind = c.className.split(/\s+/)[0];
    if (kind !== "ubar") return { kind };
    return {
      kind,
      label: c.children[0] && c.children[0].textContent,
      cells: c.children.map(x => x.className),
    };
  });
}

/* WHICH GRID TRACK each cell was placed in — the structural half of the stack,
   and the one the box SEQUENCE above cannot state. Two rows can emit the same
   boxes in the same order and still not line up: they were separate flex lines,
   so a column existed only for as long as everything in front of it happened to
   measure the same. The strip is now one grid whose tracks every row subgrids
   into, and each cell names its track — so "c1's 7d bar and codex's 7d bar are
   in column 3" is a fact the DOM states, which is what this reports. */
function place(row) {
  return row.children.map(c => ({
    kind: c.className.split(/\s+/)[0],
    label: c.className.split(/\s+/)[0] === "ubar" && c.children[0]
      ? c.children[0].textContent : "",
    sep: c._cls().includes("usep"),
    col: c.style.gridColumn,
  }));
}

function ghosts(row) {
  return row.children.map(c => c._cls().includes("ghost"));
}

function render(list) {
  sandbox.renderAccounts(list);
  return {
    rows: $accounts.children.map(sig),
    places: $accounts.children.map(place),
    // every row spans the WHOLE track set (it is a subgrid of the strip)
    rowspan: $accounts.children.map(r => r.style.gridColumn),
    // the strip's own track list — one entry per column, all measured by the
    // layout engine across the rows (`max-content`)
    tracks: $accounts.style.getPropertyValue("--acct-tracks"),
    ghosts: $accounts.children.map(ghosts),
    text: $accounts.children.map(r => r.textContent),
    aname: $accounts.style.getPropertyValue("--aname-w"),
    hidden: $accounts.hidden === true,
    // which HOST each rendered row belongs to, in render order. Columns now span
    // the WHOLE strip (keyed by duration), but rows are still GROUPED by host in
    // render order, so the python assertions need to know which row is whose.
    // Taken from the fixture, not
    // the DOM: the row carries no host marker on screen, and it should not.
    hosts: (list || []).map(a => a.harness),
  };
}

const now = Math.floor(Date.now() / 1000);
const out = { ok: true, errors: [], cases: {} };
function step(name, fn) {
  try { out.cases[name] = fn(); }
  catch (e) { out.ok = false; out.errors.push(name + ": " + e.message); }
}

/* The rows are served in the shared usage-window vocabulary
   (plugins.usage_strip): `windows` is the LIST the painter lays out, each entry
   carrying its own label, reset and `scope` — the server decides those, since
   only the owning host knows whether a window is account-wide (its own reset
   column) or a per-model cap under one. The flat `usage` dict rides along for
   the new-session account picker and is deliberately NOT what the strip reads.
   Fixtures state both, exactly as the server serves them. */
const W = (key, label, pct, reset, mins, scope) => ({
  key, label, used_percent: pct, resets_at: reset, duration_minutes: mins, scope,
  model_id: scope === "model" ? "fable" : null,
});
const H5 = (pct, reset) => W("five_hour", "5h", pct, reset, 300, "account");
const D7 = (pct, reset) => W("seven_day", "7d", pct, reset, 10080, "account");
const F7 = (pct, reset) =>
  W("seven_day_fable", "7d fable", pct, reset, 10080, "model");

// (1) The reported shape, verbatim from a live /api/accounts: c1 is the busy
// account (every window used, every reset present); c2 has been idle, so its
// 5h window ROLLED OVER — effective_usage zeroed it and dropped its reset —
// and its 7d reset is a DAY away where c1's is hours.
step("live_shape", () => render([
  { harness: "claude_code", account_id: "c1", display_name: "oboard",
    windows: [H5(60, now + 3600), D7(97, now + 10222), F7(32, now + 10222)],
    usage: { five_hour: 60, five_hour_reset: now + 3600,
             seven_day: 97, seven_day_reset: now + 10222, ts: now,
             seven_day_fable: 32, seven_day_fable_reset: now + 10222 } },
  { harness: "claude_code", account_id: "c2", display_name: "claude-01",
    windows: [H5(0, null), D7(82, now + 89422), F7(56, now + 89422)],
    usage: { five_hour: 0,
             seven_day: 82, seven_day_reset: now + 89422, ts: now,
             seven_day_fable: 56, seven_day_fable_reset: now + 89422 } },
]));

// (2) The per-model window attached to ONE account only.
step("model_window_on_one", () => render([
  { harness: "claude_code", account_id: "c1", display_name: "oboard",
    windows: [H5(60, now + 3600), D7(97, now + 10222), F7(32, now + 10222)],
    usage: { five_hour: 60, five_hour_reset: now + 3600,
             seven_day: 97, seven_day_reset: now + 10222, ts: now,
             seven_day_fable: 32, seven_day_fable_reset: now + 10222 } },
  { harness: "claude_code", account_id: "c2", display_name: "a-much-longer-label",
    windows: [H5(12, now + 900), D7(82, now + 89422)],
    usage: { five_hour: 12, five_hour_reset: now + 900,
             seven_day: 82, seven_day_reset: now + 89422, ts: now } },
]));

// (3) One account LOGGED OUT: its ⚠ badge sits before the bars, so the healthy
// row has to reserve the same slot or its bars start a badge-width to the left.
step("one_logged_out", () => render([
  { harness: "claude_code", account_id: "c1", display_name: "oboard",
    authentication_error: "run /login",
    windows: [H5(60, now + 3600), D7(97, now + 10222)],
    usage: { five_hour: 60, five_hour_reset: now + 3600,
             seven_day: 97, seven_day_reset: now + 10222, ts: now } },
  { harness: "claude_code", account_id: "c2", display_name: "claude-01",
    windows: [H5(0, null), D7(82, now + 89422)],
    usage: { five_hour: 0, seven_day: 82, seven_day_reset: now + 89422,
             ts: now } },
]));

// (4) TWO HOSTS in one strip — the whole reason the codex-only endpoint, DOM
// node, poll and painter could go. The codex row is a plain row with no slug
// and no switcher fields, and its weekly window now carries the SHARED "7d"
// (plugins.window_label): the same 10080 minutes is one column whoever reported
// it, so codex's bar has to wear the name Claude's does.
step("two_hosts", () => render([
  { harness: "claude_code", account_id: "c1", display_name: "oboard",
    windows: [H5(60, now + 3600), D7(97, now + 10222)],
    usage: { five_hour: 60, five_hour_reset: now + 3600,
             seven_day: 97, seven_day_reset: now + 10222, ts: now } },
  { harness: "codex", account_id: "", display_name: "codex · plus", switchable: false,
    plan: "plus", limit: null, authentication_error: null,
    windows: [W("w10080", "7d", 4, now + 522456, 10080, "account")] },
]));

// (5) TWO HOSTS that BOTH report a 5h window, plus a Claude-only per-model cap.
// The 5h bars and the 7d bars each share ONE column across the hosts (the
// columns are keyed by DURATION — Claude's key is `seven_day`, codex's `w10080`,
// and keying on that gave them separate columns). The "7d fable" column is
// Claude's alone, so the codex row emits no item there: it has no per-model cap
// and never will, and "—" would claim a reading it owes.
step("both_hosts_have_5h", () => render([
  { harness: "claude_code", account_id: "c1", display_name: "oboard",
    windows: [H5(60, now + 3600), D7(97, now + 10222), F7(32, now + 10222)],
    usage: { five_hour: 60, five_hour_reset: now + 3600,
             seven_day: 97, seven_day_reset: now + 10222, ts: now,
             seven_day_fable: 32, seven_day_fable_reset: now + 10222 } },
  { harness: "codex", account_id: "", display_name: "codex · plus", switchable: false,
    plan: "plus", limit: null, authentication_error: null,
    windows: [W("w300", "5h", 41, now + 1800, 300, "account"),
              W("w10080", "7d", 4, now + 522456, 10080, "account")] },
]));

// (6) THE EMPTY CELL, and the reason the columns are SORTED by duration rather
// than taken in first-seen order: codex is served FIRST and has only the weekly
// window. Its 7d bar must still sit in the 7d column — the SECOND one — with the
// 5h column left empty above Claude's, never sliding left into it. That slide is
// the reported bug: with the columns unioned per host, codex's lone weekly bar
// started where Claude's 5h bar starts.
step("codex_first_no_5h", () => render([
  { harness: "codex", account_id: "", display_name: "codex · plus", switchable: false,
    plan: "plus", limit: null, authentication_error: null,
    windows: [W("w10080", "7d", 4, now + 522456, 10080, "account")] },
  { harness: "claude_code", account_id: "c1", display_name: "oboard",
    windows: [H5(60, now + 3600), D7(97, now + 10222)],
    usage: { five_hour: 60, five_hour_reset: now + 3600,
             seven_day: 97, seven_day_reset: now + 10222, ts: now } },
]));

/* ---------- the reset column's width ------------------------------------
   Every duration the clock can produce, measured in characters of the mono
   font the column is sized in. The winner is the hours form ("resets in 23h
   59m"), not the days form it was sized from. */
const RESETS = "resets ";
let widest = { n: 0, txt: "" };
for (let s = 1; s <= 8 * 86400; s += 7) {
  const txt = sandbox.resetAgo(now + s);
  const n = RESETS.length + (txt.startsWith("in ") ? txt.length : txt.length);
  if (n > widest.n) widest = { n, txt: RESETS + txt };
}
out.widest_reset = widest;

console.log(JSON.stringify(out));
