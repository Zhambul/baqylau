// tests/jsdom/ctxbar.js — drives the REAL context-saturation bar renderer
// (dashboard/static/app.04-list.js `contextBar`) over the shared DOM shim and
// prints one JSON verdict object, which tests/test_dashboard_dom.py asserts on.
//
// Why this exists: the bar grew two behaviours that are pure DOM logic and so
// invisible to every server-side test:
//
//   * the COMPACTING state — the `compacting` class the CSS breathes from, and
//     a label/detail that stop reporting a token count nobody can act on for
//     those ~2 minutes. Critically the bar is painted at its REAL width and
//     left there: the calm reading is that geometry never moves while
//     compacting, and only a rendered width can show that;
//   * the eased DRAIN — the bar is a FRESH node on every repaint, which has no
//     previous width to transition from, so contextBar paints it at its REMEMBERED
//     width and moves it on the next frame. That memory is the whole mechanism
//     and it is per-bar: a grep can see the rAF call but not that session A's
//     drain animates from session A's last width (the bug the key exists to
//     prevent is every bar sliding from whatever the previously-rendered bar
//     happened to show).
//
// Both are properties of a SEQUENCE of renders, which is why this executes the
// renderer repeatedly rather than asserting on one call.
//
// Usage: node tests/jsdom/ctxbar.js dashboard/static/app.04-list.js
// SKIPPED when `node` is absent — it is never a build requirement
// (docs/testing.md).
"use strict";
const fs = require("fs");
const vm = require("vm");

const { El, domGlobals } = require("./domshim");

// rAF is the drain's second half: contextBar paints the remembered width, then asks
// for a frame to move to the real one. Queue the callbacks rather than running
// them inline — the POINT is that the two widths are set in different frames,
// and a shim that ran the callback immediately would report the final width for
// both and prove nothing.
const frames = [];
const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number, Object, Array, Boolean,
  Promise, RegExp, encodeURIComponent, decodeURIComponent, parseInt, isNaN,
  setTimeout: () => 1, clearTimeout: () => {},
  setInterval: () => 1, clearInterval: () => {},
  requestAnimationFrame: (fn) => { frames.push(fn); return frames.length; },
  fetch: () => new Promise(() => {}),
  location: { hash: "" },
  navigator: { userAgent: "node", onLine: true, standalone: false },
  matchMedia: () => ({ matches: false }),
  ...domGlobals(),
  S: { sessions: [], currentSessionId: null, sessionView: null },
  clog: () => {}, toast: () => {}, route: () => {},
  postJSON: () => Promise.resolve({}),
  ago: () => "", shortSid: (s) => s, proj: () => "", dur: () => "",
  kfmt: (n) => String(n),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.document.body = new El("body");
sandbox.document.getElementById = () => new El("div");
sandbox.document.querySelector = () => null;
sandbox.document.addEventListener = () => {};
sandbox.document.documentElement = new El("html");

vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox,
                { filename: "app.04-list.js" });

function flush() { const q = frames.splice(0); for (const fn of q) fn(); }

/* One rendered bar, read the way the eye reads it. `widths` is the pair the
   drain is made of: what the fill was painted at, and what it settles to once
   the frame runs. */
function read(bar) {
  const track = bar.children.find(c => c.className === "ctrack");
  const fill = track.children.find(c => c.className === "cfill");
  const painted = fill.style.width;
  flush();
  return {
    cls: bar.className,
    label: bar.children[0].textContent,
    pct: bar.children.find(c => c.className === "cpct").textContent,
    detail: bar.children.find(c => c.className === "cdetail").textContent,
    // the track's whole content — the calm bar is ONE node in there, so an
    // extra overlay segment creeping back in is visible here as a class name
    kids: track.children.map(c => c.className),
    widths: [painted, fill.style.width],
  };
}

function bar(contextWindow, big, opts) { return read(sandbox.contextBar(contextWindow, big, opts)); }

const out = { ok: true, errors: [], cases: {} };
function step(name, fn) {
  try { out.cases[name] = fn(); }
  catch (e) { out.ok = false; out.errors.push(name + ": " + e.message); }
}

const CX = (pct, used) => ({ pct, used: used || pct * 2000, window: 200000 });

// (1) The plain bar, unkeyed — every existing call site (session cards, agent
// cards) passes no opts, and must render exactly as it always has: no ghost,
// no animation, both widths equal, the token detail intact.
step("plain", () => bar(CX(42), false));

// (2) First sight of a key SEEDS without animating: a page load must not slide
// every bar up from zero.
step("first_sight", () => bar(CX(42), true, { key: "s:one" }));

// (3) ...and the NEXT render of that same key drains from the remembered width
// to the new one — the two-frame pair. This is the post-compaction money shot:
// 87% → 4%.
step("drain", () => {
  bar(CX(87), true, { key: "s:drain" });
  return bar(CX(4), true, { key: "s:drain" });
});

// (4) The memory is PER BAR. Interleave two sessions: each must animate from
// its OWN last width, not from whichever bar rendered most recently.
step("keys_dont_bleed", () => {
  bar(CX(90), true, { key: "s:a" });
  bar(CX(10), true, { key: "s:b" });
  return { a: bar(CX(20), true, { key: "s:a" }), b: bar(CX(80), true, { key: "s:b" }) };
});

// (5) COMPACTING: the class the CSS breathes from is present, the bar is
// painted at its REAL width and stays there (both frames equal — nothing
// moves), the ⟳ spins in its own span (so the word doesn't rotate with it),
// and the detail says what is happening instead of a token count that is about
// to be wrong.
step("compacting", () => bar(CX(87, 174000), true,
                             { key: "s:c", comp: { since: 1, trigger: "manual" } }));

// (6) A compacting bar does NOT consume its key's memory — the drain that
// follows must start from the pre-compaction width, not from whatever the
// rehearsal was showing. Render: settled 87 → compacting → settled 4.
step("compaction_then_drain", () => {
  bar(CX(87), true, { key: "s:full" });
  bar(CX(87), true, { key: "s:full", comp: { since: 1, trigger: "auto" } });
  return bar(CX(4), true, { key: "s:full" });
});

// (7) The colour ladder still applies while settled (hot ≥90, warn ≥70) — the
// compacting override is a CSS concern, but the CLASS must still be emitted so
// nothing else that reads it breaks.
step("ladder", () => ({
  hot: bar(CX(95), false).cls,
  warn: bar(CX(75), false).cls,
  cool: bar(CX(20), false).cls,
  hot_compacting: bar(CX(95), false, { comp: { since: 1 } }).cls,
}));

// (8) Out-of-range percentages are clamped for the WIDTH but reported verbatim
// in the text (unchanged behaviour — the server's pct is already capped, this
// only pins that the new code path didn't lose the clamp).
step("clamped", () => ({ over: bar(CX(140), false), under: bar(CX(-5), false) }));

process.stdout.write(JSON.stringify(out));
