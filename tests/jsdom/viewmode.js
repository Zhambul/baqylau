// tests/jsdom/viewmode.js — drives the REAL view-mode engine
// (dashboard/static/app.05-session.js) over a minimal DOM shim and prints one
// JSON verdict object, which test_l0_dashboard.py asserts on.
//
// Why this exists: the collapse itself — which adjacent items become one run,
// what the summary says, whether the dot is grey/green/red — lives entirely in
// the page, and the rest of the suite can only GREP that file. A grep can't
// catch "the run cut is off by one" or "the plural is wrong". This runs the
// actual source instead. The harness is deliberately tiny and dumb: it shims
// only the handful of DOM methods and app globals the engine touches, and it
// asserts nothing itself.
//
// Usage: node tests/jsdom/viewmode.js dashboard/static/app.05-session.js
// SKIPPED when `node` is absent — it is the only JS-executing test in the repo
// (see docs/testing.md), never a build requirement.
"use strict";
const fs = require("fs");
const vm = require("vm");

/* ---------- the DOM shim: elements, classes, dataset, class-selector queries */
class El {
  constructor(tag, cls, text) {
    this.tag = tag; this.className = cls || ""; this.dataset = {};
    this.children = []; this.parentNode = null; this._text = text || "";
    this.onclick = null;
    const self = this;
    this.classList = {
      add(c) { if (!self._cls().includes(c)) self.className = (self.className + " " + c).trim(); },
      remove(c) { self.className = self._cls().filter(x => x !== c).join(" "); },
      contains(c) { return self._cls().includes(c); },
      toggle(c, on) { if (on) this.add(c); else this.remove(c); },
    };
  }
  _cls() { return this.className.split(/\s+/).filter(Boolean); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  set textContent(v) { this._text = String(v); this.children = []; }
  // A DocumentFragment must FLATTEN on insert (its children move, the fragment
  // itself never becomes a node) — appendOlder batches a whole page into one.
  _flat(kids) {
    const out = [];
    for (const k of kids) {
      if (k.tag === "#frag") { out.push(...k.children); k.children = []; }
      else out.push(k);
    }
    return out;
  }
  append(...kids) {
    for (const k of this._flat(kids)) { k.parentNode = this; this.children.push(k); }
  }
  insertBefore(node, ref) {
    const at = this.children.indexOf(ref);
    const kids = this._flat([node]);
    for (const k of kids) k.parentNode = this;
    this.children.splice(at < 0 ? this.children.length : at, 0, ...kids);
  }
  remove() {
    if (!this.parentNode) return;
    const a = this.parentNode.children;
    a.splice(a.indexOf(this), 1);
    this.parentNode = null;
  }
  // Enough of innerHTML for appendItems/appendOlder, which set it on a scratch
  // div and take firstElementChild: one child element carrying the outermost
  // tag's class. The served HTML is otherwise opaque to these tests.
  set innerHTML(html) {
    const m = /^\s*<(\w+)[^>]*?(?:\sclass="([^"]*)")?[^>]*>/.exec(String(html));
    this.children = [];
    if (m) this.append(new El(m[1], m[2] || ""));
  }
  get innerHTML() { return ""; }
  get firstElementChild() { return this.children[0] || null; }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  get childElementCount() { return this.children.length; }
  // Enough of insertAdjacentHTML for the block filler: the served HTML is opaque
  // to these tests (they measure counts, classes and data-*), so it is kept as
  // one text-bearing child rather than parsed.
  insertAdjacentHTML(_pos, html) {
    const n = new El("#html", "", html);
    n.parentNode = this;
    this.children.push(n);
  }
  _all(out) { for (const c of this.children) { out.push(c); c._all(out); } return out; }
  querySelectorAll(sel) {                       // only ".cls" / ".cls[data-x]"
    const cls = sel.split("[")[0].replace(".", "");
    const attr = (/\[data-([a-z]+)\]/.exec(sel) || [])[1];
    return this._all([]).filter(n => n._cls().includes(cls)
      && (!attr || n.dataset[attr] !== undefined));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

/* ---------- the app globals the engine calls (everything else is in the file) */
const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number,
  setInterval: () => 1, clearInterval: () => {},
  setTimeout: () => 1, clearTimeout: () => {},
  performance: { now: () => 0 },
  document: { createElement: t => new El(t), createTextNode: s => new El("#text", "", s),
              createDocumentFragment: () => new El("#frag") },
  el: (tag, cls, text) => new El(tag, cls, text),
  tnode: s => new El("#text", "", s),
  dur: sec => Math.max(0, sec | 0) + "s",
  BUSY_TABS: ["thinking", "working", "executing", "awaiting-bg"],
  liveTab: () => sandbox.__tab,
  postJSON: () => { sandbox.__posted++; return Promise.resolve({}); },
  clog: () => {}, loadOlder: () => { sandbox.__loadOlder++; },
  renderAttention: () => {}, applyFilter: () => {},
  S: null, __tab: "", __loadOlder: 0, __posted: 0,
  // /history stub for the load-older loop. __pages records the block counts asked
  // for. Two content shapes, because they exercise opposite ends of the loop:
  //   __mix = true  — a realistic page (a prompt/reply/edit every ~5 blocks;
  //                   measured against real sessions, a 40-block page yields
  //                   ~20-30 focus-visible items), so the loop converges fast;
  //   __mix = false — a pathological all-commands stretch, where EVERY block
  //                   folds into the run already at the boundary, so the visible
  //                   count cannot rise however much is fetched. The loop must
  //                   spend its budget and stop, not spin.
  __pages: [],
  __mix: true,
  __exhaustAfter: 99,
  fetch: (url) => {
    const blocks = +(/blocks=(\d+)/.exec(url) || [])[1];
    sandbox.__pages.push(blocks);
    const p = sandbox.__pages.length, items = [];
    for (let i = 0; i < blocks; i++) {
      const id = "old" + p + "-" + i;
      if (sandbox.__mix && i % 5 === 0)
        items.push({ g: null, t: "msg", kind: i % 10 ? "message" : "prompt",
                     act: "msg", html: "<div class=\"msg\">x</div>" });
      else
        items.push({ g: id, t: "label", act: "bash",
                     html: "<span class=\"chip\">x</span>" });
    }
    const oldest = p >= sandbox.__exhaustAfter ? 0 : 5;
    return Promise.resolve({ json: () => Promise.resolve({ items, oldest }) });
  },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);

/* ---------- fixtures */
let seq = 0;
function item(spec) {
  const e = new El("div", "blk");
  e.dataset.kind = spec.kind || "commands";
  if (spec.act) e.dataset.act = spec.act;
  if (spec.msg) e.dataset.msg = spec.msg;
  if (spec.injected) e.dataset.injected = "1";
  if (spec.bad) e.dataset.bad = "1";
  if (spec.add) e.dataset.add = String(spec.add);
  if (spec.rem) e.dataset.rem = String(spec.rem);
  if (spec.g) e.dataset.g = spec.g;
  e.dataset.vk = String(++seq);
  e.dataset.vt = String(Date.now() / 1000 - 30);   // 30s old: past the 2s floor
  return e;
}

// oldest -> newest, mirroring how the feed is built (each item prepended)
function scene(mode, oldestFirst, tab) {
  seq = 0;
  const stream = new El("div", "stream");
  for (const spec of oldestFirst) stream.insertBefore(item(spec), stream.children[0]);
  sandbox.__tab = tab || "";
  sandbox.S = { cur: "sid", sessions: [{ sid: "sid", tab: tab || "" }], ses: {
    stream, view: mode, viewOpen: new Set(), viewSeq: seq, viewTimer: null,
    viewFill: 0, oldest: 0, blocks: new Map(), fgRun: null, meta: {},
    loadingOlder: false, moreEl: null,
  } };
  sandbox.applyViewMode();
  return sandbox.S.ses;
}

const F = {
  fg: { act: "bash", g: "t1" },
  read: { act: "read", kind: "files" },
  upd: { act: "edit", kind: "files", add: 12, rem: 3 },
  wr: { act: "write", kind: "files", add: 40 },
  agent: { act: "agent", kind: "agents" },
  prompt: { act: "msg", kind: "messages", msg: "prompt" },
  reply: { act: "msg", kind: "messages", msg: "message" },
  warn: { act: "warn", kind: "commands" },
  memread: { act: "read", kind: "memory" },
  // a user-SHAPED turn Claude Code injected: a Stop hook's feedback, a loaded
  // skill's SKILL.md body, a resume nudge (transcript isMeta)
  hookmsg: { act: "msg", kind: "messages", msg: "prompt", injected: 1 },
};

/* ---------- readers */
function sums(ses) {
  return ses.stream.children.filter(c => c.classList.contains("vsum")).map(c => ({
    text: c.querySelector(".vtext").textContent,
    dot: c.querySelector(".vdot")._cls().filter(x => x !== "vdot")[0] || "running",
    timer: c.querySelector(".vtimer").textContent,
    open: c.dataset.open,
  }));
}
function shown(ses) {
  return ses.stream.children
    .filter(c => c.dataset.kind && !c.classList.contains("vhide"))
    .map(c => c.dataset.act || c.dataset.kind);
}
function sumRow(ses) {
  return ses.stream.children.find(c => c.classList.contains("vsum"));
}

/* ---------- the verdicts the Python test asserts on */
const out = {};
const story = [F.prompt, F.read, F.read, F.fg, F.reply, F.fg, F.fg, F.upd,
               F.read, F.fg, F.reply];

out.verbose = { sums: sums(scene("verbose", story)).length,
                shown: shown(scene("verbose", story)).length };
const dflt = scene("default", story);
out.default = { sums: sums(dflt).map(s => s.text), shown: shown(dflt) };
const foc = scene("focus", story);
out.focus = { sums: sums(foc).map(s => s.text), shown: shown(foc) };

out.singular = sums(scene("default", [F.prompt, F.read, F.fg]))[0].text;
out.plural = sums(scene("default",
  [F.prompt, F.read, F.read, F.fg, F.fg]))[0].text;
out.failed = sums(scene("default",
  [F.prompt, F.fg, { act: "bash", g: "t9", bad: 1 }]))[0];
out.live = sums(scene("default", [F.prompt, F.read, F.fg], "executing"))[0];
out.memory = sums(scene("default",
  [F.prompt, F.memread, F.memread, F.agent]))[0].text;
out.editSummary = sums(scene("focus",
  [F.prompt, F.fg, F.upd, F.wr, F.read, F.reply]))[0].text;
out.warnBreaksRuns = { sums: sums(scene("default",
  [F.prompt, F.fg, F.warn, F.fg])).length,
  shown: shown(scene("default", [F.prompt, F.fg, F.warn, F.fg])) };

// expand -> collapse round trip (the summary must SURVIVE expansion: it is the
// only way back), and the signature guard must make a repeat pass a no-op
const rt = scene("default", [F.prompt, F.fg, F.fg, F.read]);
const sig = rt.viewSig;
sandbox.applyViewMode();
out.idempotent = sig === rt.viewSig;
sumRow(rt).onclick();
out.expanded = { shown: shown(rt), sums: sums(rt).map(s => s.open) };
sumRow(rt).onclick();
out.recollapsed = { shown: shown(rt), sums: sums(rt).map(s => s.open) };

// an INJECTED prompt is dropped by both non-verbose modes and does NOT close the
// turn — the reply after it still belongs to the prompt the human typed, so focus
// must not surface a second "final" reply per hook firing
const inj = [F.prompt, F.fg, F.reply, F.hookmsg, F.reply];
out.injected = {
  verbose: shown(scene("verbose", inj)).length,
  default: shown(scene("default", inj)),
  focus: shown(scene("focus", inj)),
};

// a run that GROWS keeps its key (so the user's expansion survives new items)
const grow = scene("default", [F.prompt, F.fg, F.fg]);
sumRow(grow).onclick();
const keyBefore = sumRow(grow).dataset.run;
grow.stream.insertBefore(item(F.fg), grow.stream.children[0]);
sandbox.applyViewMode();
out.growth = { sameKey: keyBefore === sumRow(grow).dataset.run,
               stillOpen: sumRow(grow).dataset.open === "1",
               text: sumRow(grow).querySelector(".vtext").textContent };

// switching back to verbose must leave NOTHING hidden and no summaries behind
const back = scene("default", [F.prompt, F.fg, F.read]);
back.view = "verbose";
sandbox.applyViewMode();
out.backToVerbose = { sums: sums(back).length, shown: shown(back).length };

// ---- the "load older · 40 more" promise, in a collapsing mode
// The page-size policy is pure, so check it directly: aim the next request at
// the shortfall at the observed yield, and reach for the ceiling when a page
// yielded nothing at all.
out.pageSize = {
  yielded2of40: sandbox.olderPageSize(40, 2, 40),   // 2 per 40 -> ~760, capped
  yielded10of40: sandbox.olderPageSize(40, 10, 40), // 10 per 40 -> 120
  yieldedNothing: sandbox.olderPageSize(40, 0, 40), // -> the ceiling
  alreadyThere: sandbox.olderPageSize(40, 40, 40),  // -> the floor, unused
};

// …and drive the real loop against the /history stub. Focus mode collapses every
// block the stub serves, so one page yields ~1 visible line: the loop must keep
// going instead of stopping at the first (the reported bug).
function fillScene(mode, opts) {
  opts = opts || {};
  const ses = scene(mode, [F.prompt, F.fg]);
  ses.oldest = 5;                      // older history exists
  sandbox.__pages = [];
  sandbox.__mix = opts.mix !== false;
  sandbox.__exhaustAfter = opts.exhaustAfter === undefined ? 99 : opts.exhaustAfter;
  return ses;
}

const fills = {};
function runFill(name, mode, target, opts) {
  const ses = fillScene(mode, opts);
  const before = sandbox.visibleCount();
  return Promise.resolve(sandbox.loadOlder(target)).then(() => new Promise(r => {
    // let the promise chain drain (the stub resolves immediately, so a few
    // microtask turns are enough)
    let n = 0;
    const tick = () => (++n < 50 ? Promise.resolve().then(tick) : r());
    tick();
  })).then(() => {
    fills[name] = { pages: sandbox.__pages.length,
                    asked: sandbox.__pages.slice(0, 3),
                    gained: sandbox.visibleCount() - before,
                    stuck: ses.loadingOlder };
  });
}

runFill("focus", "focus", 40)
  .then(() => runFill("verbose", "verbose", 40))
  .then(() => runFill("allCommands", "focus", 40, { mix: false }))
  .then(() => runFill("exhausted", "focus", 40, { mix: false, exhaustAfter: 2 }))
  .then(() => {
    out.fills = fills;
    process.stdout.write(JSON.stringify(out, null, 1));
  });
