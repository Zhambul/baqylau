"use strict";
// Executes the REAL dashboard-extension JS registry (extRegister/extTabs/
// extPage/showExtPage + the chromeBody dispatch in app.11-chrome.js) over the
// shared DOM shim with a FAKE extension, and prints one JSON verdict object.
// This is the executable spec an extension author codes against
// (docs/dashboard.md *Web extensions*): the SECTIONS defaults a registration
// gets by convention, the tab-strip insertion + scope gating, the body
// dispatch, and the #/x/<route> top-level page hook — none of it greppable.
//
// Usage: node tests/jsdom/ext.js dashboard/static/app.11-chrome.js
// SKIPPED when `node` is absent (docs/testing.md) — never a build requirement.

const fs = require("fs");
const vm = require("vm");
const { El, domGlobals } = require("./domshim");

const sandbox = {
  console, JSON, Object, Array, Promise, Set, Map, RegExp, Date, Math,
  setInterval: () => 1, clearInterval: () => {},
  setTimeout: () => 1, clearTimeout: () => {},
  ...domGlobals(),
  encodeURIComponent,
  $view: new El("div"),
  S: { cur: "sid1", ses: null },
  fetch: () => Promise.resolve({ json: () => Promise.resolve({}) }),
};
sandbox.document.addEventListener = () => {};
sandbox.document.title = "";
sandbox.window = sandbox;
sandbox.window.addEventListener = () => {};
vm.createContext(sandbox);
vm.runInContext(process.argv.slice(2).map(p => fs.readFileSync(p, "utf8")).join("\n")
                + "\n;globalThis.SECTIONS = SECTIONS; globalThis.EXT = EXT;",
                sandbox, { filename: process.argv[2] });

const calls = { body: 0, page: 0, stash: 0 };

// a fake extension exercising every hook an extension may declare
sandbox.extRegister({
  name: "fake", label: "fakes", after: "monitors",
  scopeField: "fake_scope", scoped: false,
  init: (ses) => { ses.fake = null; ses.fakeExtra = 1; },
  body: () => { calls.body += 1; },
  section: { stash: () => { calls.stash += 1; } },
  page: { route: "fk", title: "fake page",
          render: (wrap) => { calls.page += 1; wrap.append(new El("div", "fk")); } },
});

const sec = sandbox.SECTIONS.fake;

/* the tab strip's insertion: record what mk() is asked to build per anchor */
function tabsFor(meta, ses) {
  const made = [];
  const mk = (key, label, count) => { made.push([key, label, count | 0]); return new El("a"); };
  for (const anchor of ["mirror", "agents", "monitors", "jobs"])
    sandbox.extTabs(ses, meta, mk, anchor);
  return made;
}

/* the chromeBody dispatch — an EXT tab routes to the descriptor's body() */
const ses = { fake: null };
sandbox.S.ses = ses;
sandbox.chromeBody(ses, "fake", new El("div"));

/* the top-level page — the router's #/x/<route> arm resolves + renders */
const hit = sandbox.extPage("fk");
if (hit) sandbox.showExtPage(hit);

const out = {
  // the by-convention SECTIONS defaults a bare registration inherits
  section: { api: sec.api, list: sec.list, tabEl: sec.tabEl,
             countField: sec.countField, label: sec.label, scoped: sec.scoped },
  // in scope: the tab is built at its anchor with the eager served count;
  // off scope (no fake_scope in meta): no tab at all
  tabsInScope: tabsFor({ fake_scope: true, fake_count: 7 }, { }),
  tabsOffScope: tabsFor({}, {}),
  // once the list is fetched, its LENGTH beats the served count
  tabsFetched: tabsFor({ fake_scope: true, fake_count: 7 }, { fake: [1, 2] }),
  bodyCalls: calls.body,
  pageCalls: calls.page,
  pageTitle: sandbox.document.title,
  pageMiss: sandbox.extPage("nope"),
  extListNames: sandbox.extList().map(x => x.name),
};

process.stdout.write(JSON.stringify(out, null, 1));
