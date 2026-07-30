"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.

function armJump(cwd, resumeSid, o) {
  o = o || {};
  S.jumpDone = null;               // a new launch supersedes a stale forward
  S.jump = { cwd, resumeSid: resumeSid || "",
             win: o.win || "",     // the launched tab's window id when the
             //                       terminal reported one — the exact match
             show: o.show || null, // what the #/launching pending view displays
             //                       ({mode, model, effort, account, prompt});
             //                       null = no pending view (composer resume)
             quiet: false,         // route() flips this on user navigation —
             //                       resolution toasts instead of navigating
             armedAt: Date.now(),  // the pending view's elapsed counter — must
             //                       survive the view unmounting/remounting
             onfail: o.onfail || null,  // called if the watch times out with no
             //                       pending view (a composer "resume & send",
             //                       whose disabled composer needs re-enabling)
             known: new Set(S.sessions.map(r => r.sid)),
             liveAtArm: new Set(S.sessions.filter(r => r.live).map(r => r.sid)),
             until: Date.now() + JUMP_TIMEOUT_MS };
  // the client half of the launch story (the server logs web-launch/-wake): when
  // we START waiting for the launched tab to appear on the list — paired with the
  // hit/timeout below it bounds "launched from web but never showed up".
  // `pend` = armed OPTIMISTICALLY, at the click, with the POST still in flight
  // (a form launch): the row then lands BEFORE this launch's `new.begin`, which
  // is how the DB shows the waiting room covered the request instead of
  // following it (the old ordering — dead air — is a `launch.arm` after `new.ok`).
  clog(resumeSid || "", "launch.arm",
       { win: o.win || "", resume: !!resumeSid, pend: !!o.pend });
}

function checkJump() {
  const j = S.jump;
  if (!j) return;
  if (Date.now() > j.until) { jumpFail(); return; }
  // the launch's window id wins when known (r.live gates out a row from a
  // previous terminal run whose window ids restarted from 1); then the
  // resumed sid itself (its cwd may differ from the launch dir); otherwise
  // any cwd-row that is brand-new or freshly parked→live
  const row = (j.win && S.sessions.find(
    r => r.live && String(r.kitty_window_id || "") === j.win))
    || (j.resumeSid && S.sessions.find(r => r.live && r.sid === j.resumeSid))
    || S.sessions.find(r => r.live && r.cwd === j.cwd
                       && (!j.known.has(r.sid) || !j.liveAtArm.has(r.sid)));
  if (!row) return;
  jumpHit(row.sid, row.title || proj(row));
}

function jumpHit(sid, title) {
  const quiet = !!(S.jump && S.jump.quiet);
  clog(sid, "launch.hit",             // the launched session appeared — with latency
       { ms: S.jump ? Date.now() - S.jump.armedAt : 0, quiet });
  S.jump = null;                       // clear FIRST — route() must never see
  //                                      this function's own hash change armed
  const to = "#/s/" + encodeURIComponent(sid);
  if (quiet) {
    // the user navigated away mid-wait — never yank them; a clickable toast
    // announces the arrival, and #/launching (browser back) forwards there
    S.jumpDone = to;
    if (S.cur === sid) return;         // they already found it themselves
    toast("done", "session started", title || "click to open",
          () => { location.hash = to; });
    return;
  }
  // from the pending view, replace: #/launching is a waiting room, not a
  // history entry worth returning to (back should land on the list)
  if (S.pendingUI) location.replace(to);
  else location.hash = to;
  toast("done", "session started", title || "");
}

function jumpFail() {
  const onfail = S.jump && S.jump.onfail;
  clog(S.jump && S.jump.resumeSid || "", "launch.timeout",   // never appeared in time
       { ms: S.jump ? Date.now() - S.jump.armedAt : 0 });
  S.jump = null;
  if (onfail) onfail();          // a composer resume: revive its dead composer
  if (S.pendingUI) showPendingFail();
}

/* ---------- the optimistic pending view (#/launching) ---------- */
// Mounted the instant a form launch POSTs ok, BEFORE the session exists
// anywhere (claude takes ~2s to boot before its SessionStart) — the wait gets
// a visible page instead of dead air on the list, and the arrival becomes a
// swap-in-place (jumpHit's location.replace) instead of a surprise yank.
// Torn down by whatever route() runs next; its ticker dies with the DOM.

const PEND_HINT_MS = 8000;             // "still waiting…" past this — claude
//                                        boot measured ~2s, so 8s is abnormal
const PEND_TICK_MS = 500;              // ticker cadence (hint + timeout watch)
let pendToolLabel = "the session";     // the launched tool's label, retained by
//                    showPending so showPendingFail can name it (S.jump is null
//                    by the time jumpFail mounts the failure card)

function showPending() {
  leaveSession();
  S.pendingUI = true;
  const j = S.jump;
  const show = j.show || {};
  $view.textContent = "";
  const card = el("div", "pendcard");
  card.append(el("div", "pendspin"));
  const verb = show.mode === "resume" ? "resuming session"
    : show.mode === "continue" ? "continuing session" : "starting session";
  card.append(el("div", "pendtitle", verb));
  card.append(el("div", "penddir", j.cwd));
  const chips = el("div", "pendchips");
  [show.account, show.model, show.effort].filter(Boolean)
    .forEach(t => chips.append(el("span", "pendchip", t)));
  if (chips.childNodes.length) card.append(chips);
  if (show.prompt) card.append(el("div", "pendprompt", show.prompt));
  const tl = show.toolLabel || "the session";
  pendToolLabel = tl;              // retained for showPendingFail (S.jump is cleared)
  const hint = el("div", "pendhint",
                  tl + " is booting in a new terminal tab — usually a couple of seconds");
  card.append(hint);
  $view.append(card);
  // the ticker only escalates the hint and fires the timeout during total
  // silence — the jump itself arrives via the SSE wake / snapshot watches.
  // Elapsed counts from armedAt (the launch), not the mount: leaving and
  // re-entering the waiting room must not reset the clock.
  const tick = setInterval(() => {
    if (!card.isConnected) { clearInterval(tick); return; }
    const jj = S.jump;
    if (!jj) { clearInterval(tick); return; }        // jumpHit navigated
    if (Date.now() > jj.until) { clearInterval(tick); jumpFail(); return; }
    const waited = Date.now() - jj.armedAt;
    if (waited > PEND_HINT_MS)
      hint.textContent = "still waiting… (" + Math.round(waited / 1000)
        + "s) — check the terminal tab if this goes on";
  }, PEND_TICK_MS);
}

function showPendingFail() {
  if (!S.pendingUI) return;
  $view.textContent = "";
  const card = el("div", "pendcard fail");
  card.append(el("div", "pendtitle", "✗ the session never appeared"));
  card.append(el("div", "pendhint",
                 pendToolLabel + " may have failed to start — check the terminal tab"));
  const back = el("button", "nsbtn", "back to sessions");
  back.onclick = () => { location.hash = "#/"; };
  card.append(back);
  $view.append(card);
}

/* ---------- control plane: the new-session form ---------- */
// Lives in the persistent #modal host (outside #view) so a list re-render from
// an SSE snapshot never blows away a half-typed form. Directory input backed by
// suggest() over nsSuggestDirs() — the current snapshot's distinct PROJECT
// directories, worktree cwds folded into their main checkout and scratch
// (`/tmp`) paths dropped; optional first
// prompt; submit POSTs /api/sessions/new and the session appears on its own via
// SessionStart. The header "+ session" button opens it blank; a dir group's "+"
// prefills that cwd.

// The resume preview popup lives OUTSIDE $modal (on document.body, above the
// form), so tearing down the form must also dismiss any open popup + its
// capturing Esc handler — set by resumePicker while a popup is up, else null.
let resumePreviewCleanup = null;

function closeNewSession() {
  if (resumePreviewCleanup) resumePreviewCleanup();
  stopDictation();               // the form's mic dies with the form
  // persist the half-typed prompt NOW, debounce bypassed — this is the very
  // gesture (cancel / Esc / a stray backdrop click) that used to lose it, and
  // the textarea is about to stop existing. A successful launch reaches here
  // with the box already emptied, so the same flush writes the clear. Skipped
  // when the box already matches what we last stored (opened and closed
  // untouched): no pointless write, no audit-row noise — and any still-pending
  // debounced save is left to fire, since it carries exactly that same text.
  if (nsPromptBox) {
    const now = nsPromptBox.value.trim() ? nsPromptBox.value : "";
    if (now !== nsDraftFor(nsDraftDir).text)
      saveNsDraft(nsDraftDir, nsPromptBox.value, true);
  }
  nsPromptBox = null;
  $modal.hidden = true;
  $modal.textContent = "";
  document.body.classList.remove("modal-open");   // release the scroll lock
}

// Custom dropdown replacing the form's native <select>s — Safari ignores most
// select styling and always opens the native white macOS popup, which clashes
// with the theme. This renders both the closed control and the open list in
// the dashboard's own language (the cmenu pattern). API shaped for the call
// sites: value get/set, fill() (rebuild, keep the current value if it
// survives, else fall back to the first option), has()/add() for the
// resumeSid injection.
function dropdown() {
  const root = el("div", "nsdrop");
  const btn = el("button", "nsinput nsdropbtn");
  btn.type = "button";
  const lab = el("span", "nsdroplab");
  btn.append(lab, el("span", "nsdropcaret", "▾"));
  const menu = el("div", "nsdropmenu");
  menu.hidden = true;
  root.append(btn, menu);

  let items = [];                      // [{v, txt}]
  let val = "";
  let hi = -1;                         // highlighted index while open
  const label = () => {
    const it = items.find(i => i.v === val);
    lab.textContent = it ? it.txt : "";
  };
  const paint = () => {
    menu.textContent = "";
    items.forEach((it, i) => {
      const row = el("div", "nsdropitem" + (i === hi ? " sel" : ""), it.txt);
      row.onmousedown = (e) => e.preventDefault();   // keep btn focus → no blur
      // preventDefault: the .nsfield wrapper is a <label>, and a click's
      // default action forwards label activation to the button — which would
      // re-toggle the menu open right after choose() closed it
      row.onclick = (e) => { e.preventDefault(); choose(i); };
      menu.append(row);
    });
  };
  const nudge = () => {
    const sel = menu.querySelector(".sel");
    if (sel) sel.scrollIntoView({ block: "nearest" });
  };
  const close = () => { menu.hidden = true; };
  const open = () => {
    hi = Math.max(0, items.findIndex(i => i.v === val));
    paint();
    menu.hidden = false;
    nudge();
    btn.focus();   // Safari doesn't focus a clicked <button> — without this a
    //                mouse-open menu gets no keyboard nav and Escape falls
    //                through to the document handler, closing the whole modal
  };
  const choose = (i) => {
    if (items[i]) { val = items[i].v; label(); if (api.onpick) api.onpick(val); }
    close();
  };
  btn.onclick = () => (menu.hidden ? open() : close());
  btn.onblur = close;
  btn.onkeydown = (e) => {
    if (menu.hidden) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        open();
      }
      return;
    }
    if (e.key === "ArrowDown") { e.preventDefault(); hi = Math.min(items.length - 1, hi + 1); paint(); nudge(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); hi = Math.max(0, hi - 1); paint(); nudge(); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(hi); }
    // stopPropagation: the document-level Escape closes the whole modal
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
  };

  const api = {
    el: root,
    onpick: null,   // called with the value on a USER pick (not on fill/set)
    fill(pairs) {
      items = pairs.map(([v, txt]) => ({ v, txt }));
      if (!items.some(i => i.v === val)) val = items[0] ? items[0].v : "";
      label();
      if (!menu.hidden) { hi = Math.max(0, items.findIndex(i => i.v === val)); paint(); }
    },
    add(v, txt) { items.push({ v, txt }); },
    has: (v) => items.some(i => i.v === v),
    get value() { return val; },
    set value(v) { val = v; label(); },
  };
  return api;
}

// Last-used launch prefs (directory/model/effort) — preselected the next time
// the form opens (launches are usually the same project on the same settings).
// STORED ON THE BACKEND now (the durable global prefs DB, GET/POST /api/ns-prefs)
// instead of per-browser localStorage, so a launch on one device pre-selects on
// the next — S.nsPrefs is the in-memory cache (fetched once at boot, refreshed
// on every write) that keeps this read synchronous. The BEHAVIOUR is unchanged:
// written only on a successful launch; an explicit prefill (a dir group's "+",
// a resume button) still wins over the remembered directory.
const nsLast = () => S.nsPrefs || {};
// The selections of a launch whose POST FAILED, consumed by the very next
// openNewSession (which the failure path opens itself). Since the waiting room
// now mounts on the click, a rejected launch has already torn the form down —
// this is what makes the re-opened form the one you submitted (model/effort/
// account; the directory and resume row ride openNewSession's own arguments and
// the prompt its draft) rather than a blank last-used one. Never persisted: it
// is one retry's worth of state, not a preference.
let nsRetry = null;
const nsRemember = (p) => {
  S.nsPrefs = p;                                   // cache first, form is sync
  postJSON("/api/ns-prefs", p).catch(() => {});    // best-effort backend write
};

// The form's UNSENT first prompt is a DRAFT (docs/dashboard.md, *New-session
// draft*) — the composer's `composer-draft` machinery for the one box that has
// no session to hang a per-session kv on yet, so it lives in the same durable
// GLOBAL prefs store as nsLast() (GET/POST /api/ns-draft). Written debounced on
// every edit AND flushed on close: an accidental Esc / backdrop click used to
// drop a half-typed prompt on the floor, and the next open came up blank.
// Cleared by the launch that consumes it.
//
// PER DIRECTORY (`{cwd: {text, seq}}`): different projects hold different
// half-typed prompts, so the box always shows the draft belonging to the
// directory in the form. `nsDraftDir` is WHICH directory the box's text
// currently belongs to (settled on the dir field's blur, not on every keystroke
// of a half-typed path — see settleDraftDir), and `nsPromptBox` is the open
// form's textarea (null while closed): the close flush's handle on the text,
// since closeNewSession tears the DOM down.
let nsPromptBox = null;
// the launched tool's display label ("Codex" / "Claude"), kept current by
// syncTool so the prompt placeholder names the picked host instead of always
// "Claude"; syncTool runs on the initial fill (before nsPromptBox exists) and on
// every tool switch, so nsPrompt reads this for its FIRST paint and syncTool
// repaints the live box thereafter.
let nsToolLabel = "Claude";
let nsDraftDir = "";
let nsDraftTimer = 0;
// The form's notion of "the same folder" — and, since the server stores the key
// verbatim (dashboard/prefs.py `NS_DRAFT_KEY`), the ONE implementation of it.
// "" is a legitimate key: the form opened with no directory yet.
const nsDirKey = (v) => {
  const s = (v || "").trim();
  return s.length > 1 ? s.replace(/\/+$/, "") : s;
};
const nsDraftFor = (cwd) =>
  (S.nsDrafts && S.nsDrafts[nsDirKey(cwd)]) || { text: "", seq: 0 };
function saveNsDraft(cwd, text, now) {
  const key = nsDirKey(cwd);
  const t = text.trim() ? text : "";
  S.nsDrafts[key] = { text: t, seq: Date.now() };   // cache: the next open (and
  //                    a directory switch) seeds from here, no round-trip
  clearTimeout(nsDraftTimer);
  // seq is stamped at DISPATCH (like saveComposerDraft): a debounced save still
  // in flight when the launch clears the box must not resurrect the sent prompt
  // if it arrives later over the tunnel — the server keeps only the highest seq
  // (per directory, so two folders' saves never fight). The pending post
  // captures its OWN cwd+text, since the form's directory may have moved on.
  const post = () => postJSON("/api/ns-draft",
                              { cwd: key, text: t, seq: Date.now() })
    .catch(() => {});
  if (now) post();
  else nsDraftTimer = setTimeout(post, ASK_DRAFT_DEBOUNCE_MS);
}

// WHAT the directory picker offers: the snapshot's distinct PROJECT directories
// (groupKey — a linked-worktree cwd resolves to its owning main checkout), minus
// SCRATCH paths. A `/tmp` anywhere in the path is scratch: the hermetic test
// suite's per-test dirs, `mktemp -d` throwaway checkouts, `$TMPDIR` (macOS
// `/var/folders/…/T/tmpXXXXXX`, which is what a realpath'd cwd spells) — all
// long gone by the time anyone would click them, and they crowded the real
// projects out of the menu. Accepted false positive: a genuine project under a
// `/tmp`-prefixed component (`~/code/tmpl`) is menu-invisible — the field is
// freeform, so typing or pasting the path still launches there; the list is a
// shortcut, never the only way in.
const NS_SCRATCH = /\/tmp/;
const nsSuggestDirs = (rows) => [...new Set(
  rows.map(r => groupKey(r)).filter(d => d && !NS_SCRATCH.test(d)))];

// Freeform text input + picker menu — replaces the directory field's
// <datalist>, which Safari renders in the system style AND pops open on
// focus (the "somehow already clicked" look). Same visual language as
// dropdown() (.nsdropmenu/.nsdropitem). A click opens the menu: with the
// current value blank or an exact known entry it lists EVERYTHING (the
// picker look, current value highlighted); while typing it filters by
// substring. ↑/↓ move, Enter picks the highlighted row — unless that row IS
// already the value, where it closes and falls through to the caller's
// Enter = launch — Esc closes the menu only. Caller calls sug.key(e) FIRST
// in onkeydown.
function suggest(input, all) {
  const menu = el("div", "nsdropmenu");
  menu.hidden = true;
  let items = [], hi = -1, squelch = false;
  const close = () => { menu.hidden = true; hi = -1; };
  const paint = () => {
    menu.textContent = "";
    items.forEach((v, i) => {
      const row = el("div", "nsdropitem" + (i === hi ? " sel" : ""), v);
      row.onmousedown = (e) => e.preventDefault();   // keep input focus
      row.onclick = (e) => { e.preventDefault(); pickRow(i); };
      menu.append(row);
    });
    const sel = menu.querySelector(".sel");
    if (sel) sel.scrollIntoView({ block: "nearest" });
  };
  const open = () => {
    const cur = input.value.trim();
    const exact = !cur || all.includes(cur);
    items = exact ? all.slice()
                  : all.filter(v => v.toLowerCase().includes(cur.toLowerCase()));
    if (!items.length) { close(); return; }
    hi = items.indexOf(cur);            // -1 unless the value is a known entry
    paint();
    menu.hidden = false;
  };
  const pickRow = (i) => {
    input.value = items[i];
    squelch = true;                     // the input event below must not reopen
    input.dispatchEvent(new Event("input"));
    squelch = false;
    close();
  };
  // deliberately NO focus→open: the form auto-focuses this field when blank,
  // and a menu that pops without a pointer action reads as "already clicked".
  // Open only on an actual click, typing, or ArrowDown.
  input.addEventListener("input", () => { if (!squelch) open(); });
  input.addEventListener("click", () => { if (menu.hidden) open(); });
  input.addEventListener("blur", close);
  const key = (e) => {
    if (menu.hidden) {
      if (e.key === "ArrowDown") { e.preventDefault(); open(); return true; }
      return false;
    }
    if (e.key === "ArrowDown") { e.preventDefault(); hi = Math.min(items.length - 1, hi + 1); paint(); return true; }
    if (e.key === "ArrowUp") { e.preventDefault(); hi = Math.max(0, hi - 1); paint(); return true; }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); return true; }
    if (e.key === "Enter") {
      if (hi >= 0 && items[hi] !== input.value.trim()) {
        e.preventDefault();
        pickRow(hi);
        return true;
      }
      close();
      return false;                     // fall through to the caller's launch
    }
    return false;
  };
  return { el: menu, key };
}

// The resume picker's PREVIEW POPUP — a component of its own, with its own
// state (which session is shown, the mounted backdrop, the per-sid item cache)
// and its own Escape handling. It was 75 lines inside resumePicker's 240-line
// closure, sharing that scope only to reach `rows` and `list`; both are handed
// in now, so what the popup owns is visible at a glance and the picker is back
// to being about the LIST.
//
// `rowFor(sid)` gives the row record (for the title); `focusList()` returns
// focus to the picker when the popup closes.
function resumePreview(rowFor, focusList) {
  let pvSid = "", pvBack = null;
  const pvCache = new Map();

  // A POPUP WINDOW over the form (a roomy, readable overlay — the inline panel
  // was too cramped to read). It stacks above the new-session modal (.nspvback
  // z-index > .nsback) and owns its own Escape/close so the form's
  // document-level Esc handler doesn't fire underneath it
  // (resumePreviewCleanup + a capturing keydown that stopPropagation()s).
  // Closing returns focus to the row that opened it — focusList().
  const close = () => {
    if (!pvBack) return;
    document.removeEventListener("keydown", pvKey, true);
    pvBack.remove();
    pvBack = null;
    pvSid = "";
    resumePreviewCleanup = null;
    focusList();
  };
  const pvKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
  };

  const show = (sid) => {
    if (pvBack && pvSid === sid) {                  // space again on the same row closes
      clog(sid, "resume.preview", { shown: 0 });
      close();
      return;
    }
    close();                                       // switching rows: replace the popup
    pvSid = sid;
    const r = rowFor(sid);
    const title = (r && r.title) || shortSid(sid);
    pvBack = el("div", "nspvback");
    const panel = el("div", "nspvpanel");
    const head = el("div", "nspvhead");
    head.append(el("span", "nspvtitle", "preview · " + title));
    const x = el("button", "nspvx", "✕");
    x.title = "close (Esc)";
    x.onclick = close;
    head.append(x);
    const body = el("div", "nspvbody");
    body.append(el("div", "nspreview-empty", "loading…"));
    panel.append(head, body);
    pvBack.append(panel);
    pvBack.onclick = (e) => { if (e.target === pvBack) close(); };
    document.body.append(pvBack);
    document.addEventListener("keydown", pvKey, true);   // preempt the form's Esc
    resumePreviewCleanup = close;                        // form-close safety net
    x.focus();                                           // so Esc/tab live in the popup

    const render = (items) => { if (pvSid === sid && pvBack) renderPreview(body, items); };
    if (pvCache.has(sid)) {
      const items = pvCache.get(sid);
      // record the item COUNT, not just "shown" — an empty-but-successful preview
      // ("no mirror history") is otherwise indistinguishable in the audit from a
      // rendered one (the blind spot that made the last diagnosis need a repro).
      clog(sid, "resume.preview", { shown: 1, cached: 1, n: items.length });
      render(items);
      return;
    }
    // the recent mirror TAIL is /backlog (the newest TAIL_BLOCKS slice, the
    // mirror tab's own on-load call) — NOT /history, which returns blocks OLDER
    // than a cursor (before=0 → nothing: the "no mirror history" bug).
    fetch("/api/session/" + encodeURIComponent(sid) + "/backlog")
      .then(rp => rp.json())
      .then(d => {
        const items = (d && d.items) || [];
        pvCache.set(sid, items);
        clog(sid, "resume.preview", { shown: 1, cached: 0, n: items.length });
        render(items);
      })
      .catch(() => {
        clog(sid, "resume.preview.fail", {});
        if (pvSid !== sid || !pvBack) return;
        body.textContent = "";
        body.append(el("div", "nspreview-empty", "preview unavailable"));
      });
  };

  return { show, close, get sid() { return pvSid; } };
}


// The new-session resume picker (docs/dashboard.md *Resume picker*): a search
// box + a scrollable list of a directory's recent sessions (GET /api/resumable,
// up to RESUMABLE_MAX), each row carrying the session's model/effort/account. It
// replaces the old three-way "start from" dropdown's resume entries — no
// `--continue`, resuming the most-recent row IS "continue". Keyboard: ArrowDown
// from the search box drops into the list, ↑/↓ move between rows, Enter selects,
// and SPACE toggles an inline mirror-transcript preview of the highlighted row
// (renderPreview over /history). `onSelect(row)` fires on every selection so the
// caller can reuse the picked session's model+effort. `value()` is the chosen
// sid ("" = nothing picked).
function resumePicker() {
  const root = el("div", "nsresume");
  const search = el("input", "nsinput nsressearch");
  search.type = "text";
  search.spellcheck = false;
  search.placeholder = "search all sessions in this directory…";
  const hint = el("div", "nsreshint", "↑↓ navigate · space previews · enter picks");
  const list = el("div", "nsreslist");
  root.append(search, hint, list);

  let rows = [], selSid = "", lastCwd = "", qToken = 0;

  const paint = () => {
    list.textContent = "";
    if (!rows.length) {
      list.append(el("div", "nsresempty",
        search.value.trim() ? "no match" : "no sessions to resume here"));
      return;
    }
    for (const r of rows) {
      const row = el("div", "nsresrow"
        + (r.sid === selSid ? " sel" : "") + (r.live ? " live" : ""));
      row.tabIndex = 0;
      row.dataset.sid = r.sid;
      row.append(el("div", "nsrestitle", r.title || shortSid(r.sid)));
      const meta = el("div", "nsresmeta");
      const fam = shortModel(r.model);
      if (fam) meta.append(el("span", "nsreschip", fam));
      if (r.effort) meta.append(el("span", "nsreschip", r.effort));
      if (r.account && r.account.label)
        meta.append(el("span", "nsreschip", r.account.label));
      if (r.live) meta.append(el("span", "nsreschip live", "live"));
      const when = lastActive(r) ? ago(lastActive(r)) : "";
      if (when) meta.append(el("span", "nsresago", when));
      row.append(meta);
      row.onclick = () => { choose(r.sid); row.focus(); };
      row.onkeydown = (e) => rowKey(e, r);
      list.append(row);
    }
  };

  // update ONLY the selected-row highlight, in place — a full paint() would
  // recreate the row elements and DROP keyboard focus, so space (preview) and
  // the arrow keys would land nowhere after a pick (the "space did nothing" bug).
  const preview = resumePreview(
    (sid) => rows.find(x => x.sid === sid),
    () => {
      const r = list.querySelector(".nsresrow.sel") || list.querySelector(".nsresrow");
      if (r) r.focus();
    });
  const showPreview = (sid) => preview.show(sid);
  const closePreview = () => preview.close();

  const applySel = () => {
    for (const row of list.querySelectorAll(".nsresrow"))
      row.classList.toggle("sel", row.dataset.sid === selSid);
  };

  const choose = (sid) => {
    selSid = sid;
    applySel();
    const r = rows.find(x => x.sid === sid);
    if (!r) return;
    // audit the pick so a "resumed with the wrong model/effort/account" report
    // is reconstructible from the DB (docs/dashboard.md *Resume picker*): the sid
    // chosen + the model/effort/account it CARRIED (what onSelect reuses).
    clog(sid, "resume.pick", {
      model: r.model || "", effort: r.effort || "",
      account: (r.account && r.account.slug) || "", live: !!r.live });
    if (api.onSelect) api.onSelect(r);
  };

  const rowKey = (e, r) => {
    const rowEl = e.currentTarget;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (rowEl.nextElementSibling) rowEl.nextElementSibling.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (rowEl.previousElementSibling) rowEl.previousElementSibling.focus();
      else search.focus();
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(r.sid);
    } else if (e.key === " ") {
      e.preventDefault();                          // space PREVIEWS, never scrolls
      showPreview(r.sid);
    }
    // Escape with the popup open is handled by the popup's own capturing handler;
    // Escape with no popup falls through to the form's close (the expected Esc).
  };

  // Search is SERVER-SIDE (across the directory's whole history, not just the
  // loaded rows — the client-side filter over ≤RESUMABLE_MAX rows couldn't reach
  // an old session): debounced refetch with ?q=, preserving the selection.
  // debounced per keystroke; the two boxes differ deliberately — search is a
  // cheap history query you want to feel instant, a directory change reloads the
  // whole picker (and a path is typed in bursts), so it waits a little longer.
  const SEARCH_DEBOUNCE_MS = 200;
  let qTimer = 0;
  search.oninput = () => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => api.refresh(lastCwd, "", search.value.trim()),
                        SEARCH_DEBOUNCE_MS);
  };
  search.onkeydown = (e) => {
    const first = list.querySelector(".nsresrow");
    if (e.key === "ArrowDown" && first) { e.preventDefault(); first.focus(); }
    else if (e.key === "Enter" && first) { e.preventDefault(); choose(first.dataset.sid); }
  };

  const api = {
    el: root,
    onSelect: null,
    value: () => selSid,
    // focus the current row (arrows/space work at once) — never the search box,
    // which would pop the iPad keyboard; the search is a click away.
    focus() {
      const r = list.querySelector(".nsresrow.sel") || list.querySelector(".nsresrow");
      if (r) r.focus();
    },
    // focus the SEARCH box so you can type a query the instant the picker
    // reveals (the plain "resume a conversation" untoggle) — but NOT on an
    // iPad, where focusing an input pops the on-screen keyboard over the list
    // (the same reason focus() lands on a row); there we fall back to a row.
    focusSearch() {
      if (IS_IPAD) return api.focus();
      search.focus();
      search.select();
    },
    // (re)load the directory's rows (optionally filtered by `q`); `preferSid`
    // preselects a specific session (the ↻ resume target), else the current pick
    // if still present, else — for the UNFILTERED list only — the most-recent row
    // (so the default resume IS "continue the most recent"). `andFocus` focuses
    // after the load (the initial resume-open): "search" → the query box (type
    // to search at once), any other truthy value → the selected row.
    refresh(cwd, preferSid, q, andFocus) {
      lastCwd = cwd || "";
      q = (q || "").trim();
      closePreview();                            // a reload dismisses any open popup
      list.textContent = "";
      list.append(el("div", "nsresempty", "loading…"));
      const tok = ++qToken;                        // ignore a stale fetch's result
      fetch("/api/resumable?cwd=" + encodeURIComponent(cwd || "")
            + (q ? "&q=" + encodeURIComponent(q) : ""))
        .then(r => r.json())
        .then(data => {
          if (tok !== qToken) return;              // a newer search superseded this
          rows = Array.isArray(data) ? data : [];
          // audit the load — a "picker was empty / didn't show my session"
          // report is answerable from the DB (cwd + query + row count).
          clog("", "resume.list", {
            cwd: cwd || "", q, n: rows.length, prefer: preferSid || "" });
          const want = (preferSid && rows.some(x => x.sid === preferSid)) ? preferSid
            : (selSid && rows.some(x => x.sid === selSid)) ? selSid
              : (!q && rows[0] ? rows[0].sid : "");   // auto-pick newest only unfiltered
          selSid = "";
          paint();
          if (want) choose(want);                  // applySel + onSelect, no repaint
          // andFocus === "search" focuses the query box (type-to-search at once);
          // any other truthy value focuses the selected row.
          if (andFocus === "search") api.focusSearch();
          else if (andFocus) api.focus();
        })
        .catch(() => {
          if (tok !== qToken) return;
          clog("", "resume.list.fail", { cwd: cwd || "", q });
          rows = []; selSid = ""; paint();
        });
    },
  };
  return api;
}

// ---- the new-session form, in named phases -----------------------------------
// openNewSession built the whole modal in one 344-line function — seven field
// rows, their cross-wiring, the draft machinery and the launch, in a single
// scope where every closure could see every local. That is the shape the Python
// side is not allowed to have (docs/styleguide.md, *Module shape*: "long entry
// main()s are named phases" — small functions named for what they do, sharing
// ONE mutable context object), and there is no reason the page should.
//
// `F` is that context: each phase builds its own rows as plain locals — every
// statement inside a phase is what it always was — and publishes onto F only
// what a LATER phase needs. What a phase consumes it destructures at the top,
// so the dependency between phases is a readable line rather than "somewhere in
// the enclosing 344 lines". The one reference that cannot be destructured is
// nsPrompt's Enter handler calling `F.go()`: the launch is defined two phases
// later, and the closure resolved it at press time too.

function nsDirField(F) {
  const { prefillCwd, last } = F;

  // every picker/input row is a DIV, not a <label>: label activation forwards
  // any click on the row (title included) into the field — focusing it (or
  // toggling a dropdown) and making it impossible to defocus by clicking
  // beside the field; only the prompt row keeps the <label> (focusing a
  // textarea from its title is harmless and standard)
  const dirRow = el("div", "nsfield");
  dirRow.append(el("span", "nslabel", "directory"));
  const dir = el("input", "nsinput");
  dir.type = "text";
  dir.spellcheck = false;
  dir.placeholder = "/path/to/project";
  dir.value = prefillCwd || last.cwd || "";
  const sug = suggest(dir, nsSuggestDirs(S.sessions));
  dirRow.append(dir, sug.el);
  Object.assign(F, { dirRow, dir, sug });
}

function nsConversation(F) {
  const { dir, resumeSid } = F;

  // conversation: FRESH (a new conversation, the default) or RESUME one of this
  // directory's recent sessions. The old three-way "start from" dropdown is split
  // into a fresh toggle + a searchable, scrollable resume picker (resumePicker,
  // docs/dashboard.md *Resume picker*): there is no `--continue` — resuming the
  // most-recent row IS "continue". A resumed conversation forks to a new sid; the
  // adopt machinery and the jump watch handle that on their own. The picker rows
  // carry each session's model/effort/account (GET /api/resumable); selecting one
  // reuses its model+effort (the account still load-balances via autoAcct).
  const picker = resumePicker();
  const resumeRow = el("div", "nsfield nsresumerow");
  resumeRow.append(el("span", "nslabel", "resume"), picker.el);

  const freshRow = el("div", "nsfield");
  freshRow.append(el("span", "nslabel", "start"));
  const freshWrap = el("label", "nsswitch");
  const fresh = el("input");
  fresh.type = "checkbox";
  fresh.checked = !resumeSid;                 // ↻ resume opens straight to the picker
  const freshTxt = el("span", "nsswitchtxt");
  freshWrap.append(fresh, el("span", "nsslider"), freshTxt);
  freshRow.append(freshWrap);
  let pickerLoaded = false;
  const syncFresh = () => {
    freshTxt.textContent = fresh.checked
      ? "fresh conversation" : "resume a conversation";
    resumeRow.style.display = fresh.checked ? "none" : "";
    clog("", "resume.mode", { fresh: fresh.checked ? 1 : 0 });
    if (fresh.checked) return;
    if (!pickerLoaded) {                 // first reveal: load, then focus
      pickerLoaded = true;
      // a ↻ resume deep-link preselects a specific row (focus IT, ready to
      // Enter); a plain untoggle focuses the search box so you can type a
      // query with no extra click (focusSearch falls back to a row on iPad).
      picker.refresh(dir.value.trim(), resumeSid || "", "",
                     resumeSid ? true : "search");
    } else picker.focus();              // re-reveal: focus the existing selection
  };
  fresh.onchange = syncFresh;
  // reload the picker when the directory changes (debounced) — only while
  // resuming; suggest() keeps its own separate input listener (addEventListener).
  const DIR_DEBOUNCE_MS = 250;              // see SEARCH_DEBOUNCE_MS above
  let dirTimer = 0;
  dir.oninput = () => {
    if (fresh.checked) return;
    clearTimeout(dirTimer);
    pickerLoaded = true;
    dirTimer = setTimeout(() => picker.refresh(dir.value.trim(), "", "", false),
                          DIR_DEBOUNCE_MS);
  };
  Object.assign(F, { picker, resumeRow, freshRow, fresh, syncFresh });
}

// The model/effort options + first-ever defaults per HOST. codex differs from
// claude: its models are the gpt-*-codex family (a "" = "codex default" leaves
// the -m flag off so codex's own config picks), and its effort is the reasoning
// level that rides `-c model_reasoning_effort` (codex's own enum is
// low/medium/high/xhigh/max/ultra, verified against the codex binary). Every
// option is an EXPLICIT value — no "default" pseudo-option (matching Claude's
// dropdowns) so you always know what you launched. The server validates what is
// sent (MODEL_OK / EFFORTS). codex's `gpt-5-codex`/`gpt-5.1-codex` are dropped:
// they 400 on a ChatGPT account ("model not supported"), reproduced live.
const TOOL_MODELS = {
  claude_code: [["fable", "fable"], ["opus", "opus"],
                ["sonnet", "sonnet"], ["haiku", "haiku"]],
  codex: [["gpt-5.6-sol", "gpt-5.6-sol"], ["gpt-5.6-terra", "gpt-5.6-terra"],
          ["gpt-5.6-luna", "gpt-5.6-luna"], ["gpt-5.5", "gpt-5.5"],
          ["gpt-5.4", "gpt-5.4"], ["gpt-5.4-mini", "gpt-5.4-mini"]],
};
const TOOL_EFFORTS = {
  claude_code: [["low", "low"], ["medium", "medium"], ["high", "high"],
                ["xhigh", "xhigh"], ["max", "max"]],
  codex: [["low", "low"], ["medium", "medium"], ["high", "high"],
          ["xhigh", "xhigh"], ["max", "max"], ["ultra", "ultra"]],
};
const TOOL_MODEL_DEF = { claude_code: "fable", codex: "gpt-5.6-sol" };
const TOOL_EFFORT_DEF = { claude_code: "high", codex: "low" };
const toolOpts = (tbl, t) => tbl[t] || tbl.claude_code;

function nsPickers(F) {
  const { last, dir, picker, fresh, syncFresh, presetTool } = F;

  // model + effort side by side — concrete values only, no "default" entry
  // (the user always launches with explicit flags; the remembered last-used
  // value is the preselection, with a fixed first-ever fallback)
  const pick = (label, opts) => {
    const row = el("div", "nsfield");
    row.append(el("span", "nslabel", label));
    const sel = dropdown();
    sel.fill(opts);
    row.append(sel.el);
    return [row, sel];
  };

  // TOOL — which HOST to launch (claude_code / codex). Everything below FOLLOWS
  // the tool: the model/effort option sets (TOOL_MODELS/TOOL_EFFORTS) and whether
  // an account is picked at all (codex has no subscription switcher). Populated
  // from /api/hosts (cached S.hosts); the row HIDES when only one host is
  // launchable — a single-tool machine sees the form exactly as before.
  // docs/dashboard.md *Tool picker*.
  const [toolRow, tool] = pick("tool", []);
  toolRow.classList.add("nstoolrow");
  toolRow.style.display = "none";
  let toolPicked = false;
  const hostList = () => (Array.isArray(S.hosts) && S.hosts.length) ? S.hosts
    : [{ name: "claude_code", label: "Claude Code", launchable: true }];

  const [modelRow, model] = pick("model", []);   // filled per-tool by syncTool()
  const [effortRow, effort] = pick("effort", []);
  // Track whether the user has touched a picker by hand — the resume prefill and
  // the tool sync (both async / re-run) must not clobber a deliberate choice made
  // while they were in flight (same discipline as acctPicked).
  let modelPicked = false, effortPicked = false;
  effort.onpick = () => { effortPicked = true; };

  // account picker — the subscription to launch under (a switcher alias like
  // c1/c2). Populated from /api/accounts (cached in S.accts); each option shows
  // the account's latest usage inline when known, plus its active limit-hit
  // marker. No "default" option: the plain-claude login duplicates one of
  // these accounts. The row hides when there is no switcher (empty list → the
  // launch just runs plain claude).
  // The DEFAULT selection burns PERISHABLE weekly quota first (objective (b) —
  // maximise total work per week; core/sessionapi.sched_score, docs/dashboard.md
  // *Default account*): among accounts not limit-blocked for the launch AND under
  // the 5h session-safety gate (sched_ok), pick the highest sched_score — quota
  // still left whose 7d window resets soonest. Ties → registry order. It SKIPS
  // any account whose active limit-hit applies to the launch — an account-wide
  // stamp always does, a model-scoped one (limit_hit.model, e.g. a fable-only
  // limit) only when that model is the one selected, so flipping the model picker
  // re-runs the choice (that's why the model picker is built above this block).
  // Gate empties the pool → fall back to any open account; all blocked → any at
  // all. Refined again when the fresh /api/accounts fetch lands — unless the user
  // already picked by hand. Higher per-session wall risk is by design: the
  // automigrate safety net (docs/relimit.md) catches it.
  const [acctRow, acct] = pick("account", []);
  acctRow.style.display = "none";
  let acctPicked = false, acctList = [];
  acct.onpick = () => { acctPicked = true; };
  const limitBlocks = (a) =>
    a.limit_hit && (!a.limit_hit.model || a.limit_hit.model === model.value);
  // an account is picked only when there IS a switcher AND the tool uses one
  // (codex has none) — the row's visibility and the autoAcct short-circuit both
  // read this.
  const acctVisible = () => acctList.length && tool.value !== "codex";
  const autoAcct = () => {
    if (acctPicked || !acctList.length || tool.value === "codex") return;
    // never auto-select a logged-out account (its login is revoked — a launch
    // there dies on auth); fall back to the full list only if ALL are logged out
    const live = acctList.filter(a => !a.logged_out);
    const base = live.length ? live : acctList;
    const open = base.filter(a => !limitBlocks(a));
    const safe = open.filter(a => a.sched_ok);
    const pool = safe.length ? safe : (open.length ? open : base);
    acct.value = pool.reduce((b, a) => schedScore(a) > schedScore(b) ? a : b).slug;
  };
  model.onpick = () => { modelPicked = true; autoAcct(); };
  const fillAccts = (rows) => {
    // /api/accounts is the whole usage STRIP now — every host's rate-limit rows,
    // not just the switcher's accounts. Only a `switchable` row is an account
    // you can launch under, which is what this picker offers; a host-wide
    // reading (codex) carries no slug and belongs on the strip alone. Filtering
    // on the served FLAG rather than on a host name keeps the picker honest for
    // a host nobody has written yet.
    const list = (rows || []).filter(a => a.switchable);
    acctList = list;
    acctRow.style.display = acctVisible() ? "" : "none";
    acct.fill(list.map(a => {
      // every captured window rides into the option text ("5h 40% · 7d 55%
      // · 7d fable 80%") — same enumeration as the usage strip's bars
      const wins = usageWindows(a.usage);
      const usage = wins.length
        ? "  (" + wins.map(k => windowLabel(k) + " " + a.usage[k] + "%").join(" · ") + ")"
        : "";
      const lim = a.limit_hit ? "  · " + limitLabel(a.limit_hit) : "";
      const out = a.logged_out ? "  · ⚠ logged out" : "";
      return [a.slug, a.slug + " · " + a.label + usage + lim + out];
    }));
    // a RETRIED launch keeps the account it was submitted under (only nsRetry
    // sets last.account — the remembered prefs never carry one, so the normal
    // open still auto-picks); re-applied on every fill, since the async
    // /api/accounts refill rebuilds the options, and dropped the moment the
    // user picks by hand.
    if (!acctPicked && last.account && acct.has(last.account))
      acct.value = last.account;
    else autoAcct();
  };
  // Apply the current tool's option sets to model/effort and the account row's
  // visibility. Called on the initial fill, a tool switch, and a resume-row pick
  // that carries another tool. A hand-picked model/effort survives; otherwise it
  // reselects the remembered value if the new options still offer it, else the
  // tool's first-ever default.
  const syncTool = () => {
    const t = tool.value || "claude_code";
    model.fill(toolOpts(TOOL_MODELS, t));
    if (!modelPicked)
      model.value = model.has(last.model) ? last.model : (TOOL_MODEL_DEF[t] || "");
    effort.fill(toolOpts(TOOL_EFFORTS, t));
    if (!effortPicked)
      effort.value = effort.has(last.effort) ? last.effort : (TOOL_EFFORT_DEF[t] || "");
    acctRow.style.display = acctVisible() ? "" : "none";
    // name the picked host in the prompt placeholder (repaint the live box if
    // it is already open; nsPrompt reads nsToolLabel for its first paint)
    nsToolLabel = (Array.isArray(S.hosts)
                   && (S.hosts.find(h => h.name === t) || {}).label)
                  || (t === "codex" ? "Codex" : "Claude");
    if (nsPromptBox) nsPromptBox.placeholder = nsPromptPlaceholder();
    autoAcct();
  };
  tool.onpick = () => { toolPicked = true; syncTool(); };
  const fillTools = (list) => {
    const launchable = (list || []).filter(h => h && h.launchable);
    tool.fill(launchable.map(h => [h.name, h.label || h.name]));
    const want = presetTool || last.tool || "claude_code";
    if (tool.has(want)) tool.value = want;
    // one host → the picker is noise; hide the row (the launch still sends
    // tool=claude_code, the server default).
    toolRow.style.display = launchable.length > 1 ? "" : "none";
    syncTool();
  };
  fillTools(hostList());                       // tool/model/effort defaults first…
  if (!Array.isArray(S.hosts))
    fetch("/api/hosts").then(r => r.json())
      .then(list => { S.hosts = list; if (!toolPicked) fillTools(list); })
      .catch(() => {});
  if (S.accts) fillAccts(S.accts);             // …then the account pick sees them
  fetch("/api/accounts").then(r => r.json())
    .then(list => { S.accts = list; fillAccts(list); }).catch(() => {});

  // Resuming should continue where the SESSION was, not where the launcher last
  // was: on every resume-row selection, switch to the session's OWN tool (a codex
  // rollout → codex + its model/effort options) and reuse that session's model
  // (its transcript-tail model from /api/resumable) and effort (its last-applied
  // level), overriding the global last-used ns-prefs defaults — unless the user
  // has already hand-picked (toolPicked/modelPicked/effortPicked). The launch is
  // OWNER-routed server-side regardless (post_new_session), so the tool switch is
  // a UI convenience. The account is DELIBERATELY not reused: autoAcct re-runs
  // against the chosen model so the launch still load-balances.
  picker.onSelect = (r) => {
    if (r.tool && !toolPicked && r.tool !== tool.value && tool.has(r.tool)) {
      tool.value = r.tool;
      syncTool();
    }
    const fam = (shortModel(r.model) || "").split("-")[0];
    if (!modelPicked && fam && model.has(fam)) model.value = fam;
    if (!effortPicked && r.effort && effort.has(r.effort)) effort.value = r.effort;
    autoAcct();
  };
  syncFresh();                       // initial visibility + (if resuming) load
  Object.assign(F, { toolRow, tool, modelRow, model, effortRow, effort,
                     acctRow, acct });
}

function nsLayout(F) {
  const { modelRow, effortRow, acctRow } = F;

  const split = el("div", "nssplit");
  split.append(modelRow, effortRow);
  const split2 = el("div", "nssplit");
  split2.append(acctRow);
  Object.assign(F, { split, split2 });
}

// The first-prompt placeholder, naming the picked host (nsToolLabel) rather than
// always "Claude"; the terminal form adds the launch/newline hint the iPad form
// drops. Shared by nsPrompt's first paint and syncTool's live repaint.
function nsPromptPlaceholder() {
  return IS_IPAD
    ? "what should " + nsToolLabel + " start on?"
    : "what should " + nsToolLabel + " start on?  (Enter to launch · Shift+Enter for newline)";
}

function nsPrompt(F) {
  const { dir } = F;

  const promptRow = el("label", "nsfield");
  promptRow.append(el("span", "nslabel", "first prompt (optional)"));
  const prompt = el("textarea", "nsinput nsprompt");
  prompt.rows = 3;
  prompt.spellcheck = false;
  prompt.placeholder = nsPromptPlaceholder();
  // restore THIS DIRECTORY's unsent draft (an accidental close, a reload,
  // another device): synchronous from the cache, then reconciled below with a
  // fresh GET. The box belongs to the directory the form opened on until the
  // dir field settles on another one (settleDraftDir).
  nsDraftDir = nsDirKey(dir.value);
  const seeded = nsDraftFor(nsDraftDir).text;
  prompt.value = seeded;
  nsPromptBox = prompt;
  const pdic = dictation(prompt, () => dir.value.trim());
  // attachments for the initial prompt — staged under the shared "staging"
  // bucket (no sid yet); ride the launch argv as leading @-mentions
  const nsTray = attachTray(() => "");
  const nsAttach = wireAttach(nsTray, prompt, promptRow, () => true);
  const promptBox = el("div", "nsdictrow");
  promptBox.append(prompt, nsAttach, pdic.btn);
  promptRow.append(nsTray.strip, promptBox);
  // "/" completion here too — cwd-keyed to whatever directory is currently
  // typed (cached per dir, so flipping between dirs doesn't refetch)
  const cmdCache = {};
  const spm = slashMenu(prompt, promptRow,
    () => { const c = dir.value.trim(); return cmdsFor(c, cmdCache, c); },
    { enterSends: !IS_IPAD });
  // composer UX: grow with the message, Enter launches, Shift+Enter newline
  // (on an iPad Enter is a newline and only the launch button launches).
  // Every edit persists the draft (debounced) — dictation and the readline
  // keys dispatch `input` too, so their text is saved by the same handler.
  prompt.oninput = () => { autoGrow(prompt); saveNsDraft(nsDraftDir, prompt.value); };
  // reconcile with the server: the cache can be stale (another device typed, or
  // this page has been open since before that write). Merge per directory,
  // newest seq wins, so our own just-typed entries survive. Never yank text out
  // from under an edit — repaint the box only while it still holds exactly what
  // we seeded.
  fetch("/api/ns-draft").then(r => r.json()).then(m => {
    if (!m || typeof m !== "object") return;
    for (const [k, v] of Object.entries(m))
      if (v && typeof v.text === "string" && v.seq >= (nsDraftFor(k).seq || 0))
        S.nsDrafts[k] = v;
    const text = nsDraftFor(nsDraftDir).text;
    if (!prompt.isConnected || prompt.value !== seeded || text === seeded) return;
    prompt.value = text;
    autoGrow(prompt);
  }).catch(() => {});
  // Switching the form to another directory switches which draft is in the box
  // — but only once the directory has SETTLED (the field blurs: you clicked or
  // tabbed into the prompt, picked a suggestion and moved on). Never per
  // keystroke: typing "/Users/me/proj" would otherwise walk a dozen half-paths,
  // blanking the box under each. The text in the box is parked under the
  // directory it was typed for first, so nothing is ever lost; the new
  // directory's own draft then wins, and if it has NONE the text follows you
  // there (there is nothing to overwrite, and a re-targeted launch usually
  // wants the prompt you just wrote).
  const settleDraftDir = () => {
    const to = nsDirKey(dir.value);
    if (to === nsDraftDir || !prompt.isConnected) return;
    const from = nsDraftDir;
    const carry = prompt.value;
    if ((carry.trim() ? carry : "") !== nsDraftFor(from).text)
      saveNsDraft(from, carry, true);
    nsDraftDir = to;
    const mine = nsDraftFor(to).text;
    const keep = !mine && !!carry.trim();      // carry the text into an empty one
    if (!keep && prompt.value !== mine) {
      prompt.value = mine;
      autoGrow(prompt);
      prompt.selectionStart = prompt.selectionEnd = mine.length;   // caret at end
    }
    if (keep) saveNsDraft(to, carry, true);    // …and it belongs to `to` now
    clog("", "nsdraft.dir", { from, to, carried: keep ? 1 : 0,
                              chars: (keep ? carry : mine).length });
  };
  dir.addEventListener("blur", settleDraftDir);
  prompt.onkeydown = (e) => {
    if (spm.key(e)) return;
    if (!IS_IPAD && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); F.go(); }
  };
  Object.assign(F, { promptRow, prompt, pdic, nsTray, spm });
}

function nsActions(F) {
  const { dir, sug, fresh, picker, prompt, pdic, nsTray, acct, model, effort,
          tool } = F;

  const actions = el("div", "nsactions");
  const cancel = el("button", "nsbtn", "cancel");
  const submit = el("button", "nsbtn primary", "launch");
  actions.append(cancel, submit);

  const go = () => {
    pdic.stop();         // the visible (validated) prompt is what launches
    const cwd = dir.value.trim();
    if (!cwd) { dir.focus(); return; }
    // resuming needs a chosen conversation (no `--continue` fallback): if the
    // fresh toggle is off but nothing is selected, don't silently start fresh.
    const resumeSel = fresh.checked ? "" : picker.value();
    if (!fresh.checked && !resumeSel)
      return toast("ask", "pick a session",
                   "choose a conversation to resume, or switch to fresh");
    if (nsTray.pending())
      return toast("ask", "attachment still uploading", "one moment…");
    submit.disabled = true;
    const codex = tool.value === "codex";
    const body = { cwd, tool: tool.value || "claude_code" };
    const atts = nsTray.paths();
    if (atts.length) body.attachments = atts;
    if (resumeSel) body.resume = resumeSel;
    // codex has no subscription switcher — never send an account for it (the
    // server would resolve an alias the codex host then ignores)
    if (acct.value && !codex) body.account = acct.value;
    if (model.value) body.model = model.value;
    if (effort.value) body.effort = effort.value;
    if (prompt.value.trim()) body.prompt = prompt.value.trim();
    // Optimistic clear: the message is on its way — it rides the launch argv, so
    // empty the box NOW rather than leaving it looking un-sent through the
    // (kitten-slow) launch round-trip. The form tears down on success anyway;
    // this just guarantees the message never LINGERS in the input after you hit
    // launch. Restored verbatim only if the launch actually fails, so a retry
    // keeps your text (the "the draft stayed in the message input" fix).
    const sentPrompt = prompt.value;
    prompt.value = ""; autoGrow(prompt);
    // The pending view mounts on the CLICK, not on the response. The POST is
    // slow in absolute terms — measured `new.ok` p50 ~0.4 s, tail past 5 s: the
    // server runs an osascript clipboard probe (~150 ms), an `lsappinfo` front-app
    // read, a `kitten @ ls` on a resume and finally the `kitten @ launch` itself,
    // all before it answers. Gating the waiting room on that reply put a second of
    // DEAD AIR between the click and any feedback — the exact stretch the pending
    // view exists to cover, so it was missing precisely when it was needed
    // (docs/dashboard.md *The pending view*). Nothing in the response is needed to
    // MOUNT it: `win` only sharpens the jump watch (which falls back to the cwd
    // heuristic) and arrives below, and the failure path rolls the form back.
    // Arming before the POST also takes the known/live baseline from before the
    // launch, which is what checkJump wants.
    const toolLbl = (Array.isArray(S.hosts)
                     && (S.hosts.find(h => h.name === body.tool) || {}).label)
                    || (codex ? "Codex" : "Claude");
    const show = { mode: body.resume ? "resume" : "new",
                   model: model.value, effort: effort.value, toolLabel: toolLbl,
                   account: codex ? "" : acct.value, prompt: body.prompt || "" };
    armJump(cwd, body.resume, { show, pend: true });
    const mine = S.jump;               // this launch's watch — a later one wins
    closeNewSession();
    // Explicit route() when the hash already IS #/launching (a second launch
    // from the header + while waiting): no hashchange fires, but the view must
    // rebuild around the new watch.
    if (location.hash === "#/launching") route();
    else location.hash = "#/launching";
    postJSON("/api/sessions/new", body, { audit: "new", sid: "" })
      .then((d) => {
        nsRemember({ cwd, model: model.value, effort: effort.value,
                     tool: tool.value });
        // the exact-match window id, folded into the watch already running (and
        // re-checked at once: the session may have appeared while we waited)
        if (S.jump === mine && d && d.win) { mine.win = d.win; checkJump(); }
      })
      .catch(e => {
        // roll the optimistic view back: drop OUR watch (a launch that never
        // happened must not resolve onto some other session's arrival), then
        // re-open the form exactly as it was submitted — the prompt via its
        // draft (closeNewSession flushed the emptied box), the pickers via
        // nsRetry — so the failure costs a click, not the typing.
        if (S.jump === mine) S.jump = null;
        // leave the (now watchless) waiting room — but only if we're still IN
        // it: a user who navigated away mid-flight is not to be yanked back.
        if (location.hash === "#/launching") location.hash = "#/";
        nsRetry = { cwd, model: model.value, effort: effort.value,
                    account: acct.value, tool: tool.value };
        saveNsDraft(cwd, sentPrompt, true);
        openNewSession(cwd, body.resume);
        toast("ask", "launch failed", (e && e.error) || "");
      });
  };
  submit.onclick = go;
  cancel.onclick = closeNewSession;
  dir.onkeydown = (e) => {
    if (sug.key(e)) return;
    if (e.key === "Enter") { e.preventDefault(); go(); }
  };
  Object.assign(F, { actions, submit, go });
}

function nsMount(F) {
  const { panel, dirRow, toolRow, freshRow, resumeRow, split2, split, promptRow,
          actions, dir, fresh, prompt } = F;

  panel.append(dirRow, toolRow, freshRow, resumeRow, split2, split, promptRow,
               actions);
  const back = el("div", "nsback");
  back.onclick = (e) => { if (e.target === back) closeNewSession(); };
  back.append(panel);
  $modal.append(back);
  $modal.hidden = false;
  document.body.classList.add("modal-open");      // scroll-lock the page behind
  if (prompt.value) {
    autoGrow(prompt);                             // a restored multi-line draft
    //                     shows whole (scrollHeight needs the mounted element)
    prompt.selectionStart = prompt.selectionEnd = prompt.value.length;  // caret
    //                     at the end — you reopened to keep TYPING, not to
    //                     insert at the top (a fresh .value leaves it at 0)
  }
  // a known directory (remembered/prefilled) means the next thing you type is
  // the prompt — focusing the dir field there just pops its suggestion look.
  // Not on an iPad: the unasked-for keyboard covers half the form (and focus
  // triggers Safari's page auto-zoom — see style.css touch section). Resuming
  // (fresh off) focuses the picker ROW instead — done by refresh(andFocus) once
  // the rows land, so ↑/↓/space work at once without popping the keyboard.
  if (!IS_IPAD && fresh.checked) (dir.value.trim() ? prompt : dir).focus();
}

// Open the new-session modal. `prefillCwd` seeds the directory field;
// `resumeSid` opens straight onto that conversation in the resume picker;
// `presetTool` preselects the tool picker (a caller that already knows the host,
// e.g. a codex resume deep-link) — the resume picker also switches the tool from
// the chosen row's own host, so this is usually unnecessary.
function openNewSession(prefillCwd, resumeSid, presetTool) {
  $modal.textContent = "";
  const panel = el("div", "nspanel");
  panel.append(el("div", "nstitle", "new session"));
  // a failed launch's own selections outrank the remembered last-used ones,
  // once (nsRetry) — the reopened form is that launch, ready to re-submit
  const F = { prefillCwd, resumeSid, presetTool, panel,
              last: nsRetry || nsLast() };
  nsRetry = null;
  nsDirField(F);
  nsConversation(F);
  nsPickers(F);
  nsLayout(F);
  nsPrompt(F);
  nsActions(F);
  nsMount(F);
}

// Esc in a live session view = interrupt the agent (the terminal's own Esc,
// via /interrupt → Frontend.send_key). Every overlay Escape (modal below,
// slash menu, filter, dropdowns) either runs first here or stopPropagation()s
// before the document level, so this is the fallback meaning of Esc.
// The header's ⇆ migrate button: resume this session under the other
// subscription account (the server picks it — least used, active limit-hit
// excluded, no % ceiling for a manual click; docs/relimit.md *Manual
// migrate*). The old tab closes and a new one opens; the sid forks on
// resume and the adopt machinery + jump watch carry the page over.
// Returns the POST promise so the button wiring can disable itself for the
// round-trip — a double-click on ⇆ migrate would otherwise spawn two racing
// migrators (each closing the tab, each picking a target). The guard path
// resolves so a caller's `.finally` re-enable still runs.
