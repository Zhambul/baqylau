"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.12-init.js for the boot/init sequence.

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
  clog(resumeSid || "", "launch.arm", { win: o.win || "", resume: !!resumeSid });
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
  const hint = el("div", "pendhint",
                  "claude is booting in a new terminal tab — usually a couple of seconds");
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
                 "claude may have failed to start — check the terminal tab"));
  const back = el("button", "nsbtn", "back to sessions");
  back.onclick = () => { location.hash = "#/"; };
  card.append(back);
  $view.append(card);
}

/* ---------- control plane: the new-session form ---------- */
// Lives in the persistent #modal host (outside #view) so a list re-render from
// an SSE snapshot never blows away a half-typed form. Directory input backed by
// suggest() over the distinct cwds in the current snapshot; optional first
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
const nsRemember = (p) => {
  S.nsPrefs = p;                                   // cache first, form is sync
  postJSON("/api/ns-prefs", p).catch(() => {});    // best-effort backend write
};

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

  let rows = [], selSid = "", pvSid = "", lastCwd = "", qToken = 0, pvBack = null;
  const pvCache = new Map();

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

  // The preview is a POPUP WINDOW over the form (a roomy, readable overlay — the
  // inline panel was too cramped to read). It stacks above the new-session modal
  // (.nspvback z-index > .nsback) and owns its own Escape/close so the form's
  // document-level Esc handler doesn't fire underneath it (resumePreviewCleanup
  // + a capturing keydown that stopPropagation()s). Closing returns focus to the
  // row that opened it.
  const closePreview = () => {
    if (!pvBack) return;
    document.removeEventListener("keydown", pvKey, true);
    pvBack.remove();
    pvBack = null;
    pvSid = "";
    resumePreviewCleanup = null;
    const r = list.querySelector(".nsresrow.sel") || list.querySelector(".nsresrow");
    if (r) r.focus();
  };
  const pvKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePreview(); }
  };

  const showPreview = (sid) => {
    if (pvBack && pvSid === sid) {                  // space again on the same row closes
      clog(sid, "resume.preview", { shown: 0 });
      closePreview();
      return;
    }
    closePreview();                                // switching rows: replace the popup
    pvSid = sid;
    const r = rows.find(x => x.sid === sid);
    const title = (r && r.title) || shortSid(sid);
    pvBack = el("div", "nspvback");
    const panel = el("div", "nspvpanel");
    const head = el("div", "nspvhead");
    head.append(el("span", "nspvtitle", "preview · " + title));
    const x = el("button", "nspvx", "✕");
    x.title = "close (Esc)";
    x.onclick = closePreview;
    head.append(x);
    const body = el("div", "nspvbody");
    body.append(el("div", "nspreview-empty", "loading…"));
    panel.append(head, body);
    pvBack.append(panel);
    pvBack.onclick = (e) => { if (e.target === pvBack) closePreview(); };
    document.body.append(pvBack);
    document.addEventListener("keydown", pvKey, true);   // preempt the form's Esc
    resumePreviewCleanup = closePreview;                 // form-close safety net
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
  let qTimer = 0;
  search.oninput = () => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => api.refresh(lastCwd, "", search.value.trim()), 200);
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

function openNewSession(prefillCwd, resumeSid) {
  $modal.textContent = "";
  const last = nsLast();
  const panel = el("div", "nspanel");
  panel.append(el("div", "nstitle", "new session"));

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
  const sug = suggest(dir, [...new Set(S.sessions.map(r => r.cwd).filter(Boolean))]);
  dirRow.append(dir, sug.el);

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
  let dirTimer = 0;
  dir.oninput = () => {
    if (fresh.checked) return;
    clearTimeout(dirTimer);
    pickerLoaded = true;
    dirTimer = setTimeout(() => picker.refresh(dir.value.trim(), "", "", false), 250);
  };

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
  const [modelRow, model] = pick("model", [
    ["fable", "fable"], ["opus", "opus"],
    ["sonnet", "sonnet"], ["haiku", "haiku"],
  ]);
  model.value = model.has(last.model) ? last.model : "fable";
  const [effortRow, effort] = pick("effort", [
    ["low", "low"], ["medium", "medium"],
    ["high", "high"], ["xhigh", "xhigh"], ["max", "max"],
  ]);
  effort.value = effort.has(last.effort) ? last.effort : "high";
  // Track whether the user has touched a picker by hand — the resume prefill
  // below (async) must not clobber a deliberate choice made while it was in
  // flight (same discipline as acctPicked).
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
  const autoAcct = () => {
    if (acctPicked || !acctList.length) return;
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
  const fillAccts = (list) => {
    acctList = list;
    acctRow.style.display = list.length ? "" : "none";
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
    autoAcct();
  };
  if (S.accts) fillAccts(S.accts);
  fetch("/api/accounts").then(r => r.json())
    .then(list => { S.accts = list; fillAccts(list); }).catch(() => {});

  // Resuming should continue where the SESSION was, not where the launcher last
  // was: on every resume-row selection, reuse that session's own model (its
  // transcript-tail model from /api/resumable) and effort (its last-applied
  // /effort level), overriding the global last-used ns-prefs defaults set above —
  // unless the user has already hand-picked (modelPicked/effortPicked). The
  // account is DELIBERATELY not reused: autoAcct re-runs against the chosen model
  // so the launch still load-balances (docs/dashboard.md *Resume picker*).
  picker.onSelect = (r) => {
    const fam = (shortModel(r.model) || "").split("-")[0];
    if (!modelPicked && fam && model.has(fam)) model.value = fam;
    if (!effortPicked && r.effort && effort.has(r.effort)) effort.value = r.effort;
    autoAcct();
  };
  syncFresh();                       // initial visibility + (if resuming) load

  const split = el("div", "nssplit");
  split.append(modelRow, effortRow);
  const split2 = el("div", "nssplit");
  split2.append(acctRow);

  const promptRow = el("label", "nsfield");
  promptRow.append(el("span", "nslabel", "first prompt (optional)"));
  const prompt = el("textarea", "nsinput nsprompt");
  prompt.rows = 3;
  prompt.spellcheck = false;
  prompt.placeholder = IS_IPAD
    ? "what should Claude start on?"
    : "what should Claude start on?  (Enter to launch · Shift+Enter for newline)";
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
  // (on an iPad Enter is a newline and only the launch button launches)
  prompt.oninput = () => autoGrow(prompt);
  prompt.onkeydown = (e) => {
    if (spm.key(e)) return;
    if (!IS_IPAD && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); go(); }
  };

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
    const body = { cwd };
    const atts = nsTray.paths();
    if (atts.length) body.attachments = atts;
    if (resumeSel) body.resume = resumeSel;
    if (acct.value) body.account = acct.value;
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
    postJSON("/api/sessions/new", body, { audit: "new", sid: "" })
      .then((d) => {
        nsRemember({ cwd, model: model.value, effort: effort.value });
        armJump(cwd, body.resume, {
          win: (d && d.win) || "",
          show: { mode: body.resume ? "resume" : "new",
                  model: model.value, effort: effort.value,
                  account: acct.value, prompt: body.prompt || "" },
        });
        closeNewSession();
        // the optimistic pending view — the jump swaps it for the session in
        // place. Explicit route() when the hash already IS #/launching (a
        // second launch from the header + while waiting): no hashchange fires,
        // but the view must rebuild around the new watch.
        if (location.hash === "#/launching") route();
        else location.hash = "#/launching";
      })
      .catch(e => {
        submit.disabled = false;
        prompt.value = sentPrompt; autoGrow(prompt);   // launch failed — keep it
        toast("ask", "launch failed", (e && e.error) || "");
      });
  };
  submit.onclick = go;
  cancel.onclick = closeNewSession;
  dir.onkeydown = (e) => {
    if (sug.key(e)) return;
    if (e.key === "Enter") { e.preventDefault(); go(); }
  };

  panel.append(dirRow, freshRow, resumeRow, split2, split, promptRow, actions);
  const back = el("div", "nsback");
  back.onclick = (e) => { if (e.target === back) closeNewSession(); };
  back.append(panel);
  $modal.append(back);
  $modal.hidden = false;
  document.body.classList.add("modal-open");      // scroll-lock the page behind
  // a known directory (remembered/prefilled) means the next thing you type is
  // the prompt — focusing the dir field there just pops its suggestion look.
  // Not on an iPad: the unasked-for keyboard covers half the form (and focus
  // triggers Safari's page auto-zoom — see style.css touch section). Resuming
  // (fresh off) focuses the picker ROW instead — done by refresh(andFocus) once
  // the rows land, so ↑/↓/space work at once without popping the keyboard.
  if (!IS_IPAD && fresh.checked) (dir.value.trim() ? prompt : dir).focus();
}

$newbtn.onclick = () => openNewSession("");
$statsbtn.onclick = () => { location.hash = "#/stats"; };

/* ---------- fullscreen toggle ---------- */
// Header ⛶ button: browser Fullscreen API on the whole document, with the
// WebKit-prefixed fallback (iPadOS Safari ships only webkitRequestFullscreen).
// Hidden where neither exists (iPhone Safari). State syncs on the
// fullscreenchange event, not in the click handler, so Esc / the browser's
// own exit path keeps the button honest.
{
  const $fsbtn = document.getElementById("fsbtn");
  const root = document.documentElement;
  const req = root.requestFullscreen || root.webkitRequestFullscreen;
  const exit = document.exitFullscreen || document.webkitExitFullscreen;
  const cur = () => document.fullscreenElement || document.webkitFullscreenElement;
  if (!req) {
    $fsbtn.hidden = true;
  } else {
    $fsbtn.onclick = () => {
      const p = cur() ? exit.call(document) : req.call(root);
      if (p && p.catch) p.catch(() => {});   // e.g. permission denied — no-op
    };
    const sync = () => { $fsbtn.title = cur() ? "exit fullscreen" : "fullscreen"; };
    document.addEventListener("fullscreenchange", sync);
    document.addEventListener("webkitfullscreenchange", sync);
  }
}

/* ---------- readline-style editing keys (kitty-like) ---------- */
// ⌃W deletes the word left of the cursor, ⌃A jumps to line start, ⌃E to line
// end — the kitty/shell editing keys, in every dashboard text box (composer,
// first prompt, directory, filter). One delegated listener: element handlers
// (slash menu, suggest, filter-Esc) run first and none of them claim ⌃-keys.
// Safe to preventDefault on macOS — the browser's own accelerators live on
// ⌘, not ⌃ (and this beats the Cocoa text bindings only where behavior
// differs anyway). Match on e.code so a non-QWERTY layout can't move the
// keys. ⌃W dispatches an input event so autoGrow / the suggest and filter
// oninput hooks see the edit.
document.addEventListener("keydown", (e) => {
  if (!e.ctrlKey || e.altKey || e.metaKey || e.shiftKey) return;
  const t = e.target;
  if (!t || (t.tagName !== "TEXTAREA" &&
             !(t.tagName === "INPUT" && t.type === "text"))) return;
  const v = t.value, s = t.selectionStart, se = t.selectionEnd;
  if (e.code === "KeyW") {          // delete word (or the selection) leftward
    let a = s, b = se;
    if (a === b) {
      while (a > 0 && /\s/.test(v[a - 1])) a--;
      while (a > 0 && !/\s/.test(v[a - 1])) a--;
    }
    t.value = v.slice(0, a) + v.slice(b);
    t.setSelectionRange(a, a);
    t.dispatchEvent(new Event("input"));
  } else if (e.code === "KeyA") {   // start of the current line
    const p = s === 0 ? 0 : v.lastIndexOf("\n", s - 1) + 1;
    t.setSelectionRange(p, p);
  } else if (e.code === "KeyE") {   // end of the current line
    const p = v.indexOf("\n", se);
    t.setSelectionRange(p < 0 ? v.length : p, p < 0 ? v.length : p);
  } else return;
  e.preventDefault();
});

/* ---------- ⌃⇧←/→ cycle through live sessions (kitty's tab keys) ---------- */
// Mirrors kitty's next/previous-tab shortcuts: step through the LIVE sessions
// oldest-first (creation order, like the tab bar), wrapping at the ends. From
// the list view or a parked session — nowhere in the cycle — → enters at the
// first (oldest) live session and ← at the last. Deliberately not gated on
// input focus: macOS claims ⌃←/→ (Spaces) but nothing claims ⌃⇧←/→, and in a
// text box it shadows only a selection gesture that already lives on ⌥⇧/⌘⇧.
document.addEventListener("keydown", (e) => {
  if (!e.ctrlKey || !e.shiftKey || e.altKey || e.metaKey) return;
  const dir = e.code === "ArrowRight" ? 1 : e.code === "ArrowLeft" ? -1 : 0;
  if (!dir) return;
  e.preventDefault();
  const live = S.sessions.filter(r => r.live)
    .sort((a, b) => (a.started_at || 0) - (b.started_at || 0));
  if (!live.length) return;
  const at = live.findIndex(r => r.sid === S.cur);
  const to = at < 0 ? (dir > 0 ? 0 : live.length - 1)
                    : (at + dir + live.length) % live.length;
  if (live[to].sid !== S.cur)
    location.hash = "#/s/" + encodeURIComponent(live[to].sid);
});

// Esc in a live session view = interrupt the agent (the terminal's own Esc,
// via /interrupt → Frontend.send_key). Every overlay Escape (modal below,
// slash menu, filter, dropdowns) either runs first here or stopPropagation()s
// before the document level, so this is the fallback meaning of Esc.
// The header's ⇆ migrate button: resume this session under the other
// subscription account (the server picks it — least used, active limit-hit
// excluded, no % ceiling for a manual click; docs/relimit.md *Manual
// migrate*). The old tab closes and a new one opens; the sid forks on
// resume and the adopt machinery + jump watch carry the page over.
// Lock an immediate (no-confirm) control-plane action button for the duration
// of its POST so a double-tap can't fire the terminal write twice — ⇆ migrate
// would spawn two racing migrators, ■ stop/⊘ cancel would double-send Escape.
// `run` returns the POST promise; `rest` restores the button's resting state
// once it settles (default: re-enable; cancel re-derives from the tab). This
// lives on the buttons, not the functions, because the Esc-key gesture has its
// own escHold debounce and the functions are shared by both entry points.
function lockDuring(btn, run, rest) {
  btn.disabled = true;
  run().finally(rest || (() => { btn.disabled = false; }));
}

// Returns the POST promise so the button wiring can disable itself for the
// round-trip — a double-click on ⇆ migrate would otherwise spawn two racing
// migrators (each closing the tab, each picking a target). The guard path
// resolves so a caller's `.finally` re-enable still runs.
