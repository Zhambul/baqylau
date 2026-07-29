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

/* ---------- the DOM shim (shared with the other harnesses) */
const { El, domGlobals } = require("./domshim");

/* ---------- the app globals the engine calls (everything else is in the file) */
const sandbox = {
  console, Date, Math, Set, Map, JSON, String, Number,
  setInterval: () => 1, clearInterval: () => {},
  setTimeout: () => 1, clearTimeout: () => {},
  performance: { now: () => 0 },
  ...domGlobals(),
  encodeURIComponent,
  dur: sec => Math.max(0, sec | 0) + "s",
  // app.00-core's text-presentation pin (U+FE0E on emoji-capable glyphs). Identity
  // here: this harness measures ROUTING, and the pin itself is asserted from Python
  // over the bundle + opshtml.text_presentation.
  tp: s => s,
  BUSY_TABS: ["thinking", "working", "executing", "awaiting-bg"],
  liveTab: () => sandbox.__tab,
  postJSON: () => { sandbox.__posted++; return Promise.resolve({}); },
  clog: () => {}, loadOlder: () => { sandbox.__loadOlder++; },
  // `agentQ` is the agent-scope query suffix (app.00-core.js) — a cross-part
  // global like clog/pre, stubbed with its real 3-line body so the URLs this
  // harness records are the ones the browser would send
  agentQ: (sep) => { const a = (sandbox.S && sandbox.S.ses && sandbox.S.ses.agent) || "";
                     return a ? (sep || "?") + "agent=" + encodeURIComponent(a) : ""; },
  // the OUTCOME vocabulary lives with the agent cards (app.11-chrome.js
  // agentStatus); the engine under test only JOINS to it, so the stub simply
  // echoes a state planted on the agent record. That the real mapping is the one
  // consulted is asserted from Python (it must call agentStatus, not re-read
  // end_reason itself).
  agentStatus: a => ["", (a && a.st) || ""],
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
      // every served item carries an AGE MARKER `h<page>_<index>` (the server
      // sends a page oldest->newest, so a higher index is NEWER) — readable off
      // a stray's className and out of a block's chip text, which is how the
      // ordering verdict below reconstructs the feed's final order
      const mk = "h" + p + "_" + i;
      if (sandbox.__mix && i % 5 === 0)
        items.push({ g: null, t: "msg", kind: i % 10 ? "message" : "prompt",
                     act: "msg", html: "<div class=\"msg " + mk + "\">x</div>" });
      else
        items.push({ g: id, t: "label", act: "bash",
                     html: "<span class=\"chip\">" + mk + "</span>" });
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
  if (spec.resumed) e.dataset.resumed = "1";
  if (spec.bad) e.dataset.bad = "1";
  if (spec.add) e.dataset.add = String(spec.add);
  if (spec.rem) e.dataset.rem = String(spec.rem);
  if (spec.g) e.dataset.g = spec.g;
  if (spec.agent) e.dataset.agent = spec.agent;
  if (spec.mid) e.dataset.mid = spec.mid;
  if (spec.act && spec.act !== "msg") e.dataset.open = "1";   // a block card,
  //                          born expanded like appendItems' live-tail blocks
  if (spec.userset) e.dataset.userset = "1";
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
  mon: { act: "monitor", kind: "commands" },      // ◉ a monitor block
  skill: { act: "skill", kind: "commands" },      // ⏺ Skill(<name>)
  task: { act: "task", kind: "commands" },        // ✚/✓ task-list rows
  mail: { act: "mail", kind: "commands" },        // ● team mail (header + body)
  // a subagent's own blocks, both carrying its src id: one AGENT, four rows
  aLaunch: { act: "agent", kind: "agents", agent: "a1" },
  aResult: { act: "agent", kind: "agents", agent: "a1" },
  bLaunch: { act: "agent", kind: "agents", agent: "a2" },
  bResult: { act: "agent", kind: "agents", agent: "a2" },
  // one MESSAGE, two rows: it arrives (with its body) and it is read
  mailIn: { act: "mail", kind: "commands", mid: "m1" },
  mailRead: { act: "mail", kind: "commands", mid: "m1" },
  // a user-SHAPED turn Claude Code injected: a Stop hook's feedback, a loaded
  // skill's SKILL.md body, a resume nudge (transcript isMeta)
  hookmsg: { act: "msg", kind: "messages", msg: "prompt", injected: 1 },
  // …and the one flavour that RESUMED an ended turn: a blocking Stop hook's
  // feedback (transcript _RESUMES_TURN). Still hidden, but the reply above it
  // is that turn's final answer.
  stopmsg: { act: "msg", kind: "messages", msg: "prompt", injected: 1,
             resumed: 1 },
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
    .map(c => (c.dataset.act || c.dataset.kind)
              + (c.classList.contains("vdim") ? ":dim" : ""));
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

// TEAM PLUMBING — a lead session's agents, task rows and mail. Default folds it
// into the summary; focus drops it outright, counters included, and what is left
// must be exactly the conversation plus the work the turn did to the repo.
const team = [F.prompt, F.agent, F.mail, F.mail, F.task, F.fg, F.upd,
              F.agent, F.reply];
const teamD = scene("default", team), teamF = scene("focus", team);
out.teamDefault = { sums: sums(teamD).map(s => s.text), shown: shown(teamD) };
out.teamFocus = { sums: sums(teamF).map(s => s.text), shown: shown(teamF) };
// …and a session that is ONLY plumbing collapses to the conversation alone —
// no summary line at all, since a hidden act is not counted into one
const onlyTeam = scene("focus", [F.prompt, F.agent, F.mail, F.task, F.reply]);
out.teamOnly = { sums: sums(onlyTeam).length, shown: shown(onlyTeam) };
// the AGENT counter counts AGENTS, not agent-ish rows: two subagents, each with a
// launch note and a finish note, is "ran 2 agents" — counting rows said 77 for a
// session with 21 of them
out.agentCount = sums(scene("focus",
  [F.prompt, F.aLaunch, F.bLaunch, F.aResult, F.bResult, F.reply]))[0].text;
// …and a row with no src id still counts once (unattributable, never uncounted)
out.agentCountNoId = sums(scene("focus",
  [F.prompt, F.agent, F.agent, F.reply]))[0].text;
// the same rule for MAIL, by msg_id: an arrival and its read notice are one
// message ("passed 4 messages" for two that had been sent)
out.mailCount = sums(scene("focus",
  [F.prompt, F.mailIn, F.mailRead, F.reply]))[0].text;
out.mailCountNoId = sums(scene("focus",
  [F.prompt, F.mail, F.mail, F.reply]))[0].text;

// EXPANDING a run reveals what its summary COUNTED — never what the mode hid.
// The hidden items sit inside the run's span, and revealing the span wholesale
// brought every agent/mail/task row back on the first click a reader made.
const teamExp = scene("focus", team);
sumRow(teamExp).onclick();
out.teamExpanded = { shown: shown(teamExp),
                     rail: teamExp.stream.children
                       .filter(c => c.classList.contains("vrun")).length,
                     railLast: teamExp.stream.children
                       .filter(c => c.classList.contains("vrun-last"))
                       .map(c => c.dataset.act) };

// focus, mid-turn: the newest message is PROVISIONAL (greyed), older prose is
// gone, and once the tab settles that same message is the result (full weight)
const midTurn = [F.prompt, F.read, F.reply, F.fg, F.reply];
out.focusRunning = shown(scene("focus", midTurn, "executing"));
out.focusSettled = shown(scene("focus", midTurn, "awaiting-response"));
// an older turn's reply is never provisional, even while a NEW turn runs
out.focusOlderTurn = shown(scene("focus",
  [F.prompt, F.reply, F.prompt, F.fg], "executing"));

// a MONITOR folds in DEFAULT (it did not before), a background job does not
const mons = scene("default", [F.prompt, F.mon, F.mon, F.reply]);
out.monitorDefault = { sums: sums(mons).map(s => s.text), shown: shown(mons) };
const jobs = scene("default", [F.prompt, { act: "bg", kind: "commands" }, F.reply]);
out.bgDefault = { sums: sums(jobs).map(s => s.text), shown: shown(jobs) };

// a SKILL stands as its own line in default and is COUNTED in focus
const skD = scene("default", [F.prompt, F.skill, F.skill, F.reply]);
const skF = scene("focus", [F.prompt, F.skill, F.skill, F.reply]);
out.skillDefault = { sums: sums(skD).map(s => s.text), shown: shown(skD) };
out.skillFocus = { sums: sums(skF).map(s => s.text), shown: shown(skF) };

out.singular = sums(scene("default", [F.prompt, F.read, F.fg]))[0].text;
out.plural = sums(scene("default",
  [F.prompt, F.read, F.read, F.fg, F.fg]))[0].text;
out.failed = sums(scene("default",
  [F.prompt, F.fg, { act: "bash", g: "t9", bad: 1 }]))[0];
out.live = sums(scene("default", [F.prompt, F.read, F.fg], "executing"))[0];
// FOCUS, not default: default leaves agents standing as their own rows now, and
// this verdict is about the memory WORDING plus the fragment ORDER (agent before
// commands) — both of which need the agent folded into the same line.
out.memory = sums(scene("focus",
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
const marks = (ses) => ses.stream.children
  .filter(c => c.dataset.kind)
  .map(c => (c.classList.contains("vrun") ? "R" : "-")
          + (c.classList.contains("vrun-last") ? "L" : "-"));
const opens = ses => ses.stream.children
  .filter(c => c.dataset.kind && c.classList.contains("blk"))
  .map(c => c.dataset.open || "-");
out.expanded = { shown: shown(rt), sums: sums(rt).map(s => s.open),
                 marks: marks(rt), opens: opens(rt) };
sumRow(rt).onclick();
out.recollapsed = { shown: shown(rt), sums: sums(rt).map(s => s.open),
                    marks: marks(rt) };

// an INJECTED prompt is dropped by both non-verbose modes and does NOT close the
// turn — the reply after it still belongs to the prompt the human typed, so focus
// must not surface a second "final" reply per hook firing
const inj = [F.prompt, F.fg, F.reply, F.hookmsg, F.reply];
out.injected = {
  verbose: shown(scene("verbose", inj)).length,
  default: shown(scene("default", inj)),
  focus: shown(scene("focus", inj)),
};

// …but a Stop hook's feedback RESUMED a turn that had ENDED, so the reply in
// front of it IS a final answer: focus keeps BOTH it and the reply the resumed
// turn ends on (the memory-note nudge that hid every real result). The injected
// bubble itself stays hidden, and the runs either side still merge into one
// summary — only the reply search is cut.
const stop = [F.prompt, F.fg, F.reply, F.stopmsg, F.read, F.reply];
out.stopResume = {
  focus: shown(scene("focus", stop)),
  sums: sums(scene("focus", stop)).map(s => s.text),
  // …and while the RESUMED turn is still running, only its own provisional
  // reply is greyed — the answer behind the hook is settled, at full weight
  busy: shown(scene("focus", stop, "working")),
};

// a block the USER opened inside a revealed run is left alone
const keep = scene("default", [F.prompt, F.fg,
                               { act: "bash", g: "tK", userset: 1 }, F.fg]);
sumRow(keep).onclick();
out.userOpened = keep.stream.children
  .filter(c => c.dataset.kind && c.classList.contains("blk"))
  .map(c => (c.dataset.userset ? "U" : "-") + (c.dataset.open || "-"));

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

// ---- the MODE SWITCH's own fill (viewAutoFill), which is a different, much
// smaller promise than the button's: switching to a collapsing mode tops the
// window up to VIEW_FILL_MIN visible items. Nothing measured it before, and the
// number is tuned (see its comment) — so drive the real path: a scene with older
// history and almost nothing visible, switched, then drained.
function switchScene(mode) {
  // built in the OTHER mode and switched, like a real toggle — a scene rebuilt in
  // its own mode would be stopped by the signature guard before the fill is reached
  const ses = fillScene(mode === "verbose" ? "focus" : "verbose", {});
  ses.view = mode;
  sandbox.applyViewMode();                 // what setViewMode does, minus the POST
  return Promise.resolve().then(() => new Promise(r => {
    let n = 0;
    const tick = () => (++n < 200 ? Promise.resolve().then(tick) : r());
    tick();
  })).then(() => ({ visible: sandbox.visibleCount(), pages: sandbox.__pages.length,
                    fills: ses.viewFill }));
}

// ---- and the ORDER a loaded page lands in. The feed is newest-top, so a page
// (served oldest->newest) must be laid out reversed, and each next page below the
// last — otherwise the loaded stretch reads bottom-up while the live tail above
// it reads top-down. Read the age markers the stub stamps: the expected sequence
// is page 1 newest->oldest, then page 2 newest->oldest.
function orderScene() {
  const ses = fillScene("focus", { exhaustAfter: 2 });   // exactly two pages
  return Promise.resolve(sandbox.loadOlder(40)).then(() => new Promise(r => {
    let n = 0;
    const tick = () => (++n < 50 ? Promise.resolve().then(tick) : r());
    tick();
  })).then(() => {
    const marks = [];
    for (const c of ses.stream.children) {
      const m = /h(\d+)_(\d+)/.exec(c.className) || /h(\d+)_(\d+)/.exec(c.textContent);
      if (m) marks.push([+m[1], +m[2]]);
    }
    // pages must arrive in order, and each page's own items newest-first
    let pagesAscend = true, withinDescend = true;
    for (let i = 1; i < marks.length; i++) {
      const [pp, pi] = marks[i - 1], [p, i2] = marks[i];
      if (p < pp) pagesAscend = false;
      else if (p === pp && i2 > pi) withinDescend = false;
    }
    out.olderOrder = { items: marks.length, pages: sandbox.__pages.length,
                       pagesAscend, withinDescend,
                       head: marks.slice(0, 3).map(m => m.join("_")),
                       tail: marks.slice(-3).map(m => m.join("_")) };
  });
}

// ---- and the DOT on an agent note: grey while its agent runs, green when it
// finished, red when it didn't. The join is by `data-agent` into the agents
// payload, so this drives the real tintAgentNotes over a planted payload — plus
// the per-row override (a failing op inside the block reddens that row alone) and
// a row with no agent at all (team mail), which must stay untinted.
function dotScene() {
  const ses = scene("verbose", [F.aLaunch, F.aResult, F.bLaunch, F.mail]);
  ses.agents = [{ agent_id: "a1", st: "st-ok" }, { agent_id: "a2", st: "st-run" }];
  const rows = [...ses.stream.children];
  // the feed is newest-top, so the scene's specs land reversed:
  // [mail, bLaunch(a2), aResult(a1), aLaunch(a1)]
  rows[2].dataset.bad = "1";        // ONE of a1's two rows carries a failing op
  sandbox.tintAgentNotes();
  return rows.map(c => [c.dataset.agent || "-", c.dataset.out || "-"]);
}

// ---- and the QUIET COMMAND HEADER: the served pieces (opshtml.cmd_note) must land
// in their slots — the words beside the command, the closing duration AFTER it, the
// ⧉ links out of the line — and the line's dot must follow the outcome. Drives the
// real createBlock/fillBlock, the only place that routing exists.
function quietScene(closer) {
  sandbox.S = { ses: { blocks: new Map(), fgRun: null, stream: new El("div", "stream") } };
  const b = sandbox.createBlock();
  const q = (quiet, html, links) => ({ g: "t1", t: "label", quiet, html, links,
                                       bad: quiet === "close" && closer === "bad" ? 1 : 0 });
  sandbox.fillBlock(b, q("open", '<span class="anmark">D</span>',
                         '<span class="cl">L</span>'));
  sandbox.fillBlock(b, { g: "t1", t: "code", html: "make test" });
  const mid = { quiet: b.root.dataset.quiet, out: b.root.dataset.out };
  if (closer)
    sandbox.fillBlock(b, q("close", '<span class="cqt">finished</span>'));
  return { running: mid,
           quiet: b.root.dataset.quiet, out: b.root.dataset.out,
           sum: b.sum.textContent,
           chips: b.chips.textContent, links: b.links.childElementCount,
           tail: b.tail.querySelector(".cqt") ? "cqt" : "-",
           // the closer's words are NOT in the chips row (they'd land before the
           // command, where a duration reads as nothing)
           tailInChips: b.chips.textContent.includes("cqt") };
}

// ---- and the LIVE ⏱ on a running foreground command, against the ORDER that broke
// it: the `fgrun` event arms the ticker BEFORE the block's own ops arrive (a faster
// cadence — cmd_pre writes the record and the `▶ foreground` op in one hook run), so
// the opener lands with `fgRun.g` already matching. It must not retire the ticker; only
// the `■ finished` CLOSER may. Drives the real setFgRun / fillBlock / tickFgElapsed.
function fgScene(order) {
  const ses = scene("verbose", [F.prompt]);
  ses.blocks = new Map();
  const b = sandbox.createBlock();
  ses.stream.insertBefore(b.root, ses.stream.children[0]);
  ses.blocks.set("t1", b);
  const opener = { g: "t1", t: "label", quiet: "open",
                   html: '<span class="anmark">D</span>' };
  const arm = () => sandbox.setFgRun({ g: "t1", start_ts: Date.now() / 1000 - 64 });
  const ops = () => sandbox.fillBlock(b, opener);
  if (order === "fgrun-first") { arm(); ops(); } else { ops(); arm(); }
  sandbox.tickFgElapsed();                       // the 1s tick that paints the number
  const live = b.root.querySelector(".blive");
  const before = { text: live ? live.textContent : "-",
                   inTail: !!(live && live.parentNode === b.tail) };
  // …and the finish chip retires it, replacing the ticking number with the real one
  sandbox.fillBlock(b, { g: "t1", t: "label", quiet: "close",
                         html: '<span class="cqt">finished · 1m04s</span>' });
  return { live: before,
           afterFinish: !!b.root.querySelector(".blive"),
           tail: b.tail.querySelector(".cqt") ? "cqt" : "-" };
}

// ---- and a CLICK-TO-VIEW panel (an Update's diff, a Read's content), the one
// top-level stream child that is not an item: it carries no `data-kind`, so no pass
// sees it. It is a SATELLITE of the row it was opened from, tied to it by DOM
// ADJACENCY alone — which is the invariant the stylesheet's `.vhide + .view-block`
// rule leans on. So: the host is hidden by the mode, the panel is NOT (nothing marks
// it), and it is STILL the host's next sibling after a full re-pass has removed and
// re-inserted the summary lines around it.
function satelliteScene(mode) {
  const ses = scene(mode, [F.prompt, F.upd, F.fg, F.reply]);
  const row = ses.stream.children.find(c => c.dataset.act === "edit");
  const panel = new El("div", "view-block");
  ses.stream.insertBefore(panel, row.nextElementSibling);   // insertAdjacentHTML afterend
  ses.viewSig = "";                       // force a real pass with the panel in place
  sandbox.applyViewMode();
  return { hostHidden: row.classList.contains("vhide"),
           adjacent: row.nextElementSibling === panel,
           panelIsItem: !!panel.dataset.kind,
           panelMarked: panel._cls().filter(c => c !== "view-block") };
}

runFill("focus", "focus", 40)
  .then(() => runFill("verbose", "verbose", 40))
  .then(() => runFill("allCommands", "focus", 40, { mix: false }))
  .then(() => runFill("exhausted", "focus", 40, { mix: false, exhaustAfter: 2 }))
  .then(orderScene)
  .then(() => switchScene("focus")).then(r => { out.switchFocus = r; })
  .then(() => switchScene("verbose")).then(r => { out.switchVerbose = r; })
  .then(() => { out.dots = dotScene(); })
  .then(() => { out.quietRun = quietScene(null); })
  .then(() => { out.quietOk = quietScene("ok"); })
  .then(() => { out.quietBad = quietScene("bad"); })
  .then(() => { out.fgLiveArmedFirst = fgScene("fgrun-first"); })
  .then(() => { out.fgLiveOpsFirst = fgScene("ops-first"); })
  .then(() => { out.satelliteFocus = satelliteScene("focus"); })
  .then(() => { out.satelliteDefault = satelliteScene("default"); })
  .then(() => {
    out.fills = fills;
    process.stdout.write(JSON.stringify(out, null, 1));
  });
